# Sources and software inventory

## Project dependencies

Versions below are from the repository dependency manifests; this inventory does not independently verify the upstream license terms.

### Backend (`backend/requirements.txt`)

- FastAPI 0.115.6
- Uvicorn 0.34.0
- Pydantic 2.10.6
- pydantic-settings 2.16.0
- HTTPX 0.28.1
- pytest 8.3.4
- python-dotenv 1.2.4
- Mutagen 1.47.0 (audio format and metadata parsing)
- NumPy 2.3.2, SciPy 1.16.1, SoundFile 0.13.1, and pyloudnorm 0.1.1 for PCM decoding/encoding, 48 kHz resampling, integrated loudness normalization/measurement, and true-peak measurement.
- Optional FFmpeg with `libmp3lame` for MP3 encoding and MP3 decoding. FFmpeg and an MP3 encoder were not installed in the inspected environment; verify the exact binary and codec licenses before redistribution.

### Frontend (`frontend/package.json`)

- React 19.3.1 and React DOM 19.3.1
- TypeScript ~7.0.2
- Vite 8.3.4
- Tailwind CSS 4.3.3 and `@tailwindcss/vite` 4.3.3
- Framer Motion 14.4.0
- Lucide React 1.59.0
- React Router DOM 7.18.4
- Oxlint 1.88.0

## Development and validation tools used

- Python `pytest` and FastAPI `TestClient` (HTTPX-backed).
- npm scripts `build` (TypeScript and Vite) and `lint` (Oxlint).
- VS Code with Copilot SDK coding assistance.

## External music provider references

Default provider: Stability AI Stable Audio 3.0, API endpoint `POST https://api.stability.ai/v2beta/audio/stable-audio/text-to-audio`. Documentation checked October 22, 2026.

