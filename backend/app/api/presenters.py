"""Shapes domain objects into API payloads. Only fields meant for clients are ever listed here."""
from __future__ import annotations

from typing import Any, Dict

from ..modules.identity.authorization import Principal
from ..modules.identity.container import Services


def me_payload(services: Services, principal: Principal) -> Dict[str, Any]:
    return {
        "id": principal.user_id,
        "email": principal.email,
        "display_name": principal.display_name,
        "status": principal.status,
        "email_verified": principal.email_verified,
        "created_at": principal.created_at,
        "roles": sorted(principal.roles),
        "tiers": sorted(principal.tiers),
        "permissions": sorted(principal.permissions),
        "authz_version": principal.authz_version,
        "has_password": services.ctx.repo.password_identity(principal.user_id) is not None,
        "connected_providers": sorted(i["provider"] for i in services.ctx.repo.identities_for_user(principal.user_id) if i["provider"] != "password"),
        "csrf_token": services.auth.csrf_for(principal.session_id),
    }
