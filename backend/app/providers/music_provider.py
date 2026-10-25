from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.schemas.music import JobCreateRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedAudio:
    content: bytes
    provider: str
    model: str
    format: str
    is_ai_generated: bool
    warnings: list[str]


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class MusicProvider:
    name = "abstract"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def generate(self, _request: JobCreateRequest, _job_id: str) -> GeneratedAudio:
        del _request, _job_id
        raise NotImplementedError


class ElevenLabsMusicProvider(MusicProvider):
    name = "elevenlabs"
    endpoint = "https://api.elevenlabs.io/v1/music"

    def __init__(
        self,
        api_key: str | None,
        model_id: str = "music_v2_5",
        timeout_seconds: float = 300,
        max_audio_bytes: int = 100_000_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self.max_audio_bytes = max_audio_bytes
        self._transport = transport

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: JobCreateRequest, job_id: str) -> GeneratedAudio:
        if not self._api_key:
            raise ProviderError(
                "PROVIDER_NOT_CONFIGURED",
                "Set ELEVENLABS_API_KEY in the backend environment to enable music generation.",
                details={"provider": self.name},
            )
        if "mp3" not in request.output.formats:
            raise ProviderError(
                "UNSUPPORTED_OUTPUT_FORMAT",
                "The ElevenLabs integration currently supports MP3 output only.",
                details={"supported_formats": ["mp3"], "requested_formats": request.output.formats},
            )

        warnings: list[str] = []
        if "wav" in request.output.formats:
            warnings.append(
                "WAV was requested, but this integration currently stores the genuine provider MP3 only."
            )
        if request.seed is not None:
            warnings.append(
                "The requested seed was not applied because the provider API does not allow a seed with prompt-based generation."
            )

        sample_rate = request.output.sample_rate
        provider_sample_rate = sample_rate if sample_rate in (44100, 48000) else 48000
        if provider_sample_rate != sample_rate:
            warnings.append(
                f"The provider does not offer MP3 at {sample_rate} Hz; it generated {provider_sample_rate} Hz instead."
            )
        output_format = f"mp3_{provider_sample_rate}_192"
        prompt = self._compose_prompt(request)
        payload = {
            "prompt": prompt,
            "music_length_ms": int(request.duration_s * 1000),
            "model_id": self.model_id,
            "force_instrumental": True,
        }
        timeout = httpx.Timeout(
            self.timeout_seconds,
            connect=min(10.0, self.timeout_seconds),
        )
        content = self._request_audio(payload, output_format, timeout, job_id)
        return GeneratedAudio(
            content=content,
            provider=self.name,
            model=self.model_id,
            format="mp3",
            is_ai_generated=True,
            warnings=warnings,
        )

    @staticmethod
    def _compose_prompt(request: JobCreateRequest) -> str:
        prompt = request.prompt
        if request.tempo_bpm is not None:
            prompt += f"\nTempo target: approximately {request.tempo_bpm} BPM."
        if request.key:
            prompt += f"\nMusical key target: {request.key}."
        if request.loopable:
            prompt += "\nPrefer a seamless loop ending."
        if request.sections:
            timeline = "; ".join(
                f"{section.start_s:g}s: {section.mood}" for section in request.sections
            )
            prompt += f"\nArrangement timeline: {timeline}."
        prompt += "\nInstrumental only; no vocals, singing, speech, or lyrics."
        return prompt

    def _request_audio(
        self,
        payload: dict[str, object],
        output_format: str,
        timeout: httpx.Timeout,
        job_id: str,
    ) -> bytes:
        headers = {"xi-api-key": self._api_key or ""}
        params = {"output_format": output_format}
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=False,
                    transport=self._transport,
                ) as client:
                    with client.stream(
                        "POST",
                        self.endpoint,
                        params=params,
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code != 200:
                            raise self._provider_status_error(response.status_code)
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if content_type not in {"audio/mpeg", "application/octet-stream"}:
                            raise ProviderError(
                                "PROVIDER_INVALID_RESPONSE",
                                "The music provider returned an unexpected content type.",
                                details={"provider": self.name, "content_type": content_type[:80]},
                            )
                        content_length = response.headers.get("content-length")
                        declared_size: int | None = None
                        if content_length:
                            try:
                                declared_size = int(content_length)
                            except ValueError as exc:
                                raise ProviderError(
                                    "PROVIDER_INVALID_RESPONSE",
                                    "The music provider returned an invalid content length.",
                                    details={"provider": self.name},
                                ) from exc
                            if declared_size < 0:
                                raise ProviderError(
                                    "PROVIDER_INVALID_RESPONSE",
                                    "The music provider returned an invalid content length.",
                                    details={"provider": self.name},
                                )
                            if declared_size > self.max_audio_bytes:
                                raise ProviderError(
                                    "PROVIDER_RESPONSE_TOO_LARGE",
                                    "The generated audio exceeds the configured storage limit.",
                                    details={"max_bytes": self.max_audio_bytes},
                                )
                        audio = bytearray()
                        for chunk in response.iter_bytes():
                            audio.extend(chunk)
                            if len(audio) > self.max_audio_bytes:
                                raise ProviderError(
                                    "PROVIDER_RESPONSE_TOO_LARGE",
                                    "The generated audio exceeds the configured storage limit.",
                                    details={"max_bytes": self.max_audio_bytes},
                                )
                        if not audio:
                            raise ProviderError(
                                "PROVIDER_EMPTY_RESPONSE",
                                "The provider returned an empty audio response.",
                                details={"provider": self.name},
                            )
                        if declared_size is not None and len(audio) != declared_size:
                            raise ProviderError(
                                "PROVIDER_INCOMPLETE_RESPONSE",
                                "The provider audio response was incomplete.",
                                details={
                                    "provider": self.name,
                                    "expected_bytes": declared_size,
                                    "received_bytes": len(audio),
                                },
                            )
                        logger.info(
                            "Music provider returned audio",
                            extra={
                                "job_id": job_id,
                                "provider": self.name,
                                "model": self.model_id,
                                "size_bytes": len(audio),
                            },
                        )
                        return bytes(audio)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt == 0:
                    logger.warning(
                        "Safe provider connection retry",
                        extra={"job_id": job_id, "provider": self.name, "attempt": attempt + 1},
                    )
                    time.sleep(0.5)
                    continue
                raise ProviderError(
                    "PROVIDER_CONNECTION_FAILED",
                    "Could not establish a connection to the music provider.",
                    retryable=True,
                    details={"provider": self.name},
                ) from exc
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    "PROVIDER_TIMEOUT",
                    "The music provider did not respond before the configured timeout.",
                    details={"provider": self.name, "timeout_seconds": self.timeout_seconds},
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    "PROVIDER_CONNECTION_FAILED",
                    "The request to the music provider failed before audio was received.",
                    details={"provider": self.name},
                ) from exc
        raise AssertionError("Provider connection retry loop ended unexpectedly.")

    def _provider_status_error(self, status_code: int) -> ProviderError:
        if status_code in (401, 403):
            code, message = (
                "PROVIDER_AUTHENTICATION_FAILED",
                "The music provider rejected the configured API credentials or account access.",
            )
        elif status_code == 429:
            code, message = (
                "PROVIDER_RATE_LIMITED",
                "The music provider rate-limited the request; retry after its rate limit resets.",
            )
        elif status_code in (400, 422):
            code, message = (
                "PROVIDER_REJECTED_REQUEST",
                "The music provider rejected this generation request.",
            )
        elif status_code >= 500:
            code, message = (
                "PROVIDER_UNAVAILABLE",
                "The music provider is temporarily unavailable.",
            )
        else:
            code, message = (
                "PROVIDER_REQUEST_FAILED",
                "The music provider could not complete the generation request.",
            )
        return ProviderError(
            code,
            message,
            retryable=status_code == 429,
            details={"provider": self.name, "http_status": status_code},
        )
