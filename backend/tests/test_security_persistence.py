from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import Settings
from app.main import app
from app.providers.mock_provider import MockMusicProvider
from app.schemas.music import JobCreateRequest, JobError, JobStatus
from app.services import auth
from app.services.auth import enforce_api_request
from app.services.audio_storage import AudioStorage
from app.services.job_manager import JobManager
from app.services.job_store import (
    GenerationConcurrencyExceeded,
    GenerationQuotaExceeded,
    SQLiteJobStore,
    job_store,
)


def create_request(prompt: str = "Private test composition") -> JobCreateRequest:
    return JobCreateRequest.model_validate(
        {
            "prompt": prompt,
            "duration_s": 5,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )


def disable_test_auth_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(app.dependency_overrides, enforce_api_request, raising=False)


def configure_test_account(monkeypatch: pytest.MonkeyPatch, username: str = "private-owner") -> None:
    monkeypatch.setattr(auth.settings, "auth_username", username)
    monkeypatch.setattr(auth.settings, "auth_password_hash", auth.create_password_hash("test-password"))
    monkeypatch.setattr(auth.settings, "auth_session_secret", "test-session-secret-" * 3)
    monkeypatch.setattr(auth.settings, "environment", "development")
    monkeypatch.setattr(auth.settings, "auth_cookie_secure", False)


def sign_in(client: TestClient, username: str = "private-owner") -> dict[str, str]:
    csrf = client.get("/api/v1/auth/csrf")
    assert csrf.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "test-password"},
        headers={"X-CSRF-Token": csrf.json()["csrf_token"]},
    )
    assert response.status_code == 200
    return response.json()


def test_protected_api_fails_closed_when_auth_is_missing_or_session_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_test_auth_override(monkeypatch)
    monkeypatch.setattr(auth.settings, "auth_username", None)
    monkeypatch.setattr(auth.settings, "auth_password_hash", None)
    monkeypatch.setattr(auth.settings, "auth_session_secret", None)

    with TestClient(app) as client:
        missing = client.get("/api/v1/jobs")
        assert missing.status_code == 503
        assert missing.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"

        configure_test_account(monkeypatch)
        unauthenticated = client.get("/api/v1/jobs")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_login_uses_signed_http_only_cookie_and_requires_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_test_auth_override(monkeypatch)
    configure_test_account(monkeypatch)

    with TestClient(app) as client:
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        invalid_csrf = client.post(
            "/api/v1/auth/login",
            json={"username": "private-owner", "password": "test-password"},
        )
        assert invalid_csrf.status_code == 403

        invalid_password = client.post(
            "/api/v1/auth/login",
            json={"username": "private-owner", "password": "incorrect-password"},
            headers={"X-CSRF-Token": csrf},
        )
        assert invalid_password.status_code == 401
        assert invalid_password.json()["error"]["code"] == "INVALID_CREDENTIALS"

        signed_in = client.post(
            "/api/v1/auth/login",
            json={"username": "private-owner", "password": "test-password"},
            headers={"X-CSRF-Token": csrf},
        )
        assert signed_in.status_code == 200
        assert "httponly" in signed_in.headers["set-cookie"].lower()
        assert client.get("/api/v1/auth/me").json() == {"username": "private-owner"}

        protected_mutation = client.post("/api/v1/provider", json={"provider": "mock"})
        assert protected_mutation.status_code == 403
        assert protected_mutation.json()["error"]["code"] == "CSRF_VALIDATION_FAILED"

        logged_out = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
        )
        assert logged_out.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401


def test_production_auth_requires_secure_cookie_and_explicit_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(environment="production", _env_file=None).session_cookie_samesite == "none"
    assert Settings(environment="development", _env_file=None).session_cookie_samesite == "lax"

    configure_test_account(monkeypatch)
    monkeypatch.setattr(auth.settings, "environment", "production")
    monkeypatch.setattr(auth.settings, "auth_cookie_secure", False)
    monkeypatch.setattr(auth.settings, "cors_origins", ["https://studio.example"])
    assert not auth.auth_configuration_ready()

    monkeypatch.setattr(auth.settings, "auth_cookie_secure", True)
    monkeypatch.setattr(auth.settings, "auth_cookie_samesite", "none")
    assert auth.auth_configuration_ready()

    monkeypatch.setattr(auth.settings, "cors_origins", ["*"])
    assert not auth.auth_configuration_ready()


