"""PredictAI Brain API: status for everyone signed in; setup, model list and a test call for the platform owner."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictBool

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["brain"], dependencies=[Depends(enforce_csrf)])


class GeminiSetupBody(BaseModel):
    model: str = Field(max_length=120)
    daily_request_limit: Optional[int] = Field(default=None, ge=1, le=100000)
    requests_per_minute: Optional[int] = Field(default=None, ge=1, le=10000)
    acknowledge_data_use: StrictBool = False
    enable: StrictBool = True


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/brain/status", summary="Is the Brain ready, what has it used today, and what happens when it cannot answer")
def status(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.brain.status(principal))


@router.post("/brain/setup/gemini", summary="Set up Google Gemini (AI Studio) as the shared model provider; the key stays in an environment variable")
def setup_gemini(body: GeminiSetupBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.brain.setup_gemini(principal, model=body.model, daily_request_limit=body.daily_request_limit, requests_per_minute=body.requests_per_minute,
                                              acknowledge_data_use=body.acknowledge_data_use, enable=body.enable))


@router.get("/brain/models", summary="Model names the configured key can see")
def models(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.brain.models(principal))


@router.post("/brain/test", summary="One tiny real request to check the whole chain (it counts against the daily cap)")
def test(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.brain.test(principal))
