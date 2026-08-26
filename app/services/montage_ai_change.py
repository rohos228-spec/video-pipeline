"""ИИзменение в монтаже: GPT переписывает промт картинки/видео.

Мастер = живой файл img_pr проекта (как у ноды), не хардкод-переписчик.
Кадр + закадр — accompanying. Стиль берётся из агента, не из system.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from loguru import logger

from app.services.gpt_client import get_gpt_client
from app.services.img_pr_style import (
    KNITTED_STYLE_HEAD,
    KNITTED_STYLE_TAIL,
    STYLE_HEAD,
    STYLE_TAIL,
    already_has_style,
    looks_knitted,
    looks_noir,
)
from app.services.prompt_library import read_resolved_project_prompt

AiChangeKind = Literal["image", "video"]

_HARD_LOCKS = """\
ЖЁСТКИЕ ЗАПРЕТЫ (нарушение = брак):
1) Стиль — ТОЛЬКО из вложенного агента img_pr. Не меняй medium / палитру / lock.
2) ЗАПРЕЩЕНО добавлять новые предметы/объекты/людей/среду, которых нет в IMAGE_PROMPT.
3) ЗАПРЕЩЕНА смена плана: крупность, ракурс, угол, дистанция, кадрирование.
4) ЗАПРЕЩЕНО копировать или пересказывать VOICEOVER в промт.
5) Не пиши JSON apply-ops и не заполняй батч uuid.
Можно менять только действие/мизансцену, чтобы отразить смысл VOICEOVER.
"""

_SYSTEM_IMAGE = """\
Вложенный файл — мастер-агент промтов картинок проекта. Следуй ему.
IMAGE_PROMPT + VOICEOVER — один кадр, не батч.

Верни ОДИН полный промт картинки: сцена + STYLE / Final style lock + Negative
из агента. Стиль агента священен. Не JSON.

""" + _HARD_LOCKS + """
Ответ: ТОЛЬКО текст промта. Без markdown, без кавычек-обёрток, без пояснений.
"""

_SYSTEM_VIDEO = """\
Вложенный файл — визуальные правила кадра (агент картинок). Стиль не меняй.
Пишешь короткий ВИДЕОПРОМТ по IMAGE_PROMPT + VOICEOVER.

""" + _HARD_LOCKS + """
Дополнительно для видео:
- запрещена музыка;
- silent video only: без речи, диалога, закадра, звуковых эффектов и голосов;
- не переключай кадр, не делай cut на другой план.

Ответ: ТОЛЬКО текст видеопромта. Без markdown, без кавычек-обёрток, без пояснений.
"""


def build_ai_change_user_message(*, image_prompt: str, voiceover_text: str) -> str:
    img = (image_prompt or "").strip() or "(пусто)"
    vo = (voiceover_text or "").strip() or "(пусто)"
    return (
        f"IMAGE_PROMPT:\n{img}\n\n"
        f"VOICEOVER:\n{vo}\n\n"
        "Вложенный .md — мастер. Стиль и lock бери из него, не выдумывай другой. "
        "Без новых предметов, без смены плана. Не копируй закадр. Не JSON.\n"
        "Ответь ТОЛЬКО обновлённым промтом, без пояснений."
    )


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


def ensure_img_pr_style(text: str, *, variant: str = "") -> str:
    """Если GPT выкинул lock агента — вернуть канон того же стиля."""
    body = (text or "").strip()
    if not body or already_has_style(body):
        return body
    blob = f"{variant}\n{body[:800]}"
    if looks_knitted(blob):
        return f"{body}\n\n{KNITTED_STYLE_HEAD}\n{KNITTED_STYLE_TAIL}"
    if looks_noir(variant) or looks_noir(body):
        return f"{body}\n\n{STYLE_HEAD}\n{STYLE_TAIL}"
    if "watercolor" in variant.casefold() or "trash_polka" in variant.casefold():
        return f"{body}\n\n{STYLE_HEAD}\n{STYLE_TAIL}"
    return body


def system_for_kind(kind: AiChangeKind, *, img_pr_rules: str = "") -> str:
    del img_pr_rules
    return _SYSTEM_VIDEO if kind == "video" else _SYSTEM_IMAGE


async def rewrite_prompt_via_gpt(
    *,
    image_prompt: str,
    voiceover_text: str,
    kind: AiChangeKind,
    project_id: int | None = None,
    img_pr_rules: str = "",
    img_pr_path: Path | None = None,
    img_pr_variant: str = "",
) -> str:
    """Мастер = файл img_pr. Кадр — в сообщении. Стиль из агента."""
    del img_pr_rules
    user = build_ai_change_user_message(
        image_prompt=image_prompt,
        voiceover_text=voiceover_text,
    )
    system = system_for_kind(kind)
    files: list[Path] = []
    if img_pr_path is not None and img_pr_path.is_file():
        files.append(img_pr_path)
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
    if kind == "image":
        cleaned = ensure_img_pr_style(cleaned, variant=img_pr_variant)
    logger.info(
        "montage_ai_change: kind={} pid={} in_img={} in_vo={} out={} master={}",
        kind,
        project_id,
        len((image_prompt or "").strip()),
        len((voiceover_text or "").strip()),
        len(cleaned),
        img_pr_path.name if img_pr_path is not None else "—",
    )
    return cleaned
