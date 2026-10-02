"""HTML-отчёт группы script_frames_qc: шаг действия = кадр. Без T0–T10."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.project_root import find_project_root
from app.services.node_rules import (
    FIELD_WRITERS,
    NODE_FLOW,
    RULES,
    node_label,
    node_local_key,
    rule_title,
)
from app.services.node_trace import latest_diary, list_runs
from app.services.shot_templates import parse_scene_chain, plain_scene_vo
from app.services.vo_shot_expand import is_shot_child


def _esc(text: Any) -> str:
    return html.escape(" ".join(str(text or "").split()))


def _plain(text: Any) -> str:
    return " ".join(str(text or "").split())


def _attrs(fr: Any) -> dict[str, Any]:
    if isinstance(fr, dict):
        raw = fr.get("attrs")
        return raw if isinstance(raw, dict) else {}
    raw = getattr(fr, "attrs", None)
    return raw if isinstance(raw, dict) else {}


def _top(fr: Any) -> dict[str, Any]:
    if isinstance(fr, dict):
        return fr
    return {
        "number": getattr(fr, "number", None),
        "uuid": getattr(fr, "uuid", None),
        "voiceover_text": getattr(fr, "voiceover_text", None),
        "image_prompt": getattr(fr, "image_prompt", None),
        "animation_prompt": getattr(fr, "animation_prompt", None),
        "camera_subdivide": None,
    }


def _cs(fr: Any) -> dict[str, Any]:
    attrs = _attrs(fr)
    top = _top(fr)
    cs = attrs.get("camera_subdivide")
    out = dict(cs) if isinstance(cs, dict) else {}
    extra = top.get("camera_subdivide")
    if isinstance(extra, dict):
        out.update(extra)
    return out


def _get(src: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = src.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _shot_id(shot: dict[str, Any]) -> str:
    return _get(shot, "id", "shot_id")


def _kadry_list(fr: Any) -> list[dict[str, Any]]:
    raw = _attrs(fr).get("кадры") or _top(fr).get("кадры")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _scene_n(shot: dict[str, Any], fr: Any) -> int:
    n = _int(shot.get("сцена"), 0)
    if n:
        return n
    cs = _cs(fr)
    attrs = _attrs(fr)
    return _int(cs.get("сцена") or attrs.get("сцена"), 0)


def _rel_label(shot: dict[str, Any], ids: set[str]) -> str:
    pid = shot.get("parent_id")
    if pid in (None, "", "null"):
        return "родительский"
    pid_s = str(pid)
    if pid_s in ids:
        return f"дочерний, родитель {pid_s}"
    return "дочерний"


def _camera_bits(fr: Any | None, shot: dict[str, Any]) -> dict[str, str]:
    attrs = _attrs(fr) if fr is not None else {}
    cs = _cs(fr) if fr is not None else {}
    top = _top(fr) if fr is not None else {}
    src = {**shot, **attrs, **cs, **top}
    return {
        "крупность": _get(src, "план", "крупность", "size"),
        "движение": _get(src, "движение", "move"),
        "набор": _get(src, "набор", "set"),
    }


def _prompts(fr: Any | None) -> dict[str, str]:
    if fr is None:
        return {"картинка": "", "видео": ""}
    attrs = _attrs(fr)
    top = _top(fr)
    return {
        "картинка": _get(top, "image_prompt")
        or _get(attrs, "промт_картинки", "image_prompt"),
        "видео": _get(top, "animation_prompt")
        or _get(attrs, "промт_видео", "animation_prompt"),
    }


def _overlay_index(frames: list[Any]) -> dict[str, Any]:
    by_shot: dict[str, Any] = {}
    by_number: dict[int, Any] = {}
    for fr in frames:
        cs = _cs(fr)
        sid = _get(cs, "shot_id") or _get(_attrs(fr), "shot_id")
        if sid:
            by_shot[sid] = fr
        num = _int(_top(fr).get("number") or getattr(fr, "number", 0), 0)
        if num:
            by_number[num] = fr
        for shot in _kadry_list(fr):
            kid = _shot_id(shot)
            if kid and kid not in by_shot:
                by_shot[kid] = fr
    return {"shot": by_shot, "number": by_number}


def _pick_overlay(shot: dict[str, Any], index: dict[str, Any], fallback: Any) -> Any:
    sid = _shot_id(shot)
    if sid and sid in index["shot"]:
        return index["shot"][sid]
    cell = _int(shot.get("ячейка"), 0)
    if cell and cell in index["number"]:
        return index["number"][cell]
    return fallback


def _synth_shot(fr: Any) -> dict[str, Any]:
    cs = _cs(fr)
    attrs = _attrs(fr)
    top = _top(fr)
    number = _int(top.get("number") or getattr(fr, "number", 0), 0)
    return {
        "id": _get(cs, "shot_id") or (str(number) if number else ""),
        "parent_id": cs.get("parent_shot_id") or cs.get("parent_id"),
        "порядок": cs.get("shot_index") or number,
        "сцена": _int(cs.get("сцена") or attrs.get("сцена"), 0) or None,
        "место": _get(cs, "место") or _get(attrs, "место", "place"),
        "действие": _get(attrs, "действие", "shot01_action"),
        "объект": _get(cs, "объект") or _get(attrs, "объект"),
        "план": _get(cs, "план") or _get(attrs, "план"),
        "закадр": _plain(top.get("voiceover_text") or cs.get("vo_shot") or ""),
        "ячейка": number,
        "_frame": fr,
    }


def _collect_shots(frames: list[Any]) -> list[dict[str, Any]]:
    """Один кадр БД = один шаг. Если в ячейке ещё лежит список кадры[] > 1 — все."""
    index = _overlay_index(frames)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fr in frames:
        if is_shot_child(fr):
            continue
        kadry = _kadry_list(fr)
        if len(kadry) > 1:
            sources = kadry
        elif kadry:
            sources = kadry
        else:
            sources = [_synth_shot(fr)]
        number = _int(_top(fr).get("number") or getattr(fr, "number", 0), 0)
        for pos, shot in enumerate(sources, start=1):
            item = dict(shot)
            item["_pos"] = pos
            item["_frame"] = _pick_overlay(item, index, fr)
            if not item.get("ячейка"):
                item["ячейка"] = number
            if not item.get("закадр"):
                item["закадр"] = _plain(
                    _top(item["_frame"]).get("voiceover_text")
                    or item.get("voiceover_text")
                    or ""
                )
            sid = _shot_id(item)
            key = sid or f"{number}:{item.get('действие')}:{item.get('закадр')}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    return rows


def _scene_chain_line(scene: dict[str, Any]) -> str:
    n = scene.get("n") or ""
    place = str(scene.get("place") or "").strip()
    action = str(scene.get("action") or "").strip()
    if place and action:
        return f"{n}. {place} — {action}"
    if action:
        return f"{n}. {action}" if n else action
    return place


def _scene_meta(frames: list[Any]) -> dict[int, dict[str, Any]]:
    """Цепь главное_действие с VO-родителя: N. место — шаг → шаг."""
    by_n: dict[int, dict[str, Any]] = {}
    for fr in frames:
        if is_shot_child(fr):
            continue
        attrs = _attrs(fr)
        text = _get(attrs, "главное_действие", "main_action")
        from app.services.scene_shot_grammar import scene_script_text

        parsed = parse_scene_chain(text)
        if parsed:
            for scene in parsed:
                n = int(scene["n"])
                scene["chain"] = _scene_chain_line(scene)
                scene["script"] = scene_script_text(
                    str(scene.get("place") or ""),
                    str(scene.get("action") or ""),
                )
                prev = by_n.get(n)
                if prev is None or len(scene.get("blob") or "") > len(prev.get("blob") or ""):
                    by_n[n] = scene
            continue
        n = _int(_cs(fr).get("сцена") or attrs.get("сцена"), 0)
        if not n or not text.strip():
            continue
        if "→" not in text and "—" not in text:
            continue
        prev = by_n.get(n)
        if prev is None or len(text) > len(prev.get("blob") or ""):
            place = _get(_cs(fr), "место") or _get(attrs, "место", "place")
            by_n[n] = {
                "n": n,
                "place": place,
                "action": text.strip(),
                "chain": text.strip(),
                "script": scene_script_text(place, text.strip()),
                "vo": "",
                "blob": text,
            }
    return by_n


def _build_row(
    shot: dict[str, Any],
    *,
    order: int,
    ids: set[str],
    scene_meta: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    overlay = shot.get("_frame")
    n = _scene_n(shot, overlay)
    meta = scene_meta.get(n) or {}
    cell = _int(
        shot.get("ячейка")
        or _top(overlay).get("number")
        or getattr(overlay, "number", 0),
        0,
    )
    sid = _shot_id(shot) or (f"S{n}-K{order}" if n else f"K{order}")
    place = (
        _get(shot, "место", "place")
        or str(meta.get("place") or "")
        or _get(_cs(overlay) if overlay is not None else {}, "место")
        or _get(_attrs(overlay) if overlay is not None else {}, "place", "место")
    )
    return {
        "id": sid,
        "order": _int(shot.get("порядок"), order) or order,
        "plan": _get(shot, "план")
        or _get(_cs(overlay) if overlay is not None else {}, "план"),
        "object": _get(shot, "объект"),
        "place": place,
        "action": _get(shot, "действие", "action", "shot01_action"),
        "vo": plain_scene_vo(shot.get("закадр") or shot.get("voiceover_text") or ""),
        "scene": n,
        "cell": cell,
        "rel": _rel_label(shot, ids),
        "prompts": _prompts(overlay),
        "qc": _camera_bits(overlay, shot),
        "layout": _get(shot, "раскладка")
        or _get(_attrs(overlay) if overlay is not None else {}, "раскладка"),
        "pos": _int(shot.get("_pos"), 0),
        "diary": [],
    }


def _plan_shot(row: dict[str, Any]) -> dict[str, Any]:
    """Кадр для схемы: зона/камера с карточки, если в шаге пусто."""
    overlay = row.get("_frame")
    attrs = _attrs(overlay) if overlay is not None else {}
    cs = _cs(overlay) if overlay is not None else {}
    shot = {k: v for k, v in row.items() if k != "_frame"}
    if not shot.get("зона"):
        shot["зона"] = _get(attrs, "зона") or _get(cs, "зона")
    if not isinstance(shot.get("камера"), dict):
        raw = attrs.get("камера") or cs.get("камера")
        if isinstance(raw, dict):
            shot["камера"] = raw
    if not _shot_id(shot):
        sid = _get(cs, "shot_id") or _get(attrs, "shot_id")
        if sid:
            shot["id"] = sid
    if not shot.get("порядок"):
        shot["порядок"] = cs.get("shot_index") or (
            _top(overlay).get("number") if overlay is not None else None
        )
    return shot


def _plans(frames: list[Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Площадки ячеек: схема сверху + зоны/проходы + что починил код."""
    from app.services.scene_plan import accusative, plan_svg

    out: list[dict[str, Any]] = []
    for fr in frames:
        if is_shot_child(fr):
            continue
        plan = _attrs(fr).get("площадка")
        if not isinstance(plan, dict) or not plan.get("зоны"):
            continue
        number = _int(_top(fr).get("number") or getattr(fr, "number", 0), 0)
        kadry = _kadry_list(fr)
        if len(kadry) > 1:
            shots = [_plan_shot(s) for s in kadry]
        else:
            shots = [
                _plan_shot(r)
                for r in rows
                if _int(r.get("ячейка"), 0) == number
            ]
        zones = []
        for z in plan.get("зоны") or []:
            if not isinstance(z, dict):
                continue
            props = ", ".join(
                f"{p.get('id')} ({p.get('где')}{', ' + p['состояние'] if p.get('состояние') else ''})"
                for p in z.get("предметы") or []
                if isinstance(p, dict)
            )
            zones.append(f"{z.get('id')}: {props or 'без предметов'}")
        passages = [
            f"{p.get('из')} → {p.get('в')} через {accusative(str(p.get('через') or ''))}"
            for p in plan.get("проходы") or []
            if isinstance(p, dict)
        ]
        out.append(
            {
                "cell": number,
                "svg": plan_svg(plan, shots),
                "zones": zones,
                "passages": passages,
                "derived": list(plan.get("выведено_кодом") or []),
                "fixed": list(plan.get("исправлено_кодом") or []),
            }
        )
    return out


