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
Один кадр, не батч. Старого промта кадра нет — пиши с нуля по агенту и закадру.

Верни ОДИН полный промт: сцена + STYLE / Final style lock + Negative.
Не JSON apply-ops. Не копируй закадр.
Ответ: только текст промта, без пояснений.
"""

_SYSTEM_VIDEO = """\
Вложенный файл — агент картинок (стиль и правила кадра).
Пишешь короткий ВИДЕОПРОМТ по закадру. Не JSON. Без музыки, silent video only.
Ответ: только текст видеопромта.
"""


def build_ai_change_user_message(*, voiceover_text: str) -> str:
    vo = (voiceover_text or "").strip() or "(пусто)"
    return (
        f"VOICEOVER:\n{vo}\n\n"
        "Карточка кадра — во вложенном db_frames.json (База: место, действие, "
        "персонажи, камера, свет). Старого промта нет.\n"
        "Напиши полный промт по вложенному агенту. "
        "STYLE / Final style lock / Negative пишешь ты. Не JSON. Только промт."
    )


def write_ai_change_db_card(
    project: object,
    frame: object,
    dest_dir: Path,
    *,
    characters: list | None = None,
) -> Path:
    """Один кадр из Базы — тот же снимок, что img_pr кладёт в db_frames.json."""
    from app.services.db_frames_context import build_img_pr_db_context

    dest_dir.mkdir(parents=True, exist_ok=True)
    ctx = build_img_pr_db_context(
        project_id=int(getattr(project, "id", 0) or 0),
        slug=str(getattr(project, "slug", "") or ""),
        frames=[frame],
        characters=list(characters or []),
        general_plan=str(getattr(project, "general_plan", "") or ""),
        include_characters=True,
        include_field_map=True,
    )
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
) -> str:
    """Агент + карточка Базы + закадр → vibecode LLM → промт как есть."""
    del img_pr_rules, img_pr_variant, image_prompt
    from app.services.llm_override import bind_generation_llm

    user = build_ai_change_user_message(voiceover_text=voiceover_text)
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
    cleaned = strip_ai_change_reply(raw)
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
