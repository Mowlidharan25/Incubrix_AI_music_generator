# IncuBrix AI Music Studio

IncuBrix is a React + FastAPI application for creating and tracking instrumental background-music jobs. The default provider is Stability AI Stable Audio 3.0. With `STABILITY_API_KEY` configured on the backend, the app requests actual audio, polls the provider's asynchronous generation result, validates and stores the returned file, and exposes playback/download only after success. Without credentials, jobs fail clearly. Automated tests use mocked provider responses and never make paid API calls.

## Provider, pricing, and rights

The selected API is Stability AI's Stable Audio 3.0 text-to-audio service. Current official documentation specifies `POST /v2beta/audio/stable-audio/text-to-audio`, the `stable-audio-3` model, multipart form data, MP3 or WAV output, and asynchronous polling at `/v2beta/audio/results/{id}`. Its endpoint schema accepts 1–380 seconds while its prose describes compositions up to six minutes, both at 44.1 kHz stereo; this app accepts 5–300 seconds. The prompt requests instrumental music, but the provider does not expose a strict instrumental-only flag; output is not independently checked for vocals.

The [official pricing page](https://docs.stability.ai/pricing) currently lists a flat rate of **8 credits for each successful Stable Audio 3.0 generation** and states that one credit equals **$0.01** (a current listed equivalent of $0.08 per successful generation). Failed generations are documented as not charged. Rates and service terms may change. This app does not purchase credits or retry generation automatically.

Stability's current [Terms of Service](https://stability.ai/terms-of-service) assign output rights to the user as between the user and Stability, to the extent permitted by law and subject to compliance with its terms. Users are responsible for their inputs and verifying the legality and appropriateness of outputs. Outputs may be similar to other users' outputs; this is not a guarantee of uniqueness, copyrightability, or non-infringement. Read the current [Acceptable Use Policy](https://stability.ai/use-policy) and [Privacy Policy](https://stability.ai/privacypolicy) before use. This project does not provide legal clearance.

## Architecture

- `frontend/` - responsive React/TypeScript interface.
- `backend/` - FastAPI routes, Pydantic schemas, provider adapter, job manager, in-memory store, audio validation, and local storage.
- `docs/` - architecture, phase status, and OpenAPI contract.
- `backend/tests/` - isolated test suite. No paid API key or network call is needed.

The API accepts a job, puts it in `queued`, starts an in-process task, calls the configured provider, retrieves the real provider audio, processes it to the requested duration at exactly 48,000 Hz, normalizes toward the requested LUFS with oversampled true-peak protection, validates and stores the output plus provenance manifest, and then marks it `succeeded`. Optional loop mode applies a 250 ms edge crossfade and measures encoded endpoint amplitude discontinuity; this does not guarantee musical seamlessness. Mood sections, tempo, and key are recorded as prompt metadata; estimated 4/4 bar positions are not verified against the generated audio. An opt-in local stem step runs after the mix is saved; if unavailable or it fails, the generated mix still succeeds and the response/manifest carries a separate stem status/error. The provider source is retained separately from the processed delivery file. WAV output uses the Python DSP stack; MP3 requires FFmpeg with `libmp3lame`, and the backend checks for that encoder before contacting the provider. Provider failures and processing errors remain explicit. The creator studio checks `/health` separately from `/ready`; backend reachability and provider-key configuration are distinct indicators. Video context is used as text prompt context only; no video file is analyzed. WAV/MP3 narration can be uploaded to the backend for a real ducked mix with playback and download links. **Experimental SAM-Audio stem processing is available only when explicitly requested and the gated local model is set up; no real SAM-separated stems have yet been validated in this workspace.** The model returns target/residual outputs, not guaranteed clean instrument classes. AudioShake advertises an API/SDK, but its public terms/pricing/API contract have not been verified. See the Phase 4 section in [REPORT.md](./REPORT.md). The studio compares requested and measured duration, sample rate, integrated LUFS, and true peak, labels each requirement passed, failed, or not measured, and displays channel count, format, warnings, and download links. Completed jobs expose their stored JSON manifest with a download name of `music_manifest.json`. History disappears on backend restart. ElevenLabs remains an optional legacy provider by explicitly setting `MUSIC_PROVIDER=elevenlabs`.

## Prerequisites

- Python 3.11 or newer.
- Node.js 22 or newer and npm.
- Audio DSP dependencies installed from `backend/requirements.txt` (NumPy, SciPy, SoundFile, pyloudnorm).
- Optional FFmpeg build with `libmp3lame` available on `PATH` for MP3 encode/decode.
- A Stability AI API key and available account credits for live generation.
- Optional SAM-Audio source-separation runtime in `backend/requirements-stems.txt`; it is not installed by the default requirements and requires manually approved checkpoint access.

For MP3 output, install an FFmpeg build that includes `libmp3lame`, add its `bin` directory to the Windows `PATH`, and restart the backend terminal/process. Verify setup with `ffmpeg -hide_banner -encoders` and confirm `libmp3lame` is listed. The API returns a structured `MP3_ENCODER_UNAVAILABLE` error with this requirement rather than silently falling back to WAV. Check the exact FFmpeg build and enabled-codec licenses before redistribution or commercial deployment.

## Setup and start on Windows

### Backend

```powershell
cd C:\Incubrix_music_problem\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item ..\.env.example .env
notepad .env
```

In the local `backend/.env` file, set `STABILITY_API_KEY` to a **new key created after revoking any key exposed in a screenshot or environment template**. Never paste keys into chat, source code, screenshots, browser fields, API requests, or the frontend `.env`. Provider credentials are backend-environment-only; the provider settings API accepts provider selection but rejects extra fields such as `api_key`. The root `.gitignore` excludes `.env` files. Save the file, then start the API:

Before starting, configure the single-owner login in the ignored `.env` file. Generate a password hash from a hidden prompt, then generate a separate session secret:

```powershell
python -c "from getpass import getpass; from app.services.auth import create_password_hash; print(create_password_hash(getpass('Password: ')))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set those outputs as `AUTH_PASSWORD_HASH` and `AUTH_SESSION_SECRET`, choose a private `AUTH_USERNAME`, and leave `AUTH_COOKIE_SECURE=false` only for local HTTP development. Do not share or commit the session secret. The app fails closed for generation, job, provider, narration, manifest, and audio endpoints until authentication is correctly configured; `/health` and `/ready` remain public.

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If PowerShell activation is restricted, start using `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.

### Frontend

In a second terminal:

```powershell
cd C:\Incubrix_music_problem\frontend
npm install
Copy-Item .env.example .env
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. The frontend example configures `VITE_API_BASE_URL=http://127.0.0.1:8000`, matching the backend's bind address; change this public URL in `frontend/.env` when the API runs elsewhere. Vite environment variables are public and must never contain the provider key. After changing frontend `.env`, restart Vite.

The workspace also has a VS Code full-stack launch option: **Run and Debug → IncuBrix: Start full stack**.

## Backend environment variables

See root [.env.example](./.env.example). It contains placeholders only. Backend defaults allow the API and mock-based tests to start without provider credentials; real generation requires the selected provider's backend key:

- `MUSIC_PROVIDER=stability` selects Stable Audio 3.0; configure `STABILITY_API_KEY` only in local `backend/.env` or a production secret manager.
- `MUSIC_PROVIDER=elevenlabs` is an optional alternative and requires `ELEVENLABS_API_KEY` only when selected. ElevenLabs is not the default and this project does not require an ElevenLabs account or make paid requests automatically.
- For local Docker Compose only, the project-root `.env` must define `S3_ACCESS_KEY` and `S3_SECRET_KEY`; copy the clearly marked local placeholders from `.env.example` and replace them with local-only values. Do not reuse them in production.
- `PROVIDER_TIMEOUT_SECONDS` controls the total start-and-poll timeout (default 300 seconds).
- `MAX_GENERATED_AUDIO_BYTES` bounds the downloaded audio response (default 100 MB).
- `MAX_NARRATION_BYTES` bounds raw WAV/MP3 narration uploads (default 50 MB).
- `AUTH_USERNAME`, `AUTH_PASSWORD_HASH`, and `AUTH_SESSION_SECRET` configure the single-owner account. Passwords use the built-in scrypt hash format; no default user/password exists. Set `AUTH_COOKIE_SECURE=true` when served over HTTPS. Production must use an explicit CORS origin list; wildcard origins disable readiness.
- `MAX_ACTIVE_GENERATIONS` defaults to one active job and `MAX_GENERATIONS_PER_DAY` defaults to five accepted requests. SQLite-backed per-IP and per-user request limits and the generation quota apply to this single backend instance only; they are not distributed controls.
- `JOB_DATABASE_PATH` selects the SQLite job/idempotency/rate-limit database (default `backend/incubrix_jobs.sqlite3` when run directly). `AUDIO_STORAGE_DIR` selects the audio/manifests directory. Back up both paths together. Restart persistence is only as durable as the host disk/volume retaining them; this is not a multi-instance or managed-database guarantee.
- Narration and decoded source audio are limited to 300.5 seconds to bound processing; generated requests are limited to 5–300 seconds.
- `AUDIO_STORAGE_DIR` is the server-side generated file directory (default `backend/generated_audio`).
- `ELEVENLABS_MODEL_ID` optionally selects the model for the ElevenLabs adapter.
- `CORS_ORIGINS` is the JSON list of allowed browser origins; set it to the exact deployed frontend origin in production.
- `OUTBOUND_URL_ALLOWED_HOSTS` is a JSON list of exact public HTTPS hostnames permitted for remote narration and webhook URLs. It defaults to `[]`, which rejects remote URLs unless explicitly allowed.
- `WEBHOOK_SECRET` is needed only when signed completion callbacks are used. When all S3 settings are configured, object-store writes are required for a job to succeed; failures produce a failed job and do not report success. API downloads still read the local audio directory, so the object store remains a write mirror, not a recovery/read path. In local Compose, SQLite and audio use named volumes; SeaweedFS keeps its separate object-store volume. No public cloud storage is configured by this project.
- `APP_NAME`, `ENVIRONMENT`, `MAX_REQUEST_BYTES`, `MAX_IDEMPOTENCY_KEY_LENGTH`, and `S3_REGION` tune application behavior. The frontend's `VITE_API_BASE_URL` is public configuration and must never contain provider credentials.

### Optional SAM-Audio stem setup

Commercial permission is not established; have counsel review the [SAM License](https://github.com/facebookresearch/sam-audio/blob/main/LICENSE) before use. The account holder must request and accept access for the exact checkpoint in the official [SAM-Audio model card](https://huggingface.co/facebook/sam-audio-small). Do not send a Hugging Face token in chat.

1. Install a platform-compatible PyTorch/torchaudio build following the official [PyTorch install selector](https://pytorch.org/get-started/locally/), then install the optional pinned integration in the backend environment: `python -m pip install -r requirements-stems.txt`. A CUDA-compatible GPU is recommended; CPU may be slow and memory-intensive.
2. After access to the exact pinned small checkpoint is approved, authenticate locally with `hf auth login` and explicitly download it: `hf download facebook/sam-audio-small --revision 20b65f56888142eebe7c37448c6f6b3b32600e9b`. The checkpoint is about 5.1 GB; allow additional disk and memory for dependencies and PE-AV. The application uses `local_files_only=True` and never downloads model weights automatically.
3. Restart the backend. **Try experimental instrument stems** is available only as part of a newly created job; that job still calls the configured music provider and may incur a credit charge. The application currently has no reprocess-existing-WAV control, so do not use this workflow when you must avoid a new generation request.

SAM processing is opt-in and disabled by default. It prompts sequentially for drums/percussion, bass, and other instruments, plus a remaining residual track. All tracks are saved as aligned 48 kHz WAVs with the mix channel count (dual-mono for stereo input because the documented model outputs a one-dimensional waveform). The residual can contain missed sources or bleed and is not guaranteed to be an instrument-only stem. The manifest records prompts, model/checkpoint and source revisions, unity gains, measured file properties, license references, and relative reconstruction error against the mono mix. That error is not a perceptual quality score. Joint browser playback starts aligned files together on a best-effort basis; it is not sample-accurate synchronization. No real separation has been run; SAM commercial-use permission and viable access/runtime setup remain unverified. Demucs and Spleeter checkpoint rights are unverified, while Open-Unmix umxl weights are non-commercial. Phase 4 remains incomplete.

`GET /health` reports API process reachability. `GET /ready` reports generic auth, job database, provider-credential, and local storage readiness without returning the provider name, configuration variable names, or secrets. Provider connection and available credits are not checked by readiness.

The frontend signs in through an HTTP-only signed session cookie and sends a CSRF token for state-changing requests. There is no public registration, user-management, or password-reset flow. Rotate `AUTH_SESSION_SECRET` to invalidate all current sessions. Use the frontend and API on a same-site HTTPS origin for production; if they are cross-site, configure `AUTH_COOKIE_SAMESITE=none` together with `AUTH_COOKIE_SECURE=true` and the exact frontend origin.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Process liveness. |
| `GET` | `/ready` | Generic auth, persistent job database, provider-credential, and storage readiness. |
| `GET` | `/api/v1/auth/csrf` | Obtain a CSRF token before sign-in. |
| `POST` | `/api/v1/auth/login` | Sign in and receive an HTTP-only session cookie. |
| `GET` | `/api/v1/auth/me` | Return the signed-in single-owner account. |
| `POST` | `/api/v1/auth/logout` | End the current browser session. |
| `GET` | `/api/v1/provider` | Return selected provider/configuration status without returning credentials. |
| `POST` | `/api/v1/provider` | Select a provider; credentials must be configured in the backend environment. |
| `POST` | `/api/v1/music/jobs` | Validate and enqueue a request (`202`). |
| `GET` | `/api/v1/jobs?limit=50` | List up to 100 recent jobs belonging to the signed-in owner. |
| `GET` | `/api/v1/jobs/{job_id}` | Get state, error/warnings, and actual audio metadata after success. |
| `GET` | `/api/v1/jobs/{job_id}/audio` | Download audio only if generation, validation, and storage succeeded. |
| `GET` | `/api/v1/jobs/{job_id}/stems/{stem_id}` | Download a validated experimental stem WAV (`percussion`, `bass`, `other`, or `remaining`). |
| `GET` | `/api/v1/jobs/{job_id}/manifest` | Retrieve the stored generation manifest after success. |
| `POST` | `/api/v1/jobs/{job_id}/narration` | Upload raw WAV/MP3 narration and create a measured ducked mix (max 50 MB by default). |
| `GET` | `/api/v1/jobs/{job_id}/narration` | Retrieve narration-mix metadata if one was created. |
| `GET` | `/api/v1/jobs/{job_id}/narration/audio` | Download the validated narration mix. |
| `GET` | `/docs` | Interactive FastAPI docs. |
| `GET` | `/openapi.json` | Generated OpenAPI JSON. |

### Submit a real job

This operation submits a paid Stability AI generation request. Use the signed-in UI or an API client that retains the session cookie and sends the CSRF token. Run it only after confirming the replacement API key is configured, checking available credits, and explicitly deciding to spend them.

The service requests WAV from Stability and performs the requested processing locally. Use MP3 as the requested output format only after installing an FFmpeg build with `libmp3lame`; the backend checks this prerequisite before making any provider request. Output duration is processed to the requested duration and measured, and the final sample rate, integrated LUFS, and oversampled true peak are measured from the encoded file.

API callers must first request `/api/v1/auth/csrf`, post their username and password to `/api/v1/auth/login` with that CSRF header while retaining the session cookie, then use the returned CSRF token for the generation POST. This sign-in sequence is shown in the UI; no unauthenticated `curl` request is provided. The initial job response is `queued`; poll its ID using `GET /api/v1/jobs/{job_id}` with the same session. The following response is illustrative only, not a measured live generation:

```json
{
  "job_id": "provider-created-job-uuid",
  "status": "succeeded",
  "progress": 100,
  "error": null,
  "result": {
    "provider": "stability",
    "model": "stable-audio-3",
    "format": "wav",
    "requested_duration_s": 45.0,
    "requested_sample_rate": 48000,
    "target_integrated_lufs": -16.0,
    "true_peak_ceiling_dbtp": -1.0,
    "sample_rate": 48000,
    "channel_count": 2,
    "duration_s": 45.0,
    "size_bytes": 1081234,
    "integrated_lufs": -16.0,
    "true_peak_dbtp": -1.4,
    "is_ai_generated": true,
    "download_url": "/api/v1/jobs/provider-created-job-uuid/audio",
    "stem_processing": {
      "status": "not_requested",
      "error": null,
      "model_id": null,
      "model_version": null,
      "method": null,
      "stems": [],
      "reconstruction_relative_error_db": null,
      "reconstruction_metric": null,
      "reconstruction_note": null
    },
    "generation_started_at": "2026-10-22T00:00:00Z",
    "generation_completed_at": "2026-10-22T00:00:35Z",
    "processing_duration_s": 35.0
  },
  "warnings": [],
  "prompt": "Warm optimistic acoustic instrumental for a product launch",
  "duration_s": 45
}
```

The values above illustrate the response shape only; they are not evidence of a live provider generation. Measurements are taken from processed, encoded audio. The result keeps requested targets beside measured values; the manifest includes explicit requirement statuses. A failed or unconfigured job has `result: null` and no audio download link. One manually approved 5-second Stability WAV generation has since passed real-file validation; see [REPORT.md](./REPORT.md) for its job ID and measured values. This single run does not establish repeatability or musical quality.

Audio is stored under `backend/generated_audio/` by default. The provider source uses `<job-id>.source.<format>` and the processed output uses `<job-id>.<format>`; the normal playback/download endpoint serves the processed output. A JSON manifest next to them records both files, the request, provider/model provenance, requested and measured processing values, requirement statuses, warnings, timestamps, and plan-dependent licensing notice. It does not assert that the user's actual licence has been verified.

## Audio processing and known limitations

### Instrument stem separation (Phase 4)

SAM-Audio processing is implemented as an opt-in local stage, but the gated model is not installed or accessible in this workspace, so no real SAM output has been verified. The workflow prompts sequentially for drums/percussion, bass, and other instruments and retains the final model residual as a fourth track. It validates 48 kHz, duration within 0.05 seconds, channel count, finite samples, WAV persistence, and computes relative RMS reconstruction error. It does not guarantee source purity; the remaining track is not a clean instrument class, and an error metric cannot establish perceptual quality. Files are listed/served only after all four outputs pass file validation. Model load/inference failures are reported separately while preserving the successful music mix. Mute, solo, volume, and joint playback controls apply to stored tracks. Requesting stems adds no provider API call. However, this workspace has no SAM/PyTorch installation or gated checkpoint access, and no real file has passed separation/listening validation. Phase 4 remains incomplete. See [REPORT.md](./REPORT.md) for licensing/access evidence and setup requirements.

- The prompt asks for instrumental music; the Stable Audio API does not expose a strict instrumental-only switch. Audio is not independently scanned for vocals or speech.
- Exactly one final format is returned per job. With the default request listing both formats, WAV is selected; select MP3 alone for MP3 output.
- The final output is resampled to exactly 48,000 Hz; other requested sample rates are rejected. Audio is trimmed or short-padded to the exact requested sample count, with a fade-out. Output duration is measured from the encoded file and must be within 0.05 seconds of the request. If provider audio is over 0.5 seconds shorter than requested, processing fails instead of filling a long silent tail.
- Normal output receives a short fade-out. When `loopable` is enabled, output instead uses a 250 ms reverse-head/tail crossfade and checks encoded endpoint discontinuity (RMS first-vs-last sample difference) against -60 dBFS. This validates only endpoint amplitude continuity; it does not establish musical, harmonic, or rhythmic continuity.
- Audio is normalized toward the requested integrated LUFS, passed through a short look-ahead peak limiter, and validated against a -1 dBTP ceiling measured after 4x oversampling. Final encoded output is re-measured. The UI/manifest reports each target passed, failed, or not measured; peak limiting can make the final LUFS miss the target. This is not a broadcast mastering guarantee.
- WAV processing is covered by offline tests. MP3 encode/decode requires FFmpeg and a `libmp3lame` encoder; if unavailable, MP3 processing returns a structured setup error. MP3 tests run only when that encoder is available. FFmpeg build licensing depends on included codecs; check the exact binary license before redistribution or commercial packaging.
- Prompt, mood timeline, tempo, key, and loop preferences are text cues, not guaranteed features. The provider seed field is forwarded when supplied. Remote narration is fetched only from HTTPS hosts explicitly configured in `OUTBOUND_URL_ALLOWED_HOSTS`; destinations must resolve to public IP addresses, redirects are disabled, and downloads are bounded by `MAX_NARRATION_BYTES`. Prefer the narration file-upload endpoint.
- Uploaded narration is mixed with music using 10 ms RMS blocks: signal at/above -45 dBFS targets 12 dB ducking with a 50 ms attack and 250 ms release. This is signal-level ducking, not speech recognition or voice-activity detection. The upload is bounded by `MAX_NARRATION_BYTES` and 300.5 seconds, validated by audio decoding, aligned to the music with reported trim/padding, saved locally, and exposed through metadata/playback/download routes. MP3 narration needs FFmpeg decoding; actual mix metadata appears in the UI only after the backend successfully saves and validates the file.
- Sections, tempo, and key remain prompt metadata. The manifest estimates nearest 4/4 bar positions from requested tempo but does not analyze the generated audio, snap section boundaries, or claim beat alignment.
- Demucs and Spleeter are not used; their source-code licenses do not establish pretrained-weight rights. No model weights are bundled or automatically downloaded. The app does not split frequency bands and relabel them as instrument stems.
- Prompts are sent to Stability AI without an app-side copyright/artist-name filter. Follow the provider's [Acceptable Use Policy](https://stability.ai/use-policy); the app does not certify prompt or output originality or rights.
- Independent stem regeneration remains unavailable until gated SAM access/inference is verified. The loop endpoint check is not a guarantee of a musically seamless loop; full assessment compliance remains unverified.
- Jobs and idempotency keys are in memory and disappear when the backend restarts. Audio files are saved on local disk, but their job metadata/download association is lost after restart.
- FastAPI in-process background tasks are not durable and can be interrupted by process shutdown. Use durable workers/storage for production.
- This development API has no authentication. Do not deploy it publicly with provider credentials until authentication, quotas, abuse prevention, and production storage are added.
- Provider terms and local law apply. Stability AI does not guarantee outputs are unique or non-infringing; users remain responsible for their prompts and how outputs are used.
- Remote narration and webhook URLs share the outbound host allowlist. This reduces server-side request risks but does not replace API authentication or tenant isolation.

## Errors

Errors use `{ "error": { "code", "message", "retryable", "details" } }`.

- `INVALID_REQUEST`, `INVALID_IDEMPOTENCY_KEY`, `IDEMPOTENCY_KEY_CONFLICT`
- `JOB_NOT_FOUND`, `AUDIO_NOT_AVAILABLE`, `AUDIO_FILE_NOT_FOUND`
- `PROVIDER_NOT_CONFIGURED`, `PROVIDER_AUTHENTICATION_FAILED`, `PROVIDER_INSUFFICIENT_CREDITS`
- `PROVIDER_CONTENT_REJECTED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_REJECTED_REQUEST`, `PROVIDER_UNAVAILABLE`
- `PROVIDER_TIMEOUT`, `PROVIDER_CONNECTION_FAILED`, `PROVIDER_INVALID_RESPONSE`, `PROVIDER_INCOMPLETE_RESPONSE`
- `OUTBOUND_URL_NOT_ALLOWED` for narration/callback destinations that fail the configured HTTPS host policy.
- `UNSUPPORTED_OUTPUT_FORMAT`, `PROVIDER_REQUEST_TOO_LARGE`, `PROVIDER_RESPONSE_TOO_LARGE`, `PROVIDER_EMPTY_RESPONSE`
- `MP3_ENCODER_UNAVAILABLE`, `MP3_DECODER_UNAVAILABLE`, `AUDIO_DECODE_FAILED`, `AUDIO_DURATION_MISMATCH`, `AUDIO_DURATION_TOO_LONG`, `AUDIO_METADATA_INVALID`, `AUDIO_SILENT`, `AUDIO_TRUE_PEAK_VALIDATION_FAILED`
- `NARRATION_MISSING`, `NARRATION_SIZE_INVALID`, `NARRATION_TOO_LARGE`, `NARRATION_FORMAT_UNSUPPORTED`, `NARRATION_MIX_NOT_FOUND`
- `AUDIO_OUTPUT_INVALID`, `AUDIO_STORAGE_FAILED`, `JOB_PROCESSING_FAILED`, `INTERNAL_ERROR`

Generation is not retried automatically after a timeout or provider error. A timeout may occur after the provider accepted the work, and the app avoids potentially duplicate charges.

## Optional live integration test

After revoking the exposed key, create a replacement and place it only in `backend/.env` as `STABILITY_API_KEY=...`. From PowerShell, with backend dependencies installed:

```powershell
cd C:\Incubrix_music_problem\backend
$env:RUN_STABILITY_INTEGRATION_TEST = "1"
.\.venv\Scripts\python.exe -m pytest tests\test_stability_integration.py -q -s
Remove-Item Env:RUN_STABILITY_INTEGRATION_TEST
```

This explicit opt-in test makes one 5-second Stable Audio 3.0 request for WAV, currently 8 credits on success. Check your account balance first; the app does not buy credits. The test writes the returned audio under pytest's temporary directory, processes it to 48 kHz and exact requested duration, validates it, and prints actual duration, sample rate, channels, and size. All normal automated tests use mocked HTTP responses and do not call Stability AI.

## One-command start and test (Integration Standard #1)

Run all unit, integration, and end-to-end tests with **zero API keys required** using a single command:

```powershell
python run_tests.py
```
Or on Windows PowerShell:
```powershell
.\run_tests.ps1
```

Docker Compose provides a local development stack, not a production deployment. Copy the example environment file to the project root and configure `S3_ACCESS_KEY` and `S3_SECRET_KEY`. The Compose object store is SeaweedFS 4.50, exposed on the local S3-compatible endpoint at `http://127.0.0.1:9000` (Admin UI at `http://127.0.0.1:9001`); the backend connects internally to `http://object-store:8333`. SeaweedFS receives credentials through a mode-0600 runtime S3 config created inside its container, and pre-creates `incubrix-audio`. Its persistent data uses the separate `seaweedfs_data` volume. The previous `minio_data` volume is mounted read-only and is not migrated or modified. Published service ports bind to loopback only. Local audio storage remains authoritative; optional S3 mirroring is best-effort.

```powershell
Copy-Item .env.example .env
# Edit .env and set strong S3_ACCESS_KEY and S3_SECRET_KEY values.
docker compose up
```

## Batch Runner CLI (Integration Standard #10)

The batch runner executes a folder or JSON file of test cases against the running API, tracks status, downloads all outputs (`mix.wav`, stems, `mix_ducked.wav`, `music_manifest.json`), and writes a consolidated report to a single folder:

```powershell
python scripts/batch_runner.py --input test_pack/inputs/10_diverse_prompts.json --output-dir batch_results --api-url http://127.0.0.1:8000
```

Hard cases can be tested similarly:
```powershell
python scripts/batch_runner.py --input test_pack/inputs/hard_cases.json --output-dir batch_results_hard --api-url http://127.0.0.1:8000
```

## Webhooks and S3 Storage

- **HMAC-SHA256 Webhook (Integration Standard #3)**: A completion callback is sent only when `WEBHOOK_SECRET` is set and the HTTPS destination is on the outbound host allowlist.
- **S3-Compatible Storage (Integration Standard #7)**: When endpoint, bucket, access key, and secret are configured, audio and manifests are mirrored to S3 alongside local storage. Local files remain authoritative; mirroring is best-effort and failures are logged. The application does not restore files from S3, and job metadata is in memory, so this is not sufficient for production persistence across backend restarts.
- **Remote Narration URL**: An allowlisted HTTPS URL returning WAV/MP3 can be mixed with bounded, signal-level ducking. The narration upload endpoint avoids remote URL configuration.

## Tests and checks

```powershell
# Run all tests (unit + integration + end-to-end):
python run_tests.py

# Frontend lint and build:
cd frontend
npm run lint
npm run build
```
