from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.mock_provider import MockMusicProvider
from app.providers.stability_provider import StabilityMusicProvider
from app.providers.music_provider import ProviderError
from app.schemas.music import JobCreateRequest
from app.services.audio_storage import AudioStorage
from app.services.audio_storage import inspect_audio_file
from app.services.job_manager import JobManager, create_music_provider
from app.services.job_store import InMemoryJobStore
from app.providers.mock_provider import MockMusicProvider


def create_request(**overrides: object) -> JobCreateRequest:
    payload: dict[str, object] = {
        "prompt": "Warm optimistic acoustic instrumental",
        "duration_s": 45,
        "seed": 123,
        "tempo_bpm": 100,
        "key": "C",
        "sections": [{"start_s": 0, "mood": "calm opening"}],
        "output": {"sample_rate": 48000, "formats": ["mp3"]},
    }
    payload.update(overrides)
    return JobCreateRequest.model_validate(payload)


def test_stability_provider_configuration_is_server_side_and_selectable() -> None:
    configured = create_music_provider(
        Settings(music_provider="stability", stability_api_key="configured-for-test")
    )
    unconfigured = create_music_provider(Settings(music_provider="stability", _env_file=None))
    assert isinstance(configured, StabilityMusicProvider)
    assert configured.is_configured() is True
    assert unconfigured.is_configured() is False

    with pytest.raises(ValidationError):
        Settings(music_provider="unknown")


def test_stability_provider_reports_missing_key_without_request() -> None:
    provider = StabilityMusicProvider(None)
    with pytest.raises(ProviderError) as raised:
        provider.generate(create_request(), "test-job-id")
    assert raised.value.code == "PROVIDER_NOT_CONFIGURED"
    assert "STABILITY_API_KEY" in raised.value.message


def test_stability_request_and_async_poll_are_documented() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            assert request.url.path == "/v2beta/audio/stable-audio/text-to-audio"
            return httpx.Response(202, json={"id": "generation-123"})
        if len([call for call in calls if call.method == "GET"]) == 1:
            return httpx.Response(202, json={"id": "generation-123"})
        return httpx.Response(
            200,
            headers={"content-type": "audio/mpeg"},
            content=b"provider-mp3-bytes",
        )

    provider = StabilityMusicProvider(
        "test-api-key",
        poll_interval_seconds=0,
        transport=httpx.MockTransport(respond),
    )
    generated = provider.generate(create_request(), "test-job-id")

    assert generated.content == b"provider-mp3-bytes"
    assert generated.provider == "stability"
    assert generated.model == "stable-audio-3"
    assert generated.format == "mp3"
    assert generated.is_ai_generated is True
    assert len(calls) == 3
    assert calls[0].headers["authorization"] == "Bearer test-api-key"
    assert calls[0].headers["accept"] == "audio/*"
    assert calls[0].headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b"name=\"model\"\r\n\r\nstable-audio-3" in calls[0].content
    assert b"name=\"duration\"\r\n\r\n45" in calls[0].content
    assert b"name=\"output_format\"\r\n\r\nmp3" in calls[0].content
    assert b"name=\"seed\"\r\n\r\n123" in calls[0].content
    assert b"Instrumental music only" in calls[0].content
    assert calls[1].url.path == "/v2beta/audio/results/generation-123"
    assert calls[1].headers["authorization"] == "Bearer test-api-key"
    assert calls[1].headers["accept"] == "audio/*"
    assert all("test-api-key" not in str(call.url) for call in calls)


def test_stability_reads_streamed_async_start_response_before_json_parse() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(b'{"id":"streamed-generation"}'),
            )
        return httpx.Response(
            200,
            headers={"content-type": "audio/mpeg"},
            content=b"streamed-provider-audio",
        )

    provider = StabilityMusicProvider(
        "test-api-key",
        poll_interval_seconds=0,
        transport=httpx.MockTransport(respond),
    )

    generated = provider.generate(create_request(), "test-job-id")

    assert generated.content == b"streamed-provider-audio"
    assert calls[1].url.path == "/v2beta/audio/results/streamed-generation"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "PROVIDER_REJECTED_REQUEST"),
        (401, "PROVIDER_AUTHENTICATION_FAILED"),
        (402, "PROVIDER_INSUFFICIENT_CREDITS"),
        (403, "PROVIDER_CONTENT_REJECTED"),
        (413, "PROVIDER_REQUEST_TOO_LARGE"),
        (422, "PROVIDER_REJECTED_REQUEST"),
        (429, "PROVIDER_RATE_LIMITED"),
        (503, "PROVIDER_UNAVAILABLE"),
    ],
)
def test_stability_provider_maps_errors_without_leaking_response(
    status_code: int, expected_code: str
) -> None:
    provider = StabilityMusicProvider(
        "test-api-key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status_code, json={"errors": ["private diagnostic"]})
        ),
    )
    with pytest.raises(ProviderError) as raised:
        provider.generate(create_request(), "test-job-id")

    assert raised.value.code == expected_code
    assert "private diagnostic" not in raised.value.message
    assert "test-api-key" not in str(raised.value.details)
    assert raised.value.retryable is (status_code in {429, 503})


