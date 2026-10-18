"""Формат change_id ядра и soft_delete WS2."""

from __future__ import annotations

import re

# Адаптер принимает только этот вид. Ядро выдаёт orch-appr-<task>-<n>.
_WS2_SOFT_DELETE = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")
_CORE = re.compile(r"^orch-appr-\d+-\d+$")


def test_minted_change_id_matches_ws2_soft_delete() -> None:
    for task, number in ((1, 1), (13, 2), (7, 1)):
        minted = f"orch-appr-{task}-{number}"
        assert _CORE.fullmatch(minted)
        assert _WS2_SOFT_DELETE.fullmatch(minted)
    assert _WS2_SOFT_DELETE.fullmatch("orch-appr-task-1") is None
    assert _WS2_SOFT_DELETE.fullmatch("orch-appr-7") is None
