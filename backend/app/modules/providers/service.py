"""Provider configuration (owner only) and a safe read-only view for everyone else.

The owner decides which providers exist, which tasks each may run, whether it is enabled and whether it is paid. A new provider starts DISABLED. Paid providers need an explicit
confirmation here AND the platform flag PREDICTAI_AI_ALLOW_PAID. The privacy class (local / private_network / external) is worked out from the address, not typed in, so a remote
server cannot be labelled local. Keys are never accepted or stored: the owner names an environment variable (PREDICTAI_AI_KEY_...) and the key is read from it at call time."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from ...core.clock import to_iso
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.context import Context
from ..identity.tokens import new_id
from ..webaccess.urlguard import UrlRefused, check_url, classify_address
from ..workspace.errors import conflict, invalid, not_found
from .base import ALL_TASKS, DEFAULT_TASKS, KINDS, SECRET_ENV, Consent
from .router import AdapterFactory, BreakerBoard, Router
from .store import ProviderStore

KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def classify_base_url(url: str, resolver=None) -> str:
    """local: every address is loopback; private_network: private but not loopback; external: anything public. The least trusted address decides."""
    import ipaddress
    c = check_url(url, resolver=resolver, allow_private=True)
    klass = "local"
    for a in c.addresses:
        kind = classify_address(a)
        if kind == "public":
            return "external"
        if not ipaddress.ip_address(a).is_loopback:
            klass = "private_network"
    return klass


class ProviderService:
    def __init__(self, ctx: Context, board: BreakerBoard, factory: Optional[AdapterFactory] = None, resolver=None, sleeper=None) -> None:
        self.ctx, self.board, self.resolver = ctx, board, resolver
        self.store = ProviderStore(ctx.conn)
        self.factory = factory or AdapterFactory(resolver=resolver)
        self._sleeper = sleeper

    def router(self) -> Router:
        return Router(self.store, self.ctx.settings, self.ctx.clock, self.board, self.factory, **({"sleeper": self._sleeper} if self._sleeper else {}))

    # ------------------------------------------------------------------ views
    def _public(self, row: Dict[str, Any], owner_view: bool) -> Dict[str, Any]:
        host = urlsplit(row["base_url"]).hostname if row.get("base_url") else None
        try:
            probe = json.loads(row["last_probe"]) if row.get("last_probe") else None
        except ValueError:
            probe = None
        out = {"key": row["key"], "kind": row["kind"], "label": row["label"], "enabled": row["enabled"], "privacy_class": row["privacy_class"], "paid": row["paid"], "tasks": row["tasks"],
               "model": row.get("model"), "embed_model": row.get("embed_model"), "host": host, "priority": row["priority"],
               "data_leaves_platform": row["privacy_class"] != "local" and row["kind"] != "builtin", "last_probe": probe, "last_probe_at": row.get("last_probe_at"), "notes": row.get("notes")}
        if owner_view:
            out.update({"base_url": row.get("base_url"), "timeout_s": row["timeout_s"], "max_retries": row["max_retries"], "secret_env": row.get("secret_env"),
                        "has_secret": bool(row.get("secret_env") and self.factory.secret(row)()), "daily_budget_units": row.get("daily_budget_units"),
                        "daily_request_limit": row.get("daily_request_limit"), "requests_per_minute": row.get("requests_per_minute"), "quota_tz": row.get("quota_tz")})
        return out

    def list(self, principal: Principal) -> Dict[str, Any]:
        owner = principal.can(Perm.OWNER_PROVIDERS_MANAGE)
        rows = self.store.all()
        return {"items": [self._public(r, owner) for r in rows], "can_manage": owner, "paid_allowed": bool(self.ctx.settings.ai_allow_paid), "remote_allowed": bool(self.ctx.settings.ai_allow_remote_host),
                "notice": "PredictAI works without any model provider. A provider only phrases or embeds text; private material is never sent to an external provider."}

    # ------------------------------------------------------------------ validation
    def _check_fields(self, d: Mapping[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        kind = (existing or {}).get("kind") or d.get("kind")
        if existing is None:
            if kind not in KINDS or kind == "builtin":
                raise invalid("kind", "Choose ollama or openai_compatible.")
            key = str(d.get("key") or "").strip().lower()
            if not KEY_RE.match(key) or key == "builtin":
                raise invalid("key", "Use 2-32 lowercase letters, digits, - or _, starting with a letter.")
            out.update(key=key, kind=kind)
        if "label" in d or existing is None:
            label = str(d.get("label") or "").strip()
            if not 1 <= len(label) <= 80:
                raise invalid("label", "Give the provider a short name.")
            out["label"] = label
        if "base_url" in d or existing is None:
            url = str(d.get("base_url") or "").strip().rstrip("/")
            try:
                out["privacy_class"] = classify_base_url(url, self.resolver)
            except UrlRefused as e:
                raise invalid("base_url", "That address is not allowed: " + e.message) from None
            out["base_url"] = url
            if out["privacy_class"] != "local" and not self.ctx.settings.ai_allow_remote_host and out.get("key") != "gemini":
                raise invalid("base_url", "This address is not on this machine. The platform owner must set PREDICTAI_AI_ALLOW_REMOTE_HOST=true first.", "remote_host_not_allowed")
        for f in ("model", "embed_model"):
            if f in d:
                v = d.get(f)
                if v is not None and (not isinstance(v, str) or len(v.strip()) > 120 or re.search(r"[\s\x00-\x1f]", v.strip())):
                    raise invalid(f, "Use the model name exactly as the server lists it, without spaces.")
                out[f] = (v or "").strip() or None
        if "tasks" in d or existing is None:
            tasks = d.get("tasks", list(DEFAULT_TASKS))
            if not isinstance(tasks, list) or not all(t in ALL_TASKS for t in tasks):
                raise invalid("tasks", "Unknown task. Allowed: " + ", ".join(ALL_TASKS))
            out["tasks"] = sorted(set(tasks), key=ALL_TASKS.index)
        for f, lo, hi in (("priority", 1, 999), ("timeout_s", 2, 300), ("max_retries", 0, 3)):
            if f in d:
                if not isinstance(d[f], int) or isinstance(d[f], bool) or not lo <= d[f] <= hi:
                    raise invalid(f, f"Enter a whole number from {lo} to {hi}.")
                out[f] = d[f]
        if "daily_budget_units" in d:
            v = d["daily_budget_units"]
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                raise invalid("daily_budget_units", "Enter a positive whole number or leave it empty.")
            out["daily_budget_units"] = v
        for f, hi in (("daily_request_limit", 100000), ("requests_per_minute", 10000)):
            if f in d:
                v = d[f]
                if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= hi):
                    raise invalid(f, f"Enter a whole number from 1 to {hi} or leave it empty.")
                out[f] = v
        if "quota_tz" in d:
            v = d["quota_tz"]
            if v not in (None, ""):
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(str(v))
                except Exception:                                                              # noqa: BLE001
                    raise invalid("quota_tz", "Use a time zone name such as America/Los_Angeles.") from None
            out["quota_tz"] = v or None
        if "secret_env" in d:
            v = d["secret_env"]
            if v not in (None, "") and not SECRET_ENV.match(str(v)):
                raise invalid("secret_env", "Name an environment variable that starts with PREDICTAI_AI_KEY_. Never paste a key here.")
            out["secret_env"] = v or None
        if "notes" in d:
            out["notes"] = (str(d["notes"])[:300] if d["notes"] else None)
        return out

    # ------------------------------------------------------------------ owner actions
    def _require_owner(self, principal: Principal) -> None:
        require(principal, Perm.OWNER_PROVIDERS_MANAGE)
        self.ctx.limiter.hit("providers:user", principal.user_id)

    def create(self, principal: Principal, data: Mapping[str, Any]) -> Dict[str, Any]:
        self._require_owner(principal)
        fields = self._check_fields(data, None)
        if self.store.get(fields["key"]):
            raise conflict("PROVIDER_EXISTS", "A provider with that key already exists.")
        paid = bool(data.get("paid", False))
        row = {"id": new_id(), "enabled": False, "paid": paid, "priority": fields.pop("priority", 100), "timeout_s": fields.pop("timeout_s", 30), "max_retries": fields.pop("max_retries", 1), **fields}
        if not (row.get("model") or row.get("embed_model")):
            raise invalid("model", "Name at least a chat model or an embedding model.")
        self.store.insert(row, self.ctx.clock.iso())
        self.ctx.audit.record("ai.provider.created", actor=principal.user_id, metadata={"key": row["key"], "kind": row["kind"], "privacy_class": row["privacy_class"], "paid": paid})
        return self._public(self.store.get(row["key"]), True)

    def update(self, principal: Principal, key: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        self._require_owner(principal)
        row = self.store.get(key)
        if row is None or row["kind"] == "builtin":
            raise not_found("provider")
        fields = self._check_fields({k: v for k, v in data.items() if k not in ("key", "kind", "enabled", "paid")}, row)
        if "paid" in data:
            fields["paid"] = bool(data["paid"])
            if fields["paid"] and not row["paid"]:
                fields["enabled"] = False                                                      # marking a provider paid switches it off until it is enabled again on purpose
        if fields:
            self.store.update(key, fields, self.ctx.clock.iso())
            self.ctx.audit.record("ai.provider.updated", actor=principal.user_id, metadata={"key": key, "fields": sorted(fields)})
            self.board.reset(key)
        return self._public(self.store.get(key), True)

    def set_enabled(self, principal: Principal, key: str, enabled: bool, *, confirm_paid: bool = False) -> Dict[str, Any]:
        self._require_owner(principal)
        row = self.store.get(key)
        if row is None or row["kind"] == "builtin":
            raise not_found("provider")
        if enabled and row["paid"]:
            if not self.ctx.settings.ai_allow_paid:
                raise conflict("PAID_NOT_ALLOWED", "Paid providers are switched off for this platform (PREDICTAI_AI_ALLOW_PAID). Nothing was enabled.")
            if not confirm_paid:
                raise conflict("PAID_CONFIRMATION_REQUIRED", "This provider is marked paid and may cost money. Confirm explicitly to enable it.")
        if enabled and row["privacy_class"] != "local" and not self.ctx.settings.ai_allow_remote_host and row["key"] != "gemini":
            raise conflict("REMOTE_NOT_ALLOWED", "This provider is not on this machine and remote providers are switched off for this platform.")
        self.store.update(key, {"enabled": enabled}, self.ctx.clock.iso())
        self.board.reset(key)
        self.ctx.audit.record("ai.provider.enabled" if enabled else "ai.provider.disabled", actor=principal.user_id, metadata={"key": key, "paid": row["paid"]})
        return self._public(self.store.get(key), True)

    def delete(self, principal: Principal, key: str) -> Dict[str, Any]:
        self._require_owner(principal)
        row = self.store.get(key)
        if row is None or row["kind"] == "builtin":
            raise not_found("provider")
        self.store.delete(key)
        self.board.reset(key)
        self.ctx.audit.record("ai.provider.deleted", actor=principal.user_id, metadata={"key": key})
        return {"deleted": key}

    def probe(self, principal: Principal, key: str) -> Dict[str, Any]:
        self._require_owner(principal)
        row = self.store.get(key)
        adapter = self.factory.build(row) if row and row["kind"] != "builtin" else None
        if row is None or adapter is None:
            raise not_found("provider")
        from .base import ProviderError
        now = self.ctx.clock.iso()
        try:
            result = adapter.probe(float(row["timeout_s"]))
            result["checked_at"] = now
        except ProviderError as e:
            result = {"ok": False, "capabilities": [], "error": e.code, "message": e.message, "checked_at": now}
        self.store.set_probe(key, result, now)
        self.ctx.audit.record("ai.provider.probed", actor=principal.user_id, outcome="success" if result.get("ok") else "failure", metadata={"key": key})
        return result

    # ------------------------------------------------------------------ usage
    def usage(self, principal: Principal, days: int = 7) -> Dict[str, Any]:
        days = max(1, min(int(days), 90))
        since = to_iso(self.ctx.clock.now() - timedelta(days=days))
        owner = principal.can(Perm.OWNER_PROVIDERS_MANAGE)
        data = self.store.usage_summary(None if owner else principal.user_id, since)
        data["scope"] = "all people on this platform" if owner else "your own research"
        data["recent"] = self.store.recent(None if owner else principal.user_id, 30)
        data["cost_note"] = "Units are tokens as reported by the provider (or a rough estimate). Prices are not known to PredictAI, so no cost is estimated."
        return data

    # ------------------------------------------------------------------ bootstrap from environment
    def sync_from_settings(self) -> Optional[str]:
        """Mirror the existing PREDICTAI_AI_PROVIDER/BASE_URL/MODEL settings (Phase 4) as an enabled 'ollama' provider. Setting them is the owner's explicit choice.
        Returns the action taken, for logging."""
        s = self.ctx.settings
        existing = self.store.get("ollama")
        managed = existing is not None and (existing.get("notes") or "").startswith("From PREDICTAI_AI_")
        if s.ai_provider != "ollama":
            if managed and existing["enabled"]:
                self.store.update("ollama", {"enabled": False}, self.ctx.clock.iso())
                return "disabled"
            return None
        try:
            klass = classify_base_url(s.ai_base_url, self.resolver)
        except UrlRefused:
            return None
        fields = {"label": "Ollama (from environment settings)", "base_url": s.ai_base_url, "model": s.ai_model, "privacy_class": klass, "timeout_s": int(s.ai_timeout_s),
                  "notes": "From PREDICTAI_AI_* settings. Edit the environment to change it."}
        if existing is None:
            self.store.insert({"id": new_id(), "key": "ollama", "kind": "ollama", "enabled": True, "paid": False, "tasks": list(DEFAULT_TASKS), "priority": 50, "max_retries": 1, **fields}, self.ctx.clock.iso())
            return "created"
        if managed:
            self.store.update("ollama", fields, self.ctx.clock.iso())
            return "updated"
        return None
