"""Восстановить clip_*.mp4 для nicshe (#13) из URL в studio-live.log."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "studio-live.log"
OUT = ROOT / "data" / "videos" / "nicshe" / "videos"

CLIP_DEST = re.compile(
    r"→\s*(C:\\Users\\aicreator\\video-pipeline\\data\\videos\\nicshe\\videos\\(clip_\d{3}_[^\\s]+\.mp4))"
)
URL_RE = re.compile(r"https://storage\.yandexcloud\.net/outseehistory/[^\s\"']+")


def _pairs() -> dict[str, str]:
    out: dict[str, str] = {}
    last_url: str | None = None
    for line in LOG.open(encoding="utf-8", errors="ignore"):
        for u in URL_RE.findall(line):
            last_url = u
        m = CLIP_DEST.search(line)
        if m and last_url:
            out[m.group(1)] = last_url
            last_url = None
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mapping = _pairs()
    print(f"found {len(mapping)} clips in log")
    ok = fail = skip = 0
    for name, url in sorted(mapping.items()):
        dest = OUT / name
        if dest.is_file() and dest.stat().st_size > 10_000:
            skip += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "video-pipeline-restore/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if len(data) < 10_000:
                print(f"FAIL {name}: too small ({len(data)} bytes)")
                fail += 1
                continue
            dest.write_bytes(data)
            print(f"OK {name} ({len(data)} bytes)")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {name}: {e}")
            fail += 1
    print(f"done: ok={ok} skip={skip} fail={fail}")


if __name__ == "__main__":
    main()
