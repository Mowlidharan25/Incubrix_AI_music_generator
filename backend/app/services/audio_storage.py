from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioMetadata:
    duration_s: float
    sample_rate: int | None
    channel_count: int | None
    format: str


class AudioValidationError(Exception):
    pass


class AudioStorageError(OSError):
    pass


def inspect_audio(content: bytes, expected_format: str) -> AudioMetadata:
    return _audio_metadata(content, expected_format)


def inspect_audio_file(path: Path, expected_format: str) -> AudioMetadata:
    return _audio_metadata(path, expected_format)


def _audio_metadata(audio_source: bytes | Path, expected_format: str) -> AudioMetadata:
    from io import BytesIO

    from mutagen import File as MutagenFile

    if expected_format not in {"mp3", "wav"}:
        raise AudioValidationError("Unsupported audio format.")
    source = BytesIO(audio_source) if isinstance(audio_source, bytes) else audio_source
    try:
        audio = MutagenFile(source)
    except Exception as exc:
        raise AudioValidationError("Generated audio could not be parsed.") from exc
    if audio is None or audio.info is None:
        raise AudioValidationError("Generated response is not a supported audio file.")

    actual_format = "mp3" if audio.__class__.__name__ == "MP3" else "wav" if audio.__class__.__name__ == "WAVE" else None
    if actual_format != expected_format:
        raise AudioValidationError("Generated audio encoding does not match its reported format.")
    duration_s = float(audio.info.length)
    if duration_s <= 0:
        raise AudioValidationError("Generated audio has no measurable duration.")
    sample_rate = getattr(audio.info, "sample_rate", None)
    channel_count = getattr(audio.info, "channels", None)
    return AudioMetadata(
        duration_s=duration_s,
        sample_rate=int(sample_rate) if sample_rate else None,
        channel_count=int(channel_count) if channel_count else None,
        format=actual_format,
    )


