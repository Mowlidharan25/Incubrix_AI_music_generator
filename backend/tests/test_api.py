from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import math
from pathlib import Path
import socket
import struct
from uuid import uuid4
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.core.logging_config import JsonLogFormatter
from app.providers.mock_provider import MockMusicProvider
from app.providers.music_provider import GeneratedAudio, ElevenLabsMusicProvider, ProviderError
from app.providers.stability_provider import StabilityMusicProvider
from app.schemas.music import JobCreateRequest, JobStatus
from app.services.audio_processing import AudioProcessingError
from app.services import audio_processing
from app.services.audio_storage import AudioStorage
from app.services.job_manager import JobManager
from app.services.job_store import InMemoryJobStore, job_store

client = TestClient(app)


def make_test_wav(duration_s: int = 5, sample_rate: int = 48000) -> bytes:
    frames = b"".join(
        struct.pack(
            "<hh",
            int(7000 * math.sin(2 * math.pi * 330 * frame / sample_rate)),
            int(7000 * math.sin(2 * math.pi * 330 * frame / sample_rate)),
        )
        for frame in range(sample_rate)
    )
    with io.BytesIO() as output:
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames * duration_s)
        return output.getvalue()


@pytest.fixture(autouse=True)
def prevent_paid_provider_calls(monkeypatch) -> None:
    monkeypatch.setattr(routes.job_manager, "provider", StabilityMusicProvider(None))


def create_payload(**overrides):
    payload = {
        "prompt": "Warm optimistic acoustic music",
        "duration_s": 45,
        "sections": [
            {"start_s": 0, "mood": "calm intro"},
            {"start_s": 15, "mood": "build-up"},
            {"start_s": 32, "mood": "uplifting peak"},
        ],
        "tempo_bpm": 100,
        "key": "C",
        "loopable": False,
        "seed": 42,
        "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav", "mp3"]},
    }
    payload.update(overrides)
    return payload


def create_completed_mock_job(storage: AudioStorage) -> str:
    routes.audio_storage = storage
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Test narration mix",
            "duration_s": 5,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = job_store.create(request, None)
    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)
    assert job_store.get(job.job_id).status == JobStatus.succeeded
    return job.job_id


def test_health_and_readiness_endpoints() -> None:
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"]["job_store"] == {
        "status": "ready",
        "type": "sqlite",
        "persistent": True,
    }
    assert ready.json()["status"] == "not_ready"
    audio_generation = ready.json()["dependencies"]["audio_generation"]
    assert ready.json()["dependencies"]["audio_generation"]["credentials_configured"] is False
    assert ready.json()["dependencies"]["audio_generation"]["status"] == "not_configured"
    assert ready.json()["dependencies"]["audio_generation"]["provider_connection"] == "not_checked"
    assert ready.json()["dependencies"]["authentication"]["status"] == "not_configured"


def test_provider_balance_check_uses_backend_key_without_returning_it(monkeypatch) -> None:
    test_key = "test-balance-key"
    request_headers: list[dict[str, str]] = []

    def fake_get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        assert url == "https://api.stability.ai/v1/user/balance"
        assert timeout == 5.0
        request_headers.append(headers)
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"credits": 0},
        )

    monkeypatch.setattr(routes.settings, "stability_api_key", test_key)
    monkeypatch.setattr(routes.settings, "music_provider", "stability")
    monkeypatch.setattr(routes.job_manager, "provider", StabilityMusicProvider(test_key))
    monkeypatch.setattr(httpx, "get", fake_get)

    result = routes.get_provider_config()

    assert request_headers == [{"Authorization": f"Bearer {test_key}"}]
    assert result["stability_credits"] == 0
    assert result["is_configured"] is True
    assert test_key not in repr(result)


def test_mock_provider_status_matches_frontend_contract_without_balance_call(monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "music_provider", "mock")
    monkeypatch.setattr(routes.settings, "stability_api_key", "configured-test-key")
    monkeypatch.setattr(routes.job_manager, "provider", MockMusicProvider())

    def unexpected_balance_request(*args, **kwargs):
        raise AssertionError("Mock provider status must not query Stability billing.")

    monkeypatch.setattr(httpx, "get", unexpected_balance_request)

    result = routes.get_provider_config()

    assert result == {
        "provider": "mock",
        "is_configured": True,
        "stability_credits": None,
        "model": "test-fixture",
        "has_api_key": False,
    }


def test_elevenlabs_provider_status_reports_selected_model_without_balance_call(monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "music_provider", "elevenlabs")
    monkeypatch.setattr(routes.settings, "elevenlabs_api_key", "configured-test-key")
    monkeypatch.setattr(routes.settings, "elevenlabs_model_id", "music_v2_5")
    monkeypatch.setattr(
        routes.job_manager,
        "provider",
        ElevenLabsMusicProvider("configured-test-key", model_id="music_v2_5"),
    )

    def unexpected_balance_request(*args, **kwargs):
        raise AssertionError("ElevenLabs status must not query Stability billing.")

    monkeypatch.setattr(httpx, "get", unexpected_balance_request)

    result = routes.get_provider_config()

    assert result == {
        "provider": "elevenlabs",
        "is_configured": True,
        "stability_credits": None,
        "model": "music_v2_5",
        "has_api_key": True,
    }


