"""Группа script_frames_qc целиком: fw_action → fw_shots → fw_qc → fw_report.

Сценарный GPT (scripts/run_script_frames_qc_space_test.py) делает ошибки:
«к открытой двери», «уже в прихожей», камера за осью, QC ломает сцену улицы.
Код площадки должен всё это починить через реальный раннер нод.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db
import app.services.apply_ops_batches as aob
import app.services.shots_report as shots_report
from app.models import Base
from app.settings import settings

_HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "run_script_frames_qc_space_test.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("space_harness", _HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_space_plan_through_six_node_group(monkeypatch, tmp_path) -> None:
    harness = _load_harness()
    import app.settings as settings_mod

    db = tmp_path / "state.db"
    # test_project_root перезагружает app.settings: патчим и старый, и текущий объект.
    for obj in {id(settings): settings, id(settings_mod.settings): settings_mod.settings}.values():
        monkeypatch.setattr(obj, "sqlite_path", db)
        monkeypatch.setattr(obj, "data_dir", tmp_path / "data")
        monkeypatch.setattr(obj, "harness_gate_disabled", True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    fake = harness.FakeGpt()
    monkeypatch.setattr(app.db, "session_scope", harness.make_scope(factory))
    monkeypatch.setattr(aob, "run_operator_api", fake)
    monkeypatch.setattr(shots_report, "find_project_root", lambda: tmp_path)
    try:
        out = await harness.run_group(factory, fake)
    finally:
        await engine.dispose()

    frames = out["frames"]
    shots = [
        (fr.attrs or {}).get("camera_subdivide", {}).get("shot_id") for fr in frames
    ]
    assert shots == [
        "1-S1-K1", "1-S1-K2", "1-S2-K1", "1-S2-K2",
        "1-S3-K1", "1-S3-K2", "1-S3-K3", "1-S4-K1", "1-S5-K1",
    ]
    by_id = {
        (fr.attrs or {})["camera_subdivide"]["shot_id"]: fr for fr in frames
    }
    act = {k: (fr.attrs or {}).get("shot01_action") for k, fr in by_id.items()}
    assert act["1-S1-K1"] == "Иван бежит по улице к закрытой двери дома"
    assert act["1-S1-K2"] == "Иван открывает входную дверь"
    assert act["1-S2-K1"] == "Иван входит через входную дверь, тяжело дышит"
    assert by_id["1-S1-K1"].voiceover_text.endswith(",")

    lay = {k: (fr.attrs or {}).get("раскладка") or "" for k, fr in by_id.items()}
    assert "мать — у восточной стороны, на экране справа" in lay["1-S3-K3"]
    assert "Камера с юга" in lay["1-S3-K3"]
    assert "Камера с востока" in lay["1-S3-K2"]

    plans = {k: (fr.attrs or {}).get("площадка") for k, fr in by_id.items()}
    assert plans["1-S3-K1"] and plans["1-S3-K1"]["зоны"]
    notes = plans["1-S1-K1"]["исправлено_кодом"]
    assert notes and all("[1-S1-" in n for n in notes)
    assert all("[1-S3-" in n for n in plans["1-S3-K1"]["исправлено_кодом"])
    assert plans["1-S2-K2"] is None

    html = Path(out["report"]).read_text(encoding="utf-8")
    assert "Площадка" in html
    assert "<svg" in html
