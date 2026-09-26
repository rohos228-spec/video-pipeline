"""Тестовый прогон группы script_frames_qc с планом площадки (без сети).

Настоящий раннер нод (``enrich_xlsx.run``) на sqlite в памяти:
fw_action → fw_shots → fw_qc → fw_report. GPT заменён сценарием, который
делает типичные ошибки модели (дверь «уже открыта», идёт на месте,
камера перепрыгнула ось). Код группы должен их починить.

    python scripts/run_script_frames_qc_space_test.py [--out DIR]

Печатает кадры после каждой ноды и путь к HTML-отчёту.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid as _uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCENES: list[dict[str, Any]] = [
    {
        "place": "улица у дома",
        "steps": "бежит по улице → бежит к открытой двери дома",
        "vo": "Он бежал по улице, не разбирая дороги, к своему дому.",
        "shots": [
            {"действие": "Иван бежит по улице к открытой двери дома", "объект": "место",
             "закадр": "Он бежал по улице, не разбирая дороги, к своему дому.",
             "камера": {"где": "юг", "смотрит": "север"},
             "люди": [{"кто": "Иван", "где": "юг", "движется": "север"}]},
        ],
    },
    {
        "place": "прихожая",
        "steps": "уже в доме → идёт по коридору к туалету",
        "vo": "Влетел в прихожую, тяжело дыша, и сразу пошёл по коридору к туалету.",
        "shots": [
            {"действие": "Иван уже в прихожей, тяжело дышит", "объект": "место",
             "закадр": "Влетел в прихожую, тяжело дыша,",
             "люди": [{"кто": "Иван", "где": "юг", "движется": "север"}]},
            {"действие": "Иван идёт по коридору к туалету", "объект": "тело",
             "закадр": "и сразу пошёл по коридору к туалету.",
             "люди": [{"кто": "Иван", "где": "юг", "движется": "север"}]},
        ],
    },
    {
        "place": "кухня матери",
        "steps": "следователь садится напротив матери → спрашивает о сыне → мать отводит взгляд к окну",
        "vo": (
            "Следователь сел напротив матери и спросил, где её сын. "
            "Она отвела взгляд и долго молчала, глядя в окно."
        ),
        "shots": [
            {"действие": "следователь садится напротив матери", "объект": "двое",
             "закадр": "Следователь сел напротив матери",
             "камера": {"где": "юг", "смотрит": "север"},
             "люди": [{"кто": "следователь", "где": "запад", "лицом": "восток"},
                      {"кто": "мать", "где": "восток", "лицом": "запад"}]},
            {"действие": "следователь спрашивает о сыне", "объект": "двое",
             "закадр": "и спросил, где её сын.",
             "камера": {"где": "север", "смотрит": "юг"},
             "люди": [{"кто": "следователь", "где": "запад", "лицом": "восток"},
                      {"кто": "мать", "где": "восток", "лицом": "запад"}]},
            {"действие": "мать отводит взгляд к окну", "объект": "лицо",
             "закадр": "Она отвела взгляд и долго молчала, глядя в окно.",
             "люди": [{"кто": "мать", "где": "восток", "лицом": "север"}]},
        ],
    },
    {
        "place": "двор Орловской больницы",
        "steps": "санитары ведут его к корпусу",
        "vo": "В тысяча девятьсот девяносто втором году его направили в Орловскую больницу.",
        "shots": [
            {"действие": "санитары ведут его к корпусу", "объект": "место",
             "закадр": "В тысяча девятьсот девяносто втором году его направили в Орловскую больницу."},
        ],
    },
    {
        "place": "перрон Новокузнецка",
        "steps": "он выходит из вагона с сумкой",
        "vo": "Через три года он вернулся в Новокузнецк.",
        "shots": [
            {"действие": "он выходит из вагона с сумкой", "объект": "место",
             "закадр": "Через три года он вернулся в Новокузнецк.", "стык": "dissolve"},
        ],
    },
]

FULL_VO = " ".join(sc["vo"] for sc in SCENES)
BITS = [
    {"порядок": i, "изменение": f"{sc['place']}: {sc['steps']}", "якорь": sc["vo"].split(",")[0][:30]}
    for i, sc in enumerate(SCENES, start=1)
]
ACTION = "\n".join(
    f"{i}. {sc['place']} — {sc['steps']}\n({sc['vo']})" for i, sc in enumerate(SCENES, start=1)
)
PLAN = {
    "зоны": [
        {"id": "улица у дома", "предметы": [
            {"id": "входная дверь", "где": "север", "состояние": "закрыта"},
            {"id": "фонарь", "где": "восток"},
        ]},
        {"id": "прихожая", "что": "узкий коридор, обои в полоску", "предметы": [
            {"id": "входная дверь", "где": "юг"},
            {"id": "дверь туалета", "где": "север", "состояние": "закрыта"},
            {"id": "вешалка", "где": "запад"},
        ]},
        {"id": "кухня матери", "предметы": [
            {"id": "стол", "где": "центр"}, {"id": "окно", "где": "север"},
        ]},
        {"id": "двор Орловской больницы"},
        {"id": "перрон Новокузнецка"},
    ],
    "проходы": [{"из": "улица у дома", "в": "прихожая", "через": "входная дверь"}],
    "люди": [
        {"кто": "Иван", "зона": "улица у дома", "где": "юг"},
        {"кто": "следователь", "зона": "кухня матери", "где": "запад"},
        {"кто": "мать", "зона": "кухня матери", "где": "восток"},
    ],
}


def _kadry(n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for si, sc in enumerate(SCENES, start=1):
        for ki, shot in enumerate(sc["shots"], start=1):
            out.append({
                "id": f"{n}-S{si}-K{ki}",
                "parent_id": None if ki == 1 else f"{n}-S{si}-K1",
                "сцена": si,
                "место": sc["place"],
                **shot,
            })
    return out


class FakeGpt:
    """Сценарный GPT: отвечает по виду пачки из footer'а."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, **kw: Any):
        from app.services.gpt_operator_client import OperatorApiResult

        acc = str(kw.get("accompanying") or "")
        path = Path(kw["input_paths"][0])
        ctx = json.loads(path.read_text(encoding="utf-8"))
        frames = list(ctx.get("frames") or [])
        ops: list[dict[str, Any]] = []
        if "(главное действие" in acc:
            kind = "action"
        elif "(кадры-шаги" in acc:
            kind = "shots"
        elif "(QC полей кадров" in acc:
            kind = "qc"
        else:
            kind = "other"
        self.calls.append(kind)
        for fr in frames:
            vo = " ".join(str(fr.get("voiceover_text") or "").split())
            uid = str(fr["uuid"])
            n = int(fr.get("number") or 1)
            if kind == "action" and vo == FULL_VO:
                ops.append({"frame_uuid": uid, "fields": {
                    "главное_действие": ACTION, "площадка": PLAN,
                }})
            elif kind == "shots" and vo == FULL_VO:
                ops.append({"frame_uuid": uid, "fields": {"кадры": _kadry(n)}})
            elif kind == "qc" and vo.startswith("Он бежал"):
                # QC вернул сцену улицы с «открытой дверью» и без кадра на пороге.
                kadry = [dict(k) for k in (fr.get("кадры") or []) if isinstance(k, dict)]
                if kadry:
                    kadry = [kadry[0]]
                    kadry[0]["действие"] = "Иван бежит по улице к открытой двери дома"
                    kadry[0]["закадр"] = vo
                    kadry[0].pop("меняет", None)
                    ops.append({"frame_uuid": uid, "fields": {"кадры": kadry}})
        reply = json.dumps({"ops": ops}, ensure_ascii=False)
        return OperatorApiResult(
            reply_text=reply,
            output_paths=[path],
            apply_ops={"ops": ops},
        )


