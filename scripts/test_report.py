#!/usr/bin/env python
"""Turn pytest junit-xml files into ONE honest numbers block and (optionally) write it into docs/TESTING.md.

    python scripts/test_report.py --suite orchestrator=reports/orchestrator.xml \
        --suite legacy='reports/legacy/chunk_*.xml' --deselected orchestrator=84 \
        --known-failures docs/audit/G01_RED_TESTS.md --write docs/TESTING.md

Per suite: passed / failed / errors / skipped / deselected (deselected is not in junit: pass it with
--deselected NAME=N, otherwise it is shown as n/a). The block is placed between
<!-- tests:begin --> and <!-- tests:end --> in the target file. Numbers are never typed by hand.
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BEGIN = "<!-- tests:begin -->"
END = "<!-- tests:end -->"
HEADER = "| Набор | Пройдено | Упало | Ошибок | Пропущено | Не выбрано | Всего |"
SEPARATOR = "|---|---|---|---|---|---|---|"
NA = "н/д"
KNOWN_TITLE = "Известные красные тесты (G01, класс C, нужно решение владельца):"
NO_KNOWN = "Известные красные тесты: нет в списке G01."
NO_DATA = "Нет данных: прогон не выполнялся."
GENERATED = "Блок сгенерирован scripts/test_report.py из junit-xml; руками не править."


def read_junit(paths: list[str]) -> dict:
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    stamp = ""
    for p in paths:
        root = ET.parse(p).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for s in suites:
            for k in tot:
                tot[k] += int(s.get(k, 0))
            stamp = max(stamp, (s.get("timestamp") or "")[:10])
    return {
        "passed": tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"],
        "failed": tot["failures"], "errors": tot["errors"], "skipped": tot["skipped"],
        "total": tot["tests"], "date": stamp,
    }


def known_failures(path: Path) -> list[str]:
    """Class C rows of the G01 table: `| test_x.py::test_name | C | ...`."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and "::" in cells[0] and cells[1] == "C":
            out.append(cells[0])
    return out


def render_block(suites: dict[str, dict], deselected: dict[str, int], known: list[str]) -> str:
    lines = [BEGIN, GENERATED, ""]
    if not suites:
        lines.append(NO_DATA)
    else:
        dates = sorted({s["date"] for s in suites.values() if s["date"]})
        lines += [HEADER, SEPARATOR]
        for name, s in suites.items():
            des = deselected.get(name)
            lines.append(f"| {name} | {s['passed']} | {s['failed']} | {s['errors']} | {s['skipped']} | "
                         f"{NA if des is None else des} | {s['total']} |")
        lines += ["", f"Дата прогона: {', '.join(dates) if dates else NA}"]
    lines.append("")
    if known:
        lines.append(KNOWN_TITLE)
        lines += [f"- `{k}`" for k in known]
    else:
        lines.append(NO_KNOWN)
    lines.append(END)
    return "\n".join(lines)


def parse_block(text: str) -> dict:
    """Inverse of render_block (used by the drift test). Raises ValueError on a malformed block."""
    m = re.search(re.escape(BEGIN) + r"\r?\n(.*?)\r?\n" + re.escape(END), text, re.S)
    if not m:
        raise ValueError("markers not found")
    body = m.group(1).splitlines()
    if GENERATED not in body:
        raise ValueError("generated-notice line missing")
    rows = []
    if HEADER in body:
        i = body.index(HEADER)
        if body[i + 1] != SEPARATOR:
            raise ValueError("table separator missing")
        for ln in body[i + 2:]:
            if not ln.startswith("|"):
                break
            c = [x.strip() for x in ln.strip().strip("|").split("|")]
            if len(c) != 7 or not c[0]:
                raise ValueError(f"bad row: {ln}")
            nums = [int(x) for x in c[1:5]] + [None if c[5] == NA else int(c[5])] + [int(c[6])]
            if sum(nums[:4]) != nums[5]:
                raise ValueError(f"row does not add up: {ln}")
            rows.append((c[0], *nums))
    elif NO_DATA not in body:
        raise ValueError("neither table nor no-data line")
    known = [ln[3:-1] for ln in body if ln.startswith("- `") and ln.endswith("`")]
    if not (KNOWN_TITLE in body or NO_KNOWN in body):
        raise ValueError("known-failures line missing")
    return {"rows": rows, "known": known}


def write_into(target: Path, block: str) -> None:
    text = target.read_text(encoding="utf-8")
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pat.search(text):
        raise SystemExit(f"{target}: markers {BEGIN} / {END} not found")
    target.write_text(pat.sub(lambda _m: block, text, count=1), encoding="utf-8", newline="")


def _pairs(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items:
        name, _, val = it.partition("=")
        if not name or not val:
            raise SystemExit(f"expected NAME=VALUE, got {it!r}")
        out[name] = val
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", action="append", default=[], help="NAME=junit.xml or NAME=glob (repeatable)")
    ap.add_argument("--deselected", action="append", default=[], help="NAME=N from the pytest summary line")
    ap.add_argument("--known-failures", default="docs/audit/G01_RED_TESTS.md")
    ap.add_argument("--write", default="", help="file with the tests:begin/end markers (docs/TESTING.md)")
    args = ap.parse_args()
    suites = {}
    for name, pattern in _pairs(args.suite).items():
        files = sorted(glob.glob(pattern))
        if not files:
            raise SystemExit(f"suite {name}: no files match {pattern}")
        suites[name] = read_junit(files)
    des = {k: int(v) for k, v in _pairs(args.deselected).items()}
    kf = Path(args.known_failures)
    block = render_block(suites, des, known_failures(kf) if kf.exists() else [])
    if args.write:
        write_into(Path(args.write), block)
        print(f"written to {args.write}")
    else:
        print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
