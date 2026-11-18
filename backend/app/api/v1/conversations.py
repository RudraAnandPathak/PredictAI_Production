"""Conversations: one list and one chat over the dataset assistant and the research assistant (see modules/conversations)."""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.conversations.service import ConversationHub
from ...modules.identity.authorization import Principal
from ...modules.research_intelligence.reasoning.assistant import Assistant
from ...modules.research.container import ResearchServices
from ...modules.workspace.container import WorkspaceServices
from ..deps import enforce_csrf, get_research, get_workspace, require_principal

router = APIRouter(prefix="/conversations", tags=["conversations"], dependencies=[Depends(enforce_csrf)])


class CreateBody(BaseModel):
    kind: str = Field(max_length=20)
    target_id: str = Field(max_length=80)
    title: Optional[str] = Field(default=None, max_length=300)


class SendBody(BaseModel):
    text: str = Field(max_length=2500)
    source_ids: Optional[List[str]] = Field(default=None, max_length=200)
    use_ai: bool = False


class RenameBody(BaseModel):
    title: str = Field(max_length=300)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


def _hub(ws: WorkspaceServices = Depends(get_workspace), rs: ResearchServices = Depends(get_research)) -> ConversationHub:
    return ConversationHub(rs.ctx, ws.assistant, Assistant(rs.ctx, router_factory=rs.providers.router))


@router.get("", summary="Every conversation of the signed-in person, newest first; q searches titles and messages")
def list_conversations(request: Request, q: Optional[str] = None, kind: Optional[str] = None, limit: int = 50, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.list(principal, q, kind, limit))


@router.get("/targets", summary="The datasets and research workspaces a conversation can be about")
def targets(request: Request, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.targets(principal))


@router.post("", status_code=201, summary="Start a conversation about a dataset (kind=data) or a research workspace (kind=research)")
def create(body: CreateBody, request: Request, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.create(principal, body.kind, body.target_id, body.title))


@router.get("/{kind}/{conversation_id}", summary="A conversation with its messages (research answers carry their citations)")
def get(kind: str, conversation_id: str, request: Request, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.get(principal, kind, conversation_id))


@router.post("/{kind}/{conversation_id}/messages", summary="Ask a question in this conversation")
def send(kind: str, conversation_id: str, body: SendBody, request: Request, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.send(principal, kind, conversation_id, body.text, source_ids=body.source_ids, use_ai=body.use_ai))


@router.patch("/{kind}/{conversation_id}", summary="Rename a conversation")
def rename(kind: str, conversation_id: str, body: RenameBody, request: Request, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.rename(principal, kind, conversation_id, body.title))


@router.delete("/{kind}/{conversation_id}", summary="Delete a conversation and its messages")
def delete(kind: str, conversation_id: str, request: Request, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> dict:
    return _ok(request, hub.delete(principal, kind, conversation_id))


@router.get("/{kind}/{conversation_id}/export", summary="Download the conversation as Markdown, with its cited sources")
def export(kind: str, conversation_id: str, principal: Principal = Depends(require_principal), hub: ConversationHub = Depends(_hub)) -> Response:
    out = hub.export_markdown(principal, kind, conversation_id)
    data = out["content"].encode("utf-8"); name = out["filename"]
    headers = {"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type="text/markdown; charset=utf-8", headers=headers)
