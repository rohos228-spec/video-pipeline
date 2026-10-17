"""Сквозной прогон сцены проекта 65: Кадры → VO → QC → список рендера.

В репозитории нет фикстуры и данных project-65. Сцена собрана в тесте
в той же форме кадров, что уже проходит пайплайн покрытия: id, действие,
закадр, план, ракурс, место. Проекту задан id 65.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, FrameText, Project

CELL = (
    "Он вошёл в кабинет следователя и снял пальто. "
    "Затем он сел к столу и открыл толстую папку с делом."
)
ACTION = (
    "1. кабинет следователя — входит в кабинет → сел к столу\n"
    f"({CELL})"
)
DEAD_ID = "65-K3"
DEAD_TEXT = "МЁРТВЫЙ КУСОК КОТОРЫЙ НЕЛЬЗЯ ОТДАТЬ"
UID = "65" * 16


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _kadry(ops: list[dict]) -> list[dict]:
    raw = ops[0]["fields"]["кадры"]
    assert isinstance(raw, list)
    return raw


def _active_vo(shots: list[dict]) -> str:
    from app.services.frame_tombstones import shot_is_active

    parts = [
        _norm(str(shot.get("закадр") or ""))
        for shot in shots
        if shot_is_active(shot) and _norm(str(shot.get("закадр") or ""))
    ]
    return _norm(" ".join(parts))


def test_project65_scene_keeps_inactive_shots_through_pipeline(tmp_path: Path) -> None:
    """Неактивный шот остаётся в кадры[] и не попадает в VO, QC и рендер."""
    import asyncio

    asyncio.run(_run(tmp_path))


async def _run(tmp_path: Path) -> None:
    from app.services.apply_ops_batches import repair_shot_vo_ops, shots_coverage_ops_reason
    from app.services.frame_tombstones import mark_shots_inactive, shot_is_active
    from app.services.montage_scene_editor import shot_sequence_text
    from app.services.montage_scene_improve import build_improve_qc_prompt, qc_reasons
    from app.services.scene_shot_grammar import apply_grammar_to_ops, count_action_steps
    from app.services.shots_report import build_shots_report_model
    from app.services.vo_shot_expand import expand_vo_cells_into_shots

    assert count_action_steps(ACTION) == 2
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        deleted: list[object] = []
        original_delete = session.delete

        def _spy(instance: object):
            deleted.append(instance)
            return original_delete(instance)

        session.delete = _spy  # type: ignore[method-assign]

        project = Project(
            id=65,
            slug="project-65",
            topic="кабинет следователя",
            hero_mode="auto",
        )
        session.add(project)
        await session.flush()
        frame = Frame(
            project_id=65,
            number=1,
            uuid=UID,
            sort_key=1.0,
            voiceover_text=CELL,
            status="planned",
            attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": UID}},
        )
        session.add(frame)
        await session.flush()
        session.add(
            FrameText(
                project_id=65,
                frame_id=frame.id,
                kind="voiceover",
                text=CELL,
                sort_key=1.0,
            )
        )
        still = tmp_path / "frame_001.png"
        still.write_bytes(b"project-65-still")
        session.add(
            Artifact(
                project_id=65,
                frame_id=frame.id,
                kind=ArtifactKind.scene_image,
                uuid="project-65-still",
                path=str(still),
                meta={"shot": "65-K1"},
            )
        )
        await session.flush()

        shots = [
            {
                "id": "65-K1",
                "parent_id": None,
                "план": "СРЕДНИЙ",
                "ракурс": "фронт",
                "место": "кабинет следователя",
                "действие": "входит в кабинет",
                "объект": "тело",
                "закадр": "",
                "порядок": 1,
                "якорь_n": 1,
                "движение": "статика",
                "стык": "cut",
            },
            {
                "id": "65-K2",
                "parent_id": "65-K1",
                "план": "КРУПНЫЙ",
                "ракурс": "3/4",
                "место": "кабинет следователя",
                "действие": "сел к столу",
                "объект": "тело",
                "закадр": "",
                "порядок": 2,
                "якорь_n": 1,
                "движение": "статика",
                "стык": "cut",
            },
            {
                "id": DEAD_ID,
                "parent_id": "65-K1",
                "план": "ДЕТАЛЬ",
                "ракурс": "сверху",
                "место": "кабинет следователя",
                "действие": "смотрит в окно",
                "объект": "тело",
                "закадр": DEAD_TEXT,
                "порядок": 3,
                "якорь_n": 1,
                "движение": "статика",
                "стык": "cut",
            },
        ]
        shots[2] = mark_shots_inactive([shots[2]], where="project-65")[0]
        stored_n = len(shots)
        attrs = dict(frame.attrs or {})
        attrs["кадры"] = shots
        attrs["главное_действие"] = ACTION
        frame.attrs = attrs
        frames = [{
            "uuid": UID,
            "number": 1,
            "voiceover_text": CELL,
            "закадр": CELL,
            "главное_действие": ACTION,
        }]
        ops = [{"frame_uuid": UID, "fields": {"кадры": shots, "главное_действие": ACTION}}]

        async def counts() -> tuple[int, int, int]:
            n_frames = await session.scalar(select(func.count()).select_from(Frame))
            n_texts = await session.scalar(select(func.count()).select_from(FrameText))
            n_arts = await session.scalar(select(func.count()).select_from(Artifact))
            return int(n_frames or 0), int(n_texts or 0), int(n_arts or 0)

        base_rows = await counts()
        assert base_rows == (1, 1, 1)

        def persist(step: str) -> list[dict]:
            kadry = _kadry(ops)
            assert len(kadry) == stored_n, step
            assert [shot.get("id") for shot in kadry] == ["65-K1", "65-K2", DEAD_ID]
            dead = kadry[2]
            assert dead.get("активен") is False
            assert dead.get("закадр") == DEAD_TEXT
            fresh = dict(frame.attrs or {})
            fresh["кадры"] = kadry
            frame.attrs = fresh
            assert len(frame.attrs["кадры"]) == stored_n
            return kadry

        # Кадры
        apply_grammar_to_ops(ops, frames)
        persist("кадры")
        assert await counts() == base_rows

        # VO
        repair_shot_vo_ops(ops, frames)
        kadry = persist("vo")
        assert _active_vo(kadry) == _norm(CELL)
        assert DEAD_TEXT not in _active_vo(kadry)
        assert not any(shot_is_active(shot) and shot.get("id") == DEAD_ID for shot in kadry)
        assert await counts() == base_rows

        # QC
        reason = shots_coverage_ops_reason(ops, frames)
        kadry = persist("qc")
        units = [{"n": 1, "бюджет": 2, "закадр": CELL}]
        hard, soft = qc_reasons(kadry, units)
        from types import SimpleNamespace

        prompt = build_improve_qc_prompt(
            parent=SimpleNamespace(uuid=UID, number=1),
            vo=CELL,
            shots=kadry,
            units=units,
            reasons=hard or ["проверка"],
        )
        qc_blob = " ".join([reason or "", *hard, *soft, prompt])
        assert DEAD_ID not in qc_blob
        assert DEAD_TEXT not in qc_blob
        assert _active_vo(kadry) == _norm(CELL)
        assert await counts() == base_rows

        # Список рендера: отчёт шотов и разворот ячейки в кадры.
        report = build_shots_report_model([frame])
        report_ids = [str(row.get("id") or "") for row in report["shots"]]
        report_blob = " ".join(
            str(row.get("vo") or "") + str(row.get("action") or "")
            for row in report["shots"]
        )
        assert DEAD_ID not in report_ids
        assert DEAD_TEXT not in report_blob
        assert report_ids == ["65-K1", "65-K2"]
        sequence = shot_sequence_text(frame, [frame])
        assert DEAD_TEXT not in sequence
        assert "смотрит в окно" not in sequence

        ordered, _report = await expand_vo_cells_into_shots(session, project, [frame])
        assert len(frame.attrs["кадры"]) == stored_n
        assert frame.attrs["кадры"][2]["id"] == DEAD_ID
        assert frame.attrs["кадры"][2]["активен"] is False
        assert frame.attrs["кадры"][2]["закадр"] == DEAD_TEXT
        rendered_ids = []
        rendered_pieces: list[str] = []
        rendered_blob: list[str] = []
        for fr in ordered:
            cs = (fr.attrs or {}).get("camera_subdivide") or {}
            rendered_ids.append(str(cs.get("shot_id") or ""))
            rendered_pieces.append(str(cs.get("vo_shot") or ""))
            rendered_blob.append(str(fr.voiceover_text or ""))
            rendered_blob.append(str(cs.get("vo_shot") or ""))
        assert DEAD_ID not in rendered_ids
        assert DEAD_TEXT not in " ".join(rendered_blob)
        assert _norm(" ".join(part for part in rendered_pieces if part)) == _norm(CELL)

        rows_after = await counts()
        assert rows_after[0] >= base_rows[0]
        assert rows_after[1] == base_rows[1]
        assert rows_after[2] == base_rows[2]
        assert still.is_file()
        assert still.read_bytes() == b"project-65-still"
        assert not any(isinstance(obj, Frame) for obj in deleted)
    await engine.dispose()
