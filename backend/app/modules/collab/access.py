"""One place that answers: may this person do this to that resource?

A resource is reachable by (1) its owner, or (2) an active, unexpired member of a workspace to which the resource is shared by a non-revoked, unexpired grant. The role caps the grant:
viewer -> view; editor and admin -> the grant's own level (view or edit). Anything else looks exactly like a resource that does not exist, so identifiers cannot be probed.
The resource's owner must still have an active account, and platform-owner status gives nothing here (platform ownership and workspace roles are separate)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...core.clock import Clock
from ..identity.authorization import Principal
from ..p7 import not_found

# resource type -> (table, owner column, project column, extra WHERE)
RESOURCES: Dict[str, tuple] = {
    "report": ("rep_reports", "owner_user_id", "project_id", "status = 'active'"),
    "model": ("reg_models", "owner_user_id", "project_id", "1 = 1"),
    "research": ("evidence_bundles", "owner_user_id", "project_id", "1 = 1"),
    "assessment": ("exam_assessments", "owner_user_id", "project_id", "1 = 1"),
    "analysis": ("adv_runs", "owner_user_id", "project_id", "1 = 1"),
    "exam_analysis": ("exam_analyses", "owner_user_id", "project_id", "1 = 1"),
}
LEVEL = {"view": 1, "edit": 2, "owner": 3}
ROLE_CAP = {"viewer": "view", "editor": "edit", "admin": "edit"}


@dataclass(frozen=True)
class Access:
    resource_type: str
    resource_id: str
    owner_id: str
    project_id: str
    level: str                 # owner | edit | view
    via: Optional[str] = None  # workspace id for a shared grant

    @property
    def is_owner(self) -> bool:
        return self.level == "owner"

    def allows(self, need: str) -> bool:
        return LEVEL[self.level] >= LEVEL[need]


class AccessResolver:
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn, self.clock = conn, clock

    def _row(self, rtype: str, rid: Any):
        spec = RESOURCES.get(rtype)
        if spec is None or not isinstance(rid, str) or not 0 < len(rid) <= 64:
            return None
        table, owner, proj, extra = spec
        return self.conn.execute(f"SELECT id, {owner} AS owner_id, {proj} AS project_id FROM {table} WHERE id = ? AND {extra}", (rid,)).fetchone()

    def resolve(self, principal: Principal, rtype: str, rid: Any, need: str = "view") -> Access:
        row = self._row(rtype, rid)
        if row is None:
            raise not_found(rtype)
        if row["owner_id"] == principal.user_id:
            return Access(rtype, rid, row["owner_id"], row["project_id"], "owner")
        now = self.clock.iso()
        grants = self.conn.execute(
            "SELECT g.permission, g.workspace_id, m.role FROM ws_resources g "
            "JOIN ws_members m ON m.workspace_id = g.workspace_id AND m.user_id = ? AND m.status = 'active' AND (m.expires_at IS NULL OR m.expires_at > ?) "
            "JOIN ws_workspaces w ON w.id = g.workspace_id AND w.status = 'active' "
            "JOIN users u ON u.id = g.resource_owner_id AND u.status = 'active' "
            "WHERE g.resource_type = ? AND g.resource_id = ? AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > ?)",
            (principal.user_id, now, rtype, rid, now)).fetchall()
        best: Optional[Access] = None
        for g in grants:
            level = min(LEVEL[g["permission"]], LEVEL[ROLE_CAP[g["role"]]])
            name = "edit" if level >= 2 else "view"
            if best is None or LEVEL[name] > LEVEL[best.level]:
                best = Access(rtype, rid, row["owner_id"], row["project_id"], name, g["workspace_id"])
        if best is None or not best.allows(need):
            raise not_found(rtype)          # no access, or not enough access: indistinguishable from "missing"
        return best

    def can(self, principal: Principal, rtype: str, rid: Any, need: str = "view") -> bool:
        try:
            self.resolve(principal, rtype, rid, need)
            return True
        except Exception:  # noqa: BLE001
            return False

    def shared_with(self, principal: Principal, rtype: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every resource currently reachable through a workspace (for the dashboard and the 'Shared with me' lists)."""
        now = self.clock.iso()
        sql = ("SELECT g.id, g.resource_type, g.resource_id, g.permission, g.workspace_id, g.resource_owner_id, m.role, w.name AS workspace_name FROM ws_resources g "
               "JOIN ws_members m ON m.workspace_id = g.workspace_id AND m.user_id = ? AND m.status = 'active' AND (m.expires_at IS NULL OR m.expires_at > ?) "
               "JOIN ws_workspaces w ON w.id = g.workspace_id AND w.status = 'active' "
               "JOIN users u ON u.id = g.resource_owner_id AND u.status = 'active' "
               "WHERE g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > ?) AND g.resource_owner_id != ?")
        args: List[Any] = [principal.user_id, now, now, principal.user_id]
        if rtype:
            sql += " AND g.resource_type = ?"
            args.append(rtype)
        out = []
        for g in self.conn.execute(sql, args).fetchall():
            if self._row(g["resource_type"], g["resource_id"]) is None:
                continue
            level = "edit" if min(LEVEL[g["permission"]], LEVEL[ROLE_CAP[g["role"]]]) >= 2 else "view"
            out.append({"grant_id": g["id"], "resource_type": g["resource_type"], "resource_id": g["resource_id"], "level": level, "workspace_id": g["workspace_id"], "workspace_name": g["workspace_name"]})
        return out
