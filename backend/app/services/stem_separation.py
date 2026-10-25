from __future__ import annotations

import io
import logging
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

import numpy as np
import soundfile as sf

from app.services.audio_processing import AudioProcessingError, decode_audio_samples

SAM_AUDIO_MODEL_ID = "facebook/sam-audio-small"
SAM_AUDIO_MODEL_REVISION = "20b65f56888142eebe7c37448c6f6b3b32600e9b"
SAM_AUDIO_SOURCE_REVISION = "bb4c6999d2677c7402360e426afc01ddfad6dce0"
SAM_LICENSE_URL = "https://github.com/facebookresearch/sam-audio/blob/main/LICENSE"
SAM_MODEL_URL = "https://huggingface.co/facebook/sam-audio-small"
SAM_REPOSITORY_URL = "https://github.com/facebookresearch/sam-audio"
SAM_SAMPLE_RATE = 48_000
ALIGNMENT_TOLERANCE_S = 0.05
_SAM_INFERENCE_LOCK = Lock()
logger = logging.getLogger(__name__)


class StemSeparationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SeparationBackend(Protocol):
    model_id: str
    model_version: str
    sample_rate: int

    def separate(self, mono_audio: np.ndarray, description: str) -> tuple[np.ndarray, np.ndarray]: ...


@dataclass(frozen=True)
class StemAudio:
    stem_id: str
    label: str
    prompt: str
    content: bytes
    duration_s: float
    sample_rate: int
    channel_count: int
    gain_linear: float
    gain_db: float


@dataclass(frozen=True)
class StemSeparationResult:
    stems: list[StemAudio]
    model_id: str
    model_version: str
    method: str
    reconstruction_relative_error_db: float
    reconstruction_metric: str
    reconstruction_note: str


