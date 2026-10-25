from __future__ import annotations

import io
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile as sf
from scipy.signal import resample_poly

TARGET_SAMPLE_RATE = 48_000
TRUE_PEAK_CEILING_DBTP = -1.0
NARRATION_DUCK_DB = 12.0


class AudioProcessingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProcessedAudio:
    content: bytes
    format: str
    duration_s: float
    sample_rate: int
    channel_count: int
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    warnings: list[str]
    loop_crossfade_duration_s: float | None = None
    loop_endpoint_discontinuity_dbfs: float | None = None
    loop_validated: bool | None = None


def ensure_mp3_encoder() -> None:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise AudioProcessingError(
            "MP3_ENCODER_UNAVAILABLE",
            "MP3 output requires FFmpeg with the libmp3lame encoder installed and available on PATH. Choose WAV or install a licensed FFmpeg build with libmp3lame.",
        )
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioProcessingError(
            "MP3_ENCODER_UNAVAILABLE",
            "FFmpeg could not be checked for MP3 encoder support.",
        ) from exc
    if result.returncode != 0 or "libmp3lame" not in result.stdout:
        raise AudioProcessingError(
            "MP3_ENCODER_UNAVAILABLE",
            "The installed FFmpeg does not report the libmp3lame MP3 encoder.",
        )


def ensure_mp3_decoder() -> None:
    if shutil.which("ffmpeg") is None:
        raise AudioProcessingError(
            "MP3_DECODER_UNAVAILABLE",
            "The selected music provider returns MP3 audio; install FFmpeg and make it available on PATH before generating.",
        )


def process_audio(
    content: bytes,
    source_format: str,
    duration_s: float,
    output_format: str,
    target_lufs: float = -16.0,
    sample_rate: int = TARGET_SAMPLE_RATE,
    loopable: bool = False,
) -> ProcessedAudio:
    if not content:
        raise AudioProcessingError("AUDIO_EMPTY", "The source audio is empty.")
    if source_format not in {"wav", "mp3"} or output_format not in {"wav", "mp3"}:
        raise AudioProcessingError("AUDIO_FORMAT_UNSUPPORTED", "Only WAV and MP3 audio are supported.")
    if not math.isfinite(duration_s) or not 5 <= duration_s <= 300:
        raise AudioProcessingError(
            "AUDIO_DURATION_INVALID",
            "Requested audio duration must be between 5 and 300 seconds.",
        )
    if not math.isfinite(target_lufs) or not -30 <= target_lufs <= -4:
        raise AudioProcessingError("AUDIO_LOUDNESS_TARGET_INVALID", "The requested loudness target is invalid.")
    if sample_rate != TARGET_SAMPLE_RATE:
        raise AudioProcessingError(
            "AUDIO_SAMPLE_RATE_UNSUPPORTED",
            "Generated audio output must use a sample rate of exactly 48000 Hz.",
        )
    if output_format == "mp3":
        ensure_mp3_encoder()
    samples, source_rate = _decode_audio(content, source_format)
    return _process_samples(
        samples,
        source_rate,
        duration_s,
        output_format,
        target_lufs,
        sample_rate,
        loopable=loopable,
    )


def decode_audio_samples(content: bytes, audio_format: str) -> tuple[np.ndarray, int]:
    if not content or audio_format not in {"wav", "mp3"}:
        raise AudioProcessingError("AUDIO_FORMAT_UNSUPPORTED", "Only non-empty WAV and MP3 audio can be decoded.")
    return _decode_audio(content, audio_format)


