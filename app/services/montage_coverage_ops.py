"""Правки покрытия (Кадр / План / Действие) с панели монтажа."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    Frame,
    FrameEdge,
    FrameStatus,
    FrameText,
    Project,
    PromptVersion,
)
from app.services.montage_board_meta import slot_key_from_op
from app.services.vo_shot_expand import (
    _cs,
    _flag_attrs,
    _set_cs,
    coverage_shot_id,
    find_coverage_parent_frame,
    is_shot_child,
    main_action_text,
    planned_shots_from_attrs,
    uses_parent_still,
)

COVERAGE_OP_TYPES = frozenset(
    {
        "coverage_plan",
        "coverage_action",
        "coverage_kind",
        "coverage_delete",
        "coverage_template",
        "coverage_anchors",
        "coverage_vo_span",
        "coverage_angle",
        "coverage_move",
        "coverage_stitch",
        "coverage_light",
        "coverage_set",
        "coverage_sense",
        "coverage_visual_type",
        "coverage_place",
        "coverage_characters",
        "coverage_props",
        "coverage_bg",
        "coverage_accent",
        "coverage_feature",
        "coverage_scene_action",
    }
)

# Поля VO-ячейки, которые доска читает в `_scene_common` / `frame_board_scene_cell`.
# «Применить правки» пишет те же ключи, иначе клетка сцены остаётся со старым текстом.
SCENE_FIELD_OP_TYPES = frozenset(
    {
        "coverage_sense",
        "coverage_visual_type",
        "coverage_place",
        "coverage_characters",
        "coverage_props",
        "coverage_bg",
        "coverage_accent",
        "coverage_feature",
    }
)

COVERAGE_PLAN_CHOICES = (
    "ОБЩИЙ",
    "ДАЛЬНИЙ",
    "СРЕДНИЙ",
    "КРУПНЫЙ",
    "ДЕТАЛЬ",
)

COVERAGE_ANGLE_CHOICES = (
    "фронт",
    "3/4",
    "с плеча",
    "сверху",
    "снизу",
    "макро",
)

COVERAGE_MOVE_CHOICES = (
    "статика",
    "наезд",
    "отъезд",
    "панорама",
    "следование",
    "ручная",
)

COVERAGE_STITCH_CHOICES = (
    ("cut", "cut"),
    ("cut_on_action", "по действию"),
    ("eyeline", "взгляд"),
    ("match_cut", "match"),
    ("dissolve", "dissolve"),
    ("fade", "fade"),
)

COVERAGE_LIGHT_CHOICES = (
    "дневной",
    "ночной",
    "закат",
    "рассвет",
    "контровой",
    "холодный верхний",
    "туман",
)

# Словарь тип_сцены из prompts/scene_design/style.md — те же значения, что у агентов.
COVERAGE_VISUAL_TYPE_CHOICES = (
    "Реализм",
    "Кинематографический реализм",
    "Импрессионизм",
    "Экспрессионизм",
    "Сюрреализм",
    "Минимализм",
    "Поп-арт",
    "Ар-деко",
    "Детская книжная иллюстрация",
    "Детективная доска",
)


@dataclass(frozen=True)
class SceneFieldSpec:
    """Один механизм правки поля сцены: op → те ключи, которые читает доска."""

    op_type: str
    payload_key: str
    attr_keys: tuple[str, ...]
    empty_error: str
    slot: str
    cs_keys: tuple[str, ...] = ()
    kadry_keys: tuple[str, ...] = ()


SCENE_FIELD_SPECS: tuple[SceneFieldSpec, ...] = (
    SceneFieldSpec(
        "coverage_sense",
        "sense",
        ("смысл_сцены", "scene_sense"),
        "смысл сцены пустой",
        "sense",
    ),
    SceneFieldSpec(
        "coverage_visual_type",
        "visual_type",
        ("тип_сцены", "visual_type"),
        "тип сцены пустой",
        "visual_type",
    ),
    SceneFieldSpec(
        "coverage_place",
        "place",
        ("место", "place"),
        "место сцены пустое",
        "place",
        cs_keys=("место",),
        kadry_keys=("место",),
    ),
    SceneFieldSpec(
        "coverage_characters",
        "characters",
        ("персонажи_сцены", "characters"),
        "персонажи сцены пустые",
        "characters",
    ),
    SceneFieldSpec(
        "coverage_props",
        "props",
        ("предметы", "shot01_props"),
        "предметы сцены пустые",
        "props",
    ),
    SceneFieldSpec(
        "coverage_bg",
        "bg",
        ("фон", "shot01_bg"),
        "фон сцены пустой",
        "bg",
    ),
    SceneFieldSpec(
        "coverage_accent",
        "accent",
        ("акцент", "accent"),
        "акцент сцены пустой",
        "accent",
    ),
    SceneFieldSpec(
        "coverage_feature",
        "feature",
        ("особенность_сцены", "scene_feature"),
        "особенность сцены пустая",
        "feature",
    ),
)

SCENE_FIELD_BY_OP: dict[str, SceneFieldSpec] = {s.op_type: s for s in SCENE_FIELD_SPECS}

_NO_IMAGE_REGEN = frozenset(
    {
        "coverage_delete",
        "coverage_anchors",
        "coverage_vo_span",
        "coverage_stitch",
        "coverage_scene_action",
    }
)

_STITCH_ALIASES = {
    "по действию": "cut_on_action",
    "cut on action": "cut_on_action",
    "взгляд": "eyeline",
    "match": "match_cut",
    "match cut": "match_cut",
}


def choices_with_current(choices: tuple[str, ...] | list[str], current: str) -> list[str]:
    out = list(choices)
    text = (current or "").strip()
    if text and text not in out:
        out.append(text)
    return out


def canonical_stitch(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    alias = _STITCH_ALIASES.get(text.casefold())
    if alias:
        return alias
    for sid, label in COVERAGE_STITCH_CHOICES:
        if text == sid or text == label:
            return sid
        if text.casefold() == sid.casefold() or text.casefold() == label.casefold():
            return sid
    return text


def stitch_choices_for_ui(current: str = "") -> list[dict[str, str]]:
    out = [{"id": sid, "label": label} for sid, label in COVERAGE_STITCH_CHOICES]
    text = canonical_stitch(current)
    if text and all(row["id"] != text for row in out):
        out.append({"id": text, "label": text})
    return out


def stitch_label(value: str) -> str:
    sid = canonical_stitch(value)
    for key, label in COVERAGE_STITCH_CHOICES:
        if key == sid:
            return label
    return (value or "").strip()


def _vo_parent_frame(frames: list[Frame], frame: Frame) -> Frame:
    """VO-родитель ячейки по parent_uuid. coverage_parent_id (X1) не считаем."""
    if is_shot_child(frame):
        uid = str(_cs(frame).get("parent_uuid") or "").strip()
        if uid:
            for fr in frames:
                if str(getattr(fr, "uuid", "") or "") == uid:
                    return fr
    return frame


def _vo_members(frames: list[Frame], parent: Frame) -> list[Frame]:
    puid = str(getattr(parent, "uuid", "") or "")
    members = [parent]
    if puid:
        for other in frames:
            if int(other.number) == int(parent.number):
                continue
            if str(_cs(other).get("parent_uuid") or "") == puid:
                members.append(other)
    return members


def _by_number(frames: list[Frame], number: int) -> Frame | None:
    for fr in frames:
        if int(fr.number) == int(number):
            return fr
    return None


def _patch_kadry_item(frame: Frame, **fields: Any) -> None:
    planned = planned_shots_from_attrs(frame)
    if not planned:
        item: dict[str, Any] = {"id": coverage_shot_id(frame) or f"{frame.number}-K1"}
        planned = [item]
    else:
        item = planned[0]
        sid = coverage_shot_id(frame)
        if sid:
            for cand in planned:
                if str(cand.get("id") or "").strip() == sid:
                    item = cand
                    break
    for key, val in fields.items():
        if val is None:
            continue
        item[key] = val
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["кадры"] = planned
    frame.attrs = attrs
    _flag_attrs(frame)


def _remove_kadry_id(frame: Frame, shot_id: str) -> None:
    sid = (shot_id or "").strip()
    if not sid:
        return
    planned = [
        item
        for item in planned_shots_from_attrs(frame)
        if str(item.get("id") or "").strip() != sid
    ]
    attrs = dict(getattr(frame, "attrs", None) or {})
    if planned:
        attrs["кадры"] = planned
    else:
        attrs.pop("кадры", None)
        attrs.pop("shots", None)
    frame.attrs = attrs
    _flag_attrs(frame)


def _children_of(frames: list[Frame], parent: Frame) -> list[Frame]:
    """Шоты VO-ячейки по parent_uuid, не по still покрытия."""
    puid = str(getattr(parent, "uuid", "") or "")
    out: list[Frame] = []
    if not puid:
        return out
    for fr in frames:
        if int(fr.number) == int(parent.number):
            continue
        if str(_cs(fr).get("parent_uuid") or "") == puid and is_shot_child(fr):
            out.append(fr)
    return out


def _would_cycle(frames: list[Frame], child: Frame, new_parent: Frame) -> bool:
    cur: Frame | None = new_parent
    seen: set[int] = set()
    while cur is not None:
        cid = int(cur.id or 0) or int(cur.number)
        if int(cur.number) == int(child.number):
            return True
        if cid in seen:
            break
        seen.add(cid)
        nxt = find_coverage_parent_frame(frames, cur)
        if nxt is None or int(nxt.number) == int(cur.number):
            break
        cur = nxt
    return False


def _refresh_shots_in_beat(frames: list[Frame], parent: Frame) -> None:
    kids = _children_of(frames, parent)
    total = 1 + len(kids)
    _set_cs(parent, shots_in_beat=total, role="vo_parent", parent_uuid=parent.uuid)
    for i, kid in enumerate(sorted(kids, key=lambda f: (f.sort_key or 0.0, f.number))):
        _set_cs(kid, shots_in_beat=total, shot_index=i + 2)


async def _load_frames(session: AsyncSession, project_id: int) -> list[Frame]:
    return list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project_id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )


def _apply_shot_fields(
    frame: Frame,
    frames: list[Frame],
    *,
    cs_fields: dict[str, Any],
    kadry_fields: dict[str, Any],
    attrs_fields: dict[str, Any] | None = None,
) -> None:
    if attrs_fields:
        attrs = dict(getattr(frame, "attrs", None) or {})
        attrs.update(attrs_fields)
        frame.attrs = attrs
        _flag_attrs(frame)
    _set_cs(frame, **cs_fields)
    _patch_kadry_item(frame, **kadry_fields)
    parent = _vo_parent_frame(frames, frame)
    if int(parent.number) != int(frame.number):
        _patch_kadry_item_on_parent_ladder(parent, frame, **kadry_fields)


def apply_coverage_plan(frame: Frame, plan: str, frames: list[Frame]) -> None:
    text = (plan or "").strip()
    if not text:
        raise RuntimeError("план пустой")
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["крупность"] = text
    frame.attrs = attrs
    _flag_attrs(frame)
    _set_cs(frame, план=text, крупность=text)
    _patch_kadry_item(frame, план=text)
    parent = find_coverage_parent_frame(frames, frame)
    if parent is not None and int(parent.number) != int(frame.number):
        _patch_kadry_item_on_parent_ladder(parent, frame, план=text)


def apply_coverage_action(frame: Frame, action: str, frames: list[Frame]) -> None:
    text = (action or "").strip()
    if not text:
        raise RuntimeError("действие пустое")
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["shot01_action"] = text
    attrs["действие"] = text
    frame.attrs = attrs
    _flag_attrs(frame)
    _patch_kadry_item(frame, действие=text)
    parent = find_coverage_parent_frame(frames, frame)
    if parent is not None and int(parent.number) != int(frame.number):
        _patch_kadry_item_on_parent_ladder(parent, frame, действие=text)


def _clear_shot_action(frame: Frame) -> None:
    """Лишний шот ячейки после новой цепи — не держит старое действие в склейке."""
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["shot01_action"] = ""
    attrs["действие"] = ""
    role = str((attrs.get("camera_subdivide") or {}).get("role") or "")
    if role != "vo_parent":
        attrs["главное_действие"] = ""
        attrs["main_action"] = ""
        attrs.pop("кадры", None)
        attrs.pop("shots", None)
    frame.attrs = attrs
    _flag_attrs(frame)
    _set_cs(frame, действие="", leftover=True)
    if role == "vo_parent":
        _patch_kadry_item(frame, действие="")


def apply_coverage_angle(frame: Frame, angle: str, frames: list[Frame]) -> None:
    text = (angle or "").strip()
    if not text:
        raise RuntimeError("ракурс пустой")
    _apply_shot_fields(
        frame,
        frames,
        cs_fields={"ракурс": text, "angle": text},
        kadry_fields={"ракурс": text},
    )


def apply_coverage_move(frame: Frame, move: str, frames: list[Frame]) -> None:
    text = (move or "").strip()
    if not text:
        raise RuntimeError("движение пустое")
    _apply_shot_fields(
        frame,
        frames,
        cs_fields={"движение": text, "move": text},
        kadry_fields={"движение": text},
    )


def apply_coverage_stitch(frame: Frame, stitch: str, frames: list[Frame]) -> None:
    text = canonical_stitch(stitch)
    if not text:
        raise RuntimeError("стык пустой")
    label = stitch_label(text)
    _apply_shot_fields(
        frame,
        frames,
        cs_fields={"переход": text, "тип_стыка": text},
        kadry_fields={"переход": text, "тип_стыка": label},
        attrs_fields={"переход": text},
    )


def apply_coverage_light(frame: Frame, light: str, frames: list[Frame]) -> None:
    text = (light or "").strip()
    if not text:
        raise RuntimeError("свет пустой")
    parent = _vo_parent_frame(frames, frame)
    for member in _vo_members(frames, parent):
        attrs = dict(getattr(member, "attrs", None) or {})
        attrs["освещение"] = text
        member.attrs = attrs
        _flag_attrs(member)
        _set_cs(member, освещение=text)
        _patch_kadry_item(member, освещение=text)


def apply_coverage_set(frame: Frame, scene_set: str, frames: list[Frame]) -> None:
    text = (scene_set or "").strip()
    if not text:
        raise RuntimeError("набор пустой")
    parent = _vo_parent_frame(frames, frame)
    for member in _vo_members(frames, parent):
        _set_cs(member, набор=text)
        _patch_kadry_item(member, набор=text)
        if member is not parent:
            _patch_kadry_item_on_parent_ladder(parent, member, набор=text)


def apply_scene_field(
    frame: Frame,
    frames: list[Frame],
    spec: SceneFieldSpec,
    value: str,
) -> None:
    """Записать поле сцены всем кадрам VO-ячейки в ключи, которые читает доска."""
    text = (value or "").strip()
    if not text:
        raise RuntimeError(spec.empty_error)
    parent = _vo_parent_frame(frames, frame)
    for member in _vo_members(frames, parent):
        if spec.cs_keys:
            _set_cs(member, **{key: text for key in spec.cs_keys})
        if spec.attr_keys:
            attrs = dict(getattr(member, "attrs", None) or {})
            for key in spec.attr_keys:
                attrs[key] = text
            member.attrs = attrs
            _flag_attrs(member)
        if spec.kadry_keys:
            kadry = {key: text for key in spec.kadry_keys}
            _patch_kadry_item(member, **kadry)
            if member is not parent:
                _patch_kadry_item_on_parent_ladder(parent, member, **kadry)


def apply_coverage_template(
    frame: Frame,
    frames: list[Frame],
    template: str,
) -> dict[str, Any]:
    """Формат сцены (шаблон T*/X*) на всю группу ячейки + план по лестнице.

    Своё написанное «действие» не затираем — только заглушки каталога
    переписываем под новую роль кадра.
    """
    from app.services.montage_scene_editor import frame_place, scene_group
    from app.services.shot_templates import (
        catalog_shot_rows,
        compose_shot_action,
        is_stub_shot_action,
        normalize_template_id,
        parse_scene_chain,
        template_exists,
    )

    tid = normalize_template_id(template)
    if not tid:
        raise RuntimeError("формат сцены пустой")
    if not template_exists(tid):
        raise RuntimeError(f"формата сцены {tid} нет в каталоге")
    parent, members = scene_group(frames, frame)
    rows = catalog_shot_rows(tid)
    chain = parse_scene_chain(main_action_text(parent))
    scene_action = str(chain[0].get("action") or "") if chain else ""
    place = frame_place(parent)
    rewritten = 0
    for i, member in enumerate(members):
        row = rows[i] if i < len(rows) else None
        extra: dict[str, Any] = {"шаблон": tid}
        patch: dict[str, Any] = {"шаблон": tid}
        if row is not None:
            plan = str(row.get("plan") or "").split("/")[0].strip()
            angle = str(row.get("angle") or "").split("/")[0].strip()
            if plan:
                extra["план"] = plan
                extra["крупность"] = plan
                patch["план"] = plan
                attrs = dict(getattr(member, "attrs", None) or {})
                attrs["крупность"] = plan
                member.attrs = attrs
            if angle and angle != "—":
                extra["ракурс"] = angle
                patch["ракурс"] = angle
            current = str(
                (getattr(member, "attrs", None) or {}).get("shot01_action") or ""
            )
            if is_stub_shot_action(current):
                text = compose_shot_action(
                    plan=plan,
                    place=frame_place(member) or place,
                    scene_action=scene_action,
                    catalog_action=str(row.get("action") or ""),
                    catalog_role=str(row.get("role") or ""),
                )
                apply_coverage_action(member, text, frames)
                rewritten += 1
        _set_cs(member, **extra)
        _patch_kadry_item(member, **patch)
        if member is not parent:
            _patch_kadry_item_on_parent_ladder(parent, member, **patch)
    _flag_attrs(parent)
    return {
        "шаблон": tid,
        "ladder": len(rows),
        "frames": len(members),
        "rewritten_actions": rewritten,
        "missing_frames": max(0, len(rows) - len(members)),
        "extra_frames": max(0, len(members) - len(rows)) if rows else 0,
    }


def _new_shot_from_parent(parent: Frame, kid: Frame) -> None:
    """Новый шот ячейки: место/персонажи/свет от родителя, крупность — за оператором."""
    pattrs = dict(getattr(parent, "attrs", None) or {})
    attrs = dict(getattr(kid, "attrs", None) or {})
    for key in (
        "place",
        "персонажи_сцены",
        "персонажи",
        "characters",
        "shot01_id_scene",
        "id_scene",
        "свет",
        "lighting",
        "фон",
    ):
        if key in pattrs and key not in attrs:
            attrs[key] = pattrs[key]
    kid.attrs = attrs
    _flag_attrs(kid)
    fields: dict[str, Any] = {
        "role": "shot",
        "parent_uuid": parent.uuid,
        "leftover": False,
    }
    nab = str(_cs(parent).get("набор") or "").strip()
    if nab:
        fields["набор"] = nab
    _set_cs(kid, **fields)


async def _grow_cell_shots(
    session: AsyncSession,
    project: Project,
    parent: Frame,
    *,
    after: Frame,
    count: int,
    renumber: bool = True,
) -> tuple[list[Frame], list[Frame], dict[int, int]]:
    """Дописали якорь / улучшение → в ячейке появляются шоты.

    ``renumber=False`` — не трогаем number существующих кадров: превью
    ищутся как frame_NNN_*.png. Новые шоты получают max+1.
    """
    from app.services.db_v2 import insert_frame_after
    from app.services.montage_scene_editor import scene_group
    from app.services.scene_design.camera_expand import renumber_frames_by_sort_key

    before = {int(fr.id): int(fr.number) for fr in await _load_frames(session, int(project.id))}
    after_id = int(after.id)
    fresh: list[Frame] = []
    for _ in range(count):
        kid = await insert_frame_after(session, project, after_frame_id=after_id)
        kid.status = FrameStatus.planned
        kid.start_ts = None
        kid.end_ts = None
        _new_shot_from_parent(parent, kid)
        fresh.append(kid)
        after_id = int(kid.id)
    await session.flush()
    if renumber:
        ordered = await renumber_frames_by_sort_key(session, project)
        remap = {
            before[int(fr.id)]: int(fr.number)
            for fr in ordered
            if int(fr.id) in before and before[int(fr.id)] != int(fr.number)
        }
    else:
        ordered = await _load_frames(session, int(project.id))
        remap = {}
    _refresh_shots_in_beat(ordered, parent)
    _, members = scene_group(ordered, parent)
    logger.info(
        "montage grow #{} ячейка {} +{} шот(ов) → {} кадров renumber={}",
        project.id,
        parent.number,
        len(fresh),
        len(members),
        renumber,
    )
    return members, fresh, remap


def _visible_cell_members(parent: Frame, members: list[Frame]) -> list[Frame]:
    """Живые шоты ячейки: leftover-клей не маппим и не вставляем после него.

    Иначе improve снимает leftover и доска рисует хвост как новые «Сцена · кадр».
    """
    live = [m for m in members if not bool(_cs(m).get("leftover"))]
    if not live:
        live = [parent]
    elif parent not in live:
        live = [parent, *[m for m in live if int(m.id) != int(parent.id)]]
    live.sort(
        key=lambda m: (
            float(getattr(m, "sort_key", 0) or 0.0),
            int(m.number or 0),
        )
    )
    return live


async def apply_coverage_anchors(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    frames: list[Frame],
    anchors: list[Any],
) -> dict[str, Any]:
    """Якоря закадра → биты[] родителя + пересборка кусков VO по кадрам.

    Якорей больше, чем кадров — недостающие шоты создаём сами сразу после
    правленого кадра: «дописал якорь» и есть разбивка, отдельной кнопки нет.
    """
    from app.services.montage_scene_editor import (
        anchor_positions,
        cell_full_text,
        normalize_anchor_rows,
        scene_group,
        split_vo_by_anchors,
    )

    rows = normalize_anchor_rows(anchors)
    parent, members = scene_group(frames, frame)
    if not rows:
        from app.services.montage_scene_editor import claim_scene_bits

        claim_scene_bits(frames, parent, members, [])
        return {
            "anchors": 0,
            "parts": 0,
            "frames": len(members),
            "assigned": 0,
            "inserted_frames": 0,
            "recut": False,
            "renumber": {},
        }
    full = cell_full_text(parent, members)
    if not full:
        raise RuntimeError("у ячейки нет закадрового текста")
    texts = [row["якорь"] for row in rows]
    lost = [texts[i] for i, pos in enumerate(anchor_positions(full, texts)) if pos < 0]
    if lost:
        # Иначе нарезка молча теряет точки реза и кадры дублируют текст.
        raise RuntimeError(
            "якорь не найден по порядку в закадре ячейки: "
            + "; ".join(f"«{x}»" for x in lost)
        )
    parts = split_vo_by_anchors(full, texts)
    if not parts:
        raise RuntimeError("якоря не нашлись в тексте ячейки")

    from app.services.montage_scene_editor import claim_scene_bits

    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["vo_cell_full"] = full
    parent.attrs = attrs
    _flag_attrs(parent)
    claim_scene_bits(frames, parent, members, rows)

    if len(parts) < len(members):
        # Кусков меньше, чем кадров: резать нельзя — соседи остались бы с
        # чужим текстом. Якоря сохранили, шоты убирает «удалить кадр».
        logger.info(
            "montage anchors #{} ячейка {}: {} кусков на {} кадров — только биты",
            project.id,
            parent.number,
            len(parts),
            len(members),
        )
        return {
            "anchors": len(rows),
            "parts": len(parts),
            "frames": len(members),
            "assigned": 0,
            "inserted_frames": 0,
            "recut": False,
            "missing_anchors": len(members) - len(parts),
            "renumber": {},
        }

    group = list(members)
    inserted: list[Frame] = []
    renumber: dict[int, int] = {}
    if len(parts) > len(group):
        group, inserted, renumber = await _grow_cell_shots(
            session,
            project,
            parent,
            after=frame,
            count=len(parts) - len(group),
        )

    used = min(len(parts), len(group))
    # Хвост текста не теряем: последний кадр группы забирает остаток.
    assigned = list(parts[: used - 1]) + [" ".join(parts[used - 1 :])] if used else []
    total_sec = float(getattr(parent, "duration_seconds", 0) or 0)
    for i, member in enumerate(group[:used]):
        piece = " ".join((assigned[i] or "").split())
        if not piece:
            continue
        member.voiceover_text = piece
        _set_cs(member, vo_shot=piece)
        _patch_kadry_item(member, закадр=piece)
        if member is not parent:
            _patch_kadry_item_on_parent_ladder(parent, member, закадр=piece)
    if inserted and total_sec > 0 and used > 0:
        # Аудиометки остаются у родителя, длительность делим на шоты поровну.
        part_sec = round(total_sec / used, 2)
        for member in group[:used]:
            member.duration_seconds = part_sec
    return {
        "anchors": len(rows),
        "parts": len(parts),
        "frames": len(group),
        "assigned": used,
        "inserted_frames": len(inserted),
        "recut": True,
        "missing_frames": max(0, len(parts) - len(group)),
        "renumber": renumber,
    }


def _patch_kadry_item_on_parent_ladder(
    parent: Frame, child: Frame, **fields: Any
) -> None:
    planned = planned_shots_from_attrs(parent)
    if not planned:
        return
    sid = coverage_shot_id(child)
    item = None
    if sid:
        for cand in planned:
            if str(cand.get("id") or "").strip() == sid:
                item = cand
                break
    if item is None and len(planned) > 1:
        idx = int((_cs_shot_index(child) or 1) - 1)
        if 0 <= idx < len(planned):
            item = planned[idx]
    if item is None:
        return
    for key, val in fields.items():
        item[key] = val
    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["кадры"] = planned
    parent.attrs = attrs
    _flag_attrs(parent)


def _cs_shot_index(frame: Frame) -> int | None:
    raw = _cs(frame).get("shot_index")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


_CHAR_COPY_KEYS = ("characters", "персонажи", "persons", "персонажи_сцены")
_ITEM_COPY_KEYS = ("предметы", "shot01_props", "items", "items_seed")


def _has_ref_ids(attrs: dict[str, Any], keys: tuple[str, ...]) -> bool:
    from app.orchestrator.steps.generate_images import _parse_ref_ids

    for key in keys:
        raw = attrs.get(key)
        if isinstance(raw, list) and raw:
            return True
        if _parse_ref_ids(raw):
            return True
    return False


def _inherit_scene_refs_if_empty(frame: Frame, source: Frame | None) -> None:
    """После child→parent: листы персонажей/предметов вместо still родителя."""
    if source is None or int(source.number) == int(frame.number):
        return
    dst = dict(getattr(frame, "attrs", None) or {})
    src = dict(getattr(source, "attrs", None) or {})
    changed = False
    if not _has_ref_ids(dst, _CHAR_COPY_KEYS):
        for key in _CHAR_COPY_KEYS:
            if src.get(key):
                dst[key] = src[key]
                changed = True
    if not _has_ref_ids(dst, _ITEM_COPY_KEYS):
        for key in _ITEM_COPY_KEYS:
            if src.get(key):
                dst[key] = src[key]
                changed = True
    if not changed:
        return
    frame.attrs = dst
    _flag_attrs(frame)


def apply_coverage_kind(
    frame: Frame,
    frames: list[Frame],
    *,
    kind: str,
    parent_number: int | None,
) -> None:
    """Роль покрытия: still родителя вкл/выкл. VO-ячейка (parent_uuid) не меняется."""
    role = (kind or "").strip().lower()
    if role not in ("parent", "child"):
        raise RuntimeError("нужно выбрать родитель или дочерний")
    vo_head = _vo_parent_frame(frames, frame)
    if role == "parent":
        old_still = (
            find_coverage_parent_frame(frames, frame)
            if uses_parent_still(frame)
            else None
        )
        _set_cs(
            frame,
            coverage_kind="parent",
            use_parent_still=False,
            coverage_parent_id="",
        )
        _patch_kadry_item(frame, parent_id="")
        if old_still is not None and int(old_still.number) != int(frame.number):
            _inherit_scene_refs_if_empty(frame, old_still)
        _refresh_shots_in_beat(frames, vo_head)
        return

    if parent_number is None:
        raise RuntimeError("для дочернего кадра выберите родителя")
    still_src = _by_number(frames, int(parent_number))
    if still_src is None:
        raise RuntimeError(f"родитель #{parent_number} не найден")
    if int(still_src.number) == int(frame.number):
        raise RuntimeError("нельзя сделать кадр дочерним самому себе")
    if _would_cycle(frames, frame, still_src):
        raise RuntimeError("нельзя привязать кадр к своему потомку")

    parent_sid = coverage_shot_id(still_src) or f"{still_src.number}-K1"
    _set_cs(
        frame,
        coverage_kind="child",
        use_parent_still=True,
        coverage_parent_id=parent_sid,
    )
    _patch_kadry_item(frame, parent_id=parent_sid)
    _refresh_shots_in_beat(frames, vo_head)


async def delete_coverage_child(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    frames: list[Frame],
) -> None:
    parent = _vo_parent_frame(frames, frame)
    is_child = parent is not None and int(parent.number) != int(frame.number)
    if not (is_shot_child(frame) or is_child):
        raise RuntimeError("удалять можно только дочерний кадр")
    from app.services.ensure_frames_from_disk import quarantine_frame_media

    quarantine_frame_media(project, int(frame.number))
    drop_id = int(frame.id)
    sid = coverage_shot_id(frame)
    await session.execute(
        update(Artifact).where(Artifact.frame_id == drop_id).values(frame_id=None)
    )
    await session.execute(delete(PromptVersion).where(PromptVersion.frame_id == drop_id))
    await session.execute(delete(FrameText).where(FrameText.frame_id == drop_id))
    await session.execute(
        delete(FrameEdge).where(
            FrameEdge.project_id == project.id,
            or_(
                FrameEdge.from_frame_id == drop_id,
                FrameEdge.to_frame_id == drop_id,
            ),
        )
    )
    await session.delete(frame)
    await session.flush()
    if parent is not None:
        remaining = [fr for fr in frames if int(fr.id) != drop_id]
        _remove_kadry_id(parent, sid)
        _refresh_shots_in_beat(remaining, parent)


def _clear_child_scene_chain(frame: Frame) -> None:
    attrs = dict(getattr(frame, "attrs", None) or {})
    if "главное_действие" not in attrs and "main_action" not in attrs:
        return
    attrs.pop("главное_действие", None)
    attrs.pop("main_action", None)
    frame.attrs = attrs
    _flag_attrs(frame)


MAX_IMPROVE_SHOTS = 12


def _apply_shot_coverage(member: Frame, shot: dict[str, Any], group: list[Frame]) -> None:
    """Покрытие кадра с улучшения сцены → те же ключи, что пишут чипы доски."""
    for key, fn in (
        ("план", apply_coverage_plan),
        ("ракурс", apply_coverage_angle),
        ("движение", apply_coverage_move),
        ("стык", apply_coverage_stitch),
    ):
        value = str(shot.get(key) or "").strip()
        if value:
            fn(member, value, group)
    role = str(shot.get("роль") or "").strip()
    obj = str(shot.get("объект") or "").strip()
    extra = {k: v for k, v in (("роль_кадра", role), ("объект", obj)) if v}
    if extra:
        _set_cs(member, **extra)
        _patch_kadry_item(member, **{k: v for k, v in (("роль", role), ("объект", obj)) if v})


async def apply_coverage_scene_action(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    frames: list[Frame],
    action: str,
    *,
    grow: bool = False,
    kadry: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Последовательность кадров сцены → кадры[] на членах ячейки.

    По умолчанию новые Frame не создаём: insert + глобальный renumber
    сдвигает number, а превью ищутся по frame_NNN_*.png.
    ``grow`` дописывает недостающие шоты без перенумерации существующих.
    ``kadry`` — готовые кадры с покрытием (план/ракурс/движение/стык):
    текст не режем заново, покрытие пишем на каждый шот.
    """
    from app.services.montage_scene_editor import (
        cell_full_text,
        cell_scene_text,
        frame_place,
        scene_group,
    )
    from app.services.shot_templates import (
        explode_scene_action_to_kadry,
        format_scene_chain,
        parse_scene_chain,
    )
    from app.services.vo_shot_expand import _apply_shot_meta

    raw = (action or "").strip()
    if not raw:
        raise RuntimeError("последовательность кадров пустая")
    parent, members = scene_group(frames, frame)
    full = cell_full_text(parent, members)
    scene_vo = cell_scene_text(parent, members)
    place = frame_place(parent)
    with_coverage = bool(kadry)
    if kadry:
        kadry = [dict(shot) for shot in kadry if isinstance(shot, dict)]
    else:
        kadry = explode_scene_action_to_kadry(
            raw, place=place, vo=scene_vo, cell_number=int(parent.number)
        )
    if grow and not with_coverage and len(kadry) > MAX_IMPROVE_SHOTS:
        kadry = kadry[:MAX_IMPROVE_SHOTS]
    if not kadry:
        raise RuntimeError("не удалось разобрать действие на кадры")
    if with_coverage and parse_scene_chain(raw):
        chain_text = raw
    else:
        chain_text = format_scene_chain(
            [
                {
                    "n": i + 1,
                    "place": str(shot.get("место") or place or ""),
                    "action": str(shot.get("действие") or ""),
                    "vo": str(shot.get("закадр") or ""),
                }
                for i, shot in enumerate(kadry)
            ]
        )
    if not chain_text:
        raise RuntimeError("последовательность кадров пустая")

    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["главное_действие"] = chain_text
    attrs["main_action"] = chain_text
    if full:
        attrs["vo_cell_full"] = full
    parent.attrs = attrs
    _flag_attrs(parent)

    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["кадры"] = kadry
    parent.attrs = attrs
    _flag_attrs(parent)

    group = _visible_cell_members(parent, members)
    inserted: list[Frame] = []
    remap: dict[int, int] = {}
    if grow and len(kadry) > len(group):
        need = len(kadry) - len(group)
        group, inserted, remap = await _grow_cell_shots(
            session,
            project,
            parent,
            after=group[-1],
            count=need,
            renumber=False,
        )
        frames = await _load_frames(session, int(project.id))
        parent, members = scene_group(frames, parent)
        group = _visible_cell_members(parent, members)
    elif len(kadry) > len(group):
        logger.info(
            "montage scene_action #{} ячейка {} цепь={} кадров, в ячейке {} — без вставки",
            project.id,
            parent.number,
            len(kadry),
            len(group),
        )

    used = min(len(kadry), len(group))
    parent_sid = str(kadry[0].get("id") or f"{int(parent.number)}-K1")
    for i, member in enumerate(group[:used]):
        shot = kadry[i]
        _apply_shot_meta(member, shot)
        piece = " ".join(str(shot.get("закадр") or "").split())
        if piece:
            member.voiceover_text = piece
            _set_cs(member, vo_shot=piece)
        extra: dict[str, Any] = {
            "shot_id": str(shot.get("id") or ""),
            "shot_index": i + 1,
        }
        if i == 0:
            extra.update(
                {
                    "role": "vo_parent",
                    "parent_uuid": parent.uuid,
                    "coverage_parent_id": "",
                }
            )
        else:
            extra.update(
                {
                    "role": "shot",
                    "parent_uuid": parent.uuid,
                    "coverage_parent_id": parent_sid,
                }
            )
            _clear_child_scene_chain(member)
        extra["leftover"] = False
        _set_cs(member, **extra)
        act = str(shot.get("действие") or "").strip()
        if act:
            apply_coverage_action(member, act, group)
        if with_coverage:
            _apply_shot_coverage(member, shot, group)
        if member is not parent:
            ladder: dict[str, Any] = {}
            if piece:
                ladder["закадр"] = piece
            if act:
                ladder["действие"] = act
            if shot.get("план"):
                ladder["план"] = shot.get("план")
            if shot.get("ракурс"):
                ladder["ракурс"] = shot.get("ракурс")
            if ladder:
                _patch_kadry_item_on_parent_ladder(parent, member, **ladder)
    for member in group[used:]:
        _clear_shot_action(member)
    _refresh_shots_in_beat(group, parent)
    logger.info(
        "montage scene_action #{} ячейка {} кадры={} inserted={}",
        project.id,
        parent.number,
        used,
        len(inserted),
    )
    return {
        "shots": used,
        "frames": len(group),
        "inserted_frames": len(inserted),
        "skipped_shots": max(0, len(kadry) - used),
        "renumber": remap,
        "chain": chain_text,
    }


