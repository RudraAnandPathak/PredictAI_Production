"""Owner bootstrap and break-glass recovery. These run only from the server command line
(python -m app.cli); no HTTP route can reach them, and there are no default credentials."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...db import transaction
from .constants import Event, Role, Status
from .context import Context
from .errors import conflict, not_found, validation_failed
from .tokens import new_id
from .validation import clean_display_name, normalize_email, password_problems


def provision_owner(ctx: Context, *, email: str, password: Optional[str], display_name: Optional[str] = None,
                    allow_additional: bool = False) -> Dict[str, Any]:
    """Create the first owner, or promote an existing active account. Refuses when an owner already exists
    unless ``allow_additional`` (a backup owner) is set, and never exceeds the configured owner limit."""
    normalized = normalize_email(email)
    if normalized is None:
        raise validation_failed([{"field": "email", "issue": "invalid", "message": "Enter a valid email address."}])
    owners = ctx.repo.active_owner_ids()
    if owners and not allow_additional:
        raise conflict("OWNER_EXISTS", "An owner already exists. Use --allow-additional to add a backup owner.")
    if len(owners) >= ctx.settings.max_owners:
        raise conflict("OWNER_LIMIT", f"The platform allows at most {ctx.settings.max_owners} owners.")
    now = ctx.clock.iso()
    existing = ctx.repo.user_by_email(normalized)
    if existing is not None:
        if existing["status"] != Status.ACTIVE:
            raise conflict("TARGET_NOT_ELIGIBLE", "That account exists but is not active.")
        if Role.OWNER in ctx.repo.active_roles(existing["id"]):
            raise conflict("ALREADY_OWNER", "That account is already an owner.")
        with transaction(ctx.conn):
            ctx.repo.grant_role(id=new_id(), user_id=existing["id"], role=Role.OWNER, granted_by=None, now=now)
            ctx.repo.revoke_user_sessions(existing["id"], "role_changed", now)
            ctx.repo.bump_authz_version(existing["id"], now)
            ctx.audit.record(Event.OWNER_PROVISIONED, target=existing["id"], reason="promoted_existing_account")
        return {"user_id": existing["id"], "created": False}

    name = clean_display_name(display_name)
    problems = password_problems(password, email=normalized, display_name=name)
    if problems:
        raise validation_failed(problems)
    password_hash = ctx.hasher.hash(password)
    user_id = new_id()
    with transaction(ctx.conn):
        ctx.repo.create_user(id=user_id, email=email.strip(), email_normalized=normalized, display_name=name,
                             status=Status.ACTIVE, verified_at=now, now=now)
        ctx.repo.add_identity(id=new_id(), user_id=user_id, provider="password", subject=normalized,
                              password_hash=password_hash, verified=True, now=now)
        ctx.repo.grant_role(id=new_id(), user_id=user_id, role=Role.USER, granted_by=None, now=now)
        ctx.repo.grant_role(id=new_id(), user_id=user_id, role=Role.OWNER, granted_by=None, now=now)
        ctx.audit.record(Event.OWNER_PROVISIONED, target=user_id, reason="created")
    return {"user_id": user_id, "created": True}


def recover_owner_access(ctx: Context, *, email: str, new_password: str) -> str:
    """Break-glass: set a new password for an existing owner, restore the account if it was
    suspended, and end all of its sessions. Audited with no actor (it was done from the server)."""
    normalized = normalize_email(email)
    user = ctx.repo.user_by_email(normalized) if normalized else None
    if user is None or Role.OWNER not in ctx.repo.active_roles(user["id"]) or user["status"] == Status.DELETED:
        raise not_found()
    problems = password_problems(new_password, email=user["email"], display_name=user["display_name"])
    if problems:
        raise validation_failed(problems)
    new_hash = ctx.hasher.hash(new_password)
    now = ctx.clock.iso()
    with transaction(ctx.conn):
        if ctx.repo.password_identity(user["id"]) is None:
            ctx.repo.add_identity(id=new_id(), user_id=user["id"], provider="password", subject=user["email_normalized"],
                                  password_hash=new_hash, verified=True, now=now)
        else:
            ctx.repo.set_password_hash(user["id"], new_hash)
        ctx.repo.update_user(user["id"], status=Status.ACTIVE, blocked_at=None, blocked_reason=None,
                             password_changed_at=now, updated_at=now, verified_at=user["verified_at"] or now)
        ctx.repo.revoke_user_sessions(user["id"], "owner_recovery", now)
        ctx.repo.bump_authz_version(user["id"], now)
        ctx.audit.record(Event.OWNER_RECOVERED, target=user["id"], reason="server_command")
    ctx.mailer.security_notice(user["email"], "password_reset")
    return user["id"]
