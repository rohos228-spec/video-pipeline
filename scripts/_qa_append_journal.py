"""Append journal lines with UTF-8 (avoid PowerShell encoding issues)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

JOURNAL = Path(__file__).resolve().parents[1] / "docs" / "QA-RUN-LIVE.md"


def append(doing: str, done: str, result: str, bug: str = "-") -> None:
    now = datetime.now().strftime("%H:%M")
    line = f"| {now} | {doing} | {done} | {result} | {bug} |"
    text = JOURNAL.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for i, ln in enumerate(lines):
        out.append(ln)
        if (
            not inserted
            and ln.startswith("|-------")
            and i > 0
            and "Время" in lines[i - 1]
        ):
            window = "\n".join(lines[max(0, i - 8) : i])
            if "Журнал" in window:
                out.append(line)
                inserted = True
    if not inserted:
        out.append(line)
    JOURNAL.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(line)


if __name__ == "__main__":
    # args: doing | done | result | [bug]
    if len(sys.argv) < 4:
        raise SystemExit("usage: doing done result [bug]")
    append(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "-")
