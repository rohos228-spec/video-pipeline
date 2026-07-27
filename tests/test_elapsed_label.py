"""Формат и фиксация времени результата Create."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.generation_storage import (
    elapsed_from_iso,
    format_elapsed_min_sec,
    resolve_item_elapsed,
)


def test_format_elapsed_always_min_and_sec() -> None:
    assert format_elapsed_min_sec(0) == "0 мин 0 сек"
    assert format_elapsed_min_sec(45) == "0 мин 45 сек"
    assert format_elapsed_min_sec(60) == "1 мин 0 сек"
    assert format_elapsed_min_sec(125) == "2 мин 5 сек"
    assert format_elapsed_min_sec(None) == "0 мин 0 сек"


def test_elapsed_from_iso_done_without_end_does_not_use_now() -> None:
    start = "2026-07-27T10:00:00+03:00"
    # Без end и без live — None (не накручивать часы)
    assert elapsed_from_iso(start, None, live=False) is None
    # live=True — до сейчас (только для processing)
    live = elapsed_from_iso(start, None, live=True)
    assert live is not None and live > 0


def test_resolve_done_item_freezes_via_mtime(tmp_path: Path) -> None:
    start = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=67)
    meta = {
        "created_at": start.astimezone().isoformat(timespec="seconds"),
        "status": "done",
        # нет finished_at / elapsed_sec — старый sidecar
    }
    sec, label, persist = resolve_item_elapsed(
        meta, status="done", mtime=end.timestamp()
    )
    assert persist is True
    assert sec == 67
    assert label == "1 мин 7 сек"


def test_resolve_prefers_stored_elapsed() -> None:
    meta = {
        "created_at": "2026-01-01T00:00:00+03:00",
        "elapsed_sec": 42,
        "elapsed_label": "0 мин 42 сек",
    }
    sec, label, persist = resolve_item_elapsed(meta, status="done", mtime=None)
    assert sec == 42
    assert label == "0 мин 42 сек"
    assert persist is False


def test_backfill_writes_elapsed_once(tmp_path: Path, monkeypatch) -> None:
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    media = tmp_path / "generations" / "video" / "veo" / "20260727"
    media.mkdir(parents=True)
    mp4 = media / "100000_abc.mp4"
    mp4.write_bytes(b"\x00" * 64)
    start = datetime.now(timezone.utc) - timedelta(hours=10)
    # файл «создан» 67 сек после старта
    finished = start + timedelta(seconds=67)
    side = mp4.with_suffix(".json")
    side.write_text(
        json.dumps(
            {
                "id": mp4.stem,
                "media": "video",
                "model": "veo",
                "prompt": "x",
                "status": "done",
                "created_at": start.astimezone().isoformat(timespec="seconds"),
                "path": str(mp4),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # подменим mtime файла на finished
    import os

    os.utime(mp4, (finished.timestamp(), finished.timestamp()))

    items = gs.list_generation_files(kind="video", limit=10)
    assert items
    item = next(i for i in items if i["id"].endswith(mp4.stem) or mp4.stem in i["id"])
    assert item["elapsed_sec"] == 67
    # sidecar теперь содержит elapsed_sec навсегда
    meta2 = json.loads(side.read_text(encoding="utf-8"))
    assert meta2.get("elapsed_sec") == 67
    assert meta2.get("elapsed_label") == "1 мин 7 сек"

    # повторный list не меняет значение
    items2 = gs.list_generation_files(kind="video", limit=10)
    item2 = next(i for i in items2 if mp4.stem in i["id"])
    assert item2["elapsed_sec"] == 67