def test_provider_config_rejects_api_keys_from_clients(monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "music_provider", "mock")
    monkeypatch.setattr(routes.settings, "stability_api_key", None)
    monkeypatch.setattr(routes.settings, "elevenlabs_api_key", None)
    monkeypatch.setattr(routes.job_manager, "provider", MockMusicProvider())

    response = client.post(
        "/api/v1/provider",
        json={"provider": "stability", "api_key": "not-a-real-key"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert routes.settings.music_provider == "mock"
    assert routes.settings.stability_api_key is None
    assert routes.settings.elevenlabs_api_key is None


def test_s3_mirroring_does_not_fall_back_to_default_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "s3_endpoint_url", "http://minio.test:9000")
    monkeypatch.setattr(routes.settings, "s3_bucket_name", "audio")
    monkeypatch.setattr(routes.settings, "s3_access_key", None)
    monkeypatch.setattr(routes.settings, "s3_secret_key", None)

    storage = AudioStorage(tmp_path)

    assert storage._s3_client is None


def test_music_job_rejects_unapproved_outbound_urls(monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "outbound_url_allowed_hosts", [])
    response = client.post(
        "/api/v1/music/jobs",
        json=create_payload(narration_url="http://127.0.0.1/internal.wav"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OUTBOUND_URL_NOT_ALLOWED"


def test_valid_job_is_accepted_and_retains_configuration() -> None:
    response = client.post("/api/v1/music/jobs", json=create_payload())
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["progress"] == 0
    assert data["job_id"]

    status_response = client.get(f"/api/v1/jobs/{data['job_id']}")
    assert status_response.status_code == 200
    job = status_response.json()
    assert job["prompt"] == "Warm optimistic acoustic music"
    assert job["duration_s"] == 45
    stored_job = job_store.get(data["job_id"])
    assert stored_job is not None
    assert stored_job.request.output.sample_rate == 48000
    assert [section.mood for section in stored_job.request.sections] == [
        "calm intro",
        "build-up",
        "uplifting peak",
    ]


def test_optional_fields_use_documented_defaults() -> None:
    response = client.post(
        "/api/v1/music/jobs",
        json={"prompt": "A simple instrumental", "duration_s": 20},
    )
    assert response.status_code == 202
    stored_job = job_store.get(response.json()["job_id"])
    assert stored_job is not None
    assert stored_job.request.sections == []
    assert stored_job.request.loopable is False
    assert stored_job.request.output.sample_rate == 48000
    assert stored_job.request.output.target_lufs == -16
    assert stored_job.request.output.formats == ["wav", "mp3"]


def test_blank_prompt_is_rejected_with_structured_error() -> None:
    response = client.post(
        "/api/v1/music/jobs",
        json={"prompt": "  ", "duration_s": 30},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["retryable"] is False


def test_duration_below_and_above_limits_are_rejected() -> None:
    for duration in (4, 301):
        response = client.post(
            "/api/v1/music/jobs",
            json={"prompt": "A test prompt", "duration_s": duration},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_missing_required_fields_are_rejected() -> None:
    response = client.post("/api/v1/music/jobs", json={"prompt": "A test prompt"})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"]


def test_invalid_section_timestamps_are_rejected() -> None:
    payload = create_payload(
        sections=[
            {"start_s": 0, "mood": "calm"},
            {"start_s": -1, "mood": "upbeat"},
        ]
    )
    response = client.post("/api/v1/music/jobs", json=payload)
    assert response.status_code == 422


def test_sections_cannot_be_outside_duration_or_out_of_order() -> None:
    for sections in (
        [{"start_s": 30, "mood": "calm"}],
        [{"start_s": 20, "mood": "calm"}, {"start_s": 10, "mood": "upbeat"}],
    ):
        response = client.post(
            "/api/v1/music/jobs",
            json=create_payload(duration_s=30, sections=sections),
        )
        assert response.status_code == 422


def test_unsupported_output_format_is_rejected() -> None:
    response = client.post(
        "/api/v1/music/jobs",
        json=create_payload(output={"formats": ["flac"]}),
    )
    assert response.status_code == 422


def test_invalid_music_configuration_is_rejected() -> None:
    for overrides in (
        {"tempo_bpm": 300},
        {"key": "H"},
        {"seed": -1},
        {"output": {"sample_rate": 1000}},
        {"output": {"sample_rate": 44100}},
        {"output": {"target_lufs": 0}},
        {"narration_url": "file:///private/audio.wav"},
    ):
        response = client.post("/api/v1/music/jobs", json=create_payload(**overrides))
        assert response.status_code == 422


def test_unknown_job_returns_structured_404() -> None:
    response = client.get("/api/v1/jobs/unknown-job")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_recent_job_history_lists_real_jobs_and_validates_limit() -> None:
    created = client.post(
        "/api/v1/music/jobs",
        json={"prompt": "History entry", "duration_s": 20},
    )
    assert created.status_code == 202

    response = client.get("/api/v1/jobs?limit=10")
    assert response.status_code == 200
    matching = [job for job in response.json() if job["job_id"] == created.json()["job_id"]]
    assert len(matching) == 1
    assert matching[0]["prompt"] == "History entry"
    assert client.get("/api/v1/jobs?limit=0").json()["error"]["code"] == "INVALID_JOB_LIMIT"


def test_unconfigured_provider_fails_job_honestly() -> None:
    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate(
        {"prompt": "A quiet piano", "duration_s": 20}
    )
    job, created = store.create(request, None)
    assert created is True
    assert job.status == JobStatus.queued
    manager = JobManager(store, ElevenLabsMusicProvider(None), AudioStorage(Path(".")))
    manager.process_job(job.job_id)
    finished = store.get(job.job_id)
    assert finished is not None
    assert finished.status == JobStatus.failed
    assert finished.progress == 5
    assert finished.error is not None
    assert finished.error.code == "PROVIDER_NOT_CONFIGURED"
    assert finished.result is None


def test_test_mock_provider_saves_audio_with_actual_metadata(tmp_path) -> None:
    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Test fixture",
            "duration_s": 20,
            "sections": [{"start_s": 0, "mood": "calm"}],
            "tempo_bpm": 100,
            "key": "C",
            "loopable": True,
            "narration_url": "https://example.com/narration.mp3",
        }
    )
    job, _ = store.create(request, None)
    storage = AudioStorage(tmp_path)
    JobManager(store, MockMusicProvider(), storage).process_job(job.job_id)
    finished = store.get(job.job_id)
    assert finished is not None
    assert finished.status == JobStatus.succeeded
    assert finished.progress == 100
    assert finished.result is not None
    assert finished.result.provider == "test-mock"
    assert finished.result.model == "test-fixture"
    assert finished.result.is_ai_generated is False
    assert finished.result.format == "wav"
    assert finished.result.requested_duration_s == request.duration_s
    assert finished.result.requested_sample_rate == 48000
    assert finished.result.target_integrated_lufs == request.output.target_lufs
    assert finished.result.true_peak_ceiling_dbtp == -1
    assert finished.result.sample_rate == 48000
    assert finished.result.channel_count == 2
    assert finished.result.duration_s == 20
    assert finished.result.integrated_lufs is not None
    assert abs(finished.result.integrated_lufs - request.output.target_lufs) < 0.2
    assert finished.result.true_peak_dbtp is not None
    assert finished.result.true_peak_dbtp <= -1
    assert finished.result.generation_completed_at >= finished.result.generation_started_at
    assert finished.result.processing_duration_s >= 0
    assert finished.result.size_bytes > 0
    assert storage.path_for(job.job_id, "wav").is_file()
    assert storage.source_path_for(job.job_id, "wav").is_file()
    manifest = json.loads(storage.manifest_path_for(job.job_id).read_text(encoding="utf-8"))
    assert manifest["is_ai_generated"] is False
    assert manifest["processed_file"] == f"{job.job_id}.wav"
    assert manifest["provider_source"]["file"] == f"{job.job_id}.source.wav"
    assert manifest["provider_source"]["format"] == "wav"
    assert manifest["processing"]["requested"] == {
        "duration_s": request.duration_s,
        "sample_rate": 48000,
        "integrated_lufs": -16,
        "true_peak_ceiling_dbtp": -1,
    }
    assert manifest["processing"]["measured"]["duration_s"] == finished.result.duration_s
    assert manifest["processing"]["measured"]["sample_rate"] == 48000
    assert manifest["processing"]["measured"]["integrated_lufs"] == finished.result.integrated_lufs
    assert manifest["processing"]["measured"]["true_peak_dbtp"] == finished.result.true_peak_dbtp
    assert manifest["processing"]["requirements"]["duration"]["status"] == "passed"
    assert manifest["processing"]["requirements"]["sample_rate"]["status"] == "passed"
    assert manifest["processing"]["requirements"]["integrated_lufs"]["status"] == "passed"
    assert manifest["processing"]["requirements"]["true_peak"]["status"] == "passed"
    assert manifest["licensing"]["status"] == "plan_specific_and_not_independently_verified"
    assert manifest["output"]["channel_count"] == 2
    assert any("not AI-generated" in warning for warning in finished.warnings or [])
    assert not any("Exact duration has not been processed" in warning for warning in finished.warnings or [])
    assert any("beat alignment and exact section boundaries are not analyzed" in warning for warning in finished.warnings or [])
    assert finished.result.loop_validation.status == "passed"
    assert any("Tempo and key were included as prompt guidance" in warning for warning in finished.warnings or [])
    assert any(
        "narration URL was configured but could not be downloaded" in warning
        for warning in finished.warnings or []
    )


def test_job_fails_when_measured_loudness_is_outside_tolerance(tmp_path, monkeypatch) -> None:
    original_process_audio = audio_processing.process_audio

    def return_out_of_tolerance_loudness(*args, **kwargs):
        processed = original_process_audio(*args, **kwargs)
        assert processed.integrated_lufs is not None
        return replace(processed, integrated_lufs=processed.integrated_lufs + 1.01)

    monkeypatch.setattr(
        "app.services.job_manager.process_audio",
        return_out_of_tolerance_loudness,
    )
    store = InMemoryJobStore()
    storage = AudioStorage(tmp_path)
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Loudness validation regression",
            "duration_s": 5,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = store.create(request, None)

    JobManager(store, MockMusicProvider(), storage).process_job(job.job_id)

    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "AUDIO_LOUDNESS_VALIDATION_FAILED"
    assert failed.result is None
    assert not storage.path_for(job.job_id, "wav").exists()
    assert not storage.manifest_path_for(job.job_id).exists()


def test_missing_mp3_encoder_fails_before_provider_generation(tmp_path, monkeypatch) -> None:
    class TrackingProvider(MockMusicProvider):
        called = False

        def generate(self, request: JobCreateRequest, job_id: str) -> GeneratedAudio:
            self.called = True
            return super().generate(request, job_id)

    monkeypatch.setattr(audio_processing.shutil, "which", lambda _: None)
    provider = TrackingProvider()
    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate(
        {"prompt": "MP3 preflight", "duration_s": 5, "output": {"formats": ["mp3"]}}
    )
    job, _ = store.create(request, None)

    JobManager(store, provider, AudioStorage(tmp_path)).process_job(job.job_id)

    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "MP3_ENCODER_UNAVAILABLE"
    assert provider.called is False


def test_completed_mock_audio_is_downloadable_with_real_bytes(tmp_path, monkeypatch) -> None:
    routes.audio_storage = AudioStorage(tmp_path)
    request = JobCreateRequest.model_validate(
        {"prompt": "Test download fixture", "duration_s": 20}
    )
    job, _ = job_store.create(request, None)
    JobManager(job_store, MockMusicProvider(), routes.audio_storage).process_job(job.job_id)
    response = client.get(f"/api/v1/jobs/{job.job_id}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert len(response.content) == job_store.get(job.job_id).result.size_bytes
    monkeypatch.setattr(routes, "audio_storage", AudioStorage(routes.settings.audio_storage_dir))


def test_completed_mock_manifest_is_available_as_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(routes, "audio_storage", AudioStorage(tmp_path))
    request = JobCreateRequest.model_validate(
        {"prompt": "Manifest fixture", "duration_s": 20}
    )
    job, _ = job_store.create(request, None)
    JobManager(job_store, MockMusicProvider(), routes.audio_storage).process_job(job.job_id)

    response = client.get(f"/api/v1/jobs/{job.job_id}/manifest")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == 'attachment; filename="music_manifest.json"'
    manifest = response.json()
    assert manifest["job_id"] == job.job_id
    assert manifest["provider"] == "test-mock"
    assert manifest["output"]["is_ai_generated"] is False
    assert manifest["provider_source"]["file"].endswith(".source.wav")
    assert manifest["processed_file"].endswith(".wav")


def test_loop_request_is_crossfaded_and_arrangement_metadata_is_explicit(tmp_path) -> None:
    storage = AudioStorage(tmp_path)
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Loop and arrangement fixture",
            "duration_s": 20,
            "tempo_bpm": 100,
            "sections": [{"start_s": 12, "mood": "lift"}],
            "loopable": True,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = job_store.create(request, None)
    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)

    completed = job_store.get(job.job_id)
    assert completed is not None and completed.result is not None
    assert completed.result.loop_validation.status == "passed"
    assert completed.result.loop_validation.crossfade_duration_s == 0.25
    manifest = json.loads(storage.manifest_path_for(job.job_id).read_text(encoding="utf-8"))
    assert manifest["arrangement"]["sections"][0]["nearest_4_4_bar"] == 6
    assert manifest["arrangement"]["audio_beat_grid_measured"] is False
    assert manifest["processing"]["loop_validation"]["status"] == "passed"


def test_narration_upload_creates_playable_mix_and_download_link(tmp_path) -> None:
    storage = AudioStorage(tmp_path)
    job_id = create_completed_mock_job(storage)

    response = client.post(
        f"/api/v1/jobs/{job_id}/narration",
        content=make_test_wav(duration_s=3),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 200, response.text
    mix = response.json()
    assert mix["job_id"] == job_id
    assert mix["duration_s"] == 5
    assert mix["sample_rate"] == 48000
    assert mix["channel_count"] == 2
    assert mix["duck_attenuation_db"] == 12
    assert mix["integrated_lufs"] is not None
    assert mix["true_peak_dbtp"] <= -1
    assert any("padded with silence" in warning for warning in mix["warnings"])

    download = client.get(mix["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "audio/wav"
    assert len(download.content) == mix["size_bytes"]
    metadata = client.get(f"/api/v1/jobs/{job_id}/narration")
    assert metadata.status_code == 200
    assert metadata.json()["download_url"] == mix["download_url"]
    assert storage.variant_path_for(job_id, "narration-mix", "wav").is_file()


def test_narration_upload_rejects_missing_or_invalid_audio(tmp_path) -> None:
    job_id = create_completed_mock_job(AudioStorage(tmp_path))
    path = f"/api/v1/jobs/{job_id}/narration"

    missing = client.post(path, content=b"", headers={"Content-Type": "audio/wav"})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "NARRATION_MISSING"

    invalid = client.post(path, content=b"not audio", headers={"Content-Type": "audio/wav"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "AUDIO_DECODE_FAILED"


def test_narration_upload_rejects_unsupported_type_and_oversize(tmp_path, monkeypatch) -> None:
    job_id = create_completed_mock_job(AudioStorage(tmp_path))
    path = f"/api/v1/jobs/{job_id}/narration"

    unsupported = client.post(path, content=b"data", headers={"Content-Type": "text/plain"})
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "NARRATION_FORMAT_UNSUPPORTED"

    monkeypatch.setattr(routes.settings, "max_narration_bytes", 64)
    oversized = client.post(
        path,
        content=make_test_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "NARRATION_TOO_LARGE"


def test_narration_duration_mismatch_is_aligned_and_reported(tmp_path) -> None:
    job_id = create_completed_mock_job(AudioStorage(tmp_path))

    response = client.post(
        f"/api/v1/jobs/{job_id}/narration",
        content=make_test_wav(duration_s=7),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 200
    assert response.json()["duration_s"] == 5
    assert any("trimmed to match" in warning for warning in response.json()["warnings"])


def test_remote_narration_url_is_bounded_and_mixed_with_valid_call_signature(tmp_path, monkeypatch) -> None:
    import app.services.job_manager as job_manager_module
    import app.services.outbound_url as outbound_url

    monkeypatch.setattr(routes.settings, "outbound_url_allowed_hosts", ["audio.example.com"])
    monkeypatch.setattr(
        outbound_url.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        ],
    )
    original_client = httpx.Client
    request_urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        request_urls.append(str(request.url))
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "audio/wav"},
            content=make_test_wav(),
        )

    def client_with_mock_transport(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(job_manager_module.httpx, "Client", client_with_mock_transport)
    storage = AudioStorage(tmp_path)
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Remote narration fixture",
            "duration_s": 5,
            "narration_url": "https://audio.example.com/voice.wav",
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = job_store.create(request, None)

    JobManager(job_store, MockMusicProvider(), storage).process_job(job.job_id)

    completed = job_store.get(job.job_id)
    assert completed is not None and completed.result is not None
    assert completed.result.mix_ducked_download_url == f"/api/v1/jobs/{job.job_id}/narration/audio"
    assert request_urls == ["https://audio.example.com/voice.wav"]
    assert storage.variant_path_for(job.job_id, "narration-mix", "wav").is_file()


def test_narration_processing_failure_does_not_leave_partial_output(tmp_path, monkeypatch) -> None:
    storage = AudioStorage(tmp_path)
    job_id = create_completed_mock_job(storage)

    def fail_processing(
        music_content: bytes,
        music_format: str,
        narration_content: bytes,
        narration_format: str,
        *,
        target_lufs: float,
        output_format: str,
    ) -> None:
        del music_content, music_format, narration_content, narration_format, target_lufs, output_format
        raise AudioProcessingError("NARRATION_PROCESSING_FAILED", "The test mix failed.")

    monkeypatch.setattr(routes, "mix_with_narration", fail_processing)
    response = client.post(
        f"/api/v1/jobs/{job_id}/narration",
        content=make_test_wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NARRATION_PROCESSING_FAILED"
    assert not storage.variant_path_for(job_id, "narration-mix", "wav").exists()
    assert not storage.narration_manifest_path_for(job_id).exists()


def test_manifest_is_not_exposed_for_failed_job() -> None:
    request = JobCreateRequest.model_validate(
        {"prompt": "Unconfigured job", "duration_s": 20}
    )
    job, _ = job_store.create(request, None)
    response = client.get(f"/api/v1/jobs/{job.job_id}/manifest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MANIFEST_NOT_AVAILABLE"


def test_audio_download_is_not_exposed_for_failed_jobs() -> None:
    request = JobCreateRequest.model_validate(
        {"prompt": "No audio", "duration_s": 20}
    )
    job, _ = job_store.create(request, None)
    response = client.get(f"/api/v1/jobs/{job.job_id}/audio")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AUDIO_NOT_AVAILABLE"


def test_terminal_job_state_cannot_return_to_running(tmp_path) -> None:
    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate(
        {"prompt": "A quiet piano", "duration_s": 20}
    )
    job, _ = store.create(request, None)
    JobManager(store, MockMusicProvider(), AudioStorage(tmp_path)).process_job(job.job_id)
    try:
        store.update(job.job_id, status=JobStatus.running)
    except ValueError as exc:
        assert "Invalid job status transition" in str(exc)
    else:
        raise AssertionError("Expected a terminal job transition to be rejected.")


def test_elevenlabs_request_uses_instrumental_and_requested_length() -> None:
    sent: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["headers"] = dict(request.headers)
        sent["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "audio/mpeg"},
            content=b"provider-audio-response",
        )

    provider = ElevenLabsMusicProvider(
        "secret-for-test",
        model_id="music_v2_5",
        transport=httpx.MockTransport(respond),
    )
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Warm acoustic score",
            "duration_s": 45,
            "tempo_bpm": 100,
            "key": "C",
            "sections": [{"start_s": 0, "mood": "calm intro"}],
            "output": {"sample_rate": 48000, "formats": ["mp3"]},
        }
    )
    generated = provider.generate(request, "test-job-id")
    assert generated.content == b"provider-audio-response"
    assert generated.provider == "elevenlabs"
    assert generated.model == "music_v2_5"
    assert generated.format == "mp3"
    assert generated.is_ai_generated is True
    assert sent["headers"]["xi-api-key"] == "secret-for-test"
    assert sent["url"].endswith("output_format=mp3_48000_192")
    assert sent["body"]["music_length_ms"] == 45000
    assert sent["body"]["force_instrumental"] is True
    assert sent["body"]["model_id"] == "music_v2_5"
    assert "Arrangement timeline" in sent["body"]["prompt"]
    assert "seed" not in sent["body"]


def test_elevenlabs_rejects_unauthorized_response_without_leaking_body() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="sensitive vendor diagnostic")

    provider = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(respond),
    )
    request = JobCreateRequest.model_validate(
        {"prompt": "A test prompt", "duration_s": 15, "output": {"formats": ["mp3"]}}
    )
    try:
        provider.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_AUTHENTICATION_FAILED"
        assert "sensitive vendor diagnostic" not in exc.message
        assert "secret-for-test" not in str(exc.details)
    else:
        raise AssertionError("Expected invalid credentials to fail.")


def test_elevenlabs_maps_timeout_and_malformed_response() -> None:
    request = JobCreateRequest.model_validate(
        {"prompt": "A test prompt", "duration_s": 15, "output": {"formats": ["mp3"]}}
    )

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    provider = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(timeout),
    )
    try:
        provider.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_TIMEOUT"
    else:
        raise AssertionError("Expected a provider timeout to fail.")

    malformed = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": "application/json"}, json={})
        ),
    )
    try:
        malformed.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_INVALID_RESPONSE"
    else:
        raise AssertionError("Expected a malformed provider response to fail.")

    invalid_length = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={
                    "content-type": "audio/mpeg",
                    "content-length": "invalid",
                },
                content=b"provider bytes",
            )
        ),
    )
    try:
        invalid_length.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_INVALID_RESPONSE"
    else:
        raise AssertionError("Expected an invalid content length to fail.")