def mix_with_narration(
    music_content: bytes,
    music_format: str,
    narration_content: bytes,
    narration_format: str,
    target_lufs: float,
    output_format: str,
) -> ProcessedAudio:
    if not narration_content:
        raise AudioProcessingError("NARRATION_MISSING", "Upload a narration audio file before creating a mix.")
    if output_format == "mp3":
        ensure_mp3_encoder()
    music, music_rate = _decode_audio(music_content, music_format)
    narration, narration_rate = _decode_audio(narration_content, narration_format)
    music = _resample(music, music_rate, TARGET_SAMPLE_RATE)
    narration = _resample(narration, narration_rate, TARGET_SAMPLE_RATE)

    target_frames = len(music)
    warnings: list[str] = []
    if len(narration) != target_frames:
        original_duration = len(narration) / TARGET_SAMPLE_RATE
        narration = _align_narration(narration, target_frames)
        action = "trimmed" if original_duration > target_frames / TARGET_SAMPLE_RATE else "padded with silence"
        warnings.append(f"Narration ({original_duration:.3f}s) was {action} to match the music duration.")

    if narration.shape[1] == 1 and music.shape[1] > 1:
        narration = np.repeat(narration, music.shape[1], axis=1)
    elif narration.shape[1] > 1 and music.shape[1] == 1:
        narration = np.mean(narration, axis=1, keepdims=True)
    elif narration.shape[1] != music.shape[1]:
        narration = narration[:, : music.shape[1]]

    duck_curve = _narration_duck_curve(narration)
    mixed = music * duck_curve[:, np.newaxis] + narration
    processed = _process_samples(
        mixed,
        TARGET_SAMPLE_RATE,
        target_frames / TARGET_SAMPLE_RATE,
        output_format,
        target_lufs,
        TARGET_SAMPLE_RATE,
    )
    return ProcessedAudio(
        content=processed.content,
        format=processed.format,
        duration_s=processed.duration_s,
        sample_rate=processed.sample_rate,
        channel_count=processed.channel_count,
        integrated_lufs=processed.integrated_lufs,
        true_peak_dbtp=processed.true_peak_dbtp,
        warnings=[
            f"Music ducking targets {NARRATION_DUCK_DB:g} dB attenuation while narration is active, with a 50 ms attack and 250 ms release.",
            *warnings,
            *processed.warnings,
        ],
    )


