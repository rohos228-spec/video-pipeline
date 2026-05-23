"""Тесты на tool-уровень AI-агента (без LLM, прямые вызовы tools).

Запуск:  pytest -q tests/test_ai_agent_tools.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.ai_agent.tools import ALL_TOOLS, get_openai_tools_schema, get_tool
from app.ai_agent.tools._spec import ToolContext

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_ctx() -> ToolContext:
    return ToolContext(repo_root=REPO_ROOT, tool_timeout_sec=10)


# ──────────────────────────── registry ──────────────────────────────────────


def test_registry_has_expected_tools() -> None:
    """Все ключевые tools зарегистрированы."""
    expected = {
        "final_answer",
        "read_file",
        "list_dir",
        "search_code",
        "describe_db",
        "db_query",
        "git_status",
        "git_diff",
        "git_log",
        "gh_pr_list",
        "gh_pr_view",
        "run_ruff",
        "run_pytest",
        "run_mypy",
    }
    missing = expected - set(ALL_TOOLS.keys())
    assert not missing, f"missing tools: {missing}"


def test_get_openai_tools_schema_format() -> None:
    """Schema в правильном OpenAI tools формате."""
    schema = get_openai_tools_schema()
    assert len(schema) >= 10
    for item in schema:
        assert item["type"] == "function"
        fn = item["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params


def test_get_openai_tools_schema_include_edit_flag() -> None:
    """include_edit=False скрывает HITL-tools (для QA-режима)."""
    all_schema = get_openai_tools_schema(include_edit=True)
    qa_schema = get_openai_tools_schema(include_edit=False)
    # Сейчас HITL-tools ещё не добавлены (Phase I.4), но flag должен работать.
    # В Phase I.4 qa_schema будет короче.
    assert len(qa_schema) <= len(all_schema)


def test_final_answer_is_terminal() -> None:
    assert get_tool("final_answer").is_terminal is True


# ──────────────────────────── read_file ─────────────────────────────────────


def test_read_file_basic() -> None:
    async def run():
        return await ALL_TOOLS["read_file"].run(
            {"path": "README.md"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is True
    assert r["total_lines"] > 0
    assert "video-pipeline" in r["content"]


def test_read_file_slice() -> None:
    async def run():
        return await ALL_TOOLS["read_file"].run(
            {"path": "README.md", "line_offset": 0, "line_limit": 3},
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is True
    assert r["lines_shown"] == [1, 3]
    # max 3 lines
    assert r["content"].count("\n") <= 3


def test_read_file_safety_blocks_env() -> None:
    async def run():
        return await ALL_TOOLS["read_file"].run(
            {"path": ".env"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "safety" in r["error"].lower() or "forbidden" in r["error"].lower()


def test_read_file_not_found() -> None:
    async def run():
        return await ALL_TOOLS["read_file"].run(
            {"path": "nonexistent_xyz_12345.txt"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "not found" in r["error"].lower()


def test_read_file_redacts_secrets() -> None:
    """Если в читаемом файле есть секрет, он замаскирован."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix=".tmp", delete=False
    ) as tmp:
        tmp.write("key: sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb\nother\n")
        tmp_path = Path(tmp.name)

    try:
        rel = tmp_path.relative_to(REPO_ROOT).as_posix()

        async def run():
            return await ALL_TOOLS["read_file"].run(
                {"path": rel}, make_ctx()
            )

        r = asyncio.run(run())
        assert r["ok"] is True
        assert "sk-aitunnel-cNTc" not in r["content"]
        assert "REDACTED" in r["content"]
    finally:
        tmp_path.unlink(missing_ok=True)


# ──────────────────────────── list_dir ──────────────────────────────────────


