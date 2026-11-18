"""Workspaces API: workspaces, members and roles, invitations, sharing and revocation, history. Authorisation lives in CollaborationService and AccessResolver."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["workspaces"], dependencies=[Depends(enforce_csrf)])


class WorkspaceBody(BaseModel):
    name: str = Field(max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)


class InviteBody(BaseModel):
    email: str = Field(max_length=254)
    role: str = Field(max_length=10)


class RespondBody(BaseModel):
    token: str = Field(max_length=200)
    accept: bool = True


class MemberBody(BaseModel):
    role: Optional[str] = Field(default=None, max_length=10)
    expires_at: Optional[str] = Field(default=None, max_length=40)


class ShareBody(BaseModel):
    resource_type: str = Field(max_length=20)
    resource_id: str = Field(max_length=64)
    permission: str = Field(default="view", max_length=10)
    expires_at: Optional[str] = Field(default=None, max_length=40)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/workspaces", status_code=201, summary="Create a workspace; you become its admin")
def create(body: WorkspaceBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.create_workspace(principal, name=body.name, description=body.description))


@router.get("/workspaces", summary="Workspaces you belong to")
def list_workspaces(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.list_workspaces(principal))


@router.get("/workspaces/invitations/mine", summary="Invitations addressed to your email")
def my_invitations(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.my_invitations(principal))


@router.post("/workspaces/invitations/respond", summary="Accept or decline an invitation with its token")
def respond(body: RespondBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.respond(principal, body.token, body.accept))


@router.get("/workspaces/{workspace_id}", summary="A workspace: members, shares and (for admins) invitations")
def get_workspace(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.get_workspace(principal, workspace_id))


@router.delete("/workspaces/{workspace_id}", summary="Close a workspace (its creator only)")
def close(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.close_workspace(principal, workspace_id))


@router.post("/workspaces/{workspace_id}/invitations", status_code=201, summary="Invite someone by email (admins)")
def invite(workspace_id: str, body: InviteBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.invite(principal, workspace_id, email=body.email, role=body.role))


@router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", summary="Revoke a pending invitation (admins)")
def revoke_invitation(workspace_id: str, invitation_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.revoke_invitation(principal, workspace_id, invitation_id))


@router.patch("/workspaces/{workspace_id}/members/{user_id}", summary="Change a member's role or set an end date (admins)")
def set_role(workspace_id: str, user_id: str, body: MemberBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.set_role(principal, workspace_id, user_id, role=body.role, expires_at=body.expires_at))


@router.delete("/workspaces/{workspace_id}/members/{user_id}", summary="Remove a member, or leave yourself")
def remove_member(workspace_id: str, user_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.remove_member(principal, workspace_id, user_id))


@router.post("/workspaces/{workspace_id}/shares", status_code=201, summary="Share one of your own items with the workspace")
def share(workspace_id: str, body: ShareBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.share(principal, workspace_id, resource_type=body.resource_type, resource_id=body.resource_id, permission=body.permission, expires_at=body.expires_at))


@router.get("/workspaces/{workspace_id}/shares", summary="What is currently shared with the workspace")
def shares(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.shares(principal, workspace_id))


@router.delete("/shares/{grant_id}", summary="Stop sharing (the owner of the item, or a workspace admin)")
def unshare(grant_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.unshare(principal, grant_id))


@router.get("/workspaces/{workspace_id}/events", summary="Who did what in the workspace (admins)")
def events(workspace_id: str, request: Request, limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.collab.events(principal, workspace_id, limit=limit, offset=offset))
