"""Restore nicshe (#13) scene videos from Outsee Yandex storage URLs in logs."""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "studio-live.log"
DEST = ROOT / "data" / "videos" / "nicshe" / "videos"

CLIP_NAME = re.compile(r"clip_\d{3}_[a-f0-9]+\.mp4", re.I)
YANDEX_URL = re.compile(
    r"https://storage\.yandexcloud\.net/outseehistory/\S+",
    re.I,
)
FRAME_NUM = re.compile(r"\[#13\] frame (\d+) video:", re.I)


def _base_url(url: str) -> str:
    return url.split("?", 1)[0]


def _parse_log(text: str) -> tuple[dict[int, str], dict[str, str]]:
    url_by_file: dict[str, str] = {}
    frame_file: dict[int, str] = {}

    for line in text.splitlines():
        if "_download_via_queue_video_result" in line:
            mu = YANDEX_URL.search(line)
            mc = CLIP_NAME.search(line)
            if mu and mc:
                url_by_file[mc.group(0)] = _base_url(mu.group(0))
        elif "[#13] frame" in line and " video:" in line and "nicshe" in line:
            mf = FRAME_NUM.search(line)
            mc = CLIP_NAME.search(line)
            if mf and mc:
                frame_file[int(mf.group(1))] = mc.group(0)

    return frame_file, url_by_file


def main() -> int:
    text = LOG.read_text(encoding="utf-8", errors="replace")
    frame_file, url_by_file = _parse_log(text)
    if not frame_file:
        print("no frame entries in log", file=sys.stderr)
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    for n in sorted(frame_file):
        fn = frame_file[n]
        dst = DEST / fn
        if dst.is_file() and dst.stat().st_size > 100_000:
            skip += 1
            continue
        url = url_by_file.get(fn)
        if not url:
            print(f"frame {n}: no URL for {fn}", file=sys.stderr)
            fail += 1
            continue
        tmp = dst.with_suffix(".mp4.part")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "video-pipeline-restore/1.0"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            tmp.replace(dst)
            size = dst.stat().st_size
            print(f"frame {n:3d}: OK {fn} ({size // 1024} KiB)")
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"frame {n:3d}: HTTP {e.code} {url}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            fail += 1
        except Exception as e:  # noqa: BLE001
            print(f"frame {n:3d}: {e}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            fail += 1

    print(f"done: downloaded={ok} skipped={skip} failed={fail} -> {DEST}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
