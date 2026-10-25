# Soundcraft implementation and verification report

## Current status (October 23, 2026)

- **Phase 3 real generation: VERIFIED for one explicitly approved run.** Stable Audio 3.0 returned a WAV, the processed output and source were saved, the output passed measured processing requirements, and the app playback endpoint/player loaded it. This single run does not establish repeatability or broader quality.
- **Phase 5 provider-to-file path: VERIFIED for the same one real WAV.** Measured duration, 48 kHz sample rate, integrated LUFS, and true peak all met the configured targets. MP3 remains unverified.
- `STABILITY_API_KEY` is configured in the local `backend/.env`; its value was not read or disclosed. This confirms presence, not that the key remains valid or unexposed. Revoke any still-active key previously shown in a screenshot.
- Automated tests use mocked provider interactions and synthetic fixtures; the live generation was a separate single request after explicit user approval. This Phase 6/7 implementation made no paid API calls and downloaded no gated model weights.
- **Phase 4: opt-in SAM-Audio integration implemented and offline-tested; real-model validation remains BLOCKED.** Test-fixture WAVs exercise separation plumbing, alignment, manifest, and API behavior. No real SAM stem files were created, no model/weights were downloaded, and no frequency-band or duplicated-mix substitutes were used. The adapter now loads SAM model/processor files with `local_files_only=True` and has a focused test asserting that it cannot fetch missing checkpoint files automatically. Phase 4 is not complete.
- The original assessment rubric/Integration Standard was not available in the workspace, so its detailed acceptance criteria cannot be confirmed.

## Phase 4 feasibility investigation and implementation (October 23, 2026)

- **Candidate model: Meta SAM-Audio.** The official repository describes open-vocabulary audio separation by text/visual/time prompts and outputs a target plus residual. It cites instrument separation evaluation and includes a piano prompt example. The SAM License expressly includes trained weights in “SAM Materials” and grants a worldwide, non-exclusive, royalty-free limited right to use, copy, modify, and distribute subject to its conditions. The inspected terms contain no non-commercial-only restriction, but do not use an explicit “commercial use” phrase; obtain legal review before relying on it commercially. Redistribution requires providing the license. Research publications using the materials must acknowledge them. Model cards require manual access approval and Hugging Face authentication; approval and acceptance must be performed by the account holder.
- SAM-Audio is not a ready-made multi-instrument classifier. Its supported primitive is one prompted target plus a residual; the implementation applies prompts for percussion, bass, and other instruments in sequence and saves the final residual as a fourth track. Model bleed, missed sources, residual content, and sum-to-mixture error must be measured. Instrument labels remain approximate rather than guaranteed.
- Official setup calls for Python 3.11+ and recommends a CUDA-compatible GPU. The smallest listed checkpoint is still about 5.1 GB according to the Hugging Face model API. The required `facebook/pe-av-large` dependency is listed under Apache-2.0 and is 2.23 billion parameters. This machine exposes Intel Iris Xe only; the project venv has no PyTorch, torchaudio, or SAM-Audio, and no Hugging Face token/model access was configured. The model was not installed or downloaded.
- **Commercial service candidate: AudioShake.** Its official product page advertises extracting drums, bass, guitar, piano, winds, strings, and other stems, and says API/SDK integration is available. The reviewed public page did not establish API pricing, output-use rights, or a sufficiently detailed public API contract. No account, vendor terms, or credentials were available, so it was not selected or called.
- Demucs and Spleeter code is MIT-licensed, and their repositories describe multi-stem outputs. The code license does not by itself establish the license or commercial rights for separately distributed pretrained weights; neither model was selected.
- User selected SAM-Audio for the next implementation path. The application now offers opt-in local processing: three prompted targets (percussion, bass, other instruments) plus the last model residual; target/residual splits are sequential, not a dedicated multi-output instrument model. The model and source revisions are pinned in code, and dependencies are isolated in `backend/requirements-stems.txt` rather than the default backend install.
- The job stage runs only after the original processed mix is saved. It stores stems as separate float WAVs at 48 kHz, aligned within 0.05 seconds and duplicated across the mix channel count because the documented SAM result is mono. It uses unity gains to preserve model outputs, measures per-file properties, and computes relative RMS reconstruction error against a mono downmix. The UI provides per-file play/download, volume, mute/solo, and joint playback controls. `music_manifest.json` and job responses record processing status, errors, prompts, paths, measured properties, model/code revisions, gains, license references, and reconstruction metric/limitations.
- Separation failures are distinct from generation failures: the validated original mix remains successful and available, while the stem status is `unavailable` or `failed` with a structured error. No local model weights, credentials, or test-only synthetic data are used to generate production outputs.
- **Remaining Phase 4 gates:** the account holder must obtain gated checkpoint access and locally authenticate; the SAM License must be reviewed for intended commercial use; this machine has Intel Iris Xe but no CUDA-compatible GPU or SAM/PyTorch installation. Benchmarking the model on supported hardware/CPU and validating separation quality, alignment, saved stem playback/download, and measured reconstruction on the existing real generated WAV are still required. Phase 4 remains incomplete. No paid API was used.
- Regression checks after implementation: backend **92 passed, 0 failed, 2 skipped** (the opt-in live Stability request and MP3 encoder path); frontend lint passed; frontend production build passed. No Stability or other paid API was called.

