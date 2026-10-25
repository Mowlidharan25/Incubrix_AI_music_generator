# Project phases

## Phase 1 - Requirements, architecture, and UI shell

**Status: Partial.** The React/Vite interface, FastAPI app, request schemas, architecture notes, and a phase map exist. The original assessment rubric/Integration Standard is not present in this workspace, so exact criterion-by-criterion compliance cannot be certified.

## Phase 2 - API and background jobs

**Status: Implemented and locally tested; not production durable.** Validated request creation, in-memory job lifecycle, idempotency, structured errors, correlation IDs, job history, status, audio/manifest delivery, and readiness/health endpoints exist. Jobs and workers are lost on backend restart.

## Phase 3 - Real AI music generation

**Status: One explicitly approved live Stable Audio 3.0 generation verified on 2026-10-23.** The asynchronous endpoint returned a generation ID; polling retrieved real WAV audio. Job `3db2275f-1eb6-4c33-bb70-8699feba8c6f` completed successfully, and its original provider source and processed output were saved separately. The final output passed the duration, sample-rate, loudness, and true-peak requirements, was available through the playback endpoint, and its manifest was checked. This is evidence for one run only, not a guarantee of repeatability or musical quality. Do not make further paid requests without explicit approval.

## Phase 4 - Genuine instrument stems

**Status: Opt-in SAM-Audio integration implemented and offline-tested; real-model validation BLOCKED.** The optional local path prompts sequentially for percussion, bass, and other instruments and stores the final model residual as a fourth WAV. These are target/residual results from an open-vocabulary model, not guaranteed pure instrument classes. The pipeline validates duration (within 0.05 s), 48 kHz sample rate, channel layout, file persistence, structured unavailable/failure states, and relative RMS reconstruction error. Response/manifest contain paths, prompt/model/code revisions, gains, measured properties, licensing references, and error limitations. UI supports each stored file, combined playback, volume, mute, and solo. In offline fixtures only, four tracks and API download/manifest behavior passed tests. The checkpoint is gated; this host has no SAM/PyTorch installation or CUDA-capable GPU. The loader now uses `local_files_only=True`, requiring an explicit operator download after exact-checkpoint approval. SAM commercial-use permission is not established and requires legal review. Demucs/Spleeter weight rights are unverified, Open-Unmix umxl weights are non-commercial, and AudioShake's public pricing/API/output-use terms remain unverified. No commercially permitted ready-to-run method is verified. No weights have been downloaded and no real generated music has been separated, listened to, or validated. Phase 4 remains incomplete pending access, licensing, runtime, and real-audio validation.

## Phase 5 - Exact duration, resampling, loudness, and true peak

**Status: One real WAV output passed end-to-end verification on 2026-10-23; MP3 remains unverified.** For the 5-second approved generation (job `3db2275f-1eb6-4c33-bb70-8699feba8c6f`), measured final values were 5.000 seconds, 48,000 Hz, stereo, -16.006179 LUFS, and -1.952284 dBTP. All four manifest requirements were `passed`. The playback endpoint returned the same saved WAV bytes and browser decoding/playback startup succeeded. Requests remain restricted to 5–300 seconds and exactly 48,000 Hz. Standard processing trims or short-pads, applies a fade-out, targets integrated LUFS, applies look-ahead peak limiting, and measures encoded output; opt-in loop processing substitutes an edge crossfade for the fade. Offline WAV tests use synthetic fixtures. MP3 requires FFmpeg with `libmp3lame`, unavailable during this validation; MP3 encoding/decoding remains unverified. This live result verifies one run only.

## Phase 6 - Narration upload and ducking

**Status: Implemented and focused offline-tested.** A bounded raw WAV/MP3 upload endpoint validates audio content, aligns narration to the music duration with an explicit trim/pad warning, applies a smoothed 12 dB music duck (50 ms attack / 250 ms release), normalizes and validates the mix, stores the mix and metadata, and exposes playback/download endpoints. MP3 decoding and MP3 output require optional FFmpeg. This is file-level signal-level ducking, not speech recognition or voice activity detection. The UI can retrieve and display a previously saved mix only when the backend's stored metadata and output are still available.

## Phase 7 - Sections, loop behavior, and manifest

**Status: Implemented and focused offline-tested; content guarantees remain limited.** The manifest records section mood/times, tempo/key prompt targets, estimated nearest 4/4 bars when tempo is supplied, and explicitly marks the audio beat grid and applied section boundaries as unmeasured/not applied. Optional loop mode applies a 250 ms reverse-head/tail crossfade and validates the encoded first/last-sample-frame RMS discontinuity against -60 dBFS. This is endpoint continuity evidence only, not a musical seamless-loop guarantee. Real beat detection, exact section boundaries, and independent stem regeneration are not implemented.

## Phase 8 - Tests and edge cases

**Status: Focused tests cover new loop-crossfade and arrangement-manifest behavior; previous offline coverage includes schemas, provider mocks, processing conditions, upload limits, narration mismatch, and output cleanup.** The latest Phase 4 follow-up ran compileall and all 11 stem tests passed; `test_api.py` had 45 passed and one unrelated existing loop-warning assertion failed. The full backend suite was not run. The opt-in integration test remains skipped in automated runs to avoid paid requests; the separately approved provider run is recorded above. MP3 remains unverified because FFmpeg is unavailable; no frontend test suite is defined. Exact results are in REPORT.md.

## Phase 9 - Docker and deployment

**Status: Development configurations exist; container runtime validation unavailable.** Dockerfile and Compose configuration are present. No Docker runtime validation was performed in this update. In-memory job state, in-process workers, local disk assets, no authentication, and lack of production isolation remain deployment gaps.

## Phase 10 - Documentation and delivery

**Status: Updated; assessment evidence remains incomplete.** README, sources, AI-use disclosure, report, phase list, and hand-maintained OpenAPI describe measured DSP, narration mixing, loop endpoint checks, and limitations. Previous full regression baseline was 92 passed, 0 failed, 2 skipped; this update's focused checks are reported separately in REPORT.md. The one live Stability output is documented, but the original Integration Standard, real beat detection, and Phase 4 genuine-stem evidence remain outstanding.

## Immediate gates

1. If the screenshot-exposed API key remains active, revoke it and configure a replacement locally; never share the secret.
2. Review account billing/credit history for the one approved request; the project does not inspect account billing.
3. Install and verify an appropriately licensed FFmpeg build if MP3 workflows are needed.
4. Resolve Phase 4's gates: legal review of SAM commercial use, account-holder access to the exact pinned `facebook/sam-audio-small` checkpoint, supported runtime/hardware, and manual local checkpoint download. The app is cache-only and currently has no reprocess-existing-WAV action; do not create another generation job merely to test stems. Do not mark Phase 4 complete without real-file separation, alignment, reconstruction-error measurement, and download/playback/quality validation.
5. Do not make further paid provider requests without explicit approval.
