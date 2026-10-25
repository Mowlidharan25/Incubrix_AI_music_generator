from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf

from app.services import audio_processing
from app.services.audio_processing import (
    AudioProcessingError,
    _narration_duck_curve,
    process_audio,
)
from app.services.audio_storage import inspect_audio


def make_wav(
    duration_s: float = 5.5,
    sample_rate: int = 44100,
    amplitude: float = 0.2,
    channels: int = 2,
) -> bytes:
    frame_count = round(duration_s * sample_rate)
    timeline = np.arange(frame_count, dtype=np.float64) / sample_rate
    tone = amplitude * np.sin(2 * np.pi * 440 * timeline)
    samples = np.repeat(tone[:, np.newaxis], channels, axis=1)
    output = io.BytesIO()
    sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def test_processing_trims_resamples_and_measures_requested_targets() -> None:
    processed = process_audio(make_wav(), "wav", 5.25, "wav", -16, 48000)
    metadata = inspect_audio(processed.content, "wav")

    assert abs(processed.duration_s - 5.25) <= 1 / 48000
    assert metadata.sample_rate == 48000
    assert metadata.channel_count == 2
    assert abs(processed.integrated_lufs - (-16)) < 0.2
    assert abs(processed.integrated_lufs - (-16)) <= 1
    assert processed.true_peak_dbtp is not None
    assert processed.true_peak_dbtp <= -1
    assert "trimmed" in " ".join(processed.warnings)


def test_lookahead_limiter_reduces_transient_peaks() -> None:
    samples = np.zeros((48000, 1), dtype=np.float64)
    samples[24000] = 2

    limited, reduced_gain = audio_processing._limit_peaks(samples, 48000)

    assert reduced_gain
    assert np.max(np.abs(limited)) <= 10 ** (-2 / 20)
    assert limited[24000, 0] < samples[24000, 0]


def test_processing_pads_only_small_duration_mismatches() -> None:
    processed = process_audio(make_wav(duration_s=4.8), "wav", 5.0, "wav", -16, 48000)

    assert processed.duration_s == 5.0
    assert any("Silence was added" in warning for warning in processed.warnings)


def test_processing_trims_long_audio_to_exact_requested_duration() -> None:
    processed = process_audio(make_wav(duration_s=5.5), "wav", 5.0, "wav")

    assert processed.duration_s == 5.0
    assert "trimmed" in " ".join(processed.warnings)


def test_loopable_processing_crossfades_and_measures_encoded_endpoints() -> None:
    processed = process_audio(make_wav(duration_s=5.5), "wav", 5.0, "wav", loopable=True)
    audio, sample_rate = audio_processing._decode_audio(processed.content, "wav")
    discontinuity = np.sqrt(np.mean(np.square(audio[0] - audio[-1])))

    assert sample_rate == 48000
    assert processed.duration_s == 5.0
    assert processed.loop_crossfade_duration_s == 0.25
    assert processed.loop_validated is True
    assert processed.loop_endpoint_discontinuity_dbfs is not None
    assert processed.loop_endpoint_discontinuity_dbfs <= -60
    assert 20 * np.log10(max(discontinuity, 1e-12)) <= -60
    assert any("reverse-head/tail edge crossfade" in warning for warning in processed.warnings)


def test_non_loopable_processing_reports_loop_validation_not_requested() -> None:
    processed = process_audio(make_wav(), "wav", 5.0, "wav")

    assert processed.loop_crossfade_duration_s is None
    assert processed.loop_endpoint_discontinuity_dbfs is None
    assert processed.loop_validated is None


def test_processing_rejects_provider_audio_far_shorter_than_requested() -> None:
    with pytest.raises(AudioProcessingError) as raised:
        process_audio(make_wav(duration_s=2.0), "wav", 5.0, "wav")

    assert raised.value.code == "AUDIO_DURATION_MISMATCH"

def test_processing_rejects_out_of_range_duration_and_non_48khz_output() -> None:
    with pytest.raises(AudioProcessingError) as duration_error:
        process_audio(make_wav(), "wav", 301, "wav")
    assert duration_error.value.code == "AUDIO_DURATION_INVALID"

    with pytest.raises(AudioProcessingError) as sample_rate_error:
        process_audio(make_wav(), "wav", 5.0, "wav", sample_rate=44100)
    assert sample_rate_error.value.code == "AUDIO_SAMPLE_RATE_UNSUPPORTED"