def test_elevenlabs_rejects_incomplete_audio_response() -> None:
    provider = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "audio/mpeg", "content-length": "100"},
                content=b"incomplete audio",
            )
        ),
    )
    request = JobCreateRequest.model_validate(
        {"prompt": "A test prompt", "duration_s": 15, "output": {"formats": ["mp3"]}}
    )
    try:
        provider.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_INCOMPLETE_RESPONSE"
        assert exc.details["expected_bytes"] == 100
        assert exc.details["received_bytes"] == len(b"incomplete audio")
    else:
        raise AssertionError("Expected an incomplete provider response to fail.")


def test_corrupt_audio_fails_job_and_does_not_create_manifest(tmp_path) -> None:
    class CorruptAudioProvider(MockMusicProvider):
        def generate(self, request: JobCreateRequest, job_id: str) -> GeneratedAudio:
            generated = super().generate(request, job_id)
            return replace(generated, content=b"not an audio file")

    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate({"prompt": "Test", "duration_s": 20})
    job, _ = store.create(request, None)
    storage = AudioStorage(tmp_path)
    JobManager(store, CorruptAudioProvider(), storage).process_job(job.job_id)

    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "AUDIO_DECODE_FAILED"
    assert failed.result is None
    assert not storage.path_for(job.job_id, "wav").exists()
    assert not storage.source_path_for(job.job_id, "wav").exists()
    assert not storage.manifest_path_for(job.job_id).exists()


