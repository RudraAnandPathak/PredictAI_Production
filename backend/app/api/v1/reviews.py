"""Reviews API: a person reviews their own work; the platform owner reads every review with the account that wrote it (or the deleted-account label)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["reviews"], dependencies=[Depends(enforce_csrf)])


class ReviewBody(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=2000)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/reviews/kinds", summary="Kinds of work that can be reviewed")
def kinds(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reviews.kinds())


@router.get("/reviews/{kind}/{target_id}", summary="My review of one piece of my work (or null)")
def mine(kind: str, target_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reviews.get_mine(principal, kind, target_id))


@router.put("/reviews/{kind}/{target_id}", summary="Write or change my review of one piece of my work")
def put(kind: str, target_id: str, body: ReviewBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reviews.put(principal, kind, target_id, body.rating, body.comment))


@router.delete("/reviews/{kind}/{target_id}", summary="Withdraw my review")
def delete(kind: str, target_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reviews.delete_mine(principal, kind, target_id))


@router.get("/owner/reviews", summary="Owner: every review, newest first, with the account that wrote it")
def owner_list(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7), kind: Optional[str] = None, rating: Optional[str] = None,
               page: int = 1) -> dict:
    return _ok(request, p7.reviews.owner_list(principal, kind, rating, page))