def _frame_numbers_by_uuid(frames: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for fr in frames:
        top = _top(fr)
        uid = str(top.get("uuid") or getattr(fr, "uuid", "") or "").strip()
        n = _int(top.get("number") or getattr(fr, "number", 0), 0)
        if uid and n:
            out[uid] = n
    return out


def _attach_diary(
    frames: list[Any],
    rows: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    diary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Записи дневника → строка кадра / сцена. Возвращает записи без кадра."""
    number_of = _frame_numbers_by_uuid(frames)
    rows_by_cell: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_cell.setdefault(_int(row.get("cell"), 0), []).append(row)
    scene_of_cell: dict[int, dict[str, Any]] = {}
    for sc in scenes:
        sc.setdefault("diary", [])
        for sh in sc.get("shots") or []:
            scene_of_cell.setdefault(_int(sh.get("cell"), 0), sc)
    rows_by_id = {
        str(row.get("id") or ""): row
        for row in rows
        if str(row.get("id") or "")
    }
    orphans: list[dict[str, Any]] = []
    for item in diary:
        cell = number_of.get(str(item.get("frame_uuid") or ""), 0)
        cell_rows = rows_by_cell.get(cell) or []
        shot = item.get("shot")
        sid = str(item.get("shot_id") or "")
        target = rows_by_id.get(sid) if sid else None
        if target is None and shot:
            by_id = [r for r in cell_rows if sid and r.get("id") == sid]
            by_pos = [r for r in cell_rows if _int(r.get("pos"), 0) == _int(shot, 0)]
            hit = by_id or by_pos
            target = hit[0] if hit else None
        if target is not None:
            target["diary"].append(item)
        elif cell in scene_of_cell:
            scene_of_cell[cell]["diary"].append({**item, "cell": cell})
        else:
            orphans.append({**item, "cell": cell or None})
    return orphans


def build_shots_report_model(
    frames: list[Any],
    *,
    diary: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
    check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collected = _collect_shots(frames)
    ids = {_shot_id(s) for s in collected if _shot_id(s)}
    scene_meta = _scene_meta(frames)
    built = [
        _build_row(shot, order=i, ids=ids, scene_meta=scene_meta)
        for i, shot in enumerate(collected, start=1)
    ]
    by_scene: dict[int, list[dict[str, Any]]] = {}
    for row in built:
        by_scene.setdefault(int(row.get("scene") or 0), []).append(row)
    scenes: list[dict[str, Any]] = []
    for n in sorted(by_scene):
        shots = by_scene[n]
        meta = scene_meta.get(n) or {}
        place = str(meta.get("place") or shots[0].get("place") or "")
        action = str(meta.get("action") or "").strip()
        if not action:
            action = " → ".join(
                s["action"] for s in shots if s.get("action")
            )
        chain = str(meta.get("chain") or "").strip()
        if not chain:
            chain = _scene_chain_line({"n": n, "place": place, "action": action})
        vo = plain_scene_vo(meta.get("vo") or "")
        if not vo:
            vo = plain_scene_vo(" ".join(s["vo"] for s in shots if s.get("vo")))
        scenes.append(
            {
                "id": str(n) if n else "—",
                "n": n,
                "place": place,
                "action": action,
                "chain": chain,
                "vo": vo,
                "script": str(meta.get("script") or ""),
                "shots": shots,
            }
        )
    orphans = _attach_diary(frames, built, scenes, list(diary or []))
    return {
        "scenes": scenes,
        "plans": _plans(frames, collected),
        "shots": built,
        "shot_count": len(built),
        "templates": [],
        "when_catalog": [],
        "diary_orphans": orphans,
        "runs": list(runs or []),
        "check": dict(check or {}),
    }


def _plan_block(pl: dict[str, Any]) -> str:
    items = "".join(f"<li>{_esc(z)}</li>" for z in pl.get("zones") or [])
    items += "".join(f"<li>проход: {_esc(p)}</li>" for p in pl.get("passages") or [])
    extra = "".join(
        f"<li class=fix>код починил: {_esc(t)}</li>" for t in pl.get("fixed") or []
    )
    extra += "".join(
        f"<li class=der>код вывел: {_esc(t)}</li>" for t in pl.get("derived") or []
    )
    return (
        f"<details class=plan><summary>Площадка · Ячейка {_esc(pl.get('cell'))}</summary>"
        f"{pl.get('svg') or ''}<ul>{items}{extra}</ul></details>"
    )


_DIARY_KIND = {"fix": "починила", "warn": "заметила", "stop": "остановила", "info": "решила"}


def _diary_li(item: dict[str, Any], *, with_cell: bool = False) -> str:
    kind = str(item.get("kind") or "fix")
    who = node_label(str(item.get("node_key") or ""))
    where = []
    if with_cell and item.get("cell"):
        where.append(f"ячейка {item.get('cell')}")
    if item.get("shot"):
        where.append(f"кадр {item.get('shot')}")
    if item.get("field"):
        where.append(f"поле «{item.get('field')}»")
    head = f"{who}: программа {_DIARY_KIND.get(kind, kind)}"
    if where:
        head += " · " + ", ".join(where)
    change = ""
    if item.get("before") is not None or item.get("after") is not None:
        change = (
            f"<div class=chg><s>{_esc(item.get('before') or '—')}</s>"
            f" → <b>{_esc(item.get('after') or '—')}</b></div>"
        )
    note = f"<div class=note>{_esc(item.get('note'))}</div>" if item.get("note") else ""
    rule = str(item.get("rule") or "")
    return (
        f"<li class=d-{_esc(kind)}>{_esc(head)}{change}{note}"
        f"<div class=rule>{_esc(rule)} · {_esc(rule_title(rule))}</div></li>"
    )


def _diary_block(items: list[dict[str, Any]], *, title: str, with_cell: bool = False) -> str:
    if not items:
        return ""
    lis = "".join(_diary_li(it, with_cell=with_cell) for it in items)
    return (
        f"<details class=diary><summary>{_esc(title)} ({len(items)})</summary>"
        f"<ul>{lis}</ul></details>"
    )


def _counts_line(counts: dict[str, Any]) -> str:
    parts = [
        f"{_DIARY_KIND.get(k, k)} {int(v)}"
        for k, v in sorted((counts or {}).items())
        if int(v or 0)
    ]
    return ", ".join(parts) or "правок нет"


def _runs_block(runs: list[dict[str, Any]], check: dict[str, Any]) -> str:
    latest: dict[str, dict[str, Any]] = {}
    script_ok = 0
    script_fail = 0
    for run in runs:
        nk = str(run.get("node_key") or "")
        latest[nk] = run
        if node_local_key(nk) == "script":
            if str(run.get("status") or "") == "ok":
                script_ok += 1
            else:
                script_fail += 1
    order = {f.key: i for i, f in enumerate(NODE_FLOW)}
    rows = "".join(
        "<tr>"
        f"<td>{_esc(node_label(nk))}</td>"
        f"<td>{_esc(run.get('finished') or run.get('started') or '—')}</td>"
        f"<td class=st-{_esc(run.get('status') or '')}>{_esc(run.get('status') or '—')}</td>"
        f"<td>{_esc(run.get('calls') or 0)}</td>"
        f"<td>{_esc(_counts_line(run.get('counts') or {}))}</td>"
        f"<td class=path>{_esc(run.get('dir') or '')}</td>"
        "</tr>"
        for nk, run in sorted(latest.items(), key=lambda kv: order.get(node_local_key(kv[0]), 99))
    )
    table = (
        "<table class=runs><thead><tr><th>Нода</th><th>Когда</th><th>Итог</th>"
        "<th>Вызовов GPT</th><th>Что сделала программа</th><th>Папка прогона</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else "<p>Дневника ещё нет: он появится после следующего прогона нод.</p>"
    )
    check_html = ""
    if check:
        verdict = str(check.get("verdict") or "").lower()
        word = "Ок — пропустила дальше" if verdict == "pass" else "Не ок — вернула на сценарий"
        bad = "".join(
            f"<li>{_esc(c.get('id'))}: {_esc(c.get('note') or 'не прошло')}</li>"
            for c in check.get("checks") or []
            if isinstance(c, dict) and not c.get("ok", True)
        )
        check_html = (
            f"<p><b>Проверка сценария (последняя):</b> {_esc(word)}."
            f" {_esc(check.get('summary') or '')}</p>"
            + (f"<ul>{bad}</ul>" if bad else "")
        )
    if script_ok or script_fail:
        extra = f", сбоев до ответа GPT {script_fail}" if script_fail else ""
        check_html += (
            f"<p><b>Сценарий:</b> удачных прогонов {script_ok}{extra}.</p>"
        )
    return (
        "<section class=runs-box><h2>Дневник группы нод</h2>"
        f"{check_html}{table}</section>"
    )


def _rules_block() -> str:
    rules = "".join(
        f"<tr><td>{_esc(r.id)}</td><td>{_esc(r.title)}</td><td>{_esc(r.plain)}</td>"
        f"<td>{_esc(r.action)}</td></tr>"
        for r in RULES
    )
    flow = "".join(
        f"<tr><td>{_esc(f.label)}</td><td>{_esc(', '.join(f.reads) or '—')}</td>"
        f"<td>{_esc('; '.join(f.gpt_writes) or '—')}</td>"
        f"<td>{_esc(', '.join(f.code_rules) or '—')}</td>"
        f"<td>{_esc('; '.join(f.code_writes) or '—')}"
        + (f"<br>«Не ок» → {_esc(node_label(f.on_fail))}" if f.on_fail else "")
        + "</td></tr>"
        for f in NODE_FLOW
    )
    writers = "".join(
        f"<tr><td>{_esc(fld)}</td><td>{_esc(' → '.join(who))}</td></tr>"
        for fld, who in FIELD_WRITERS
    )
    return (
        "<section class=runs-box>"
        "<details><summary>Схема группы нод: кто что читает и пишет</summary>"
        "<table><thead><tr><th>Нода</th><th>Читает</th><th>Пишет GPT</th>"
        f"<th>Потом программа</th><th>Пишет программа</th></tr></thead><tbody>{flow}</tbody></table>"
        "<h3>Кто по очереди пишет одно поле (последний побеждает)</h3>"
        f"<table><tbody>{writers}</tbody></table></details>"
        "<details><summary>Все правила программы</summary>"
        "<table><thead><tr><th>ID</th><th>Правило</th><th>Простыми словами</th>"
        f"<th>Что делает</th></tr></thead><tbody>{rules}</tbody></table></details>"
        "</section>"
    )


def _shot_tr(i: int, sh: dict[str, Any]) -> str:
    prompts = sh.get("prompts") or {}
    qc = sh.get("qc") or {}
    img = _esc(prompts.get("картинка") or "—")
    vid = _esc(prompts.get("видео") or "—")
    qc_line = " · ".join(
        f"{lab} {_esc(qc.get(lab) or '—')}"
        for lab in ("крупность", "движение", "набор")
    )
    return (
        "<tr>"
        f"<td>{i}</td>"
        f"<td>{_esc(sh.get('plan') or '—')}</td>"
        f"<td><div class=rel>{_esc(sh.get('rel') or '—')}</div>"
        f"<b>{_esc(sh.get('id') or '—')}</b> — {_esc(sh.get('action') or '—')}</td>"
        f"<td>{_esc(sh.get('vo') or '—')}</td>"
        f"<td class=layout>{_esc(sh.get('layout') or '—')}</td>"
        f"<td><div class=vo-bit>картинка: {img}</div>"
        f"<div class=vo-bit>видео: {vid}</div>"
        f"<div class=vo-bit>QC: {qc_line}</div>"
        f"{_diary_block(sh.get('diary') or [], title='Дневник кадра')}</td>"
        "</tr>"
    )


def _take_plan(
    sc: dict[str, Any], plans_by_cell: dict[int, dict[str, Any]]
) -> dict[str, Any] | None:
    seen: list[int] = []
    for sh in sc.get("shots") or []:
        cell = _int(sh.get("cell"), 0)
        if cell and cell not in seen:
            seen.append(cell)
    for cell in seen:
        pl = plans_by_cell.pop(cell, None)
        if pl:
            return pl
    return None


def render_shots_report_html(
    model: dict[str, Any],
    *,
    slug: str = "",
    project_id: int | None = None,
) -> str:
    scenes = [sc for sc in (model.get("scenes") or []) if sc.get("n") or sc.get("shots")]
    shots = list(model.get("shots") or [])
    if not shots:
        shots = [sh for sc in scenes for sh in (sc.get("shots") or [])]
    shot_n = int(model.get("shot_count") or len(shots))
    scene_n = len([s for s in scenes if s.get("n")])
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    pid = f"#{project_id} " if project_id else ""
    plans_by_cell = {
        _int(pl.get("cell"), 0): pl
        for pl in (model.get("plans") or [])
        if _int(pl.get("cell"), 0)
    }
    toc = "".join(
        f'<a href="#scene-{_esc(sc.get("n") or "x")}">'
        f"{_esc(sc.get('n') or '—')}. {_esc(sc.get('place') or 'сцена')}</a>"
        for sc in scenes
    )
    articles: list[str] = []
    for sc in scenes:
        n = sc.get("n") or "x"
        place = sc.get("place") or "—"
        beats = [sh for sh in (sc.get("shots") or [])]
        action = str(sc.get("action") or "").strip()
        if not action:
            action = " → ".join(
                str(sh.get("action") or "").strip() for sh in beats if str(sh.get("action") or "").strip()
            )
        chain = str(sc.get("chain") or "").strip() or action
        script = str(sc.get("script") or "").strip()
        if not script:
            from app.services.scene_shot_grammar import scene_script_text
            script = scene_script_text(str(place or ""), action)
        vo = str(sc.get("vo") or "").strip()
        chain_note = ""
        if chain and chain != action and not chain.endswith(action):
            chain_note = f'<div class=scene-action-chain>{_esc(chain)}</div>'
        pl = _take_plan(sc, plans_by_cell)
        plan_html = _plan_block(pl) if pl else ""
        rows = "".join(_shot_tr(i, sh) for i, sh in enumerate(beats, start=1))
        title = f"Сцена {_esc(n)} · {_esc(place)}" if n != "x" else f"Без номера · {_esc(place)}"
        articles.append(
            f'<article class=scene id="scene-{_esc(n)}">'
            f"<h2>{title}</h2>"
            f'<div class=scene-action>'
            f'<div class=scene-action-label>Сценарий сцены</div>'
            f"<p>{_esc(script or '—')}</p>"
            f'<div class=scene-action-label>Шаги в кадрах</div>'
            f"<p>{_esc(action or chain or '—')}</p>"
            f"{chain_note}"
            f"</div>"
            f"<p class=vo-full><b>Закадр.</b> {_esc(vo or '—')}</p>"
            f"{plan_html}"
            f"{_diary_block(sc.get('diary') or [], title='Что поменяла программа в ячейке', with_cell=True)}"
            "<h3>Кадры</h3>"
            "<table><thead><tr>"
            "<th>№</th><th>План</th><th>Действие</th><th>Закадр</th>"
            "<th>Раскладка</th><th>Промты / QC</th>"
            f"</tr></thead><tbody>{rows or '<tr><td colspan=6>нет кадров</td></tr>'}</tbody></table>"
            "</article>"
        )
    leftover = "".join(_plan_block(pl) for pl in plans_by_cell.values())
    leftover_html = (
        f"<h2>Площадка без сцены</h2>{leftover}" if leftover else ""
    )
    body_scenes = "".join(articles) or "<p class=meta>сцен с номером нет</p>"
    orphans = _diary_block(
        list(model.get("diary_orphans") or []),
        title="Правки программы без кадра в отчёте",
        with_cell=True,
    )
    runs_html = _runs_block(list(model.get("runs") or []), dict(model.get("check") or {}))
    runs_html += (f"<section class=runs-box>{orphans}</section>" if orphans else "") + _rules_block()
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<title>Отчёт кадров · {html.escape(slug or 'проект')}</title>
<style>
html,body{{background:#000;margin:0;color-scheme:light}}
body{{font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:20px;color:#111}}
h1{{font-size:22px;margin:0 0 6px;color:#f2f2f2}}
h2{{font-size:18px;margin:0 0 8px;color:#111}}
h3{{font-size:13px;margin:14px 0 6px;color:#444}}
.meta{{color:#9a9a9a;margin:0 0 12px}}
.toc{{display:flex;flex-wrap:wrap;gap:8px 14px;margin:0 0 18px}}
.toc a{{color:#9ecbff;text-decoration:none;font-size:13px}}
.scene{{background:#fff;padding:16px 18px;margin:0 0 16px;border-radius:8px}}
.scene-action{{background:#111;color:#fff;padding:14px 16px;margin:0 0 12px;border-radius:8px;border-left:6px solid #f5c518}}
.scene-action-label{{font-size:13px;color:#f5c518;margin:0 0 8px;font-weight:700}}
.scene-action p{{font-size:20px;line-height:1.35;margin:0;font-weight:700}}
.scene-action-chain{{margin:8px 0 0;color:#cfcfcf;font-size:13px}}
.vo-full{{margin:0 0 10px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;vertical-align:top;padding:8px 10px;background:#fff}}
th{{text-align:left;background:#f4f4f4}}
th:first-child,td:first-child{{width:44px;text-align:center;color:#666;font-weight:650}}
.vo-bit{{color:#666;margin-top:2px}}
.rel{{color:#8a4b00;font-size:12px;font-weight:700;margin:0 0 4px}}
.layout{{color:#333;font-size:13px;min-width:200px}}
.plan{{background:#f7f7f5;padding:8px 12px;margin:8px 0 12px;border-radius:6px;overflow-x:auto}}
.plan ul{{margin:6px 0 0;padding-left:18px}}
.plan-svg{{display:block;max-width:100%;height:auto}}
.plan-legend{{color:#9a9a9a;margin:0 0 14px;max-width:72rem}}
.fix{{color:#8a4b00}} .der{{color:#666}}
.runs-box{{background:#fff;padding:12px 16px;margin:0 0 16px;border-radius:8px}}
.runs-box summary{{cursor:pointer;font-weight:650;margin:4px 0}}
.path{{font-size:11px;color:#666;word-break:break-all}}
.st-ok{{color:#11772b}} .st-failed{{color:#b00020}} .st-running{{color:#8a4b00}}
.diary{{margin-top:6px;font-size:12px}}
.diary summary{{cursor:pointer;color:#0b57d0}}
.diary ul{{margin:4px 0 0;padding-left:16px}}
.diary li{{margin:0 0 6px}}
.diary .chg s{{color:#999}} .diary .note{{color:#333}} .diary .rule{{color:#888;font-size:11px}}
.d-warn{{color:#8a4b00}} .d-stop{{color:#b00020}} .d-info{{color:#555}}
</style></head><body>
<h1>Сцены</h1>
<p class=meta>проект {html.escape(pid)}{html.escape(slug or '—')} · {html.escape(stamp)} · сцен {scene_n} · кадров {shot_n}. Жёлтый блок — Действие. Шаблонов T нет.</p>
<nav class=toc>{toc}</nav>
{runs_html}
<p class=plan-legend>Площадка внутри сцены (свёрнута). Север сверху. К — камера кадра, стрелка — куда смотрит объектив, не куда идёт герой.</p>
{body_scenes}
{leftover_html}
</body></html>
"""


def report_paths(project: Any, *, node_key: str = "n_excel_gpt_fw_report") -> list[Path]:
    slug = str(getattr(project, "slug", None) or "project")
    data_dir = Path(getattr(project, "data_dir"))
    exports = find_project_root() / "exports"
    return [
        data_dir / "reports" / "shots-report.html",
        data_dir / "excel_gpt_uploads" / node_key / "shots-report.html",
        exports / f"{slug}-shots-report.html",
    ]


def existing_shots_report_path(
    project: Any, *, node_key: str = "n_excel_gpt_fw_report"
) -> Path | None:
    """Файл, который уже записала нода fw_report. GET его не пересобирает."""
    return next((p for p in report_paths(project, node_key=node_key) if p.is_file()), None)


def _safe_load(fn: Any, default: Any) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        logger.debug("shots_report: diary load failed", exc_info=True)
        return default


def _latest_check(data_dir: Path) -> dict[str, Any]:
    """Последний вердикт ноды «Проверка сценария» (analysis.json)."""
    paths = sorted(
        (data_dir / "excel_gpt_uploads").glob("*_fw_check_script/analysis.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not paths:
        return {}
    raw = json.loads(paths[-1].read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) and raw.get("verdict") else {}


def write_shots_report(
    project: Any,
    frames: list[Any],
    *,
    node_key: str = "n_excel_gpt_fw_report",
) -> list[Path]:
    data_dir = Path(project.data_dir)
    group = {f.key for f in NODE_FLOW}

    def _mine(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [it for it in items if node_local_key(str(it.get("node_key") or "")) in group]

    model = build_shots_report_model(
        frames,
        diary=_safe_load(lambda: _mine(latest_diary(data_dir)), []),
        runs=_safe_load(lambda: _mine(list_runs(data_dir)), []),
        check=_safe_load(lambda: _latest_check(data_dir), {}),
    )
    html_text = render_shots_report_html(
        model,
        slug=str(getattr(project, "slug", None) or ""),
        project_id=getattr(project, "id", None),
    )
    written: list[Path] = []
    for path in report_paths(project, node_key=node_key):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_text, encoding="utf-8")
        written.append(path)
    return written