def test_saved_audio_that_cannot_be_reopened_fails_and_cleans_outputs(tmp_path) -> None:
    class CorruptOnSaveStorage(AudioStorage):
        def save(self, job_id: str, audio_format: str, content: bytes) -> Path:
            path = super().save(job_id, audio_format, content)
            path.write_bytes(b"not readable audio")
            return path

    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate({"prompt": "Test persisted audio", "duration_s": 20})
    job, _ = store.create(request, None)
    storage = CorruptOnSaveStorage(tmp_path)
    JobManager(store, MockMusicProvider(), storage).process_job(job.job_id)

    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "AUDIO_OUTPUT_INVALID"
    assert failed.result is None
    assert not storage.path_for(job.job_id, "wav").exists()
    assert not storage.source_path_for(job.job_id, "wav").exists()
    assert not storage.manifest_path_for(job.job_id).exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_manifest_write_failure_removes_saved_audio_and_partial_manifest(tmp_path) -> None:
    class BrokenManifestStorage(AudioStorage):
        def save_manifest(self, job_id: str, manifest: dict[str, object]) -> Path:
            partial = self.manifest_path_for(job_id)
            partial.write_text('{"partial":', encoding="utf-8")
            raise OSError("disk full")

    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate({"prompt": "Test manifest cleanup", "duration_s": 20})
    job, _ = store.create(request, None)
    storage = BrokenManifestStorage(tmp_path)
    JobManager(store, MockMusicProvider(), storage).process_job(job.job_id)

    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "AUDIO_STORAGE_FAILED"
    assert failed.result is None
    assert not storage.path_for(job.job_id, "wav").exists()
    assert not storage.source_path_for(job.job_id, "wav").exists()
    assert not storage.manifest_path_for(job.job_id).exists()


