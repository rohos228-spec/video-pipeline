"""docs/TESTING.md: the numbers block must be in the format scripts/test_report.py produces.

No concrete counts are asserted (they change every run): only structure and that the rows add up.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "TESTING.md"


def _report():
    spec = importlib.util.spec_from_file_location("test_report_mod", ROOT / "scripts" / "test_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doc_has_exactly_one_marker_pair():
    text = DOC.read_text(encoding="utf-8")
    assert text.count("<!-- tests:begin -->") == 1
    assert text.count("<!-- tests:end -->") == 1
    assert text.index("<!-- tests:begin -->") < text.index("<!-- tests:end -->")


def test_numbers_block_has_generated_format():
    rep = _report()
    parsed = rep.parse_block(DOC.read_text(encoding="utf-8"))  # raises ValueError on drift
    names = [r[0] for r in parsed["rows"]]
    assert len(names) == len(set(names)), "duplicate suite rows"


def test_known_failures_in_block_match_g01():
    rep = _report()
    parsed = rep.parse_block(DOC.read_text(encoding="utf-8"))
    g01 = ROOT / "docs" / "audit" / "G01_RED_TESTS.md"
    assert parsed["known"] == rep.known_failures(g01)


def test_script_output_round_trips(tmp_path):
    rep = _report()
    xml = tmp_path / "a.xml"
    suite = ET.Element("testsuite", tests="10", failures="1", errors="2", skipped="3",
                       timestamp="2026-10-12T10:00:00")
    ET.ElementTree(suite).write(xml)
    block = rep.render_block({"demo": rep.read_junit([str(xml)])}, {"demo": 4}, ["t.py::x"])
    parsed = rep.parse_block("intro\n" + block + "\nouter")
    assert parsed["rows"] == [("demo", 4, 1, 2, 3, 4, 10)]
    assert parsed["known"] == ["t.py::x"]


def test_hand_edited_block_is_rejected():
    rep = _report()
    block = rep.render_block({}, {}, [])
    bad = block.replace(rep.GENERATED, "typed by hand")
    with pytest.raises(ValueError):
        rep.parse_block(bad)


# --- Q-10 / Q-16: наборы, цифры и CI не расходятся -----------------------------------------------------------------

def _suites():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import test_suites
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    return test_suites


def _outside_block(text: str) -> str:
    return re.sub(r"<!-- tests:begin -->.*?<!-- tests:end -->", "", text, flags=re.S)


def test_every_test_file_belongs_to_an_automatic_suite():
    """Q-10: раньше около 60 модулей tests/observe_v1 не входили ни в один набор. Новый файл или папка без набора - ошибка."""
    ts = _suites()
    assert ts.uncovered() == []
    names = ts.suites()
    assert names["observe"], "observe-набор пуст: tests/observe_v1 снова выпал из прогонов"
    assert not set(names["observe"]) & set(names["docker"])


def test_doc_describes_every_suite():
    text = _outside_block(DOC.read_text(encoding="utf-8"))
    for token in ("orchestrator", "legacy", "observe", "docker", "--suite observe", "_pytest.run.lock", "VP_STRICT_SKIPS"):
        assert token in text, f"docs/TESTING.md не описывает: {token}"


def test_no_hand_typed_counts_outside_the_numbers_block():
    """Q-16: числа живут только в сгенерированном блоке. Любое «127 тестов» или «7 красных» руками рядом с блоком устаревает."""
    text = _outside_block(DOC.read_text(encoding="utf-8"))
    hits = re.findall(r"\b\d{2,}\s*(?:passed|failed|тест\w*|красн\w*)", text, flags=re.IGNORECASE)
    assert hits == [], f"числа руками вне блока: {hits}"


def test_block_rows_use_known_suite_names_and_a_real_date():
    rep = _report()
    text = DOC.read_text(encoding="utf-8")
    parsed = rep.parse_block(text)
    allowed = {"orchestrator", "orchestrator-fast", "legacy", "observe", "docker"}
    for name, *_ in parsed["rows"]:
        assert name in allowed, f"неизвестный набор в блоке: {name}"
    m = re.search(r"Дата прогона: (\d{4}-\d{2}-\d{2})", text)
    if m:
        assert date.fromisoformat(m.group(1)) <= date.today()


def test_ci_workflow_runs_every_suite_and_is_not_windows_bound():
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    ts = _suites()
    for token in ("run_orchestrator_tests.py", "--suite observe", "--suite legacy", "schedule:", "VP_STRICT_SKIPS", "VP_RUN_DOCKER_ONLY"):
        assert token in wf, f"CI не содержит: {token}"
    for path in ts.DOCKER_ONLY:
        assert path in wf, f"docker-модуль не запускается в CI: {path}"
    assert "windows" not in wf.lower() and "C:\\" not in wf
    assert "cmp scripts/diagnostics/gateway.py" in wf and (ROOT / "scripts" / "diagnostics" / "gateway.py").exists()
