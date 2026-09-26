"""HTML-отчёт группы script_frames_qc: шаг действия = кадр. Без T0–T10."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from app.project_root import find_project_root
from app.services.shot_templates import parse_scene_chain, plain_scene_vo


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
        kadry = _kadry_list(fr)
        if len(kadry) > 1:
            sources = kadry
        elif kadry:
            sources = kadry
        else:
            sources = [_synth_shot(fr)]
        number = _int(_top(fr).get("number") or getattr(fr, "number", 0), 0)
        for shot in sources:
            item = dict(shot)
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


def _scene_meta(frames: list[Any]) -> dict[int, dict[str, Any]]:
    by_n: dict[int, dict[str, Any]] = {}
    for fr in frames:
        text = _get(_attrs(fr), "главное_действие", "main_action")
        if not text or "—" not in text and "→" not in text:
            continue
        for scene in parse_scene_chain(text):
            n = int(scene["n"])
            prev = by_n.get(n)
            if prev is None or len(scene.get("blob") or "") > len(prev.get("blob") or ""):
                by_n[n] = scene
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
    }


def _plans(frames: list[Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Площадки ячеек: схема сверху + зоны/проходы + что починил код."""
    from app.services.scene_plan import accusative, plan_svg

    out: list[dict[str, Any]] = []
    for fr in frames:
        plan = _attrs(fr).get("площадка")
        if not isinstance(plan, dict) or not plan.get("зоны"):
            continue
        number = _int(_top(fr).get("number") or getattr(fr, "number", 0), 0)
        shots = _kadry_list(fr)
        if len(shots) <= 1:
            shots = [
                {"зона": _get(_attrs(r.get("_frame")), "зона"), **r}
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


def build_shots_report_model(frames: list[Any]) -> dict[str, Any]:
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
        action = str(meta.get("action") or " → ".join(s["action"] for s in shots if s.get("action")))
        vo = plain_scene_vo(meta.get("vo") or "")
        if not vo:
            vo = plain_scene_vo(" ".join(s["vo"] for s in shots if s.get("vo")))
        scenes.append(
            {
                "id": str(n) if n else "—",
                "n": n,
                "place": place,
                "action": action,
                "vo": vo,
                "shots": shots,
            }
        )
    return {
        "scenes": scenes,
        "plans": _plans(frames, collected),
        "shots": built,
        "shot_count": len(built),
        "templates": [],
        "when_catalog": [],
    }


def render_shots_report_html(
    model: dict[str, Any],
    *,
    slug: str = "",
    project_id: int | None = None,
) -> str:
    scenes = list(model.get("scenes") or [])
    shots = list(model.get("shots") or [])
    if not shots:
        shots = [sh for sc in scenes for sh in (sc.get("shots") or [])]
    shot_n = int(model.get("shot_count") or len(shots))
    scene_n = len([s for s in scenes if s.get("n")])
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    pid = f"#{project_id} " if project_id else ""
    body_rows: list[str] = []
    for i, sh in enumerate(shots, start=1):
        prompts = sh.get("prompts") or {}
        qc = sh.get("qc") or {}
        img = _esc(prompts.get("картинка") or "—")
        vid = _esc(prompts.get("видео") or "—")
        qc_line = " · ".join(
            f"{lab} {_esc(qc.get(lab) or '—')}"
            for lab in ("крупность", "движение", "набор")
        )
        body_rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{_esc(sh.get('scene') or '—')}</td>"
            f"<td>{_esc(sh.get('place') or '—')}</td>"
            f"<td><b>{_esc(sh.get('id') or '—')}</b> — {_esc(sh.get('action') or '—')}"
            f"<div class=rel>{_esc(sh.get('rel'))}</div></td>"
            f"<td>{_esc(sh.get('object') or '—')}</td>"
            f"<td>{_esc(sh.get('plan') or '—')}</td>"
            f"<td>{_esc(sh.get('vo') or '—')}</td>"
            f"<td class=layout>{_esc(sh.get('layout') or '—')}</td>"
            f"<td><div class=vo-bit>картинка: {img}</div>"
            f"<div class=vo-bit>видео: {vid}</div>"
            f"<div class=vo-bit>QC: {qc_line}</div></td>"
            "</tr>"
        )
    scene_rows: list[str] = []
    for sc in scenes:
        if not sc.get("n"):
            continue
        steps = " → ".join(
            str(sh.get("action") or "").strip()
            for sh in (sc.get("shots") or [])
            if str(sh.get("action") or "").strip()
        )
        scene_rows.append(
            "<tr>"
            f"<td>{_esc(sc.get('n'))}</td>"
            f"<td>{_esc(sc.get('place') or '—')}</td>"
            f"<td>{_esc(steps or sc.get('action') or '—')}</td>"
            f"<td>{len(sc.get('shots') or [])}</td>"
            "</tr>"
        )
    plan_blocks: list[str] = []
    for pl in model.get("plans") or []:
        items = "".join(f"<li>{_esc(z)}</li>" for z in pl.get("zones") or [])
        items += "".join(f"<li>проход: {_esc(p)}</li>" for p in pl.get("passages") or [])
        extra = "".join(
            f"<li class=fix>код починил: {_esc(t)}</li>" for t in pl.get("fixed") or []
        )
        extra += "".join(
            f"<li class=der>код вывел: {_esc(t)}</li>" for t in pl.get("derived") or []
        )
        plan_blocks.append(
            f"<div class=plan><h3>Ячейка {_esc(pl.get('cell'))}</h3>"
            f"{pl.get('svg') or ''}<ul>{items}{extra}</ul></div>"
        )
    plans_html = "".join(plan_blocks) or "<p class=meta>площадка не записана</p>"
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<title>Отчёт кадров · {html.escape(slug or 'проект')}</title>
<style>
html,body{{background:#000;margin:0;color-scheme:light}}
body{{font:14px/1.4 system-ui,Segoe UI,sans-serif;padding:20px;color:#111}}
h1{{font-size:22px;margin:0 0 6px;color:#f2f2f2}}
h2{{font-size:16px;margin:24px 0 8px;color:#f2f2f2}}
.meta{{color:#9a9a9a;margin:0 0 16px}}
table{{border-collapse:collapse;width:100%;min-width:1200px}}
th,td{{border:1px solid #ddd;vertical-align:top;padding:10px 12px;background:#fff}}
th{{text-align:left;background:#f4f4f4;position:sticky;top:0}}
th:first-child,td:first-child{{width:56px;text-align:center;color:#666;font-weight:650}}
.vo-bit{{color:#666;margin-top:2px}}
.rel{{color:#1a4d8c;margin-top:2px;font-weight:650}}
.layout{{color:#333;font-size:13px;min-width:260px}}
.plan{{background:#fff;padding:10px 14px;margin:0 0 10px;border-radius:6px}}
.plan h3{{margin:0 0 6px;font-size:14px}}
.plan ul{{margin:6px 0 0;padding-left:18px}}
.fix{{color:#8a4b00}} .der{{color:#666}}
</style></head><body>
<h1>Сцены → кадры</h1>
<p class=meta>проект {html.escape(pid)}{html.escape(slug or '—')} · {html.escape(stamp)} · сцен {scene_n} · кадров {shot_n}. Один шаг действия = один кадр. Шаблонов T нет.</p>
<h2>Сцены</h2>
<table>
<thead><tr><th>№</th><th>Место</th><th>Шаги</th><th>Кадров</th></tr></thead>
<tbody>{''.join(scene_rows) or '<tr><td colspan=4>нет сцен с номером</td></tr>'}</tbody>
</table>
<h2>Площадка (вид сверху, К — камера кадра, стрелка — куда смотрит)</h2>
{plans_html}
<h2>Кадры</h2>
<table>
<thead><tr><th>№</th><th>Сцена</th><th>Место</th><th>Шаг</th><th>Объект</th><th>План</th><th>Закадр</th><th>Раскладка (старт кадра)</th><th>Промты / QC</th></tr></thead>
<tbody>{''.join(body_rows)}</tbody>
</table>
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


def write_shots_report(
    project: Any,
    frames: list[Any],
    *,
    node_key: str = "n_excel_gpt_fw_report",
) -> list[Path]:
    model = build_shots_report_model(frames)
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
