"""Authentication use cases: registration, email verification, sign-in, sessions, password
change and recovery, and step-up re-authentication."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from ...core.clock import from_iso, to_iso
from ...db import transaction
from .authorization import Principal, build_principal
from .constants import Event, Purpose, Role, Status
from .context import Context
from .errors import (conflict, invalid_credentials, invalid_token, not_found, rate_limited, reauth_required,
                     session_expired, unauthenticated, validation_failed, wrong_password)
from .ratelimit import Limit
from .tokens import csrf_token, hash_token, new_id, new_token
from .validation import clean_display_name, normalize_email, password_problems, require_valid_password

logger = logging.getLogger("predictai.identity")

TOUCH_INTERVAL_SECONDS = 60
VERIFY_TTL = timedelta(hours=24)
MAGIC_TTL_MINUTES = 15
RESET_TTL_MINUTES = 30
PASSWORD_CHECK_LIMIT = Limit(5, 900)  # wrong password confirmations per user per 15 minutes


@dataclass(frozen=True)
class IssuedSession:
    token: str
    session_id: str
    persistent: bool
    max_age_seconds: int


@dataclass(frozen=True)
class LoginResult:
    session: IssuedSession
    principal: Principal


def confirm_password(ctx: Context, principal: Principal, password: Any):
    """Re-check the signed-in user's password for a sensitive action (rate limited, audited)."""
    ctx.limiter.ensure_allowed("password_check:user", principal.user_id, PASSWORD_CHECK_LIMIT)
    identity = ctx.repo.password_identity(principal.user_id)
    if identity is None or not identity["password_hash"]:
        # An account created through Google/GitHub has no password. Its proof is a recent provider sign-in
        # (the same step-up window as everything else); nothing weaker is accepted.
        ensure_fresh_auth(ctx, principal)
        return None
    ok = isinstance(password, str) and ctx.hasher.verify(password, identity["password_hash"] if identity else None)
    if not ok or identity is None:
        ctx.limiter.record("password_check:user", principal.user_id)
        ctx.audit.record(Event.PASSWORD_CONFIRM_FAILED, "failure", actor=principal.user_id)
        raise wrong_password()
    return identity


def ensure_fresh_auth(ctx: Context, principal: Principal) -> None:
    """Step-up: sensitive actions need a sign-in or password confirmation within the window."""
    limit = timedelta(minutes=ctx.settings.step_up_minutes)
    if principal.reauthenticated_at is None or ctx.clock.now() - principal.reauthenticated_at > limit:
        raise reauth_required()