class AudioStorage:
    STEM_IDS = frozenset({"percussion", "bass", "other", "remaining"})

    def __init__(self, directory: Path) -> None:
        backend_root = Path(__file__).resolve().parents[2]
        self.directory = directory if directory.is_absolute() else backend_root / directory
        self.directory = self.directory.resolve()
        self._s3_client = None
        self._s3_required = bool(settings.s3_endpoint_url or settings.s3_bucket_name)
        if settings.s3_endpoint_url and settings.s3_bucket_name:
            if not settings.s3_access_key or not settings.s3_secret_key:
                logger.error(
                    "S3 mirroring is configured but S3_ACCESS_KEY or S3_SECRET_KEY is missing"
                )
                return
            try:
                import boto3
                from botocore.config import Config

                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=settings.s3_endpoint_url,
                    aws_access_key_id=settings.s3_access_key,
                    aws_secret_access_key=settings.s3_secret_key,
                    region_name=settings.s3_region or "us-east-1",
                    config=Config(
                        signature_version="s3v4",
                        connect_timeout=2,
                        read_timeout=2,
                        retries={"max_attempts": 1},
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Could not initialize S3/MinIO client",
                    extra={"failure_type": type(exc).__name__},
                )

    def check_ready(self) -> bool:
        if self._s3_required:
            if self._s3_client is None or not settings.s3_bucket_name:
                return False
            try:
                self._s3_client.head_bucket(Bucket=settings.s3_bucket_name)
                return True
            except Exception as exc:
                logger.warning(
                    "Configured object storage readiness check failed",
                    extra={"failure_type": type(exc).__name__},
                )
                return False
        directory = self.directory
        while not directory.exists() and directory != directory.parent:
            directory = directory.parent
        return directory.is_dir() and os.access(directory, os.W_OK)

    def _mirror_to_s3(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        if self._s3_client is not None and settings.s3_bucket_name:
            try:
                self._s3_client.upload_file(
                    str(path),
                    settings.s3_bucket_name,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
            except Exception as exc:
                logger.exception(
                    "Configured object storage upload failed",
                    extra={"object_key": key, "failure_type": type(exc).__name__},
                )
                raise AudioStorageError(
                    "Generated audio could not be stored in the configured object store."
                ) from exc
        elif self._s3_required:
            raise AudioStorageError(
                "Configured object storage is unavailable or missing credentials."
            )

    def save(self, job_id: str, audio_format: str, content: bytes) -> Path:
        if audio_format not in {"mp3", "wav"}:
            raise ValueError("Unsupported audio file extension.")
        return self._save_path(self.path_for(job_id, audio_format), job_id, content)

    def save_manifest(self, job_id: str, manifest: dict[str, Any]) -> Path:
        return self._save_json_path(self.manifest_path_for(job_id), job_id, manifest)

    def save_source(self, job_id: str, audio_format: str, content: bytes) -> Path:
        if audio_format not in {"mp3", "wav"}:
            raise ValueError("Unsupported source audio file extension.")
        return self._save_path(self.source_path_for(job_id, audio_format), job_id, content)

    def save_narration_manifest(self, job_id: str, manifest: dict[str, Any]) -> Path:
        return self._save_json_path(self.narration_manifest_path_for(job_id), job_id, manifest)

    def delete_narration_manifest(self, job_id: str) -> None:
        self._delete_path(self.narration_manifest_path_for(job_id))

    def _save_json_path(self, target: Path, job_id: str, manifest: dict[str, Any]) -> Path:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{job_id}-",
                suffix=".tmp",
                dir=self.directory,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(manifest, temporary, ensure_ascii=True, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            self._mirror_to_s3(target.name, target, "application/json")
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return target

    def delete(self, job_id: str, audio_format: str) -> None:
        self._delete_path(self.path_for(job_id, audio_format))

    def delete_manifest(self, job_id: str) -> None:
        self._delete_path(self.manifest_path_for(job_id))

    def delete_source(self, job_id: str, audio_format: str) -> None:
        self._delete_path(self.source_path_for(job_id, audio_format))

    def save_stem(self, job_id: str, stem_id: str, content: bytes) -> Path:
        return self._save_path(self.stem_path_for(job_id, stem_id), job_id, content)

    def delete_stem(self, job_id: str, stem_id: str) -> None:
        self._delete_path(self.stem_path_for(job_id, stem_id))

    def save_variant(self, job_id: str, variant: str, audio_format: str, content: bytes) -> Path:
        if variant != "narration-mix" or audio_format not in {"mp3", "wav"}:
            raise ValueError("Unsupported generated audio variant.")
        return self._save_path(self.variant_path_for(job_id, variant, audio_format), job_id, content)

    def delete_variant(self, job_id: str, variant: str, audio_format: str) -> None:
        self._delete_path(self.variant_path_for(job_id, variant, audio_format))

    def variant_path_for(self, job_id: str, variant: str, audio_format: str) -> Path:
        if variant != "narration-mix" or audio_format not in {"mp3", "wav"}:
            raise ValueError("Unsupported generated audio variant.")
        self._validate_job_id(job_id)
        return self._validated_path(f"{job_id}.{variant}.{audio_format}")

    def narration_manifest_path_for(self, job_id: str) -> Path:
        self._validate_job_id(job_id)
        return self._validated_path(f"{job_id}.narration-mix.json")

    def manifest_path_for(self, job_id: str) -> Path:
        self._validate_job_id(job_id)
        return self._validated_path(f"{job_id}.manifest.json")

    def source_path_for(self, job_id: str, audio_format: str) -> Path:
        self._validate_job_id(job_id)
        if audio_format not in {"mp3", "wav"}:
            raise ValueError("Invalid provider source audio reference.")
        return self._validated_path(f"{job_id}.source.{audio_format}")

    def stem_path_for(self, job_id: str, stem_id: str) -> Path:
        self._validate_job_id(job_id)
        if stem_id not in self.STEM_IDS:
            raise ValueError("Invalid generated stem reference.")
        return self._validated_path(f"{job_id}.stem.{stem_id}.wav")

    def path_for(self, job_id: str, audio_format: str) -> Path:
        self._validate_job_id(job_id)
        if audio_format not in {"mp3", "wav"}:
            raise ValueError("Invalid generated audio reference.")
        return self._validated_path(f"{job_id}.{audio_format}")

    def _save_path(self, target: Path, job_id: str, content: bytes) -> Path:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{job_id}-",
                suffix=".tmp",
                dir=self.directory,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            media_type = "audio/mpeg" if target.suffix.lower() == ".mp3" else "audio/wav"
            self._mirror_to_s3(target.name, target, media_type)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return target

    def _delete_path(self, target: Path) -> None:
        target.unlink(missing_ok=True)
        if self._s3_client is not None and settings.s3_bucket_name:
            try:
                self._s3_client.delete_object(Bucket=settings.s3_bucket_name, Key=target.name)
            except Exception as exc:
                logger.exception(
                    "Configured object storage cleanup failed",
                    extra={"object_key": target.name, "failure_type": type(exc).__name__},
                )
                raise AudioStorageError(
                    "A stored object could not be removed from the configured object store."
                ) from exc
        elif self._s3_required:
            raise AudioStorageError(
                "Configured object storage is unavailable or missing credentials."
            )

    def _validated_path(self, filename: str) -> Path:
        path = (self.directory / filename).resolve()
        if self.directory not in path.parents:
            raise ValueError("Generated asset path escaped the configured storage directory.")
        return path

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        try:
            if str(UUID(job_id)) != job_id:
                raise ValueError("Invalid job identifier.")
        except ValueError as exc:
            raise ValueError("Invalid generated audio reference.") from exc
