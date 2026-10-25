from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.services.job_store import job_store

router = APIRouter()
session_cookie_scheme = APIKeyCookie(name="incubrix_session", auto_error=False)
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_LENGTH = 32


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def create_password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_KEY_LENGTH,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str | None) -> bool:
    if not encoded_hash:
        return False
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded_hash.split("$")
        if (
            algorithm != "scrypt"
            or int(n) != _SCRYPT_N
            or int(r) != _SCRYPT_R
            or int(p) != _SCRYPT_P
        ):
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        if len(salt) != 16 or len(expected) != _SCRYPT_KEY_LENGTH:
            return False
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_KEY_LENGTH,
    )
    return hmac.compare_digest(actual, expected)


def auth_configuration_ready() -> bool:
    if (
        not settings.auth_username
        or not settings.auth_password_hash
        or not settings.auth_session_secret
        or len(settings.auth_session_secret) < 32
    ):
        return False
    try:
        algorithm, n, r, p, salt_text, digest_text = settings.auth_password_hash.split("$")
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        digest = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        return (
            algorithm == "scrypt"
            and int(n) == _SCRYPT_N
            and int(r) == _SCRYPT_R
            and int(p) == _SCRYPT_P
            and len(salt) == 16
            and len(digest) == _SCRYPT_KEY_LENGTH
            and (
                settings.environment != "production"
                or (
                    settings.auth_cookie_secure
                    and "*" not in settings.cors_origins
                )
            )
        )
    except (ValueError, TypeError):
        return False


def _client_ip(request: Request) -> str:
    if request.client is None or not request.client.host:
        raise HTTPException(
            status_code=503,
            detail={"code": "CLIENT_ADDRESS_UNAVAILABLE", "message": "Request protection is unavailable."},
        )
    return request.client.host


def _rate_limit_key(scope: str, value: str) -> str:
    key_material = settings.auth_session_secret
    if not key_material:
        raise HTTPException(
            status_code=503,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Authentication is not configured on the server."},
        )
    digest = hmac.new(key_material.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{scope}:{digest}"


def _consume_ip_limit(request: Request, limit: int) -> None:
    try:
        allowed = job_store.consume_rate_limit(
            _rate_limit_key("ip", _client_ip(request)),
            limit,
            window_seconds=60,
            now=int(time.time()),
        )
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "REQUEST_PROTECTION_UNAVAILABLE", "message": "Request protection is unavailable."},
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMIT_EXCEEDED", "message": "Too many requests. Try again shortly."},
            headers={"Retry-After": "60"},
        )


def require_authenticated_user(
    request: Request,
    session_cookie: Annotated[str | None, Depends(session_cookie_scheme)],
) -> str:
    if not auth_configuration_ready():
        raise HTTPException(
            status_code=503,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Authentication is not configured on the server."},
        )
    username = request.session.get("username")
    if (
        session_cookie is None
        or not isinstance(username, str)
        or not hmac.compare_digest(username, settings.auth_username or "")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTHENTICATION_REQUIRED", "message": "Sign in to continue."},
            headers={"WWW-Authenticate": "Session"},
        )
    return username


def enforce_api_request(
    request: Request,
    username: Annotated[str, Depends(require_authenticated_user)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        session_token = request.session.get("csrf")
        if (
            not isinstance(session_token, str)
            or not csrf_token
            or not hmac.compare_digest(session_token, csrf_token)
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "CSRF_VALIDATION_FAILED", "message": "The request could not be verified."},
            )
    _consume_ip_limit(request, settings.rate_limit_per_ip_per_minute)
    try:
        allowed = job_store.consume_rate_limit(
            _rate_limit_key("user", username),
            settings.rate_limit_per_user_per_minute,
            window_seconds=60,
            now=int(time.time()),
        )
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "REQUEST_PROTECTION_UNAVAILABLE", "message": "Request protection is unavailable."},
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMIT_EXCEEDED", "message": "Too many requests. Try again shortly."},
            headers={"Retry-After": "60"},
        )
    request.state.user_id = username


@router.get("/auth/csrf")
def get_csrf_token(request: Request, response: Response) -> dict[str, str]:
    token = request.session.get("csrf")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token}


@router.post("/auth/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> dict[str, str]:
    if not auth_configuration_ready():
        raise HTTPException(
            status_code=503,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Authentication is not configured on the server."},
        )
    session_token = request.session.get("csrf")
    if (
        not isinstance(session_token, str)
        or not csrf_token
        or not hmac.compare_digest(session_token, csrf_token)
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "CSRF_VALIDATION_FAILED", "message": "The request could not be verified."},
        )
    _consume_ip_limit(request, settings.login_attempts_per_ip_per_minute)
    if (
        not hmac.compare_digest(body.username, settings.auth_username or "")
        or not verify_password(body.password, settings.auth_password_hash)
    ):
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_CREDENTIALS", "message": "The username or password is incorrect."},
        )
    csrf = secrets.token_urlsafe(32)
    request.session.clear()
    request.session.update({"username": settings.auth_username, "csrf": csrf})
    response.headers["Cache-Control"] = "no-store"
    return {"username": settings.auth_username or "", "csrf_token": csrf}


@router.get("/auth/me")
def current_user(
    response: Response,
    username: Annotated[str, Depends(require_authenticated_user)],
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"username": username}


@router.post("/auth/logout", dependencies=[Depends(enforce_api_request)])
def logout(request: Request, response: Response) -> dict[str, str]:
    request.session.clear()
    response.headers["Cache-Control"] = "no-store"
    return {"status": "logged_out"}
