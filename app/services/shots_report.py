"""HTML-отчёт группы script_frames_qc: кадры-шаги, камера из таблицы, QC полей."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from app.project_root import find_project_root
from app.services.scene_shot_grammar import OBJECTS, SHOT_VO_IDEAL, SHOT_VO_MAX, SHOT_VO_MIN
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


def _when_by_template() -> dict[str, str]:
    return {}


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
    return by_scene


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
    mm = _get(src, "линза_мм")
    return {
        "объект": _get(src, "объект") or "",
        "план": _get(src, "план"),
        "линза_мм": mm,
        "ракурс": _get(src, "ракурс"),
        "движение": _get(src, "движение", "move"),
        "крупность": _get(src, "крупность", "size") or _get(src, "план"),
        "набор": _get(src, "набор", "set") or _get(src, "место", "place"),
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
    kadry = _collect_kadry(frames)
    index = _overlay_index(frames)
    scene_nums = sorted(set(chain_map) | set(kadry) | _cs_scene_nums(frames)) or [1]
    scenes: list[dict[str, Any]] = []
    for n in scene_nums:
        scene = chain_map.get(n) or {}
        packed = kadry.get(n) or {}
        shots = list(packed.values())
        if not shots:
            shots = _shots_from_frames(frames, n)
        else:
            shots.sort(key=lambda s: int(s.get("порядок") or 0))
        ids = {_shot_id(s) for s in shots if _shot_id(s)}
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
            qc = _camera_bits(overlay, shot)
            built.append(
                {
                    "id": sid,
                    "template": _get(shot, "шаблон", "template") or "",
                    "order": int(shot.get("порядок") or i),
                    "object": _get(shot, "объект") or qc.get("объект") or "",
                    "plan": _get(shot, "план") or qc.get("план") or "",
                    "lens": _get(shot, "линза_мм") or qc.get("линза_мм") or "",
                    "angle": _get(shot, "ракурс") or qc.get("ракурс") or "",
                    "move": _get(shot, "движение") or qc.get("движение") or "",
                    "place": _get(shot, "место", "place")
                    or str(scene.get("place") or ""),
                    "action": _get(shot, "действие", "action"),
                    "vo": plain_scene_vo(
                        shot.get("закадр") or shot.get("voiceover_text") or ""
                    ),
                    "scene": n,
                    "cell": cell,
                    "rel": _rel_label(shot, ids),
                    "prompts": _prompts(overlay),
                    "qc": qc,
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
                    or (built[0].get("place") if built else "")
                    or ""
                ),
                "action": str(
                    scene.get("action")
                    or (built[0].get("action") if built else "")
                    or ""
                ),
                "vo": vo,
                "same_place": False if n == min(scene_nums) else True,
                "template": "",
                "when": "",
                "when_key": "",
                "shots": built,
            }
        )
    prev_place = ""
    for scene in scenes:
        place = str(scene.get("place") or "").strip().casefold()
        scene["same_place"] = bool(place and place == prev_place)
        if place:
            prev_place = place
    return {
        "scenes": scenes,
        "shot_count": sum(len(s["shots"]) for s in scenes),
        "templates": [],
        "when_catalog": [],
        "objects": list(OBJECTS),
        "vo_window": f"{SHOT_VO_MIN}–{SHOT_VO_MAX} (цель ~{SHOT_VO_IDEAL})",
    }


def render_shots_report_html(
    model: dict[str, Any],
    *,
    slug: str = "",
    project_id: int | None = None,
) -> str:
    scenes = list(model.get("scenes") or [])
    shot_n = int(model.get("shot_count") or 0)
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    pid = f"#{project_id} " if project_id else ""
    vo_win = _esc(model.get("vo_window") or f"{SHOT_VO_MIN}–{SHOT_VO_MAX}")
    rows: list[str] = []
    for i, scene in enumerate(scenes, start=1):
        cards: list[str] = []
        for sh in scene.get("shots") or []:
            sid = _esc(sh.get("id"))
            vo = _esc(sh.get("vo"))
            prompts = sh.get("prompts") or {}
            qc = sh.get("qc") or {}
            img = _esc(prompts.get("картинка") or "—")
            vid = _esc(prompts.get("видео") or "—")
            obj = _esc(sh.get("object") or qc.get("объект") or "—")
            mm = _esc(sh.get("lens") or qc.get("линза_мм") or "—")
            qc_line = " · ".join(
                f"{lab} {_esc(qc.get(lab) or sh.get(key) or '—')}"
                for lab, key in (
                    ("объект", "object"),
                    ("план", "plan"),
                    ("линза_мм", "lens"),
                    ("ракурс", "angle"),
                    ("движение", "move"),
                )
            )
            vo_html = f"<div class=vo-bit>({vo})</div>" if vo else ""
            cards.append(
                f"<div class=shot><b>{sid}</b> · {obj} · "
                f"{_esc(sh.get('plan') or '—')} · {mm}mm · "
                f"{_esc(sh.get('angle') or '—')} "
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
        rows.append(
            "<tr>"
            f"<td>{_esc(scene.get('id') or scene.get('n') or i)}</td>"
            f"<td>{_esc(scene.get('place') or '—')}</td>"
            f"<td>{_esc(scene.get('action'))}{vo_bit}</td>"
            f"<td>{'да' if scene.get('same_place') else 'нет'}</td>"
            f"<td>{''.join(cards) or '—'}</td>"
            "</tr>"
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
<h1>Сцены → кадры-шаги и QC полей</h1>
<p class=meta>проект {html.escape(pid)}{html.escape(slug or '—')} · {html.escape(stamp)} · сцен {len(scenes)} · кадров {shot_n} · закадр кадра {vo_win}</p>
<h2>Грамматика группы (не T0–T10)</h2>
<table>
<thead><tr><th>id</th><th>Правило</th><th>Как используется</th></tr></thead>
<tbody>
<tr><td>1</td><td>VO-ячейка = сцена</td><td>биты якорями, код режет spans; закадр не пишет GPT</td></tr>
<tr><td>2</td><td>карточка сцены</td><td>N. место — шаг → шаг + (дословный кусок). Одно место не плодит новые N.</td></tr>
<tr><td>3</td><td>кадр = видимый шаг</td><td>объект: место|тело|двое|предмет|лицо|взгляд. Камеру дописывает код</td></tr>
<tr><td>4</td><td>закадр кадра</td><td>13–80, цель ~45. Склейка = весь voiceover_text</td></tr>
<tr><td>5</td><td>parent</td><td>новое место — null; то же место — id первого кадра локации (PNG)</td></tr>
<tr><td>6</td><td>промты картинок</td><td>не эта группа — шаг img_pr</td></tr>
</tbody>
</table>
<h2>Сцены: кадры схемы</h2>
<table>
<thead><tr><th>id</th><th>Место</th><th>Действие сцены + закадр</th><th>То же место</th><th>Кадры схемы</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<h2>Куда это вставлено в пайплайн</h2>
<table>
<thead><tr><th>id</th><th>Кусок</th><th>Как используется</th></tr></thead>
<tbody>
<tr><td>1</td><td>fw_script</td><td>биты: изменение + якорь. Код fill_bit_spans</td></tr>
<tr><td>2</td><td>fw_action</td><td>карточка сцены. Код merge_same_place_scenes</td></tr>
<tr><td>3</td><td>fw_shots + scene_shot_grammar</td><td>шаги → кадры, camera_pack по объекту, 13–80</td></tr>
<tr><td>4</td><td>fw_qc</td><td>shots_qc_ru: склейка, уникальность, enum, parent. Не промты</td></tr>
<tr><td>5</td><td>fw_report</td><td>этот HTML из БД: кадры[] + цепь. Картинки — если уже есть от img_pr</td></tr>
</tbody>
</table>
</body></html>
"""


