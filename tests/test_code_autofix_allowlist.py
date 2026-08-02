"""Allowlist / path safety for orchestrator code_autofix."""

from __future__ import annotations

import pytest

from app.services import code_autofix as ca


def test_deny_env() -> None:
    with pytest.raises(ca.CodeAutofixError):
        ca.assert_path_allowed(".env")


def test_deny_data() -> None:
    with pytest.raises(ca.CodeAutofixError):
        ca.assert_path_allowed("data/state.db")


def test_deny_traversal() -> None:
    with pytest.raises(ca.CodeAutofixError):
        ca.assert_path_allowed("app/../.env")


def test_allow_app_and_tests() -> None:
    assert ca.assert_path_allowed("app/services/code_autofix.py").startswith("app/")
    assert ca.assert_path_allowed("tests/test_code_autofix_allowlist.py").startswith(
        "tests/"
    )


def test_apply_edit_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    app = root / "app"
    app.mkdir()
    target = app / "sample.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(ca, "repo_root", lambda: root)

    out = ca.apply_edits(
        [{"path": "app/sample.py", "old_string": "x = 1", "new_string": "x = 2"}]
    )
    assert out["changed"] == ["app/sample.py"]
    assert target.read_text(encoding="utf-8") == "x = 2\n"


def test_apply_edit_ambiguous(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    app = root / "app"
    app.mkdir()
    (app / "sample.py").write_text("a\na\n", encoding="utf-8")
    monkeypatch.setattr(ca, "repo_root", lambda: root)
    with pytest.raises(ca.CodeAutofixError, match="встречается"):
        ca.apply_edits(
            [{"path": "app/sample.py", "old_string": "a", "new_string": "b"}]
        )