def test_cross_user_job_audio_and_manifest_requests_are_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes.settings, "s3_endpoint_url", None)
    monkeypatch.setattr(routes.settings, "s3_bucket_name", None)
    storage = AudioStorage(tmp_path / "audio")
    monkeypatch.setattr(routes, "audio_storage", storage)
    monkeypatch.setattr(routes.job_manager, "audio_storage", storage)
    monkeypatch.setattr(routes.job_manager, "provider", MockMusicProvider())

    job, _ = job_store.create(create_request(), None, owner_id="alice")
    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)
    assert job_store.get(job.job_id, "alice").status == JobStatus.succeeded

    def authenticate_as(user_id: str):
        async def dependency(request: Request) -> None:
            request.state.user_id = user_id
        return dependency

    monkeypatch.setitem(app.dependency_overrides, enforce_api_request, authenticate_as("bob"))
    with TestClient(app) as client:
        assert client.get(f"/api/v1/jobs/{job.job_id}").status_code == 404
        assert client.get(f"/api/v1/jobs/{job.job_id}/audio").status_code == 404
        assert client.get(f"/api/v1/jobs/{job.job_id}/manifest").status_code == 404

        app.dependency_overrides[enforce_api_request] = authenticate_as("alice")
        assert client.get(f"/api/v1/jobs/{job.job_id}").status_code == 200
        assert client.get(f"/api/v1/jobs/{job.job_id}/audio").status_code == 200
        assert client.get(f"/api/v1/jobs/{job.job_id}/manifest").status_code == 200


def test_sqlite_jobs_and_audio_are_retrievable_after_store_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes.settings, "s3_endpoint_url", None)
    monkeypatch.setattr(routes.settings, "s3_bucket_name", None)
    database_path = tmp_path / "restart.sqlite3"
    storage = AudioStorage(tmp_path / "persistent-audio")
    monkeypatch.setattr(job_store, "database_path", database_path)
    job_store._initialize()
    job, _ = job_store.create(create_request(), "restart-key", owner_id="local")
    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)

    reopened_store = SQLiteJobStore(database_path, max_active_generations=1, max_generations_per_day=5)
    reopened_storage = AudioStorage(tmp_path / "persistent-audio")
    assert reopened_store.get(job.job_id, "local").status == JobStatus.succeeded
    assert reopened_storage.path_for(job.job_id, "wav").is_file()
    assert reopened_storage.manifest_path_for(job.job_id).is_file()

    monkeypatch.setattr(routes, "audio_storage", reopened_storage)
    with TestClient(app) as client:
        assert client.get(f"/api/v1/jobs/{job.job_id}").status_code == 200
        audio = client.get(f"/api/v1/jobs/{job.job_id}/audio")
        assert audio.status_code == 200
        assert len(audio.content) > 10_000
        assert client.get(f"/api/v1/jobs/{job.job_id}/manifest").status_code == 200


