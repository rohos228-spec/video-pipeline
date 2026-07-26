"""Контракт vp.check.v1 — парсер проверки GPT."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.check_analysis import (
    RESPONSE_FOOTER,
    SCHEMA_ID,
    append_response_footer,
    expected_artifacts_for_node_type,
    parse_check_analysis,
    parse_gate_status,
    write_analysis_json,
)


def test_parse_pass_json() -> None:
    raw = json.dumps(
        {
            "schema": SCHEMA_ID,
            "verdict": "pass",
            "summary": "всё ок",
            "checks": [{"id": "prompt_ok", "ok": True, "note": ""}],
            "forward": {"mode": "inherit", "paths": []},
            "fix": {"target": "none", "instructions": ""},
        },
        ensure_ascii=False,
    )
    a = parse_check_analysis(raw)
    assert a.verdict == "pass"
    assert a.ok
    assert a.summary == "всё ок"
    assert a.checks[0].id == "prompt_ok"
    assert parse_gate_status(raw) == "pass"


def test_parse_fail_and_broken() -> None:
    fail = parse_check_analysis(
        '{"schema":"vp.check.v1","verdict":"fail","summary":"брак","checks":[]}'
    )
    assert fail.verdict == "fail"
    assert not fail.ok

    broken = parse_check_analysis("просто текст без json")
    assert broken.verdict == "fail"
    assert broken.raw_error
    assert parse_gate_status("") == "fail"


def test_parse_fenced_and_aliases() -> None:
    fenced = """
Вот результат:
```json
{"schema":"vp.check.v1","verdict":"ok","summary":"норм","checks":[]}
```
"""
    a = parse_check_analysis(fenced)
    assert a.verdict == "pass"

    auto = parse_check_analysis('{"decision":"approved","criteria":[]}')
    assert auto.verdict == "pass"

    rej = parse_check_analysis('{"decision":"rejected","reason":"x"}')
    assert rej.verdict == "fail"


def test_write_analysis_json(tmp_path: Path) -> None:
    a = parse_check_analysis(
        '{"schema":"vp.check.v1","verdict":"pass","summary":"ok","checks":[]}'
    )
    path = write_analysis_json(tmp_path / "excel_gpt_uploads" / "n1", a)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["verdict"] == "pass"
    assert data["schema"] == SCHEMA_ID


def test_footer_and_artifacts() -> None:
    assert SCHEMA_ID in RESPONSE_FOOTER
    assert "verdict" in RESPONSE_FOOTER
    out = append_response_footer("проверь картинки")
    assert "проверь картинки" in out
    assert SCHEMA_ID in out
    # повторно не дублируем
    assert append_response_footer(out) == out
    assert "scenes/" in expected_artifacts_for_node_type("images")
    assert "characters/" in expected_artifacts_for_node_type("hero")
    assert "project.xlsx" in expected_artifacts_for_node_type("plan")
