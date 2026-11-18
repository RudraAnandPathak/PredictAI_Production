"""Uploads and data assets. Uploads are chunked so large files can progress, be cancelled and resume after a break."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.errors import AppError
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.workspace.container import WorkspaceServices
from ..deps import enforce_csrf, get_workspace, require_principal

router = APIRouter(tags=["data"], dependencies=[Depends(enforce_csrf)])


class UploadStart(BaseModel):
    filename: str = Field(max_length=1000)
    size: int
    checksum_sha256: Optional[str] = Field(default=None, max_length=64)


class CleaningPreview(BaseModel):
    operations: List[Dict[str, Any]] = Field(max_length=25)


class Retention(BaseModel):
    retain_until: Optional[str] = Field(default=None, max_length=40)


class Confirm(BaseModel):
    confirm: bool = False


class Rollback(BaseModel):
    version_id: str = Field(max_length=64)
    confirm: bool = False


def _ok(request: Request, data: dict, status_code: Optional[int] = None) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/projects/{project_id}/uploads", status_code=201, summary="Start (or resume) an upload")
def start_upload(project_id: str, body: UploadStart, request: Request, principal: Principal = Depends(require_principal),
                 ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.uploads.start(principal, project_id, body.filename, body.size, body.checksum_sha256))


@router.get("/projects/{project_id}/uploads", summary="Uploads still in progress for a project")
def list_uploads(project_id: str, request: Request, principal: Principal = Depends(require_principal),
                 ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.uploads.list_open(principal, project_id))


@router.get("/uploads/{upload_id}", summary="Which parts of an upload have arrived")
def upload_status(upload_id: str, request: Request, principal: Principal = Depends(require_principal),
                  ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.uploads.status(principal, upload_id))


@router.post("/uploads/{upload_id}/chunks/{index}", summary="Send one part of a file (raw bytes)")
async def upload_chunk(upload_id: str, index: int, request: Request, principal: Principal = Depends(require_principal),
                       ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    limit = request.app.state.settings.upload_chunk_max_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise AppError("CHUNK_TOO_LARGE", "This part is larger than the allowed chunk size.", 413)
    body = bytearray()
    async for part in request.stream():  # never buffer more than the limit, even if Content-Length lied or is missing
        body += part
        if len(body) > limit:
            raise AppError("CHUNK_TOO_LARGE", "This part is larger than the allowed chunk size.", 413)
    return _ok(request, ws.uploads.put_chunk(principal, upload_id, index, bytes(body), request.headers.get("x-chunk-sha256")))


@router.post("/uploads/{upload_id}/complete", summary="Finish an upload: assemble, validate and process the file")
def complete_upload(upload_id: str, request: Request, principal: Principal = Depends(require_principal),
                    ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.uploads.complete(principal, upload_id))


@router.delete("/uploads/{upload_id}", summary="Cancel an upload and discard what was sent")
def cancel_upload(upload_id: str, request: Request, principal: Principal = Depends(require_principal),
                  ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.uploads.cancel(principal, upload_id))


@router.get("/projects/{project_id}/assets", summary="Files in a project")
def list_assets(project_id: str, request: Request, kind: Optional[str] = None, limit: int = 100, offset: int = 0,
                principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assets.list(principal, project_id, kind=kind, limit=limit, offset=offset))


@router.get("/assets/{asset_id}", summary="One file with its version history")
def get_asset(asset_id: str, request: Request, principal: Principal = Depends(require_principal),
              ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assets.get(principal, asset_id))


@router.delete("/assets/{asset_id}", summary="Delete a file and everything derived from it")
def delete_asset(asset_id: str, request: Request, principal: Principal = Depends(require_principal),
                 ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assets.delete(principal, asset_id))


@router.patch("/assets/{asset_id}/retention", summary="Set or clear the date this file is deleted automatically")
def set_retention(asset_id: str, body: Retention, request: Request, principal: Principal = Depends(require_principal),
                  ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assets.set_retention(principal, asset_id, body.retain_until))


@router.get("/assets/{asset_id}/footprint", summary="What deleting this file would remove")
def footprint(asset_id: str, request: Request, principal: Principal = Depends(require_principal),
              ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assets.footprint(principal, asset_id))


@router.get("/assets/{asset_id}/download", summary="Download the original file, byte for byte")
def download_asset(asset_id: str, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)):
    name, size, stream = ws.assets.open_original(principal, asset_id)
    ascii_name = "".join(c if c.isascii() and c.isalnum() or c in "._-" else "_" for c in name) or "download"
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}",
        "Content-Length": str(size), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
    }
    return StreamingResponse(stream, media_type="application/octet-stream", headers=headers)


@router.get("/assets/{asset_id}/extraction", summary="What was extracted from a file: columns and counts, or pages, methods and OCR confidence")
def get_extraction(asset_id: str, request: Request, version_id: Optional[str] = None, principal: Principal = Depends(require_principal),
                   ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.data.extraction(principal, asset_id, version_id))


@router.get("/assets/{asset_id}/preview", summary="A page of rows from a dataset")
def preview_asset(asset_id: str, request: Request, offset: int = 0, limit: int = 50, version_id: Optional[str] = None,
                  principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.data.preview(principal, asset_id, offset=offset, limit=limit, version_id=version_id))


@router.get("/assets/{asset_id}/pages", summary="Page-addressed text of a document")
def document_pages(asset_id: str, request: Request, start: int = 1, limit: int = 5, version_id: Optional[str] = None,
                   principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.data.pages(principal, asset_id, start=start, limit=limit, version_id=version_id))


@router.get("/assets/{asset_id}/structure", summary="Headings and tables found in a document")
def document_structure(asset_id: str, request: Request, version_id: Optional[str] = None, principal: Principal = Depends(require_principal),
                       ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.data.structure(principal, asset_id, version_id))


@router.get("/assets/{asset_id}/profile", summary="The latest profile of a file version (facts, heuristics and estimates are labelled)")
def get_profile(asset_id: str, request: Request, version_id: Optional[str] = None, principal: Principal = Depends(require_principal),
                ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.profiles.get(principal, asset_id, version_id))


@router.post("/assets/{asset_id}/profile", summary="Create a profile (re-uses the existing one unless refresh is set)")
def create_profile(asset_id: str, request: Request, version_id: Optional[str] = None, refresh: bool = False,
                   principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.profiles.generate(principal, asset_id, version_id=version_id, refresh=refresh))


@router.get("/assets/{asset_id}/cleaning/suggestions", summary="Suggested cleaning steps (rules of thumb; nothing is applied)")
def cleaning_suggestions(asset_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.suggestions(principal, asset_id))


@router.post("/assets/{asset_id}/cleaning/preview", summary="Preview cleaning steps with before/after examples; nothing is saved")
def cleaning_preview(asset_id: str, body: CleaningPreview, request: Request, principal: Principal = Depends(require_principal),
                     ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.preview(principal, asset_id, body.operations))


@router.get("/cleaning/{transformation_id}", summary="Re-open a cleaning preview")
def cleaning_get(transformation_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.get_preview(principal, transformation_id))


@router.post("/cleaning/{transformation_id}/confirm", summary="Apply a previewed cleaning as a new version (needs confirm: true)")
def cleaning_confirm(transformation_id: str, body: Confirm, request: Request, principal: Principal = Depends(require_principal),
                     ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.confirm(principal, transformation_id, body.confirm))


@router.post("/cleaning/{transformation_id}/discard", summary="Throw a cleaning preview away")
def cleaning_discard(transformation_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.discard(principal, transformation_id))


@router.get("/assets/{asset_id}/history", summary="Versions and the transformation history of a dataset")
def asset_history(asset_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.history(principal, asset_id))


@router.post("/assets/{asset_id}/rollback", summary="Make an earlier version current again (nothing is deleted; needs confirm: true)")
def asset_rollback(asset_id: str, body: Rollback, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.cleaning.rollback(principal, asset_id, body.version_id, body.confirm))