## Phase 4 follow-up: commercial feasibility and cache-only loading (October 23, 2026)

- The existing real provider source and processed WAV are present in `backend/generated_audio` for job `3db2275f-1eb6-4c33-bb70-8699feba8c6f`. The current backend environment has none of `torch`, `torchaudio`, `sam_audio`, `demucs`, `spleeter`, `audio_separator`, or `onnxruntime`; no real stem inference was attempted.
- SAM-Audio is the only local adapter already implemented, but exact checkpoint access remains manually gated and commercial permission is not established by the license text. This host has Intel Iris Xe and no CUDA-compatible GPU. If legal review and account access are resolved, the operator must install a compatible inference runtime and manually download the exact pinned small checkpoint; application loading is now cache-only and reports a missing local checkpoint instead of downloading it.
- No alternative was selected as commercially cleared: Demucs describes 4-/6-source separation, but pretrained-weight commercial rights were not established and the repository is archived; Spleeter describes 4-/5-stem models, but commercial rights for its pretrained weights were not established; Open-Unmix `umxl` weights are CC BY-NC-SA 4.0, and other model weights rely on dataset rights not established here. AudioShake advertises instrument stems, but public materials reviewed did not establish its API terms, pricing, or output-use rights. Thus **no commercially permitted, locally ready method was verified**.
- Documentation was updated to state these licensing/access limitations. The existing UI only requests stem separation when creating a new music-generation job; there is no reprocess-existing-WAV action, so that UI route would call the music provider again. Do not use it to test the saved WAV when avoiding a paid generation. A dedicated no-generation reprocessing route remains an implementation gap.
- Verification after the cache-only change: Python `compileall` passed. `tests/test_stem_separation.py`: **11 passed**, one upstream Starlette deprecation warning. `tests/test_api.py`: **45 passed, 1 failed**; the failing existing test expects the warning `"Loopability was requested as prompt guidance"`, which is unrelated to this stem-loader change. No paid API or model download was performed. The full suite and frontend lint/build were not rerun.
- The real generated WAV has **not** been separated into stems; no stem file, measured reconstruction result, or real stem playback was verified. Phase 4 remains incomplete.

## Implemented in this update

