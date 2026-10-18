"""Неблокирующее: unit-тесты web входят в tsc."""

import json
from pathlib import Path


def test_web_unit_tests_are_typechecked() -> None:
    cfg = json.loads(Path("web/tsconfig.json").read_text(encoding="utf-8"))
    exclude = cfg.get("exclude") or []
    assert "**/*.test.ts" not in exclude
    assert cfg["compilerOptions"]["allowImportingTsExtensions"] is True
    assert cfg["compilerOptions"]["noEmit"] is True
