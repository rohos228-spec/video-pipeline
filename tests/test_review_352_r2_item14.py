"""Повторное ревью #352 №14: coverage apply не сносит чужие coverage-опы."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_coverage_apply_predicate_keeps_other_frames() -> None:
    board = Path("web/src/components/canvas/assemble-montage-board.tsx").read_text(
        encoding="utf-8"
    )
    assert "queueAfterCoverageApply(prev, numbers, changedTypes)" in board
    assert '!String(x.type).startsWith("coverage_")' not in board
    subprocess.check_call(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            "web/src/lib/montage-apply-queue.test.ts",
        ]
    )
