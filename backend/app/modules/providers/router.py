"""Task-based provider routing with privacy and cost controls.

For each call the router lists the providers the OWNER enabled, keeps those allowed for the task, the data and the cost rules, tries them in priority order with a per-attempt
timeout, bounded retries with backoff, a circuit breaker and a fallback to the next provider, and records every attempt in the usage ledger. If no model provider may or can
answer, it returns `deterministic=True`: the caller then uses its own built-in, rule-based path. PredictAI therefore works with no provider at all.

Hard rules (not settings): private material never goes to an `external` provider; a `paid` provider is never used unless the owner enabled it AND the platform flag
PREDICTAI_AI_ALLOW_PAID is on; a provider the owner has not enabled is never contacted."""
from __future__ import annotations

import os
import threading
import time
from datetime import timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.clock import Clock, to_iso
from .base import EMBED_TASKS, GENERATE_TASKS, Adapter, Consent, ProviderError, RouteOutcome
from .http import JsonHttp
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter
from .quota import day_start as day_start_for
from .store import ProviderStore

Sleeper = Callable[[float], None]


class BreakerBoard:
    """Shared across requests and threads. After `threshold` consecutive failures a provider is skipped for `cooldown_s`."""

    def __init__(self, clock: Clock, threshold: int = 3, cooldown_s: int = 60) -> None:
        self.clock, self.threshold, self.cooldown_s = clock, threshold, cooldown_s
        self._lock = threading.Lock()
        self._state: Dict[str, Tuple[int, Optional[Any]]] = {}

    def open_until(self, key: str):
        with self._lock:
            fails, until = self._state.get(key, (0, None))
            return until if until is not None and until > self.clock.now() else None

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def failure(self, key: str) -> None:
        with self._lock:
            fails, _ = self._state.get(key, (0, None))
            fails += 1
            self._state[key] = (fails, self.clock.now() + timedelta(seconds=self.cooldown_s) if fails >= self.threshold else None)

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            (self._state.clear() if key is None else self._state.pop(key, None))


class AdapterFactory:
    def __init__(self, env: Optional[Mapping[str, str]] = None, resolver=None) -> None:
        self.env, self.resolver = env if env is not None else os.environ, resolver

    def secret(self, row: Dict[str, Any]) -> Callable[[], Optional[str]]:
        name = row.get("secret_env")
        return (lambda: (self.env.get(name) or None)) if name else (lambda: None)

    def build(self, row: Dict[str, Any]) -> Optional[Adapter]:
        http = JsonHttp(allow_private=row["privacy_class"] != "external", resolver=self.resolver)
        if row["kind"] == "ollama" and row.get("base_url"):
            return OllamaAdapter(row["base_url"], row.get("model"), row.get("embed_model"), http)
        if row["kind"] == "openai_compatible" and row.get("base_url"):
            return OpenAICompatAdapter(row["base_url"], row.get("model"), row.get("embed_model"), http, self.secret(row))
        return None


