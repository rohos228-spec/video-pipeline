"""Неблокирующее: ключ очереди, короткий список и persist после apply."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_montage_queue_keys_and_persist_ref() -> None:
    board = Path("web/src/components/canvas/assemble-montage-board.tsx").read_text(encoding="utf-8")
    assert "montageOpSelectKey" in board
    assert "serverQueueReplacesLocal" in board
    assert "applyRunningRef.current" in board
    assert "if (applyRunning) return;" not in board.split("const persistQueue")[1].split("useEffect")[0]
    subprocess.check_call(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            "web/src/lib/montage-queue-keys.test.ts",
        ]
    )
