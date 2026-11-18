"""Who allowed what. Nothing leaves the platform unless every layer says yes: platform flag (owner) AND the person's setting AND, when set, the project's setting.
The project may only narrow what the person allowed, never widen it."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from ..webaccess.providers import PROVIDERS


class ConsentStore:
    def __init__(self, conn: sqlite3.Connection, settings: Any, clock: Any) -> None:
        self.conn, self.settings, self.clock = conn, settings, clock

    def _row(self, owner: str, project: Optional[str]):
        return self.conn.execute("SELECT * FROM research_settings WHERE owner_user_id = ? AND COALESCE(project_id, '') = COALESCE(?, '')", (owner, project)).fetchone()

    def _present(self, row) -> Dict[str, Any]:
        return {"web_enabled": bool(row["web_enabled"]), "providers": json.loads(row["providers"] or "[]"), "external_ai": bool(row["external_ai"]), "news_enabled": bool(row["news_enabled"]),
                "freshness_days": row["freshness_days"], "cache_days": row["cache_days"], "updated_at": row["updated_at"]} if row else {
            "web_enabled": False, "providers": [], "external_ai": bool(getattr(self.settings, "ai_external_default", False)), "news_enabled": False, "freshness_days": None, "cache_days": None, "updated_at": None}

    def effective(self, owner: str, project: str) -> Dict[str, Any]:
        person, proj = self._present(self._row(owner, None)), self._row(owner, project)
        project_set = self._present(proj) if proj else None
        platform = bool(self.settings.research_web_enabled)
        web = platform and person["web_enabled"] and (project_set["web_enabled"] if project_set else True)
        allowed = [p for p in person["providers"] if p in PROVIDERS and PROVIDERS[p].configured(self.settings)[0]]
        if project_set and project_set["providers"] is not None and proj is not None:
            allowed = [p for p in allowed if p in project_set["providers"]] if project_set["providers"] else allowed
        reasons: List[str] = []
        if not platform:
            reasons.append("The platform owner has not enabled web research (PREDICTAI_RESEARCH_WEB_ENABLED).")
        elif not person["web_enabled"]:
            reasons.append("You have not turned on web research.")
        elif project_set and not project_set["web_enabled"]:
            reasons.append("Web research is switched off for this project.")
        elif not allowed:
            reasons.append("No search provider is selected.")
        return {"web_allowed": bool(web and allowed), "providers": allowed if web else [], "reasons": reasons, "platform_enabled": platform, "person": person, "project": project_set,
                "external_ai": bool(person["external_ai"] and (project_set["external_ai"] if project_set else True)),
                "freshness_days": (project_set or {}).get("freshness_days") or person["freshness_days"], "news_allowed": bool(platform and person["news_enabled"] and (project_set["news_enabled"] if project_set else True))}

    def get(self, owner: str, project: Optional[str]) -> Dict[str, Any]:
        return self._present(self._row(owner, project))

    def put(self, owner: str, project: Optional[str], values: Dict[str, Any], row_id: str) -> Dict[str, Any]:
        cur = self._present(self._row(owner, project))
        merged = {**cur, **values}
        now = self.clock.iso()
        exists = self._row(owner, project)
        args = (int(merged["web_enabled"]), json.dumps(merged["providers"]), int(merged["external_ai"]), int(merged["news_enabled"]), merged["freshness_days"], merged["cache_days"], now)
        if exists:
            self.conn.execute("UPDATE research_settings SET web_enabled=?, providers=?, external_ai=?, news_enabled=?, freshness_days=?, cache_days=?, updated_at=? WHERE id = ?", (*args, exists["id"]))
        else:
            self.conn.execute("INSERT INTO research_settings (id, owner_user_id, project_id, web_enabled, providers, external_ai, news_enabled, freshness_days, cache_days, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (row_id, owner, project, *args))
        return self._present(self._row(owner, project))
