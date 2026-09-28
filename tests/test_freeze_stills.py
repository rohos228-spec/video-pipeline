"""Freeze раскладка СТАРТ/КОНЕЦ → два still и last_frame для видео."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.freeze_stills import (
    frame_needs_end_still,
    layout_end_text,
    layout_is_freeze,
    layout_start_text,
    seed_end_still_prompt,
    video_end_still,
)
from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_STATUS_ATTR

FREEZE = (
    "СТАРТ (первый кадр, до действия): рука над стопкой листов, ладонь ещё не легла. "
    "План СРЕДНИЙ. Зона «стол». Камера с фронта, смотрит на стол. "
    "КОНЕЦ (последний кадр действия): ладонь лежит на стопке, пальцы расправлены."
)
PROCESS = (
    "ДЕЙСТВИЕ (процесс в одном кадре): идёт по коридору к двери. "
    "Картинка = этот процесс в разгаре, не пара «до/после». План СРЕДНИЙ."
)


def test_layout_is_freeze_detects_start_end() -> None:
    assert layout_is_freeze(FREEZE) is True
    assert layout_is_freeze(PROCESS) is False
    assert layout_is_freeze("") is False


def test_layout_start_and_end_text() -> None:
    assert "рука над стопкой" in layout_start_text(FREEZE)
    assert "ладонь лежит" in layout_end_text(FREEZE)
    assert layout_start_text(PROCESS) == ""
    assert layout_end_text(PROCESS) == ""


def test_seed_end_still_prompt_from_layout() -> None:
    fr = SimpleNamespace(attrs={"раскладка": FREEZE})
    assert seed_end_still_prompt(fr) is True
    prompt = str(fr.attrs[SHOT2_PROMPT_ATTR])
    assert "ладонь лежит" in prompt
    assert fr.attrs[SHOT2_STATUS_ATTR] == "image_prompt_ready"
    fr.attrs[SHOT2_PROMPT_ATTR] = "уже полный STYLE LOCK промт конца"
    assert seed_end_still_prompt(fr) is True
    assert fr.attrs[SHOT2_PROMPT_ATTR] == "уже полный STYLE LOCK промт конца"


def test_seed_skips_process_layout() -> None:
    fr = SimpleNamespace(attrs={"раскладка": PROCESS})
    assert seed_end_still_prompt(fr) is False
    assert SHOT2_PROMPT_ATTR not in fr.attrs


def test_frame_needs_end_still() -> None:
    freeze = SimpleNamespace(attrs={"раскладка": FREEZE})
    process = SimpleNamespace(attrs={"раскладка": PROCESS})
    seeded = SimpleNamespace(attrs={SHOT2_PROMPT_ATTR: "конечный кадр стиль"})
    assert frame_needs_end_still(freeze) is True
    assert frame_needs_end_still(process) is False
    assert frame_needs_end_still(seeded) is True


def test_video_end_still_only_for_shot1(tmp_path: Path) -> None:
    png = tmp_path / "frame_007_s2_abc.png"
    png.write_bytes(b"png")
    assert video_end_still(tmp_path, 7, 1) == png
    assert video_end_still(tmp_path, 7, 2) is None
    assert video_end_still(tmp_path, 8, 1) is None


def test_img_pr_footer_asks_both_prompts() -> None:
    from app.services.img_pr_batches import _BATCH_FOOTER

    assert "промт_картинки_2" in _BATCH_FOOTER
    assert "КОНЕЦ" in _BATCH_FOOTER


def test_writeable_keeps_op_when_only_shot2_is_long() -> None:
    from app.services.img_pr_batches import uuid_of_op, writeable_img_pr_ops

    long_end = (
        "Вертикальный кадр. Конечный стоп-кадр: ладонь на стопке. "
        "STYLE LOCK: Archival Noir Watercolour. Negative: text, watermark. "
        + ("scene " * 40)
    )
    kept = writeable_img_pr_ops(
        [
            {
                "frame_uuid": "aaaaaaaaaaaaaaaaaaaaaaaa",
                "fields": {"промт_картинки": "…", "промт_картинки_2": long_end},
            }
        ]
    )
    assert [uuid_of_op(op) for op in kept] == ["aaaaaaaaaaaaaaaaaaaaaaaa"]
    assert kept[0]["fields"]["промт_картинки_2"] == long_end


def test_layout_end_stops_before_items_line() -> None:
    layout = (
        "СТАРТ (первый кадр, до действия): ещё до жеста — папка закрыта. "
        "План ОБЩИЙ. Зона «кабинет». "
        "КОНЕЦ (последний кадр действия): жест завершён — папка открыта. "
        "Предметы в конце: стол следователя, папка с делом."
    )
    assert layout_is_freeze(layout) is True
    assert "папка закрыта" in layout_start_text(layout)
    assert "папка открыта" in layout_end_text(layout)
    assert "Предметы" not in layout_end_text(layout)


def test_seed_unskips_legacy_freeze_end() -> None:
    fr = SimpleNamespace(
        attrs={
            "раскладка": FREEZE,
            SHOT2_STATUS_ATTR: "skipped",
            SHOT2_PROMPT_ATTR: "уже полный промт конца",
        }
    )
    assert seed_end_still_prompt(fr) is True
    assert fr.attrs[SHOT2_STATUS_ATTR] == "image_prompt_ready"
    assert fr.attrs[SHOT2_PROMPT_ATTR] == "уже полный промт конца"


def test_ui_has_start_end_rows() -> None:
    board = (
        Path(__file__).resolve().parents[1]
        / "web/src/components/canvas/assemble-montage-board.tsx"
    )
    text = board.read_text(encoding="utf-8")
    assert 'key: "image2"' in text
    assert "Начальный кадр" in text
    assert "Конечный кадр" in text
    assert "layout_end" in text
    assert "shot_parent_number" in text
    assert "startStillUrl" in text
    assert "нет конечного кадра" not in text
    assert '{ key: "scene_info" as RowKey, label: "Сцена" }' in text
    assert text.index('{ key: "scene_info" as RowKey, label: "Сцена" }') < text.index(
        "...frameRow"
    )
