#!/usr/bin/env python3
"""Offline smoke tests for relay-imagegen: starts a mock relay and exercises every route."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE.parent / "skill" / "scripts" / "imagegen.py"
MOCK = HERE / "mock_relay.py"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TINY_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")

PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}  {detail}")


def run(args, config):
    return subprocess.run(
        [sys.executable, str(CLI), "--config", str(config)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def main():
    work = Path(tempfile.mkdtemp(prefix="relay-imagegen-test-"))
    proc = subprocess.Popen(
        [sys.executable, str(MOCK)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("PORT="), f"mock server did not start: {line!r}"
        port = int(line.split("=", 1)[1])
        base = f"http://127.0.0.1:{port}/v1"

        config = work / "config.env"
        config.write_text(
            "\n".join(
                [
                    f"BASE_URL={base}",
                    "API_KEY=sk-test-1234567890abcdef",
                    "IMAGE_MODEL=auto",
                    "RESPONSES_MODEL=gpt-5.4",
                    "ROUTE=auto",
                    f"OUT_DIR={work / 'out'}",
                ]
            ),
            encoding="utf-8",
        )
        ref = work / "ref.png"
        ref.write_bytes(TINY_PNG)

        # 1. probe
        result = run(["probe", "--json"], config)
        check("probe exits 0", result.returncode == 0, result.stderr)
        report = json.loads(result.stdout)
        check("probe finds image models", "gpt-image-2.5" in report.get("image_model_candidates", []))
        check("probe picks newest image model", report.get("picked_image_model") == "gpt-image-2.5")
        check("probe masks key", report.get("api_key", "").startswith("sk-tes") and "1234567890" not in report.get("api_key", ""))

        # 2. generate via images route
        result = run(["generate", "--prompt", "a cat", "--route", "images", "--json"], config)
        check("generate/images exits 0", result.returncode == 0, result.stderr)
        payload = json.loads(result.stdout)
        files = [Path(p) for p in payload.get("files", [])]
        check("generate/images saved a PNG", bool(files) and files[0].exists() and files[0].read_bytes().startswith(PNG_MAGIC))

        # 3. generate via responses route (SSE)
        result = run(
            ["generate", "--prompt", "a dog", "--route", "responses", "--json"], config
        )
        check("generate/responses exits 0", result.returncode == 0, result.stderr)
        payload = json.loads(result.stdout)
        check("generate/responses route label", payload.get("route") == "responses")
        check("generate/responses saved a PNG", Path(payload["files"][0]).read_bytes().startswith(PNG_MAGIC))

        # 4. auto route prefers images
        result = run(["generate", "--prompt", "a bird", "--json"], config)
        check("generate/auto exits 0", result.returncode == 0, result.stderr)
        check("generate/auto uses images route", json.loads(result.stdout).get("route") == "images")

        # 5. edit through multipart
        result = run(["edit", "--prompt", "make it blue", "--image", str(ref), "--json"], config)
        check("edit exits 0", result.returncode == 0, result.stderr)
        payload = json.loads(result.stdout)
        check("edit route label", payload.get("route") == "images-edit")
        check("edit saved a PNG", Path(payload["files"][0]).read_bytes().startswith(PNG_MAGIC))

        # 6. custom output path
        target = work / "exact.png"
        result = run(["generate", "--prompt", "x", "--out", str(target), "--json"], config)
        check("generate --out writes exact path", result.returncode == 0 and target.exists(), result.stderr)

        # 7. dry run makes no request and still works
        result = run(["generate", "--prompt", "x", "--dry-run"], config)
        check("dry-run exits 0", result.returncode == 0, result.stderr)
        check("dry-run mentions routes", "routes" in result.stdout)

        # 8. bad key surfaces HTTP error
        bad = work / "bad.env"
        bad.write_text(f"BASE_URL={base}\nAPI_KEY=sk-bad\nOUT_DIR={work / 'bad'}\n", encoding="utf-8")
        result = run(["generate", "--prompt", "x", "--route", "images"], bad)
        check("bad key exits 5 (all routes failed)", result.returncode == 5, f"got {result.returncode}: {result.stderr[:200]}")

        # 9. reference image forces responses route
        result = run(
            ["generate", "--prompt", "same style", "--ref", str(ref), "--json"], config
        )
        check("ref-image generate exits 0", result.returncode == 0, result.stderr)
        check("ref-image generate uses responses", json.loads(result.stdout).get("route") == "responses")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