def visible_len_safe(text: Any) -> int:
    from app.services.scene_shot_grammar import visible_len

    return visible_len(str(text or ""))


def render_group_run_html(
    run: dict[str, Any],
    *,
    slug: str = "",
    title: str = "Отчёт группы нод",
) -> str:
    """Полный прогон группы: биты → последовательность кадров → QC."""
    bits = list(run.get("bits") or [])
    shots = list(run.get("shots") or [])
    action = str(run.get("action") or "")
    qc = run.get("qc")
    vo = str(run.get("vo") or "")
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    chain = parse_scene_chain(action)
    bit_rows = "".join(
        "<tr>"
        f"<td>B{int(b.get('порядок') or i)}</td>"
        f"<td><b>{_esc(b.get('изменение'))}</b>"
        f"<div class=vo-bit>якорь: {_esc(b.get('якорь'))}</div>"
        f"<div class=vo-bit>({_esc(b.get('закадр'))})</div></td>"
        "</tr>"
        for i, b in enumerate(bits, start=1)
    )
    scene_rows = "".join(
        "<tr>"
        f"<td>S{int(sc.get('n') or i):02d}</td>"
        f"<td>{_esc(sc.get('place'))}</td>"
        f"<td>{_esc(sc.get('action'))}"
        f"<div class=vo-bit>({_esc(plain_scene_vo(sc.get('vo') or ''))})</div></td>"
        "</tr>"
        for i, sc in enumerate(chain, start=1)
    )
    shot_cards = "".join(
        "<tr>"
        f"<td>{_esc(s.get('id'))}</td>"
        f"<td>{_esc(s.get('место'))}</td>"
        f"<td>{_esc(s.get('действие'))}</td>"
        f"<td>{_esc(s.get('объект'))}</td>"
        f"<td>{_esc(s.get('план'))} · {_esc(s.get('линза_мм'))}mm"
        f"<div class=vo-bit>{_esc(s.get('ракурс'))} · {_esc(s.get('движение'))}</div></td>"
        f"<td>{'null' if s.get('parent_id') in (None, '', 'null') else _esc(s.get('parent_id'))}</td>"
        f"<td>{_esc(s.get('закадр'))}"
        f"<div class={'warn' if visible_len_safe(s.get('закадр')) > SHOT_VO_MAX or visible_len_safe(s.get('закадр')) < SHOT_VO_MIN else 'vo-bit'}>{visible_len_safe(s.get('закадр'))} симв. · цель ~{SHOT_VO_IDEAL}</div></td>"
        "</tr>"
        for s in shots
    )
    qc_cls = "ok" if not qc else "warn"
    qc_text = "пусто ops — поля чистые" if not qc else str(qc)
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<title>{html.escape(title)}</title>
<style>
body{{font:14px/1.4 system-ui,Segoe UI,sans-serif;margin:20px;color:#111;background:#fff}}
h1{{font-size:22px;margin:0 0 6px}}
h2{{font-size:16px;margin:28px 0 8px;padding-top:8px;border-top:1px solid #e5e5e5}}
.meta{{color:#555;margin:0 0 12px}}
.nav{{position:sticky;top:0;background:#fff;padding:10px 0 12px;border-bottom:1px solid #ddd;z-index:5;margin:0 0 16px}}
.nav a{{margin-right:12px;color:#1a4d8c;font-weight:650;text-decoration:none}}
table{{border-collapse:collapse;width:100%;min-width:1100px;margin:0 0 8px}}
th,td{{border:1px solid #ddd;vertical-align:top;padding:10px 12px}}
th{{text-align:left;background:#f4f4f4;position:sticky;top:48px}}
th:first-child,td:first-child{{width:72px;text-align:center;color:#666;font-weight:650}}
.vo-bit{{color:#666;margin-top:2px}}
.note{{background:#eef5ff;border:1px solid #c9dbf5;padding:10px 12px;margin:0 0 16px}}
.warn{{background:#fff6e8;border:1px solid #f0d2a0;padding:10px 12px;margin:0 0 16px}}
.ok{{background:#eef8ee;border:1px solid #b7ddb7;padding:10px 12px;margin:0 0 16px}}
.badge{{display:inline-block;background:#1a4d8c;color:#fff;border-radius:4px;padding:1px 7px;font-size:12px;font-weight:700;margin-right:6px}}
.badge.g{{background:#2a7}}
.badge.o{{background:#c80}}
.node{{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#444}}
pre.note{{white-space:pre-wrap}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class=meta>группа <b>script_frames_qc</b> · {html.escape(slug or '—')} · {html.escape(stamp)}
· битов {len(bits)} · сцен {len(chain)} · кадров {len(shots)} · закадр кадра {SHOT_VO_MIN}–{SHOT_VO_MAX}</p>
<p class=nav>
<a href="#gate">гейт</a>
<a href="#n0">4 ноды</a>
<a href="#n1">1 биты</a>
<a href="#n2">2 кадры</a>
<a href="#n3">3 QC</a>
<a href="#n4">4 отчёт</a>
</p>
<p class=note>Формат как у старого отчёта группы, но <b>без T0–T10</b>.
Кадр = видимый шаг действия. Камера из таблицы по полю <code>объект</code>.
Промты картинок — <code>img_pr</code>, не эта группа. Нода frames на новых канвасах не вставляется.</p>
<h2 id="gate">Гейт: только если группа на пайплайне</h2>
<p class=ok><span class=badge g>вкл</span> <code>canvas_has_script_frames_qc</code> —
<code>data.groupId=script_frames_qc</code>.</p>
<table>
<thead><tr><th>id</th><th>Механика</th><th>С группой</th><th>Без группы (main)</th></tr></thead>
<tbody>
<tr><td>1</td><td>Ячейка = сцена, дети = шаги действия</td><td>да, scene_shot_grammar</td><td>не эта грамматика; T/X остаётся монтажу</td></tr>
<tr><td>2</td><td>камера</td><td>таблица объект → план / мм / ракурс</td><td>меню съёмки / каталог T/X</td></tr>
<tr><td>3</td><td>parent PNG</td><td>первое новое место null, дальше id первого</td><td>как было</td></tr>
<tr><td>4</td><td>промты картинок</td><td>img_pr</td><td>fw_frames если нода осталась на старом канвасе</td></tr>
</tbody>
</table>
<h2 id="n0">Четыре ноды группы</h2>
<p class=meta>сценарист → последовательность кадров → QC полей → HTML-отчёт</p>
<table>
<thead><tr><th>id</th><th>Нода</th><th>canvas id</th><th>промт / режим</th><th>Что пишет</th></tr></thead>
<tbody>
<tr><td>1</td><td><b>сценарист</b></td><td class=node>n_excel_gpt_fw_script</td><td class=node>script_writer_ru</td><td>биты: изменение + якорь. Закадр не пишет.</td></tr>
<tr><td>2</td><td><b>кадры</b></td><td class=node>n_excel_gpt_fw_shots</td><td class=node>scenes_to_frames_ru</td><td>биты → последовательность кадров. Камеру дописывает код. Не T0–T10.</td></tr>
<tr><td>3</td><td><b>QC полей</b></td><td class=node>n_excel_gpt_fw_qc</td><td class=node>shots_qc_ru</td><td>склейка, 13–80, уникальность, enum. Не промты картинок.</td></tr>
<tr><td>4</td><td><b>отчёт</b></td><td class=node>n_excel_gpt_fw_report</td><td class=node>transport=code</td><td>HTML без GPT.</td></tr>
</tbody>
</table>
<h2 id="n1">1 · Сценарист · n_excel_gpt_fw_script</h2>
<p class=note>Промт <code>script_writer_ru.md</code>. Пишет только <code>биты</code>.
Код <code>fill_bit_spans</code> режет закадр по якорям.</p>
<table>
<thead><tr><th>id</th><th>Бит</th></tr></thead>
<tbody>{bit_rows}</tbody>
</table>
<h2 id="n2">2 · Кадры · n_excel_gpt_fw_shots</h2>
<p class=note>Промт <code>scenes_to_frames_ru.md</code> + <code>scene_shot_grammar</code>.
Последовательность кадров = сцена. Камера из таблицы, не каталог T/X.</p>
<pre class=note>{html.escape(action)}</pre>
<table>
<thead><tr><th>id</th><th>Место</th><th>Действие</th><th>объект</th><th>камера</th><th>parent</th><th>закадр</th></tr></thead>
<tbody>{shot_cards}</tbody>
</table>
<h2 id="n3">3 · QC · n_excel_gpt_fw_qc</h2>
<p class="{qc_cls}"><span class="badge {'g' if not qc else 'o'}">shots_grammar_reason</span>
{html.escape(qc_text)}</p>
<p class=note>Промт <code>shots_qc_ru.md</code>. Пустые ops = ок. Промты картинок не пишет.</p>
<h2 id="n4">4 · Отчёт · n_excel_gpt_fw_report</h2>
<p class=ok>Этот файл. Картинки/видео пустые — их пишет img_pr позже.</p>
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
