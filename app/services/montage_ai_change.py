"""ИИзменение в монтаже: LLM пишет промт по агенту img_pr.

В модель: файл агента (master) + закадр. Старый промт кадра не отправляем.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from loguru import logger

from app.services.gpt_client import get_gpt_client
from app.services.prompt_library import read_resolved_project_prompt

AiChangeKind = Literal["image", "video"]

_SYSTEM_IMAGE = """\
Ты — агент из вложенного файла. Пиши промт картинки по его правилам.
Один кадр, не батч. Старого промта кадра нет — пиши с нуля по агенту и карточке.

Если в карточке есть coverage_parent — первый абзац = A0 Preserve/Change:
тот же сет, те же люди, те же экземпляры предметов, тот же свет.
Меняются только камера и фаза действия ЭТОГО кадра. Не новая комната.

Затем персонаж: Image N / cNN, где стоит, что делает.
Референс = только identity (лицо/тело/одежда). Не копируй сетку листа
и не клонируй людей с приложенной картинки, если промт описывает другой каст.

Крупность и ракурс из карточки обязательны в строке:
Scene feature / shot scale: [план] · [ракурс] · [движение]
Не подменяй чипы. ДЕТАЛЬ = punch-in на предмет действия, не вся локация.
ОБЩИЙ = фигуры и то, что видно из действия ЭТОГО кадра. Не тащи
место/фон/дверь из чужого шота и не выдумывай паспорт сцены.

Accent — только этого шота.

STYLE / Final style lock / Negative — максимум 6 коротких строк.
Не копируй словарь стиля / §5–§6 из агента.

