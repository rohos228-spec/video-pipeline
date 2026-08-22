"""Check без Excel: assemble_check_master_prompt(db_sot=True)."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.steps.enrich_xlsx import _check_mode_input_paths
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
    assert "не ошибка" in text.casefold() or "НЕ ошибка" in text
    low = text.casefold()
    assert "tsv во вложении = книга" not in low
    # Можно запретить маркером «НЕ пиши XLSX_WRITEBACK», но не требовать writeback.
    assert "ОБЯЗАТЕЛЕН блок --- XLSX_WRITEBACK" not in text
    assert "# Лист: Общий план" not in text or "НЕ пиши" in text


def test_assemble_check_legacy_still_mentions_tsv() -> None:
    text = assemble_check_master_prompt(
        [{"ok": True, "nodeKey": "n1", "text": "fill excel"}],
        check_fix=True,
        db_sot=False,
    )
    assert "XLSX_WRITEBACK" in text
    assert "# Лист:" in text or "TSV" in text


def test_check_mode_input_paths_drops_leftover_batches(tmp_path: Path) -> None:
    db = tmp_path / "db_check.json"
    db.write_text("{}", encoding="utf-8")
    leftover = [
        tmp_path / "db_frames.json",
        tmp_path / "db_frames_batch_01_L1.json",
        tmp_path / "gpt_reply.txt",
        tmp_path / "gpt_reply_raw.txt",
    ]
    for p in leftover:
        p.write_text("x", encoding="utf-8")
    img = tmp_path / "hero.png"
    img.write_bytes(b"\x89PNG")
    out = _check_mode_input_paths([db, *leftover, img], db)
    names = [p.name for p in out]
    assert names == ["db_check.json", "hero.png"]
