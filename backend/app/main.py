from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.exception_handlers import (
    error_response,
    http_exception_handler,
    internal_exception_handler,
    validation_exception_handler,
)
from app.api.routes import audio_storage, job_manager as music_job_manager
from app.api.routes import router as api_router
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.services.auth import auth_configuration_ready, router as auth_router
from app.services.job_store import job_store

configure_logging()
logger = logging.getLogger(__name__)
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestObservabilityMiddleware:
    def __init__(self, app: ASGIApp, max_request_bytes: int) -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        is_narration_upload = (
            scope.get("method") == "POST"
            and scope.get("path", "").startswith("/api/v1/jobs/")
            and scope.get("path", "").endswith("/narration")
        )
        requested_id = headers.get(b"x-correlation-id", b"").decode("latin-1")
        correlation_id = (
            requested_id
            if CORRELATION_ID_PATTERN.fullmatch(requested_id)
            else str(uuid4())
        )
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        started = time.perf_counter()
        status_code = 500

        async def observe_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message["headers"].append(
                    (b"x-correlation-id", correlation_id.encode("ascii"))
                )
                if scope.get("path", "").startswith("/api/v1/") and not any(
                    name.lower() == b"cache-control" for name, _ in message["headers"]
                ):
                    message["headers"].append((b"cache-control", b"no-store"))
                if not any(name.lower() == b"x-content-type-options" for name, _ in message["headers"]):
                    message["headers"].append((b"x-content-type-options", b"nosniff"))
            await send(message)

        if is_narration_upload:
            try:
                await self.app(scope, receive, observe_send)
            finally:
                self._log_request(scope, correlation_id, status_code, started)
            return

        content_length = headers.get(b"content-length")
        if content_length:
            try:
                too_large = int(content_length) > self.max_request_bytes
            except ValueError:
                too_large = True
            if too_large:
                self._log_request(scope, correlation_id, 413, started)
                response = error_response(
                    413,
                    "REQUEST_TOO_LARGE",
                    "The request body exceeds the configured size limit.",
                    details={"max_bytes": self.max_request_bytes},
                )
                await response(scope, receive, self._send_with_correlation(send, correlation_id))
                return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_request_bytes:
                self._log_request(scope, correlation_id, 413, started)
                response = error_response(
                    413,
                    "REQUEST_TOO_LARGE",
                    "The request body exceeds the configured size limit.",
                    details={"max_bytes": self.max_request_bytes},
                )
                await response(scope, receive, self._send_with_correlation(send, correlation_id))
                return
            more_body = message.get("more_body", False)

        replayed = False

        async def replay_body() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        try:
            await self.app(scope, replay_body, observe_send)
        finally:
            self._log_request(scope, correlation_id, status_code, started)

    @staticmethod
    def _log_request(
        scope: Scope,
        correlation_id: str,
        status_code: int,
        started: float,
    ) -> None:
        route = scope.get("route")
        route_path = getattr(route, "path", scope.get("path", ""))
        job_id = scope.get("path_params", {}).get("job_id")
        logger.info(
            "HTTP request completed",
            extra={
                "correlation_id": correlation_id,
                "http_method": scope.get("method"),
                "route": route_path,
                "response_status": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "job_id": job_id,
            },
        )

    @staticmethod
    def _send_with_correlation(send: Send, correlation_id: str) -> Send:
        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"].append(
                    (b"x-correlation-id", correlation_id.encode("ascii"))
                )
            await send(message)

        return wrapped_send


app = FastAPI(
    title="IncuBrix AI Music Studio API",
    version="0.2.0",
    description=(
        "Creates music-generation jobs using the configured provider, validates and "
        "stores generated audio locally, and exposes download links only after success. "
        "Jobs use single-host SQLite persistence; duration, arrangement, loopability, and mastering "
        "targets are reported honestly and are not guaranteed by this API."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Correlation-Id", "X-CSRF-Token"],
    expose_headers=["X-Correlation-Id"],
)
app.add_middleware(
    RequestObservabilityMiddleware,
    max_request_bytes=settings.max_request_bytes,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.auth_session_secret or secrets.token_urlsafe(32),
    session_cookie="incubrix_session",
    max_age=60 * 60 * 12,
    same_site=settings.session_cookie_samesite,
    https_only=settings.auth_cookie_secure,
)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, internal_exception_handler)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(api_router, prefix="/api/v1")


@app.get(
    "/health",
    tags=["system"],
    summary="Check that the API process is alive",
    operation_id="healthCheck",
)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/ready",
    tags=["system"],
    summary="Check API dependency readiness",
    operation_id="readinessCheck",
)
def ready() -> dict[str, object]:
    try:
        store_ready = job_store.check_ready()
    except (sqlite3.Error, OSError):
        logger.exception("Persistent job storage readiness check failed")
        store_ready = False
    storage_ready = audio_storage.check_ready()
    provider_ready = music_job_manager.provider.is_configured()
    authentication_ready = auth_configuration_ready()
    ready_status = store_ready and storage_ready and provider_ready and authentication_ready
    return {
        "status": "ready" if ready_status else "not_ready",
        "dependencies": {
            "job_store": {
                "status": "ready" if store_ready else "unavailable",
                "type": "sqlite",
                "persistent": True,
            },
            "audio_generation": {
                "status": "configured" if provider_ready else "not_configured",
                "credentials_configured": provider_ready,
                "provider_connection": "not_checked",
            },
            "audio_storage": {
                "status": "ready" if storage_ready else "unavailable",
                "type": "local_path",
            },
            "authentication": {
                "status": "configured" if authentication_ready else "not_configured",
            },
        },
    }
