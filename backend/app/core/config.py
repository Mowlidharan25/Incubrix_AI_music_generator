from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "IncuBrix AI Music Studio"
    environment: str = "development"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    max_request_bytes: int = Field(default=65536, ge=1024, le=1048576)
    max_narration_bytes: int = Field(default=50_000_000, ge=1_000_000, le=100_000_000)
    max_idempotency_key_length: int = Field(default=255, ge=1, le=1024)
    auth_username: str | None = Field(default=None, min_length=1, max_length=64)
    auth_password_hash: str | None = None
    auth_session_secret: str | None = None
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    job_database_path: Path = Path("incubrix_jobs.sqlite3")
    max_active_generations: int = Field(default=1, ge=1, le=4)
    max_generations_per_day: int = Field(default=5, ge=1, le=100)
    rate_limit_per_ip_per_minute: int = Field(default=120, ge=1, le=10000)
    rate_limit_per_user_per_minute: int = Field(default=180, ge=1, le=10000)
    login_attempts_per_ip_per_minute: int = Field(default=10, ge=1, le=1000)
    music_provider: Literal["stability", "elevenlabs", "mock"] = "stability"
    stability_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_model_id: Literal["music_v1", "music_v2", "music_v2_5"] = "music_v2_5"
    provider_timeout_seconds: float = Field(default=300, ge=10, le=900)
    max_generated_audio_bytes: int = Field(default=100_000_000, ge=1_000_000, le=500_000_000)
    audio_storage_dir: Path = Path("generated_audio")
    s3_endpoint_url: str | None = None
    s3_bucket_name: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    webhook_secret: str | None = None
    outbound_url_allowed_hosts: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env", str(Path(__file__).resolve().parents[2] / ".env")),
        extra="ignore",
    )


settings = Settings()
