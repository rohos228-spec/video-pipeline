"""Separate, versioned empty-set references for the opt-in cinema group.

The scene key is the VO parent's UUID, never Scene.id or the mutable frame number.
Backgrounds are not scene_image artifacts and never become montage frames.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.models import Frame

BACKGROUND_ATTR = "scene_background_ref"
USED_HASH_ATTR = "scene_background_used_hash"


def _attrs(frame: Any) -> dict:
    raw = getattr(frame, "attrs", None)
    return raw if isinstance(raw, dict) else {}


def scene_parent(frames: list[Any], frame: Any) -> Any:
    """Follow stable VO membership before optional shot-to-shot camera links."""
    by_uuid = {str(f.uuid): f for f in frames}
    current = frame
    seen: set[str] = set()
    while True:
        uid = str(getattr(current, "uuid", ""))
        if not uid or uid in seen:
            raise RuntimeError("Фон сцены: повреждена или зациклена связь VO-родителя")
        seen.add(uid)
        cs = _attrs(current).get("camera_subdivide") or {}
        parent_uid = str(cs.get("parent_uuid") or "")
        if cs.get("role") != "shot":
            return current
        if not parent_uid or parent_uid == uid or parent_uid not in by_uuid:
            raise RuntimeError(f"Фон сцены: не найден VO-родитель кадра {current.number}")
        current = by_uuid[parent_uid]


def scene_members(frames: list[Any]) -> dict[str, tuple[Any, list[Any]]]:
    groups: dict[str, tuple[Any, list[Any]]] = {}
    for frame in frames:
        cs = _attrs(frame).get("camera_subdivide") or {}
        if cs.get("leftover"):
            continue
        parent = scene_parent(frames, frame)
        uid = str(parent.uuid)
        if uid not in groups:
            groups[uid] = (parent, [])
        groups[uid][1].append(frame)
    return groups


def background_spec(parent: Any) -> tuple[str, str]:
    """Build an empty-set prompt from the approved place inventory, not VO."""
    from app.services import frame_style

    attrs = _attrs(parent)
    plan = attrs.get("площадка")
    if not isinstance(plan, dict) or not plan.get("зоны"):
        raise RuntimeError(
            f"Фон сцены #{parent.number}: нет площадки с зонами. Сначала запустите «Площадка»."
        )
    # People and dynamic deltas are deliberately excluded from the empty set.
    static_plan = {key: plan[key] for key in ("зоны", "проходы") if key in plan}
    style_name = ""
    for shot in attrs.get("кадры") or []:
        if isinstance(shot, dict) and shot.get(frame_style.STYLE_NAME_KEY):
            style_name = str(shot[frame_style.STYLE_NAME_KEY])
            break
    if not style_name:
        raise RuntimeError(
            f"Фон сцены #{parent.number}: не задан стиль. Сначала запустите «Стиль»."
        )
    style = frame_style.load_style_text(style_name)
    spec = {
        "version": 1,
        "place": attrs.get("place") or attrs.get("место") or "",
        "plan": static_plan,
        "style": style,
    }
    source = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    body = (
        "Create an EMPTY FILM SET / LOCATION reference, not a storyboard or a floor plan. "
        "One coherent wide establishing view at eye level, physically plausible perspective. "
        "NO people, faces, bodies, silhouettes, action, captions, lettering or collage. "
        "Show the architecture, fixed props, materials, doors, windows and light sources "
        "in the starting state specified below. Preserve the spatial relationships. "
        "Compass directions describe the set, not labels to draw. Do not invent an action.\n\n"
        + json.dumps({"место": spec["place"], "площадка": static_plan}, ensure_ascii=False)
    )
    prompt, _scene, _trimmed = frame_style.compose_styled_prompt(body, style)
    return prompt, digest


def background_path(data_dir: Path, parent: Any, *, require_current: bool = True) -> Path:
    from app.services.scan_frames import is_valid_scene_image

    record = _attrs(parent).get(BACKGROUND_ATTR) or {}
    if not isinstance(record, dict) or record.get("status") != "ready":
        raise RuntimeError(f"Фон сцены #{parent.number} не готов. Запустите ноду «Фон сцены».")
    if require_current:
        _prompt, digest = background_spec(parent)
        if record.get("hash") != digest:
            raise RuntimeError(f"Фон сцены #{parent.number} устарел. Запустите ноду «Фон сцены».")
    root = (Path(data_dir) / "backgrounds").resolve()
    path = (Path(data_dir) / str(record.get("path") or "")).resolve()
    if path.parent != root or not is_valid_scene_image(path):
        raise RuntimeError(f"Фон сцены #{parent.number}: файл отсутствует или повреждён.")
    return path


def merge_background_refs(background: Path, refs: list[Path], *, max_refs: int = 2) -> list[Path]:
    """Mandatory background first; never silently drop an identity or a still."""
    out: list[Path] = []
    for path in [background, *refs]:
        candidate = Path(path)
        if candidate not in out:
            out.append(candidate)
    if len(out) > max_refs:
        raise RuntimeError(
            f"Кино с фоном: требуется {len(out)} референса, доступно {max_refs}. "
            "Фон, персонажи и родительский кадр не будут отброшены. "
            "Уменьшите число дополнительных референсов или используйте отдельный кадр/референс состава."
        )
    return out


async def _frames(session: Any, project: Any) -> list[Any]:
    return list((await session.execute(
        select(Frame).where(Frame.project_id == project.id).order_by(Frame.sort_key, Frame.number)
    )).scalars().all())


async def background_ref_for_frame(session: Any, project: Any, frame: Any) -> Path:
    return background_path(project.data_dir, scene_parent(await _frames(session, project), frame))


async def mark_background_used(session: Any, project: Any, frame: Any) -> None:
    parent = scene_parent(await _frames(session, project), frame)
    background_path(project.data_dir, parent)
    attrs = dict(_attrs(frame))
    attrs[USED_HASH_ATTR] = _attrs(parent)[BACKGROUND_ATTR]["hash"]
    attrs.pop("scene_background_stale", None)
    frame.attrs = attrs
    await session.flush()


async def ensure_backgrounds_ready(session: Any, project: Any, frames: list[Any]) -> None:
    from app.services.scan_frames import disk_has_valid_frame_image

    for parent, members in scene_members(frames).values():
        background_path(project.data_dir, parent)
        digest = _attrs(parent)[BACKGROUND_ATTR]["hash"]
        for member in members:
            if disk_has_valid_frame_image(project.data_dir / "scenes", member.number):
                if _attrs(member).get(USED_HASH_ATTR) != digest:
                    raise RuntimeError(
                        f"Кадр #{member.number} создан без текущего фона сцены. "
                        "Перегенерируйте его с монтажной доски; одобренные картинки не заменяются автоматически."
                    )


async def generate_scene_backgrounds(session: Any, project: Any) -> dict:
    """Idempotent code-node. Commit checkpoints before paid external calls."""
    from app.generation_options import IMAGE_GENERATORS_BY_ID
    from app.orchestrator.steps.generate_images import _img_http_primary, _optional_browser_session
    from app.bots.outsee import OutseeBot
    from app.services.outsee_retry import generate_image_with_retries
    from app.services.scan_frames import is_valid_scene_image
    from app.services.step_cancel import raise_if_cancelled
    from app.services.vibecode_catalog import resolve_node_media_settings

    frames = await _frames(session, project)
    groups = scene_members(frames)
    if not groups:
        raise RuntimeError("Фон сцены: в проекте нет сцен. Сначала выполните Каркас и Шоты.")
    # Validate all scenes before spending anything.
    specs = {uid: background_spec(parent) for uid, (parent, _members) in groups.items()}
    media = resolve_node_media_settings(project, node_type="images")
    generator = IMAGE_GENERATORS_BY_ID.get(media["image_generator_id"])
    model = generator.outsee_slug if generator else None
    from app.services.media_route import image_provider_for

    backend = image_provider_for(model)
    need_cdp = backend == "outsee" and not _img_http_primary()
    report: dict = {"generated": [], "reused": [], "scenes": []}
    async with _optional_browser_session(need_cdp) as browser:
        outsee = OutseeBot(browser) if need_cdp else None
        for uid, (parent, members) in groups.items():
            raise_if_cancelled(project.id)
            prompt, digest = specs[uid]
            try:
                path = background_path(project.data_dir, parent)
            except RuntimeError:
                path = None
            if path is not None:
                report["reused"].append(uid)
            else:
                safe_uid = re.sub(r"[^a-zA-Z0-9_-]", "_", uid)
                filename = f"scene_{safe_uid}_{digest[:12]}_{uuid.uuid4().hex[:8]}.png"
                path = project.data_dir / "backgrounds" / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                record = {"status": "generating", "hash": digest, "path": str(Path("backgrounds") / filename), "prompt": prompt}
                parent.attrs = {**_attrs(parent), BACKGROUND_ATTR: record}
                await session.commit()
                try:
                    await generate_image_with_retries(
                        outsee, None, prompt=prompt, out_path=path,
                        max_attempts_per_prompt=3, gpt_rewrite=False,
                        aspect_ratio=media["aspect_slug"] or "9:16",
                        model_slug=model, resolution=media["resolution_slug"],
                        quality=media["quality_slug"], relax=bool(project.image_relax),
                        project_id=project.id, gen_id=uuid.uuid4().hex,
                        prompt_id_prefix=f"P{project.id}-BG-{safe_uid[:12]}-{digest[:8]}",
                    )
                    if not is_valid_scene_image(path):
                        raise RuntimeError(f"Генератор вернул невалидный фон сцены #{parent.number}")
                except Exception as exc:
                    parent.attrs = {**_attrs(parent), BACKGROUND_ATTR: {**record, "status": "failed", "error": str(exc)[:1000]}}
                    await session.commit()
                    raise
                parent.attrs = {**_attrs(parent), BACKGROUND_ATTR: {**record, "status": "ready"}}
                for member in members:
                    if _attrs(member).get(USED_HASH_ATTR) != digest:
                        member.attrs = {**_attrs(member), "scene_background_stale": True}
                await session.commit()
                report["generated"].append(uid)
            report["scenes"].append({"scene_uuid": uid, "number": parent.number, "path": str(path), "hash": digest})
    return report
