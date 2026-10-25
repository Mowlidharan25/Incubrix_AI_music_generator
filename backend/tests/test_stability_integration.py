from __future__ import annotations

import os

import pytest

from app.core.config import settings
from app.providers.stability_provider import StabilityMusicProvider
from app.schemas.music import JobCreateRequest
from app.services.audio_storage import AudioStorage
from app.services.job_manager import JobManager
from app.services.job_store import InMemoryJobStore

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_STABILITY_INTEGRATION_TEST") != "1" or not settings.stability_api_key,
    reason=(
        "Set RUN_STABILITY_INTEGRATION_TEST=1 and STABILITY_API_KEY to explicitly "
        "run the paid Stability AI integration test."
    ),
)


def test_live_stability_generation_writes_readable_audio(tmp_path) -> None:
    provider = StabilityMusicProvider(
        settings.stability_api_key,
        timeout_seconds=settings.provider_timeout_seconds,
        max_audio_bytes=settings.max_generated_audio_bytes,
    )
    request = JobCreateRequest.model_validate(
        {
            "prompt": "Short calm acoustic instrumental, no singing or speech",
            "duration_s": 5,
            "output": {"sample_rate": 48000, "formats": ["wav"]},
        }
    )
    store = InMemoryJobStore()
    job, _ = store.create(request, None)
    storage = AudioStorage(tmp_path)
    JobManager(store, provider, storage).process_job(job.job_id)
    finished = store.get(job.job_id)

    assert finished is not None
    assert finished.status.value == "succeeded", finished.error
    assert finished.result is not None
    assert finished.result.provider == "stability"
    assert finished.result.model == "stable-audio-3"
    assert finished.result.is_ai_generated is True
    output_path = storage.path_for(job.job_id, finished.result.format)
    assert output_path.is_file() and output_path.stat().st_size > 0
    assert finished.result.format == "wav"
    assert finished.result.sample_rate == 48000
    assert finished.result.duration_s > 0
    print(
        f"Live Stable Audio output: {output_path.stat().st_size} bytes, "
        f"{finished.result.duration_s:.3f}s, {finished.result.sample_rate} Hz, "
        f"{finished.result.channel_count} channels, "
        f"{finished.result.processing_duration_s:.2f}s processing; "
        f"stored at {output_path}."
    )