def test_audio_storage_failure_keeps_job_failed_without_result(tmp_path) -> None:
    class BrokenAudioStorage(AudioStorage):
        def save(self, job_id: str, audio_format: str, content: bytes) -> Path:
            del job_id, audio_format, content
            raise OSError("disk full")

    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate({"prompt": "Test", "duration_s": 20})
    job, _ = store.create(request, None)
    JobManager(store, MockMusicProvider(), BrokenAudioStorage(tmp_path)).process_job(job.job_id)

    failed = store.get(job.job_id)
    assert failed is not None
    assert failed.status == JobStatus.failed
    assert failed.error is not None
    assert failed.error.code == "AUDIO_STORAGE_FAILED"
    assert failed.result is None


def test_elevenlabs_maps_provider_server_error_without_retry() -> None:
    call_count = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503, text="upstream diagnostic")

    provider = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(respond),
    )
    request = JobCreateRequest.model_validate(
        {"prompt": "A test prompt", "duration_s": 15, "output": {"formats": ["mp3"]}}
    )
    try:
        provider.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_UNAVAILABLE"
        assert exc.retryable is False
        assert "upstream diagnostic" not in exc.message
    else:
        raise AssertionError("Expected a provider server error to fail.")
    assert call_count == 1


def test_elevenlabs_marks_rate_limit_retryable_without_repeating_request() -> None:
    call_count = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429)

    provider = ElevenLabsMusicProvider(
        "secret-for-test",
        transport=httpx.MockTransport(respond),
    )
    request = JobCreateRequest.model_validate(
        {"prompt": "A test prompt", "duration_s": 15, "output": {"formats": ["mp3"]}}
    )
    try:
        provider.generate(request, "test-job-id")
    except ProviderError as exc:
        assert exc.code == "PROVIDER_RATE_LIMITED"
        assert exc.retryable is True
    else:
        raise AssertionError("Expected rate limiting to fail.")
    assert call_count == 1


