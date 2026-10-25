from __future__ import annotations

import json
import logging
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.schemas.music import JobAccepted, JobCreateRequest, JobResponse, NarrationMixResponse
from app.services.audio_processing import AudioProcessingError, mix_with_narration
from app.services.audio_storage import AudioStorage, AudioValidationError, inspect_audio_file
from app.services.job_manager import JobManager, create_music_provider
from app.services.job_store import IdempotencyConflict, JobRecord, job_store
from app.services.outbound_url import validate_outbound_url

logger = logging.getLogger(__name__)
router = APIRouter()
audio_storage = AudioStorage(settings.audio_storage_dir)
job_manager = JobManager(job_store, create_music_provider(settings), audio_storage)
NARRATION_CONTENT_TYPES = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
}


class ProviderConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["stability", "elevenlabs", "mock"]


@router.get("/provider", operation_id="getProviderConfig")
def get_provider_config() -> dict[str, Any]:
    active_name = settings.music_provider
    configured = job_manager.provider.is_configured()
    credits = None
    if active_name == "stability" and settings.stability_api_key:
        try:
            resp = httpx.get(
                "https://api.stability.ai/v1/user/balance",
                headers={"Authorization": f"Bearer {settings.stability_api_key}"},
                timeout=5.0,
            )
            resp.raise_for_status()
            balance = resp.json()
            if isinstance(balance, dict) and isinstance(balance.get("credits"), (int, float)):
                credits = balance["credits"]
            else:
                logger.warning("Stability returned an invalid credit balance response")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Stability credit balance could not be checked",
                extra={"failure_type": type(exc).__name__},
            )
    return {
        "provider": active_name,
        "is_configured": configured,
        "stability_credits": credits,
        "model": {
            "stability": "stable-audio-3",
            "elevenlabs": settings.elevenlabs_model_id,
            "mock": "test-fixture",
        }[active_name],
        "has_api_key": (
            bool(settings.stability_api_key)
            if active_name == "stability"
            else bool(settings.elevenlabs_api_key)
            if active_name == "elevenlabs"
            else False
        ),
    }


@router.post("/provider", operation_id="updateProviderConfig")
def update_provider_config(body: ProviderConfigRequest) -> dict[str, Any]:
    settings.music_provider = body.provider
    job_manager.provider = create_music_provider(settings)
    return get_provider_config()


def serialize_job(job: JobRecord) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error=job.error,
        result=job.result,
        warnings=job.warnings or [],
        prompt=job.request.prompt,
        duration_s=job.request.duration_s,
    )


@router.get("/jobs", response_model=list[JobResponse], operation_id="listRecentJobs")
def list_jobs(limit: int = 50) -> list[JobResponse]:
    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_JOB_LIMIT",
                "message": "Job history limit must be between 1 and 100.",
                "details": {"minimum": 1, "maximum": 100},
            },
        )
    return [serialize_job(job) for job in job_store.list_recent(limit)]


@router.get("/jobs/{job_id}", response_model=JobResponse, operation_id="getJobStatus")
def get_job(job_id: str) -> JobResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": "The requested job does not exist.",
                "details": {"job_id": job_id},
            },
        )
    return serialize_job(job)


@router.get("/jobs/{job_id}/audio", operation_id="downloadJobAudio")
def download_job_audio(job_id: str) -> FileResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": "The requested job does not exist.",
                "details": {"job_id": job_id},
            },
        )
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "AUDIO_NOT_AVAILABLE",
                "message": "This job has no successfully generated audio.",
                "details": {"job_id": job_id},
            },
        )
    try:
        audio_path = audio_storage.path_for(job_id, job.result.format)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "AUDIO_NOT_AVAILABLE", "message": "Audio is not available."},
        ) from exc
    if not audio_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "AUDIO_FILE_NOT_FOUND",
                "message": "The generated audio file is no longer available.",
                "details": {"job_id": job_id},
            },
        )
    media_type = "audio/mpeg" if job.result.format == "mp3" else "audio/wav"
    return FileResponse(
        path=audio_path,
        media_type=media_type,
        filename=f"{job_id}.{job.result.format}",
    )


@router.get("/jobs/{job_id}/stems/{stem_id}", operation_id="downloadJobStem")
def download_job_stem(job_id: str, stem_id: str) -> FileResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": "The requested job does not exist.",
                "details": {"job_id": job_id},
            },
        )
    if (
        job.status != "succeeded"
        or job.result is None
        or job.result.stem_processing.status != "available"
        or not any(stem.stem_id == stem_id for stem in job.result.stem_processing.stems)
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STEM_NOT_AVAILABLE",
                "message": "This job has no validated stem with that identifier.",
                "details": {"job_id": job_id, "stem_id": stem_id},
            },
        )
    try:
        stem_path = audio_storage.stem_path_for(job_id, stem_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "STEM_NOT_AVAILABLE", "message": "Stem is not available."},
        ) from exc
    if not stem_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STEM_FILE_NOT_FOUND",
                "message": "The validated stem file is no longer available.",
                "details": {"job_id": job_id, "stem_id": stem_id},
            },
        )
    return FileResponse(
        path=stem_path,
        media_type="audio/wav",
        filename=f"{job_id}.stem.{stem_id}.wav",
    )