def test_sqlite_recovers_interrupted_jobs_and_enforces_quota_and_concurrency(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "limits.sqlite3"
    store = SQLiteJobStore(database_path, max_active_generations=1, max_generations_per_day=2)
    first, _ = store.create(create_request("one"), "one", owner_id="owner")
    with pytest.raises(GenerationConcurrencyExceeded):
        store.create(create_request("two"), "two", owner_id="owner")

    interrupted_store = SQLiteJobStore(database_path, max_active_generations=1, max_generations_per_day=2)
    interrupted = interrupted_store.get(first.job_id, "owner")
    assert interrupted is not None
    assert interrupted.status == JobStatus.failed
    assert interrupted.error is not None
    assert interrupted.error.code == "JOB_INTERRUPTED"

    second, _ = interrupted_store.create(create_request("two"), "two", owner_id="owner")
    interrupted_store.update(
        second.job_id,
        status=JobStatus.failed,
        error=JobError(code="TEST_FAILURE", message="Expected test failure.", retryable=False),
    )
    with pytest.raises(GenerationQuotaExceeded):
        interrupted_store.create(create_request("three"), "three", owner_id="owner")
    duplicate, created = interrupted_store.create(create_request("two"), "two", owner_id="owner")
    assert not created
    assert duplicate.job_id == second.job_id


def test_sqlite_rate_limit_persists_and_resets_on_new_window(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "rate.sqlite3")
    assert store.consume_rate_limit("user:test", limit=2, window_seconds=60, now=120)
    assert store.consume_rate_limit("user:test", limit=2, window_seconds=60, now=121)
    assert not store.consume_rate_limit("user:test", limit=2, window_seconds=60, now=122)

    reopened = SQLiteJobStore(tmp_path / "rate.sqlite3")
    assert not reopened.consume_rate_limit("user:test", limit=2, window_seconds=60, now=123)
    assert reopened.consume_rate_limit("user:test", limit=2, window_seconds=60, now=180)


def test_authenticated_api_rate_limit_returns_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_test_auth_override(monkeypatch)
    configure_test_account(monkeypatch)
    monkeypatch.setattr(auth.settings, "rate_limit_per_user_per_minute", 1)

    with TestClient(app) as client:
        sign_in(client)
        assert client.get("/api/v1/provider").status_code == 200
        limited = client.get("/api/v1/provider")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        assert limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_generation_quota_is_reported_without_accepting_an_extra_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes.settings, "s3_endpoint_url", None)
    monkeypatch.setattr(routes.settings, "s3_bucket_name", None)
    storage = AudioStorage(tmp_path / "audio")
    monkeypatch.setattr(routes, "audio_storage", storage)
    monkeypatch.setattr(routes.job_manager, "audio_storage", storage)
    monkeypatch.setattr(job_store, "max_generations_per_day", 1)
    monkeypatch.setattr(routes.job_manager, "provider", MockMusicProvider())
    payload = create_request().model_dump(mode="json")

    with TestClient(app) as client:
        accepted = client.post("/api/v1/music/jobs", json=payload)
        assert accepted.status_code == 202
        limited = client.post(
            "/api/v1/music/jobs",
            json=create_request("second prompt").model_dump(mode="json"),
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "GENERATION_QUOTA_EXCEEDED"


def test_rate_limiter_storage_outage_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_test_auth_override(monkeypatch)
    configure_test_account(monkeypatch)
    with TestClient(app) as client:
        sign_in(client)

        def unavailable(*args, **kwargs) -> bool:
            raise sqlite3.OperationalError("database unavailable")

        monkeypatch.setattr(job_store, "consume_rate_limit", unavailable)
        response = client.get("/api/v1/jobs")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "REQUEST_PROTECTION_UNAVAILABLE"


def test_object_store_write_failure_fails_job_and_cleans_local_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingObjectStore:
        def upload_file(self, *args, **kwargs) -> None:
            raise OSError("simulated object-store outage")

        def delete_object(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(routes.settings, "s3_bucket_name", "test-bucket")
    storage = AudioStorage(tmp_path / "audio")
    storage._s3_required = True
    storage._s3_client = FailingObjectStore()
    job, _ = job_store.create(create_request("storage outage"), None, owner_id="owner")

    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)

    failed = job_store.get(job.job_id, "owner")
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "AUDIO_STORAGE_FAILED"
    assert failed.result is None
    assert not storage.source_path_for(job.job_id, "wav").exists()
    assert not storage.path_for(job.job_id, "wav").exists()
    assert not storage.manifest_path_for(job.job_id).exists()


def test_narration_object_store_failure_removes_partial_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingObjectStore:
        def upload_file(self, *args, **kwargs) -> None:
            raise OSError("simulated object-store outage")

        def delete_object(self, *args, **kwargs) -> None:
            return None

    storage = AudioStorage(tmp_path / "audio")
    monkeypatch.setattr(routes, "audio_storage", storage)
    monkeypatch.setattr(routes.job_manager, "audio_storage", storage)
    job, _ = job_store.create(create_request("narration storage outage"), None, owner_id="local")
    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)
    narration_bytes = storage.path_for(job.job_id, "wav").read_bytes()

    monkeypatch.setattr(routes.settings, "s3_bucket_name", "test-bucket")
    storage._s3_required = True
    storage._s3_client = FailingObjectStore()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/jobs/{job.job_id}/narration",
            content=narration_bytes,
            headers={"Content-Type": "audio/wav"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "NARRATION_MIX_STORAGE_FAILED"
    assert not storage.variant_path_for(job.job_id, "narration-mix", "wav").exists()
    assert not storage.narration_manifest_path_for(job.job_id).exists()
