"""Exception handlers that turn every failure into the standard error envelope."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cookies import expired_cookie_header
from .envelope import failure
from .errors import AppError, ErrorCode, describe_status
from .middleware import security_headers
from .request_context import REQUEST_ID_HEADER, request_id_from_scope

logger = logging.getLogger("predictai.errors")


def _respond(request: Request, status_code: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    request_id = request_id_from_scope(request.scope)
    merged = {**security_headers(), REQUEST_ID_HEADER: request_id, **(headers or {})}
    return JSONResponse(failure(code, message, request_id, details), status_code=status_code, headers=merged)


async def _app_error(request: Request, exc: AppError) -> JSONResponse:
    headers = dict(exc.headers or {})
    if exc.code == "SESSION_EXPIRED":  # drop the dead cookie so the browser stops sending it
        settings = request.app.state.settings
        headers["Set-Cookie"] = expired_cookie_header(settings.session_cookie_name, settings.cookie_secure)
    return _respond(request, exc.status_code, exc.code, exc.message, exc.details, headers=headers)


async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = describe_status(exc.status_code)
    # Never echo exc.detail: it can contain internal information.
    return _respond(request, exc.status_code, code, message, headers=dict(exc.headers or {}))


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Report which field failed and the rule type, never the submitted value.
    details = [
        {"field": ".".join(str(part) for part in error.get("loc", ())), "issue": error.get("type", "invalid")}
        for error in exc.errors()
    ]
    code, message = describe_status(422)
    return _respond(request, 422, code, message, details)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error while serving %s %s", request.method, request.url.path)
    code, message = describe_status(500)
    return _respond(request, 500, code, message)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
