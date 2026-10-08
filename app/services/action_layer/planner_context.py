"""Контекст проекта для планировщика: факты из БД и файлов, значения по умолчанию, сжатие для промпта.

Факты (``build_facts``) целиком входят в хэш версии контекста (устаревший план нельзя
подтверждать). В промпт модели идёт только нужная часть (``prompt_context``): кадры, очередь,
предложения изменений и т.п. показываются, когда в каталоге есть действия, которым они нужны.
Текстов кадров, промтов и темы проекта здесь нет.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select

MAX_FRAMES = 80            # строк кадров в контексте (больше: окно вокруг выбранного кадра)
MAX_QUEUE_OPS = 40
MAX_PROPOSALS = 6
MAX_VERSIONS = 6
MAX_PROMPT_NAMES = 12

_IMG_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_FRAME_FILE = re.compile(r"^frame_(\d+)_", re.IGNORECASE)
_CLIP_FILE = re.compile(r"^clip_(\d+)_", re.IGNORECASE)
_SAFE_NODE = re.compile(r"[^\w.\-: ]", re.UNICODE)

# Текущий шаг проекта по статусу (best effort): значение по умолчанию для step_code.
STATUS_STEP = {
    "planning": "plan", "plan_ready": "plan",
    "scripting": "script", "script_ready": "script",
    "splitting": "split", "frames_ready": "split",
    "scene_designing": "scene_d", "scene_agents_ready": "scene_d",
    "scene_assembling": "scene_asm", "scene_design_ready": "scene_asm",
    "generating_hero": "hero", "hero_ready": "hero",
    "generating_items": "items", "items_ready": "items",
    "generating_image_prompts": "img_pr", "image_prompts_ready": "img_pr",
    "generating_animation_prompts": "anim_pr", "animation_prompts_ready": "anim_pr",
}

# Свойства схем действий, по которым видно, что действию нужна карта кадров.
FRAME_PROPS = ("frame_id", "frame_number", "frame_numbers", "a_frame", "b_frame", "from_frame",
               "to_frame", "left_frame_id", "right_frame_id", "after_frame_id", "frame_ids")
QUEUE_ACTIONS = frozenset({"montage.cancel_queue_ops"})  # очередь для apply/queue сервер читает сам
GEN_ACTIONS = frozenset({"montage.apply_queue", "montage.generate_scene_images",
                         "montage.improve_scene"})
UUID_ACTIONS = frozenset({"shot.edit_cell_voiceover", "shot.edit_field"})


# --- файлы проекта ---------------------------------------------------------------------

def media_presence(data_dir: Path) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """({кадр: {шоты с картинкой}}, {кадр: {шоты с видео}}) по именам файлов проекта."""
    imgs: dict[int, set[int]] = {}
    vids: dict[int, set[int]] = {}
    scenes = Path(data_dir) / "scenes"
    if scenes.is_dir():
        for p in scenes.iterdir():
            m = _FRAME_FILE.match(p.name)
            if not m or p.suffix.lower() not in _IMG_EXT or "_parent" in p.name.lower():
                continue
            imgs.setdefault(int(m.group(1)), set()).add(2 if "_s2_" in p.name else 1)
    videos = Path(data_dir) / "videos"
    if videos.is_dir():
        for p in videos.iterdir():
            m = _CLIP_FILE.match(p.name)
            if not m or p.suffix.lower() != ".mp4":
                continue
            vids.setdefault(int(m.group(1)), set()).add(2 if "_s2_" in p.name else 1)
    return imgs, vids


def _mask(shots: set[int] | None) -> str:
    return "".join(str(n) for n in sorted(shots or ())) or "-"


# --- кадры ------------------------------------------------------------------------------

def _role(attrs: Any) -> str:
    cs = attrs.get("camera_subdivide") if isinstance(attrs, dict) else None
    return "c" if isinstance(cs, dict) and str(cs.get("role") or "") == "shot" else "p"


def _has_shot2_attrs(attrs: Any) -> bool:
    if not isinstance(attrs, dict):
        return False
    return bool(str(attrs.get("image_prompt_shot2") or "").strip()
                or str(attrs.get("animation_prompt_shot2") or "").strip())


def frame_rows(frames: list[tuple[int, int, Any, str | None]],
               imgs: dict[int, set[int]], vids: dict[int, set[int]],
               selected: int | None = None) -> tuple[list[list], str]:
    """Строки [number, frame_id, role, shots, img, vid] по порядку доски и пометка об усечении.

    ``frames``: (number, id, attrs, uuid) уже в порядке доски. role: p - сцена (ячейка закадра),
    c - шот сцены. shots: 1 или 2. img/vid: шоты, у которых файл есть ("-" нет)."""
    rows: list[list] = []
    for number, fid, attrs, _uuid in frames:
        two = (2 in imgs.get(number, ())) or (2 in vids.get(number, ())) or _has_shot2_attrs(attrs)
        rows.append([number, fid, _role(attrs), 2 if two else 1,
                     _mask(imgs.get(number)), _mask(vids.get(number))])
    note = ""
    if len(rows) > MAX_FRAMES:
        center = next((i for i, r in enumerate(rows) if r[0] == selected), 0)
        lo = max(0, min(center - MAX_FRAMES // 2, len(rows) - MAX_FRAMES))
        note = f"показаны кадры {rows[lo][0]}..{rows[lo + MAX_FRAMES - 1][0]} из {len(rows)}"
        rows = rows[lo:lo + MAX_FRAMES]
    return rows, note


def frames_text(rows: list[list]) -> str:
    """Компактная запись строк кадров для промпта: «number,frame_id,role,shots,img,vid;...»."""
    return ";".join(",".join(str(x) for x in r) for r in rows)


def scene_of(rows: list[list], number: int | None) -> list[list]:
    """Строки сцены кадра: голова (role p) и её шоты (role c) до следующей головы."""
    if number is None:
        return []
    start = None
    for i, r in enumerate(rows):
        if r[2] == "p":
            start = i
        if r[0] == number:
            break
    else:
        return []
    if start is None:
        return []
    out = [rows[start]]
    for r in rows[start + 1:]:
        if r[2] == "p":
            break
        out.append(r)
    return out


def resolve_selected(rows: list[list], selected: int | None) -> dict:
    """Выбранный в интерфейсе кадр: по frame_id доски, иначе по номеру. {number, frame_id, scene_id}."""
    if selected is None:
        return {}
    hit = next((r for r in rows if r[1] == selected), None) or next(
        (r for r in rows if r[0] == selected), None)
    if hit is None:
        return {}
    scene = scene_of(rows, hit[0])
    return {"number": hit[0], "frame_id": hit[1], "scene_head_id": scene[0][1] if scene else hit[1]}


# --- очередь ----------------------------------------------------------------------------

def queue_facts(project_meta: Any) -> dict:
    board = project_meta.get("montage_board") if isinstance(project_meta, dict) else None
    ops = [o for o in ((board or {}).get("pending_ops") or []) if isinstance(o, dict)]
    img = sum(1 for o in ops if str(o.get("type") or "").startswith("image_"))
    vid = sum(1 for o in ops if str(o.get("type") or "").startswith("video_"))
    rows = []
    for o in ops[:MAX_QUEUE_OPS]:
        row: list = [o.get("frame_number"), o.get("type"), 2 if o.get("shot") == 2 else 1]
        if o.get("slot"):
            row.append(o["slot"])
        rows.append(row)
    out: dict = {"image": img, "video": vid, "ops": rows}
    if len(ops) > MAX_QUEUE_OPS:
        out["more"] = len(ops) - MAX_QUEUE_OPS
    return out


# --- настройки по умолчанию -------------------------------------------------------------

def effective_media(project: Any) -> dict[str, str]:
    """Генератор и разрешение, как их берёт регенерация панели (настройки проекта или умолчания)."""
    try:
        from app.services.action_layer.builtin_montage_more import _effective_settings
        return dict(_effective_settings({
            "meta": project.meta or {}, "image_generator": project.image_generator,
            "image_resolution": project.image_resolution, "image_quality": project.image_quality,
            "aspect_ratio": project.aspect_ratio, "video_generator": project.video_generator,
            "video_resolution": project.video_resolution}))
    except Exception:  # noqa: BLE001 - настройки не должны ронять планировщик
        return {}


def build_defaults(project: Any, status: str) -> dict:
    out: dict = dict(effective_media(project))
    step = STATUS_STEP.get(status)
    if step:
        out["step_code"] = step
    from app.services.prompt_library import DEFAULT_NAME
    out["prompt_variant"] = DEFAULT_NAME
    out["slot_id"] = "main"
    return out


# --- ноды -------------------------------------------------------------------------------

def node_facts(nodes_snapshot: Any) -> list[dict]:
    from app.services.action_layer import node_run_cost as nrc
    out: list[dict] = []
    for node in list(nodes_snapshot or [])[:60]:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        item = {"id": _SAFE_NODE.sub("", str(node.get("id", "")))[:60],
                "type": _SAFE_NODE.sub("", str(node.get("type") or data.get("type") or ""))[:40]}
        kind = nrc.node_kind(item["id"])
        if kind:
            item["kind"] = kind.removeprefix("_fw_")
            item["prompt"] = nrc.expected_prompt(item["id"])
        elif nrc.is_report_node(item["id"]):
            item["kind"] = "report"
        out.append(item)
    out.sort(key=lambda n: (n["id"], n["type"]))
    return out


# --- контур изменений -------------------------------------------------------------------

async def change_facts(project_id: int) -> dict:
    """Последние предложения проекта (n - порядковый номер от старых к новым) и версии целей."""
    try:
        from app.services.action_layer import store
        from app.services.change_control import store as cs
        async with store.sessions()() as s:
            rows = (await s.execute(
                select(cs.ChangeProposal.id, cs.ChangeProposal.status, cs.ChangeProposal.tier,
                       cs.ChangeProposal.kind, cs.ChangeProposal.target, cs.ChangeProposal.digest,
                       cs.ChangeProposal.node_key)
                .where(or_(cs.ChangeProposal.project_id == project_id,
                           cs.ChangeProposal.project_id.is_(None)))
                .order_by(cs.ChangeProposal.created_at.asc(), cs.ChangeProposal.id.asc())
                .limit(500))).all()
            proposals = [{"n": i + 1, "id": r.id, "status": r.status, "tier": r.tier,
                          "kind": r.kind, "node": r.node_key, "target": r.target,
                          "digest": r.digest} for i, r in enumerate(rows)][-MAX_PROPOSALS:]
            ptrs = (await s.execute(
                select(cs.ConfigPointer.target, cs.ConfigPointer.current_version)
                .where(cs.ConfigPointer.target.like(f"project:{project_id}:%"))
                .order_by(cs.ConfigPointer.updated_at.desc(), cs.ConfigPointer.target)
                .limit(MAX_VERSIONS))).all()
        return {"proposals": proposals,
                "versions": [{"target": t, "v": int(v)} for t, v in ptrs]}
    except Exception:  # noqa: BLE001 - таблиц контура может не быть (старая БД)
        return {"proposals": [], "versions": []}


def publish_facts(project: Any) -> dict:
    """Подпись и площадки публикации, известные только серверу (в промпт НЕ идут: тема проекта это
    недоверенный текст). Их подставляет ``planner_rules.fill_defaults`` в шаг ``publish.send``."""
    try:
        from app.services.action_layer import publish_action as pa
        meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
        done = meta.get("published_platforms") if isinstance(meta.get("published_platforms"), dict) else {}
        platforms = [p for p in pa.registry_platforms() if p not in done]
        caption = pa.caption_of({"topic": getattr(project, "topic", None),
                                 "slug": getattr(project, "slug", None)})
    except Exception:  # noqa: BLE001 - публикаторов нет в окружении: шаг получит отказ в проверке
        return {"platforms": [], "caption": ""}
    return {"platforms": platforms, "caption": caption}


# --- сборка фактов ----------------------------------------------------------------------

async def build_facts(s, project: Any, project_id: int, selected_frame: int | None) -> dict:
    """Факты проекта (без выбранных сцены/кадра и project_id: их ставит вызывающий)."""
    from app.models import Frame, WorkflowRun
    status = str(getattr(project.status, "value", project.status))
    count = await s.scalar(select(func.count(Frame.id)).where(Frame.project_id == project_id))
    max_no = await s.scalar(select(func.max(Frame.number)).where(Frame.project_id == project_id))
    run = await s.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id))
    facts: dict = {"status": status, "frame_count": int(count or 0),
                   "max_frame_number": int(max_no or 0),
                   "nodes": node_facts(getattr(run, "nodes_snapshot", None))}
    frames = (await s.execute(
        select(Frame.number, Frame.id, Frame.attrs, Frame.uuid, Frame.sort_key)
        .where(Frame.project_id == project_id)
        .order_by(Frame.sort_key.asc(), Frame.number.asc()))).all()
    try:
        imgs, vids = await asyncio.to_thread(media_presence, project.data_dir)
    except OSError:  # папка проекта недоступна: карта кадров без пометок о файлах
        imgs, vids = {}, {}
    rows, note = frame_rows([(r.number, r.id, r.attrs, r.uuid) for r in frames], imgs, vids,
                            selected_frame)
    facts["frames"] = rows
    if note:
        facts["frames_note"] = note
    picked = resolve_selected(rows, selected_frame)
    if picked:
        facts["selected"] = picked
    facts["frame_uuids"] = {str(r.number): r.uuid for r in frames if r.uuid}
    facts["queue"] = queue_facts(project.meta)
    facts["defaults"] = build_defaults(project, status)
    overrides = project.prompt_overrides if isinstance(project.prompt_overrides, dict) else {}
    facts["prompt_overrides"] = {str(k)[:40]: str(v)[:120] for k, v in list(overrides.items())[:20]
                                 if isinstance(v, str) and v}
    facts["publish_server"] = publish_facts(project)
    facts.update(await change_facts(project_id))
    return facts


# --- мастер-промты ----------------------------------------------------------------------

def master_prompts(text: str, facts: dict) -> dict:
    """{step_code: {"sel": выбранный в проекте или None, "names": [...]}} для шагов запроса.

    В список попадают шаги, чей код или имя варианта названы в запросе, и шаги с выбранным
    в проекте вариантом. Файлы промтов общие для всех проектов (prompt_library)."""
    try:
        from app.services import prompt_library as pl
    except Exception:  # noqa: BLE001
        return {}
    words = set(re.findall(r"[\w\-]+", (text or "").lower().replace("ё", "е")))
    overrides = facts.get("prompt_overrides") or {}
    out: dict = {}
    for step in pl.STEP_FOLDERS:
        if step in pl.ENRICH_STEP_CODES and step != pl.EXCEL_GPT_UNIFIED_STEP and step not in overrides:
            continue  # слоты enrich_N показывают тот же список, что excel_gpt
        try:
            names = pl.list_prompts(step)
        except Exception:  # noqa: BLE001
            continue
        named = step.lower() in words or any(n.lower() in words for n in names)
        if not (named or step in overrides):
            continue
        shown = names[:MAX_PROMPT_NAMES]
        out[step] = {"sel": overrides.get(step), "names": [n[:40] for n in shown]}
        if len(names) > len(shown):
            out[step]["more"] = len(names) - len(shown)
    return out


# --- сжатие для промпта -----------------------------------------------------------------

def _schema_dump(registry: Any, aid: str) -> str:
    spec = registry.find(aid)
    return json.dumps(spec.params_schema, ensure_ascii=False) if spec else ""


def _mentioned_numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", text or "") if len(n) <= 6}


def prompt_context(facts: dict, offered: list[str] | frozenset[str], registry: Any,
                   text: str = "") -> dict:
    """Часть фактов, нужная модели для показанных ей действий (остальное в промпт не идёт).

    Все строки очищаются (``planner_guard.sanitize_tree``): имена, подписи и тексты из проекта
    - недоверенные данные, а не часть инструкции."""
    from app.services.action_layer import planner_guard
    return planner_guard.sanitize_tree(_prompt_context(facts, offered, registry, text))


def _prompt_context(facts: dict, offered: list[str] | frozenset[str], registry: Any,
                    text: str = "") -> dict:
    ids = set(offered)
    out: dict = {k: facts[k] for k in ("project_id", "selected_scene", "selected_frame", "status",
                                       "frame_count") if k in facts}
    if facts.get("project_id") is None:
        return out
    needs_frames = any(any(f'"{p}"' in _schema_dump(registry, a) for p in FRAME_PROPS) for a in ids)
    if needs_frames:
        out["frames"] = frames_text(facts.get("frames", []))
        if facts.get("selected"):
            out["selected"] = facts["selected"]
        if facts.get("frames_note"):
            out["frames_note"] = facts["frames_note"]
    if ids & QUEUE_ACTIONS:
        out["queue"] = facts.get("queue", {})
    defaults = facts.get("defaults") or {}
    pick: dict = {}  # генератор/разрешение, лимиты, шаг и вариант промта сервер подставляет сам
    if "node.reset_from_step" in ids and defaults.get("step_code"):
        pick["step_code"] = defaults["step_code"]
    if "node.set_prompt_variant" in ids:
        pick["slot_id"] = defaults.get("slot_id", "main")
    if pick:
        out["defaults"] = pick
    if any(a.startswith("node.") or a == "change.propose" for a in ids):
        out["nodes"] = [{k: v for k, v in n.items() if k != "type"} for n in facts.get("nodes", [])]
    if ids & {"change.apply", "change.get"}:
        out["proposals"] = facts.get("proposals", [])
    if ids & {"change.rollback", "change.get"}:
        out["versions"] = facts.get("versions", [])
    if any(a.startswith("prompt.") for a in ids) or "node.set_prompt_variant" in ids:
        prompts = master_prompts(text, facts)
        if prompts:
            out["prompts"] = prompts
    if ids & UUID_ACTIONS:
        uuids = facts.get("frame_uuids") or {}
        wanted = {str(n) for n in _mentioned_numbers(text)}
        if facts.get("selected_frame") is not None:
            wanted.add(str(facts["selected_frame"]))
        shown = {k: v for k, v in uuids.items() if k in wanted}
        if shown:
            out["frame_uuids"] = shown
    return out
