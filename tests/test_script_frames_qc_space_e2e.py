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

    def _sid(fr) -> str:
        return str((fr.attrs or {}).get("camera_subdivide", {}).get("shot_id") or "")

    suffixes = [(_sid(fr).split("-", 1)[1] if "-" in _sid(fr) else _sid(fr)) for fr in frames]
    assert suffixes[0] == "S1-K1"
    assert "S1-K2" in suffixes
    assert "S2-K1" in suffixes
    by_id = {_sid(fr).split("-", 1)[1]: fr for fr in frames}
    act = {k: (fr.attrs or {}).get("shot01_action") for k, fr in by_id.items()}
    assert act["S1-K1"] == "Иван бежит по улице"
    assert any("открывает входную дверь" in str(v or "") for v in act.values()), act
    assert any("входит через входную дверь" in str(v or "") for v in act.values()), act

    s3_ids = [k for k in by_id if k.startswith("S3-")]
    # авто-«общий вид {место}» отключён для script_frames_qc
    assert not any(str(v or "").startswith("общий вид") for v in act.values()), act
    def _layout(fr):
        attrs = fr.attrs or {}
        lay = str(attrs.get("раскладка") or "")
        if lay:
            return lay
        # после expand раскладка часто остаётся в кадры[] родителя
        for shot in attrs.get("кадры") or []:
            if isinstance(shot, dict) and shot.get("раскладка"):
                return str(shot.get("раскладка") or "")
        return ""
    lay = {k: _layout(fr) for k, fr in by_id.items()}
    # если раскладка ещё не разложена по детям — хотя бы планы канонические
    if not any(lay[k] for k in s3_ids):
        plans = {
            k: str(
                ((by_id[k].attrs or {}).get("camera_subdivide") or {}).get("крупность")
                or (by_id[k].attrs or {}).get("план")
                or ""
            )
            for k in s3_ids
        }
        assert any(p in {"ОБЩИЙ", "СРЕДНИЙ", "СРЕДНЕ-КРУПНЫЙ", "КРУПНЫЙ", "ДЕТАЛЬ", "ДАЛЬНИЙ"} for p in plans.values()), plans
    else:
        assert any(("мать" in lay[k] and ("экране" in lay[k] or "сторон" in lay[k])) for k in s3_ids), {k: lay[k][:180] for k in s3_ids}
        assert any("Камера с юга" in lay[k] for k in s3_ids)
        process = [lay[k] for k in s3_ids if "ДЕЙСТВИЕ (процесс в одном кадре):" in lay[k]]
        assert process
        assert any("Угол камеры относительно референса ≥30°." in lay[k] for k in s3_ids)
        with_ang = [k for k in s3_ids if "Ракурс" in lay[k]]
        assert len(with_ang) >= 2
        assert lay[with_ang[0]].split("Ракурс", 1)[1][:40] != lay[with_ang[1]].split("Ракурс", 1)[1][:40]

    plans = {k: (fr.attrs or {}).get("площадка") for k, fr in by_id.items()}
    assert any(isinstance(plans[k], dict) and plans[k].get("зоны") for k in s3_ids)
    notes: list[str] = []
    for p in plans.values():
        if isinstance(p, dict):
            notes.extend(str(n) for n in (p.get("исправлено_кодом") or []))
    assert any("S1-" in n for n in notes)

    html = Path(out["report"]).read_text(encoding="utf-8")
    assert "Площадка" in html
    assert "<svg" in html
