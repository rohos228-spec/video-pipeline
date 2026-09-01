"""HTML-отчёт группы script_frames_qc: сцены → кадры, справа промты и QC."""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.project_root import find_project_root
from app.services.shot_templates import (
    load_shot_templates,
    parse_scene_chain,
    plain_scene_vo,
    plan_templates_for_action,
)

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


def _when_by_template() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in load_shot_templates().get("templates") or []:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("id") or "").strip()
        if tid:
            out[tid] = str(row.get("when") or "").strip()
    return out


def _frame_action(fr: Any) -> str:
    attrs = _attrs(fr)
    top = _top(fr)
    text = _get(
        {**attrs, **{k: top.get(k) for k in ("main_action", "главное_действие") if k in top}},
        "главное_действие",
        "main_action",
    )
    return text or _get(attrs, "главное_действие", "main_action")


def _scene_action_map(frames: list[Any]) -> dict[int, dict[str, Any]]:
    """Все сцены из всех ячеек, не только самая длинная цепь одного кадра."""
    by_n: dict[int, dict[str, Any]] = {}
    for fr in frames:
        for scene in parse_scene_chain(_frame_action(fr)):
            n = int(scene["n"])
            prev = by_n.get(n)
            if prev is None or len(scene.get("blob") or "") > len(prev.get("blob") or ""):
                by_n[n] = scene
    return by_n


def _merged_action(frames: list[Any]) -> str:
    parts: list[str] = []
    for n, scene in sorted(_scene_action_map(frames).items()):
        place = str(scene.get("place") or "").strip()
        act = str(scene.get("action") or "").strip()
        vo = str(scene.get("vo") or "").strip()
        head = f"{n}. {place} — {act}".strip(" —")
        parts.append(f"{head}\n({vo})" if vo else head)
    return "\n".join(parts)


def _shot_id(shot: dict[str, Any]) -> str:
    return _get(shot, "id", "shot_id")


_LADDER_ID = re.compile(r"^\d+-S\d+-K", re.I)


