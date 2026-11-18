"""Persistence for provider configuration and the usage ledger. The ledger records counts, timings and outcomes only: never a prompt, an answer or a key."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["tasks"] = [t for t in json.loads(d.get("tasks") or "[]") if isinstance(t, str)]
    except ValueError:
        d["tasks"] = []
    d["enabled"], d["paid"] = bool(d["enabled"]), bool(d["paid"])
    return d


class ProviderStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def all(self) -> List[Dict[str, Any]]:
        return [_row(r) for r in self.conn.execute("SELECT * FROM ai_providers ORDER BY priority, key").fetchall()]

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM ai_providers WHERE key = ?", (key,)).fetchone()
        return _row(r) if r else None

    def insert(self, d: Dict[str, Any], now: str) -> None:
        self.conn.execute("INSERT INTO ai_providers (id, key, kind, label, base_url, model, embed_model, enabled, privacy_class, paid, tasks, priority, timeout_s, max_retries, secret_env, "
                          "daily_budget_units, daily_request_limit, requests_per_minute, quota_tz, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (d["id"], d["key"], d["kind"], d["label"], d.get("base_url"), d.get("model"), d.get("embed_model"), int(d.get("enabled", False)), d["privacy_class"], int(d.get("paid", False)),
                           json.dumps(d.get("tasks", [])), d.get("priority", 100), d.get("timeout_s", 30), d.get("max_retries", 1), d.get("secret_env"), d.get("daily_budget_units"),
                           d.get("daily_request_limit"), d.get("requests_per_minute"), d.get("quota_tz"), d.get("notes"), now, now))

    def update(self, key: str, fields: Dict[str, Any], now: str) -> None:
        allowed = {"label", "base_url", "model", "embed_model", "enabled", "privacy_class", "paid", "tasks", "priority", "timeout_s", "max_retries", "secret_env", "daily_budget_units", "daily_request_limit", "requests_per_minute", "quota_tz", "notes"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = ?")
            args.append(json.dumps(v) if k == "tasks" else int(v) if k in ("enabled", "paid") else v)
        if sets:
            self.conn.execute(f"UPDATE ai_providers SET {', '.join(sets)}, updated_at = ? WHERE key = ?", (*args, now, key))

    def delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM ai_providers WHERE key = ? AND kind != 'builtin'", (key,))

    def set_probe(self, key: str, result: Dict[str, Any], now: str) -> None:
        self.conn.execute("UPDATE ai_providers SET last_probe = ?, last_probe_at = ? WHERE key = ?", (json.dumps(result)[:4000], now, key))

    # ---- ledger
    def record(self, *, owner: Optional[str], project: Optional[str], provider: str, model: Optional[str], task: str, outcome: str, error_code: Optional[str], latency_ms: Optional[int],
               units_in: int, units_out: int, privacy_class: Optional[str], fallback_from: Optional[str], reason: Optional[str], at: str) -> None:
        self.conn.execute("INSERT INTO ai_usage (owner_user_id, project_id, provider_key, model, task, outcome, error_code, latency_ms, units_in, units_out, cost_estimate, privacy_class, "
                          "fallback_from, reason, occurred_at) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
                          (owner, project, provider, model, task, outcome, error_code, latency_ms, units_in, units_out, privacy_class, fallback_from, (reason or "")[:200] or None, at))

    def units_since(self, provider: str, since: str) -> int:
        r = self.conn.execute("SELECT COALESCE(SUM(units_in + units_out), 0) AS n FROM ai_usage WHERE provider_key = ? AND outcome = 'ok' AND occurred_at >= ?", (provider, since)).fetchone()
        return int(r["n"])

    def requests_between(self, provider: str, since: str) -> int:
        """Requests actually sent to a provider since `since` (answered, failed or timed out; a request skipped before it was sent is not counted). A free tier is limited in requests, so this is
        the number the request caps compare with. Failures count too: the provider may count them, and counting too many only makes the guard safer."""
        r = self.conn.execute("SELECT COUNT(*) AS n FROM ai_usage WHERE provider_key = ? AND outcome IN ('ok', 'error', 'timeout') AND occurred_at >= ?", (provider, since)).fetchone()
        return int(r["n"])

    def usage_summary(self, owner: Optional[str], since: str) -> Dict[str, Any]:
        where, args = ("WHERE occurred_at >= ?" + (" AND owner_user_id = ?" if owner else "")), ([since, owner] if owner else [since])
        by = self.conn.execute(f"SELECT provider_key, task, outcome, COUNT(*) AS calls, COALESCE(SUM(units_in),0) AS u_in, COALESCE(SUM(units_out),0) AS u_out, "
                               f"COALESCE(AVG(latency_ms),0) AS avg_ms FROM ai_usage {where} GROUP BY provider_key, task, outcome ORDER BY provider_key, task", args).fetchall()
        errs = self.conn.execute(f"SELECT provider_key, error_code, COUNT(*) AS n, MAX(occurred_at) AS last_at FROM ai_usage {where} AND error_code IS NOT NULL GROUP BY provider_key, error_code ORDER BY n DESC LIMIT 20", args).fetchall()
        return {"since": since, "rows": [{"provider": r["provider_key"], "task": r["task"], "outcome": r["outcome"], "calls": r["calls"], "units_in": r["u_in"], "units_out": r["u_out"],
                                          "avg_latency_ms": int(r["avg_ms"])} for r in by],
                "errors": [{"provider": r["provider_key"], "code": r["error_code"], "count": r["n"], "last_at": r["last_at"]} for r in errs]}

    def recent(self, owner: Optional[str], limit: int = 50) -> List[Dict[str, Any]]:
        q, a = ("SELECT * FROM ai_usage" + (" WHERE owner_user_id = ?" if owner else "") + " ORDER BY id DESC LIMIT ?"), ([owner, limit] if owner else [limit])
        return [{k: r[k] for k in ("provider_key", "model", "task", "outcome", "error_code", "latency_ms", "units_in", "units_out", "privacy_class", "fallback_from", "reason", "occurred_at")}
                for r in self.conn.execute(q, a).fetchall()]
