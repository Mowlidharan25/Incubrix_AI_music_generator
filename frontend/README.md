# IncuBrix AI Music Studio frontend

This React and TypeScript app provides the Music Studio, generation history, audio results and stems, narration mixer, and API reference.

## Run locally

From this directory, install packages and start the Vite development server:

```powershell
npm install
npm run dev
```

The frontend expects the FastAPI backend at `http://127.0.0.1:8000` by default in development. To use another development backend URL, set `VITE_API_BASE_URL` in a local `.env` file and restart Vite. Production builds use `https://incubrix-ai-music-api.onrender.com`; they intentionally ignore a stale `VITE_API_BASE_URL` so a local URL cannot be compiled into the public site. Never put provider API keys in frontend environment variables; they are public to browser clients.

See the repository [README](../README.md) for backend setup, provider configuration, generation requirements, and full-stack run instructions.

Live music generation uses the configured provider and is subject to its available credits and terms. Mock Preview only exercises the workflow with synthetic test audio; it is not unlimited AI music generation.
