from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote

import httpx

from app.providers.music_provider import GeneratedAudio, MusicProvider, ProviderError
from app.schemas.music import JobCreateRequest

logger = logging.getLogger(__name__)


class StabilityMusicProvider(MusicProvider):
    name = "stability"
    model_id = "stable-audio-3"
    base_url = "https://api.stability.ai"
    generation_endpoint = f"{base_url}/v2beta/audio/stable-audio/text-to-audio"
    results_endpoint = f"{base_url}/v2beta/audio/results"
    generation_id_pattern = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float = 300,
        max_audio_bytes: int = 100_000_000,
        poll_interval_seconds: float = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self.timeout_seconds = timeout_seconds
        self.max_audio_bytes = max_audio_bytes
        self.poll_interval_seconds = poll_interval_seconds
        self._transport = transport

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate(self, request: JobCreateRequest, job_id: str) -> GeneratedAudio:
        if not self._api_key:
            raise ProviderError(
                "PROVIDER_NOT_CONFIGURED",
                "Set STABILITY_API_KEY in the backend environment to enable music generation.",
                details={"provider": self.name},
            )

        output_format = "mp3" if "mp3" in request.output.formats else "wav"
        warnings: list[str] = []
        if len(request.output.formats) > 1:
            other_formats = sorted(set(request.output.formats) - {output_format})
            warnings.append(
                f"Only {output_format.upper()} was generated; requested additional formats "
                f"{', '.join(item.upper() for item in other_formats)} are not converted in this phase."
            )
        if request.output.sample_rate != 44100:
            warnings.append(
                f"Stable Audio returned 44.1 kHz source audio; backend post-processing will target "
                f"{request.output.sample_rate} Hz."
            )

        prompt = self._compose_prompt(request)
        data: dict[str, str | int | float] = {
            "prompt": prompt,
            "model": self.model_id,
            "duration": request.duration_s,
            "output_format": output_format,
        }
        if request.seed is not None:
            data["seed"] = request.seed

        deadline = time.monotonic() + self.timeout_seconds
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "audio/*",
        }
        try:
            with httpx.Client(
                timeout=self._timeout(self.timeout_seconds),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                with client.stream(
                    "POST",
                    self.generation_endpoint,
                    headers=headers,
                    data=data,
                    files={"none": (None, "")},
                ) as response:
                    if response.status_code != 202:
                        raise self._status_error(response.status_code)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type != "application/json" and not content_type.endswith("+json"):
                        raise ProviderError(
                            "PROVIDER_INVALID_RESPONSE",
                            "Stability AI returned an unexpected generation response type.",
                            details={"provider": self.name, "content_type": content_type[:80]},
                        )
                    try:
                        response.read()
                        response_data = response.json()
                    except ValueError as exc:
                        raise ProviderError(
                            "PROVIDER_INVALID_RESPONSE",
                            "Stability AI returned an invalid generation response.",
                            details={"provider": self.name},
                        ) from exc
                    generation_id = (
                        response_data.get("id") if isinstance(response_data, dict) else None
                    )
                    if (
                        not isinstance(generation_id, str)
                        or not self.generation_id_pattern.fullmatch(generation_id)
                    ):
                        raise ProviderError(
                            "PROVIDER_INVALID_RESPONSE",
                            "Stability AI returned an invalid generation identifier.",
                            details={"provider": self.name},
                        )

                audio = self._poll_result(client, generation_id, deadline, job_id)
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                "Stability AI did not complete generation before the configured timeout.",
                details={"provider": self.name, "timeout_seconds": self.timeout_seconds},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "PROVIDER_CONNECTION_FAILED",
                "The request to Stability AI failed before audio was received.",
                retryable=True,
                details={"provider": self.name},
            ) from exc

        logger.info(
            "Music provider returned audio",
            extra={
                "job_id": job_id,
                "provider": self.name,
                "model": self.model_id,
                "format": output_format,
                "size_bytes": len(audio),
            },
        )
        return GeneratedAudio(
            content=audio,
            provider=self.name,
            model=self.model_id,
            format=output_format,
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
        prompt += "\nInstrumental music only; no vocals, singing, speech, or lyrics."
        return prompt

    def _poll_result(
        self,
        client: httpx.Client,
        generation_id: str,
        deadline: float,
        job_id: str,
    ) -> bytes:
        result_url = f"{self.results_endpoint}/{quote(generation_id, safe='')}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "audio/*",
        }
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError(
                    "PROVIDER_TIMEOUT",
                    "Stability AI did not complete generation before the configured timeout.",
                    details={"provider": self.name, "timeout_seconds": self.timeout_seconds},
                )
            with client.stream(
                "GET",
                result_url,
                headers=headers,
                timeout=self._timeout(remaining),
            ) as response:
                if response.status_code == 202:
                    time.sleep(min(self.poll_interval_seconds, max(0, deadline - time.monotonic())))
                    continue
                if response.status_code != 200:
                    raise self._status_error(response.status_code)

                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in {
                    "audio/mpeg",
                    "audio/wav",
                    "audio/x-wav",
                    "application/octet-stream",
                }:
                    raise ProviderError(
                        "PROVIDER_INVALID_RESPONSE",
                        "Stability AI returned an unexpected audio content type.",
                        details={"provider": self.name, "content_type": content_type[:80]},
                    )
                declared_size = self._declared_size(response.headers.get("content-length"))
                if declared_size is not None and declared_size > self.max_audio_bytes:
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
                        "Stability AI returned an empty audio response.",
                        details={"provider": self.name},
                    )
                if declared_size is not None and len(audio) != declared_size:
                    raise ProviderError(
                        "PROVIDER_INCOMPLETE_RESPONSE",
                        "The Stability AI audio response was incomplete.",
                        details={
                            "provider": self.name,
                            "expected_bytes": declared_size,
                            "received_bytes": len(audio),
                        },
                    )
                return bytes(audio)

    @staticmethod
    def _timeout(seconds: float) -> httpx.Timeout:
        timeout = max(seconds, 0.001)
        return httpx.Timeout(timeout, connect=min(10.0, timeout))

    @staticmethod
    def _declared_size(content_length: str | None) -> int | None:
        if content_length is None:
            return None
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise ProviderError(
                "PROVIDER_INVALID_RESPONSE",
                "Stability AI returned an invalid content length.",
                details={"provider": "stability"},
            ) from exc
        if declared_size < 0:
            raise ProviderError(
                "PROVIDER_INVALID_RESPONSE",
                "Stability AI returned an invalid content length.",
                details={"provider": "stability"},
            )
        return declared_size

    def _status_error(self, status_code: int) -> ProviderError:
        if status_code == 401:
            code, message, retryable = (
                "PROVIDER_AUTHENTICATION_FAILED",
                "Stability AI rejected the configured API key. Check STABILITY_API_KEY.",
                False,
            )
        elif status_code == 402:
            code, message, retryable = (
                "PROVIDER_INSUFFICIENT_CREDITS",
                "The Stability AI account has insufficient credits for this generation.",
                False,
            )
        elif status_code == 403:
            code, message, retryable = (
                "PROVIDER_CONTENT_REJECTED",
                "Stability AI rejected this request under its content policy.",
                False,
            )
        elif status_code in (400, 422):
            code, message, retryable = (
                "PROVIDER_REJECTED_REQUEST",
                "Stability AI rejected the generation request. Check the prompt and settings.",
                False,
            )
        elif status_code == 413:
            code, message, retryable = (
                "PROVIDER_REQUEST_TOO_LARGE",
                "The Stability AI request exceeded its size limit.",
                False,
            )
        elif status_code == 429:
            code, message, retryable = (
                "PROVIDER_RATE_LIMITED",
                "Stability AI rate-limited this request. Wait before trying again.",
                True,
            )
        elif status_code >= 500:
            code, message, retryable = (
                "PROVIDER_UNAVAILABLE",
                "Stability AI is temporarily unavailable.",
                True,
            )
        else:
            code, message, retryable = (
                "PROVIDER_REQUEST_FAILED",
                "Stability AI could not complete the generation request.",
                False,
            )
        return ProviderError(
            code,
            message,
            retryable=retryable,
            details={"provider": self.name, "http_status": status_code},
        )