Верни ОДИН полный промт. Не JSON apply-ops. Не копируй закадр.
Ответ: только текст промта, без пояснений.
"""

_SYSTEM_VIDEO = """\
Вложенный файл — агент картинок (стиль и правила кадра).
Пишешь короткий ВИДЕОПРОМТ по закадру. Не JSON. Без музыки, silent video only.
Ответ: только текст видеопромта.
"""


def build_ai_change_user_message(
    *,
    voiceover_text: str,
    instruction: str = "",
    action: str = "",
    camera: dict[str, str] | None = None,
    coverage_role: str = "",
) -> str:
    vo = (voiceover_text or "").strip() or "(пусто)"
    note = (instruction or "").strip()
    act = (action or "").strip()
    parts = [f"VOICEOVER:\n{vo}\n"]
    if act:
        parts.append(
            f"\nACTION:\n{act}\n"
            "\nЭто действие ЭТОГО кадра, не цепи всей сцены.\n"
        )
    chips = camera if isinstance(camera, dict) else {}
    plan = str(chips.get("план") or chips.get("крупность") or "").strip()
    angle = str(chips.get("ракурс") or "").strip()
    move = str(chips.get("движение") or "").strip()
    if plan or angle or move:
        parts.append("\nCAMERA:\n")
        if plan:
            parts.append(f"план: {plan}\n")
        if angle:
            parts.append(f"ракурс: {angle}\n")
        if move:
            parts.append(f"движение: {move}\n")
        parts.append(
            "Эти значения обязательны в строке Scene feature / shot scale. "
            "Не подменяй план и ракурс.\n"
        )
    role = str(coverage_role or "").strip().lower()
    if role == "child":
        parts.append(
            "\nCOVERAGE: дочерний кадр. В карточке coverage_parent — "
            "начни с A0 Preserve/Change. Тот же сет и те же предметы, "
            "другая камера и фаза.\n"
        )
    if note:
        parts.append(
            f"\nOPERATOR_CHANGE:\n{note}\n\n"
            "Это то, что оператор написал для промта. "
            "Если там новое действие или правка действия — изобрази именно его: "
            "OPERATOR_CHANGE важнее ACTION, если они расходятся. "
            "Не копируй заметку дословно — переведи в визуальный промт по правилам агента."
        )
    parts.append(
        "\nКарточка кадра — во вложенном db_frames.json (действие, персонажи, "
        "камера, coverage_parent). Паспорта сцены нет — не бери место/фон/"
        "свет/предметы ячейки. Старого промта нет.\n"
        "Напиши полный промт по вложенному агенту. "
        "Дочерний кадр — A0 первым, иначе блок персонажа первым. "
        "STYLE / Final style lock / Negative — коротко, не словарь из агента. "
        "Не JSON. Только промт."
    )
    return "".join(parts)


def write_ai_change_db_card(
    project: object,
    frame: object,
    dest_dir: Path,
    *,
    characters: list | None = None,
    all_frames: list | None = None,
) -> Path:
    """Один кадр из Базы — тот же снимок, что img_pr кладёт в db_frames.json."""
    from app.services.db_frames_context import build_img_pr_db_context

    dest_dir.mkdir(parents=True, exist_ok=True)
    universe = list(all_frames) if all_frames is not None else [frame]
    ctx = build_img_pr_db_context(
        project_id=int(getattr(project, "id", 0) or 0),
        slug=str(getattr(project, "slug", "") or ""),
        frames=[frame],
        characters=list(characters or []),
        general_plan=str(getattr(project, "general_plan", "") or ""),
        include_characters=True,
        include_field_map=True,
        all_frames=universe,
    )
    _pin_ai_change_shot_action(ctx, frame)
    path = dest_dir / "db_frames.json"
    path.write_text(
        json.dumps(ctx, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info(
        "montage_ai_change: db card {} bytes frame={} chars={}",
        path.stat().st_size,
        getattr(frame, "number", "?"),
        len(ctx.get("characters") or []),
    )
    return path


_SCENE_PASSPORT_KEYS = frozenset(
    {
        "place",
        "место",
        "shot01_bg",
        "фон",
        "lighting",
        "scene_lighting",
        "освещение",
        "освещение_сцены",
        "shot01_props",
        "предметы",
        "accent",
        "акцент",
        "scene_sense",
        "смысл",
        "смысл_сцены",
        "scene_feature",
        "особенность",
        "особенность_сцены",
        "visual_type",
        "тип_сцены",
        "набор",
        "set",
        "scene_set",
    }
)


def _strip_scene_passport_fields(row: dict) -> None:
    """Паспорт ячейки не участвует в ИИзменении — только шот."""
    for key in _SCENE_PASSPORT_KEYS:
        row.pop(key, None)
    parent = row.get("coverage_parent")
    if isinstance(parent, dict):
        for key in _SCENE_PASSPORT_KEYS:
            parent.pop(key, None)


def _pin_ai_change_shot_action(ctx: dict, frame: object) -> None:
    """В карточке ИИзменения — действие этого шота, без цепи сцены и чужих K."""
    from app.services.db_frames_context import apply_camera_chips_to_row
    from app.services.montage_board import _action_for_frame
    from app.services.vo_shot_expand import _cs, coverage_shot_id

    action = _action_for_frame(frame)
    sid = str(_cs(frame).get("shot_id") or "").strip() or coverage_shot_id(frame)
    for row in ctx.get("frames") or []:
        if not isinstance(row, dict):
            continue
        _strip_scene_passport_fields(row)
        apply_camera_chips_to_row(row, frame)
        if action:
            row["shot01_action"] = action
            row["действие"] = action
        row.pop("main_action", None)
        row.pop("главное_действие", None)
        kadry = row.get("кадры")
        if not isinstance(kadry, list) or not kadry:
            continue
        match = [
            item
            for item in kadry
            if isinstance(item, dict) and str(item.get("id") or "").strip() == sid
        ]
        if match:
            row["кадры"] = match
            continue
        if action:
            same = [
                item
                for item in kadry
                if isinstance(item, dict)
                and str(item.get("действие") or item.get("action") or "").strip()
                == action
            ]
            if same:
                row["кадры"] = same
                continue
        if len(kadry) > 1:
            row["кадры"] = [kadry[0]] if isinstance(kadry[0], dict) else kadry[:1]


def load_img_pr_master(project: object | None) -> tuple[Path | None, str]:
    """Живой .md агента img_pr проекта — тот же файл, что у ноды."""
    if project is None:
        return None, ""
    try:
        name, path, _text, source = read_resolved_project_prompt(project, "img_pr")
        logger.info(
            "montage_ai_change: img_pr master variant={!r} source={} path={}",
            name,
            source,
            path,
        )
        if path.is_file():
            return path, name
    except Exception as exc:  # noqa: BLE001
        logger.warning("montage_ai_change: img_pr master skip: {}", exc)
    return None, ""


def load_img_pr_rules(project: object | None) -> str:
    """Текст агента — только если нужно прочитать, не класть в system."""
    path, _name = load_img_pr_master(project)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_C_ID_RE = re.compile(r"\bc(\d{2})\b", re.I)


def character_ids_from_prompt(text: str) -> list[str]:
    seen: list[str] = []
    for match in _C_ID_RE.finditer(text or ""):
        rid = f"c{match.group(1)}"
        if rid not in seen:
            seen.append(rid)
    return seen


_STYLE_LINE_RE = re.compile(
    r"(?im)^(?:\*\*)?(?:STYLE|Final style lock|Negative)\b"
)
_MAX_AI_CHANGE_STYLE = 900


def trim_style_encyclopedia(text: str, max_style: int = _MAX_AI_CHANGE_STYLE) -> str:
    """Агент часто копирует §5–§6 целиком — тогда Outsee сжимает персонажа."""
    raw = (text or "").strip()
    match = _STYLE_LINE_RE.search(raw)
    start = match.start() if match else raw.find("STYLE:")
    if start < 0:
        return raw
    scene, style = raw[:start].rstrip(), raw[start:].strip()
    if len(style) <= max_style:
        return raw
    cut = style[:max_style]
    sp = cut.rfind(" ")
    if sp > int(max_style * 0.85):
        cut = cut[:sp]
    logger.info(
        "montage_ai_change: STYLE {} → {} симв (блок персонажа не трогаем)",
        len(style),
        len(cut),
    )
    return f"{scene}\n\n{cut}" if scene else cut


def strip_ai_change_reply(raw: str) -> str:
    """Срезать обёртки; если модель вернула apply-ops — взять промт_картинки."""
    text = (raw or "").strip()
    if not text:
        return ""
    fence = re.match(r"^```(?:\w+)?\s*\n?(.*?)\n?```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{") and '"ops"' in text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            ops = data.get("ops")
            if isinstance(ops, list) and ops:
                fields = ops[0].get("fields") if isinstance(ops[0], dict) else None
                if isinstance(fields, dict):
                    for key in ("промт_картинки", "image_prompt", "промт_видео"):
                        val = str(fields.get(key) or "").strip()
                        if val:
                            return val
    text = re.sub(
        r"^(?:updated\s+)?(?:image\s+|video\s+)?prompt\s*:\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:обновлённый\s+|новый\s+)?пром[пт]\s*:\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'«»":
        text = text[1:-1].strip()
    return text.strip()


def system_for_kind(kind: AiChangeKind, *, img_pr_rules: str = "") -> str:
    del img_pr_rules
    return _SYSTEM_VIDEO if kind == "video" else _SYSTEM_IMAGE


async def rewrite_prompt_via_gpt(
    *,
    voiceover_text: str,
    kind: AiChangeKind,
    project_id: int | None = None,
    project: object | None = None,
    img_pr_rules: str = "",
    img_pr_path: Path | None = None,
    img_pr_variant: str = "",
    image_prompt: str = "",
    db_card_path: Path | None = None,
    instruction: str = "",
    action: str = "",
    camera: dict[str, str] | None = None,
    coverage_role: str = "",
) -> str:
    """Агент + карточка Базы + закадр → vibecode LLM → промт как есть."""
    del img_pr_rules, img_pr_variant, image_prompt
    from app.services.llm_override import bind_generation_llm

    user = build_ai_change_user_message(
        voiceover_text=voiceover_text,
        instruction=instruction,
        action=action,
        camera=camera,
        coverage_role=coverage_role,
    )
    system = system_for_kind(kind)
    files: list[Path] = []
    if img_pr_path is not None and img_pr_path.is_file():
        files.append(img_pr_path)
    if db_card_path is not None and db_card_path.is_file():
        files.append(db_card_path)
    with bind_generation_llm(project, node_type="image_prompts"):
        gpt = get_gpt_client()
        raw = await gpt.ask_with_files(
            user,
            files,
            timeout=180,
            project_id=project_id,
            expect_file_download=False,
            system=system,
            auto_pack=False,
        )
    cleaned = trim_style_encyclopedia(strip_ai_change_reply(raw))
    if not cleaned:
        raise RuntimeError("ИИзменение: GPT вернул пустой промт")
    logger.info(
        "montage_ai_change: kind={} pid={} in_vo={} out={} master={} head={!r} refs={}",
        kind,
        project_id,
        len((voiceover_text or "").strip()),
        len(cleaned),
        img_pr_path.name if img_pr_path is not None else "—",
        (cleaned or "").replace("\n", " ")[:80],
        character_ids_from_prompt(cleaned),
    )
    return cleaned