- Audio processing for actual provider bytes: WAV decode/encode, polyphase resampling to exactly 48,000 Hz, requested-duration trim/short pad to an exact sample count, a 20 ms fade-out, integrated loudness targeting, a look-ahead peak limiter, 4x-oversampled true-peak measurement, and post-encoding validation.
- Requested durations are limited to 5–300 seconds and final encoded duration must be within 0.05 seconds. Processing fails clearly for silent, malformed, non-finite, or materially short provider audio, invalid output, or unavailable MP3 encoders. Long silent padding is not used to disguise provider duration mismatch.
- The original provider audio is stored as `<job-id>.source.<format>` and the processed delivery file as `<job-id>.<format>`. Existing playback/download endpoints serve the processed file. The manifest retains requested/measured duration, sample rate, loudness, true peak, and per-requirement status.
- The job response carries requested targets beside measured values. The audio-results UI reports passed, failed, or not measured for duration (±0.05 s), sample rate (exactly 48 kHz), integrated loudness (-16 LUFS ±1 LU by default), and true peak (at or below -1 dBTP).
- One approved live run completed on 2026-10-23. Job `3db2275f-1eb6-4c33-bb70-8699feba8c6f` used a 5-second WAV request. The saved provider source was 44,100 Hz stereo, 5.000 s, 900,060 bytes. The processed WAV was 48,000 Hz stereo, 5.000 s, 1,440,044 bytes. Measured final integrated loudness was **-16.006179 LUFS** and true peak was **-1.952284 dBTP**. Manifest status was `passed` for duration, sample rate, integrated loudness, and true peak.
- Current official pricing lists 8 credits per successful Stable Audio 3.0 generation; the account's billing/credit history was not inspected, so the actual debit was not independently confirmed.
- The audio endpoint returned HTTP 200 with `audio/wav`, and its 1,440,044 response bytes matched the saved processed file. The browser audio element loaded the 5-second resource, reported `readyState=4` with no decode error, and accepted `play()`. This verifies file delivery and browser playback startup; no human listening/subjective quality assessment was performed.
- MP3 output is preflighted before provider generation and requires FFmpeg with `libmp3lame`. The current environment had no FFmpeg, so MP3 encoding and decoding have not been validated.
- Narration upload is implemented as a bounded raw WAV/MP3 request. Audio is decoded, aligned to the music duration with a warning, ducked by a smoothed 12 dB envelope while narration signal is active, normalized, revalidated, stored, and exposed through metadata, playback, and download routes. MP3 narration requires FFmpeg.
- Narration signal activity is measured in 10 ms RMS blocks; blocks at or above -45 dBFS target 12 dB music attenuation with a 50 ms attack and 250 ms release. This is signal-level ducking, not speech recognition or voice-activity detection.
- Optional `loopable` processing applies a 250 ms reverse-head/tail edge crossfade and measures first/last-sample RMS discontinuity from the encoded output. The API/manifest reports `passed` only at or below -60 dBFS. This does not assess musical, harmonic, or rhythmic continuity.
- The manifest records arrangement prompt inputs and an estimated nearest 4/4 bar/time for each section when tempo is provided. It explicitly records that no audio beat grid was measured and section boundaries were not applied to the waveform.
- Final integrated LUFS and true peak are returned only after measurement. A measured loudness miss remains visible as a failed target and warning; no target is reported as passed without measurements.
- SAM-Audio separation is opt-in and has offline plumbing/tests only. The model is still gated and not installed; no real generated music has been separated, heard, or validated. AudioShake remains a possible alternative if its vendor terms/API access are obtained.
- Exact section-boundary application, measured beat alignment, and independent stem regeneration remain unavailable. Stem controls are shown only for validated files from the opt-in SAM path.

## Relevant implementation

- DSP: `backend/app/services/audio_processing.py`
- Local audio and artifact storage: `backend/app/services/audio_storage.py`
- Generation processing lifecycle: `backend/app/services/job_manager.py`
- Narration upload/status/download: `backend/app/api/routes.py`
- Stem processing: `backend/app/services/stem_separation.py`
- Request/response models: `backend/app/schemas/music.py`
- Frontend studio, narration mixer, and experimental stem controls: `frontend/src/App.tsx`
- Offline DSP tests: `backend/tests/test_audio_processing.py`
- Offline stem processing/storage/API tests: `backend/tests/test_stem_separation.py`
- API/lifecycle tests: `backend/tests/test_api.py`
- Provider mocks and optional live test: `backend/tests/test_stability_provider.py`, `backend/tests/test_stability_integration.py`

## Licensing and operational caveats

- Stability's output rights depend on its current terms, plan, and applicable law. This application does not provide legal clearance or guarantee originality or non-infringement.
- The DSP Python libraries are declared in `backend/requirements.txt`. Optional FFmpeg licensing depends on the exact build and compiled codecs; check its terms before redistribution or commercial packaging.
- SAM-Audio checkpoint/code revisions and license references are recorded in the source/manifest, but the model remains gated and no weights were downloaded. The SAM License has no non-commercial-only clause in the reviewed text but does not expressly state commercial use; obtain legal review before commercial deployment. The optional requirements file is not the default runtime.
- Narration assets are uploaded to and stored on the local application backend. The app does not send narration to Stability AI. Storage is local and has no retention/authorization policy; do not expose this development API publicly.

