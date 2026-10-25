from __future__ import annotations

import io
import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.providers.mock_provider import MockMusicProvider
from app.schemas.music import JobCreateRequest, JobStatus
from app.services.audio_storage import AudioStorage, inspect_audio_file
from app.services.job_manager import JobManager
from app.services.job_store import InMemoryJobStore, job_store
from app.services.stem_separation import (
    SAM_SAMPLE_RATE,
    StemSeparationError,
    _load_sam_audio_from_cache,
    process_instrument_stems,
)


def make_wav(
    duration_s: float = 5,
    sample_rate: int = SAM_SAMPLE_RATE,
    silent: bool = False,
    channel_count: int = 2,
) -> bytes:
    frames = round(duration_s * sample_rate)
    with io.BytesIO() as output:
        samples = np.zeros((frames, channel_count), dtype=np.float64)
        if not silent:
            timeline = np.arange(frames) / sample_rate
            tone = 0.2 * np.sin(2 * math.pi * 220 * timeline)
            samples[:, 0] = tone
            if channel_count == 2:
                samples[:, 1] = tone * 0.8
        sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_24")
        return output.getvalue()


class FakeSeparator:
    model_id = "fixture-separator"
    model_version = "test-1"
    sample_rate = SAM_SAMPLE_RATE

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def separate(self, mono_audio: np.ndarray, description: str) -> tuple[np.ndarray, np.ndarray]:
        self.prompts.append(description)
        if description == "drums and percussion":
            return mono_audio * 0.2, mono_audio * 0.7
        return mono_audio * 0.3, mono_audio * 0.6


def test_separation_returns_four_aligned_wav_stems_and_measured_reconstruction(tmp_path) -> None:
    separator = FakeSeparator()
    result = process_instrument_stems(
        make_wav(),
        "wav",
        5,
        2,
        backend=separator,
    )

    assert [stem.stem_id for stem in result.stems] == ["percussion", "bass", "other", "remaining"]
    assert separator.prompts == [
        "drums and percussion",
        "bass instruments",
        "guitar, piano, keyboard, and string instruments",
    ]
    assert result.model_id == "fixture-separator"
    assert result.reconstruction_relative_error_db < 0
    for stem in result.stems:
        metadata = inspect_audio_file(write_stem(tmp_path, stem.content), "wav")
        assert metadata.duration_s == pytest.approx(5, abs=0.05)
        assert metadata.sample_rate == SAM_SAMPLE_RATE
        assert metadata.channel_count == 2
        assert stem.gain_linear == 1
        assert stem.gain_db == 0


def test_mono_mix_produces_mono_stems() -> None:
    result = process_instrument_stems(
        make_wav(channel_count=1),
        "wav",
        5,
        1,
        backend=FakeSeparator(),
    )
    for stem in result.stems:
        with io.BytesIO(stem.content) as encoded:
            audio, sample_rate = sf.read(encoded, dtype="float64", always_2d=True)
        assert sample_rate == SAM_SAMPLE_RATE
        assert audio.shape == (5 * SAM_SAMPLE_RATE, 1)


