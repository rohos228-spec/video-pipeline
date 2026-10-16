from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "LIVE_RUNS.md"
COLUMNS = ["Дата", "Что", "Потолок", "Оценка расхода", "Результат"]
NOT_PROVEN = re.compile(r"Не проверено|не доказано")
PASS_START = re.compile(r"^[*`\s]*(ПРОЙДЕНО|PASS)")


def _rows() -> list[tuple[int, list[str]]]:
    rows = []
    for no, line in enumerate(DOC.read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("Дата", "---") or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append((no, cells))
    return rows


def test_header_is_the_five_columns():
    header = next(l for l in DOC.read_text(encoding="utf-8").splitlines() if l.startswith("| Дата"))
    assert [c.strip() for c in header.strip().strip("|").split("|")] == COLUMNS


def test_registry_is_not_empty():
    assert len(_rows()) >= 10


def test_every_row_has_five_columns_and_a_date():
    for no, cells in _rows():
        if len(cells) == 2 and re.match(r"\d{4}-\d{2}-\d{2}$", cells[0]) and cells[1]:
            continue
        assert len(cells) == 5, f"LIVE_RUNS.md:{no}: expected 5 columns, got {len(cells)}"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]), f"LIVE_RUNS.md:{no}: bad date {cells[0]!r}"
        for name, value in zip(COLUMNS, cells, strict=True):
            assert value, f"LIVE_RUNS.md:{no}: empty column {name}"


def test_every_pass_row_says_what_is_not_proven():
    passes = [(no, c) for no, c in _rows() if len(c) == 5 and PASS_START.match(c[4])]
    assert passes, "no PASS rows found: the check would be vacuous"
    for no, cells in passes:
        assert NOT_PROVEN.search(cells[4]) or NOT_PROVEN.search(" ".join(cells)), (
            f"LIVE_RUNS.md:{no}: PASS row without 'Не проверено' / 'не доказано'")