def test_stability_provider_rejects_malformed_generation_response() -> None:
    for response in (
        httpx.Response(202, json={"id": "../bad"}),
        httpx.Response(202, json={"id": 123}),
        httpx.Response(202, content=b"not-json", headers={"content-type": "application/json"}),
        httpx.Response(202, content=b'{"id":"generation-123"}', headers={"content-type": "audio/mpeg"}),
    ):
        provider = StabilityMusicProvider(
            "test-api-key",
            transport=httpx.MockTransport(lambda _, response=response: response),
        )
        with pytest.raises(ProviderError) as raised:
            provider.generate(create_request(), "test-job-id")
        assert raised.value.code == "PROVIDER_INVALID_RESPONSE"


def test_stability_provider_reports_timeout_without_retrying_generation() -> None:
    calls = 0

    def timeout(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private diagnostic")

    provider = StabilityMusicProvider(
        "test-api-key",
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(ProviderError) as raised:
        provider.generate(create_request(), "test-job-id")
    assert raised.value.code == "PROVIDER_TIMEOUT"
    assert calls == 1


def test_stability_provider_reports_timeout_while_polling() -> None:
    calls: list[str] = []

    def timeout_during_poll(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(202, json={"id": "generation-123"})
        raise httpx.ReadTimeout("private provider diagnostic")

    provider = StabilityMusicProvider(
        "test-api-key",
        transport=httpx.MockTransport(timeout_during_poll),
    )
    with pytest.raises(ProviderError) as raised:
        provider.generate(create_request(), "test-job-id")
    assert raised.value.code == "PROVIDER_TIMEOUT"
    assert calls == ["POST", "GET"]


def test_stability_provider_rejects_empty_audio_result() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "generation-123"})
        return httpx.Response(
            200,
            headers={"content-type": "audio/mpeg", "content-length": "0"},
            content=b"",
        )

    provider = StabilityMusicProvider("test-api-key", transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as raised:
        provider.generate(create_request(), "test-job-id")
    assert raised.value.code == "PROVIDER_EMPTY_RESPONSE"


def test_stability_provider_maps_poll_http_errors() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "generation-123"})
        return httpx.Response(401, json={"errors": ["private diagnostic"]})

    provider = StabilityMusicProvider("test-api-key", transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as raised:
        provider.generate(create_request(), "test-job-id")
    assert raised.value.code == "PROVIDER_AUTHENTICATION_FAILED"
    assert "private diagnostic" not in raised.value.message


def test_stability_provider_rejects_invalid_audio_and_truncated_download() -> None:
    for content_type, content, headers in (
        ("application/json", b'{"error":"not audio"}', {}),
        ("audio/mpeg", b"short", {"content-length": "100"}),
    ):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"id": "generation-123"})
            return httpx.Response(
                200,
                headers={"content-type": content_type, **headers},
                content=content,
            )

        provider = StabilityMusicProvider(
            "test-api-key",
            transport=httpx.MockTransport(respond),
        )
        with pytest.raises(ProviderError) as raised:
            provider.generate(create_request(), "test-job-id")
        expected = (
            "PROVIDER_INVALID_RESPONSE"
            if content_type == "application/json"
            else "PROVIDER_INCOMPLETE_RESPONSE"
        )
        assert raised.value.code == expected


def test_stability_wav_selection_and_metadata_survive_job_lifecycle(tmp_path) -> None:
    request = create_request(
        output={"sample_rate": 48000, "formats": ["wav"]},
    )
    wav_content = MockMusicProvider().generate(request, "test-job-id").content

    def respond(http_request: httpx.Request) -> httpx.Response:
        if http_request.method == "POST":
            assert b"name=\"output_format\"\r\n\r\nwav" in http_request.content
            return httpx.Response(202, json={"id": "generation-123"})
        return httpx.Response(
            200,
            headers={"content-type": "audio/wav"},
            content=wav_content,
        )

    store = InMemoryJobStore()
    job, _ = store.create(request, None)
    storage = AudioStorage(tmp_path)
    provider = StabilityMusicProvider(
        "test-api-key",
        poll_interval_seconds=0,
        transport=httpx.MockTransport(respond),
    )
    JobManager(store, provider, storage).process_job(job.job_id)

    finished = store.get(job.job_id)
    assert finished is not None
    assert finished.status.value == "succeeded"
    assert finished.result is not None
    assert finished.result.provider == "stability"
    assert finished.result.model == "stable-audio-3"
    assert finished.result.format == "wav"
    assert finished.result.sample_rate == 48000
    assert finished.result.duration_s == 45
    assert finished.result.is_ai_generated is True
    audio_path = storage.path_for(job.job_id, "wav")
    assert audio_path.read_bytes() != wav_content
    assert inspect_audio_file(audio_path, "wav").duration_s == 45
    manifest = json.loads(storage.manifest_path_for(job.job_id).read_text(encoding="utf-8"))
    assert manifest["licensing"]["terms_url"] == "https://stability.ai/terms-of-service"
    assert any("44.1 kHz" in warning for warning in finished.warnings or [])


@pytest.mark.parametrize("asset", ["audio", "manifest"])
def test_audio_storage_cleans_temporary_file_when_flush_fails(
    tmp_path, monkeypatch, asset: str
) -> None:
    storage = AudioStorage(tmp_path)

    def fail_fsync(_: int) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("app.services.audio_storage.os.fsync", fail_fsync)
    job_id = str(uuid4())
    with pytest.raises(OSError, match="simulated disk failure"):
        if asset == "audio":
            storage.save(job_id, "wav", b"audio")
        else:
            storage.save_manifest(job_id, {"job_id": job_id})

    assert list(tmp_path.iterdir()) == []
