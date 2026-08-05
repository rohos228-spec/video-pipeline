"""Check без Excel: assemble_check_master_prompt(db_sot=True)."""

from __future__ import annotations

from app.services.gpt_operator import assemble_check_master_prompt


def test_assemble_check_db_sot_forbids_excel_findings() -> None:
    text = assemble_check_master_prompt(
        [
            {
                "ok": True,
                "nodeKey": "n_excel_gpt_5",
                "text": "scene grammar unified agent",
            }
        ],
        check_fix=False,
        db_sot=True,
    )
    assert "db_check.json" in text
    assert "DB SoT" in text
    assert "XLSX_WRITEBACK" not in text
    assert "не ошибка" in text.casefold() or "НЕ ошибка" in text
    low = text.casefold()
    assert "tsv во вложении = книга" not in low


def test_assemble_check_legacy_still_mentions_tsv() -> None:
    text = assemble_check_master_prompt(
        [{"ok": True, "nodeKey": "n1", "text": "fill excel"}],
        check_fix=True,
        db_sot=False,
    )
    assert "XLSX_WRITEBACK" in text
    assert "# Лист:" in text or "TSV" in text
