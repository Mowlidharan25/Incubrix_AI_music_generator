#!/usr/bin/env python3
"""Batch Runner for IncuBrix AI Music Engine.

Integration Standard Requirement 10:
Runs a folder or file of test inputs against the API and writes all results to one folder.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("batch_runner")


def load_test_inputs(input_path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Loads test cases from a single JSON file or a directory of JSON files."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    cases: list[tuple[str, dict[str, Any]]] = []
    if input_path.is_file():
        content = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(content, list):
            for i, item in enumerate(content):
                name = item.get("name") or f"case_{i + 1:02d}"
                cases.append((name, item))
        elif isinstance(content, dict):
            name = content.get("name") or input_path.stem
            cases.append((name, content))
        else:
            raise ValueError("Input file must contain a JSON object or array of objects.")
    elif input_path.is_dir():
        for file in sorted(input_path.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                name = data.get("name") or file.stem
                cases.append((name, data))
            except Exception as exc:
                logger.warning("Could not read %s: %s", file, exc)

    if not cases:
        raise ValueError(f"No test cases found in {input_path}")
    return cases


def run_batch(
    inputs: list[tuple[str, dict[str, Any]]],
    api_url: str,
    output_dir: Path,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    api_url = api_url.rstrip("/")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api_url": api_url,
        "total_cases": len(inputs),
        "succeeded": 0,
        "failed": 0,
        "results": [],
    }

    client = httpx.Client(base_url=api_url, timeout=30.0)

    # Preflight health check
    try:
        health_resp = client.get("/health")
        if health_resp.status_code != 200:
            logger.error("API /health returned %d: %s", health_resp.status_code, health_resp.text)
            sys.exit(1)
        logger.info("Connected to IncuBrix API at %s (/health OK)", api_url)
    except Exception as exc:
        logger.error("Could not connect to API at %s: %s", api_url, exc)
        sys.exit(1)

    print("=" * 80)
    print(f"INCUBRIX AI MUSIC ENGINE - BATCH RUNNER")
    print(f"Test cases: {len(inputs)} | Output folder: {output_dir.resolve()}")
    print("=" * 80)

    for idx, (name, payload) in enumerate(inputs, start=1):
        print(f"\n[{idx}/{len(inputs)}] Running test: '{name}'")
        test_payload = {k: v for k, v in payload.items() if k != "name"}
        correlation_id = f"batch-{uuid4().hex[:8]}"
        idempotency_key = f"idemp-{uuid4().hex[:12]}"
        headers = {
            "Content-Type": "application/json",
            "X-Correlation-Id": correlation_id,
            "Idempotency-Key": idempotency_key,
        }

        started_at = time.perf_counter()
        job_result: dict[str, Any] = {
            "name": name,
            "input": test_payload,
            "status": "failed",
            "job_id": None,
            "duration_s": test_payload.get("duration_s"),
            "prompt": test_payload.get("prompt"),
            "error": None,
            "downloaded_files": [],
        }

        try:
            post_resp = client.post("/api/v1/music/jobs", json=test_payload, headers=headers)
            if post_resp.status_code != 202:
                err_detail = post_resp.json() if "application/json" in post_resp.headers.get("content-type", "") else post_resp.text
                job_result["error"] = {"status_code": post_resp.status_code, "detail": err_detail}
                logger.error("Job submission failed (%d): %s", post_resp.status_code, err_detail)
                summary["failed"] += 1
                summary["results"].append(job_result)
                continue

            accepted = post_resp.json()
            job_id = accepted["job_id"]
            job_result["job_id"] = job_id
            logger.info("Job accepted: %s. Polling for results...", job_id)

            # Poll status
            elapsed = 0.0
            job_data: dict[str, Any] = {}
            while elapsed < timeout:
                time.sleep(poll_interval)
                elapsed = time.perf_counter() - started_at
                status_resp = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Correlation-Id": correlation_id})
                if status_resp.status_code != 200:
                    logger.warning("Status polling returned HTTP %d", status_resp.status_code)
                    continue
                job_data = status_resp.json()
                current_status = job_data.get("status")
                progress = job_data.get("progress", 0)
                logger.info("Job %s: status=%s, progress=%d%% (%.1fs elapsed)", job_id, current_status, progress, elapsed)
                if current_status in {"succeeded", "failed"}:
                    break

            job_result["status"] = job_data.get("status", "timed_out")
            job_result["time_seconds"] = round(time.perf_counter() - started_at, 2)

            # Create test folder
            case_dir = output_dir / f"{idx:02d}_{name}_{job_id[:8]}"
            case_dir.mkdir(parents=True, exist_ok=True)

            if job_data.get("status") == "succeeded":
                summary["succeeded"] += 1
                res = job_data.get("result", {})
                job_result["measured"] = {
                    "duration_s": res.get("duration_s"),
                    "sample_rate": res.get("sample_rate"),
                    "integrated_lufs": res.get("integrated_lufs"),
                    "true_peak_dbtp": res.get("true_peak_dbtp"),
                }

                # 1. Download mix
                mix_url = res.get("download_url", f"/api/v1/jobs/{job_id}/audio")
                mix_resp = client.get(mix_url)
                if mix_resp.status_code == 200:
                    ext = res.get("format", "wav")
                    mix_file = case_dir / f"mix.{ext}"
                    mix_file.write_bytes(mix_resp.content)
                    job_result["downloaded_files"].append(str(mix_file.name))

                # 2. Download manifest
                manifest_url = res.get("manifest_download_url", f"/api/v1/jobs/{job_id}/manifest")
                man_resp = client.get(manifest_url)
                if man_resp.status_code == 200:
                    man_file = case_dir / "music_manifest.json"
                    man_file.write_bytes(man_resp.content)
                    job_result["downloaded_files"].append(str(man_file.name))

                # 3. Download mix_ducked if present
                if res.get("mix_ducked_download_url"):
                    duck_resp = client.get(res["mix_ducked_download_url"])
                    if duck_resp.status_code == 200:
                        duck_file = case_dir / "mix_ducked.wav"
                        duck_file.write_bytes(duck_resp.content)
                        job_result["downloaded_files"].append(str(duck_file.name))

                # 4. Download stems if available
                stems_info = res.get("stem_processing", {}).get("stems", [])
                for stem in stems_info:
                    s_id = stem.get("stem_id")
                    s_url = stem.get("download_url") or f"/api/v1/jobs/{job_id}/stems/{s_id}"
                    stem_resp = client.get(s_url)
                    if stem_resp.status_code == 200:
                        stem_file = case_dir / f"stem_{s_id}.wav"
                        stem_file.write_bytes(stem_resp.content)
                        job_result["downloaded_files"].append(str(stem_file.name))

                logger.info("Successfully completed '%s' (saved %d files)", name, len(job_result["downloaded_files"]))
            else:
                summary["failed"] += 1
                job_result["error"] = job_data.get("error")
                err_file = case_dir / "error.json"
                err_file.write_text(json.dumps(job_data, indent=2), encoding="utf-8")
                logger.error("Job '%s' failed: %s", name, job_data.get("error"))

        except Exception as exc:
            logger.exception("Error executing test '%s': %s", name, exc)
            job_result["error"] = {"message": str(exc)}
            summary["failed"] += 1

        summary["results"].append(job_result)

    summary["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary_path = output_dir / "batch_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 80)
    print(f"BATCH RUN COMPLETE: {summary['succeeded']}/{summary['total_cases']} succeeded ({summary['failed']} failed)")
    print(f"Summary written to: {summary_path.resolve()}")
    print("=" * 80)

    return summary


def main():
    parser = argparse.ArgumentParser(description="IncuBrix Batch Runner CLI tool (Integration Standard #10)")
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="Path to a JSON file or directory of test input JSON files",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("batch_results"),
        help="Folder to store all generated files, stems, manifests and reports",
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Base URL of IncuBrix API (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Max seconds to wait per job (default: 300.0)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2.0)",
    )

    args = parser.parse_args()

    try:
        test_cases = load_test_inputs(args.input)
    except Exception as exc:
        logger.error("Failed to load test cases: %s", exc)
        sys.exit(1)

    summary = run_batch(
        inputs=test_cases,
        api_url=args.api_url,
        output_dir=args.output_dir,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )

    if summary["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
