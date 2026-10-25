# IncuBrix AI Music Studio Architecture

## Generation and processing

Stability AI Stable Audio 3.0 is the default real-generation provider. With backend-only `STABILITY_API_KEY`, the background task submits the prompt through the asynchronous text-to-audio endpoint, polls for bytes, processes the audio, validates the encoded file, stores it and a provenance manifest, and only then transitions the job to `succeeded`.

```mermaid
flowchart LR
    UI[React + Vite] -->|session cookie + CSRF| API[FastAPI]
    API --> AUTH[Single-owner auth and authorization]
    AUTH -->|authenticated request| API
    UI -->|poll /api/v1/jobs/{job_id}| API
    API --> SCHEMA[Pydantic request validation]
    API --> STORE[SQLite jobs, idempotency, limits and quota]
    API --> TASK[In-process background task]
    TASK --> PROVIDER[Stability adapter]
    PROVIDER -->|generation ID then audio bytes| DSP[Decode, resample, trim/pad, LUFS and true-peak processing]
    DSP --> VALIDATE[Reopen and validate encoded output]
    VALIDATE --> FILES[Local audio and manifest volume]
    FILES -. if configured .-> OBJECTS[Required S3 write mirror]
    FILES --> STORE
    UI -->|raw WAV/MP3 narration upload| API
    API --> MIX[Decode, align, duck, mix, normalize and validate]
    MIX --> FILES
    UI -->|play/download| FILES
    API -. future .-> QUEUE[Durable worker queue]
```

The frontend sends final requested audio format/processing settings to FastAPI. Stability is requested to return WAV; the backend processes it to exact requested sample frames, resamples to the requested rate (48 kHz in the UI), applies a brief fade, targets integrated LUFS, and limits static gain as needed to keep the final 4x-oversampled true peak at or below -1 dBTP. It then reopens the written output and measures metadata. If provider audio is more than 0.5 seconds short, processing fails rather than creating an excessive silent tail. If peak protection leaves final integrated loudness outside the ±1 LU tolerance, the job fails rather than reporting a successful delivery.

WAV processing uses NumPy, SciPy, SoundFile, pyloudnorm, and Mutagen. MP3 output and decoding require an installed FFmpeg build with `libmp3lame` for encoding. The backend preflights MP3 encoder support before making a generation request. MP3 workflows were not validated in this environment because FFmpeg was unavailable.

## Provider and secrets

The adapter uses `POST https://api.stability.ai/v2beta/audio/stable-audio/text-to-audio`, `model=stable-audio-3`, multipart prompt/duration/output fields, optional seed, and the documented asynchronous response ID; it polls `GET /v2beta/audio/results/{id}` until audio is returned. It has bounded timeouts/download size and structured provider errors. Provider keys are read only from backend environment settings; the browser has no key-entry field, provider API requests cannot supply keys, and keys are never returned in API responses.

The app supports 5–300 second requested duration. The provider source is 44.1 kHz stereo; app output is resampled, validated, and reported at the requested sample rate. Instrumental-only intent is text prompt guidance, not a strict provider flag, and vocals are not independently classified. Video descriptions are text context only; no video file is analyzed.

## Narration upload and ducking

After a successful music job, `POST /api/v1/jobs/{job_id}/narration` accepts raw WAV/MP3 bytes, bounded by `MAX_NARRATION_BYTES` (50 MB by default). It validates audio by decoding rather than trusting the filename/MIME type, aligns narration to the existing music duration (trimming or padding and reporting the operation), creates a smoothed signal-level duck with approximately 12 dB attenuation, 50 ms attack, and 250 ms release, then normalizes and validates the mix. It stores a separate mix artifact and metadata and exposes read/download routes. Narration remains on the application backend and is not sent to Stability. The duck envelope is based on signal level, not speech recognition/VAD.

## Stems, sections, and looping

Stable Audio returns a single mix. Demucs and Spleeter repositories have permissive source-code licenses, but commercial-use rights for their pretrained model weights were not established from reviewed official materials. No separator is installed or bundled. The application does not synthesize fake stems or frequency-split the mix. Independent stem controls and stem regeneration are unavailable. Section start times, tempo, key, and loopability remain provider prompt cues; beat alignment, precise section boundaries, and seamless looping have not been analyzed or tested.

## Jobs, storage, readiness

Generation states are `queued`, `running`, `succeeded`, and `failed`. Jobs, idempotency keys, quotas, and fixed-window rate counters use transactional SQLite; audio, narration mixes, and manifests use atomic local writes under `AUDIO_STORAGE_DIR`. Compose assigns separate named volumes to the SQLite data directory and audio directory. Startup marks queued/running jobs failed with `JOB_INTERRUPTED` and never retries a possibly chargeable provider request. When all S3 settings are present, every object write is required for success; the API still reads local files and has no S3 recovery/read path. `/health` checks process liveness. `/ready` reports generic authentication, SQLite, storage, and configured-provider status but does not contact the music provider or check credits. Logs exclude prompt text and credentials.

`/api/v1/provider` checks Stability credits only when Stability is selected and configured; the UI refreshes provider status once per minute instead of on every health poll. A failed balance check is logged and returned as unknown. The UI distinguishes configured credentials from a verified credit balance. Webhook callbacks require an explicit `WEBHOOK_SECRET`; without it, callbacks are skipped and logged. Remote narration and callbacks require HTTPS hostnames in `OUTBOUND_URL_ALLOWED_HOSTS`, public DNS resolutions, and no redirects.

The private-instance security model is single-owner authentication with a scrypt password hash, signed HTTP-only session cookie, CSRF token for mutations, owner-scoped jobs/audio, SQLite-backed per-IP/per-owner request limits, a configurable daily generation quota (default five), and one active generation per owner by default. No account creation, password reset, or hardcoded administrator exists. Production requires a strong `AUTH_SESSION_SECRET`, secure cookies, HTTPS, explicit CORS origins, and persistent local paths. These controls are single-host only, not distributed rate limiting or multi-instance coordination. SQLite and local file volumes are not backups or proof of resilience on ephemeral hosting. A durable queue, replicated/managed metadata, object-store read/recovery, retention/backup controls, and deployment testing remain outstanding.

The development Compose file uses SeaweedFS 4.50 as a local S3-compatible service and reads access credentials from the untracked project-root `.env`; it is not a production deployment configuration. Compose writes the S3 identity file inside the container with restrictive permissions, starts `weed mini` on a dedicated `seaweedfs_data` volume, and pre-creates the application bucket. The old `minio_data` volume remains attached read-only and is not compatible with SeaweedFS storage or automatically migrated. Docker was unavailable during the current security/persistence validation, so the named-volume and object-store path were not executed. Do not deploy publicly.

## Phase 3 verification gate

Mocked tests do not complete real-provider acceptance. Phase 3 remains **unverified** until the operator explicitly approves a live request and a real file is retrieved, processed, stored, reopened, and reviewed. No paid request is initiated automatically.

## Pricing and rights

The current [Stability API reference](https://docs.stability.ai/api-reference/stable-audio/text-to-audio) describes the endpoint; the [pricing page](https://docs.stability.ai/pricing) lists eight credits per successful Stable Audio 3.0 request and $0.01 per credit. Prices may change; the app does not buy credits.

Under Stability's current [Terms of Service](https://stability.ai/terms-of-service), output rights are subject to plan, terms, and applicable law. The app does not guarantee uniqueness, copyrightability, or freedom from infringement. Provider [Acceptable Use Policy](https://stability.ai/use-policy) and [Privacy Policy](https://stability.ai/privacypolicy) apply.