@router.get("/jobs/{job_id}/manifest", operation_id="getJobManifest")
def get_job_manifest(job_id: str) -> JSONResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": "The requested job does not exist.",
                "details": {"job_id": job_id},
            },
        )
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MANIFEST_NOT_AVAILABLE",
                "message": "This job has no successfully generated manifest.",
                "details": {"job_id": job_id},
            },
        )
    try:
        manifest_path = audio_storage.manifest_path_for(job_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "MANIFEST_NOT_AVAILABLE", "message": "Manifest is not available."},
        ) from exc
    if not manifest_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MANIFEST_FILE_NOT_FOUND",
                "message": "The generation manifest is no longer available.",
                "details": {"job_id": job_id},
            },
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Stored generation manifest could not be read", extra={"job_id": job_id})
        raise HTTPException(
            status_code=500,
            detail={
                "code": "MANIFEST_READ_FAILED",
                "message": "The generation manifest could not be read.",
                "details": {"job_id": job_id},
            },
        ) from exc
    return JSONResponse(
        content=manifest,
        headers={"Content-Disposition": 'attachment; filename="music_manifest.json"'},
    )


@router.post(
    "/jobs/{job_id}/narration",
    response_model=NarrationMixResponse,
    operation_id="createNarrationMix",
)
async def create_narration_mix(job_id: str, request: Request) -> NarrationMixResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": "The requested job does not exist."},
        )
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MUSIC_AUDIO_NOT_AVAILABLE",
                "message": "Narration mixing requires a successfully generated music file.",
            },
        )
    audio_format = NARRATION_CONTENT_TYPES.get(
        request.headers.get("content-type", "").split(";", 1)[0].lower()
    )
    if audio_format is None:
        raise HTTPException(
            status_code=415,
            detail={
                "code": "NARRATION_FORMAT_UNSUPPORTED",
                "message": "Upload a WAV or MP3 narration file.",
                "details": {"supported_content_types": sorted(NARRATION_CONTENT_TYPES)},
            },
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "NARRATION_SIZE_INVALID", "message": "The upload size is invalid."},
            ) from exc
        if declared_size < 0:
            raise HTTPException(
                status_code=400,
                detail={"code": "NARRATION_SIZE_INVALID", "message": "The upload size is invalid."},
            )
        if declared_size > settings.max_narration_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "NARRATION_TOO_LARGE",
                    "message": "The narration file exceeds the configured upload limit.",
                    "details": {"max_bytes": settings.max_narration_bytes},
                },
            )

    narration = bytearray()
    async for chunk in request.stream():
        narration.extend(chunk)
        if len(narration) > settings.max_narration_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "NARRATION_TOO_LARGE",
                    "message": "The narration file exceeds the configured upload limit.",
                    "details": {"max_bytes": settings.max_narration_bytes},
                },
            )
    if not narration:
        raise HTTPException(
            status_code=422,
            detail={"code": "NARRATION_MISSING", "message": "Upload a non-empty narration audio file."},
        )

    saved_format: str | None = None
    try:
        music_path = audio_storage.path_for(job_id, job.result.format)
        if not music_path.is_file():
            raise HTTPException(
                status_code=404,
                detail={"code": "AUDIO_FILE_NOT_FOUND", "message": "The generated music file is no longer available."},
            )
        processed = mix_with_narration(
            music_path.read_bytes(),
            job.result.format,
            bytes(narration),
            audio_format,
            target_lufs=job.request.output.target_lufs,
            output_format=job.result.format,
        )
        output_path = audio_storage.save_variant(
            job_id,
            "narration-mix",
            processed.format,
            processed.content,
        )
        saved_format = processed.format
        saved_metadata = inspect_audio_file(output_path, processed.format)
        if (
            abs(saved_metadata.duration_s - processed.duration_s) > 0.05
            or saved_metadata.sample_rate != processed.sample_rate
            or saved_metadata.channel_count != processed.channel_count
        ):
            raise AudioProcessingError(
                "NARRATION_OUTPUT_VALIDATION_FAILED",
                "The saved narration mix did not match its validated audio metadata.",
            )
        result = NarrationMixResponse(
            job_id=job_id,
            format=processed.format,
            duration_s=processed.duration_s,
            sample_rate=processed.sample_rate,
            channel_count=processed.channel_count,
            size_bytes=len(processed.content),
            duck_attenuation_db=12.0,
            integrated_lufs=processed.integrated_lufs,
            true_peak_dbtp=processed.true_peak_dbtp,
            download_url=f"/api/v1/jobs/{job_id}/narration/audio",
            warnings=processed.warnings,
        )
        audio_storage.save_narration_manifest(
            job_id,
            result.model_dump(mode="json"),
        )
        if not output_path.is_file():
            raise OSError("Narration mix output was not persisted.")
        return result
    except AudioProcessingError as exc:
        _cleanup_narration_mix(job_id, saved_format)
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except AudioValidationError as exc:
        _cleanup_narration_mix(job_id, saved_format)
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NARRATION_OUTPUT_VALIDATION_FAILED",
                "message": "The saved narration mix could not be validated.",
            },
        ) from exc
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        _cleanup_narration_mix(job_id, saved_format)
        logger.exception("Narration mix could not be saved", extra={"job_id": job_id})
        raise HTTPException(
            status_code=500,
            detail={"code": "NARRATION_MIX_STORAGE_FAILED", "message": "The narration mix could not be saved."},
        ) from exc