async def apply_coverage_op(
    session: AsyncSession,
    project: Project,
    op: dict[str, Any],
) -> dict[str, Any]:
    """Записать план/действие/родство. Highlight ключ слота покрытия."""
    op_type = str(op.get("type") or "").strip()
    if op_type not in COVERAGE_OP_TYPES:
        raise RuntimeError(f"неизвестная coverage-операция: {op_type}")
    frame_number = int(op["frame_number"])
    frames = await _load_frames(session, int(project.id))
    frame = _by_number(frames, frame_number)
    if frame is None:
        raise RuntimeError(f"кадр {frame_number} не найден")

    report: dict[str, Any] | None = None
    if op_type == "coverage_plan":
        apply_coverage_plan(frame, str(op.get("plan") or ""), frames)
    elif op_type == "coverage_action":
        apply_coverage_action(frame, str(op.get("action") or ""), frames)
    elif op_type == "coverage_angle":
        apply_coverage_angle(frame, str(op.get("angle") or ""), frames)
    elif op_type == "coverage_move":
        apply_coverage_move(frame, str(op.get("move") or ""), frames)
    elif op_type == "coverage_stitch":
        apply_coverage_stitch(frame, str(op.get("stitch") or ""), frames)
    elif op_type == "coverage_light":
        apply_coverage_light(frame, str(op.get("light") or ""), frames)
    elif op_type == "coverage_set":
        apply_coverage_set(frame, str(op.get("set") or ""), frames)
    elif op_type in SCENE_FIELD_BY_OP:
        spec = SCENE_FIELD_BY_OP[op_type]
        apply_scene_field(frame, frames, spec, str(op.get(spec.payload_key) or ""))
    elif op_type == "coverage_template":
        report = apply_coverage_template(frame, frames, str(op.get("template") or ""))
    elif op_type == "coverage_anchors":
        report = await apply_coverage_anchors(
            session, project, frame, frames, list(op.get("anchors") or [])
        )
    elif op_type == "coverage_vo_span":
        from app.services.montage_scene_editor import apply_vo_span, scene_group

        parent, members = scene_group(frames, frame)
        raw = None if op.get("clear") else (op.get("vo_span") or op)
        full_raw = op.get("full") or op.get("vo_cell_full")
        apply_vo_span(
            parent,
            members,
            raw,
            full_text=str(full_raw) if isinstance(full_raw, str) else None,
        )
    elif op_type == "coverage_scene_action":
        report = await apply_coverage_scene_action(
            session, project, frame, frames, str(op.get("action") or "")
        )
    elif op_type == "coverage_kind":
        parent_raw = op.get("parent_number")
        parent_number = int(parent_raw) if parent_raw not in (None, "") else None
        apply_coverage_kind(
            frame,
            frames,
            kind=str(op.get("kind") or ""),
            parent_number=parent_number,
        )
    elif op_type == "coverage_delete":
        await delete_coverage_child(session, project, frame, frames)

    await session.flush()
    highlight = slot_key_from_op(op)
    logger.info(
        "montage coverage #{} {} frame {} → {}",
        project.id,
        op_type,
        frame_number,
        highlight,
    )
    out = {
        "ok": True,
        "highlight": highlight,
        "frame_number": frame_number,
        "op": op,
        # Якоря меняют только нарезку закадра — картинку не трогаем.
        "regen_image": op_type not in _NO_IMAGE_REGEN,
    }
    if report is not None:
        out["report"] = report
        # Вставка шота сдвинула нумерацию — очередь и подсветку правит apply.
        renumber = report.pop("renumber", None)
        if renumber:
            out["renumber"] = {int(k): int(v) for k, v in renumber.items()}
        if report.get("inserted_frames"):
            out["refresh_board"] = True
    return out
