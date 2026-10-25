from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.music import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)


def error_response(
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {
            "field": ".".join(str(part) for part in error.get("loc", []) if part != "body"),
            "message": error.get("msg", "Invalid value."),
        }
        for error in exc.errors()
    ]
    return error_response(
        422,
        "INVALID_REQUEST",
        "The request contains invalid values.",
        details={"fields": fields},
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        code = str(exc.detail.get("code", "HTTP_ERROR"))
        message = str(exc.detail.get("message", "The request could not be completed."))
        retryable = bool(exc.detail.get("retryable", False))
        details = exc.detail.get("details", {})
    else:
        code, message, retryable, details = "HTTP_ERROR", str(exc.detail), False, {}
    return error_response(exc.status_code, code, message, retryable, details)


async def internal_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request exception", exc_info=exc)
    return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
