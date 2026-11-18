"""Projects: create, list, open, rename, archive, restore, delete, activity. Every route is scoped to the signed-in owner."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.workspace.container import WorkspaceServices
from ..deps import enforce_csrf, get_workspace, require_principal

router = APIRouter(tags=["projects"], dependencies=[Depends(enforce_csrf)])


class ProjectCreate(BaseModel):
    name: str = Field(max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    metadata: Optional[Dict[str, Any]] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    metadata: Optional[Dict[str, Any]] = None


class ConfirmDelete(BaseModel):
    confirm_name: str = Field(max_length=500)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/projects", status_code=201, summary="Create a project")
def create_project(body: ProjectCreate, request: Request, principal: Principal = Depends(require_principal),
                   ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.create(principal, body.name, body.description, body.metadata))


@router.get("/projects", summary="Your projects")
def list_projects(request: Request, status: Optional[str] = None, q: Optional[str] = None, limit: int = 50, offset: int = 0,
                  principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.list(principal, status=status, query=q, limit=limit, offset=offset))


@router.get("/projects/{project_id}", summary="One project")
def get_project(project_id: str, request: Request, principal: Principal = Depends(require_principal),
                ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.get(principal, project_id))


@router.patch("/projects/{project_id}", summary="Rename or describe a project")
def update_project(project_id: str, body: ProjectUpdate, request: Request, principal: Principal = Depends(require_principal),
                   ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    changes = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}
    return _ok(request, ws.projects.update(principal, project_id, changes))


@router.post("/projects/{project_id}/archive", summary="Archive a project (data becomes read-only)")
def archive_project(project_id: str, request: Request, principal: Principal = Depends(require_principal),
                    ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.archive(principal, project_id))


@router.post("/projects/{project_id}/restore", summary="Restore an archived project")
def restore_project(project_id: str, request: Request, principal: Principal = Depends(require_principal),
                    ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.restore(principal, project_id))


@router.post("/projects/{project_id}/delete", summary="Permanently delete a project and all of its data")
def delete_project(project_id: str, body: ConfirmDelete, request: Request, principal: Principal = Depends(require_principal),
                   ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.delete(principal, project_id, body.confirm_name))


@router.get("/projects/{project_id}/activity", summary="Recent activity in a project")
def project_activity(project_id: str, request: Request, before_id: Optional[int] = None, limit: int = 50,
                     principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.projects.activity(principal, project_id, before_id=before_id, limit=limit))


@router.get("/workspace/quota", summary="Your upload limits and usage")
def quota(request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.quota.summary(principal))


@router.get("/workspace/deletions", summary="Evidence that deletions completed")
def deletions(request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.governance.list_deletions(principal))


@router.get("/workspace/inventory", summary="What you have stored and what will be deleted soon")
def inventory(request: Request, horizon_days: int = 30, principal: Principal = Depends(require_principal),
              ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assets.inventory(principal, horizon_days))