def process_instrument_stems(
    content: bytes,
    audio_format: str,
    duration_s: float,
    channel_count: int,
    backend: SeparationBackend | None = None,
) -> StemSeparationResult:
    if not math.isfinite(duration_s) or not 5 <= duration_s <= 300:
        raise StemSeparationError(
            "STEM_DURATION_INVALID",
            "Stem processing duration must be between 5 and 300 seconds.",
        )
    if channel_count not in {1, 2}:
        raise StemSeparationError(
            "STEM_CHANNELS_UNSUPPORTED",
            "Stem processing requires a mono or stereo processed mix.",
        )
    try:
        samples, sample_rate = decode_audio_samples(content, audio_format)
    except AudioProcessingError as exc:
        raise StemSeparationError(exc.code, exc.message) from exc
    if sample_rate != SAM_SAMPLE_RATE:
        raise StemSeparationError(
            "STEM_SAMPLE_RATE_UNSUPPORTED",
            "Stem input must be the validated 48,000 Hz processed mix.",
        )
    expected_frames = round(duration_s * SAM_SAMPLE_RATE)
    samples = _align_audio(samples, expected_frames)
    if samples.ndim != 2 or samples.shape[1] != channel_count or not np.isfinite(samples).all():
        raise StemSeparationError(
            "STEM_INPUT_INVALID",
            "The processed mix has invalid sample values or channel dimensions.",
        )
    reference_mono = np.mean(samples, axis=1)
    if not np.any(np.abs(reference_mono) > 1e-8):
        raise StemSeparationError("STEM_INPUT_SILENT", "Silent audio cannot be separated into instrument stems.")

    separator = backend if backend is not None else _load_sam_audio_backend()
    if separator.sample_rate != SAM_SAMPLE_RATE:
        raise StemSeparationError(
            "STEM_MODEL_SAMPLE_RATE_UNSUPPORTED",
            "The selected separator does not produce 48,000 Hz audio.",
        )
    descriptions = (
        ("percussion", "Drums and percussion", "drums and percussion"),
        ("bass", "Bass", "bass instruments"),
        ("other", "Other instruments", "guitar, piano, keyboard, and string instruments"),
    )
    residual = reference_mono
    stem_signals: list[tuple[str, str, str, np.ndarray]] = []
    for stem_id, label, prompt in descriptions:
        target, next_residual = separator.separate(residual, prompt)
        target = _validate_model_output(target, expected_frames)
        residual = _validate_model_output(next_residual, expected_frames)
        stem_signals.append((stem_id, label, prompt, target))
    stem_signals.append(
        (
            "remaining",
            "Remaining mix (model residual)",
            "model residual after percussion, bass, and other-instrument prompts",
            residual,
        )
    )

    stems: list[StemAudio] = []
    recombined = np.zeros(expected_frames, dtype=np.float64)
    for stem_id, label, prompt, signal in stem_signals:
        try:
            stem = _encode_stem(stem_id, label, prompt, signal, channel_count)
            saved_audio, saved_rate = sf.read(
                io.BytesIO(stem.content),
                dtype="float64",
                always_2d=True,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise StemSeparationError(
                "STEM_OUTPUT_INVALID",
                "A generated stem could not be encoded or re-read for validation.",
            ) from exc
        if saved_rate != SAM_SAMPLE_RATE or saved_audio.shape != (expected_frames, channel_count):
            raise StemSeparationError(
                "STEM_OUTPUT_INVALID",
                "A saved stem did not preserve the mix sample rate, duration, or channel layout.",
            )
        recombined += np.mean(saved_audio, axis=1)
        stems.append(stem)
    difference_rms = float(np.sqrt(np.mean(np.square(recombined - reference_mono))))
    reference_rms = float(np.sqrt(np.mean(np.square(reference_mono))))
    if reference_rms <= 1e-12:
        raise StemSeparationError(
            "STEM_RECONSTRUCTION_UNMEASURABLE",
            "The reference mix is too quiet to measure stem reconstruction error.",
        )
    relative_error_db = (
        -300.0
        if difference_rms <= 1e-12
        else float(20 * np.log10(difference_rms / reference_rms))
    )
    return StemSeparationResult(
        stems=stems,
        model_id=separator.model_id,
        model_version=separator.model_version,
        method="Sequential SAM-Audio text-prompt target/residual separation",
        reconstruction_relative_error_db=relative_error_db,
        reconstruction_metric="20*log10(RMS(sum(stems)-mono_reference)/RMS(mono_reference))",
        reconstruction_note=(
            "The reference is the mono downmix of the saved processed mix. Model target/residual outputs are "
            "recombined at unity gain; -300 dB is the reported numerical floor for effectively zero error. "
            "A negative value indicates error below the reference RMS. This metric "
            "does not establish perceptual stem quality, source purity, or absence of bleed. The remaining "
            "track is an inclusive residual, not a guaranteed instrument-only class. Stems are dual-mono "
            "copies because the documented SAM-Audio result format is a one-dimensional waveform."
        ),
    )


def _validate_model_output(samples: np.ndarray, expected_frames: int) -> np.ndarray:
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or not np.isfinite(signal).all():
        raise StemSeparationError(
            "STEM_MODEL_OUTPUT_INVALID",
            "SAM-Audio returned malformed or non-finite waveform data.",
        )
    return _align_audio(signal, expected_frames)


def _align_audio(samples: np.ndarray, expected_frames: int) -> np.ndarray:
    if samples.ndim not in {1, 2} or len(samples) == 0:
        raise StemSeparationError("STEM_AUDIO_MALFORMED", "Stem audio is empty or has an invalid shape.")
    difference = abs(len(samples) - expected_frames) / SAM_SAMPLE_RATE
    if difference > ALIGNMENT_TOLERANCE_S:
        raise StemSeparationError(
            "STEM_DURATION_MISMATCH",
            "The mix or model output differs from the requested duration by more than 0.05 seconds.",
        )
    if len(samples) > expected_frames:
        return samples[:expected_frames]
    if len(samples) < expected_frames:
        padding = [(0, expected_frames - len(samples))]
        if samples.ndim == 2:
            padding.append((0, 0))
        return np.pad(samples, padding)
    return samples


def _encode_stem(
    stem_id: str,
    label: str,
    prompt: str,
    mono_signal: np.ndarray,
    channel_count: int,
) -> StemAudio:
    channels = np.repeat(mono_signal[:, np.newaxis], channel_count, axis=1)
    output = io.BytesIO()
    sf.write(output, channels.astype(np.float32), SAM_SAMPLE_RATE, format="WAV", subtype="FLOAT")
    return StemAudio(
        stem_id=stem_id,
        label=label,
        prompt=prompt,
        content=output.getvalue(),
        duration_s=len(mono_signal) / SAM_SAMPLE_RATE,
        sample_rate=SAM_SAMPLE_RATE,
        channel_count=channel_count,
        gain_linear=1.0,
        gain_db=0.0,
    )


class _SamAudioBackend:
    def __init__(self) -> None:
        try:
            import torch
            from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError
            from sam_audio import SAMAudio, SAMAudioProcessor
        except ImportError as exc:
            raise StemSeparationError(
                "STEM_DEPENDENCY_MISSING",
                "Local SAM-Audio support is not installed. Follow the Phase 4 setup instructions in README.md.",
            ) from exc
        self._torch = torch
        self._torch = torch
        self.model_id = SAM_AUDIO_MODEL_ID
        self.model_version = (
            f"weights:{SAM_AUDIO_MODEL_REVISION};code:{SAM_AUDIO_SOURCE_REVISION}"
        )
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self.model, self.processor = _load_sam_audio_from_cache(
                SAMAudio,
                SAMAudioProcessor,
                self.model_id,
                SAM_AUDIO_MODEL_REVISION,
            )
            self.model = self.model.to(self.device).eval()
            self.sample_rate = int(self.processor.audio_sampling_rate)
        except LocalEntryNotFoundError as exc:
            raise StemSeparationError(
                "SAM_CHECKPOINT_NOT_CACHED",
                "SAM-Audio weights are not present in the local Hugging Face cache. After manual checkpoint approval, download the pinned files explicitly; the application does not download model weights.",
            ) from exc
        except HfHubHTTPError as exc:
            raise StemSeparationError(
                "SAM_CHECKPOINT_ACCESS_REQUIRED",
                "SAM-Audio checkpoint access is unavailable. Request and accept access to the model as the account holder, then authenticate locally with Hugging Face.",
            ) from exc
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise StemSeparationError(
                "SAM_MODEL_LOAD_FAILED",
                "The SAM-Audio model could not be loaded. Check local memory, PyTorch, and model-cache setup.",
            ) from exc
        if self.sample_rate != SAM_SAMPLE_RATE:
            raise StemSeparationError(
                "STEM_MODEL_SAMPLE_RATE_UNSUPPORTED",
                "The selected SAM-Audio processor does not produce 48,000 Hz audio.",
            )

    def separate(self, mono_audio: np.ndarray, description: str) -> tuple[np.ndarray, np.ndarray]:
        with tempfile.TemporaryDirectory(prefix="soundcraft-sam-") as directory:
            input_path = Path(directory) / "residual.wav"
            try:
                sf.write(input_path, mono_audio, self.sample_rate, subtype="FLOAT")
                inputs = self.processor(
                    audios=[str(input_path)],
                    descriptions=[description],
                ).to(self.device)
                with _SAM_INFERENCE_LOCK, self._torch.inference_mode():
                    result = self.model.separate(
                        inputs,
                        predict_spans=False,
                        reranking_candidates=1,
                    )
                target = np.asarray(result.target[0].detach().float().cpu().numpy())
                residual = np.asarray(result.residual[0].detach().float().cpu().numpy())
            except (AttributeError, IndexError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "SAM-Audio separation failed",
                    extra={"model_id": self.model_id, "device": self.device},
                    exc_info=True,
                )
                raise StemSeparationError(
                    "SAM_SEPARATION_FAILED",
                    "SAM-Audio failed while separating an instrument prompt. The music mix remains available.",
                ) from exc
        return target, residual


_sam_backend: _SamAudioBackend | None = None
_sam_backend_lock = Lock()


def _load_sam_audio_from_cache(
    model_class: type,
    processor_class: type,
    model_id: str,
    revision: str,
) -> tuple[object, object]:
    model = model_class.from_pretrained(
        model_id,
        revision=revision,
        local_files_only=True,
    )
    processor = processor_class.from_pretrained(
        model_id,
        revision=revision,
        local_files_only=True,
    )
    return model, processor


def _load_sam_audio_backend() -> _SamAudioBackend:
    global _sam_backend
    with _sam_backend_lock:
        if _sam_backend is None:
            _sam_backend = _SamAudioBackend()
        return _sam_backend