class Router:
    def __init__(self, store: ProviderStore, settings: Any, clock: Clock, board: BreakerBoard, factory: Optional[AdapterFactory] = None, sleeper: Sleeper = time.sleep) -> None:
        self.store, self.settings, self.clock, self.board = store, settings, clock, board
        self.factory, self.sleeper = factory or AdapterFactory(), sleeper

    # ------------------------------------------------------------------ selection
    def plan(self, task: str, data_class: str, consent: Optional[Consent] = None) -> Tuple[List[Tuple[Dict[str, Any], Adapter]], List[Dict[str, str]]]:
        consent = consent or Consent()
        need = "embed" if task in EMBED_TASKS else "chat"
        allowed_privacy = {"local"} if data_class == "private" else {"local", "private_network", "external"}
        if data_class == "private" and consent.private_network:
            allowed_privacy.add("private_network")
        if data_class == "private" and consent.external:
            allowed_privacy.update({"private_network", "external"})
        chosen: List[Tuple[Dict[str, Any], Adapter]] = []
        skipped: List[Dict[str, str]] = []
        day_start = to_iso(self.clock.now().replace(hour=0, minute=0, second=0, microsecond=0))
        for row in self.store.all():
            if row["kind"] == "builtin":
                continue
            why = None
            adapter = self.factory.build(row) if row["enabled"] else None
            if not row["enabled"]:
                why = "not enabled by the owner"
            elif task not in row["tasks"]:
                why = "not allowed for this task"
            elif adapter is None or need not in adapter.capabilities():
                why = "no model configured for this task"
            elif row["paid"] and not getattr(self.settings, "ai_allow_paid", False):
                why = "paid providers are switched off for this platform"
            elif row["privacy_class"] not in allowed_privacy:
                why = "this material may not be sent to that kind of provider"
            elif self.board.open_until(row["key"]) is not None:
                why = "cooling down after repeated failures"
            elif row.get("daily_budget_units") and self.store.units_since(row["key"], day_start) >= row["daily_budget_units"]:
                why = "daily budget reached"
            elif self.request_cap(row):
                why = self.request_cap(row)
            if why:
                skipped.append({"provider": row["key"], "reason": why})
            else:
                chosen.append((row, adapter))                                                 # type: ignore[arg-type]
        if task in tuple(getattr(self.settings, "ai_local_first_tasks", ()) or ()):
            chosen.sort(key=lambda c: 0 if c[0]["privacy_class"] == "local" else 1)             # bulk jobs try the local model first; sort is stable, so priority order holds within each group
        return chosen, skipped

    def request_cap(self, row: Dict[str, Any]) -> Optional[str]:
        """Why this provider may not be sent another request right now (a request cap is used up), or None. Checked before every request, including a retry: a cap that only looked at the start of a
        call could be overshot by the retries of that call."""
        if row.get("daily_request_limit") and self.store.requests_between(row["key"], day_start_for(self.clock.now(), row.get("quota_tz"))) >= row["daily_request_limit"]:
            return "daily request limit reached"
        if row.get("requests_per_minute") and self.store.requests_between(row["key"], to_iso(self.clock.now() - timedelta(seconds=60))) >= row["requests_per_minute"]:
            return "per-minute request limit reached"
        return None

    # ------------------------------------------------------------------ execution
    def chat(self, owner: Optional[str], project: Optional[str], task: str, messages: Sequence[Dict[str, str]], *, data_class: str = "public", consent: Optional[Consent] = None,
             max_tokens: int = 400, temperature: float = 0.0) -> RouteOutcome:
        if task not in GENERATE_TASKS:
            raise ValueError("unknown generation task")
        return self._run(owner, project, task, data_class, consent, lambda a, row: a.chat(messages, max_tokens=max_tokens, temperature=temperature, timeout=float(row["timeout_s"])), "chat")

    def embed(self, owner: Optional[str], project: Optional[str], texts: Sequence[str], *, data_class: str = "private", consent: Optional[Consent] = None) -> RouteOutcome:
        return self._run(owner, project, "embed", data_class, consent, lambda a, row: a.embed(texts, timeout=float(row["timeout_s"])), "embed")

    def _run(self, owner, project, task, data_class, consent, call, mode) -> RouteOutcome:
        chosen, skipped = self.plan(task, data_class, consent)
        trace: List[Dict[str, Any]] = []
        previous: Optional[str] = None
        for row, adapter in chosen:
            for attempt in range(1 + max(0, int(row["max_retries"]))):
                if attempt > 0 and self.request_cap(row):
                    trace.append({"provider": row["key"], "attempt": attempt + 1, "outcome": "skipped", "code": "REQUEST_CAP", "fallback_from": previous})
                    break
                try:
                    res = call(adapter, row)
                except ProviderError as e:
                    outcome = "timeout" if e.code == "PROVIDER_TIMEOUT" else "error"
                    self._log(owner, project, row, task, outcome, e.code, None, 0, 0, previous, e.message)
                    trace.append({"provider": row["key"], "attempt": attempt + 1, "outcome": outcome, "code": e.code, "fallback_from": previous})
                    if e.retryable and attempt < int(row["max_retries"]):
                        self.sleeper(min(4.0, 0.5 * (2 ** attempt)))
                        continue
                    break
                except Exception:                                                              # noqa: BLE001  an adapter bug must not take research down
                    self._log(owner, project, row, task, "error", "PROVIDER_INTERNAL", None, 0, 0, previous, "unexpected error")
                    trace.append({"provider": row["key"], "attempt": attempt + 1, "outcome": "error", "code": "PROVIDER_INTERNAL", "fallback_from": previous})
                    break
                self.board.success(row["key"])
                uin, uout = getattr(res, "units_in", 0), getattr(res, "units_out", 0)
                self._log(owner, project, row, task, "ok", None, res.latency_ms, uin, uout, previous, None)
                trace.append({"provider": row["key"], "attempt": attempt + 1, "outcome": "ok", "code": None, "fallback_from": previous, "latency_ms": res.latency_ms})
                return RouteOutcome(ok=True, provider=row["key"], model=res.model, text=getattr(res, "text", None) if mode == "chat" else None,
                                    vectors=getattr(res, "vectors", None) if mode == "embed" else None, trace=trace, skipped=skipped, units_in=uin, units_out=uout)
            self.board.failure(row["key"])
            previous = row["key"]
        reason = "no model provider is enabled and allowed for this task" if not chosen else "every allowed provider failed"
        self.store.record(owner=owner, project=project, provider="builtin", model=None, task=task, outcome="skipped", error_code=None, latency_ms=None, units_in=0, units_out=0,
                          privacy_class="local", fallback_from=previous, reason=reason, at=to_iso(self.clock.now()))
        return RouteOutcome(ok=False, provider="builtin", deterministic=True, trace=trace, skipped=skipped)

    def _log(self, owner, project, row, task, outcome, code, latency, uin, uout, previous, reason) -> None:
        self.store.record(owner=owner, project=project, provider=row["key"], model=row.get("model") if task != "embed" else row.get("embed_model"), task=task, outcome=outcome, error_code=code,
                          latency_ms=latency, units_in=uin, units_out=uout, privacy_class=row["privacy_class"], fallback_from=previous, reason=reason, at=to_iso(self.clock.now()))
