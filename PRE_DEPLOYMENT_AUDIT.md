# Pre-deployment audit — IncuBrix AI Background Music Engine

**Audit date:** 2026-10-25  
**Decision:** **FAIL — do not deploy publicly or describe the engine as submission-ready.**

## Scope and evidence limits

The referenced Engineering Assessment 1 PDF was not present in the workspace or available attachment files during this audit. This report evaluates every requirement in the accompanying task message; an exact PDF-specific crosswalk remains unavailable until that PDF is supplied.

No live paid music generation was submitted and no provider credits were consumed by this audit. During the browser walkthrough, the backend's selected provider changed to Stability. At least one read-only account-balance request was made while checking provider status; the provider-status endpoint queries the balance when Stability is selected and configured. No generation was submitted. HTTP end-to-end and batch checks used the explicitly labelled `mock` provider, which creates synthetic test audio and is not evidence of generated music quality. One pre-existing AI-generated WAV was read and measured locally without contacting the provider.

Docker could not be tested because the Docker executable is not installed in this environment. The workspace has no `.git` metadata, so commit history and tracked-file status could not be verified.

### Docker image pull investigation and local object-store resolution — 2026-10-25

- The initial `compose.yaml` used `minio/minio:RELEASE.2024-01-18T22-51-28Z` for the MinIO service and `node:22-alpine` for the frontend. The backend image is built from `backend/Dockerfile`.
- The Docker Hub registry metadata endpoint returned **404** for the exact MinIO repository/tag. This supports the reported unavailable image, but an actual `docker pull` could not be run because the Docker CLI and daemon are unavailable.
- Docker Hub metadata for `library/node:22-alpine` reports the tag as active with published multi-architecture images. This verifies the official tag exists; the interrupted image pull could not be retried from this environment.
- The official [MinIO repository README](https://github.com/minio/minio/blob/master/README.md) states that the community repository is no longer maintained and distributed as source only. The upstream repository was archived on 2026-04-25. Its community license is AGPLv3; assess obligations before use.
- The upstream [AIStor container instructions](https://docs.min.io/aistor/installation/container/install/) state that an active license is required for production and instruct operators to obtain a license file. This is not a drop-in free local-development replacement without obtaining the required license and validating its startup/configuration.
- Requests to Quay's MinIO repository/registry returned **401 Unauthorized** in this environment, so neither the existing MinIO tag nor a replacement Quay tag was verified as pullable. No unofficial image or guessed tag was selected.
- **Resolution:** switched the local Compose object store to the upstream SeaweedFS project’s official Docker Hub image `chrislusf/seaweedfs:4.50`, pinned to registry digest `sha256:c2658820e763294a9e9de7b36c81e59b879e3459148351aee4e31a126ca64860`. Docker Hub metadata for the exact tag reports active status and amd64, arm64, arm, 386, ppc64le, and s390x images. The official [SeaweedFS Docker README](https://github.com/seaweedfs/seaweedfs/blob/master/docker/README.md) identifies `chrislusf/seaweedfs` as the project image and documents signatures for release images; the official repository publishes its Docker Compose setup. Actual pull/signature verification could not be run locally.
- SeaweedFS’s official source declares Apache License 2.0. Its official [S3 API support table](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API) lists `HeadBucket`, `CreateBucket`, `PutObject`, object retrieval, and multipart create/upload/complete/abort operations as supported. The backend uses S3v4 with `head_bucket`, `create_bucket`, and `upload_file`.
- Upstream [single-node documentation](https://github.com/seaweedfs/seaweedfs/wiki/Getting-Started) documents `weed mini -dir=/data`, `-s3.config`, and `-bucket` startup options. The official container entrypoint and mini source note that `/data` must remain persistent (including filer metadata). The Compose stack now gives SeaweedFS a separate `seaweedfs_data` volume and pre-creates `incubrix-audio`.
- The runtime entrypoint builds the SeaweedFS S3 identity config from the existing S3 environment variables, restricts their allowed characters, writes it only to the container’s `/tmp` with mode 0600, and logs no credential values. It invokes the image’s official `/entrypoint.sh` so its non-root privilege drop and data permissions remain in effect.
- The S3 endpoint is `http://object-store:8333` inside Compose, while local clients retain host port `127.0.0.1:9000`; the SeaweedFS Admin UI is bound to `127.0.0.1:9001`. The official health endpoint `/healthz` on port 8333 is checked using `curl`, which the official image Dockerfile installs. Backend startup waits for this health check.
- Existing `minio_data` is not a SeaweedFS-compatible data format. The Compose file keeps that volume mounted read-only at an unused path so its contents are preserved; new object data uses `seaweedfs_data`. No migration or deletion was performed.
- The individual pull commands and `docker compose config --quiet` were attempted, but PowerShell reported that `docker` is not recognized; the Docker CLI/daemon is unavailable. `docker compose build`, `docker compose up -d`, `docker compose ps`, and container logs were not run. No containers were started and no volumes/data were touched.

## Fixes made during the audit

- Corrected Stability generation, polling, and balance-check requests to use the backend-held bearer credential. The provider key is not returned in API responses; tests use fake credentials and mocked HTTP.
- Replaced silent credit-balance failures with structured server-side warning logs. An unknown balance remains unknown in the UI.
- Removed the “Engine Ready” label. The UI now distinguishes missing configuration, mock mode, verified depleted/available credits, and unverified credits.
- Blanked the provider key and webhook secret in `.env.example`. Removed the fixed webhook-secret default; callbacks are logged and skipped unless `WEBHOOK_SECRET` is explicitly configured.
- Added a job success gate for measured integrated loudness outside the requested ±1 LU tolerance. Such output now remains a failed job without a result file or success manifest.
- Added regression tests for provider authorization, secret non-disclosure, concurrent independent mock jobs, missing webhook secrets, and loudness rejection.
- Regenerated both OpenAPI YAML files from the FastAPI schema and verified exact structural equality with the running service schema.
- Fixed the remote-narration call to pass the current mixer signature. Remote narration is now streamed with a size limit, content-type check, no redirects, and an explicit outbound-host allowlist. Callback URLs use the same HTTPS/host/DNS policy; callback failures do not log the full URL.
- Added outbound URL policy tests and API regression tests for selected-provider reporting, missing S3 credentials, and bounded remote narration.
- Removed fallback S3/MinIO credentials. The example environment no longer suggests credentials; S3 mirroring is explicitly disabled and logged when required credentials are absent.
- Reduced provider-status polling to once per minute, separated from health and job polling, and aligned UI provider indicators with actual backend configuration.
- Hardened development Compose configuration: loopback-only published ports, required local S3 credentials, backend and object-store health checks, health-gated frontend/backend dependencies, and `npm ci`. Added a backend `.dockerignore` and expanded `.gitignore` for local secrets, caches, generated audio, and build artifacts.
- Updated [README.md](./README.md) and [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for outbound host configuration, remote narration, storage behavior, and development-only Compose limitations.

### Files changed in this audit

- `backend/app/api/routes.py`
- `backend/app/core/config.py`
- `backend/app/services/outbound_url.py` (new)
- `backend/app/services/job_manager.py`
- `backend/app/services/audio_storage.py`
- `backend/tests/test_api.py`
- `backend/tests/test_outbound_url.py` (new)
- `frontend/src/App.tsx`
- `frontend/src/App.css`
- `.gitignore`
- `.env.example`
- `compose.yaml`
- `scripts/seaweedfs-entrypoint.sh` (new; generates runtime S3 auth config without exposing credentials)
- `PRE_DEPLOYMENT_AUDIT.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `backend/.dockerignore` (new)

## Priority 1 — Backend connectivity

| Requirement | Status | Evidence and remaining action |
|---|---|---|
| Frontend API base URL and CORS | **PASS (local)** | Frontend uses `http://127.0.0.1:8000`; backend defaults allow `http://localhost:5173` and `http://127.0.0.1:5173`. Browser API requests from the audit Vite origin `http://127.0.0.1:5175` succeeded after that origin was explicitly allowed; the first attempt correctly failed CORS before the test server was restarted with the origin. Set and verify the exact deployed origin before production. |
| Backend starts and displayed API endpoints exist | **PASS (local)** | Started a fresh FastAPI process with the mock provider. Frontend client paths correspond to implemented routes. The live schema contains 11 paths, including provider configuration, job create/list/status, audio, stems, manifest, narration, health, and readiness. |
| Health and readiness | **PARTIAL** | `/health` remains process liveness. `/ready` now checks SQLite, local/object-store storage configuration, auth configuration, and whether provider credentials are configured; it does not contact the music provider or prove account credits. Docker health/readiness and production HTTPS behavior remain unverified. |
| Submit → poll → manifest → audio playback/download lifecycle | **PASS (mock only)** | Live HTTP lifecycle returned 202, reached `succeeded`, and returned 200 for both manifest and WAV. A 7.25-second fixture measured 7.250000 seconds, 48 kHz, −16.000 LUFS, and −15.054 dBTP. |
| Documented JSON errors | **PASS** | Live invalid-prompt request returned HTTP 422 with `error.code = INVALID_REQUEST` and an `X-Correlation-Id`. Backend tests cover additional validation, missing-job, and provider errors. |
| Idempotency-Key | **PASS** | Repeating an identical live mock request with one key returned the same job ID. Tests also cover conflicting payloads, normalized defaults, and concurrent requests sharing a key. |
| Concurrent jobs | **PASS (mock only)** | Two distinct mock jobs submitted concurrently both succeeded and returned separate audio files. This does not establish provider quota, worker-capacity, or multi-process behavior. |
| Provider timeouts/failures | **PARTIAL** | Offline tests simulate provider timeout, polling timeout, authentication, insufficient-credit, rate-limit, and server errors; paid requests are not retried automatically. No live provider request was made. A previously masked authorization value in the adapter was fixed, but real authentication remains unverified. |
| Provider-credit status and “Engine Ready” | **PARTIAL** | The UI distinguishes mock mode, missing configuration, and depleted/available/unverified provider credits rather than equating readiness with account credits. `/ready` still reports dependency configuration rather than external billing/connection health. At least one read-only balance request occurred while checking provider status after the selected provider became Stability; no generation was submitted. |
| Remote URL and callback handling | **PARTIAL** | The backend requires HTTPS, exact configured hostnames, public DNS answers, and no embedded credentials/nonstandard ports/literal IPs; redirects are disabled and remote narration has a size cap and content-type check. DNS is validated before the HTTP client resolves the host again, so DNS rebinding/pinning is not fully addressed. Empty allowlist rejects remote URLs by default. |

## Priority 2 — Audio requirements

| Requirement | Status | Evidence and remaining action |
|---|---|---|
| Exact requested duration (±0.05 s) | **PASS for tested outputs** | The archived real WAV measured 5.000000 s; the live mock lifecycle measured 7.250000 s. Job processing rejects duration outside tolerance. |
| 48 kHz WAV | **PASS for tested outputs** | The archived real WAV and live mock lifecycle both measured 48,000 Hz. Output validation checks the requested sample rate. |
| −16 LUFS ±1 LU | **PASS for tested outputs; gate fixed** | Archived real WAV measured −16.006 LUFS; mock lifecycle measured −16.000 LUFS. Job processing now fails instead of succeeding if measured loudness is outside ±1 LU. A regression test verifies no successful result/manifest is written for out-of-tolerance audio. |
| True peak ≤ −1 dBTP | **PASS for tested outputs** | Archived real WAV measured −1.952 dBTP; mock lifecycle measured −15.054 dBTP. The job success path rejects values above −1 dBTP. |
| No clipping / no hard-cut ending | **PARTIAL** | The archived real WAV had sample peak 0.794328, zero full-scale/clipped samples, and its final 20 ms was 14.55 dB lower in RMS than the preceding 20 ms. The processing path applies a short fade for non-loop output. This is evidence for one file, not a general hard-cut/music-quality guarantee. |
| Instrumental-only output | **FAIL / unverified** | “No vocals” is prompt guidance only. No vocal detector or independent review was run. Add and validate a vocal-detection/acceptance step before claiming instrumental-only output. |
| Three genuinely distinct instrument stems summing to final mix | **FAIL — blocking** | SAM-Audio integration is opt-in and cache-only; no real model weights/runtime were available or used. Offline stem tests use a fake separator and do not prove real instrument separation. The current adapter sequentially prompts for three targets plus a residual and measures reconstruction against a mono downmix; this does not prove three pure instrument stems or reconstruct the full stereo mix. Do not advertise this as complete. |
| Commercially permitted stem solution | **FAIL — blocking** | The available SAM-Audio path has gated checkpoint access and commercial rights have not been independently cleared. No ready-to-run commercially cleared alternative was verified. Obtain legal/vendor approval, configure an approved model/service, then validate real stems from an existing audio file, gains, reconstruction, and playback. |
| Section timing / beat alignment | **PARTIAL** | Section timestamps are prompt guidance and are recorded in manifests; exact section boundaries and beat alignment are explicitly not measured or applied. Do not claim beat alignment. |
| Narration ducking | **PARTIAL** | Offline tests cover alignment and the signal-level duck curve (nominal 12 dB, 50 ms attack, 250 ms release). The remote narration API call signature bug was fixed and a bounded remote-fetch regression test passes. The curve is not voice-activity detection, and no live narration upload was submitted in this audit. Validate with representative speech/music assets before production claims. |
| Seamless loop | **PARTIAL** | Offline tests and the mock batch case exercise a 250 ms edge crossfade and endpoint-discontinuity measurement. This tests an endpoint metric only; musical continuity is not guaranteed. |
| Licensing and provenance | **PARTIAL** | Manifests record provider/model, source-file metadata, request, measured audio properties, processing, and provider terms references. They explicitly disclaim rights clearance. Provider-plan rights and SAM commercial rights require legal review for the intended use. |

## Priority 3 — Integration and deployment

| Requirement | Status | Evidence and remaining action |
|---|---|---|
| Docker Compose startup and Docker build | **PARTIAL / pending local execution** | Replaced the unavailable MinIO image with official SeaweedFS 4.50, pinned to an active Docker Hub multi-architecture digest; verified upstream Apache-2.0 licensing, documented S3 operations, bucket setup, persistent `/data`, and `/healthz`. Compose also retains official Node `22-alpine`. Docker CLI is unavailable here, so pulls, `docker compose config --quiet`, builds, startup, service status, and live health have not been verified. Run the requested local Docker commands before considering this resolved. This remains a development stack, not production deployment. |
| One-command tests without provider keys | **PASS** | Ran `.\run_tests.ps1` with both provider-key environment variables explicitly blank; it selected the mock provider and completed tests plus the mock end-to-end lifecycle. |
| OpenAPI documents vs running FastAPI | **PASS** | Both `openapi.yaml` and `docs/openapi.yaml` were regenerated from `app.openapi()` and compared structurally to the running `/openapi.json`; all 11 paths, methods, operations, and component schemas match. |
| Environment-driven configuration / `.env.example` | **PARTIAL** | Pydantic settings load environment configuration, including local CORS/storage settings and the empty-by-default `OUTBOUND_URL_ALLOWED_HOSTS` allowlist. Provider-key, webhook-secret, and S3 sample credentials are blank/removed. Configure production secrets/origins/storage explicitly; webhook callbacks are intentionally skipped without `WEBHOOK_SECRET`. |
| Structured logs and correlation IDs | **PASS** | Backend emits JSON request logs with route, status, duration, job ID, and correlation ID; correlation IDs are returned in response headers. Tests and live HTTP validation passed. |
| Audio output storage and manifests | **PARTIAL** | SQLite job/idempotency/rate-limit state and audio/manifests are persisted locally; restart/retrieval tests passed. Compose now declares separate backend-data and audio-data named volumes. When configured, S3 writes are required for job success and cleanup errors are logged; the API still reads local files and has no S3 recovery/read path. Docker volume persistence was not executed. |
| Batch runner | **PASS (mock only)** | Ran `scripts/batch_runner.py` with `test_pack/inputs/demo_run.json`: 1/1 succeeded, downloaded WAV and manifest, and recorded measured values. |
| Secrets not committed/exposed | **PARTIAL / unresolved** | A credential-shaped value previously found in `.env.example` was removed and the webhook-secret default removed. The scan of non-secret workspace source found no credential-shaped token matches; `.env` was not scanned or disclosed. Rotate the former provider value if genuine. There is no `.git` metadata, so tracked status/history and whether secrets are committed cannot be verified. The API has no authentication; provider configuration and job endpoints must not be exposed publicly as-is. |
| MP3 support | **FAIL / unavailable here** | `ffmpeg` is not installed; the MP3 encoder test was skipped. MP3 output requires FFmpeg with `libmp3lame`; install an appropriately licensed build in the target image and run encoding/decoding tests there. |

## Priority 4 — Test and report results

| Check | Status | Result |
|---|---|---|
| Full backend test suite / one-command runner | **PASS** | `.\run_tests.ps1`: **117 passed, 2 skipped, 1 warning**. Skips were the live Stability integration and MP3 encoder test. The warning is an upstream Starlette/AnyIO deprecation. The runner’s mock end-to-end lifecycle also passed. |
| Frontend lint | **PASS** | `Set-Location .\frontend; npm run lint` completed successfully. |
| Frontend production build | **PASS** | `Set-Location .\frontend; npm run build` completed TypeScript checking and Vite production build successfully. |
| Image pulls / Compose config / build / startup | **PARTIAL / pending local execution** | Registry metadata confirms `chrislusf/seaweedfs:4.50` and its pinned manifest digest are active, and `node:22-alpine` remains an active official tag. Docker CLI is unavailable, so actual pulls, `docker compose config --quiet`, build, startup, `ps`, and live health have not been verified. |
| Backend HTTP end-to-end | **PASS (mock only)** | Fresh Uvicorn process: health/readiness, structured validation error, job lifecycle, idempotency, audio download, manifest, and two concurrent mock jobs passed. The audit UI also called health, jobs, and provider status. |
| Batch end-to-end | **PASS (mock only)** | One existing demo case completed and saved the WAV and manifest. |
| Real paid provider generation / credit status | **PARTIAL** | No paid generation was submitted and no credits were consumed by this audit. At least one read-only balance GET occurred while checking provider status after the backend's selected provider changed to Stability. The archived AI WAV was inspected offline only; live generation authentication, availability, and repeatability remain unverified. |
| Frontend browser walkthrough | **PARTIAL** | No frontend E2E test script is defined in `frontend/package.json`. Manually inspected the Studio, History, Results, Narration Mixer, and API Reference at 1440, 1024, 768, and 390 px; no horizontal overflow was observed, mobile navigation/history worked, and empty states rendered. A generation was not submitted: the UI displayed depleted Stability credits, and the mock-generate button was unavailable in that observed state. Successful result actions were verified by the mock HTTP lifecycle, not through browser playback. |

## Commands and checks run

- `$env:STABILITY_API_KEY = ''; $env:ELEVENLABS_API_KEY = ''; .\run_tests.ps1` — backend pytest and mock E2E with provider-key environment variables blank; final result **117 passed, 2 skipped, 1 upstream deprecation warning**.
- `Set-Location .\frontend; npm run lint; if ($?) { npm run build }` — both passed.
- Started `uvicorn app.main:app` on `127.0.0.1:8001` with `MUSIC_PROVIDER=mock` and isolated temporary output storage; live HTTP health/readiness, structured errors, lifecycle, idempotency, concurrency, manifest, and audio checks passed. Started Vite at `127.0.0.1:5175` for browser inspection. Both audit server sessions were stopped afterward; the unrelated listener on port 5174 was left untouched.
- `scripts\batch_runner.py --input .\test_pack\inputs\demo_run.json --output-dir <audit-temp> --api-url http://127.0.0.1:8001 --timeout 30 --poll-interval 0.1` — 1/1 succeeded.
- Compared parsed `openapi.yaml` and `docs/openapi.yaml` to `GET /openapi.json` — exact structural matches, 11 paths each.
- Inspected `backend/generated_audio` WAV and manifest locally — no provider request.
- `Get-Command docker` and `docker --version` — Docker CLI unavailable.
- Docker Hub official metadata: `library/node:22-alpine` — tag active with multi-architecture manifests; `minio/minio:RELEASE.2024-01-18T22-51-28Z` — HTTP 404; `chrislusf/seaweedfs:4.50` — active multi-architecture tag, manifest digest pinned in Compose. These are registry metadata checks, not Docker pulls.
- Official SeaweedFS source and documentation: Apache-2.0 license; S3 `HeadBucket`, `CreateBucket`, object put/get support; `weed mini` supports `-s3.config`, `-bucket`, and persistent `/data`; `/healthz` endpoint and official image `curl` dependency verified from upstream docs/source.
- Official MinIO upstream README/release docs — community repository unmaintained, archived 2026-04-25 and community distribution source-only; AIStor container requires an active license for production. Quay registry checks returned HTTP 401, so no Quay tag was confirmed pullable.
- `docker pull node:22-alpine`, `docker pull chrislusf/seaweedfs:4.50`, `docker compose config --quiet`, `docker compose build`, `docker compose up -d`, `docker compose ps`, and `docker compose logs` — not run because the Docker CLI is unavailable; no containers or volumes were changed.
- `compose.yaml` parsed successfully with PyYAML, and its pinned object-store image, S3 endpoint, health-gated backend dependency, and separate data volumes were asserted. `scripts/seaweedfs-entrypoint.sh` passed `bash -n` after normalizing the script to LF.
- `Get-Command ffmpeg` — unavailable.
- Workspace credential-shaped token scan over non-secret source — no matches; `.env` intentionally not printed or scanned.
- Python bytecode compilation — passed (the exact command was not retained in the audit log).
- Browser viewport walkthrough — 1440, 1024, 768, and 390 px; no horizontal overflow observed.

## Required actions before reconsidering deployment

1. Supply the Engineering Assessment 1 PDF for an exact criterion-by-criterion crosswalk.
2. Rotate the provider key that appeared in the prior sample file if it was real; verify its status through the provider account without sharing the value.
3. Add authentication/authorization, rate limits, and tenant isolation before exposing any API endpoint publicly. The outbound URL allowlist is defense-in-depth only; pin validated DNS results to the connection or use a controlled egress proxy to address the remaining DNS-rebinding window.
4. Replace in-memory jobs/in-process work with durable job processing and implement a tested persistent storage/retention strategy.
5. Run `docker compose config --quiet`, `docker compose build`, `docker compose up -d`, and `docker compose ps` in the VS Code terminal. Confirm that object-store and backend report healthy, then inspect `docker compose logs object-store backend` and verify S3 bucket/object operations before using the stack. No command has been claimed as passed in this audit.
6. If a previous MinIO volume contains needed objects, plan a separate S3-level migration from the preserved read-only `minio_data` volume; do not attach it as SeaweedFS’s `/data` or delete it.
7. Add FFmpeg/libmp3lame in the target environment if MP3 is required, then run MP3 integration tests.
8. Resolve stem-model access and commercial licensing; validate real, distinct instrument stems and full-mix reconstruction without fake data.
9. Add vocal detection or documented human acceptance for instrumental-only output, and test representative real-provider results without automatic retries.
10. Treat `/ready` as credential/store readiness only until provider connectivity and billing availability are explicitly represented.

**No public deployment is approved by this audit.**

## GitHub push and deployment readiness follow-up — 2026-11-05

This follow-up covers the user's supplied repository, [Mowlidharan25/Incubrix_AI_music_generator](https://github.com/Mowlidharan25/Incubrix_AI_music_generator), and the current local workspace. It does not stage, commit, push, deploy, start Compose, or make paid provider calls.

### Environment and secret findings

- The local workspace is **not a Git repository**: `git status --short` returned `fatal: not a git repository`. The supplied GitHub repository currently has no default branch or commits; GitHub returned `409 Git Repository is empty`, and branch listing was empty. Consequently there is no local index/history or remote commit history available to inspect for already-tracked secrets.
- The root `.env.example` contained a credential-shaped Stability value. It has been replaced with an empty `STABILITY_API_KEY=` entry. `ELEVENLABS_API_KEY=` is also blank. The template's S3 values are clearly identified local-only placeholders and are not production credentials.
- Existing root `.env`, `backend/.env`, and `frontend/.env` were preserved and their values were not printed or changed. `.gitignore` excludes `.env` and `.env.*`, while allowing `.env.example`; it also excludes generated audio, batch outputs, common SQLite files, logs, caches, build output, and dependency directories. Because this workspace has no Git metadata, Git's actual ignore/tracking behavior could not be verified.
- A credential-pattern scan of source, docs, and examples after the change returned no matches. Local secret environment files were intentionally excluded from that scan. This does not inspect the remote's past history.
- Treat any Stability key previously shown in a screenshot or accidentally published in an earlier sample as compromised. Revoke it with Stability and create a replacement only in the backend environment or deployment secret manager. If such a value ever entered a Git commit, rotate it first and plan a history purge before making that repository public; no history purge was performed here.

### Environment variable inventory

Backend settings are loaded from environment variables by `backend/app/core/config.py`; defaults allow API startup and mock-based tests without provider or S3 secrets.

| Variable(s) | Use and requirement |
|---|---|
| `APP_NAME`, `ENVIRONMENT` | Optional app metadata/runtime mode; local defaults exist. |
| `CORS_ORIGINS` | Defaults to local Vite origins. Set to the exact HTTPS frontend origin for deployment. |
| `MAX_REQUEST_BYTES`, `MAX_IDEMPOTENCY_KEY_LENGTH` | Optional request/idempotency limits with defaults. These are not rate limits or authentication. |
| `MUSIC_PROVIDER` | Defaults to `stability`. Set explicitly for the provider used by the deployment; do not switch providers silently. |
| `STABILITY_API_KEY` | Required only for live Stability generation. Keep backend-only; account must have available credits. No generation or billing request was made in this follow-up. |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_MODEL_ID` | `ELEVENLABS_API_KEY` is required only when `MUSIC_PROVIDER=elevenlabs`; model selection is optional. ElevenLabs is not the default and no ElevenLabs key/account is required to run the application or tests. Live use is usage/credit dependent and was not tested. |
| `PROVIDER_TIMEOUT_SECONDS`, `MAX_GENERATED_AUDIO_BYTES`, `MAX_NARRATION_BYTES` | Optional provider timeout and response/upload bounds; defaults are configured. |
| `AUDIO_STORAGE_DIR` | Optional local storage location. For production, a durable mounted volume is necessary for a single backend instance; jobs themselves are still in memory. |
| `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | Optional backend S3 mirroring configuration. In the **local Compose stack only**, `S3_ACCESS_KEY` and `S3_SECRET_KEY` are mandatory for SeaweedFS startup. The values in root `.env.example` are placeholders. |
| `WEBHOOK_SECRET` | Optional; required to send authenticated completion callbacks. Without it, callbacks are skipped. |
| `OUTBOUND_URL_ALLOWED_HOSTS` | Optional JSON list of exact HTTPS hostnames for remote narration/callback features; empty by default, which rejects these URLs. DNS rebinding is not fully prevented. |
| `VITE_API_BASE_URL` | Frontend-only, public API URL. It must not contain credentials. |

The test-only `RUN_STABILITY_INTEGRATION_TEST=1` explicitly enables a paid Stability integration test. It was not set. `run_tests.py` sets `MUSIC_PROVIDER=mock` and its own Python path/encoding environment for deterministic zero-credit tests.

### Provider/API key handling changes

- Removed the provider-key input from the frontend. The UI now tells operators to configure backend secrets and refresh provider status.
- The frontend's provider-selection request now sends only the provider name.
- The backend provider request schema forbids extra fields, including `api_key`, and no longer mutates provider keys from request bodies. A regression test verifies that a submitted key is rejected and does not change backend settings.
- The GET status response exposes only configuration status/credit status, not key material. Provider selection and other API routes are still unauthenticated; CORS is not an authentication mechanism.
- Changed S3 storage failure logging to record only the failure type/object identifier rather than raw exception strings. Provider credentials are not directly logged by the provider adapters.

### Storage, security, and deployment status

| Requirement | Status | Evidence / remaining blocker |
|---|---|---|
| Root environment template contains placeholders only | **PASS** | Provider-key entries are blank; S3 entries are labeled local placeholders and contain no credential-shaped values. |
| `.env` ignore policy | **PARTIAL** | Ignore rules are present and existing local files were preserved, but Git ignore/tracked status cannot be checked without local `.git` metadata. The remote repository is empty. |
| Provider credentials remain backend-only | **PASS (code/tests)** | No frontend key-entry field or request parameter remains. The API rejects extra `api_key` input; regression test and OpenAPI schema check pass. |
| Stability is primary; ElevenLabs remains optional | **PASS (configuration)** | Stability remains the default. ElevenLabs needs its key only if explicitly selected. Neither provider was called during validation. |
| Real provider connectivity, billing, and generation | **NOT TESTED** | No paid calls were made. Replace/revoke the exposed key before any live test; confirm provider credits independently. |
| S3 object storage as local Compose dependency | **PARTIAL** | Compose expects the two root local S3 variables and defines SeaweedFS persistence. Docker is unavailable here, so Compose parsing/build/startup and object operations were not verified. |
| Production audio persistence | **FAIL / BLOCKING** | Local SQLite and local audio survive only when their host paths/volumes are retained. The restart test verifies a local filesystem, not a host failure, ephemeral hosting, backups, or multi-instance operation. S3 is not a read/recovery path and no production object store, database, queue, or cloud resource is configured. |
| CORS and request bounds | **PARTIAL** | Local origins and bounded body sizes are configured. Production origin must be explicitly configured. There are no rate limits, quotas, tenant isolation, or API authentication. |
| Public deployment security | **FAIL / BLOCKING** | Single-owner signed-cookie auth, CSRF checks, owner-scoped job/audio access, SQLite-backed request limits/quotas, bounded body sizes, and a one-active-job default are implemented and covered by tests. Controls are single-host only; production TLS/cookie/CORS configuration, proxy behavior, distributed controls, backup/retention policy, and live deployment checks remain unverified. Do not expose publicly. |
| GitHub tracked-secret/history audit | **NOT TESTED** | Local workspace has no Git repository; remote repository is empty, so neither tracked status nor commit history exists to scan. |
| Public GitHub push readiness | **PARTIAL** | No files were staged, committed, or pushed. Before initializing/pushing, rotate the exposed provider key and verify `.env` files are ignored and absent from the index. |

### Changes made in this follow-up

- `.env.example`
- `.gitignore`
- `backend/app/api/routes.py`
- `backend/app/services/audio_storage.py`
- `backend/tests/test_api.py`
- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `openapi.yaml`
- `docs/openapi.yaml`
- `README.md`
- `docs/ARCHITECTURE.md`
- `run_tests.py`
- `PRE_DEPLOYMENT_AUDIT.md`

No `.env` file or dependency manifest was modified.

### Commands executed and results

- First `.\run_tests.ps1` attempt on C: — **failed due to disk exhaustion**, with about 0.11 GB free. Pytest reported 117 passed, 1 failed, 2 skipped; the failure was an `OSError: [Errno 28] No space left on device` while writing test audio. The runner's failure message then raised a Windows cp1252 `UnicodeEncodeError` due to an emoji; `run_tests.py` was changed to use ASCII for that failure marker.
- Reran `.\run_tests.ps1` with `AUDIO_STORAGE_DIR`, `TEMP`, and `TMP` redirected to the isolated D: validation directory — **118 passed, 2 skipped, 1 upstream Starlette deprecation warning**. The runner's end-to-end mock workflow passed health/readiness, mock job completion, audio download, and manifest checks. No external provider calls or credits were used.
- `npm run lint` from the project root — **failed** because there is no root `package.json`. Then `Set-Location C:\Incubrix_music_problem\frontend; npm run lint` — **failed** because the existing workspace's `oxlint` executable is missing. In an isolated copy of `frontend/` on D:, ran `npm ci --no-audit --no-fund`, `npm run lint`, and `npm run build` — **all passed**; TypeScript compilation and Vite production bundle completed. The project manifests were not changed. The original C: workspace was not used for the build because of low free space and missing local `oxlint`.
- Regenerated `openapi.yaml` and `docs/openapi.yaml` from the FastAPI schema. Parsed comparison — **structurally equal**; `ProviderConfigRequest` has no `api_key` property and declares `additionalProperties: false`.
- Safe credential-pattern scan over source/docs/examples — **no matches** after correcting `.env.example`; local secret environment files were excluded and not printed.
- `Get-Command docker` — **Docker command not found**. Therefore `docker compose config --quiet`, Docker build/startup, health checks, and container logs were **not run**.
- `git status --short` — **not available** (`fatal: not a git repository`). The GitHub repository API reported the supplied remote is empty; no push or commit was attempted.

### Exact next actions

1. Revoke any Stability key previously shown in a screenshot or sample and store a replacement only in `backend/.env` locally or a deployment secret manager.
2. Before pushing, initialize/connect the intended local repository yourself, confirm `.env`, `backend/.env`, and `frontend/.env` are ignored and untracked, then inspect `git status` and scan the staged diff for secrets. The supplied GitHub repository is currently empty.
3. Run the Docker commands on a machine with Docker installed: `docker compose config --quiet`, `docker compose build`, `docker compose up -d`, and `docker compose ps`; verify health and logs without printing credentials.
4. Select a production storage/database/queue design. Use durable storage for generated audio and implement/read-test retrieval from that storage; S3 mirroring alone is not sufficient.
5. Implement authentication, authorization, request quotas/rate limits, and tenant isolation before public deployment. Configure exact production CORS origins and outbound URL allowlists.
6. Only after key rotation and explicit approval, test a real provider request and the required audio/stem criteria. Do not automatically retry failed paid jobs.

**Deployment remains blocked. No public deployment, GitHub push, paid API call, or local-secret modification was performed.**

## Hosting deployment assessment — 2026-10-25

This section supersedes the earlier statements in this report that local Git metadata and the remote repository were unavailable/empty. It reflects the present workspace and was verified after the repository was initialized and pushed. No hosting account, service, storage bucket, public URL, or deployment was created.

### Current repository and secret check

- Local repository root: `C:\Incubrix_music_problem`; branch `main`; worktree clean at audit start.
- `origin` points to `https://github.com/Mowlidharan25/Incubrix_AI_music_generator.git`.
- Local `HEAD` and GitHub `main` both resolved to `b42f1110e2e93d352f401f0ed56587ccc3ecdef0`. No pull, push, or commit was made during this deployment assessment.
- The repository contains 74 tracked files. A safe scan of tracked text found no credential-shaped strings and no tracked non-example `.env` files. `git check-ignore` confirmed root `.env`, `backend/.env`, and `frontend/.env` are ignored. `.env.example` files are tracked by design and contain placeholders.
- These checks cover current tracked files and the current remote head, not credentials in external screenshots or any future local changes. Rotate the previously exposed Stability key regardless of the scan result.

### Application deployment shape

- **Frontend:** React/Vite static site. Production build is `npm ci && npm run build`; output directory is `frontend/dist`. `VITE_API_BASE_URL` is compiled into client JavaScript and must be the public HTTPS API origin. It is public configuration, never a place for provider credentials. The current fallback `http://127.0.0.1:8000` is for local development only and is invalid for public clients.
- **Backend:** FastAPI under `backend/`, Docker build context `backend/`, health endpoint `/health`; production must run Uvicorn without `--reload`, bind to `0.0.0.0`, and honor the hosting platform's injected port. Configure `CORS_ORIGINS` to the exact HTTPS frontend origin. Keep `STABILITY_API_KEY` and optional `ELEVENLABS_API_KEY` only in backend secret settings.
- **Local object store:** Compose uses digest-pinned SeaweedFS and local S3 credentials from the root `.env`; the development Compose stack binds service ports to loopback and starts Vite's dev server. It is not a production deployment definition. Docker CLI is unavailable in this workspace, so no Compose/build/container health claim is made.
- **Provider:** Stability remains the default; no live provider request or credit check was made during this deployment assessment. ElevenLabs is optional only when explicitly selected. A valid key is not proof of provider balance/availability.
- **Persistence (updated below):** The application now uses SQLite for authoritative job/idempotency/rate-limit state and local files for audio/manifests. Both require the same retained host/volume across restart; SQLite is single-host, work remains in-process, and S3 has no read/recovery path. See the implementation/results section below.
- **Critical security blockers (updated below):** the initial unauthenticated routes have been protected for a single owner, with owner scoping and local rate/quota controls. The private-instance design is not a distributed or production identity system. Keep public deployment blocked until the remaining controls and persistence limitations below are addressed.

### Free-tier-aware target plan (not configured or deployed)

1. **Frontend candidate — Cloudflare Pages static hosting.** Official Pages build settings list React (Vite) as `npm run build` with output `dist`; for this repository set the project root to `frontend`. Set the public `VITE_API_BASE_URL` to the eventual backend HTTPS origin at build time and redeploy the frontend whenever that value changes. Pages has a free offering, but a public Pages deployment is still a public product and must wait for the security gate. Official setup: [Cloudflare Pages build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/).
2. **Backend candidate — container-capable managed web service.** Render documents a free web-service plan suitable for testing/hobby use, but explicitly says free instances are not for production: they spin down after idle time and have ephemeral filesystems; free PostgreSQL expires after 30 days. Therefore a free Render backend is not an acceptable production home for this audio service. For a real launch, select a paid always-on backend plan only after approval, configure a persistent worker/job solution and health check `/health`, and confirm timeouts/resource limits for long generations. Official limitations: [Render free services](https://render.com/docs/free).
3. **Audio object storage candidate — Cloudflare R2.** Official R2 documentation currently lists a Standard free allowance of 10 GB-month storage, 1 million Class A operations, 10 million Class B operations, and free egress; usage beyond that can incur charges. This may reduce cost for an early workload, but it is not a guaranteed permanently free unlimited tier. The project is not ready to use R2 as its source of truth: implement and test an object-store read path, durable metadata/job storage, object access authorization or short-lived signed URLs, retention/deletion, and recovery behavior first. Do not create a bucket or add billing until the owner explicitly approves. Official docs: [R2 pricing](https://developers.cloudflare.com/r2/pricing/) and [R2 boto3 configuration](https://developers.cloudflare.com/r2/examples/aws/boto3/).
4. **Persistent job store:** SQLite persistence is now implemented for a single host, but a durable worker queue and managed/replicated database are not. Do not scale to multiple instances or use ephemeral disks; both exceed the approved local-only/₹0 scope.
5. **Deployment order after the gate:** implement and test authentication/authorization, rate limits/quotas and user isolation; durable jobs and object reads/writes; provider secret injection and key rotation; production CORS; storage lifecycle/backups; observability and health/readiness semantics; then perform a private staging deployment and verify real health, persistence across restart, billing/error paths, and audio download. Only then request explicit approval for a public deployment.

### Required owner dashboard actions

**Do not perform a public deploy yet.** No dashboard actions are required before the blockers are fixed.

After security and persistence work is complete, and after you approve any service that may bill:

- In the frontend host dashboard, connect the `main` branch, choose `frontend` as the root directory, set build command `npm ci && npm run build`, output `dist`, and set the public `VITE_API_BASE_URL` to the actual backend HTTPS URL. The backend URL does not exist yet and must not be guessed.
- In the backend host dashboard, create the web service only after approval, configure a production start command that binds to the platform port (for example `uvicorn app.main:app --host 0.0.0.0 --port $PORT` if the platform provides `PORT`), health path `/health`, exact `CORS_ORIGINS`, and backend-only secrets. Do not paste keys into source code, frontend variables, chat, or logs.
- In the object-storage dashboard, create a private bucket and scoped access credentials only after approval of the provider, retention, and billing policy. Use the actual endpoint/account details shown by that dashboard; do not use example endpoints or commit credentials.
- Verify the final service URL, TLS, health checks, CORS preflight, authenticated API requests, rate limiting, object retrieval after backend restart, and job polling before considering the service usable.

### Exact next step

In VS Code, do not click Deploy or push additional commits. First approve a target architecture and cost boundary: (a) private static preview only, or (b) production backend plus durable database/queue and object storage, with a monthly spend ceiling. The current report does not establish a safe public deployment. Once that choice is made, implement the security and persistence gates before creating hosting resources.

## Single-owner security and local persistence implementation - 2026-10-25

This section supersedes older in-memory/no-auth findings above. The user approved a private single-owner instance using SQLite and local audio volumes. No external service, bucket, database, account, paid API, public deployment, commit, or push was created.

### Phase 1 findings before implementation

- All provider, generation, job, manifest, audio, stem, and narration routes under `/api/v1` lacked authentication and job-owner checks. A caller with a job ID could retrieve job status and media.
- `InMemoryJobStore` held status and idempotency keys only in process memory. Restart lost jobs; generation work ran in-process and had no durable queue or retry recovery.
- Audio and manifests were atomically saved locally. S3 was a write mirror and not a read/recovery path; storage code attempted bucket creation when `head_bucket` failed.
- Existing request validation and body/narration size limits were present, but API rate limits, generation quotas, and active-job limits were not.
- `/health` was safe liveness. `/ready` exposed provider-selection details and checked only in-memory state/configuration, not database, audio-storage, or authentication readiness.
- Compose had no persistent backend database/audio volumes. There was no user-facing delete route; cleanup was internal to failed generation/narration processing.

### Changes implemented

- Added a single-owner sign-in flow with a scrypt password hash, signed HTTP-only session cookie, rotating CSRF token, login-attempt IP throttling, and fail-closed behavior when credentials/session secret are absent or malformed. No administrator password or credentials are hardcoded. API errors do not expose stack traces.
- Protected all `/api/v1` provider/job/audio/narration endpoints. Job create, list, status, audio, stems, manifests, and narration operations are scoped to the authenticated username. OpenAPI now documents the cookie security scheme.
- Replaced authoritative in-memory jobs/idempotency with transactional SQLite. Owner-scoped idempotency keys, status/progress/result/error/warnings, rate-limit counters, daily quota checks, and active-job checks are persisted. Startup changes queued/running jobs to a visible `JOB_INTERRUPTED` failure and never retries a possibly chargeable request.
- Added SQLite-backed per-IP and per-owner fixed-window limits, configurable daily generation quota (default 5 accepted jobs/day), and one active generation per owner by default. Limits fail closed on database errors. These controls do not provide distributed protection and must not be used across multiple instances.
- Kept local audio/manifests as the read source and added named `backend_data` and `audio_data` volumes to Compose. When S3 is configured, storage writes now fail the job instead of silently succeeding on a failed mirror; S3 deletes are attempted during cleanup and logged explicitly. Bucket auto-creation was removed from backend startup. There remains no S3 read/recovery path.
- Updated readiness to check SQLite, local/object storage, auth configuration, and configured provider credentials without exposing the provider name or environment-variable names. It still does not check provider connectivity or credits.
- Added an authenticated frontend sign-in/sign-out gate, credentialed API requests, CSRF headers, and credentialed audio playback. Added auth, ownership, restart/retrieval, rate, quota, concurrency, and storage-outage tests.
- Added `itsdangerous` as an explicit session dependency; updated `.env.example`, README, architecture documentation, Compose persistence volumes, and both generated OpenAPI documents. Existing `.env` files and values were not edited.

### Requirement status after the changes

| Requirement | Status | Evidence / remaining limit |
|---|---|---|
| Missing/invalid authentication and CSRF | **PASS (tests)** | Missing auth returns a structured 503 until configured; invalid session/password and missing CSRF are rejected; session cookie is HTTP-only. Production HTTPS behavior still needs live validation. |
| Cross-owner job/audio/manifest isolation | **PASS (tests)** | A second owner receives 404 for job status, audio, and manifest; the authorized owner can retrieve the same outputs. |
| Rate limit, quota, concurrency | **PASS (single host)** | SQLite limits and quota/concurrency tests pass; the API emits 429 and `Retry-After`. Reverse-proxy IP attribution and distributed limits are not implemented. |
| Input/body/file bounds | **PASS (automated tests)** | Existing schema constraints and request/narration byte limits remain in place; backend suite passes. |
| CORS and session security | **PARTIAL** | Explicit origins, CSRF, secure-cookie production readiness checks, and CORS CSRF header are configured. No browser cross-origin/HTTPS production deployment was available to test. |
| Job state/idempotency after process restart | **PASS (local SQLite test)** | Reopened SQLite retains completed job/idempotency records; interrupted active jobs are marked failed without automatic retry. |
| Audio/manifest retrieval after restart | **PASS (local filesystem test)** | A reopened store and storage instance retrieved a completed WAV and manifest. This proves same-filesystem restart persistence only, not host failure or ephemeral-host durability. |
| Object-store outage / partial output | **PASS (deterministic test)** | Simulated S3 upload failure leaves the job failed without result metadata and cleans local source/output/manifest; actual SeaweedFS/Docker outage was not tested. |
| Audio object-store recovery/read/delete | **PARTIAL** | S3 writes are no longer success-shaped on failure, and cleanup attempts remote deletes. The application still reads from local disk and has no S3 restoration path; no user-facing job DELETE route exists. |
| Docker Compose volumes/startup/health | **NOT TESTED** | `docker compose config --quiet` could not run because Docker is not installed. Build, startup, health, and actual volume-restart tests were not run. |
| Paid provider integration / commercial generation | **NOT TESTED** | Tests use mock/fake responses; no live provider request or credit was used. The previous exposed Stability key still needs revocation and replacement by the account owner. |
| Public deployment readiness | **FAIL / BLOCKING** | Local single-owner SQLite/filesystem is not multi-instance or backup-safe; there is no durable queue, object-store read path, production deployment verification, or distributed rate limiting. Do not deploy publicly. |

### Validation actually executed

- `Set-Location backend; $env:PYTHONPATH = "$PWD;.."; python -m pytest -q --basetemp D:\Incubrix_pytest_backend_final4` - **130 passed, 2 skipped**. Tests used mock/fake providers; no paid request was made.
- `Set-Location frontend; npm ci` - **passed**, 54 packages installed; npm reported zero vulnerabilities.
- `Set-Location frontend; npm run lint` - **passed**.
- `Set-Location frontend; npm run build` - **passed** (`tsc -b` and Vite production build).
- FastAPI `/openapi.json` was fetched with `TestClient`; `openapi.yaml` and `docs/openapi.yaml` were generated from and structurally compared against it - **15 paths match**, including cookie authentication.
- Pylance syntax checks for `auth.py`, `job_store.py`, `routes.py`, and `main.py` - **no syntax errors**; diagnostics for changed backend/auth and frontend `App.tsx` returned no items. Signature compatibility for `SQLiteJobStore.create` found all five call sites compatible.
- `git diff --check` - **passed**; Git emitted only LF-to-CRLF normalization warnings for `frontend/src/App.css` and `frontend/src/App.tsx`.
- `git check-ignore -v .env backend/.env frontend/.env` - all three matched `.gitignore`; `git ls-files -- .env backend/.env frontend/.env` returned no tracked secret files. Tracked-file credential-shape scan found no credential after reducing the `.env.example` S3 entries to explicit short placeholders. `git status --short` lists only the implementation/docs files changed here; nothing was staged or committed.
- `docker compose config --quiet` - **not run successfully**: PowerShell reported `docker` is not recognized. Docker build/up/status and container health are unverified.
- An initial pytest attempt used the nearly-full C: temporary directory and failed with disk I/O/no-space errors; its known pytest-only temporary directories were removed. The final complete suite was rerun on D: and passed. A first full-suite collection attempt from `backend` also lacked the repository root on `PYTHONPATH`; the final command above includes it.

### Remaining blockers and exact next actions

1. Revoke the Stability key previously exposed in the screenshot and provision its replacement only in a private backend environment if/when live paid generation is explicitly approved. Do not use it for the current verification.
2. Generate a unique `AUTH_PASSWORD_HASH` through the hidden password prompt and a high-entropy `AUTH_SESSION_SECRET`; put them in the ignored local environment file. Keep `.env.example` placeholders unchanged. Production additionally needs HTTPS, `AUTH_COOKIE_SECURE=true`, the exact frontend CORS origin, and a reviewed same-site cookie policy.
3. Install/enable Docker Desktop, then run `docker compose config --quiet`, `docker compose build`, and `docker compose up -d` only when the operator wants to start the **local** stack. Verify `docker compose ps`, `/health`, `/ready`, authenticated job/media access, and restart retrieval before relying on the named volumes. Do not delete volumes.
4. Before any public deployment, choose an approved durable metadata/queue and audio-recovery design. Do not put this local SQLite/in-process-work setup on ephemeral or multi-instance hosting; add tested object-store reads, retention/backups, distributed rate limiting, and operational controls first.
5. No full browser E2E or real provider/audio-model acceptance was run. The frontend was linted/built, and API auth/lifecycle tests passed, but production TLS/CORS, actual object-store persistence, and provider behavior remain unverified.
