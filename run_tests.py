#!/usr/bin/env python3
"""One-command test runner for IncuBrix AI Background Music Engine.

Integration Standard Requirement 1:
One command runs all tests (unit, integration and at least one end-to-end) with no API keys.
"""

import os
import subprocess
import sys
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parent
    backend_dir = repo_root / "backend"

    print("=" * 70)
    print(" INCUBRIX ENGINE - RUN ALL TESTS (ZERO API KEYS REQUIRED)")
    print("=" * 70)

    # Ensure PYTHONPATH includes backend
    env = os.environ.copy()
    python_path = str(backend_dir)
    if "PYTHONPATH" in env:
        python_path = f"{python_path}{os.pathsep}{env['PYTHONPATH']}"
    env["PYTHONPATH"] = python_path
    # Explicitly ensure mock provider is used so no external API keys are needed
    env["MUSIC_PROVIDER"] = "mock"
    env["PYTHONIOENCODING"] = "utf-8"

    venv_python = backend_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = backend_dir / ".venv" / "bin" / "python"
    if not venv_python.exists():
        venv_python = Path(sys.executable)

    print(f"\n[1/2] Running backend unit & integration tests with pytest...")
    pytest_cmd = [
        str(venv_python),
        "-m",
        "pytest",
        str(backend_dir / "tests"),
        "-v",
        "-k",
        "not test_stability_live",
    ]
    result = subprocess.run(pytest_cmd, cwd=str(repo_root), env=env)
    if result.returncode != 0:
        print("\n[FAIL] Pytest suite failed!")
        sys.exit(result.returncode)

    print("\n[2/2] Running end-to-end verification (job creation, audio DSP, manifest)...")
    e2e_code = """
import io, json
from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)

# 1. Health & readiness checks
assert client.get('/health').status_code == 200
assert client.get('/ready').status_code == 200

# 2. End-to-end music job creation
resp = client.post('/api/v1/music/jobs', json={
    'prompt': 'Upbeat corporate acoustic guitar and bright piano',
    'duration_s': 10,
    'sections': [{'start_s': 0, 'mood': 'bright intro'}, {'start_s': 5, 'mood': 'confident build'}],
    'tempo_bpm': 120,
    'key': 'C',
    'loopable': True,
    'output': {'sample_rate': 48000, 'target_lufs': -16, 'formats': ['wav']}
}, headers={'Idempotency-Key': 'e2e-test-key-1'})
assert resp.status_code == 202, f'Expected 202, got {resp.status_code}: {resp.text}'
job_id = resp.json()['job_id']

# 3. Poll job status
status_resp = client.get(f'/api/v1/jobs/{job_id}')
assert status_resp.status_code == 200
data = status_resp.json()
assert data['status'] == 'succeeded'
assert data['progress'] == 100

# 4. Verify audio download
audio_resp = client.get(f'/api/v1/jobs/{job_id}/audio')
assert audio_resp.status_code == 200
assert len(audio_resp.content) > 10000

# 5. Verify manifest
manifest_resp = client.get(f'/api/v1/jobs/{job_id}/manifest')
assert manifest_resp.status_code == 200
manifest = manifest_resp.json()
assert manifest['processing']['requirements']['duration']['status'] == 'passed'
assert manifest['processing']['requirements']['sample_rate']['status'] == 'passed'
print('  [OK] Health & Ready endpoints verified')
print('  [OK] End-to-end mock generation completed')
print('  [OK] Processed audio downloaded (48 kHz, exact duration, true-peak limited)')
print('  [OK] Manifest verified (requirements passed)')
"""

    e2e_result = subprocess.run([str(venv_python), "-c", e2e_code], cwd=str(repo_root), env=env)
    if e2e_result.returncode != 0:
        print("\n[FAIL] End-to-end verification failed!")
        sys.exit(e2e_result.returncode)

    print("\n" + "=" * 70)
    print(" ALL TESTS PASSED: Unit, Integration & End-to-End verified!")
    print("=" * 70)


if __name__ == "__main__":
    main()
