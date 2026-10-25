from __future__ import annotations

import io
import math
import struct
import wave

from app.providers.music_provider import GeneratedAudio, MusicProvider
from app.schemas.music import JobCreateRequest


class MockMusicProvider(MusicProvider):
    """Test-only provider that creates an explicitly labelled tone fixture."""

    name = "test-mock"

    def is_configured(self) -> bool:
        return True

    def generate(self, _request: JobCreateRequest, job_id: str) -> GeneratedAudio:
        del job_id
        sample_rate = 48000
        frame_count = round(_request.duration_s * sample_rate)
        one_second = b"".join(
            struct.pack(
                "<hh",
                int(5000 * math.sin(2 * math.pi * 220 * frame / sample_rate)),
                int(5000 * math.sin(2 * math.pi * 220 * frame / sample_rate)),
            )
            for frame in range(sample_rate)
        )
        full_seconds, remaining_frames = divmod(frame_count, sample_rate)
        frames = one_second * full_seconds + one_second[: remaining_frames * 4]
        with io.BytesIO() as output:
            with wave.open(output, "wb") as wav_file:
                wav_file.setnchannels(2)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(frames)
            content = output.getvalue()
        return GeneratedAudio(
            content=content,
            provider=self.name,
            model="test-fixture",
            format="wav",
            is_ai_generated=False,
            warnings=["Test-only mock fixture; this file is not AI-generated music."],
        )