async def _setup(session_factory, data_dir: Path):
    from app.models import (
        Frame,
        Project,
        ProjectStatus,
        Workflow,
        WorkflowRun,
        WorkflowRunStatus,
    )
    from app.services.node_groups import insert_node_group

    kinds = ["topic", "plan", "script", "split", "hero"]
    nodes = [
        {"id": f"n_{k}", "type": k, "position": {"x": 80.0 + i * 290.0, "y": 200.0},
         "data": {"label": k}}
        for i, k in enumerate(kinds)
    ]
    edges = [
        {"id": f"e_{i}", "source": f"n_{a}", "target": f"n_{b}"}
        for i, (a, b) in enumerate(zip(kinds, kinds[1:], strict=False))
    ]
    async with session_factory() as session:
        wf = Workflow(name=f"wf-{_uuid.uuid4().hex[:6]}", is_default=True, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        project = Project(
            slug=f"space-{_uuid.uuid4().hex[:6]}",
            topic="тест площадки",
            status=ProjectStatus.new,
            meta={"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}},
        )
        session.add(project)
        await session.flush()
        session.add(WorkflowRun(
            project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
            nodes_snapshot=nodes, edges_snapshot=edges,
        ))
        await insert_node_group(session, project, "script_frames_qc")
        session.add(Frame(
            project_id=project.id,
            number=1,
            voiceover_text=FULL_VO,
            uuid=str(_uuid.uuid4()),
            sort_key=1.0,
            attrs={"биты": BITS},
        ))
        await session.commit()
        return int(project.id)


async def _run_node(session_factory, project_id: int, node_key: str) -> None:
    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import enrich_xlsx

    async with session_factory() as session:
        project = await session.get(Project, project_id)
        meta = dict(project.meta or {})
        meta["active_excel_gpt_node_key"] = node_key
        project.meta = meta
        project.status = ProjectStatus.enriching_1
        await session.commit()
        await enrich_xlsx.run(session, project, None)
        await session.commit()


async def _frames(session_factory, project_id: int):
    from sqlalchemy import select

    from app.models import Frame

    async with session_factory() as session:
        return list((await session.execute(
            select(Frame).where(Frame.project_id == project_id).order_by(Frame.sort_key, Frame.number)
        )).scalars().all())


def _dump(title: str, frames) -> None:
    print(f"\n==== {title}: кадров в БД {len(frames)} ====")
    for fr in frames:
        attrs = fr.attrs or {}
        cs = attrs.get("camera_subdivide") or {}
        print(
            f"#{fr.number} shot={cs.get('shot_id')} parent={cs.get('coverage_parent_id')} "
            f"план={cs.get('крупность')} зона={attrs.get('зона')}"
        )
        print(f"   vo: {fr.voiceover_text}")
        print(f"   действие: {attrs.get('shot01_action')}")
        if attrs.get("раскладка"):
            print(f"   раскладка: {attrs['раскладка']}")
        kadry = attrs.get("кадры")
        if isinstance(kadry, list) and len(kadry) > 1:
            for k in kadry:
                print(f"   · {k.get('id')} [{k.get('план')}/{k.get('стык')}] {k.get('действие')} | {k.get('закадр')}")
        plan = attrs.get("площадка")
        if isinstance(plan, dict) and plan.get("исправлено_кодом"):
            for t in plan["исправлено_кодом"]:
                print(f"   ✓ код починил: {t}")


def make_scope(factory):
    @asynccontextmanager
    async def scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return scope


async def run_group(factory, fake: FakeGpt, out_dir: Path | None = None) -> dict[str, Any]:
    """Прогон 4 нод группы; ``session_scope`` и GPT уже подменены вызывающим."""
    from app.models import Project

    pid = await _setup(factory, Path(tempfile.gettempdir()))
    for suffix in ("_fw_action", "_fw_shots", "_fw_qc", "_fw_report"):
        await _run_node(factory, pid, f"n_excel_gpt{suffix}")
        _dump(suffix, await _frames(factory, pid))
    async with factory() as session:
        project = await session.get(Project, pid)
        report = Path(str((project.meta or {}).get("shots_report_path") or ""))
    if out_dir and report.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / "space-plan-test-run-report.html"
        dst.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
        report = dst
    print(f"\nGPT вызовы: {fake.calls}")
    print(f"Отчёт: {report}")
    return {"frames": await _frames(factory, pid), "report": report, "calls": fake.calls}


async def main(out_dir: Path | None = None) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.db
    import app.services.apply_ops_batches as aob
    from app.models import Base
    from app.settings import settings

    tmp = Path(tempfile.mkdtemp(prefix="space-run-"))
    settings.data_dir = tmp / "data"
    settings.sqlite_path = tmp / "state.db"
    settings.harness_gate_disabled = True
    engine = create_async_engine(f"sqlite+aiosqlite:///{settings.sqlite_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    app.db.session_scope = make_scope(factory)
    fake = FakeGpt()
    aob.run_operator_api = fake
    try:
        return await run_group(factory, fake, out_dir)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    asyncio.run(main(args.out))
