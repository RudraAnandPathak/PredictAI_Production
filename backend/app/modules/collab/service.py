"""Collaboration: workspaces, roles, invitations, per-resource sharing, revocation and history.

Rules, all enforced here and in AccessResolver (never in the browser):
  * a workspace role caps what a shared resource allows (viewer: view only; editor and admin: up to the grant's level);
  * only the person who owns a resource can share it, and only into a workspace where they are an editor or admin;
  * revoking, expiring, removing a member, closing a workspace or deactivating the owner's account each end access on the next request;
  * there is no public sharing: nothing in Phase 7 creates a link that works without signing in;
  * being a platform owner gives no workspace access: owner status and workspace roles are separate."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from ...core.errors import AppError
from ..identity.authorization import Principal
from ..identity.email import EmailMessage
from ..identity.tokens import hash_token, new_token
from ..p7 import E, Service7, choice, clean, conflict, invalid, jdump, jload, new_id, not_found, page
from .access import RESOURCES, AccessResolver

ROLES = ("viewer", "editor", "admin")
MAX_WORKSPACES_PER_USER = 10
MAX_MEMBERS = 50
INVITE_DAYS = 7
MAX_SHARE_DAYS = 365
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class CollaborationService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver) -> None:
        super().__init__(ctx, ws)
        self.access = access
        self.listeners: List[Callable[[str, Dict[str, Any]], None]] = []      # e.g. scheduling: told when a grant, member or workspace stops giving access

    def _emit(self, kind: str, **info: Any) -> None:
        for fn in self.listeners:
            fn(kind, info)

    def _log(self, wid: str, principal: Optional[Principal], event: str, target_type: Optional[str] = None, target_id: Optional[str] = None, **detail: Any) -> None:
        self.conn.execute("INSERT INTO ws_events (workspace_id, actor_user_id, event, target_type, target_id, detail, created_at) VALUES (?,?,?,?,?,?,?)",
                          (wid, principal.user_id if principal else None, event, target_type, target_id, jdump(detail) if detail else None, self.now()))
        self.audit(E.WS_CHANGED, principal, wid, change=event, target_type=target_type)

    # ------------------------------------------------------------------ membership lookups
    def _member(self, principal: Principal, wid: Any, *, roles: Optional[tuple] = None):
        """The caller's active membership; anything else looks like a workspace that does not exist."""
        if not isinstance(wid, str) or len(wid) > 64:
            raise not_found("workspace")
        row = self.conn.execute("SELECT m.*, w.name, w.status AS wstatus, w.created_by FROM ws_members m JOIN ws_workspaces w ON w.id = m.workspace_id "
                                "WHERE m.workspace_id = ? AND m.user_id = ? AND m.status = 'active' AND (m.expires_at IS NULL OR m.expires_at > ?)", (wid, principal.user_id, self.now())).fetchone()
        if row is None or row["wstatus"] != "active":
            raise not_found("workspace")
        if roles and row["role"] not in roles:
            raise AppError("FORBIDDEN_ROLE", "Your role in this workspace does not allow that.", 403)
        return row

    # ------------------------------------------------------------------ workspaces
    def create_workspace(self, principal: Principal, *, name: Any, description: Any = None) -> Dict[str, Any]:
        self.limit("p7_share:user", principal)
        nm = clean(name, "name", maximum=80)
        owned = self.conn.execute("SELECT COUNT(*) FROM ws_workspaces WHERE created_by = ? AND status = 'active'", (principal.user_id,)).fetchone()[0]
        if owned >= MAX_WORKSPACES_PER_USER:
            raise AppError("WORKSPACE_LIMIT", f"You can own up to {MAX_WORKSPACES_PER_USER} workspaces.", 409)
        wid, now = new_id(), self.now()
        with self.conn:
            self.conn.execute("INSERT INTO ws_workspaces (id, name, description, created_by, status, created_at) VALUES (?,?,?,?,?,?)", (wid, nm, clean(description, "description", maximum=300, required=False), principal.user_id, "active", now))
            self.conn.execute("INSERT INTO ws_members (workspace_id, user_id, role, status, added_by, expires_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)", (wid, principal.user_id, "admin", "active", principal.user_id, None, now, now))
            self._log(wid, principal, "workspace.created")
        return self.get_workspace(principal, wid)

    def list_workspaces(self, principal: Principal) -> Dict[str, Any]:
        rows = self.conn.execute("SELECT w.id, w.name, w.description, m.role, w.created_at, (SELECT COUNT(*) FROM ws_members x WHERE x.workspace_id = w.id AND x.status = 'active') AS members "
                                 "FROM ws_members m JOIN ws_workspaces w ON w.id = m.workspace_id WHERE m.user_id = ? AND m.status = 'active' AND (m.expires_at IS NULL OR m.expires_at > ?) AND w.status = 'active' ORDER BY w.name",
                                 (principal.user_id, self.now())).fetchall()
        return {"items": [dict(r) for r in rows]}

    def get_workspace(self, principal: Principal, wid: str) -> Dict[str, Any]:
        me = self._member(principal, wid)
        members = self.conn.execute("SELECT m.user_id, m.role, m.expires_at, m.created_at, u.email, u.display_name FROM ws_members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? AND m.status = 'active' ORDER BY m.created_at", (wid,)).fetchall()
        out = {"id": wid, "name": me["name"], "your_role": me["role"], "is_creator": me["created_by"] == principal.user_id,
               "members": [{"user_id": m["user_id"], "email": m["email"], "name": m["display_name"], "role": m["role"], "expires_at": m["expires_at"]} for m in members],
               "shares": self.shares(principal, wid)["items"], "roles": {"viewer": "Can open what is shared with the workspace.", "editor": "Can open and, where the share allows, edit; can share their own work here.", "admin": "Everything an editor can do, plus invite, remove, change roles and revoke shares."},
               "public_sharing": "Off. Nothing in PredictAI can be opened without signing in as a member."}
        if me["role"] == "admin":
            out["invitations"] = self.invitations(principal, wid)["items"]
        return out

    def close_workspace(self, principal: Principal, wid: str) -> Dict[str, Any]:
        me = self._member(principal, wid)
        if me["created_by"] != principal.user_id:
            raise AppError("FORBIDDEN_ROLE", "Only the person who created a workspace can close it.", 403)
        with self.conn:
            self.conn.execute("UPDATE ws_workspaces SET status = 'closed' WHERE id = ?", (wid,))
            self.conn.execute("UPDATE ws_invitations SET status = 'revoked', resolved_at = ? WHERE workspace_id = ? AND status = 'pending'", (self.now(), wid))
            self._log(wid, principal, "workspace.closed")
        self._emit("workspace_closed", workspace_id=wid)
        return {"closed": True, "note": "Everything shared into this workspace is no longer reachable through it. The original items still belong to their owners."}

    # ------------------------------------------------------------------ invitations and members
    def invite(self, principal: Principal, wid: str, *, email: Any, role: Any) -> Dict[str, Any]:
        self.limit("p7_invite:user", principal)
        me = self._member(principal, wid, roles=("admin",))
        em = clean(email, "email", maximum=254).lower()
        if not _EMAIL.match(em):
            raise invalid("email", "Enter a valid email address.")
        role = choice(role, "role", ROLES)
        if self.conn.execute("SELECT 1 FROM ws_members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? AND lower(u.email) = ? AND m.status = 'active'", (wid, em)).fetchone():
            raise conflict("ALREADY_MEMBER", "This person is already a member.")
        if self.conn.execute("SELECT COUNT(*) FROM ws_members WHERE workspace_id = ? AND status = 'active'", (wid,)).fetchone()[0] >= MAX_MEMBERS:
            raise AppError("MEMBER_LIMIT", f"A workspace can have up to {MAX_MEMBERS} members.", 409)
        now = self.now()
        self.conn.execute("UPDATE ws_invitations SET status = 'expired', resolved_at = ? WHERE status = 'pending' AND expires_at <= ?", (now, now))
        if self.conn.execute("SELECT 1 FROM ws_invitations WHERE workspace_id = ? AND email = ? AND status = 'pending'", (wid, em)).fetchone():
            raise conflict("ALREADY_INVITED", "There is already a pending invitation for this email. Revoke it to send a new one.")
        token = new_token()
        iid = new_id()
        expires = (_utc(now) + timedelta(days=INVITE_DAYS)).isoformat()
        with self.conn:
            self.conn.execute("INSERT INTO ws_invitations (id, workspace_id, email, role, token_hash, status, invited_by, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (iid, wid, em, role, hash_token(token), "pending", principal.user_id, expires, now))
            self._log(wid, principal, "invitation.created", "invitation", iid, role=role)
        link = f"{self.ctx.settings.frontend_base_url}/invitations#token={token}"
        delivered = True
        try:                                                                       # email goes through the same sender as account emails (a local outbox unless the owner configured SMTP); it never carries workspace contents
            self.ctx.mailer.sender.send(EmailMessage(to=em, subject="You have been invited to a PredictAI workspace",
                                                     body=f"{principal.user_id and (self.user_email(principal.user_id) or 'Someone')} invited you to join a workspace on PredictAI as {role}.\n\nSign in with this email address and open:\n{link}\n\nThe invitation is valid for {INVITE_DAYS} days. If you were not expecting it, ignore this message."))
        except Exception:  # noqa: BLE001
            delivered = False
        return {"id": iid, "email": em, "role": role, "expires_at": expires, "status": "pending", "delivery": "sent" if delivered else "not_sent", "invite_link": link,
                "note": "The link is shown once, to you. Only someone signed in with the invited email address can accept it."}

    def invitations(self, principal: Principal, wid: str) -> Dict[str, Any]:
        self._member(principal, wid, roles=("admin",))
        now = self.now()
        rows = self.conn.execute("SELECT id, email, role, status, expires_at, created_at FROM ws_invitations WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 100", (wid,)).fetchall()
        return {"items": [{**dict(r), "status": "expired" if r["status"] == "pending" and r["expires_at"] <= now else r["status"]} for r in rows]}

    def revoke_invitation(self, principal: Principal, wid: str, invitation_id: str) -> Dict[str, Any]:
        self._member(principal, wid, roles=("admin",))
        n = self.conn.execute("UPDATE ws_invitations SET status = 'revoked', resolved_at = ? WHERE id = ? AND workspace_id = ? AND status = 'pending'", (self.now(), invitation_id, wid)).rowcount
        if not n:
            raise not_found("invitation")
        with self.conn:
            self._log(wid, principal, "invitation.revoked", "invitation", invitation_id)
        return {"revoked": True}

    def my_invitations(self, principal: Principal) -> Dict[str, Any]:
        em = (self.user_email(principal.user_id) or "").lower()
        rows = self.conn.execute("SELECT i.id, i.role, i.expires_at, w.name FROM ws_invitations i JOIN ws_workspaces w ON w.id = i.workspace_id WHERE i.email = ? AND i.status = 'pending' AND i.expires_at > ? AND w.status = 'active'", (em, self.now())).fetchall()
        return {"items": [dict(r) for r in rows]}

    def respond(self, principal: Principal, token: Any, accept: bool) -> Dict[str, Any]:
        self.limit("p7_invite:user", principal)
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise not_found("invitation")
        inv = self.conn.execute("SELECT i.*, w.status AS wstatus FROM ws_invitations i JOIN ws_workspaces w ON w.id = i.workspace_id WHERE i.token_hash = ?", (hash_token(token),)).fetchone()
        email = (self.user_email(principal.user_id) or "").lower()
        if inv is None or inv["email"] != email or inv["status"] != "pending" or inv["expires_at"] <= self.now() or inv["wstatus"] != "active":
            raise not_found("invitation")            # wrong person, used, expired or revoked: all look the same
        now = self.now()
        with self.conn:
            if accept:
                self.conn.execute("INSERT INTO ws_members (workspace_id, user_id, role, status, added_by, expires_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?) "
                                  "ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = excluded.role, status = 'active', expires_at = NULL, updated_at = excluded.updated_at", (inv["workspace_id"], principal.user_id, inv["role"], "active", inv["invited_by"], None, now, now))
            self.conn.execute("UPDATE ws_invitations SET status = ?, resolved_at = ? WHERE id = ?", ("accepted" if accept else "declined", now, inv["id"]))
            self._log(inv["workspace_id"], principal, "invitation.accepted" if accept else "invitation.declined", "invitation", inv["id"], role=inv["role"])
        return {"accepted": accept, "workspace_id": inv["workspace_id"] if accept else None}

    def _admins(self, wid: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM ws_members WHERE workspace_id = ? AND role = 'admin' AND status = 'active'", (wid,)).fetchone()[0]

    def set_role(self, principal: Principal, wid: str, user_id: str, *, role: Any = None, expires_at: Any = None) -> Dict[str, Any]:
        self._member(principal, wid, roles=("admin",))
        target = self.conn.execute("SELECT * FROM ws_members WHERE workspace_id = ? AND user_id = ? AND status = 'active'", (wid, user_id)).fetchone() if isinstance(user_id, str) else None
        if target is None:
            raise not_found("member")
        sets, args = [], []
        if role is not None:
            role = choice(role, "role", ROLES)
            if target["role"] == "admin" and role != "admin" and self._admins(wid) <= 1:
                raise conflict("LAST_ADMIN", "A workspace needs at least one admin. Make someone else an admin first.")
            sets.append("role = ?")
            args.append(role)
        if expires_at is not None:
            sets.append("expires_at = ?")
            args.append(self._future(expires_at, "expires_at", MAX_SHARE_DAYS))
        if not sets:
            raise invalid("role", "Nothing to change.")
        with self.conn:
            self.conn.execute(f"UPDATE ws_members SET {', '.join(sets)}, updated_at = ? WHERE workspace_id = ? AND user_id = ?", (*args, self.now(), wid, user_id))
            self._log(wid, principal, "member.changed", "user", user_id, role=role, expires_at=expires_at)
        self._emit("member_changed", workspace_id=wid, user_id=user_id)
        return self.get_workspace(principal, wid)

    def remove_member(self, principal: Principal, wid: str, user_id: str) -> Dict[str, Any]:
        me = self._member(principal, wid)
        if user_id != principal.user_id and me["role"] != "admin":
            raise AppError("FORBIDDEN_ROLE", "Only an admin can remove other members.", 403)
        target = self.conn.execute("SELECT * FROM ws_members WHERE workspace_id = ? AND user_id = ? AND status = 'active'", (wid, user_id)).fetchone()
        if target is None:
            raise not_found("member")
        if target["role"] == "admin" and self._admins(wid) <= 1:
            raise conflict("LAST_ADMIN", "A workspace needs at least one admin. Make someone else an admin first, or close the workspace.")
        with self.conn:
            self.conn.execute("UPDATE ws_members SET status = 'removed', updated_at = ? WHERE workspace_id = ? AND user_id = ?", (self.now(), wid, user_id))
            # what this person shared into the workspace stays shared by its owner; what they could reach through it ends now (AccessResolver only counts active members)
            self._log(wid, principal, "member.removed" if user_id != principal.user_id else "member.left", "user", user_id)
        self._emit("member_removed", workspace_id=wid, user_id=user_id)
        return {"removed": True}

    # ------------------------------------------------------------------ sharing
    def _future(self, value: Any, field: str, max_days: int) -> str:
        if not isinstance(value, str):
            raise invalid(field, "Use a date and time such as 2026-12-31T00:00:00Z.")
        try:
            dt = _utc(value)
        except ValueError:
            raise invalid(field, "Use a date and time such as 2026-12-31T00:00:00Z.") from None
        now = _utc(self.now())
        if dt <= now:
            raise invalid(field, "Choose a time in the future.")
        if dt > now + timedelta(days=max_days):
            raise invalid(field, f"Choose a time within {max_days} days.")
        return dt.isoformat()

    def share(self, principal: Principal, wid: str, *, resource_type: Any, resource_id: Any, permission: Any = "view", expires_at: Any = None) -> Dict[str, Any]:
        self.limit("p7_share:user", principal)
        me = self._member(principal, wid, roles=("editor", "admin"))
        rtype = choice(resource_type, "resource_type", tuple(RESOURCES))
        perm = choice(permission, "permission", ("view", "edit"))
        acc = self.access.resolve(principal, rtype, resource_id, "view")
        if not acc.is_owner:
            raise not_found(rtype)                                # only the owner shares; a shared-with-me item cannot be passed on
        exp = self._future(expires_at, "expires_at", MAX_SHARE_DAYS) if expires_at is not None else None
        if self.conn.execute("SELECT 1 FROM ws_resources WHERE workspace_id = ? AND resource_type = ? AND resource_id = ? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)", (wid, rtype, resource_id, self.now())).fetchone():
            raise conflict("ALREADY_SHARED", "This is already shared with the workspace. Stop sharing it first to change the permission.")
        gid = new_id()
        with self.conn:
            self.conn.execute("INSERT INTO ws_resources (id, workspace_id, resource_type, resource_id, resource_owner_id, permission, shared_by, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (gid, wid, rtype, resource_id, principal.user_id, perm, principal.user_id, exp, self.now()))
            self._log(wid, principal, "resource.shared", rtype, resource_id, permission=perm, expires_at=exp)
        self.audit(E.SHARE_CHANGED, principal, gid, action="share", resource_type=rtype)
        return {"grant_id": gid, "workspace_id": wid, "resource_type": rtype, "resource_id": resource_id, "permission": perm, "expires_at": exp,
                "note": "Members see this according to their role: viewers can only read; editors and admins get at most the permission you chose. Anything a person has already downloaded stays with them."}

    def unshare(self, principal: Principal, grant_id: str) -> Dict[str, Any]:
        g = self.conn.execute("SELECT * FROM ws_resources WHERE id = ? AND revoked_at IS NULL", (grant_id,)).fetchone() if isinstance(grant_id, str) and len(grant_id) <= 64 else None
        if g is None:
            raise not_found("share")
        is_owner = g["resource_owner_id"] == principal.user_id
        if not is_owner:
            try:
                self._member(principal, g["workspace_id"], roles=("admin",))
            except AppError:
                raise not_found("share") from None
        with self.conn:
            self.conn.execute("UPDATE ws_resources SET revoked_at = ?, revoked_by = ? WHERE id = ?", (self.now(), principal.user_id, grant_id))
            self._log(g["workspace_id"], principal, "resource.unshared", g["resource_type"], g["resource_id"])
        self.audit(E.SHARE_CHANGED, principal, grant_id, action="unshare", resource_type=g["resource_type"])
        self._emit("grant_revoked", grant_id=grant_id, resource_type=g["resource_type"], resource_id=g["resource_id"], workspace_id=g["workspace_id"])
        return {"revoked": True, "note": "Access ends on the next request. Copies already downloaded cannot be recalled."}

    def shares(self, principal: Principal, wid: str) -> Dict[str, Any]:
        self._member(principal, wid)
        now = self.now()
        rows = self.conn.execute("SELECT g.*, u.email AS owner_email FROM ws_resources g JOIN users u ON u.id = g.resource_owner_id WHERE g.workspace_id = ? AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > ?) ORDER BY g.created_at DESC", (wid, now)).fetchall()
        return {"items": [{"grant_id": r["id"], "resource_type": r["resource_type"], "resource_id": r["resource_id"], "permission": r["permission"], "owner": r["owner_email"], "expires_at": r["expires_at"], "created_at": r["created_at"]} for r in rows]}

    def events(self, principal: Principal, wid: str, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        self._member(principal, wid, roles=("admin",))
        lim, off = page(limit, offset, maximum=200, default=50)
        total = self.conn.execute("SELECT COUNT(*) FROM ws_events WHERE workspace_id = ?", (wid,)).fetchone()[0]
        rows = self.conn.execute("SELECT e.*, u.email FROM ws_events e LEFT JOIN users u ON u.id = e.actor_user_id WHERE e.workspace_id = ? ORDER BY e.id DESC LIMIT ? OFFSET ?", (wid, lim, off)).fetchall()
        return {"items": [{"id": r["id"], "actor": r["email"], "event": r["event"], "target_type": r["target_type"], "target_id": r["target_id"], "detail": jload(r["detail"], {}), "at": r["created_at"]} for r in rows], "total": total, "limit": lim, "offset": off}