def _cleanup_narration_mix(job_id: str, audio_format: str | None) -> None:
    try:
        if audio_format is not None:
            audio_storage.delete_variant(job_id, "narration-mix", audio_format)
        audio_storage.narration_manifest_path_for(job_id).unlink(missing_ok=True)
    except OSError:
        logger.exception("Failed to clean narration mix assets", extra={"job_id": job_id})


@router.get(
    "/jobs/{job_id}/narration",
    response_model=NarrationMixResponse,
    operation_id="getNarrationMix",
)
def get_narration_mix(job_id: str) -> NarrationMixResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": "The requested job does not exist."},
        )
    try:
        mix_manifest_path = audio_storage.narration_manifest_path_for(job_id)
        return NarrationMixResponse.model_validate_json(mix_manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NARRATION_MIX_NOT_FOUND", "message": "No narration mix is available for this job."},
        ) from exc
    except (OSError, ValueError) as exc:
        logger.exception("Stored narration mix metadata could not be read", extra={"job_id": job_id})
        raise HTTPException(
            status_code=500,
            detail={"code": "NARRATION_MIX_READ_FAILED", "message": "Narration mix metadata could not be read."},
        ) from exc


@router.get("/jobs/{job_id}/narration/audio", operation_id="downloadNarrationMix")
def download_narration_mix(job_id: str) -> FileResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": "The requested job does not exist."},
        )
    try:
        manifest_path = audio_storage.narration_manifest_path_for(job_id)
        manifest = NarrationMixResponse.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        mix_path = audio_storage.variant_path_for(job_id, "narration-mix", manifest.format)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "NARRATION_MIX_NOT_FOUND", "message": "No narration mix is available for this job."},
        ) from exc
    if not mix_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "NARRATION_MIX_NOT_FOUND", "message": "The narration mix file is no longer available."},
        )
    media_type = "audio/mpeg" if manifest.format == "mp3" else "audio/wav"
    return FileResponse(
        path=mix_path,
        media_type=media_type,
        filename=f"{job_id}.narration-mix.{manifest.format}",
    )


@router.post(
    "/music/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    operation_id="createMusicJob",
)
def create_music_job(
    payload: JobCreateRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobAccepted:
    for url in (payload.narration_url, payload.callback_url):
        if url is not None:
            try:
                validate_outbound_url(str(url), settings.outbound_url_allowed_hosts)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "OUTBOUND_URL_NOT_ALLOWED",
                        "message": (
                            "Narration and callback URLs must use HTTPS, resolve to public "
                            "addresses, and match an OUTBOUND_URL_ALLOWED_HOSTS entry."
                        ),
                    },
                ) from exc

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > settings.max_idempotency_key_length:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_IDEMPOTENCY_KEY",
                    "message": "Idempotency-Key must be non-empty and within the configured length limit.",
                    "details": {"max_length": settings.max_idempotency_key_length},
                },
            )

    try:
        job, created = job_store.create(payload, idempotency_key)
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEMPOTENCY_KEY_CONFLICT",
                "message": "This Idempotency-Key was already used with a different request.",
            },
        ) from exc

    if created:
        background_tasks.add_task(job_manager.process_job, job.job_id)
        logger.info("Music job accepted", extra={"job_id": job.job_id})
    return JobAccepted(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        created_at=job.created_at,
        updated_at=job.updated_at,
        message="Music generation job accepted for processing.",
    )