def _drop_stale_ladders(shots: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Если есть актуальная лестница 1-S3-K*, не тащить хвост старого expand (1-K7)."""
    if not shots:
        return shots
    current = {sid: shot for sid, shot in shots.items() if _LADDER_ID.match(sid)}
    return current or shots


def _collect_kadry(frames: list[Any]) -> dict[int, dict[str, dict[str, Any]]]:
    by_scene: dict[int, dict[str, dict[str, Any]]] = {}
    for fr in frames:
        raw = _attrs(fr).get("кадры") or _top(fr).get("кадры")
        if not isinstance(raw, list):
            continue
        for shot in raw:
            if not isinstance(shot, dict):
                continue
            try:
                n = int(shot.get("сцена"))
            except (TypeError, ValueError):
                continue
            sid = _shot_id(shot) or f"S{n}-{len(by_scene.get(n) or {}) + 1}"
            by_scene.setdefault(n, {}).setdefault(sid, dict(shot))
    return {n: _drop_stale_ladders(shots) for n, shots in by_scene.items()}


def _overlay_index(frames: list[Any]) -> dict[str, Any]:
    by_shot: dict[str, Any] = {}
    by_number: dict[int, Any] = {}
    for fr in frames:
        cs = _cs(fr)
        sid = _get(cs, "shot_id") or _get(_attrs(fr), "shot_id")
        if sid:
            by_shot[sid] = fr
        try:
            num = int(_top(fr).get("number") or getattr(fr, "number", 0) or 0)
        except (TypeError, ValueError):
            num = 0
        if num:
            by_number[num] = fr
    return {"shot": by_shot, "number": by_number}


def _pick_overlay(shot: dict[str, Any], index: dict[str, Any]) -> Any | None:
    sid = _shot_id(shot)
    if sid and sid in index["shot"]:
        return index["shot"][sid]
    try:
        cell = int(shot.get("ячейка") or 0)
    except (TypeError, ValueError):
        cell = 0
    if cell and cell in index["number"]:
        return index["number"][cell]
    return None


def _shots_from_frames(frames: list[Any], scene_n: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fr in frames:
        cs = _cs(fr)
        attrs = _attrs(fr)
        try:
            n = int(cs.get("сцена") or attrs.get("сцена") or 0)
        except (TypeError, ValueError):
            n = 0
        if n != scene_n:
            continue
        top = _top(fr)
        try:
            number = int(top.get("number") or getattr(fr, "number", 0) or 0)
        except (TypeError, ValueError):
            number = 0
        sid = _get(cs, "shot_id") or (f"{number}" if number else "")
        rows.append(
            {
                "id": sid,
                "parent_id": cs.get("parent_shot_id") or cs.get("parent_id"),
                "порядок": cs.get("shot_index") or number,
                "сцена": n,
                "шаблон": _get(cs, "шаблон", "template") or _get(attrs, "шаблон"),
                "план": _get(cs, "план") or _get(attrs, "план"),
                "ракурс": _get(cs, "ракурс") or _get(attrs, "ракурс"),
                "место": _get(cs, "место") or _get(attrs, "место", "place"),
                "действие": _get(attrs, "действие", "main_action", "главное_действие"),
                "закадр": _plain(top.get("voiceover_text") or cs.get("vo_shot") or ""),
                "ячейка": number,
                "_frame": fr,
            }
        )
    rows.sort(key=lambda s: (int(s.get("порядок") or 0), int(s.get("ячейка") or 0)))
    return rows


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
        "крупность": _get(src, "крупность", "size"),
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


def _cs_scene_nums(frames: list[Any]) -> set[int]:
    out: set[int] = set()
    for fr in frames:
        raw = _cs(fr).get("сцена") or _attrs(fr).get("сцена")
        try:
            n = int(raw or 0)
        except (TypeError, ValueError):
            n = 0
        if n:
            out.add(n)
    return out


def build_shots_report_model(frames: list[Any]) -> dict[str, Any]:
    action = _merged_action(frames)
    chain_map = _scene_action_map(frames)
    plan = {int(row["n"]): row for row in plan_templates_for_action(action)}
    when_map = _when_by_template()
    kadry = _collect_kadry(frames)
    index = _overlay_index(frames)
    scene_nums = sorted(set(chain_map) | set(kadry) | _cs_scene_nums(frames)) or [1]
    scenes: list[dict[str, Any]] = []
    for n in scene_nums:
        scene = chain_map.get(n) or {}
        prow = plan.get(n) or {}
        packed = kadry.get(n) or {}
        shots = list(packed.values())
        if not shots:
            shots = _shots_from_frames(frames, n)
        else:
            shots.sort(key=lambda s: int(s.get("порядок") or 0))
        ids = {_shot_id(s) for s in shots if _shot_id(s)}
        first_tid = _get(shots[0], "шаблон", "template") if shots else ""
        tid = first_tid or str(prow.get("template") or "")
        built: list[dict[str, Any]] = []
        for i, shot in enumerate(shots, start=1):
            overlay = shot.get("_frame") or _pick_overlay(shot, index)
            try:
                cell = int(
                    shot.get("ячейка")
                    or _top(overlay).get("number")
                    or getattr(overlay, "number", 0)
                    or 0
                )
            except (TypeError, ValueError):
                cell = 0
            sid = _shot_id(shot) or f"S{n}-K{i}"
            built.append(
                {
                    "id": sid,
                    "template": _get(shot, "шаблон", "template") or tid,
                    "order": int(shot.get("порядок") or i),
                    "plan": _get(shot, "план"),
                    "angle": _get(shot, "ракурс"),
                    "place": _get(shot, "место", "place")
                    or str(scene.get("place") or prow.get("place") or ""),
                    "action": _get(shot, "действие", "action"),
                    "vo": plain_scene_vo(
                        shot.get("закадр") or shot.get("voiceover_text") or ""
                    ),
                    "scene": n,
                    "cell": cell,
                    "rel": _rel_label(shot, ids),
                    "prompts": _prompts(overlay),
                    "qc": _camera_bits(overlay, shot),
                }
            )
        cell0 = next((s["cell"] for s in built if s.get("cell")), 0)
        scene_id = f"{cell0}-S{n}" if cell0 else f"S{n}"
        vo = plain_scene_vo(scene.get("vo") or "")
        if not vo:
            vo = plain_scene_vo(" ".join(s["vo"] for s in built if s.get("vo")))
        scenes.append(
            {
                "id": scene_id,
                "n": n,
                "place": str(
                    scene.get("place")
                    or prow.get("place")
                    or (built[0].get("place") if built else "")
                    or ""
                ),
                "action": str(
                    scene.get("action")
                    or prow.get("action")
                    or (built[0].get("action") if built else "")
                    or ""
                ),
                "vo": vo,
                "same_place": bool(prow.get("same_place")),
                "template": tid,
                "when": when_map.get(tid, ""),
                "when_key": str(prow.get("reason") or ""),
                "shots": built,
            }
        )
    tids = sorted({s["template"] for s in scenes if s.get("template")})
    select_if = {
        str(row.get("then_template") or ""): str(row.get("if") or "")
        for row in (load_shot_templates().get("select") or [])
        if isinstance(row, dict)
    }
    select_q = {
        str(row.get("then_template") or ""): str(row.get("question") or "")
        for row in (load_shot_templates().get("select") or [])
        if isinstance(row, dict)
    }
    return {
        "scenes": scenes,
        "shot_count": sum(len(s["shots"]) for s in scenes),
        "templates": tids,
        "when_catalog": [
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "when_key": select_if.get(str(row.get("id") or ""), ""),
                "when": str(row.get("when") or ""),
                "fits": select_q.get(str(row.get("id") or ""), ""),
                "ladder": str(row.get("shots_full") or ""),
            }
            for row in (load_shot_templates().get("templates") or [])
            if isinstance(row, dict) and row.get("id")
        ],
    }


def render_shots_report_html(
    model: dict[str, Any],
    *,
    slug: str = "",
    project_id: int | None = None,
) -> str:
    scenes = list(model.get("scenes") or [])
    shot_n = int(model.get("shot_count") or 0)
    tids = ", ".join(model.get("templates") or []) or "—"
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    pid = f"#{project_id} " if project_id else ""
    rows: list[str] = []
    for i, scene in enumerate(scenes, start=1):
        cards: list[str] = []
        for sh in scene.get("shots") or []:
            tid = _esc(sh.get("template") or "T")
            sid = _esc(sh.get("id"))
            vo = _esc(sh.get("vo"))
            prompts = sh.get("prompts") or {}
            qc = sh.get("qc") or {}
            img = _esc(prompts.get("картинка") or "—")
            vid = _esc(prompts.get("видео") or "—")
            qc_line = " · ".join(
                f"{lab} {_esc(qc.get(lab) or '—')}"
                for lab in ("крупность", "движение", "набор")
            )
            vo_html = f"<div class=vo-bit>({vo})</div>" if vo else ""
            cards.append(
                f"<div class=shot><b>{sid}</b> · {tid} · "
                f"{_esc(sh.get('plan') or '—')} · {_esc(sh.get('angle') or '—')} "
                f"— {_esc(sh.get('action'))}{vo_html}"
                f"<div class=rel>{_esc(sh.get('rel'))}</div>"
                f"<div class=vo-bit>картинка: {img}</div>"
                f"<div class=vo-bit>видео: {vid}</div>"
                f"<div class=vo-bit>QC: {qc_line}</div></div>"
            )
        vo_bit = (
            f"<div class=vo-bit>({_esc(scene.get('vo'))})</div>"
            if scene.get("vo")
            else ""
        )
        when_key = _esc(scene.get("when_key") or "")
        rows.append(
            "<tr>"
            f"<td>{_esc(scene.get('id') or scene.get('n') or i)}</td>"
            f"<td>{_esc(scene.get('place') or '—')}</td>"
            f"<td>{_esc(scene.get('action'))}{vo_bit}</td>"
            f"<td>{'да' if scene.get('same_place') else 'нет'}</td>"
            f"<td><div class=bit><b>{_esc(scene.get('template') or '—')}</b>"
            f"{('<div class=vo-bit>when: ' + when_key + '</div>') if when_key else ''}"
            f"<div class=vo-bit>{_esc(scene.get('when'))}</div></div></td>"
            f"<td>{''.join(cards) or '—'}</td>"
            "</tr>"
        )
    when_rows = "".join(
        "<tr>"
        f"<td>{_esc(row.get('id'))}</td>"
        f"<td>{_esc(row.get('name'))}</td>"
        f"<td>{_esc(row.get('when_key') or row.get('when'))}</td>"
        f"<td>{_esc(row.get('fits') or row.get('when'))}</td>"
        f"<td>{_esc(row.get('ladder'))}</td>"
        "</tr>"
        for row in (model.get("when_catalog") or [])
    )
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<title>Отчёт кадров · {html.escape(slug or 'проект')}</title>
<style>
html,body{{background:#000;margin:0;color-scheme:light}}
body{{font:14px/1.4 system-ui,Segoe UI,sans-serif;padding:20px;color:#111}}
h1{{font-size:22px;margin:0 0 6px;color:#f2f2f2}}
h2{{font-size:16px;margin:24px 0 8px;color:#f2f2f2}}
.meta{{color:#9a9a9a;margin:0 0 16px}}
table{{border-collapse:collapse;width:100%;min-width:1500px}}
th,td{{border:1px solid #ddd;vertical-align:top;padding:10px 12px;background:#fff}}
th{{text-align:left;background:#f4f4f4;position:sticky;top:0}}
th:first-child,td:first-child{{width:56px;text-align:center;color:#666;font-weight:650}}
.bit,.scene,.shot{{margin:0 0 12px}}
.vo-bit{{color:#666;margin-top:2px}}
.rel{{color:#1a4d8c;margin-top:2px;font-weight:650}}
.note{{background:#eef5ff;border:1px solid #c9dbf5;padding:10px 12px;margin:0 0 16px}}
</style></head><body>
<h1>Сцены → кадры, промты и QC</h1>
<p class=meta>проект {html.escape(pid)}{html.escape(slug or '—')} · {html.escape(stamp)} · сцен {len(scenes)} · кадров {shot_n} · шаблоны {html.escape(tids)}</p>
<h2>Листы xlsx → куда легло в код</h2>
<table>
<thead><tr><th>id</th><th>Лист таблицы</th><th>Как использовал</th></tr></thead>
<tbody>
<tr><td>1</td><td>Как читать</td><td>слоты {{место}} {{кто}} {{предмет}}; короткий текст — резать с конца лестницы; один жест = один кадр</td></tr>
<tr><td>2</td><td>Каталог + столбец Когда / when</td><td>templates[].when — условие, когда брать T0–T10 и X1/X2</td></tr>
<tr><td>3</td><td>Кадры</td><td>shots[]: план, ракурс, parent, роль каждого кадра схемы</td></tr>
<tr><td>4</td><td>Сжатие</td><td>drop_order, если кусок закадра сцены короткий</td></tr>
<tr><td>5</td><td>Выбор</td><td>select[]: первое «да» сверху вниз. То же место → X1, если нет более точного when</td></tr>
<tr><td>6</td><td>Цепи</td><td>жил→служил→работал = T8+T8+T8; две сцены одно место = T* → X1</td></tr>
<tr><td>7</td><td>Parent</td><td>новое место — parent нет; то же место — parent = первый кадр локации</td></tr>
</tbody>
</table>
<h2>Столбец when</h2>
<table><thead><tr><th>id</th><th>Шаблон</th><th>when из таблицы</th><th>Когда подходит</th><th>Лестница кадров</th></tr></thead>
<tbody>{when_rows}</tbody></table>
<h2>Сцены: какой when сработал</h2>
<table>
<thead><tr><th>id</th><th>Место</th><th>Действие сцены + закадр</th><th>То же место</th><th>when → шаблон</th><th>Кадры схемы</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<h2>Куда это вставлено в пайплайн</h2>
<table>
<thead><tr><th>id</th><th>Кусок</th><th>Как используется</th></tr></thead>
<tbody>
<tr><td>1</td><td>fw_frames, проход сцены→кадры</td><td>в accompanying уходит каталог + готовый SELECT ПО when. GPT не выбирает один T* на ячейку — ставит id из этой колонки</td></tr>
<tr><td>2</td><td>scenes_to_frames_ru</td><td>алгоритм: разбор главное_действие → select на каждую сцену → shots из таблицы</td></tr>
<tr><td>3</td><td>валидатор apply-ops</td><td>разные шаблоны в ячейке можно. Нельзя все ОБЩИЙ на одно место и два кадра подряд с одним планом</td></tr>
<tr><td>4</td><td>fw_report</td><td>собирает HTML из текущей БД: все сцены из кадры[] + цепь, справа у кадра промты и QC</td></tr>
</tbody>
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
