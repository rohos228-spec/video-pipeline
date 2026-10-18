"""П.13: двоеточие после $brLabel не ломает разбор PowerShell."""

from __future__ import annotations

import re
from pathlib import Path


def test_studio_menu_braces_branch_label() -> None:
    text = Path("scripts/studio.ps1").read_text(encoding="utf-8-sig")
    assert "${brLabel}:" in text
    assert not re.search(r"\$brLabel:", text)
