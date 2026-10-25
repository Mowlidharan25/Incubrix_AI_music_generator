from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Request

from app.api import routes
from app.main import app
from app.providers.mock_provider import MockMusicProvider
from app.services.auth import enforce_api_request
from app.services.job_store import job_store


@pytest.fixture(autouse=True)
def isolate_persistent_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path.with_name(f"{tmp_path.name}_jobs.sqlite3")
    monkeypatch.setattr(job_store, "database_path", database_path)
    monkeypatch.setattr(job_store, "max_active_generations", 4)
    monkeypatch.setattr(job_store, "max_generations_per_day", 100)
    monkeypatch.setattr(routes.settings, "auth_username", None)
    monkeypatch.setattr(routes.settings, "auth_password_hash", None)
    monkeypatch.setattr(routes.settings, "auth_session_secret", None)
    monkeypatch.setattr(routes.settings, "stability_api_key", None)
    monkeypatch.setattr(routes.settings, "elevenlabs_api_key", None)
    monkeypatch.setattr(routes.settings, "s3_endpoint_url", None)
    monkeypatch.setattr(routes.settings, "s3_bucket_name", None)
    job_store._initialize()
    job_store._fail_interrupted_jobs()

    async def allow_test_user(request: Request) -> None:
        request.state.user_id = "local"

    monkeypatch.setitem(app.dependency_overrides, enforce_api_request, allow_test_user)
    monkeypatch.setattr(routes.job_manager, "provider", MockMusicProvider())
