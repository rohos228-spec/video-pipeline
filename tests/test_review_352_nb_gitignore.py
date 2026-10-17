"""Неблокирующее ревью #352: tracked-промты видны git, *.bak* не глотает .bake."""

from __future__ import annotations

import subprocess


def _ignored(path: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd="/workspace",
        check=False,
    )
    return proc.returncode == 0


def test_tracked_prompt_is_not_ignored() -> None:
    assert _ignored("prompts/03_razbivka/default.md") is False


def test_bak_star_does_not_swallow_bake() -> None:
    assert _ignored("notes.bake") is False
    assert _ignored("local-edit.bak") is True
