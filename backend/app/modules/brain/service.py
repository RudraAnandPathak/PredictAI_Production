"""The PredictAI Brain: setup, status and one guarded way for features to ask the shared model provider.

What it adds to the provider layer (which already routes, retries, falls back and keeps a usage ledger):
  * a one-step Gemini setup (the key is never accepted here: the owner puts it in an environment variable, PredictAI only reads it at call time);
  * request caps, because a free tier is limited in requests per day and per minute, with the quota day that Google uses (midnight Pacific);
  * a status screen that says plainly what is ready, what is blocking, what has been used today and what happens when the cap is reached (Ollama, then the built-in rules);
  * `ask`, which applies the person's own consent for external AI before any private text leaves the platform.

Nothing here makes a feature depend on a model: when no provider is allowed or able to answer, `ask` says so and the caller uses its own rule-based path."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...core.clock import to_iso
from ..identity.authorization import Principal
from ..identity.constants import Perm
from ..providers import Consent, ProviderService, build_messages
from ..providers.quota import day_start, next_reset
from ..workspace.errors import conflict, invalid, not_found
from . import presets as P

SKIP_TEXT = {
    "this material may not be sent to that kind of provider": "Your “allow external AI” setting is off, so your own material was not sent to an outside provider.",
    "daily request limit reached": "the daily request limit is used up (it resets at the provider's midnight)",
    "per-minute request limit reached": "too many requests in the last minute",
    "not enabled by the owner": "it is not enabled",
    "cooling down after repeated failures": "it failed several times in a row and is resting for a minute",
    "paid providers are switched off for this platform": "paid providers are switched off for this platform",
    "no model configured for this task": "no model is configured for this task",
    "not allowed for this task": "it is not allowed for this task",
}


@dataclass
class Answer:
    ok: bool
    text: Optional[str]
    provider: str
    model: Optional[str]
    deterministic: bool
    reasons: List[str] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    units_in: int = 0
    units_out: int = 0

    def summary(self) -> Dict[str, Any]:
        return {"used_ai": self.ok, "provider": self.provider, "model": self.model, "reasons": self.reasons, "attempts": len(self.trace), "tokens_in": self.units_in, "tokens_out": self.units_out}


class BrainService:
    def __init__(self, ctx: Any, providers: ProviderService, consent: Any = None) -> None:
        self.ctx, self.providers, self.consent = ctx, providers, consent

    # ------------------------------------------------------------------ consent
    def external_allowed(self, principal: Principal, project_id: Optional[str]) -> bool:
        """The person's own switch (and the project's, which can only narrow it). Off unless they turned it on."""
        if self.consent is None:
            return False
        try:
            eff = self.consent.effective(principal.user_id, project_id) if project_id else {"external_ai": self.consent.get(principal.user_id, None)["external_ai"]}
        except Exception:                                                                    # noqa: BLE001  a settings problem must never turn private text loose
            return False
        return bool(eff.get("external_ai"))

    # ------------------------------------------------------------------ ask
    def ask(self, principal: Principal, project_id: Optional[str], task: str, instruction: str, sources: Sequence[Tuple[str, str]] = (), *, question: Optional[str] = None, system: Optional[str] = None,
            data_class: str = "private", max_tokens: int = 1200, temperature: float = 0.2, force_local: bool = False) -> Answer:
        """One request through the router. `data_class='private'` means the text comes from the person's own documents: it goes to an outside provider only when their consent allows it."""
        allow = self.external_allowed(principal, project_id) and not force_local            # a notebook set to local-only can only narrow the person's own setting
        messages = build_messages(instruction, sources, question, system)
        out = self.providers.router().chat(principal.user_id, project_id, task, messages, data_class=data_class, consent=Consent(external=allow), max_tokens=max_tokens, temperature=temperature)
        reasons: List[str] = []
        for s in out.skipped:                                                                 # why a provider was passed over matters even when another one then answered
            if s["reason"] != "not enabled by the owner":
                reasons.append(f"{s['provider']}: {SKIP_TEXT.get(s['reason'], s['reason'])}")
        for t in out.trace:
            if t.get("outcome") != "ok":
                reasons.append(f"{t['provider']}: the request failed ({t.get('code')})")
        if not out.ok and not out.trace and not reasons:
            reasons.append("no AI provider is set up")
        return Answer(ok=out.ok, text=out.text if out.ok else None, provider=out.provider, model=out.model, deterministic=not out.ok, reasons=reasons, trace=out.trace, units_in=out.units_in, units_out=out.units_out)

    # ------------------------------------------------------------------ status
    def _counts(self, row: Dict[str, Any]) -> Dict[str, Any]:
        now = self.ctx.clock.now()
        store = self.providers.store
        used_today = store.requests_between(row["key"], day_start(now, row.get("quota_tz")))
        used_minute = store.requests_between(row["key"], to_iso(now - timedelta(seconds=60)))
        lim, rpm = row.get("daily_request_limit"), row.get("requests_per_minute")
        return {"requests_today": used_today, "daily_request_limit": lim, "remaining_today": max(0, lim - used_today) if lim else None, "requests_last_minute": used_minute, "requests_per_minute": rpm,
                "resets_at": next_reset(now, row.get("quota_tz")), "reset_zone": row.get("quota_tz") or "UTC"}

    def status(self, principal: Principal) -> Dict[str, Any]:
        owner = principal.can(Perm.OWNER_PROVIDERS_MANAGE)
        s = self.ctx.settings
        row = self.providers.store.get(P.GEMINI_KEY)
        pub = self.providers._public(row, owner) if row else None
        has_key = bool(row and row.get("secret_env") and self.providers.factory.secret(row)())
        blocked: List[str] = []
        if row is None:
            blocked.append("Gemini has not been set up yet (the platform owner does this once).")
        else:
            if not has_key:
                blocked.append(f"The key is missing: set the environment variable {row.get('secret_env') or P.GEMINI_ENV} on the server and restart it.")
            if not row["enabled"]:
                blocked.append("The Gemini provider is switched off.")
        person_ext = bool(self.consent.get(principal.user_id, None)["external_ai"]) if self.consent is not None else False
        if not person_ext:
            blocked_for_you = "Your “allow external AI” setting is off: your own material is not sent to Gemini (public text, such as a web search query, still can be)."
        else:
            blocked_for_you = None
        others = [{"key": r["key"], "label": r["label"], "privacy": r["privacy_class"]} for r in self.providers.store.all() if r["enabled"] and r["kind"] != "builtin" and r["key"] != P.GEMINI_KEY]
        ready = bool(row and row["enabled"] and has_key)
        out: Dict[str, Any] = {
            "ready": ready, "configured": row is not None, "blocked_by": blocked, "for_you": {"external_ai": person_ext, "note": blocked_for_you},
            "fallback": {"providers": others, "built_in": "When Gemini cannot answer, PredictAI tries these, then its built-in rules. Every feature still works with the rules alone."},
            "data_notice": P.FREE_TIER_NOTICE, "cost_notice": P.COST_NOTICE, "can_manage": owner,
            "features": [{"feature": "Exam prediction: predicted questions", "uses_brain": True, "needs": "allow external AI"},
                         {"feature": "Research assistant and analysis", "uses_brain": True, "needs": "the research workspace's AI data policy set to external allowed (a separate switch)"},
                         {"feature": "Search by meaning (embeddings)", "uses_brain": False, "needs": "an embedding model; not set by this preset"}]}
        if pub:
            out["provider"] = {k: pub[k] for k in ("key", "label", "enabled", "model", "host", "privacy_class", "paid", "data_leaves_platform", "last_probe", "last_probe_at")}
            out["usage"] = self._counts(row)
            if owner:
                out["provider"].update({k: pub.get(k) for k in ("secret_env", "has_secret", "timeout_s", "max_retries", "quota_tz")})
        return out

    # ------------------------------------------------------------------ owner actions
    def setup_gemini(self, principal: Principal, *, model: Any, daily_request_limit: Any = None, requests_per_minute: Any = None, acknowledge_data_use: Any = False, enable: bool = True) -> Dict[str, Any]:
        self.providers._require_owner(principal)
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 120 or any(c.isspace() for c in model.strip()):
            raise invalid("model", "Name the Gemini model exactly as AI Studio shows it (no spaces). Use the model list in this screen to see what your key can use.")
        model = model.strip().removeprefix("models/")
        for name, v in (("daily_request_limit", daily_request_limit), ("requests_per_minute", requests_per_minute)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
                raise invalid(name, "Use a whole number of 1 or more.")
        s = self.ctx.settings
        fields = P.gemini_fields(model, daily_request_limit or P.DEFAULT_DAILY_REQUESTS, requests_per_minute or P.DEFAULT_PER_MINUTE)
        existing = self.providers.store.get(P.GEMINI_KEY)
        if existing is None:
            self.providers.create(principal, {"key": P.GEMINI_KEY, "kind": "openai_compatible", "paid": False, **fields})
            action = "created"
        else:
            keep = {k: v for k, v in fields.items() if k not in ("notes",)}
            if daily_request_limit is None:
                keep["daily_request_limit"] = existing.get("daily_request_limit") or fields["daily_request_limit"]
            if requests_per_minute is None:
                keep["requests_per_minute"] = existing.get("requests_per_minute") or fields["requests_per_minute"]
            self.providers.update(principal, P.GEMINI_KEY, keep)
            action = "updated"
        row = self.providers.store.get(P.GEMINI_KEY)
        has_key = bool(self.providers.factory.secret(row)())
        steps: List[str] = []
        probe = None
        if has_key:
            probe = self.providers.probe(principal, P.GEMINI_KEY)
            if enable:
                self.providers.set_enabled(principal, P.GEMINI_KEY, True)
            if probe.get("problems"):
                steps.append("Pick a model your key can use: " + "; ".join(probe["problems"]))
        else:
            steps.append(f"Create a key in Google AI Studio, set it as the environment variable {P.GEMINI_ENV} on the server, restart the server, then run this setup again. The key is never typed into PredictAI.")
            if enable:
                steps.append("Gemini stays switched off until the key is present.")
        self.ctx.audit.record("brain.gemini.setup", actor=principal.user_id, metadata={"action": action, "model": model, "daily_request_limit": row["daily_request_limit"], "has_key": has_key})
        return {"action": action, "status": self.status(principal), "probe": probe, "next_steps": steps}

    def models(self, principal: Principal) -> Dict[str, Any]:
        """Model names the key can see (a read-only listing; it does not use a generation request)."""
        self.providers._require_owner(principal)
        row = self.providers.store.get(P.GEMINI_KEY)
        adapter = self.providers.factory.build(row) if row else None
        if row is None or adapter is None:
            raise not_found("provider")
        from ..providers.base import ProviderError
        try:
            data = adapter.http.request("GET", f"{adapter.base}/models", headers=adapter._auth(), timeout=float(row["timeout_s"]))
        except ProviderError as e:
            return {"ok": False, "error": e.code, "message": e.message, "models": []}
        names = sorted({str(m.get("id")).removeprefix("models/") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")})
        return {"ok": True, "models": names[:200], "note": "Which of these are free, and their limits, are shown only in the AI Studio rate-limit console."}

    def test(self, principal: Principal) -> Dict[str, Any]:
        """One tiny real request (it counts against the caps). The answer is not kept."""
        self.providers._require_owner(principal)
        out = self.ask(principal, None, "summarize", "Reply with the single word: ready", [], data_class="public", max_tokens=32, temperature=0.0)
        return {"ok": out.ok, "answered_by": out.provider, "model": out.model, "reasons": out.reasons, "attempts": out.trace, "tokens_in": out.units_in, "tokens_out": out.units_out,
                "note": ("Gemini answered." if out.ok and out.provider == P.GEMINI_KEY else "A different provider answered, or none did: see the reasons." if out.ok else "No provider answered; see the reasons.")}
