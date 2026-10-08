"""W1-8 (A20, A21): числа в документах сверяются с реестром и кодом; чат не правит код механически.

Без сети, без БД, без платных вызовов: читаются только реестр способностей, список миграций,
markdown-документы и исходный текст роутера.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _doc(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def coverage() -> dict:
    from app.services.node_coverage import node_coverage
    return node_coverage()


def test_registry_numbers_are_the_agreed_ones(coverage):
    s = coverage["summary"]
    assert (s["managed"], s["observed"], s["human_only"]) == (17, 6, 1)
    assert s["total"] == 24


_TRIPLES = (
    re.compile(r"под управлением (\d+) типов, под наблюдением (\d+), только человек (\d+)"),
    re.compile(r"(\d+) под управлением, (\d+) под наблюдением, (\d+) только человек"),
)


@pytest.mark.parametrize("name", ["STAGE_STATUS_REPORT.md", "CUSTOMER_REPORT.md"])
def test_level_counts_in_docs_match_registry(coverage, name):
    s = coverage["summary"]
    want = (str(s["managed"]), str(s["observed"]), str(s["human_only"]))
    found = [m.groups() for rx in _TRIPLES for m in rx.finditer(_doc(name))]
    assert found, f"{name}: нет строки с числами уровней покрытия"
    assert all(f == want for f in found), f"{name}: {found} != {want}"


def test_customer_report_recovery_and_total_match_registry(coverage):
    text = _doc("CUSTOMER_REPORT.md")
    m = re.search(r"автоматическое восстановление у (\d+) типов", text)
    assert m and int(m.group(1)) == coverage["summary"]["with_automatic_recovery"]
    assert f"({coverage['summary']['total']} типа" in text


def test_node_coverage_confirmation_table_matches_registry(coverage):
    text = _doc("NODE_COVERAGE.md")
    start = text.index("## Подтверждено по нодам")
    block = text[start:text.index("\n## ", start + 5)]
    labels = {"под управлением": "managed", "под наблюдением": "observed", "только человек": "human_only"}
    got: dict[str, str] = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].startswith("`") and cells[1] in labels:
            got[cells[0].strip("`")] = labels[cells[1]]
    want = {r["node_type"]: r["level"] for r in coverage["nodes"]}
    assert got == want
    for cells_line in block.splitlines():
        if cells_line.startswith("| `"):
            assert re.search(r"\b[UDLH]\b", cells_line.split("|")[3]), cells_line


def test_node_coverage_doc_names_current_levels(coverage):
    text = _doc("NODE_COVERAGE.md")
    assert "17 под управлением / 6 под наблюдением / 1 только человек" in text
    assert "11 под управлением, 12 под наблюдением" not in text


def test_security_defaults_lists_all_migrations():
    from app.services.schema_migrations import MIGRATIONS
    ids = [m.id for m in MIGRATIONS]
    text = _doc("SECURITY_DEFAULTS.md")
    assert ids[0] == "0001_observation_core" and ids[-1].startswith("0016")
    assert f"`{ids[0]}` … `{ids[-1]}`" in text
    assert f"всего {len(ids)}" in text
    assert "`0001` … `0009_projects_columns`" not in text


def test_security_defaults_kill_switch_caveat_present():
    text = _doc("SECURITY_DEFAULTS.md")
    assert "рубильник" in text and "НЕ охватывал ручные" in text and "A07" in text


@pytest.mark.parametrize("name", ["STAGE_STATUS_REPORT.md", "CUSTOMER_REPORT.md"])
def test_old_green_count_is_never_stated_without_caveat(name):
    for line in _doc(name).splitlines():
        if "268" in line:
            assert "историч" in line.lower(), f"{name}: «268» без пометки об историчности: {line[:120]}"


def test_customer_report_states_red_legacy_suite():
    text = _doc("CUSTOMER_REPORT.md")
    legacy = _doc("LEGACY_TEST_FAILURES.md")
    assert "**21**" in legacy
    assert "21 падение" in text
    assert "29" in text
    assert "упали 6" not in text and "стало 6 " not in text


# --- A21: механический запрет правки кода и git push из чата --------------------------------

_ROUTER = ROOT / "app" / "web" / "routers" / "db_browser.py"
_DANGEROUS = {"apply_edits", "commit_and_push"}


def _enclosing_functions_calling(names: set[str]):
    src = _ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        used = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and node.id in names:
                used.add(node.id)
        if used:
            yield fn, ast.get_source_segment(src, fn) or "", used


def test_every_code_editing_call_in_chat_router_is_behind_the_flag():
    seen = list(_enclosing_functions_calling(_DANGEROUS))
    assert seen, "вызовы apply_edits/commit_and_push не найдены: тест устарел"
    for fn, body, used in seen:
        assert "chat_code_actions_enabled" in body, f"{fn.name} вызывает {used} без проверки флага"
        assert "_CHAT_CODE_ACTIONS" in body or fn.name == "orchestrator_confirm_git_push"


def test_chat_code_actions_default_is_off_and_cover_both_actions(monkeypatch):
    from app.settings import Settings
    from app.web.routers import db_browser
    monkeypatch.delenv("CHAT_CODE_ACTIONS_ENABLED", raising=False)
    assert Settings(_env_file=None).chat_code_actions_enabled is False
    assert set(db_browser._CHAT_CODE_ACTIONS) == {"edit_files", "git_commit_push"}
    assert "CHAT_CODE_ACTIONS_ENABLED" in db_browser._CHAT_CODE_OFF_MESSAGE


def test_chat_refusal_is_checked_before_dispatch():
    src = _ROUTER.read_text(encoding="utf-8")
    guard = src.index("any(k in act for k in _CHAT_CODE_ACTIONS)")
    assert guard < src.index('elif "edit_files" in act')
    assert guard < src.index('elif "git_commit_push" in act')
    assert '"refused"' in src[guard:guard + 400]
