"""Dev-сид для ручной проверки панели «Сцена» на доске монтажа.

Не часть пайплайна: создаёт в проекте ячейку закадра с лестницей T/X
(родитель + два дочерних шота), биты с якорями и ставит на канвас группу
``script_frames_qc``, иначе строка «Сцена» на доске скрыта.

    python3 scripts/dev_seed_montage_scene.py [project_id]
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select

from app.db import session_scope
from app.models import Frame, Project
from app.project_db import (
    init_project_db,
    project_db_session_scope,
    sync_project_row_to_project_db,
)

CELLS = [
    {
        "vo": (
            "В сентябре 1911 года в кабинет следователя вошёл человек "
            "с портфелем. Он достал папку и положил её на стол. "
            "На первой странице стояло чужое имя."
        ),
        "place": "кабинет следователя",
        "scene_action": "приносит папку и открывает чужое дело",
        "template": "T2",
        "shots": [
            ("ОБЩИЙ", "кабинет следователя. вошёл, снял пальто, встал у стола"),
            ("ДЕТАЛЬ", "руки достают папку из портфеля, видна тесьма и номер дела"),
            ("КРУПНЫЙ", "лицо следователя, взгляд упал на чужое имя на странице"),
        ],
        "anchors": [
            "В сентябре 1911 года",
            "Он достал папку",
            "На первой странице стояло чужое имя",
        ],
    },
    {
        "vo": (
            "Через два дня дело забрали в столицу. Курьер увёз портфель "
            "в ночном поезде."
        ),
        "place": "перрон вокзала",
        "scene_action": "дело уезжает из города",
        "template": "T6",
        "shots": [
            ("ОБЩИЙ", "перрон вокзала ночью, пар от состава, курьер идёт к вагону"),
            ("СРЕДНИЙ", "курьер поднимает портфель на ступень вагона, проводник светит фонарём"),
        ],
        "anchors": ["Через два дня дело забрали", "Курьер увёз портфель"],
    },
]


def _split_by_anchors(text: str, anchors: list[str]) -> list[str]:
    from app.services.montage_scene_editor import split_vo_by_anchors

    return split_vo_by_anchors(text, anchors)


async def main(project_id: int) -> None:
    async with session_scope() as master:
        project = await master.get(Project, project_id)
        if project is None:
            raise SystemExit(f"проекта #{project_id} нет")
        data_dir = project.data_dir
        meta = dict(project.meta or {})
        graph = dict(meta.get("canvas_graph") or {})
        nodes = list(graph.get("nodes") or [])
        if not any(
            str((n.get("data") or {}).get("groupId") or "").split("#", 1)[0]
            == "script_frames_qc"
            for n in nodes
            if isinstance(n, dict)
        ):
            nodes.append(
                {
                    "id": "n_dev_script_frames_qc",
                    "type": "pipeline",
                    "position": {"x": 0, "y": 0},
                    "data": {"nodeType": "excel_gpt", "groupId": "script_frames_qc"},
                }
            )
            graph["nodes"] = nodes
            graph.setdefault("edges", [])
            meta["canvas_graph"] = graph
            project.meta = meta
        await master.flush()

    async with session_scope() as master:
        project = await master.get(Project, project_id)
        await init_project_db(data_dir, project)
        await sync_project_row_to_project_db(project, data_dir)

    async with project_db_session_scope(data_dir) as session:
        await session.execute(delete(Frame).where(Frame.project_id == project_id))
        await session.flush()
        number = 0
        sort_key = 0.0
        for cell_i, cell in enumerate(CELLS, start=1):
            parts = _split_by_anchors(cell["vo"], cell["anchors"])
            shots = cell["shots"][: len(parts)] or cell["shots"][:1]
            ladder = []
            master_id = f"{cell_i}-S1-K1"
            for i, (plan, action) in enumerate(shots):
                ladder.append(
                    {
                        "id": f"{cell_i}-S1-K{i + 1}",
                        "порядок": i + 1,
                        "parent_id": None if i == 0 else master_id,
                        "сцена": 1,
                        "шаблон": cell["template"],
                        "план": plan,
                        "место": cell["place"],
                        "действие": action,
                        "закадр": parts[i] if i < len(parts) else "",
                    }
                )
            parent_uuid = f"{cell_i:02d}" * 16
            for i, (plan, action) in enumerate(shots):
                number += 1
                sort_key += 1.0
                is_parent = i == 0
                attrs = {
                    "shot01_action": action,
                    "крупность": plan,
                    "кадры": ladder if is_parent else [ladder[i]],
                    "camera_subdivide": {
                        "role": "vo_parent" if is_parent else "shot",
                        "parent_uuid": parent_uuid,
                        "coverage_parent_id": "" if is_parent else master_id,
                        "shot_index": i + 1,
                        "shots_in_beat": len(shots),
                        "scene_split": 1,
                        "шаблон": cell["template"],
                        "план": plan,
                        "место": cell["place"],
                        "shot_id": ladder[i]["id"],
                    },
                }
                if is_parent:
                    attrs["vo_cell_full"] = cell["vo"]
                    attrs["главное_действие"] = (
                        f"1. {cell['place']} — {cell['scene_action']}\n({cell['vo']})"
                    )
                    attrs["биты"] = [
                        {
                            "порядок": j + 1,
                            "якорь": anchor,
                            "изменение": "было → стало",
                            "главный": j == 0,
                        }
                        for j, anchor in enumerate(cell["anchors"])
                    ]
                session.add(
                    Frame(
                        project_id=project_id,
                        number=number,
                        uuid=parent_uuid if is_parent else f"{cell_i}{i}" * 16,
                        sort_key=sort_key,
                        voiceover_text=parts[i] if i < len(parts) else "",
                        duration_seconds=4.0,
                        status="planned",
                        attrs=attrs,
                    )
                )
        await session.flush()
        rows = list(
            (await session.execute(select(Frame).where(Frame.project_id == project_id)))
            .scalars()
            .all()
        )
        print(f"кадров создано: {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
