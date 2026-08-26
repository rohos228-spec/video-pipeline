"""ИИзменение в монтаже: GPT переписывает промт картинки/видео из IMAGE_PROMPT + VOICEOVER."""

from __future__ import annotations

import re
from typing import Literal

from loguru import logger

from app.services.gpt_client import get_gpt_client
from app.services.prompt_library import get_project_prompt

AiChangeKind = Literal["image", "video"]

_HARD_LOCKS = """\
ЖЁСТКИЕ ЗАПРЕТЫ (нарушение = брак):
1) ЗАПРЕЩЕНО добавлять новые предметы/объекты/людей/элементы окружения, которых нет в IMAGE_PROMPT.
2) ЗАПРЕЩЕНА смена изображения: тот же кадр, те же персонажи, та же среда, тот же стиль и композиция.
3) ЗАПРЕЩЕНА смена плана: не меняй крупность (общий/средний/крупный), ракурс, угол камеры, дистанцию, кадрирование.
4) ЗАПРЕЩЕНО ломать правила и логику промта картинок: структуру блоков, Reference/character sheet, STYLE / Final style lock, Negative.
5) ЗАПРЕЩЕНО копировать или пересказывать VOICEOVER в промт (закадр ≠ текст на картинке и ≠ сюжетный абзац).
Можно менять только действие/мизансцену внутри УЖЕ заданного плана и набора объектов, чтобы отразить смысл VOICEOVER.
"""

_SYSTEM_IMAGE = """\
Ты переписываешь ОДИН уже готовый промт картинки.

Вход размечен строго:
- IMAGE_PROMPT — текущий визуальный промт. Это источник правды по кадру.
- VOICEOVER — закадровый текст (смысл сцены). Не копировать в промт.
- Правила агента img_pr проекта — обязательны, если приложены ниже.

Задача: вернуть обновлённый IMAGE_PROMPT, который:
1) отражает логику и смысл VOICEOVER только через действие/мизансцену;
2) сохраняет визуал IMAGE_PROMPT: стиль, персонажи, предметы, среда, план и ракурс;
3) сохраняет ту же логику и форму, что у промта картинок: блоки, рефы cNN, STYLE, Negative — не выкидывай и не ужимай.

Сейчас ты НЕ пишешь JSON apply-ops и НЕ заполняешь батч uuid.

""" + _HARD_LOCKS + """
Ответ: ТОЛЬКО текст обновлённого промта. Без markdown, без кавычек-обёрток, без пояснений.
"""

_SYSTEM_VIDEO = """\
Ты пишешь промт для генерации короткого ВИДЕО по стартовому кадру.

Вход размечен строго:
- IMAGE_PROMPT — визуал стартового кадра (что уже есть в картинке: объекты, план, ракурс).
- VOICEOVER — закадровый текст кадра (какое действие/смысл нужно показать).

Задача: вернуть обновлённый ВИДЕОПРОМТ, который:
1) отражает логику VOICEOVER как движение/действие ВНУТРИ того же кадра;
2) опирается только на объекты и композицию из IMAGE_PROMPT;
3) держит тот же план и ракурс, что заданы стартовым кадром / IMAGE_PROMPT;
4) соблюдает правила и логику промта картинок (стиль, персонажи, запрет новых объектов).

Сейчас ты НЕ пишешь JSON apply-ops.

""" + _HARD_LOCKS + """
Дополнительно для видео:
- запрещена музыка;
- silent video only: без речи, диалога, закадра, звуковых эффектов и голосов;
- не переключай кадр, не делай cut на другой план, не «переснимай» сцену с другого угла.

Ответ: ТОЛЬКО текст обновлённого видеопромта. Без markdown, без кавычек-обёрток, без пояснений.
"""


def build_ai_change_user_message(*, image_prompt: str, voiceover_text: str) -> str:
    img = (image_prompt or "").strip() or "(пусто)"
    vo = (voiceover_text or "").strip() or "(пусто)"
    return (
        f"IMAGE_PROMPT:\n{img}\n\n"
        f"VOICEOVER:\n{vo}\n\n"
        "Жёстко: соблюдай правила и логику промта картинок. "
        "Без новых предметов, без смены изображения, без смены плана/ракурса. "
        "Не копируй закадр. Сохрани структуру, STYLE и Negative. Не JSON.\n"
        "Ответь ТОЛЬКО обновлённым промтом, без пояснений."
    )


def load_img_pr_rules(project: object | None) -> str:
    """Живой мастер img_pr проекта. Нет файла / сломанный путь — пусто, не падаем."""
    if project is None:
        return ""
    try:
        return (get_project_prompt(project, "img_pr") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("montage_ai_change: img_pr rules skip: {}", exc)
        return ""


def _append_img_pr_rules(system: str, img_pr_rules: str) -> str:
    rules = (img_pr_rules or "").strip()
    if not rules:
        return system
    return (
        f"{system}\n\n"
        "=== ПРАВИЛА И ЛОГИКА ПРОМТА КАРТИНОК (агент img_pr проекта, обязательны) ===\n"
        "Соблюдай стиль, структуру блоков, референсы персонажей и запреты агента.\n"
        "Игнор формата ответа агента: не JSON, не apply-ops, не батч. "
        "Один обновлённый промт.\n\n"
        f"{rules}\n"
    )


def strip_ai_change_reply(raw: str) -> str:
    """Срезать типичные обёртки модели, оставить чистый промт."""
    text = (raw or "").strip()
    if not text:
        return ""
    # ```...``` fences
    fence = re.match(r"^```(?:\w+)?\s*\n?(.*?)\n?```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # "PROMPT: ..." / «Промт: ...»
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
    # Обёртка в одинарные/двойные кавычки на весь ответ
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'«»":
        text = text[1:-1].strip()
    return text.strip()


def system_for_kind(kind: AiChangeKind, *, img_pr_rules: str = "") -> str:
    base = _SYSTEM_VIDEO if kind == "video" else _SYSTEM_IMAGE
    return _append_img_pr_rules(base, img_pr_rules)


async def rewrite_prompt_via_gpt(
    *,
    image_prompt: str,
    voiceover_text: str,
    kind: AiChangeKind,
    project_id: int | None = None,
    img_pr_rules: str = "",
) -> str:
    """Вызвать GPT и вернуть только очищенный обновлённый промт."""
    user = build_ai_change_user_message(
        image_prompt=image_prompt,
        voiceover_text=voiceover_text,
    )
    system = system_for_kind(kind, img_pr_rules=img_pr_rules)
    gpt = get_gpt_client()
    raw = await gpt.ask_with_files(
        user,
        [],
        timeout=180,
        project_id=project_id,
        expect_file_download=False,
        system=system,
    )
    cleaned = strip_ai_change_reply(raw)
    if not cleaned:
        raise RuntimeError("ИИзменение: GPT вернул пустой промт")
    logger.info(
        "montage_ai_change: kind={} pid={} in_img={} in_vo={} out={} rules={}",
        kind,
        project_id,
        len((image_prompt or "").strip()),
        len((voiceover_text or "").strip()),
        len(cleaned),
        len((img_pr_rules or "").strip()),
    )
    return cleaned
