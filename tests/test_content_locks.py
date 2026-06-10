from app.models import Project
from app.services.content_locks import is_ui_locked, lock_ui_field


def test_lock_ui_field_blocks_xlsx_overwrite() -> None:
    p = Project(topic="t")
    assert not is_ui_locked(p, "script_text")
    lock_ui_field(p, "script_text")
    assert is_ui_locked(p, "script_text")
    lock_ui_field(p, "script_text")
    assert p.meta["ui_locked_fields"] == ["script_text"]
