from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from app.core.config import Settings, settings
from app.providers.music_provider import (
    ElevenLabsMusicProvider,
    MusicProvider,
    ProviderError,
)
from app.providers.stability_provider import StabilityMusicProvider
from app.schemas.music import AudioResult, JobError, JobStatus, StemArtifact, StemProcessingResult
from app.schemas.music import LoopValidation, OutputFormat
from app.services.audio_processing import (
    AudioProcessingError,
    ensure_mp3_decoder,
    ensure_mp3_encoder,
    mix_with_narration,
    process_audio,
)
from app.services.audio_storage import (
    AudioStorage,
    AudioValidationError,
    inspect_audio,
    inspect_audio_file,
)
from app.services.job_store import JobRecord, JobStore
from app.services.outbound_url import validate_outbound_url
from app.services.stem_separation import (
    SAM_LICENSE_URL,
    SAM_MODEL_URL,
    SAM_REPOSITORY_URL,
    StemSeparationError,
    StemSeparationResult,
    process_instrument_stems,
)

logger = logging.getLogger(__name__)


class JobManager:
    """Runs one real provider generation and persists its validated audio output."""

    def __init__(
        self,
        store: JobStore,
        provider: MusicProvider,
        audio_storage: AudioStorage,
        stem_processor: Callable[[bytes, str, float, int], StemSeparationResult] | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.audio_storage = audio_storage
        self.stem_processor = stem_processor or process_instrument_stems

    def process_job(self, job_id: str) -> None:
        started_at = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        job = self.store.update(job_id, status=JobStatus.running, progress=5)
        if job is None:
            logger.error("Queued job disappeared before processing", extra={"job_id": job_id})
            return

        generated = None
        output_format = (
            "mp3"
            if OutputFormat.mp3 in job.request.output.formats
            and OutputFormat.wav not in job.request.output.formats
            else "wav"
        )
        try:
            job.request.model_validate(job.request.model_dump())
            if not self.provider.is_configured():
                credential_variable = (
                    "STABILITY_API_KEY"
                    if self.provider.name == "stability"
                    else "ELEVENLABS_API_KEY"
                )
                raise ProviderError(
                    "PROVIDER_NOT_CONFIGURED",
                    f"Music generation is not configured. Set {credential_variable} in the backend environment.",
                    details={"provider": self.provider.name},
                )
            if output_format == "mp3":
                ensure_mp3_encoder()
            if self.provider.name == "elevenlabs":
                ensure_mp3_decoder()
            self.store.update(job_id, progress=10)
            provider_request = job.request.model_copy(deep=True)
            if self.provider.name == "stability":
                provider_request.output.formats = [OutputFormat.wav]
            elif self.provider.name == "elevenlabs":
                provider_request.output.formats = [OutputFormat.mp3]
            generated = self.provider.generate(provider_request, job_id)
            self.store.update(job_id, progress=80)
            processed = process_audio(
                generated.content,
                generated.format,
                job.request.duration_s,
                output_format,
                target_lufs=job.request.output.target_lufs,
                sample_rate=job.request.output.sample_rate,
                loopable=job.request.loopable,
            )
            self.store.update(job_id, progress=90)
            warnings = list(generated.warnings) + processed.warnings
            if len(job.request.output.formats) > 1:
                warnings.append(
                    f"Only {output_format.upper()} was delivered; the other requested format is not generated in the same job."
                )
            if job.request.sections:
                warnings.append(
                    "Section timestamps remain prompt guidance; beat alignment and exact section boundaries are not analyzed."
                )
            if job.request.loopable:
                if processed.loop_validated is False:
                    warnings.append("Loop crossfade was applied, but the saved file failed endpoint validation.")
            if job.request.tempo_bpm is not None or job.request.key is not None:
                warnings.append(
                    "Tempo and key were included as prompt guidance only; neither has been measured or guaranteed."
                )
            metadata = inspect_audio(processed.content, processed.format)
            if abs(metadata.duration_s - job.request.duration_s) > 0.05:
                raise AudioProcessingError(
                    "AUDIO_DURATION_VALIDATION_FAILED",
                    "The processed audio did not meet the requested duration tolerance of 0.05 seconds.",
                )
            if metadata.sample_rate != job.request.output.sample_rate:
                raise AudioProcessingError(
                    "AUDIO_SAMPLE_RATE_VALIDATION_FAILED",
                    "The processed audio did not match the requested sample rate.",
                )
            if processed.integrated_lufs is None:
                raise AudioProcessingError(
                    "AUDIO_LOUDNESS_UNMEASURABLE",
                    "Integrated loudness could not be measured from the processed audio.",
                )
            if abs(processed.integrated_lufs - job.request.output.target_lufs) > 1:
                raise AudioProcessingError(
                    "AUDIO_LOUDNESS_VALIDATION_FAILED",
                    "The processed audio did not meet the requested integrated loudness tolerance of 1 LU.",
                )
            if processed.true_peak_dbtp is None or processed.true_peak_dbtp > -1:
                raise AudioProcessingError(
                    "AUDIO_TRUE_PEAK_VALIDATION_FAILED",
                    "The processed audio did not meet the -1 dBTP ceiling.",
                )
            output_format = processed.format
            source_metadata = inspect_audio(generated.content, generated.format)
            source_path = self.audio_storage.save_source(job_id, generated.format, generated.content)
            output_path = self.audio_storage.save(job_id, output_format, processed.content)
            saved_metadata = inspect_audio_file(output_path, output_format)
            if saved_metadata != metadata:
                raise AudioValidationError("Stored audio metadata does not match the validated provider output.")
            saved_source_metadata = inspect_audio_file(source_path, generated.format)
            if saved_source_metadata != source_metadata:
                raise AudioValidationError("Stored provider source metadata does not match the provider output.")
            metadata = saved_metadata
            stem_processing = StemProcessingResult(status="not_requested")
            if job.request.separate_stems:
                self.store.update(job_id, progress=92)
                try:
                    separation = self.stem_processor(
                        processed.content,
                        output_format,
                        metadata.duration_s,
                        metadata.channel_count or 1,
                    )
                    returned_stem_ids = {stem.stem_id for stem in separation.stems}
                    if (
                        len(separation.stems) < 3
                        or returned_stem_ids != self.audio_storage.STEM_IDS
                    ):
                        raise StemSeparationError(
                            "STEM_COUNT_INVALID",
                            "The separator did not return the required validated instrument targets and residual.",
                        )
                    artifacts: list[StemArtifact] = []
                    for stem in separation.stems:
                        stem_path = self.audio_storage.save_stem(job_id, stem.stem_id, stem.content)
                        stem_metadata = inspect_audio_file(stem_path, "wav")
                        if (
                            stem_metadata.sample_rate != job.request.output.sample_rate
                            or stem_metadata.channel_count != metadata.channel_count
                            or abs(stem_metadata.duration_s - metadata.duration_s) > 0.05
                        ):
                            raise AudioValidationError(
                                f"Stored {stem.stem_id} stem does not align with the processed mix."
                            )
                        artifacts.append(
                            StemArtifact(
                                stem_id=stem.stem_id,
                                label=stem.label,
                                prompt=stem.prompt,
                                format="wav",
                                duration_s=stem_metadata.duration_s,
                                sample_rate=stem_metadata.sample_rate or 0,
                                channel_count=stem_metadata.channel_count or 0,
                                size_bytes=stem_path.stat().st_size,
                                file_name=stem_path.name,
                                gain_linear=stem.gain_linear,
                                gain_db=stem.gain_db,
                                download_url=f"/api/v1/jobs/{job_id}/stems/{stem.stem_id}",
                            )
                        )
                    stem_processing = StemProcessingResult(
                        status="available",
                        model_id=separation.model_id,
                        model_version=separation.model_version,
                        method=separation.method,
                        stems=artifacts,
                        reconstruction_relative_error_db=(
                            separation.reconstruction_relative_error_db
                        ),
                        reconstruction_metric=separation.reconstruction_metric,
                        reconstruction_note=separation.reconstruction_note,
                    )
                except StemSeparationError as exc:
                    self._cleanup_stems(job_id)
                    unavailable_codes = {
                        "STEM_DEPENDENCY_MISSING",
                        "SAM_CHECKPOINT_NOT_CACHED",
                        "SAM_CHECKPOINT_ACCESS_REQUIRED",
                        "SAM_MODEL_LOAD_FAILED",
                    }
                    stem_processing = StemProcessingResult(
                        status="unavailable" if exc.code in unavailable_codes else "failed",
                        error=JobError(
                            code=exc.code,
                            message=exc.message,
                            retryable=False,
                            details={"stage": "stem_separation"},
                        ),
                    )
                    warnings.append(
                        f"Instrument stem processing {stem_processing.status}: {exc.message}"
                    )
                except (AudioValidationError, OSError, ValueError) as exc:
                    self._cleanup_stems(job_id)
                    stem_processing = StemProcessingResult(
                        status="failed",
                        error=JobError(
                            code="STEM_OUTPUT_INVALID"
                            if isinstance(exc, (AudioValidationError, ValueError))
                            else "STEM_STORAGE_FAILED",
                            message="Instrument stems could not be saved and validated.",
                            retryable=False,
                            details={"stage": "stem_storage"},
                        ),
                    )
                    warnings.append(
                        "Instrument stem processing failed while saving or validating the stem files."
                    )
            mix_ducked_download_url: str | None = None
            if job.request.narration_url is not None:
                try:
                    validate_outbound_url(
                        str(job.request.narration_url),
                        settings.outbound_url_allowed_hosts,
                    )
                    self.store.update(job_id, progress=95)
                    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
                        with client.stream("GET", str(job.request.narration_url)) as resp:
                            if resp.status_code != 200:
                                warnings.append(
                                    f"The narration URL returned HTTP {resp.status_code}; no remote audio was mixed."
                                )
                                narration_content = b""
                                narration_format = None
                            else:
                                narration_format = {
                                    "audio/wav": "wav",
                                    "audio/x-wav": "wav",
                                    "audio/wave": "wav",
                                    "audio/mpeg": "mp3",
                                    "audio/mp3": "mp3",
                                }.get(
                                    resp.headers.get("content-type", "")
                                    .split(";", 1)[0]
                                    .lower()
                                )
                                if narration_format is None:
                                    raise ValueError(
                                        "The narration URL did not return a supported WAV or MP3 content type."
                                    )
                                content_length = resp.headers.get("content-length")
                                if content_length and int(content_length) > settings.max_narration_bytes:
                                    raise ValueError("Narration audio exceeds the configured upload limit.")
                                narration_bytes = bytearray()
                                for chunk in resp.iter_bytes():
                                    narration_bytes.extend(chunk)
                                    if len(narration_bytes) > settings.max_narration_bytes:
                                        raise ValueError(
                                            "Narration audio exceeds the configured upload limit."
                                        )
                                narration_content = bytes(narration_bytes)

                    if narration_content and narration_format:
                        ducked_mix = mix_with_narration(
                            processed.content,
                            output_format,
                            narration_content,
                            narration_format,
                            target_lufs=job.request.output.target_lufs,
                            output_format=output_format,
                        )
                        self.audio_storage.save_variant(
                            job_id,
                            "narration-mix",
                            ducked_mix.format,
                            ducked_mix.content,
                        )
                        ducked_manifest = {
                            "job_id": job_id,
                            "format": ducked_mix.format,
                            "duration_s": ducked_mix.duration_s,
                            "sample_rate": ducked_mix.sample_rate,
                            "channel_count": ducked_mix.channel_count,
                            "size_bytes": len(ducked_mix.content),
                            "duck_attenuation_db": 12.0,
                            "integrated_lufs": ducked_mix.integrated_lufs,
                            "true_peak_dbtp": ducked_mix.true_peak_dbtp,
                            "download_url": f"/api/v1/jobs/{job_id}/narration/audio",
                            "warnings": ducked_mix.warnings,
                        }
                        self.audio_storage.save_narration_manifest(job_id, ducked_manifest)
                        mix_ducked_download_url = f"/api/v1/jobs/{job_id}/narration/audio"
                except (httpx.HTTPError, OSError, ValueError, AudioProcessingError, AudioValidationError) as exc:
                    logger.warning(
                        "Remote narration could not be downloaded or mixed",
                        extra={"job_id": job_id, "failure_type": type(exc).__name__},
                    )
                    warnings.append(
                        "The narration URL was configured but could not be downloaded, validated, or mixed."
                    )
            completed_at = datetime.now(timezone.utc)
            processing_duration_s = time.perf_counter() - started_clock
            result = AudioResult(
                provider=generated.provider,
                model=generated.model,
                format=metadata.format,
                requested_duration_s=job.request.duration_s,
                requested_sample_rate=job.request.output.sample_rate,
                target_integrated_lufs=job.request.output.target_lufs,
                true_peak_ceiling_dbtp=-1.0,
                sample_rate=metadata.sample_rate,
                channel_count=metadata.channel_count,
                duration_s=metadata.duration_s,
                size_bytes=len(processed.content),
                integrated_lufs=processed.integrated_lufs,
                true_peak_dbtp=processed.true_peak_dbtp,
                is_ai_generated=generated.is_ai_generated,
                download_url=f"/api/v1/jobs/{job_id}/audio",
                mix_ducked_download_url=mix_ducked_download_url,
                manifest_download_url=f"/api/v1/jobs/{job_id}/manifest",
                loop_validation=LoopValidation(
                    status=(
                        "not_requested"
                        if not job.request.loopable
                        else "passed"
                        if processed.loop_validated
                        else "failed"
                    ),
                    crossfade_duration_s=processed.loop_crossfade_duration_s,
                    endpoint_discontinuity_dbfs=processed.loop_endpoint_discontinuity_dbfs,
                    note=(
                        "No loop processing was requested."
                        if not job.request.loopable
                        else "Validates only the encoded first/last sample discontinuity (<= -60 dBFS); it does not assess musical, harmonic, or rhythmic continuity. A short reverse-head/tail crossfade is used."
                    ),
                ),
                stem_processing=stem_processing,
                generation_started_at=started_at,
                generation_completed_at=completed_at,
                processing_duration_s=processing_duration_s,
            )
            if generated.is_ai_generated:
                warnings.append(
                    "Instrumental-only generation was requested from the provider; vocals were not independently detected or assessed."
                )
            terms_url = {
                "stability": "https://stability.ai/terms-of-service",
                "elevenlabs": "https://elevenlabs.io/music-terms",
            }.get(generated.provider)
            processing_requirements = {
                "duration": {
                    "status": "passed" if abs(metadata.duration_s - job.request.duration_s) <= 0.05 else "failed",
                    "tolerance_s": 0.05,
                },
                "sample_rate": {
                    "status": "passed" if metadata.sample_rate == job.request.output.sample_rate else "failed",
                    "required_hz": job.request.output.sample_rate,
                },
                "integrated_lufs": {
                    "status": (
                        "not_measured"
                        if processed.integrated_lufs is None
                        else "passed"
                        if abs(processed.integrated_lufs - job.request.output.target_lufs) <= 1
                        else "failed"
                    ),
                    "tolerance_lu": 1,
                },
                "true_peak": {
                    "status": (
                        "not_measured"
                        if processed.true_peak_dbtp is None
                        else "passed"
                        if processed.true_peak_dbtp <= -1
                        else "failed"
                    ),
                    "ceiling_dbtp": -1,
                },
            }
            manifest = {
                "job_id": job_id,
                "status": "succeeded",
                "provider": generated.provider,
                "model": generated.model,
                "is_ai_generated": generated.is_ai_generated,
                "generated_file": f"{job_id}.{metadata.format}",
                "processed_file": f"{job_id}.{metadata.format}",
                "mix_ducked_file": f"{job_id}.narration-mix.{metadata.format}" if mix_ducked_download_url else None,
                "manifest_file": "music_manifest.json",
                "provider_source": {
                    "file": source_path.name,
                    "format": source_metadata.format,
                    "duration_s": source_metadata.duration_s,
                    "sample_rate": source_metadata.sample_rate,
                    "channel_count": source_metadata.channel_count,
                    "size_bytes": len(generated.content),
                },
                "generation_started_at": started_at.isoformat(),
                "generation_completed_at": completed_at.isoformat(),
                "processing_duration_s": processing_duration_s,
                "request": job.request.model_dump(mode="json"),
                "arrangement": self._arrangement_manifest(job),
                "output": result.model_dump(mode="json"),
                "processing": {
                    "requested": {
                        "duration_s": job.request.duration_s,
                        "sample_rate": job.request.output.sample_rate,
                        "integrated_lufs": job.request.output.target_lufs,
                        "true_peak_ceiling_dbtp": -1,
                    },
                    "measured": {
                        "duration_s": metadata.duration_s,
                        "sample_rate": metadata.sample_rate,
                        "integrated_lufs": processed.integrated_lufs,
                        "true_peak_dbtp": processed.true_peak_dbtp,
                    },
                    "requirements": processing_requirements,
                    "loop_validation": result.loop_validation.model_dump(mode="json"),
                },
                "stem_separation": {
                    **stem_processing.model_dump(mode="json"),
                    "license": {
                        "license_name": "SAM License",
                        "license_url": SAM_LICENSE_URL,
                        "model_url": SAM_MODEL_URL,
                        "source_repository": SAM_REPOSITORY_URL,
                        "commercial_use_review_required": True,
                    },
                },
                "warnings": warnings,
                "licensing": {
                    "status": "plan_specific_and_not_independently_verified",
                    "terms_url": terms_url,
                    "note": "Review provider terms and applicable law for the intended use; this manifest is not a rights clearance.",
                },
            }
            self.audio_storage.save_manifest(job_id, manifest)
            self.store.update(
                job_id,
                status=JobStatus.succeeded,
                progress=100,
                error=None,
                result=result,
                warnings=warnings,
            )
            logger.info(
                "Music generation job completed",
                extra={
                    "job_id": job_id,
                    "provider": generated.provider,
                    "format": metadata.format,
                    "duration_s": round(metadata.duration_s, 3),
                    "size_bytes": len(processed.content),
                },
            )
        except AudioProcessingError as exc:
            self._cleanup_failed_output(job_id, output_format, generated.format if generated else None)
            self.store.update(
                job_id,
                status=JobStatus.failed,
                error=JobError(
                    code=exc.code,
                    message=exc.message,
                    retryable=False,
                    details={"stage": "audio_processing"},
                ),
            )
            logger.warning(
                "Music generation job failed during audio processing",
                extra={"job_id": job_id, "error_code": exc.code},
            )
        except ProviderError as exc:
            self._cleanup_failed_output(job_id, output_format, generated.format if generated else None)
            self.store.update(
                job_id,
                status=JobStatus.failed,
                error=JobError(
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                    details=exc.details,
                ),
            )
            logger.warning(
                "Music generation job failed at provider",
                extra={"job_id": job_id, "provider": self.provider.name, "error_code": exc.code},
            )
        except (AudioValidationError, OSError) as exc:
            self._cleanup_failed_output(job_id, output_format, generated.format if generated else None)
            logger.exception("Generated audio validation or storage failed", extra={"job_id": job_id})
            self.store.update(
                job_id,
                status=JobStatus.failed,
                error=JobError(
                    code="AUDIO_OUTPUT_INVALID" if isinstance(exc, AudioValidationError) else "AUDIO_STORAGE_FAILED",
                    message="The generated audio could not be validated or stored.",
                    retryable=False,
                    details={"stage": "audio_validation" if isinstance(exc, AudioValidationError) else "storage"},
                ),
            )
        except Exception:
            self._cleanup_failed_output(job_id, output_format, generated.format if generated else None)
            logger.exception("Unexpected music job processing failure", extra={"job_id": job_id})
            self.store.update(
                job_id,
                status=JobStatus.failed,
                error=JobError(
                    code="JOB_PROCESSING_FAILED",
                    message="The job could not be processed due to an internal error.",
                    retryable=False,
                    details={"stage": "processing"},
                ),
            )
        finally:
            self._dispatch_callback_if_requested(job_id)

    def _dispatch_callback_if_requested(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None or job.request.callback_url is None:
            return
        if not settings.webhook_secret:
            logger.error(
                "Webhook callback was not sent because WEBHOOK_SECRET is not configured",
                extra={"job_id": job_id},
            )
            return
        try:
            validate_outbound_url(
                str(job.request.callback_url),
                settings.outbound_url_allowed_hosts,
            )
            payload = {
                "job_id": job.job_id,
                "status": job.status.value if hasattr(job.status, "value") else job.status,
                "progress": job.progress,
                "created_at": job.created_at.isoformat(),
                "updated_at": job.updated_at.isoformat(),
                "error": job.error.model_dump(mode="json") if job.error else None,
                "result": job.result.model_dump(mode="json") if job.result else None,
                "warnings": job.warnings or [],
                "prompt": job.request.prompt,
                "duration_s": job.request.duration_s,
            }
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            signature = hmac.new(
                settings.webhook_secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers = {
                "Content-Type": "application/json",
                "X-IncuBrix-Signature": signature,
            }
            with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                response = client.post(
                    str(job.request.callback_url),
                    content=body_bytes,
                    headers=headers,
                )
                if response is not None and not 200 <= response.status_code < 300:
                    logger.warning(
                        "Webhook callback returned a non-success status",
                        extra={"job_id": job_id, "response_status": response.status_code},
                    )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning(
                "Webhook callback was rejected or failed",
                extra={"job_id": job_id, "failure_type": type(exc).__name__},
            )

    @staticmethod
    def _arrangement_manifest(job: JobRecord) -> dict[str, object]:
        tempo = job.request.tempo_bpm
        bar_duration_s = 240 / tempo if tempo else None
        sections = []
        for section in job.request.sections:
            section_metadata: dict[str, object] = {
                "start_s": section.start_s,
                "mood": section.mood,
            }
            if bar_duration_s is not None:
                nearest_bar = round(section.start_s / bar_duration_s)
                section_metadata.update(
                    {
                        "nearest_4_4_bar": nearest_bar + 1,
                        "nearest_bar_start_s": nearest_bar * bar_duration_s,
                        "offset_from_nearest_bar_s": section.start_s - nearest_bar * bar_duration_s,
                    }
                )
            sections.append(section_metadata)
        return {
            "tempo_bpm_prompt_target": tempo,
            "key_prompt_target": job.request.key,
            "meter_assumption": "4/4" if tempo else None,
            "estimated_bar_duration_s": bar_duration_s,
            "sections": sections,
            "audio_beat_grid_measured": False,
            "section_boundaries_applied_to_audio": False,
            "note": "Section times, tempo, and key are arrangement/prompt metadata. Estimated bar positions assume 4/4 and do not establish alignment to generated audio.",
        }

    def _cleanup_failed_output(
        self,
        job_id: str,
        audio_format: str | None,
        source_format: str | None = None,
    ) -> None:
        self._cleanup_stems(job_id)
        cleanup_actions = [("manifest", lambda: self.audio_storage.delete_manifest(job_id))]
        if audio_format is not None:
            cleanup_actions.insert(
                0,
                ("audio", lambda: self.audio_storage.delete(job_id, audio_format)),
            )
        if source_format is not None:
            cleanup_actions.insert(
                0,
                ("provider source", lambda: self.audio_storage.delete_source(job_id, source_format)),
            )
        for asset, cleanup in cleanup_actions:
            try:
                cleanup()
            except OSError:
                logger.exception(
                    "Failed to clean up generated asset after job failure",
                    extra={"job_id": job_id, "asset": asset},
                )

    def _cleanup_stems(self, job_id: str) -> None:
        for stem_id in self.audio_storage.STEM_IDS:
            try:
                self.audio_storage.delete_stem(job_id, stem_id)
            except OSError:
                logger.exception(
                    "Failed to clean up generated stem after stem processing failure",
                    extra={"job_id": job_id, "stem_id": stem_id},
                )


def create_music_provider(settings: Settings) -> MusicProvider:
    if settings.music_provider == "mock":
        from app.providers.mock_provider import MockMusicProvider

        return MockMusicProvider()
    if settings.music_provider == "stability":
        return StabilityMusicProvider(
            api_key=settings.stability_api_key,
            timeout_seconds=settings.provider_timeout_seconds,
            max_audio_bytes=settings.max_generated_audio_bytes,
        )
    if settings.music_provider == "elevenlabs":
        return ElevenLabsMusicProvider(
            api_key=settings.elevenlabs_api_key,
            model_id=settings.elevenlabs_model_id,
            timeout_seconds=settings.provider_timeout_seconds,
            max_audio_bytes=settings.max_generated_audio_bytes,
        )
    raise ValueError(f"Unsupported MUSIC_PROVIDER value: {settings.music_provider}")
