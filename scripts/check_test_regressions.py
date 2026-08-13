"""Регрессионный гейт для CI: сравнивает FAILED-тесты с baseline.

Использование:
    python3 -m pytest tests/ -q | python3 scripts/check_test_regressions.py

Exit 1, если появились НОВЫЕ падения (нет в tests/known_failures.txt).
Если baseline-тест перестал падать — печатает «fixed» (не валит сборку;
baseline стоит почистить вручную).

Обновить baseline после осознанных изменений:
    python3 -m pytest tests/ -q 2>&1 | grep ^FAILED | sed 's/FAILED //' \\
        | cut -d' ' -f1 | sort -u > tests/known_failures.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / "tests" / "known_failures.txt"


def _failed_from_output(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        line = line.strip()
        if line.startswith("FAILED "):
            out.add(line.split()[1].strip())
    return out


def main() -> int:
    current = _failed_from_output(sys.stdin.read().splitlines())
    baseline = set()
    if BASELINE.is_file():
        baseline = {
            ln.strip()
            for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        }
    new_failures = sorted(current - baseline)
    fixed = sorted(baseline - current)
    for t in fixed:
        print(f"FIXED (почисти baseline): {t}")
    if not BASELINE.is_file():
        print(f"WARN: нет {BASELINE} — все текущие падения считаются новыми")
    if new_failures:
        print("\nНОВЫЕ падения (нет в baseline):")
        for t in new_failures:
            print(f"  NEW-FAIL {t}")
        print(f"\nИтого новых: {len(new_failures)}")
        return 1
    print(f"OK: новых падений нет (baseline={len(baseline)}, current={len(current)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
