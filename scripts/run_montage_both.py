"""Смонтировать #15 и #17, отчёт в data/_montage_run_report.txt"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)
REPORT = ROOT / "data" / "_montage_run_report.txt"
IDS = (15, 17)


def db_rows() -> list[tuple]:
    conn = sqlite3.connect(ROOT / "data" / "state.db")
    rows = conn.execute(
        "SELECT id, slug, status FROM projects WHERE id IN (15,17) ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def disk_check(slug: str) -> dict:
    base = ROOT / "data" / "videos" / slug
    voice = list((base / "audio").glob("voice_full*.mp3")) if (base / "audio").is_dir() else []
    clips = list((base / "videos").glob("clip_*.mp4")) if (base / "videos").is_dir() else []
    final = base / "final" / f"{slug}.mp4"
    return {
        "xlsx": (base / "project.xlsx").is_file(),
        "voice": len(voice),
        "clips": len(clips),
        "final": final.is_file(),
        "final_path": str(final),
    }


def run_one(pid: int) -> tuple[int, int, str]:
    log = ROOT / "data" / f"montage-{pid}.log"
    cmd = [str(PY), str(ROOT / "scripts" / "assemble_r15_direct.py"), str(pid)]
    with log.open("w", encoding="utf-8") as f:
        p = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
    text = log.read_text(encoding="utf-8", errors="replace")
    return p.returncode, pid, text[-8000:]


def main() -> None:
    lines = [f"=== montage both @ {datetime.now().isoformat()} ===", ""]
    for r in db_rows():
        lines.append(f"DB {r}")
    lines.append("")
    conn = sqlite3.connect(ROOT / "data" / "state.db")
    slugs = {row[0]: row[1] for row in conn.execute("SELECT id,slug FROM projects WHERE id IN (15,17)")}
    conn.close()
    for pid in IDS:
        slug = slugs.get(pid, "?")
        chk = disk_check(slug)
        lines.append(f"#{pid} {slug}: {chk}")
    lines.append("")
    results = []
    for pid in IDS:
        lines.append(f"--- RUN #{pid} ---")
        code, _, tail = run_one(pid)
        lines.append(f"exit={code}")
        lines.append(tail)
        results.append((pid, code))
        lines.append("")
    lines.append("SUMMARY:")
    for pid, code in results:
        slug = slugs.get(pid, "?")
        final = ROOT / "data" / "videos" / slug / "final" / f"{slug}.mp4"
        lines.append(f"  #{pid} {slug}: exit={code} final={final.is_file()} -> {final}")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
