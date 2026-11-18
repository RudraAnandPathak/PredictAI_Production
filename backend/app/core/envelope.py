"""JSON response envelopes shared by every endpoint (see shared/contracts)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def success(data: Any, request_id: str) -> Dict[str, Any]:
    return {"data": data, "meta": {"request_id": request_id}}


def failure(
    code: str,
    message: str,
    request_id: str,
    details: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        error["details"] = details
    return {"error": error}
