from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol
from uuid import uuid4

from app.schemas.music import AudioResult, JobCreateRequest, JobError, JobStatus


@dataclass
class JobRecord:
    job_id: str
    request: JobCreateRequest
    status: JobStatus
    progress: int
    created_at: datetime
    updated_at: datetime
    error: JobError | None = None
    result: AudioResult | None = None
    warnings: list[str] | None = None


class IdempotencyConflict(Exception):
    """Raised when a key is reused for a different request body."""


class JobStore(Protocol):
    def create(self, request: JobCreateRequest, idempotency_key: str | None) -> tuple[JobRecord, bool]: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def list_recent(self, limit: int = 50) -> list[JobRecord]: ...

    def update(self, job_id: str, **changes: object) -> JobRecord | None: ...

    def check_ready(self) -> bool: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._lock = RLock()

    def create(self, request: JobCreateRequest, idempotency_key: str | None) -> tuple[JobRecord, bool]:
        request_data = request.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self._lock:
            if idempotency_key:
                previous = self._idempotency.get(idempotency_key)
                if previous:
                    previous_hash, previous_job_id = previous
                    if previous_hash != request_hash:
                        raise IdempotencyConflict
                    return self._copy(self._jobs[previous_job_id]), False

            now = datetime.now(timezone.utc)
            job = JobRecord(
                job_id=str(uuid4()),
                request=request,
                status=JobStatus.queued,
                progress=0,
                created_at=now,
                updated_at=now,
                warnings=[],
            )
            self._jobs[job.job_id] = job
            if idempotency_key:
                self._idempotency[idempotency_key] = (request_hash, job.job_id)
            return self._copy(job), True

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._copy(job) if job else None

    def list_recent(self, limit: int = 50) -> list[JobRecord]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda job: job.created_at,
                reverse=True,
            )
            return [self._copy(job) for job in jobs[:limit]]

    def update(self, job_id: str, **changes: object) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            new_status = changes.get("status", job.status)
            allowed_transitions = {
                JobStatus.queued: {JobStatus.queued, JobStatus.running, JobStatus.failed},
                JobStatus.running: {JobStatus.running, JobStatus.succeeded, JobStatus.failed},
                JobStatus.succeeded: {JobStatus.succeeded},
                JobStatus.failed: {JobStatus.failed},
            }
            if new_status not in allowed_transitions[job.status]:
                raise ValueError(f"Invalid job status transition: {job.status} -> {new_status}")
            new_progress = changes.get("progress", job.progress)
            if not isinstance(new_progress, int) or not 0 <= new_progress <= 100:
                raise ValueError("Job progress must be an integer between 0 and 100.")
            if new_progress < job.progress:
                raise ValueError("Job progress cannot decrease.")
            if new_status == JobStatus.succeeded and not changes.get("result", job.result):
                raise ValueError("A job cannot succeed without result metadata.")
            if new_status == JobStatus.succeeded and new_progress != 100:
                raise ValueError("A succeeded job must have 100 percent progress.")
            for field, value in changes.items():
                setattr(job, field, value)
            job.updated_at = datetime.now(timezone.utc)
            return self._copy(job)

    def check_ready(self) -> bool:
        with self._lock:
            return isinstance(self._jobs, dict)

    @staticmethod
    def _copy(job: JobRecord) -> JobRecord:
        return JobRecord(
            job_id=job.job_id,
            request=job.request.model_copy(deep=True),
            status=job.status,
            progress=job.progress,
            created_at=job.created_at,
            updated_at=job.updated_at,
            error=job.error.model_copy(deep=True) if job.error else None,
            result=job.result.model_copy(deep=True) if job.result else None,
            warnings=list(job.warnings or []),
        )


job_store: JobStore = InMemoryJobStore()