class AuthService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ sessions
    def csrf_for(self, session_id: str) -> str:
        return csrf_token(self.ctx.settings.secret_key, session_id)

    def _policy(self, roles) -> tuple:
        s = self.ctx.settings
        if Role.OWNER in roles:
            return s.owner_session_idle_minutes, s.owner_session_absolute_hours
        return s.session_idle_minutes, s.session_absolute_hours

    def issue_session(self, user_id: str, *, persistent: bool, rotated_from: Optional[str] = None,
                      reauthenticated_at: Optional[str] = None) -> IssuedSession:
        """Always a brand-new random identifier (never accepts one from the client)."""
        ctx = self.ctx
        roles = ctx.repo.active_roles(user_id)
        _, absolute_hours = self._policy(roles)
        now = ctx.clock.now()
        expires = now + timedelta(hours=absolute_hours)
        token, session_id = new_token(32), new_id()
        ctx.repo.create_session(
            id=session_id, token_hash=hash_token(token), user_id=user_id, now=to_iso(now), absolute_expires_at=to_iso(expires),
            reauthenticated_at=reauthenticated_at or to_iso(now), rotated_from=rotated_from, persistent=persistent,
            device_label=ctx.meta.device_label,
        )
        return IssuedSession(token, session_id, persistent, int((expires - now).total_seconds()))

    def authenticate(self, raw_token: Optional[str]) -> Principal:
        ctx = self.ctx
        if not raw_token:
            raise unauthenticated()
        session = ctx.repo.session_by_token_hash(hash_token(raw_token))
        if session is None or session["revoked_at"] is not None:
            raise session_expired()
        user = ctx.repo.user_by_id(session["user_id"])
        roles = ctx.repo.active_roles(session["user_id"])
        idle_minutes, absolute_hours = self._policy(roles)
        now = ctx.clock.now()
        absolute = from_iso(session["absolute_expires_at"])
        if Role.OWNER in roles:  # a stricter owner policy also applies to sessions that predate promotion
            absolute = min(absolute, from_iso(session["issued_at"]) + timedelta(hours=absolute_hours))
        last_seen = from_iso(session["last_seen_at"])
        if now >= absolute or now - last_seen > timedelta(minutes=idle_minutes):
            ctx.repo.revoke_session(session["id"], "expired", to_iso(now))
            raise session_expired()
        if user is None or user["status"] != Status.ACTIVE:
            ctx.repo.revoke_session(session["id"], f"account_{user['status'] if user else 'missing'}", to_iso(now))
            ctx.audit.record(Event.SESSION_REJECTED, "denied", target=session["user_id"], reason="account_not_active")
            raise session_expired()
        if (now - last_seen).total_seconds() > TOUCH_INTERVAL_SECONDS:
            ctx.repo.touch_session(session["id"], to_iso(now))
        return build_principal(ctx.repo, user, session)

    def logout(self, raw_token: Optional[str], *, everywhere: bool = False) -> None:
        ctx = self.ctx
        session = ctx.repo.session_by_token_hash(hash_token(raw_token)) if raw_token else None
        if session is None:
            return
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            if everywhere:
                ctx.repo.revoke_user_sessions(session["user_id"], "logout_all", now)
            else:
                ctx.repo.revoke_session(session["id"], "logout", now)
            ctx.audit.record(Event.LOGOUT, actor=session["user_id"], metadata={"scope": "all" if everywhere else "current"})

    def list_sessions(self, principal: Principal) -> List[Dict[str, Any]]:
        out = []
        now = self.ctx.clock.now()
        idle, _ = self._policy(principal.roles)
        for row in self.ctx.repo.live_sessions(principal.user_id, to_iso(now)):
            if now - from_iso(row["last_seen_at"]) > timedelta(minutes=idle):
                continue
            out.append({"id": row["id"], "device": row["device_label"] or "Unknown device", "issued_at": row["issued_at"],
                        "last_seen_at": row["last_seen_at"], "current": row["id"] == principal.session_id})
        return out

    def revoke_session(self, principal: Principal, session_id: str) -> None:
        ctx = self.ctx
        row = ctx.repo.session_by_id(session_id)
        if row is None or row["user_id"] != principal.user_id:  # object-level check: others' sessions look absent
            raise not_found()
        with transaction(ctx.conn):
            ctx.repo.revoke_session(session_id, "revoked_by_user", ctx.clock.iso())
            ctx.audit.record(Event.SESSION_REVOKED, actor=principal.user_id, target=principal.user_id)

    def _rotate(self, principal: Principal, *, reason: str, revoke_others: bool, reauth_now: bool) -> IssuedSession:
        """Replace the current session identifier (after sign-in changes) and optionally end all others."""
        ctx = self.ctx
        old = ctx.repo.session_by_id(principal.session_id)
        now = ctx.clock.iso()
        issued = self.issue_session(
            principal.user_id, persistent=bool(old["persistent"]) if old else True, rotated_from=principal.session_id,
            reauthenticated_at=now if reauth_now else (old["reauthenticated_at"] if old else now),
        )
        ctx.repo.revoke_session(principal.session_id, reason, now)
        if revoke_others:
            ctx.repo.revoke_user_sessions(principal.user_id, "password_changed", now, except_id=issued.session_id)
        return issued

    # ------------------------------------------------------------------ registration
    def _issue_token(self, user_id: str, purpose: str, ttl: timedelta) -> str:
        ctx = self.ctx
        raw = new_token(32)
        now = ctx.clock.now()
        ctx.repo.invalidate_tokens(user_id, purpose, to_iso(now))
        ctx.repo.insert_token(id=new_id(), purpose=purpose, token_hash=hash_token(raw), user_id=user_id,
                              now=to_iso(now), expires_at=to_iso(now + ttl))
        return raw

    def register(self, email: Any, password: Any, display_name: Any = None) -> Dict[str, Any]:
        """Same outward result whether or not the address is already registered (no enumeration)."""
        ctx = self.ctx
        ctx.limiter.hit("register:ip", ctx.meta.ip or "unknown")
        problems: List[Dict[str, Any]] = []
        normalized = normalize_email(email)
        if normalized is None:
            problems.append({"field": "email", "issue": "invalid", "message": "Enter a valid email address."})
        name: Optional[str] = None
        try:
            name = clean_display_name(display_name)
        except ValueError as error:
            problems.append({"field": "display_name", "issue": "invalid", "message": str(error)})
        problems += password_problems(password, email=normalized, display_name=name)
        if problems:
            raise validation_failed(problems)

        password_hash = ctx.hasher.hash(password)  # computed first so existing and new emails take equal time
        existing = ctx.repo.user_by_email(normalized)
        needs_verification = ctx.settings.require_email_verification
        if existing is not None:
            ctx.audit.record(Event.REGISTRATION_DUPLICATE, "failure", target=existing["id"], reason="email_exists")
            ctx.mailer.registration_exists(existing["email"])
            return {"verification_required": needs_verification}

        now = ctx.clock.iso()
        user_id = new_id()
        with transaction(ctx.conn):
            ctx.repo.create_user(id=user_id, email=email.strip(), email_normalized=normalized, display_name=name,
                                 status=Status.PENDING if needs_verification else Status.ACTIVE, verified_at=None, now=now)
            ctx.repo.add_identity(id=new_id(), user_id=user_id, provider="password", subject=normalized,
                                  password_hash=password_hash, verified=False, now=now)
            ctx.repo.grant_role(id=new_id(), user_id=user_id, role=Role.USER, granted_by=None, now=now)
            ctx.audit.record(Event.REGISTRATION_CREATED, actor=user_id, target=user_id,
                             metadata={"verification_required": needs_verification})
            token = self._issue_token(user_id, Purpose.EMAIL_VERIFY, VERIFY_TTL) if needs_verification else None
        if token:
            ctx.mailer.verification(email.strip(), token)
        return {"verification_required": needs_verification}

    def verify_email(self, token: Any) -> None:
        ctx = self.ctx
        row = ctx.repo.token_by_hash(Purpose.EMAIL_VERIFY, hash_token(token)) if isinstance(token, str) and token else None
        if row is None or row["consumed_at"] is not None or row["expires_at"] <= ctx.clock.iso():
            raise invalid_token()
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            if not ctx.repo.consume_token(row["id"], now):
                raise invalid_token()
            user = ctx.repo.user_by_id(row["user_id"])
            fields: Dict[str, Any] = {"verified_at": now, "updated_at": now}
            if user["status"] == Status.PENDING:
                fields["status"] = Status.ACTIVE
            ctx.repo.update_user(user["id"], **fields)
            ctx.repo.mark_identities_verified(user["id"])
            ctx.audit.record(Event.EMAIL_VERIFIED, actor=user["id"], target=user["id"])

    def resend_verification(self, email: Any) -> None:
        ctx = self.ctx
        ctx.limiter.hit("verify_resend:ip", ctx.meta.ip or "unknown")
        normalized = normalize_email(email)
        if normalized is None:
            return
        ctx.limiter.hit("verify_resend:email", normalized)
        user = ctx.repo.user_by_email(normalized)
        if user is None or user["status"] != Status.PENDING:
            return
        token = self._issue_token(user["id"], Purpose.EMAIL_VERIFY, VERIFY_TTL)
        ctx.audit.record(Event.EMAIL_VERIFICATION_SENT, target=user["id"])
        ctx.mailer.verification(user["email"], token)

    # ------------------------------------------------------------------ sign-in
    def _lock_limit(self) -> Limit:
        s = self.ctx.settings
        return Limit(s.login_max_failures, s.login_lockout_minutes * 60)

    def login(self, email: Any, password: Any, remember_me: bool = True) -> LoginResult:
        ctx = self.ctx
        ctx.limiter.hit("login:ip", ctx.meta.ip or "unknown")
        normalized = normalize_email(email) if isinstance(password, str) else None
        key = normalized or "invalid"
        wait = ctx.limiter.wait_time("login_fail:account", key, self._lock_limit())
        if wait:
            ctx.audit.record(Event.LOGIN_LOCKED, "denied", reason="too_many_failures")
            raise rate_limited(wait)

        user = ctx.repo.user_by_email(normalized) if normalized else None
        identity = ctx.repo.password_identity(user["id"]) if user else None
        verified = isinstance(password, str) and ctx.hasher.verify(password, identity["password_hash"] if identity else None)
        reason: Optional[str] = None
        if not verified:
            reason = "bad_password" if identity else "unknown_account"
        elif user["status"] != Status.ACTIVE:
            reason = f"account_{user['status']}"
        if reason:
            ctx.limiter.record("login_fail:account", key)
            ctx.audit.record(Event.LOGIN_FAILURE, "failure", target=user["id"] if user else None, reason=reason)
            raise invalid_credentials()

        now = ctx.clock.iso()
        with transaction(ctx.conn):
            if ctx.hasher.needs_rehash(identity["password_hash"]):
                ctx.repo.set_password_hash(user["id"], ctx.hasher.hash(password))
            ctx.repo.touch_identity(identity["id"], now)
            issued = self.issue_session(user["id"], persistent=bool(remember_me))
            ctx.audit.record(Event.LOGIN_SUCCESS, actor=user["id"], target=user["id"])
        session = ctx.repo.session_by_id(issued.session_id)
        return LoginResult(issued, build_principal(ctx.repo, user, session))

    # ------------------------------------------------------------------ magic link
    def request_magic_link(self, email: Any) -> None:
        """Always succeeds from the caller's point of view. A link is mailed only to an ACTIVE account whose mailbox has been verified: an unverified address proves nothing."""
        ctx = self.ctx
        ctx.limiter.hit("magic_link:ip", ctx.meta.ip or "unknown")
        normalized = normalize_email(email)
        if normalized is None:
            return
        ctx.limiter.hit("magic_link:email", normalized)
        user = ctx.repo.user_by_email(normalized)
        if user is None or user["status"] != Status.ACTIVE or user["verified_at"] is None:
            ctx.audit.record(Event.MAGIC_LINK_REQUESTED, "failure", reason="unknown_or_ineligible")
            return
        token = self._issue_token(user["id"], Purpose.MAGIC_LINK, timedelta(minutes=MAGIC_TTL_MINUTES))
        ctx.audit.record(Event.MAGIC_LINK_REQUESTED, target=user["id"])
        ctx.mailer.magic_link(user["email"], token, MAGIC_TTL_MINUTES)

    def consume_magic_link(self, token: Any, remember_me: bool = False) -> LoginResult:
        """One use. The token is looked up by its hash, must be unexpired and unused, and the account must still be active and verified; every failure looks the same."""
        ctx = self.ctx
        ctx.limiter.hit("magic_link_use:ip", ctx.meta.ip or "unknown")
        row = ctx.repo.token_by_hash(Purpose.MAGIC_LINK, hash_token(token)) if isinstance(token, str) and 0 < len(token) <= 200 else None
        user = ctx.repo.user_by_id(row["user_id"]) if row is not None and row["consumed_at"] is None and row["expires_at"] > ctx.clock.iso() else None
        if user is None or user["status"] != Status.ACTIVE or user["verified_at"] is None:
            ctx.audit.record(Event.MAGIC_LINK_REFUSED, "failure", reason="invalid_or_expired")
            raise invalid_token()
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            if not ctx.repo.consume_token(row["id"], now):             # a second use of the same link loses the race and fails
                raise invalid_token()
            issued = self.issue_session(user["id"], persistent=bool(remember_me))
            ctx.audit.record(Event.MAGIC_LINK_USED, actor=user["id"], target=user["id"])
        session = ctx.repo.session_by_id(issued.session_id)
        return LoginResult(issued, build_principal(ctx.repo, user, session))

    def reauthenticate(self, principal: Principal, password: Any) -> IssuedSession:
        ctx = self.ctx
        confirm_password(ctx, principal, password)
        with transaction(ctx.conn):
            issued = self._rotate(principal, reason="rotated", revoke_others=False, reauth_now=True)
            ctx.audit.record(Event.REAUTH, actor=principal.user_id)
        return issued

    # ------------------------------------------------------------------ password management
    def change_password(self, principal: Principal, current_password: Any, new_password: Any) -> IssuedSession:
        ctx = self.ctx
        identity = confirm_password(ctx, principal, current_password)
        if identity is None:
            raise conflict("NO_PASSWORD", "Your account signs in with a provider and has no password yet. Use “Set a password” to add one.")
        problems = password_problems(new_password, email=principal.email, display_name=principal.display_name, field="new_password")
        if not problems and ctx.hasher.verify(new_password, identity["password_hash"]):
            problems.append({"field": "new_password", "issue": "unchanged", "message": "Choose a password different from your current one."})
        if problems:
            raise validation_failed(problems)
        new_hash = ctx.hasher.hash(new_password)
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.set_password_hash(principal.user_id, new_hash)
            ctx.repo.update_user(principal.user_id, password_changed_at=now, updated_at=now)
            issued = self._rotate(principal, reason="rotated", revoke_others=True, reauth_now=True)
            ctx.audit.record(Event.PASSWORD_CHANGED, actor=principal.user_id, target=principal.user_id)
        ctx.mailer.security_notice(principal.email, "password_changed")
        return issued

    def request_recovery(self, email: Any) -> None:
        """Always succeeds from the caller's point of view; mail is sent only for eligible accounts."""
        ctx = self.ctx
        ctx.limiter.hit("recovery:ip", ctx.meta.ip or "unknown")
        normalized = normalize_email(email)
        if normalized is None:
            return
        ctx.limiter.hit("recovery:email", normalized)
        user = ctx.repo.user_by_email(normalized)
        # A password can be reset, or first set (accounts made through a provider), only by whoever controls a verified mailbox.
        eligible = (user is not None and user["status"] in (Status.ACTIVE, Status.PENDING)
                    and (ctx.repo.password_identity(user["id"]) is not None or user["verified_at"] is not None))
        if not eligible:
            ctx.audit.record(Event.PASSWORD_RESET_REQUESTED, "failure", reason="unknown_or_ineligible")
            return
        token = self._issue_token(user["id"], Purpose.PASSWORD_RESET, timedelta(minutes=RESET_TTL_MINUTES))
        ctx.audit.record(Event.PASSWORD_RESET_REQUESTED, target=user["id"])
        ctx.mailer.password_reset(user["email"], token, RESET_TTL_MINUTES)

    def complete_recovery(self, token: Any, new_password: Any) -> None:
        ctx = self.ctx
        ctx.limiter.hit("recovery_complete:ip", ctx.meta.ip or "unknown")
        row = ctx.repo.token_by_hash(Purpose.PASSWORD_RESET, hash_token(token)) if isinstance(token, str) and token else None
        if row is None or row["consumed_at"] is not None or row["expires_at"] <= ctx.clock.iso():
            raise invalid_token()
        user = ctx.repo.user_by_id(row["user_id"])
        if user is None or user["status"] not in (Status.ACTIVE, Status.PENDING):
            raise invalid_token()
        require_valid_password(new_password, email=user["email"], display_name=user["display_name"], field="new_password")
        new_hash = ctx.hasher.hash(new_password)
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            if not ctx.repo.consume_token(row["id"], now):
                raise invalid_token()
            if ctx.repo.password_identity(user["id"]) is None:  # first password of a provider-created account
                ctx.repo.add_identity(id=new_id(), user_id=user["id"], provider="password", subject=user["email_normalized"],
                                      password_hash=new_hash, verified=True, now=now)
            else:
                ctx.repo.set_password_hash(user["id"], new_hash)
            fields: Dict[str, Any] = {"password_changed_at": now, "updated_at": now}
            if user["verified_at"] is None:  # the link reached the mailbox, which proves control of it
                fields["verified_at"] = now
            if user["status"] == Status.PENDING:
                fields["status"] = Status.ACTIVE
            ctx.repo.update_user(user["id"], **fields)
            ctx.repo.mark_identities_verified(user["id"])
            ctx.repo.revoke_user_sessions(user["id"], "password_reset", now)
            ctx.audit.record(Event.PASSWORD_RESET_COMPLETED, actor=user["id"], target=user["id"])
        ctx.mailer.security_notice(user["email"], "password_reset")
