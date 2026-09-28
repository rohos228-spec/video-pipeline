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


def test_process_with_start_end_words_is_not_freeze() -> None:
    layout = (
        "ДЕЙСТВИЕ (процесс в одном кадре): бежит от старта к концу коридора, "
        "старт гонки, конец дистанции. План СРЕДНИЙ."
    )
    assert layout_is_freeze(layout) is False
    assert layout_is_freeze(layout.upper()) is False


def test_layout_end_stops_before_reference_lines() -> None:
    layout = (
        "СТАРТ (первый кадр, до действия): папка закрыта. План СРЕДНИЙ. "
        "КОНЕЦ (последний кадр действия): папка открыта. "
        "В кадре меняется: папка → открыта. "
        "В референсе: план ОБЩИЙ, ракурс фронт. Смена: план СРЕДНИЙ."
    )
    end = layout_end_text(layout)
    assert end == "папка открыта."
    no_ref = layout.replace("В референсе: план ОБЩИЙ, ракурс фронт. Смена: план СРЕДНИЙ.", "")
    no_ref = no_ref.replace("В кадре меняется: папка → открыта. ", "Референса нет — первый кадр места.")
    assert layout_end_text(no_ref) == "папка открыта."


def test_video_end_still_skips_legacy_shot2(tmp_path: Path) -> None:
    png = tmp_path / "frame_007_s2_abc.png"
    png.write_bytes(b"png")
    legacy = SimpleNamespace(number=7, attrs={SHOT2_PROMPT_ATTR: "второй шот: крупно руки"})
    freeze = SimpleNamespace(number=7, attrs={"раскладка": FREEZE})
    assert video_end_still(tmp_path, 7, 1, frame=legacy) is None
    assert video_end_still(tmp_path, 7, 1, frame=freeze) == png


def test_scene_image_ops_end_still_only_for_freeze() -> None:
    from app.services.montage_action_gpt import build_scene_image_ops

    legacy = SimpleNamespace(
        number=3, attrs={SHOT2_PROMPT_ATTR: "второй шот: крупно руки"}, voiceover_text="vo"
    )
    freeze = SimpleNamespace(number=4, attrs={"раскладка": FREEZE}, voiceover_text="vo")
    ops = build_scene_image_ops([legacy, freeze], passport={}, chain="шаг → шаг")
    shot2 = [(op["frame_number"], op["shot"]) for op in ops if op.get("shot") == 2]
    assert shot2 == [(4, 2)]


async def test_claim_shot2_video_skips_freeze_end_still(
    tmp_path: Path, monkeypatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Frame, Project
    from app.orchestrator.steps import generate_videos as gv

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))

    async def _shot1_on_disk(*_a, **_k):
        return tmp_path / "clip.mp4"

    monkeypatch.setattr(gv, "_scene_video_file_on_disk", _shot1_on_disk)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    anim = "камера медленно наезжает, рука кладёт ладонь на стопку листов"
    async with factory() as session:
        project = Project(id=77, slug="freeze-claim", topic="t", hero_mode="auto")
        freeze = Frame(
            project_id=77,
            number=1,
            voiceover_text="vo",
            status="planned",
            animation_prompt=anim,
            attrs={"раскладка": FREEZE, SHOT2_PROMPT_ATTR: "конечный кадр"},
        )
        legacy = Frame(
            project_id=77,
            number=2,
            voiceover_text="vo",
            status="planned",
            animation_prompt=anim,
            attrs={SHOT2_PROMPT_ATTR: "второй шот: крупно руки на столе"},
        )
        session.add_all([project, freeze, legacy])
        await session.flush()
        scenes = tmp_path / "scenes"
        videos = tmp_path / "videos"
        scenes.mkdir()
        videos.mkdir()
        (scenes / "frame_001_s2_a.png").write_bytes(b"png")
        (scenes / "frame_002_s2_b.png").write_bytes(b"png")
        claimed = await gv._claim_shot2_video_batch(
            session, project, videos, scenes, limit=5
        )
        assert [fr.number for fr, _p, _img in claimed] == [2]
    await engine.dispose()