def test_idempotency_key_returns_same_job_for_equivalent_request() -> None:
    key = str(uuid4())
    payload = create_payload()
    first = client.post(
        "/api/v1/music/jobs", json=payload, headers={"Idempotency-Key": key}
    )
    second = client.post(
        "/api/v1/music/jobs", json=payload, headers={"Idempotency-Key": key}
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_idempotency_treats_omitted_defaults_as_equivalent() -> None:
    key = str(uuid4())
    minimal = {"prompt": "Same request", "duration_s": 20}
    explicit_defaults = {
        **minimal,
        "sections": [],
        "loopable": False,
        "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav", "mp3"]},
    }
    first = client.post(
        "/api/v1/music/jobs", json=minimal, headers={"Idempotency-Key": key}
    )
    second = client.post(
        "/api/v1/music/jobs",
        json=explicit_defaults,
        headers={"Idempotency-Key": key},
    )
    assert first.json()["job_id"] == second.json()["job_id"]


def test_idempotency_key_conflicts_for_different_request() -> None:
    key = str(uuid4())
    first = client.post(
        "/api/v1/music/jobs",
        json=create_payload(),
        headers={"Idempotency-Key": key},
    )
    second = client.post(
        "/api/v1/music/jobs",
        json=create_payload(duration_s=60),
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_concurrent_requests_with_same_idempotency_key_create_one_job() -> None:
    key = str(uuid4())
    payload = create_payload()

    def submit():
        with TestClient(app) as local_client:
            return local_client.post(
                "/api/v1/music/jobs",
                json=payload,
                headers={"Idempotency-Key": key},
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(lambda _: submit(), range(6)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["job_id"] for response in responses}) == 1


def test_concurrent_distinct_mock_jobs_complete_independently(tmp_path, monkeypatch) -> None:
    storage = AudioStorage(tmp_path)
    monkeypatch.setattr(routes, "audio_storage", storage)
    monkeypatch.setattr(routes.job_manager, "audio_storage", storage)
    monkeypatch.setattr(routes.job_manager, "provider", MockMusicProvider())
    payload = {
        "prompt": "Concurrent offline mock test",
        "duration_s": 5,
        "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
    }

    def submit_and_download(_: int) -> tuple[str, str, int]:
        with TestClient(app) as local_client:
            accepted = local_client.post("/api/v1/music/jobs", json=payload)
            assert accepted.status_code == 202
            job_id = accepted.json()["job_id"]
            status_response = local_client.get(f"/api/v1/jobs/{job_id}")
            audio_response = local_client.get(f"/api/v1/jobs/{job_id}/audio")
            return job_id, status_response.json()["status"], len(audio_response.content)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit_and_download, range(2)))

    assert len({job_id for job_id, _, _ in results}) == 2
    assert all(job_status == "succeeded" for _, job_status, _ in results)
    assert all(audio_size > 10_000 for _, _, audio_size in results)