## Phase 6/7 focused verification

- Backend `compileall`: passed.
- Requested loop/arrangement focused backend tests: **3 passed, 0 failed** for loop crossfade/endpoint measurement, unchanged non-loop output reporting, and background job/manifest arrangement integration.
- Narration focused backend tests: **6 passed, 0 failed**, covering smoothed duck-curve behavior, successful upload/mix/metadata/download, missing or malformed input, unsupported/oversized uploads, narration duration alignment, and processing-failure cleanup.
- Focused backend runs emit one pre-existing Starlette/AnyIO deprecation warning.
- Previous full-suite baseline before this update: **92 passed, 0 failed, 2 skipped**. The skipped tests were the opt-in live Stability AI test and MP3 encode/decode test (FFmpeg/libmp3lame unavailable). This update did not make any provider request.
- Frontend `npm run lint`: passed after the saved narration-mix status UI update.
- Frontend `npm run build` (`tsc -b && vite build`): passed after the saved narration-mix status UI update.
- Static OpenAPI YAML parsed successfully and includes the `LoopValidation` schema plus manifest and narration behavior descriptions. Generated FastAPI OpenAPI includes `loop_validation` and narration upload/status/download operations. No formal OpenAPI validator beyond YAML parsing and schema assertions was run.
- FFmpeg and `libmp3lame` were unavailable, so MP3 encode/decode was skipped. Phase 3 and the Phase 5 WAV path now have one successful real-output verification, recorded above; repeated real generation and MP3 output remain unverified.

Test-only tone output does not verify the real provider, commercial musical suitability, or assessment completion.

## Integration Standard implementation and verification (October 24, 2026 update)

- **Integration Standard #1 (One-command test runner)**: Implemented via `run_tests.py` and `run_tests.ps1`. Automatically sets `PYTHONPATH`, executes all unit and integration tests, and runs an end-to-end simulation asserting job lifecycle, audio DSP, and manifest generation with zero API keys required (`MUSIC_PROVIDER=mock`). All **101 tests passed**.
- **Integration Standard #2 (Documented HTTP API)**: `openapi.yaml` (OpenAPI 3.1) placed in root and `docs/openapi.yaml`, matching all `/api/v1/` routes, schemas, headers, error models, and download links.
- **Integration Standard #3 (Background jobs & signed webhook callback)**: Implemented in `JobManager`. When `callback_url` is passed in `JobCreateRequest`, the worker dispatches a webhook POST upon job completion (succeeded or failed) signed with HMAC-SHA256 in header `X-IncuBrix-Signature` using `WEBHOOK_SECRET`.
- **Integration Standard #7 (S3-compatible storage and narration_url input)**: Added MinIO service to `compose.yaml`. `AudioStorage` automatically mirrors generated mixes, stems, ducked mixes, and manifests to S3/MinIO when configured (`S3_ENDPOINT_URL`). When `narration_url` is provided in `POST /api/v1/music/jobs`, the engine downloads the voiceover, applies smoothed 12 dB ducking, saves `mix_ducked.wav`, and returns `mix_ducked_download_url`.
- **Integration Standard #10 (Batch runner CLI)**: Implemented in `scripts/batch_runner.py`. Reads test input JSON files or directories, manages async jobs, downloads all assets (`mix.wav`, stems, `mix_ducked.wav`, `music_manifest.json`) into structured folders, and generates `batch_summary.json`. Verified with test input packs: `test_pack/inputs/10_diverse_prompts.json` and `test_pack/inputs/hard_cases.json`.

## Remaining candidate-side submission checklist

1. **Submission Form (Excel file)**: Complete every row with links to verified code, logs, and measured numbers.
2. **Real Stem Verification (Must-Have 2)**: Resolve commercial clearance and environment setup (GPU / PyTorch / weights) to separate real stems from audio without synthetic mocks.
3. **Live Demo Deployment**: Deploy the containerized API and frontend to a publicly accessible URL with rate limiting.
4. **Demo Video**: Record the ≤ 8-minute unedited walkthrough (clean run, handled failure, test suite, and code architecture).
5. **Test & Audio Pack**: Run the batch runner against live generation to produce the 10 real audio packs and logs for submission.

