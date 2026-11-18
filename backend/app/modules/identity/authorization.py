"""Authorization: who a request acts as, and what that identity may do. Permissions are
computed from the database on every request, so revocation takes effect immediately."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Optional

from ...core.clock import from_iso
from .constants import ROLE_PERMISSIONS, TIER_PERMISSIONS, Role, Tier
from .errors import forbidden
from .repository import Repository


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    display_name: Optional[str]
    status: str
    email_verified: bool
    created_at: str
    roles: FrozenSet[str]
    tiers: FrozenSet[str]
    permissions: FrozenSet[str]
    authz_version: int
    session_id: Optional[str] = None
    reauthenticated_at: Optional[datetime] = None

    @property
    def is_owner(self) -> bool:
        return Role.OWNER in self.roles

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def permissions_for(roles: FrozenSet[str], tiers: FrozenSet[str]) -> FrozenSet[str]:
    granted = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())  # unknown roles grant nothing
    for tier in tiers:
        granted |= TIER_PERMISSIONS.get(tier, frozenset())
    return frozenset(granted)


def build_principal(repo: Repository, user, session=None) -> Principal:
    roles = frozenset(repo.active_roles(user["id"]))
    tiers = frozenset({Tier.STANDARD} | {e["tier"] for e in repo.active_entitlements(user["id"])})
    return Principal(
        user_id=user["id"], email=user["email"], display_name=user["display_name"], status=user["status"],
        email_verified=user["verified_at"] is not None, created_at=user["created_at"],
        roles=roles, tiers=tiers, permissions=permissions_for(roles, tiers), authz_version=user["authz_version"],
        session_id=session["id"] if session is not None else None,
        reauthenticated_at=from_iso(session["reauthenticated_at"]) if session is not None else None,
    )


def require(principal: Principal, permission: str) -> None:
    if not principal.can(permission):
        raise forbidden()