def _process_samples(
    samples: np.ndarray,
    source_rate: int,
    duration_s: float,
    output_format: str,
    target_lufs: float,
    sample_rate: int,
    loopable: bool = False,
) -> ProcessedAudio:
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise AudioProcessingError("AUDIO_DURATION_INVALID", "The requested audio duration is invalid.")
    if samples.ndim != 2 or samples.shape[1] not in {1, 2} or not len(samples):
        raise AudioProcessingError("AUDIO_CHANNELS_UNSUPPORTED", "Audio must contain one or two channels.")
    if not np.isfinite(samples).all():
        raise AudioProcessingError("AUDIO_SAMPLES_INVALID", "Audio contains invalid numeric samples.")
    source_duration = len(samples) / source_rate
    if source_duration < duration_s - 0.5:
        raise AudioProcessingError(
            "AUDIO_DURATION_MISMATCH",
            "Provider audio is more than 0.5 seconds shorter than requested; it was not extended with a long silent tail.",
        )
    source_reaches_full_scale = float(np.max(np.abs(samples))) >= 1 - 1 / 32768
    samples = _resample(samples, source_rate, sample_rate)
    exact_frames = round(duration_s * sample_rate)
    warnings: list[str] = []
    if source_reaches_full_scale:
        warnings.append("Source audio reaches or exceeds full scale; pre-existing clipping may be present.")
    if len(samples) > exact_frames:
        samples = samples[:exact_frames]
        warnings.append("Audio was trimmed to the exact requested duration.")
    elif len(samples) < exact_frames:
        samples = np.pad(samples, ((0, exact_frames - len(samples)), (0, 0)))
        warnings.append("Silence was added at the end to reach the exact requested duration.")
    loop_crossfade_duration_s: float | None = None
    if loopable:
        samples, loop_crossfade_duration_s = _apply_loop_crossfade(samples, sample_rate)
        warnings.append(
            "A 250 ms reverse-head/tail edge crossfade was applied; musical continuity is not guaranteed."
        )
    else:
        samples = _fade_out(samples, sample_rate)

    if float(np.max(np.abs(samples))) < 1e-8:
        raise AudioProcessingError("AUDIO_SILENT", "Silent audio cannot be loudness-normalized or used as a valid music result.")

    meter = pyloudnorm.Meter(sample_rate)
    try:
        before_lufs = float(meter.integrated_loudness(samples))
    except (ValueError, ZeroDivisionError) as exc:
        raise AudioProcessingError("AUDIO_LOUDNESS_UNMEASURABLE", "Integrated loudness could not be measured.") from exc
    if not math.isfinite(before_lufs):
        raise AudioProcessingError("AUDIO_SILENT", "Audio is too quiet to measure integrated loudness.")
    samples *= 10 ** ((target_lufs - before_lufs) / 20)

    samples, limiter_reduced_gain = _limit_peaks(samples, sample_rate)
    if limiter_reduced_gain:
        warnings.append("A look-ahead peak limiter reduced transient gain before true-peak validation.")
    peak_ceiling = -2.0 if output_format == "mp3" else TRUE_PEAK_CEILING_DBTP
    peak_db = _true_peak_dbtp(samples)
    if peak_db > peak_ceiling:
        samples *= 10 ** ((peak_ceiling - peak_db) / 20)
        warnings.append("Peak protection reduced gain to keep measured true peak at or below -1 dBTP.")

    encoded = b""
    final_samples = samples
    measured_peak = float("inf")
    for attempt in range(4):
        encoded = _encode_audio(samples, sample_rate, output_format)
        final_samples, final_rate = _decode_audio(encoded, output_format)
        final_samples = _resample(final_samples, final_rate, sample_rate)[:exact_frames]
        if len(final_samples) < exact_frames:
            final_samples = np.pad(final_samples, ((0, exact_frames - len(final_samples)), (0, 0)))
        measured_peak = _true_peak_dbtp(final_samples)
        if measured_peak <= TRUE_PEAK_CEILING_DBTP + 0.01:
            break
        if output_format != "mp3" or attempt == 3:
            raise AudioProcessingError(
                "TRUE_PEAK_LIMIT_FAILED",
                "The encoded file exceeds the -1 dBTP ceiling after validation.",
            )
        samples *= 10 ** ((TRUE_PEAK_CEILING_DBTP - measured_peak - 0.1) / 20)
        warnings.append("MP3 codec reconstruction required additional gain reduction to meet the true-peak ceiling.")
    try:
        measured_lufs = float(meter.integrated_loudness(final_samples))
    except (ValueError, ZeroDivisionError):
        measured_lufs = float("nan")
    if not math.isfinite(measured_lufs):
        raise AudioProcessingError(
            "AUDIO_LOUDNESS_UNMEASURABLE",
            "Integrated loudness could not be measured from the encoded output.",
        )
    if abs(measured_lufs - target_lufs) > 1:
        warnings.append(
            f"Measured loudness is {measured_lufs:.2f} LUFS after peak protection; target was {target_lufs:.2f} LUFS."
        )
    loop_endpoint_discontinuity_dbfs: float | None = None
    loop_validated: bool | None = None
    if loopable:
        endpoint_delta = final_samples[0] - final_samples[-1]
        endpoint_rms = float(np.sqrt(np.mean(np.square(endpoint_delta))))
        loop_endpoint_discontinuity_dbfs = 20 * math.log10(max(endpoint_rms, 1e-12))
        loop_validated = loop_endpoint_discontinuity_dbfs <= -60
        if not loop_validated:
            warnings.append(
                "The encoded loop endpoint discontinuity exceeded the -60 dBFS validation threshold."
            )
    return ProcessedAudio(
        content=encoded,
        format=output_format,
        duration_s=len(final_samples) / sample_rate,
        sample_rate=sample_rate,
        channel_count=final_samples.shape[1],
        integrated_lufs=measured_lufs,
        true_peak_dbtp=measured_peak,
        warnings=warnings,
        loop_crossfade_duration_s=loop_crossfade_duration_s,
        loop_endpoint_discontinuity_dbfs=loop_endpoint_discontinuity_dbfs,
        loop_validated=loop_validated,
    )


