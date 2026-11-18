"""Roles, tiers, permissions and event names. Add new roles or permissions here only."""
from __future__ import annotations

from types import MappingProxyType
from typing import FrozenSet, Mapping


class Role:
    USER = "user"
    OWNER = "owner"


class Tier:
    STANDARD = "standard"
    SPECIAL = "special"


class Status:
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class Perm:
    # every active account
    ACCOUNT_READ = "account.read"
    ACCOUNT_UPDATE = "account.update"
    ACCOUNT_PASSWORD_CHANGE = "account.password_change"
    ACCOUNT_DELETE = "account.delete"
    SESSIONS_MANAGE_OWN = "sessions.manage_own"
    OAUTH_MANAGE_OWN = "oauth.manage_own"
    ACCESS_CODE_ACTIVATE = "access_code.activate"
    ENTITLEMENTS_READ_OWN = "entitlements.read_own"
    # Phase 3: every active account may manage its own projects and data (quotas are enforced separately)
    PROJECTS_MANAGE_OWN = "projects.manage_own"
    DATA_MANAGE_OWN = "data.manage_own"
    # Special tier (named now so later phases can gate features on them; nothing is built behind them yet)
    UPLOADS_UNLIMITED = "uploads.unlimited"
    ANALYTICS_ADVANCED = "analytics.advanced"
    WORKSPACE_DEVELOPMENT = "workspace.development"
    INTELLIGENCE_LIVE = "intelligence.live"
    COMPUTE_EXTENDED = "compute.extended"
    # Owner
    OWNER_USERS_READ = "owner.users.read"
    OWNER_USERS_BLOCK = "owner.users.block"
    OWNER_ACCESS_CODES_MANAGE = "owner.access_codes.manage"
    OWNER_ENTITLEMENTS_MANAGE = "owner.entitlements.manage"
    OWNER_AUDIT_READ = "owner.audit.read"
    OWNER_OWNERS_MANAGE = "owner.owners.manage"
    OWNER_PROVIDERS_MANAGE = "owner.providers.manage"   # Phase 6: AI provider configuration


_BASE: FrozenSet[str] = frozenset({
    Perm.ACCOUNT_READ, Perm.ACCOUNT_UPDATE, Perm.ACCOUNT_PASSWORD_CHANGE, Perm.ACCOUNT_DELETE,
    Perm.SESSIONS_MANAGE_OWN, Perm.OAUTH_MANAGE_OWN, Perm.ACCESS_CODE_ACTIVATE, Perm.ENTITLEMENTS_READ_OWN,
    Perm.PROJECTS_MANAGE_OWN, Perm.DATA_MANAGE_OWN,
})
_SPECIAL: FrozenSet[str] = frozenset({
    Perm.UPLOADS_UNLIMITED, Perm.ANALYTICS_ADVANCED, Perm.WORKSPACE_DEVELOPMENT,
    Perm.INTELLIGENCE_LIVE, Perm.COMPUTE_EXTENDED,
})
_OWNER: FrozenSet[str] = frozenset({
    Perm.OWNER_USERS_READ, Perm.OWNER_USERS_BLOCK, Perm.OWNER_ACCESS_CODES_MANAGE,
    Perm.OWNER_ENTITLEMENTS_MANAGE, Perm.OWNER_AUDIT_READ, Perm.OWNER_OWNERS_MANAGE, Perm.OWNER_PROVIDERS_MANAGE,
})

# Design note: the owner can use Special features (so the platform can be verified end to end)
# but gets no access to other users' document content: no permission here grants that.
ROLE_PERMISSIONS: Mapping[str, FrozenSet[str]] = MappingProxyType({
    Role.USER: _BASE,
    Role.OWNER: _OWNER | _SPECIAL,
})
TIER_PERMISSIONS: Mapping[str, FrozenSet[str]] = MappingProxyType({
    Tier.STANDARD: frozenset(),
    Tier.SPECIAL: _SPECIAL,
})
KNOWN_ROLES = frozenset(ROLE_PERMISSIONS)
GRANTABLE_TIERS = frozenset({Tier.SPECIAL})


class Event:
    REGISTRATION_CREATED = "registration.created"
    REGISTRATION_DUPLICATE = "registration.duplicate_attempt"
    EMAIL_VERIFIED = "email.verified"
    EMAIL_VERIFICATION_SENT = "email.verification_sent"
    LOGIN_SUCCESS = "login.success"
    MAGIC_LINK_REQUESTED = "login.magic_link_requested"
    MAGIC_LINK_USED = "login.magic_link_used"
    MAGIC_LINK_REFUSED = "login.magic_link_refused"
    LOGIN_FAILURE = "login.failure"
    LOGIN_LOCKED = "login.locked"
    LOGOUT = "logout"
    SESSION_REVOKED = "session.revoked"
    SESSION_REJECTED = "session.rejected"
    REAUTH = "session.reauthenticated"
    PASSWORD_CHANGED = "password.changed"
    PASSWORD_CONFIRM_FAILED = "password.confirmation_failed"
    PASSWORD_RESET_REQUESTED = "password.reset_requested"
    PASSWORD_RESET_COMPLETED = "password.reset_completed"
    PROFILE_UPDATED = "profile.updated"
    ACCOUNT_DELETED = "account.deleted"
    ACCESS_CODE_CREATED = "access_code.created"
    ACCESS_CODE_ACTIVATED = "access_code.activated"
    ACCESS_CODE_FAILED = "access_code.activation_failed"
    ACCESS_CODE_REVOKED = "access_code.revoked"
    ENTITLEMENT_GRANTED = "entitlement.granted"
    ENTITLEMENT_REVOKED = "entitlement.revoked"
    USER_BLOCKED = "user.blocked"
    USER_UNBLOCKED = "user.unblocked"
    OWNER_PROVISIONED = "owner.provisioned"
    OWNER_PROMOTED = "owner.promoted"
    OWNER_DEMOTED = "owner.demoted"
    OWNER_RECOVERED = "owner.recovered"
    OAUTH_LINKED = "oauth.linked"
    OAUTH_UNLINKED = "oauth.unlinked"
    OAUTH_FAILED = "oauth.link_failed"
    OAUTH_LOGIN = "oauth.login"
    OAUTH_REGISTERED = "oauth.registered"
    OAUTH_LOGIN_FAILED = "oauth.login_failed"
    PASSWORD_SET = "password.set"
    AUTHZ_DENIED = "authz.denied"
    CSRF_REJECTED = "csrf.rejected"
    RATE_LIMITED = "rate_limit.exceeded"
    AUDIT_PURGED = "audit.purged"


class Purpose:
    EMAIL_VERIFY = "email_verify"
    PASSWORD_RESET = "password_reset"
    MAGIC_LINK = "magic_link"
    OAUTH_STATE = "oauth_state"
