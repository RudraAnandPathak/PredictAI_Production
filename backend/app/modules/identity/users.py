"""User management: profile, self-service deletion, and the owner's user and owner controls."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...db import transaction
from .authentication import confirm_password, ensure_fresh_auth
from .authorization import Principal, require
from .constants import Event, Perm, Role, Status
from .context import Context
from .errors import conflict, not_found, unavailable, validation_failed
from .validation import clean_display_name

MAX_REASON = 200


class UserService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        # Set by the application wiring: removes the person's projects and stored files (Phase 3). None in identity-only use.
        self.account_cleanup = None

    # ------------------------------------------------------------------ self service
    def update_profile(self, principal: Principal, display_name: Any) -> None:
        ctx = self.ctx
        require(principal, Perm.ACCOUNT_UPDATE)
        try:
            name = clean_display_name(display_name)
        except ValueError as error:
            raise validation_failed([{"field": "display_name", "issue": "invalid", "message": str(error)}])
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.update_user(principal.user_id, display_name=name, updated_at=now)
            ctx.audit.record(Event.PROFILE_UPDATED, actor=principal.user_id, target=principal.user_id)

    def delete_account(self, principal: Principal, password: Any) -> None:
        """Erase personal data. The row stays as an anonymous tombstone so audit history stays consistent."""
        ctx = self.ctx
        require(principal, Perm.ACCOUNT_DELETE)
        confirm_password(ctx, principal, password)
        if principal.is_owner and len(ctx.repo.active_owner_ids()) <= 1:
            raise conflict("LAST_OWNER", "You are the only owner. Give another account the owner role before deleting this one.")
        if self.account_cleanup is not None:
            try:
                self.account_cleanup(principal.user_id)
            except Exception:
                raise unavailable("Your data could not be fully removed yet, so the account was not deleted. Please try again shortly.")
        email, now = principal.email, ctx.clock.iso()
        placeholder = f"deleted-{principal.user_id}@deleted.invalid"
        with transaction(ctx.conn):
            ctx.repo.update_user(principal.user_id, email=placeholder, email_normalized=placeholder, display_name=None, status=Status.DELETED,
                                 verified_at=None, blocked_at=None, blocked_reason=None, deleted_at=now, updated_at=now)
            ctx.repo.delete_identities_for_user(principal.user_id)
            ctx.repo.delete_sessions_for_user(principal.user_id)
            ctx.repo.delete_tokens_for_user(principal.user_id)
            ctx.repo.revoke_entitlements_for_user(principal.user_id, principal.user_id, "account_deleted", now)
            ctx.repo.revoke_roles_for_user(principal.user_id, principal.user_id, now)
            ctx.repo.bump_authz_version(principal.user_id, now)
            ctx.audit.record(Event.ACCOUNT_DELETED, actor=principal.user_id, target=principal.user_id)
        ctx.mailer.security_notice(email, "account_deleted")

    # ------------------------------------------------------------------ owner controls
    def _guard(self, actor: Principal, permission: str, *, mutate: bool) -> None:
        require(actor, permission)
        self.ctx.limiter.hit("owner_api:user", actor.user_id)
        if mutate:
            ensure_fresh_auth(self.ctx, actor)

    def _present(self, row) -> Dict[str, Any]:
        repo = self.ctx.repo
        identity = repo.password_identity(row["id"])
        return {
            "id": row["id"], "email": row["email"], "display_name": row["display_name"], "status": row["status"],
            "email_verified": row["verified_at"] is not None, "created_at": row["created_at"],
            "blocked_reason": row["blocked_reason"], "roles": sorted(repo.active_roles(row["id"])),
            "special_access": any(e["tier"] == "special" for e in repo.active_entitlements(row["id"])),
            "last_sign_in_at": identity["last_used_at"] if identity else None,
        }

    def list_users(self, actor: Principal, *, query: Optional[str] = None, status: Optional[str] = None,
                   limit: int = 25, offset: int = 0) -> Dict[str, Any]:
        self._guard(actor, Perm.OWNER_USERS_READ, mutate=False)
        if status and status not in (Status.PENDING, Status.ACTIVE, Status.BLOCKED):
            raise validation_failed([{"field": "status", "issue": "invalid", "message": "Unknown status."}])
        rows, total = self.ctx.repo.search_users(query=(query or "").strip()[:100] or None, status=status,
                                                 limit=max(1, min(int(limit), 100)), offset=max(0, int(offset)))
        return {"items": [self._present(r) for r in rows], "total": total}

    def get_user(self, actor: Principal, user_id: str) -> Dict[str, Any]:
        self._guard(actor, Perm.OWNER_USERS_READ, mutate=False)
        row = self.ctx.repo.user_by_id(user_id)
        if row is None or row["status"] == Status.DELETED:
            raise not_found()
        detail = self._present(row)
        detail["entitlements"] = [
            {"id": e["id"], "tier": e["tier"], "source": e["source"], "activated_at": e["activated_at"]}
            for e in self.ctx.repo.active_entitlements(user_id)
        ]
        return detail

    def block_user(self, actor: Principal, user_id: str, reason: Any = None) -> None:
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_USERS_BLOCK, mutate=True)
        target = ctx.repo.user_by_id(user_id)
        if target is None or target["status"] == Status.DELETED:
            raise not_found()
        if target["id"] == actor.user_id:
            raise conflict("CANNOT_BLOCK_SELF", "You cannot suspend your own account.")
        if Role.OWNER in ctx.repo.active_roles(target["id"]):
            raise conflict("OWNER_PROTECTED", "Owners cannot be suspended. Remove the owner role first.")
        if target["status"] == Status.BLOCKED:
            return
        text = (reason or "").strip()[:MAX_REASON] if isinstance(reason, str) else ""
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.update_user(target["id"], status=Status.BLOCKED, blocked_at=now, blocked_reason=text or None, updated_at=now)
            ctx.repo.revoke_user_sessions(target["id"], "account_blocked", now)
            ctx.repo.bump_authz_version(target["id"], now)
            ctx.audit.record(Event.USER_BLOCKED, actor=actor.user_id, target=target["id"], reason=text or None)
        ctx.mailer.security_notice(target["email"], "account_blocked")

    def unblock_user(self, actor: Principal, user_id: str) -> None:
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_USERS_BLOCK, mutate=True)
        target = ctx.repo.user_by_id(user_id)
        if target is None or target["status"] == Status.DELETED:
            raise not_found()
        if target["status"] != Status.BLOCKED:
            return
        restored = Status.ACTIVE if (target["verified_at"] or not ctx.settings.require_email_verification) else Status.PENDING
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.update_user(target["id"], status=restored, blocked_at=None, blocked_reason=None, updated_at=now)
            ctx.repo.bump_authz_version(target["id"], now)
            ctx.audit.record(Event.USER_UNBLOCKED, actor=actor.user_id, target=target["id"])
        ctx.mailer.security_notice(target["email"], "account_unblocked")

    def query_audit(self, actor: Principal, *, event_type: Optional[str] = None, outcome: Optional[str] = None,
                    actor_user_id: Optional[str] = None, target_user_id: Optional[str] = None, since: Optional[str] = None,
                    until: Optional[str] = None, before_id: Optional[int] = None, limit: int = 50) -> Dict[str, Any]:
        self._guard(actor, Perm.OWNER_AUDIT_READ, mutate=False)
        if outcome and outcome not in ("success", "failure", "denied"):
            raise validation_failed([{"field": "outcome", "issue": "invalid", "message": "Unknown outcome."}])
        return self.ctx.audit.query(event_type=(event_type or "").strip()[:60] or None, outcome=outcome or None, actor=actor_user_id or None,
                                    target=target_user_id or None, since=since or None, until=until or None, before_id=before_id, limit=limit)

    def list_owners(self, actor: Principal) -> List[Dict[str, Any]]:
        self._guard(actor, Perm.OWNER_OWNERS_MANAGE, mutate=False)
        return [self._present(self.ctx.repo.user_by_id(uid)) for uid in self.ctx.repo.active_owner_ids()]

    def promote_owner(self, actor: Principal, user_id: str) -> None:
        """Add a backup owner. Ordinary users can never reach this: it needs the owner permission and a fresh sign-in."""
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_OWNERS_MANAGE, mutate=True)
        target = ctx.repo.user_by_id(user_id)
        if target is None or target["status"] != Status.ACTIVE:
            raise conflict("TARGET_NOT_ELIGIBLE", "Only an active account can become an owner.")
        if target["verified_at"] is None and ctx.settings.require_email_verification:
            raise conflict("TARGET_NOT_ELIGIBLE", "The account's email address must be verified first.")
        roles = ctx.repo.active_roles(target["id"])
        if Role.OWNER in roles:
            raise conflict("ALREADY_OWNER", "That account is already an owner.")
        if len(ctx.repo.active_owner_ids()) >= ctx.settings.max_owners:
            raise conflict("OWNER_LIMIT", f"The platform allows at most {ctx.settings.max_owners} owners.")
        now = ctx.clock.iso()
        from .tokens import new_id

        with transaction(ctx.conn):
            ctx.repo.grant_role(id=new_id(), user_id=target["id"], role=Role.OWNER, granted_by=actor.user_id, now=now)
            ctx.repo.revoke_user_sessions(target["id"], "role_changed", now)  # new sessions get the owner policy
            ctx.repo.bump_authz_version(target["id"], now)
            ctx.audit.record(Event.OWNER_PROMOTED, actor=actor.user_id, target=target["id"])
        ctx.mailer.security_notice(target["email"], "owner_promoted")

    def demote_owner(self, actor: Principal, user_id: str) -> None:
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_OWNERS_MANAGE, mutate=True)
        if user_id == actor.user_id:
            raise conflict("CANNOT_DEMOTE_SELF", "Ask another owner to remove your owner role.")
        target = ctx.repo.user_by_id(user_id)
        if target is None or Role.OWNER not in ctx.repo.active_roles(user_id):
            raise not_found()
        if len(ctx.repo.active_owner_ids()) <= 1:
            raise conflict("LAST_OWNER", "The last owner cannot be removed.")
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.revoke_role(target["id"], Role.OWNER, actor.user_id, now)
            ctx.repo.revoke_user_sessions(target["id"], "role_changed", now)
            ctx.repo.bump_authz_version(target["id"], now)
            ctx.audit.record(Event.OWNER_DEMOTED, actor=actor.user_id, target=target["id"])
        ctx.mailer.security_notice(target["email"], "owner_demoted")