def _decode_audio(content: bytes, audio_format: str) -> tuple[np.ndarray, int]:
    from mutagen import File as MutagenFile

    try:
        metadata = MutagenFile(io.BytesIO(content))
    except Exception as exc:
        raise AudioProcessingError("AUDIO_DECODE_FAILED", "Audio metadata could not be parsed.") from exc
    if metadata is None or metadata.info is None:
        raise AudioProcessingError("AUDIO_DECODE_FAILED", "The upload is not a supported audio file.")
    actual_format = "mp3" if metadata.__class__.__name__ == "MP3" else "wav" if metadata.__class__.__name__ == "WAVE" else None
    if actual_format != audio_format:
        raise AudioProcessingError(
            "AUDIO_FORMAT_MISMATCH",
            "The audio bytes do not match the reported source format.",
        )
    try:
        source_duration = float(metadata.info.length)
    except (TypeError, ValueError, AttributeError) as exc:
        raise AudioProcessingError("AUDIO_METADATA_INVALID", "Audio duration metadata is invalid.") from exc
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise AudioProcessingError("AUDIO_METADATA_INVALID", "Audio duration metadata is invalid.")
    if source_duration > 300.5:
        raise AudioProcessingError(
            "AUDIO_DURATION_TOO_LONG",
            "Audio files longer than 300 seconds are not accepted for this workflow.",
        )
    try:
        samples, sample_rate = sf.read(io.BytesIO(content), dtype="float64", always_2d=True)
        if sample_rate <= 0:
            raise AudioProcessingError("AUDIO_SAMPLE_RATE_INVALID", "Audio sample rate is invalid.")
        return samples, int(sample_rate)
    except (RuntimeError, ValueError) as exc:
        if audio_format != "mp3":
            raise AudioProcessingError("AUDIO_DECODE_FAILED", "The WAV audio could not be decoded.") from exc
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise AudioProcessingError(
            "AUDIO_DECODER_UNAVAILABLE",
            "MP3 decoding requires FFmpeg installed and available on PATH.",
        )
    with tempfile.TemporaryDirectory(prefix="soundcraft-decode-") as directory:
        source_path = Path(directory) / "input.mp3"
        target_path = Path(directory) / "decoded.wav"
        source_path.write_bytes(content)
        try:
            subprocess.run(
                [executable, "-nostdin", "-v", "error", "-y", "-i", str(source_path), "-c:a", "pcm_f32le", str(target_path)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            samples, sample_rate = sf.read(target_path, dtype="float64", always_2d=True)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            raise AudioProcessingError("AUDIO_DECODE_FAILED", "The MP3 audio could not be decoded by FFmpeg.") from exc
    return samples, int(sample_rate)


def _encode_audio(samples: np.ndarray, sample_rate: int, audio_format: str) -> bytes:
    if audio_format == "wav":
        output = io.BytesIO()
        sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_24")
        return output.getvalue()
    ensure_mp3_encoder()
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise AudioProcessingError("MP3_ENCODER_UNAVAILABLE", "FFmpeg is not available on PATH.")
    with tempfile.TemporaryDirectory(prefix="soundcraft-encode-") as directory:
        source_path = Path(directory) / "input.wav"
        target_path = Path(directory) / "output.mp3"
        sf.write(source_path, samples, sample_rate, format="WAV", subtype="PCM_24")
        try:
            subprocess.run(
                [executable, "-nostdin", "-v", "error", "-y", "-i", str(source_path), "-codec:a", "libmp3lame", "-b:a", "192k", str(target_path)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            return target_path.read_bytes()
        except (OSError, subprocess.SubprocessError) as exc:
            raise AudioProcessingError("MP3_ENCODING_FAILED", "FFmpeg failed to encode the MP3 output.") from exc


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(samples, dtype=np.float64)
    divisor = math.gcd(source_rate, target_rate)
    return resample_poly(samples, target_rate // divisor, source_rate // divisor, axis=0)


def _true_peak_dbtp(samples: np.ndarray) -> float:
    oversampled = resample_poly(samples, 4, 1, axis=0, window=("kaiser", 8.6))
    peak = float(np.max(np.abs(oversampled)))
    if peak <= 0:
        return float("-inf")
    return 20 * math.log10(peak)


def _limit_peaks(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, bool]:
    block_frames = max(1, sample_rate // 100)
    lookahead_frames = block_frames
    release = math.exp(-block_frames / (sample_rate * 0.1))
    ceiling = 10 ** (-2 / 20)
    block_gains: list[float] = []
    current_gain = 1.0
    reduced_gain = False

    for start in range(0, len(samples), block_frames):
        end = min(len(samples), start + block_frames)
        lookahead_end = min(len(samples), end + lookahead_frames)
        peak = float(np.max(np.abs(samples[start:lookahead_end])))
        target_gain = min(1.0, ceiling / peak) if peak > 0 else 1.0
        if target_gain < current_gain:
            current_gain = target_gain
            reduced_gain = True
        else:
            current_gain = target_gain + release * (current_gain - target_gain)
        block_gains.append(current_gain)

    gain_curve = np.repeat(np.asarray(block_gains), block_frames)[: len(samples)]
    return samples * gain_curve[:, np.newaxis], reduced_gain


def _fade_out(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    fade_frames = min(len(samples), max(1, round(sample_rate * 0.02)))
    fade = np.linspace(1.0, 0.0, fade_frames, endpoint=True)
    samples[-fade_frames:] *= fade[:, np.newaxis]
    return samples


def _apply_loop_crossfade(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, float]:
    crossfade_frames = min(round(0.25 * sample_rate), len(samples) // 4)
    if crossfade_frames < 2:
        raise AudioProcessingError(
            "LOOP_CROSSFADE_TOO_SHORT",
            "Audio is too short to apply a validated loop crossfade.",
        )
    angle = np.linspace(0, np.pi / 2, crossfade_frames, endpoint=True)
    tail_gain = np.cos(angle)[:, np.newaxis]
    head_gain = np.sin(angle)[:, np.newaxis]
    crossfade = (
        samples[-crossfade_frames:] * tail_gain
        + samples[:crossfade_frames][::-1] * head_gain
    )
    loop_samples = samples.copy()
    loop_samples[-crossfade_frames:] = crossfade
    return loop_samples, crossfade_frames / sample_rate


def _align_narration(narration: np.ndarray, target_frames: int) -> np.ndarray:
    if len(narration) > target_frames:
        return narration[:target_frames]
    return np.pad(narration, ((0, target_frames - len(narration)), (0, 0)))


def _narration_duck_curve(narration: np.ndarray) -> np.ndarray:
    block_frames = max(1, TARGET_SAMPLE_RATE // 100)
    mono = np.mean(narration, axis=1)
    active_gain = 10 ** (-NARRATION_DUCK_DB / 20)
    attack = math.exp(-block_frames / (TARGET_SAMPLE_RATE * 0.05))
    release = math.exp(-block_frames / (TARGET_SAMPLE_RATE * 0.25))
    curve = np.ones(len(mono), dtype=np.float64)
    current_gain = 1.0
    for start in range(0, len(mono), block_frames):
        end = min(len(mono), start + block_frames)
        rms = float(np.sqrt(np.mean(np.square(mono[start:end]))))
        target_gain = active_gain if rms >= 10 ** (-45 / 20) else 1.0
        coefficient = attack if target_gain < current_gain else release
        current_gain = target_gain + coefficient * (current_gain - target_gain)
        curve[start:end] = current_gain
    return curve
