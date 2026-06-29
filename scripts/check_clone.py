"""Тест clone upload: prepare sample + curl через proxy из .env."""
from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "check-clone-result.txt"
sys.path.insert(0, str(ROOT))


def log(msg: str) -> None:
    print(msg, flush=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


async def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("", encoding="utf-8")

    from app.services.elevenlabs_api import (
        build_proxy_url,
        clone_voice_from_sample,
        prepare_clone_sample,
        _find_curl,
    )

    proxy = build_proxy_url()
    log(f"Proxy: {proxy or 'none'}")
    log(f"curl: {_find_curl() or 'NOT FOUND'}")

    lab = ROOT / "data" / "elevenlabs_lab"
    samples = sorted(lab.glob("sample_*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if samples:
        raw = samples[0].read_bytes()
        name = samples[0].name
        log(f"Sample file: {samples[0]} ({len(raw)} bytes)")
    else:
        clips = sorted(lab.glob("clip_*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not clips:
            log("FAIL: нет sample_*.mp3 или clip_*.mp3 в data/elevenlabs_lab")
            return 1
        raw = clips[0].read_bytes()
        name = clips[0].name
        log(f"Clip file: {clips[0]} ({len(raw)} bytes)")

    mp3, fname, dur = await prepare_clone_sample(raw, name)
    log(f"Prepared: {fname}, {dur:.2f}s, {len(mp3)} bytes")
    log("Uploading to ElevenLabs (max ~4 min)...")

    try:
        r = await clone_voice_from_sample(
            name="Lab Test Clone",
            sample_bytes=mp3,
            sample_filename=fname,
        )
        log(f"OK voice_id: {r.get('voice_id')}")
        return 0
    except Exception as exc:
        log(f"FAIL: {exc}")
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
