"""Собрать отчёт сцена × нода из scene_design/*.json проекта #60."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data" / "videos" / "spesivcevy-chrono-dyn" / "scene_design"
OUT = ROOT / "data" / "videos" / "spesivcevy-chrono-dyn" / "ops" / "chrono_dyn_agent_scene_report.json"


def first_str(obj: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        if k not in obj or obj[k] is None:
            continue
        v = obj[k]
        if isinstance(v, (str, int, float)):
            return str(v)
        if isinstance(v, list):
            parts: list[str] = []
            for x in v[:8]:
                if isinstance(x, dict):
                    a = x.get("action") or x.get("действие") or x.get("text") or ""
                    pi = x.get("phase_index", x.get("фаза"))
                    sub = x.get("subject") or x.get("субъект") or ""
                    parts.append(f"[{pi}] {sub}: {a}".strip(": "))
                else:
                    parts.append(str(x))
            return " | ".join(parts)
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)[:300]
    return default


def clip(s: object, n: int = 220) -> str:
    t = " ".join(str(s or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def main() -> None:
    action = json.loads((D / "action.json").read_text(encoding="utf-8"))
    camera = json.loads((D / "camera.json").read_text(encoding="utf-8"))
    chars = json.loads((D / "characters.json").read_text(encoding="utf-8"))
    world = json.loads((D / "world.json").read_text(encoding="utf-8"))
    style = json.loads((D / "style.json").read_text(encoding="utf-8"))

    shots_by: dict[str, list] = defaultdict(list)
    for sh in camera.get("shot_plan") or []:
        shots_by[str(sh.get("id_scene") or "")].append(sh)

    style_by: dict[str, dict] = {}
    for st in style.get("style_arc") or []:
        hint = str(
            st.get("scene_hint") or st.get("id_scene") or st.get("сцены") or ""
        )
        style_by[hint] = st

    rows = []
    for sc in action.get("scenes") or []:
        sid = str(sc.get("id_scene") or "")
        phases = sc.get("цепь_действия") or sc.get("chain") or []
        n_phases = len(phases) if isinstance(phases, list) else 0
        shots = shots_by.get(sid, [])
        st = style_by.get(sid)
        if st is None:
            for k, v in style_by.items():
                if sid and sid in k:
                    st = v
                    break

        phase_summaries = []
        if isinstance(phases, list):
            for i, ph in enumerate(phases, 1):
                if isinstance(ph, dict):
                    phase_summaries.append(
                        {
                            "phase": ph.get("phase_index", i),
                            "beat": clip(ph.get("beat") or ph.get("бит") or "", 40),
                            "subject": clip(
                                ph.get("subject") or ph.get("субъект") or "", 80
                            ),
                            "action": clip(
                                ph.get("action")
                                or ph.get("действие")
                                or ph.get("text")
                                or json.dumps(ph, ensure_ascii=False),
                                220,
                            ),
                            "orientation": clip(
                                ph.get("orientation") or ph.get("ориентация") or "",
                                60,
                            ),
                            "transition": clip(
                                ph.get("переход_к_следующей")
                                or ph.get("transition")
                                or "",
                                60,
                            ),
                            "info_change": clip(
                                ph.get("info_change") or ph.get("изменение") or "",
                                140,
                            ),
                        }
                    )
                else:
                    phase_summaries.append(
                        {
                            "phase": i,
                            "subject": "",
                            "action": clip(ph, 220),
                            "orientation": "",
                        }
                    )

        shot_summaries = []
        for sh in shots:
            shot_summaries.append(
                {
                    "phase": sh.get("phase_index"),
                    "size": first_str(sh, "крупность", "size", "framing"),
                    "move": first_str(sh, "движение", "camera_move", "move"),
                    "angle": first_str(sh, "угол", "angle"),
                    "comp": clip(first_str(sh, "композиция", "composition"), 140),
                    "who": clip(first_str(sh, "кто_в_кадре", "who", "subject"), 120),
                    "quote": clip(first_str(sh, "цитата", "quote", "vo"), 120),
                    "set": first_str(sh, "набор", "set"),
                }
            )

        rows.append(
            {
                "id_scene": sid,
                "vo": clip(
                    first_str(sc, "цитата_закадра", "vo", "voiceover", "текст"),
                    180,
                ),
                "start_words": sc.get("start_words"),
                "end_words": sc.get("end_words"),
                "location": clip(
                    first_str(sc, "локация_в_кадре", "location", "локация"),
                    120,
                ),
                "who_scene": clip(
                    first_str(sc, "кто_в_кадре", "characters_in_frame"), 140
                ),
                "link_prev": clip(first_str(sc, "связь_с_прошлой", "link_prev"), 180),
                "hook_next": clip(
                    first_str(sc, "крючок_в_следующую", "hook_next"), 180
                ),
                "structure": clip(
                    first_str(sc, "структура_сцены", "structure"), 40
                ),
                "scene_transition": clip(
                    first_str(sc, "переход_в_сцену", "scene_transition"), 40
                ),
                "scene_meaning": clip(first_str(sc, "смысл_сцены", "мотив"), 160),
                "n_phases": n_phases,
                "n_shots": len(shots),
                "action_phases": phase_summaries,
                "camera_shots": shot_summaries,
                "style": (
                    {
                        "mood": clip(
                            first_str(st, "настроение", "mood", "атмосфера"), 80
                        ),
                        "hint": clip(first_str(st, "scene_hint"), 80),
                    }
                    if st
                    else None
                ),
            }
        )

    chars_sum = [
        {
            "id": c.get("id"),
            "name": first_str(c, "имя", "name"),
            "look": clip(first_str(c, "внешность", "look", "описание"), 160),
        }
        for c in chars.get("characters") or []
    ]
    locs_sum = [
        {
            "id": loc.get("id"),
            "name": first_str(loc, "name", "имя", "название"),
            "zones": clip(first_str(loc, "зоны", "zones"), 140),
        }
        for loc in world.get("locations") or []
    ]

    report = {
        "project": "#60 spesivcevy-chrono-dyn",
        "counts": {
            "scenes_action": len(rows),
            "shots_camera": len(camera.get("shot_plan") or []),
            "characters": len(chars_sum),
            "locations": len(locs_sum),
            "style_beats": len(style.get("style_arc") or []),
        },
        "characters": chars_sum,
        "locations": locs_sum,
        "scenes": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    table_rows = []
    for sc in rows:
        if sc["action_phases"]:
            for ph in sc["action_phases"]:
                cam = next(
                    (
                        x
                        for x in sc["camera_shots"]
                        if x.get("phase") == ph.get("phase")
                    ),
                    None,
                )
                if cam is None and sc["camera_shots"]:
                    idx = int(ph.get("phase") or 1) - 1
                    if 0 <= idx < len(sc["camera_shots"]):
                        cam = sc["camera_shots"][idx]
                table_rows.append(
                    {
                        "scene": sc["id_scene"],
                        "vo": sc["vo"],
                        "phase": ph.get("phase"),
                        "beat": ph.get("beat") or "",
                        "transition": ph.get("transition") or "",
                        "link_prev": sc.get("link_prev") or "",
                        "hook_next": sc.get("hook_next") or "",
                        "structure": sc.get("structure") or "",
                        "scene_transition": sc.get("scene_transition") or "",
                        "action_subject": ph.get("subject"),
                        "action_text": ph.get("action"),
                        "info_change": ph.get("info_change") or "",
                        "camera_size": (cam or {}).get("size", ""),
                        "camera_move": (cam or {}).get("move", ""),
                        "camera_who": (cam or {}).get("who", ""),
                        "camera_comp": (cam or {}).get("comp", ""),
                        "location": sc["location"],
                    }
                )
        else:
            table_rows.append(
                {
                    "scene": sc["id_scene"],
                    "vo": sc["vo"],
                    "phase": "",
                    "beat": "",
                    "transition": "",
                    "link_prev": sc.get("link_prev") or "",
                    "hook_next": sc.get("hook_next") or "",
                    "structure": sc.get("structure") or "",
                    "scene_transition": sc.get("scene_transition") or "",
                    "action_subject": "",
                    "action_text": "",
                    "info_change": "",
                    "camera_size": "",
                    "camera_move": "",
                    "camera_who": "",
                    "camera_comp": "",
                    "location": sc["location"],
                }
            )

    compact = OUT.with_name("chrono_dyn_agent_scene_table.json")
    compact.write_text(
        json.dumps(
            {
                "counts": report["counts"],
                "characters": chars_sum,
                "locations": locs_sum,
                "rows": table_rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(OUT)
    print(compact)
    print(json.dumps(report["counts"], ensure_ascii=False))
    print("table_rows", len(table_rows))


if __name__ == "__main__":
    main()