def write_stem(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "test-stem.wav"
    path.write_bytes(content)
    return path


def test_short_input_is_padded_within_tolerance_and_long_mismatch_is_rejected() -> None:
    result = process_instrument_stems(
        make_wav(duration_s=4.99),
        "wav",
        5,
        2,
        backend=FakeSeparator(),
    )
    assert all(stem.duration_s == pytest.approx(5) for stem in result.stems)

    with pytest.raises(StemSeparationError, match="differs from the requested duration"):
        process_instrument_stems(
            make_wav(duration_s=4.9),
            "wav",
            5,
            2,
            backend=FakeSeparator(),
        )


def test_sample_rate_silence_and_malformed_input_fail_explicitly() -> None:
    with pytest.raises(StemSeparationError, match="48,000 Hz"):
        process_instrument_stems(
            make_wav(sample_rate=44_100),
            "wav",
            5,
            2,
            backend=FakeSeparator(),
        )
    with pytest.raises(StemSeparationError, match="Silent audio"):
        process_instrument_stems(
            make_wav(silent=True),
            "wav",
            5,
            2,
            backend=FakeSeparator(),
        )
    with pytest.raises(StemSeparationError, match="supported audio file"):
        process_instrument_stems(b"not a WAV file", "wav", 5, 2, backend=FakeSeparator())


def test_malformed_model_output_and_wrong_channel_layout_are_rejected() -> None:
    class InvalidSeparator(FakeSeparator):
        def separate(self, mono_audio: np.ndarray, description: str) -> tuple[np.ndarray, np.ndarray]:
            return np.zeros(10), mono_audio

    with pytest.raises(StemSeparationError, match="differs from the requested duration"):
        process_instrument_stems(make_wav(), "wav", 5, 2, backend=InvalidSeparator())
    with pytest.raises(StemSeparationError, match="mono or stereo"):
        process_instrument_stems(make_wav(), "wav", 5, 3, backend=FakeSeparator())


def test_missing_local_model_is_reported_without_downloading_weights(monkeypatch) -> None:
    def missing_backend():
        raise StemSeparationError(
            "STEM_DEPENDENCY_MISSING",
            "Local SAM-Audio support is not installed.",
        )

    monkeypatch.setattr("app.services.stem_separation._load_sam_audio_backend", missing_backend)
    with pytest.raises(StemSeparationError) as error:
        process_instrument_stems(make_wav(), "wav", 5, 2)
    assert error.value.code == "STEM_DEPENDENCY_MISSING"


def test_sam_model_and_processor_load_only_from_local_cache() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class LocalModel:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> str:
            calls.append((model_id, kwargs))
            return "model"

    class LocalProcessor:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> str:
            calls.append((model_id, kwargs))
            return "processor"

    model, processor = _load_sam_audio_from_cache(
        LocalModel,
        LocalProcessor,
        "facebook/sam-audio-small",
        "pinned-revision",
    )

    assert model == "model"
    assert processor == "processor"
    assert len(calls) == 2
    assert all(call[1]["local_files_only"] is True for call in calls)
    assert all(call[1]["revision"] == "pinned-revision" for call in calls)


def test_stem_storage_paths_accept_only_known_stem_ids(tmp_path) -> None:
    storage = AudioStorage(tmp_path)
    job_id = str(uuid4())
    path = storage.save_stem(job_id, "percussion", make_wav())
    assert path.is_file()
    assert storage.stem_path_for(job_id, "percussion") == path
    with pytest.raises(ValueError, match="Invalid generated stem reference"):
        storage.stem_path_for(job_id, "..")


def test_job_keeps_successful_mix_when_stem_model_is_unavailable(tmp_path) -> None:
    storage = AudioStorage(tmp_path)
    store = InMemoryJobStore()
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Offline test instrumental",
            "duration_s": 5,
            "separate_stems": True,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = store.create(request, None)

    def missing_model(content: bytes, audio_format: str, duration_s: float, channels: int) -> None:
        assert content
        assert audio_format == "wav"
        assert duration_s == pytest.approx(5)
        assert channels == 2
        raise StemSeparationError("SAM_CHECKPOINT_ACCESS_REQUIRED", "Model access is required.")

    JobManager(store, MockMusicProvider(), storage, stem_processor=missing_model).process_job(job.job_id)
    finished = store.get(job.job_id)

    assert finished is not None
    assert finished.status == JobStatus.succeeded
    assert finished.result is not None
    assert finished.result.stem_processing.status == "unavailable"
    assert finished.result.stem_processing.error is not None
    assert finished.result.stem_processing.error.code == "SAM_CHECKPOINT_ACCESS_REQUIRED"
    assert storage.path_for(job.job_id, "wav").is_file()
    assert storage.manifest_path_for(job.job_id).is_file()
    assert not any(storage.stem_path_for(job.job_id, stem_id).exists() for stem_id in storage.STEM_IDS)


client = TestClient(app)


def test_stem_files_are_persisted_in_manifest_and_download_route(tmp_path, monkeypatch) -> None:
    storage = AudioStorage(tmp_path)
    monkeypatch.setattr(routes, "audio_storage", storage)
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Offline stem endpoint test",
            "duration_s": 5,
            "separate_stems": True,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = job_store.create(request, None)
    manager = JobManager(
        job_store,
        MockMusicProvider(),
        storage,
        stem_processor=lambda content, audio_format, duration_s, channels: process_instrument_stems(
            content,
            audio_format,
            duration_s,
            channels,
            backend=FakeSeparator(),
        ),
    )
    manager.process_job(job.job_id)

    finished = job_store.get(job.job_id)
    assert finished is not None and finished.result is not None
    assert finished.status == JobStatus.succeeded
    assert finished.result.stem_processing.status == "available"
    assert len(finished.result.stem_processing.stems) == 4
    for stem in finished.result.stem_processing.stems:
        response = client.get(stem.download_url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        saved_path = storage.stem_path_for(job.job_id, stem.stem_id)
        assert response.content == saved_path.read_bytes()

    manifest_response = client.get(f"/api/v1/jobs/{job.job_id}/manifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["stem_separation"]["status"] == "available"
    assert len(manifest["stem_separation"]["stems"]) == 4
    assert manifest["stem_separation"]["reconstruction_relative_error_db"] < 0
    assert manifest["stem_separation"]["license"]["license_url"].endswith("/LICENSE")


def test_unavailable_stem_download_returns_404(tmp_path, monkeypatch) -> None:
    storage = AudioStorage(tmp_path)
    monkeypatch.setattr(routes, "audio_storage", storage)
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Unavailable stem route test",
            "duration_s": 5,
            "separate_stems": True,
            "output": {"sample_rate": 48000, "target_lufs": -16, "formats": ["wav"]},
        }
    )
    job, _ = job_store.create(request, None)

    def missing_model(content: bytes, audio_format: str, duration_s: float, channels: int) -> None:
        assert content
        assert audio_format == "wav"
        assert duration_s == pytest.approx(5)
        assert channels == 2
        raise StemSeparationError("STEM_DEPENDENCY_MISSING", "SAM-Audio is not installed.")

    JobManager(job_store, MockMusicProvider(), storage, stem_processor=missing_model).process_job(job.job_id)
    response = client.get(f"/api/v1/jobs/{job.job_id}/stems/percussion")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STEM_NOT_AVAILABLE"