- [Stable Audio 3.0 text-to-audio API](https://docs.stability.ai/api-reference/stable-audio/text-to-audio) specifies the `stable-audio-3` multipart endpoint, 1–380 second duration field, MP3/WAV output, and asynchronous generation IDs; its prose describes up-to-six-minute, 44.1 kHz stereo audio. This application restricts requested duration to 5–300 seconds.
- [Fetch Audio Result](https://docs.stability.ai/api-reference/audio-results/fetch-audio-result) documents polling `GET /v2beta/audio/results/{id}` with HTTP 202 while processing and HTTP 200 with audio when ready.
- [Stability API reference](https://docs.stability.ai/api-reference) documents the `Authorization: Bearer ...` header, multipart requests, audio responses, error status codes, and rate limits.
- [Stability pricing](https://docs.stability.ai/pricing) lists Stable Audio 3.0 at 8 credits per successful generation and 1 credit = $0.01. Pricing and availability can change.
- [Getting started](https://docs.stability.ai/getting-started) documents API-key creation, account credits, and key safety.
- [Stability AI Terms of Service](https://stability.ai/terms-of-service) assign output rights to users as between the parties to the extent permitted by law and subject to terms; users remain responsible for inputs and outputs.
- [Acceptable Use Policy](https://stability.ai/use-policy) applies to prompts, outputs, and API use. [Privacy Policy](https://stability.ai/privacypolicy) describes data handling.

Current pricing implies $0.08 per successful Stable Audio 3.0 generation, not per minute; failed generations are documented as not charged. No credits are purchased by this app. The adapter sends the model identifier, prompt, duration, one output format, and optional seed. Stability says Stable Audio models are trained on licensed data, but the app does not certify generated output as unique, copyrightable, or non-infringing and does not provide legal advice.

## Audio processing and stem-separation review

- [SoundFile documentation](https://python-soundfile.readthedocs.io/) and [SciPy signal resampling](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html) support the tested PCM decode/write and polyphase resampling path.
- [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) implements integrated loudness measurement; this application uses it to target loudness and separately oversamples the decoded final signal to measure true peak. The delivered WAV is fixed at 48,000 Hz; requested and measured values plus requirement statuses are stored in the manifest.
- Narration ducking uses a locally implemented 10 ms RMS envelope with a -45 dBFS activity threshold, 50 ms attack, 250 ms release, and 12 dB attenuation target. It detects signal level only; it is not speech recognition or voice activity detection.
- Loop mode applies a 250 ms reverse-head/tail edge crossfade and measures the encoded channel-wise first/last sample-frame RMS difference. The -60 dBFS threshold checks only endpoint amplitude discontinuity, not musical continuity.
- [FFmpeg licensing](https://ffmpeg.org/legal.html) depends on the build configuration and enabled codecs. MP3 output in this application additionally requires `libmp3lame`; the precise FFmpeg binary's licensing must be checked by the operator.
- [Meta SAM-Audio repository](https://github.com/facebookresearch/sam-audio) documents text/visual/time-prompted sound separation and a target/residual output. Integration pins source commit `bb4c6999d2677c7402360e426afc01ddfad6dce0`; the [small checkpoint](https://huggingface.co/facebook/sam-audio-small) pins revision `20b65f56888142eebe7c37448c6f6b3b32600e9b`. The checkpoint is gated and requires the account holder's manual approval and authentication. The [SAM License](https://github.com/facebookresearch/sam-audio/blob/main/LICENSE) covers trained weights under limited rights and restrictions; the reviewed terms do not expressly establish commercial use, so obtain legal review. Research publications using the materials must acknowledge them. The repository requires Python >=3.11 and recommends a CUDA-compatible GPU. Its model card demonstrates target/residual and an instrument prompt, but not a guaranteed multi-instrument decomposition. The adapter loads model and processor with `local_files_only=True`; an operator must manually download the pinned checkpoint after access approval. No runtime or weights are installed here.
- [PE-AV large model](https://huggingface.co/facebook/pe-av-large), a dependency described as crucial to SAM-Audio, is published under Apache-2.0 and has 2.23 billion parameters according to its model API. Audit all installed dependency licenses before redistribution.
- [AudioShake instrument stem separation](https://www.audioshake.ai/products/instrument-stem-separation) advertises extracting drums, bass, guitar, piano, winds, strings, and other stems, and the product site says API/SDK integration is available. Public materials inspected for this report did not establish API pricing, a detailed API contract, or commercial output-use rights; obtain the vendor's agreement before selecting it.
- [Demucs repository](https://github.com/facebookresearch/demucs) describes four-source separation and an experimental six-source model (with a piano-quality caveat); the repository is archived. Its documented hardware needs are about 3 GB VRAM (about 7 GB with default GPU settings) or CPU processing around 1.5 times track duration. The code is MIT-licensed, but commercial rights for pretrained weights were not established.
- [Spleeter repository](https://github.com/deezer/spleeter) documents four- and five-stem models and publishes MIT-licensed code; commercial rights for its pretrained weights were not established. Spleeter Pro is a separate commercial product and does not establish the open model weights' terms.
- [Open-Unmix repository](https://github.com/sigsep/open-unmix-pytorch) publishes MIT-licensed code, but its `umxl` weights are [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Other pretrained weights are tied to MUSDB18 sources whose access conditions and included non-commercially licensed material do not establish commercial rights for this use.
- [AudioShake instrument stem separation](https://www.audioshake.ai/products/instrument-stem-separation) advertises distinct instrument outputs, but reviewed public materials did not establish API pricing, a detailed API contract, or commercial output-use rights. A vendor agreement and access are needed before selection. No commercially permitted, ready-to-run method has been verified.

The backend reads the credential only from `STABILITY_API_KEY`; never add actual keys to source, frontend variables, docs, logs, or tests. Revoke any still-active key displayed in the user-provided screenshot and use a replacement. The normal automated suite uses mocked provider responses and explicitly identified test-only tone fixtures; it never calls the paid API. The optional real integration test is explicitly guarded by `RUN_STABILITY_INTEGRATION_TEST=1`. A separate single 5-second WAV request was made after explicit user approval on 2026-10-23; its verified measurements and limitations are recorded in [REPORT.md](./REPORT.md).

ElevenLabs references are no longer used by default; its adapter remains an optional legacy provider if `MUSIC_PROVIDER=elevenlabs` and its own credentials are configured.

Dependency versions are recorded from project manifests. Upstream software licenses and provider-specific rights should be checked at their authoritative sources before production or redistribution; this inventory is not a legal opinion.