def test_silence_is_rejected_instead_of_claiming_normalized_audio() -> None:
    output = io.BytesIO()
    sf.write(output, np.zeros((5 * 44100, 2)), 44100, format="WAV", subtype="PCM_16")

    with pytest.raises(AudioProcessingError) as raised:
        process_audio(output.getvalue(), "wav", 5.0, "wav")

    assert raised.value.code == "AUDIO_SILENT"


def test_peak_protection_prevents_clipped_output() -> None:
    source = io.BytesIO()
    sample_count = 5 * 44100
    timeline = np.arange(sample_count, dtype=np.float64) / 44100
    tone = 1.8 * np.sin(2 * np.pi * 440 * timeline)
    sf.write(source, np.repeat(tone[:, np.newaxis], 2, axis=1), 44100, format="WAV", subtype="FLOAT")

    processed = process_audio(source.getvalue(), "wav", 5.0, "wav", -16, 48000)
    audio, _ = sf.read(io.BytesIO(processed.content), dtype="float64", always_2d=True)

    assert np.max(np.abs(audio)) < 1
    assert processed.true_peak_dbtp is not None and processed.true_peak_dbtp <= -1
    assert any("pre-existing clipping may be present" in warning for warning in processed.warnings)


def test_high_crest_audio_uses_limiter_and_measures_final_peak() -> None:
    sample_rate = 44100
    frame_count = sample_rate * 5
    timeline = np.arange(frame_count, dtype=np.float64) / sample_rate
    tone = 0.015 * np.sin(2 * np.pi * 440 * timeline)
    samples = np.repeat(tone[:, np.newaxis], 2, axis=1)
    samples[::sample_rate, :] = 1.2
    source = io.BytesIO()
    sf.write(source, samples, sample_rate, format="WAV", subtype="FLOAT")

    processed = process_audio(source.getvalue(), "wav", 5.0, "wav")

    assert any("look-ahead peak limiter" in warning for warning in processed.warnings)
    assert processed.true_peak_dbtp is not None
    assert processed.true_peak_dbtp <= -1


def test_full_scale_pcm_input_is_flagged_for_possible_clipping() -> None:
    processed = process_audio(make_wav(amplitude=1.0), "wav", 5.0, "wav")

    assert any("pre-existing clipping may be present" in warning for warning in processed.warnings)


@pytest.mark.parametrize("invalid_sample", [np.nan, np.inf, -np.inf])
def test_non_finite_audio_samples_are_rejected(invalid_sample: float) -> None:
    output = io.BytesIO()
    samples = np.zeros((5 * 44100, 2), dtype=np.float64)
    samples[100, 0] = invalid_sample
    sf.write(output, samples, 44100, format="WAV", subtype="FLOAT")

    with pytest.raises(AudioProcessingError) as raised:
        process_audio(output.getvalue(), "wav", 5.0, "wav")

    assert raised.value.code == "AUDIO_SAMPLES_INVALID"


def test_malformed_audio_is_rejected() -> None:
    with pytest.raises(AudioProcessingError) as raised:
        process_audio(b"not audio", "wav", 5.0, "wav")

    assert raised.value.code == "AUDIO_DECODE_FAILED"


def test_mp3_output_reports_missing_encoder_before_processing(monkeypatch) -> None:
    monkeypatch.setattr(audio_processing.shutil, "which", lambda _: None)

    with pytest.raises(AudioProcessingError) as raised:
        process_audio(make_wav(), "wav", 5.0, "mp3")

    assert raised.value.code == "MP3_ENCODER_UNAVAILABLE"


def test_mp3_output_when_encoder_is_available() -> None:
    try:
        audio_processing.ensure_mp3_encoder()
    except AudioProcessingError as exc:
        pytest.skip(exc.message)

    processed = process_audio(make_wav(), "wav", 5.0, "mp3")
    metadata = inspect_audio(processed.content, "mp3")

    assert metadata.sample_rate == 48000
    assert abs(metadata.duration_s - 5.0) <= 0.05
    assert processed.integrated_lufs is not None
    assert processed.true_peak_dbtp is not None
    assert processed.true_peak_dbtp <= -1


def test_narration_duck_curve_has_smooth_attack_and_release() -> None:
    narration = np.zeros((48000, 1), dtype=np.float64)
    narration[4800:24000, 0] = 0.2

    curve = _narration_duck_curve(narration)

    assert 0.24 < curve[19200] < 0.27
    assert curve[4800] > curve[19200]
    assert curve[24000] < 0.5
    assert curve[-1] > curve[24000]
    assert np.all((curve > 0) & (curve <= 1))
