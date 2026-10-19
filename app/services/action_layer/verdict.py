"""Вердикт keep/modify для приёмки F2.

По умолчанию keep. modify — исполнитель зовёт удаление или перезапись
в API студии (детектор в tests/actions/test_whitelist_no_exec.py).
"""

from __future__ import annotations

# Исполнители, у которых детектор видит delete/remove/clear/overwrite
# либо unlink/rmtree внутри обработчика студии.
MODIFY_IDS: frozenset[str] = frozenset({
    "group.delete",
    "montage.delete_child_frame",
    "montage.delete_frame",
    "montage.delete_image",
    "montage.delete_manual_ref",
    "montage.delete_video",
    "montage.unlink_ref",
    "node.clear_check_agent",
    "project.upload_xlsx",
    "prompt.delete_master",
    "storage.clear_files",
})


def verdict_of(action_id: str) -> str:
    if action_id in MODIFY_IDS:
        return "modify"
    return "keep"
