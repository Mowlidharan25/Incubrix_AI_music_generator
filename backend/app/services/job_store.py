from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    owner_id: str = "local"
    error: JobError | None = None
    result: AudioResult | None = None
    warnings: list[str] | None = None


class IdempotencyConflict(Exception):
    """Raised when a key is reused for a different request body."""


class GenerationQuotaExceeded(Exception):
    """Raised when the configured daily generation quota has been exhausted."""


class GenerationConcurrencyExceeded(Exception):
    """Raised when the configured active-generation limit has been reached."""


class JobStore(Protocol):
    def create(
        self,
        request: JobCreateRequest,
        idempotency_key: str | None,
        owner_id: str = "local",
    ) -> tuple[JobRecord, bool]: ...

    def get(self, job_id: str, owner_id: str | None = None) -> JobRecord | None: ...

    def list_recent(self, limit: int = 50, owner_id: str | None = None) -> list[JobRecord]: ...

    def update(self, job_id: str, **changes: object) -> JobRecord | None: ...

    def check_ready(self) -> bool: ...

    def consume_rate_limit(self, key: str, limit: int, window_seconds: int, now: int) -> bool: ...


class InMemoryJobStore:
    def __init__(
        self,
        max_active_generations: int = 1,
        max_generations_per_day: int = 5,
    ) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._rate_limits: dict[str, tuple[int, int]] = {}
        self.max_active_generations = max_active_generations
        self.max_generations_per_day = max_generations_per_day
        self._lock = RLock()

    def create(
        self,
        request: JobCreateRequest,
        idempotency_key: str | None,
        owner_id: str = "local",
    ) -> tuple[JobRecord, bool]:
        request_data = request.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self._lock:
            if idempotency_key:
                previous = self._idempotency.get((owner_id, idempotency_key))
                if previous:
                    previous_hash, previous_job_id = previous
                    if previous_hash != request_hash:
                        raise IdempotencyConflict
                    existing = self._jobs.get(previous_job_id)
                    if existing:
                        return self._copy(existing), False

            today = datetime.now(timezone.utc).date()
            todays_jobs = [
                job
                for job in self._jobs.values()
                if job.owner_id == owner_id and job.created_at.date() == today
            ]
            if self.max_generations_per_day and len(todays_jobs) >= self.max_generations_per_day:
                raise GenerationQuotaExceeded
            if sum(job.status in {JobStatus.queued, JobStatus.running} for job in todays_jobs) >= self.max_active_generations:
                raise GenerationConcurrencyExceeded

            now = datetime.now(timezone.utc)
            job = JobRecord(
                job_id=str(uuid4()),
                request=request,
                status=JobStatus.queued,
                progress=0,
                created_at=now,
                updated_at=now,
                owner_id=owner_id,
                warnings=[],
            )
            self._jobs[job.job_id] = job
            if idempotency_key:
                self._idempotency[(owner_id, idempotency_key)] = (request_hash, job.job_id)
            return self._copy(job), True

    def get(self, job_id: str, owner_id: str | None = None) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._copy(job) if job and (owner_id is None or job.owner_id == owner_id) else None

    def list_recent(self, limit: int = 50, owner_id: str | None = None) -> list[JobRecord]:
        with self._lock:
            jobs = sorted(
                (job for job in self._jobs.values() if owner_id is None or job.owner_id == owner_id),
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

    def consume_rate_limit(self, key: str, limit: int, window_seconds: int, now: int) -> bool:
        with self._lock:
            window_start = now - now % window_seconds
            previous_start, previous_count = self._rate_limits.get(key, (window_start, 0))
            count = previous_count + 1 if previous_start == window_start else 1
            self._rate_limits[key] = (window_start, count)
            return count <= limit

    @staticmethod
    def _copy(job: JobRecord) -> JobRecord:
        return JobRecord(
            job_id=job.job_id,
            request=job.request.model_copy(deep=True),
            status=job.status,
            progress=job.progress,
            created_at=job.created_at,
            updated_at=job.updated_at,
            owner_id=job.owner_id,
            error=job.error.model_copy(deep=True) if job.error else None,
            result=job.result.model_copy(deep=True) if job.result else None,
            warnings=list(job.warnings or []),
        )


class SQLiteJobStore:
    def __init__(
        self,
        database_path: Path | str,
        max_active_generations: int = 1,
        max_generations_per_day: int = 5,
    ) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_absolute():
            self.database_path = Path(__file__).resolve().parents[2] / self.database_path
        self.max_active_generations = max_active_generations
        self.max_generations_per_day = max_generations_per_day
        self._lock = RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._secure_database_files()
        self._fail_interrupted_jobs()
        self._secure_database_files()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        self._secure_database_files()
        return connection

    def _secure_database_files(self) -> None:
        if platform.system() == "Windows":
            return
        for candidate in (
            self.database_path,
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
        ):
            if candidate.exists():
                candidate.chmod(0o600)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_json TEXT,
                    result_json TEXT,
                    warnings_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_owner_created
                    ON jobs(owner_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    PRIMARY KEY(owner_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS rate_limits (
                    rate_key TEXT PRIMARY KEY,
                    window_start INTEGER NOT NULL,
                    request_count INTEGER NOT NULL
                );
                """
            )

    def _fail_interrupted_jobs(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        error = JobError(
            code="JOB_INTERRUPTED",
            message="The server restarted before this job completed. Submit a new request if needed.",
            retryable=False,
            details={"stage": "restart_recovery"},
        ).model_dump_json()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, error_json = ?
                WHERE status IN (?, ?)
                """,
                (
                    JobStatus.failed.value,
                    now,
                    error,
                    JobStatus.queued.value,
                    JobStatus.running.value,
                ),
            )
            connection.commit()

    def create(
        self,
        request: JobCreateRequest,
        idempotency_key: str | None,
        owner_id: str = "local",
    ) -> tuple[JobRecord, bool]:
        request_json = request.model_dump_json()
        request_hash = hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                previous = connection.execute(
                    "SELECT request_hash, job_id FROM idempotency_keys WHERE owner_id = ? AND idempotency_key = ?",
                    (owner_id, idempotency_key),
                ).fetchone()
                if previous:
                    if previous["request_hash"] != request_hash:
                        connection.rollback()
                        raise IdempotencyConflict
                    existing = connection.execute(
                        "SELECT * FROM jobs WHERE job_id = ?", (previous["job_id"],)
                    ).fetchone()
                    if existing:
                        connection.commit()
                        return self._from_row(existing), False

            if self.max_generations_per_day:
                count = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE owner_id = ? AND created_at >= ?",
                    (owner_id, today_start),
                ).fetchone()[0]
                if count >= self.max_generations_per_day:
                    connection.rollback()
                    raise GenerationQuotaExceeded
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE owner_id = ? AND status IN (?, ?)",
                (owner_id, JobStatus.queued.value, JobStatus.running.value),
            ).fetchone()[0]
            if active >= self.max_active_generations:
                connection.rollback()
                raise GenerationConcurrencyExceeded

            job = JobRecord(
                job_id=str(uuid4()),
                request=request,
                status=JobStatus.queued,
                progress=0,
                created_at=now,
                updated_at=now,
                owner_id=owner_id,
                warnings=[],
            )
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, owner_id, request_json, status, progress,
                    created_at, updated_at, error_json, result_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    job.job_id,
                    owner_id,
                    request_json,
                    job.status.value,
                    job.progress,
                    now.isoformat(),
                    now.isoformat(),
                    "[]",
                ),
            )
            if idempotency_key:
                connection.execute(
                    "INSERT INTO idempotency_keys(owner_id, idempotency_key, request_hash, job_id) VALUES (?, ?, ?, ?)",
                    (owner_id, idempotency_key, request_hash, job.job_id),
                )
            connection.commit()
            return job, True

    def get(self, job_id: str, owner_id: str | None = None) -> JobRecord | None:
        with closing(self._connect()) as connection:
            if owner_id is None:
                row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ? AND owner_id = ?", (job_id, owner_id)
                ).fetchone()
        return self._from_row(row) if row else None

    def list_recent(self, limit: int = 50, owner_id: str | None = None) -> list[JobRecord]:
        limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            if owner_id is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?",
                    (owner_id, limit),
                ).fetchall()
        return [self._from_row(row) for row in rows]

    def update(self, job_id: str, **changes: object) -> JobRecord | None:
        permitted = {"status", "progress", "error", "result", "warnings"}
        if changes.keys() - permitted:
            raise ValueError("Unsupported job update field.")
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                connection.rollback()
                return None
            job = self._from_row(row)
            new_status = changes.get("status", job.status)
            allowed_transitions = {
                JobStatus.queued: {JobStatus.queued, JobStatus.running, JobStatus.failed},
                JobStatus.running: {JobStatus.running, JobStatus.succeeded, JobStatus.failed},
                JobStatus.succeeded: {JobStatus.succeeded},
                JobStatus.failed: {JobStatus.failed},
            }
            if new_status not in allowed_transitions[job.status]:
                connection.rollback()
                raise ValueError(f"Invalid job status transition: {job.status} -> {new_status}")
            new_progress = changes.get("progress", job.progress)
            if not isinstance(new_progress, int) or not 0 <= new_progress <= 100:
                connection.rollback()
                raise ValueError("Job progress must be an integer between 0 and 100.")
            if new_progress < job.progress:
                connection.rollback()
                raise ValueError("Job progress cannot decrease.")
            if new_status == JobStatus.succeeded and not changes.get("result", job.result):
                connection.rollback()
                raise ValueError("A job cannot succeed without result metadata.")
            if new_status == JobStatus.succeeded and new_progress != 100:
                connection.rollback()
                raise ValueError("A succeeded job must have 100 percent progress.")

            error = changes.get("error", job.error)
            result = changes.get("result", job.result)
            warnings = changes.get("warnings", job.warnings)
            updated_at = datetime.now(timezone.utc)
            connection.execute(
                """
                UPDATE jobs SET status = ?, progress = ?, updated_at = ?,
                    error_json = ?, result_json = ?, warnings_json = ?
                WHERE job_id = ?
                """,
                (
                    new_status.value,
                    new_progress,
                    updated_at.isoformat(),
                    error.model_dump_json() if isinstance(error, JobError) else None,
                    result.model_dump_json() if isinstance(result, AudioResult) else None,
                    json.dumps(warnings or []),
                    job_id,
                ),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        return self._from_row(updated)

    def delete(self, job_id: str, owner_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE job_id = ? AND owner_id = ?", (job_id, owner_id)
            )
            connection.commit()
            return cursor.rowcount > 0

    def consume_rate_limit(self, key: str, limit: int, window_seconds: int, now: int) -> bool:
        window_start = now - now % window_seconds
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM rate_limits WHERE window_start < ?",
                (window_start - 2 * window_seconds,),
            )
            row = connection.execute(
                "SELECT window_start, request_count FROM rate_limits WHERE rate_key = ?", (key,)
            ).fetchone()
            count = row["request_count"] + 1 if row and row["window_start"] == window_start else 1
            connection.execute(
                """
                INSERT INTO rate_limits(rate_key, window_start, request_count) VALUES (?, ?, ?)
                ON CONFLICT(rate_key) DO UPDATE SET window_start = excluded.window_start,
                    request_count = excluded.request_count
                """,
                (key, window_start, count),
            )
            connection.commit()
            return count <= limit

    def check_ready(self) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("SELECT 1").fetchone()
            result = connection.execute("PRAGMA quick_check").fetchone()
        return bool(result and result[0] == "ok")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            request=JobCreateRequest.model_validate_json(row["request_json"]),
            status=JobStatus(row["status"]),
            progress=row["progress"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            owner_id=row["owner_id"],
            error=JobError.model_validate_json(row["error_json"]) if row["error_json"] else None,
            result=AudioResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
            warnings=json.loads(row["warnings_json"]),
        )


from app.core.config import settings

job_store: JobStore = SQLiteJobStore(
    settings.job_database_path,
    max_active_generations=settings.max_active_generations,
    max_generations_per_day=settings.max_generations_per_day,
)
