from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class MusicSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_s: float = Field(ge=0, le=300)
    mood: str = Field(min_length=1, max_length=80)

    @field_validator("mood")
    @classmethod
    def validate_mood(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Mood cannot be blank.")
        return normalized


class OutputFormat(str, Enum):
    wav = "wav"
    mp3 = "mp3"


class MusicOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_rate: Literal[48000] = 48000
    target_lufs: float = Field(default=-16.0, ge=-30, le=-4)
    formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.wav, OutputFormat.mp3], min_length=1)


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)
    duration_s: float = Field(ge=5, le=300)
    sections: list[MusicSection] = Field(default_factory=list, max_length=32)
    tempo_bpm: int | None = Field(default=None, ge=40, le=240)
    key: str | None = Field(default=None, max_length=8)
    loopable: bool = False
    narration_url: HttpUrl | None = None
    callback_url: HttpUrl | None = None
    separate_stems: bool = False
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    output: MusicOutputConfig = Field(default_factory=MusicOutputConfig)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Prompt cannot be blank.")
        return normalized

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        valid_keys = {
            "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb",
            "G", "G#", "Ab", "A", "A#", "Bb", "B",
        }
        valid_keys |= {f"{note}m" for note in valid_keys}
        if normalized not in valid_keys:
            raise ValueError("Key must be a supported musical key, such as C or Am.")
        return normalized

    @model_validator(mode="after")
    def validate_section_bounds(self) -> JobCreateRequest:
        previous_start = -1.0
        for section in self.sections:
            if section.start_s >= self.duration_s:
                raise ValueError("Section timestamps must be less than the overall duration.")
            if section.start_s < previous_start:
                raise ValueError("Sections must be ordered by increasing start time.")
            previous_start = section.start_s
        return self


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class StemArtifact(BaseModel):
    stem_id: str
    label: str
    prompt: str
    format: Literal["wav"]
    duration_s: float
    sample_rate: int
    channel_count: int
    size_bytes: int
    file_name: str
    gain_linear: float
    gain_db: float
    download_url: str


class StemProcessingResult(BaseModel):
    status: Literal["not_requested", "available", "unavailable", "failed"]
    error: JobError | None = None
    model_id: str | None = None
    model_version: str | None = None
    method: str | None = None
    stems: list[StemArtifact] = Field(default_factory=list)
    reconstruction_relative_error_db: float | None = None
    reconstruction_metric: str | None = None
    reconstruction_note: str | None = None


class LoopValidation(BaseModel):
    status: Literal["not_requested", "passed", "failed"]
    crossfade_duration_s: float | None = None
    endpoint_discontinuity_dbfs: float | None = None
    note: str


class AudioResult(BaseModel):
    provider: str
    model: str
    format: str
    requested_duration_s: float
    requested_sample_rate: int
    target_integrated_lufs: float
    true_peak_ceiling_dbtp: float
    sample_rate: int
    channel_count: int | None = None
    duration_s: float
    size_bytes: int
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    is_ai_generated: bool
    download_url: str
    mix_ducked_download_url: str | None = None
    manifest_download_url: str | None = None
    loop_validation: LoopValidation = Field(
        default_factory=lambda: LoopValidation(
            status="not_requested",
            note="Loop processing was not requested.",
        )
    )
    stem_processing: StemProcessingResult = Field(
        default_factory=lambda: StemProcessingResult(status="not_requested")
    )
    generation_started_at: datetime
    generation_completed_at: datetime
    processing_duration_s: float = Field(ge=0)


class NarrationMixResponse(BaseModel):
    job_id: str
    format: str
    duration_s: float
    sample_rate: int
    channel_count: int
    size_bytes: int
    duck_attenuation_db: float
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    download_url: str
    warnings: list[str] = Field(default_factory=list)


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: JobError | None = None
    result: AudioResult | None = None
    warnings: list[str] = Field(default_factory=list)
    prompt: str
    duration_s: float


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    progress: int
    created_at: datetime
    updated_at: datetime
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