def test_malformed_json_uses_structured_validation_error() -> None:
    response = client.post(
        "/api/v1/music/jobs",
        content='{"prompt":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_correlation_id_is_propagated_and_generated_when_missing() -> None:
    supplied = client.get("/health", headers={"X-Correlation-Id": "test-trace-42"})
    generated = client.get("/health")
    assert supplied.headers["x-correlation-id"] == "test-trace-42"
    assert generated.headers["x-correlation-id"]
    assert generated.headers["x-correlation-id"] != "test-trace-42"


def test_invalid_correlation_id_is_replaced() -> None:
    response = client.get("/health", headers={"X-Correlation-Id": "bad value"})
    assert " " not in response.headers["x-correlation-id"]


def test_request_logs_are_structured_and_traceable(caplog) -> None:
    with caplog.at_level("INFO"):
        response = client.get("/health", headers={"X-Correlation-Id": "trace-log-test"})
    record = next(
        record for record in caplog.records if record.getMessage() == "HTTP request completed"
    )
    log_data = json.loads(JsonLogFormatter().format(record))
    assert log_data["correlation_id"] == response.headers["x-correlation-id"]
    assert log_data["http_method"] == "GET"
    assert log_data["route"] == "/health"
    assert log_data["response_status"] == 200
    assert isinstance(log_data["duration_ms"], (int, float))


def test_request_body_size_limit_returns_structured_error() -> None:
    response = client.post(
        "/api/v1/music/jobs",
        content=b" " * 70000,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_api_docs_are_available() -> None:
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/music/jobs" in schema["paths"]
    assert "/api/v1/jobs/{job_id}" in schema["paths"]


def test_callback_url_dispatches_signed_webhook(monkeypatch, tmp_path) -> None:
    import hashlib
    import hmac

    from app.core.config import settings
    import app.services.outbound_url as outbound_url

    dispatched: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(settings, "webhook_secret", "test-webhook-secret")
    monkeypatch.setattr(settings, "outbound_url_allowed_hosts", ["webhook.example.com"])
    monkeypatch.setattr(
        outbound_url.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        ],
    )

    class MockHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url: str, content: bytes, headers: dict[str, str]):
            dispatched.append((url, content, headers))

    monkeypatch.setattr("httpx.Client", MockHttpClient)

    store = InMemoryJobStore()
    storage = AudioStorage(tmp_path)
    job_mgr = JobManager(store, MockMusicProvider(), storage)

    request = JobCreateRequest.model_validate(
        {
            "prompt": "Test webhook job",
            "duration_s": 10,
            "callback_url": "https://webhook.example.com/incubrix-callback",
        }
    )
    job, _ = store.create(request, None)
    job_mgr.process_job(job.job_id)

    assert len(dispatched) == 1
    url, content, headers = dispatched[0]
    assert url == "https://webhook.example.com/incubrix-callback"
    assert "X-IncuBrix-Signature" in headers
    expected_sig = hmac.new(
        settings.webhook_secret.encode("utf-8"),
        content,
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-IncuBrix-Signature"] == expected_sig
    payload = json.loads(content.decode("utf-8"))
    assert payload["job_id"] == job.job_id
    assert payload["status"] == "succeeded"
    assert payload["result"]["download_url"] == f"/api/v1/jobs/{job.job_id}/audio"
    assert payload["result"]["manifest_download_url"] == f"/api/v1/jobs/{job.job_id}/manifest"


def test_callback_is_not_sent_without_a_configured_secret(monkeypatch, tmp_path) -> None:
    from app.core.config import settings

    dispatched: list[str] = []

    class MockHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url: str, content: bytes, headers: dict[str, str]):
            dispatched.append(url)

    monkeypatch.setattr("httpx.Client", MockHttpClient)
    monkeypatch.setattr(settings, "webhook_secret", None)

    store = InMemoryJobStore()
    job_mgr = JobManager(store, MockMusicProvider(), AudioStorage(tmp_path))
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Webhook secret missing test",
            "duration_s": 5,
            "callback_url": "https://webhook.example.com/callback",
        }
    )
    job, _ = store.create(request, None)

    job_mgr.process_job(job.job_id)

    assert store.get(job.job_id).status == JobStatus.succeeded
    assert dispatched == []
