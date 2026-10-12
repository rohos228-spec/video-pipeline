"""docs/TESTING.md: the numbers block must be in the format scripts/test_report.py produces.

No concrete counts are asserted (they change every run): only structure and that the rows add up.
"""
from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
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