def test_list_dir_basic() -> None:
    async def run():
        return await ALL_TOOLS["list_dir"].run(
            {"path": "app/ai_agent"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is True
    assert r["total"] > 0
    names = {e["name"] for e in r["entries"]}
    assert "client.py" in names
    assert "safety.py" in names


def test_list_dir_blocks_outside_repo() -> None:
    async def run():
        return await ALL_TOOLS["list_dir"].run(
            {"path": "/etc"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "safety" in r["error"].lower() or "outside" in r["error"].lower()


# ──────────────────────────── search_code ───────────────────────────────────


def test_search_code_basic() -> None:
    async def run():
        return await ALL_TOOLS["search_code"].run(
            {"pattern": "class AISession", "max_matches": 5}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is True
    assert r["total"] >= 1
    # должен найтись в models.py
    found_in_models = any(
        m["path"].endswith("models.py") for m in r["matches"]
    )
    assert found_in_models


def test_search_code_with_glob() -> None:
    async def run():
        return await ALL_TOOLS["search_code"].run(
            {"pattern": "def test_", "glob": "tests/*.py", "max_matches": 3},
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is True
    # все matches только из tests/
    for m in r.get("matches", []):
        assert m["path"].startswith("tests/")


# ──────────────────────────── db_query ──────────────────────────────────────


def test_db_query_blocks_insert() -> None:
    async def run():
        return await ALL_TOOLS["db_query"].run(
            {"sql": "INSERT INTO projects VALUES (1)"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "SELECT" in r["error"] or "запрещ" in r["error"].lower()


def test_db_query_blocks_drop() -> None:
    async def run():
        return await ALL_TOOLS["db_query"].run(
            {"sql": "DROP TABLE projects"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False


def test_db_query_blocks_update() -> None:
    async def run():
        return await ALL_TOOLS["db_query"].run(
            {"sql": "UPDATE projects SET status='x' WHERE id=1"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False


def test_db_query_blocks_multistatement() -> None:
    async def run():
        return await ALL_TOOLS["db_query"].run(
            {"sql": "SELECT 1; DROP TABLE projects"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert not r["ok"]  # any blocking message is ok


def test_db_query_blocks_pragma() -> None:
    async def run():
        return await ALL_TOOLS["db_query"].run(
            {"sql": "SELECT * FROM sqlite_master; PRAGMA database_list"},
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is False


# ──────────────────────────── git_status / git_log ──────────────────────────


def test_git_status() -> None:
    async def run():
        return await ALL_TOOLS["git_status"].run({}, make_ctx())

    r = asyncio.run(run())
    assert r["ok"] is True
    # должен показать current branch
    assert "##" in r["output"]


def test_git_log() -> None:
    async def run():
        return await ALL_TOOLS["git_log"].run({"n": 3}, make_ctx())

    r = asyncio.run(run())
    assert r["ok"] is True
    assert len(r["commits"]) <= 3
    # каждый коммит — sha + message
    for c in r["commits"]:
        if c:  # пропускаем пустые
            parts = c.split(" ", 1)
            assert len(parts) >= 1


# ──────────────────────────── final_answer ──────────────────────────────────


def test_final_answer() -> None:
    async def run():
        return await ALL_TOOLS["final_answer"].run(
            {"answer": "тестовый итог"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is True
    assert r["answer"] == "тестовый итог"


# ──────────────────────────── edit_file (HITL) ──────────────────────────────


def test_edit_file_basic(tmp_path: Path) -> None:
    """edit_file заменяет уникальную подстроку."""
    # Создаём временный файл В REPO (иначе safety заблокирует — path outside)
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix=".tmp", delete=False
    ) as tmp:
        tmp.write("foo bar baz")
        tmp_p = Path(tmp.name)
    rel = tmp_p.relative_to(REPO_ROOT).as_posix()
    try:
        async def run():
            return await ALL_TOOLS["edit_file"].run(
                {"path": rel, "old_string": "bar", "new_string": "QUX"},
                make_ctx(),
            )

        r = asyncio.run(run())
        assert r["ok"] is True
        assert tmp_p.read_text() == "foo QUX baz"
    finally:
        tmp_p.unlink(missing_ok=True)


def test_edit_file_blocks_secret_in_new_string() -> None:
    """secret-scan на new_string не пропускает токены."""
    async def run():
        return await ALL_TOOLS["edit_file"].run(
            {
                "path": "README.md",
                "old_string": "x",
                "new_string": "key=sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb",
            },
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "secret" in r["error"].lower() or "ключ" in r["error"].lower()


def test_edit_file_blocks_env_path() -> None:
    async def run():
        return await ALL_TOOLS["edit_file"].run(
            {"path": ".env", "old_string": "x", "new_string": "y"},
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "safety" in r["error"].lower() or "forbidden" in r["error"].lower()


def test_edit_file_requires_unique_old_string(tmp_path: Path) -> None:
    """Не-уникальная old_string → error с подсказкой."""
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix=".tmp", delete=False
    ) as tmp:
        tmp.write("aa\nbb\naa\n")
        tmp_p = Path(tmp.name)
    rel = tmp_p.relative_to(REPO_ROOT).as_posix()
    try:
        async def run():
            return await ALL_TOOLS["edit_file"].run(
                {"path": rel, "old_string": "aa", "new_string": "cc"},
                make_ctx(),
            )

        r = asyncio.run(run())
        assert r["ok"] is False
        assert "уникальн" in r["error"].lower() or "unique" in r["error"].lower()
    finally:
        tmp_p.unlink(missing_ok=True)


def test_edit_file_old_not_found() -> None:
    async def run():
        return await ALL_TOOLS["edit_file"].run(
            {
                "path": "README.md",
                "old_string": "zzz-definitely-not-here-xyz-12345",
                "new_string": "replacement",
            },
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "not found" in r["error"].lower()


# ──────────────────────────── write_file (HITL) ─────────────────────────────


def test_write_file_blocks_env() -> None:
    async def run():
        return await ALL_TOOLS["write_file"].run(
            {"path": ".env", "content": "FOO=BAR\n"}, make_ctx()
        )

    r = asyncio.run(run())
    assert r["ok"] is False


def test_write_file_blocks_secrets_in_content() -> None:
    async def run():
        return await ALL_TOOLS["write_file"].run(
            {
                "path": "tests/test_new_dummy.tmp",
                "content": "api = 'sk-aitunnel-cNTc7vWTMaSAAC0B8KCdcEaJ7L1QTcJb'",
            },
            make_ctx(),
        )

    r = asyncio.run(run())
    assert r["ok"] is False
    assert "secret" in r["error"].lower() or "ключ" in r["error"].lower()


def test_write_file_creates_new(tmp_path: Path) -> None:
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", dir=REPO_ROOT / "tests", suffix=".tmp", delete=False
    ) as tmp:
        tmp_p = Path(tmp.name)
    tmp_p.unlink()  # remove so write_file creates it
    rel = tmp_p.relative_to(REPO_ROOT).as_posix()
    try:
        async def run():
            return await ALL_TOOLS["write_file"].run(
                {"path": rel, "content": "новый файл\n"}, make_ctx()
            )

        r = asyncio.run(run())
        assert r["ok"] is True
        assert r["existed_before"] is False
        assert tmp_p.read_text() == "новый файл\n"
    finally:
        tmp_p.unlink(missing_ok=True)


# ──────────────────────────── git_branch (HITL) ─────────────────────────────


def test_git_branch_blocks_reserved_names() -> None:
    """main, vetka-final, legacy/* запрещены как имена."""
    for name in ("main", "vetka-final", "legacy/x"):
        async def run(_n=name):
            return await ALL_TOOLS["git_branch"].run(
                {"name": _n}, make_ctx()
            )

        r = asyncio.run(run())
        assert r["ok"] is False, f"branch '{name}' должен быть запрещён"


# ──────────────────────────── HITL flag check ───────────────────────────────


def test_hitl_tools_marked_correctly() -> None:
    """Опасные tools имеют is_hitl=True."""
    must_be_hitl = {
        "edit_file",
        "write_file",
        "git_branch",
        "git_commit",
        "gh_pr_create",
    }
    for name in must_be_hitl:
        ts = get_tool(name)
        assert ts is not None, f"missing tool: {name}"
        assert ts.is_hitl is True, f"{name} must have is_hitl=True"


def test_readonly_tools_marked_correctly() -> None:
    """Read-only tools имеют is_hitl=False."""
    must_be_readonly = {
        "read_file",
        "list_dir",
        "search_code",
        "describe_db",
        "db_query",
        "git_status",
        "git_diff",
        "git_log",
        "gh_pr_list",
        "gh_pr_view",
        "run_ruff",
        "run_pytest",
        "run_mypy",
    }
    for name in must_be_readonly:
        ts = get_tool(name)
        assert ts is not None, f"missing tool: {name}"
        assert ts.is_hitl is False, f"{name} must have is_hitl=False"
