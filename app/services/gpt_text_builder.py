"""Сборщик «сопр. сообщения» (full_prompt / chat_msg) для шагов пайплайна,
которые шлют текстовый запрос в ChatGPT.

Зачем нужен этот модуль:
  - На каждом этапе бот собирает один длинный текст и отправляет его в GPT
    (через `ask_fresh`, `ask_with_file`, `ask_with_files`). Этот текст
    собирается из мастер-промта (выбранный вариант с диска) + контекстных
    данных проекта (тема, плана, сценария, ссылок на прикреплённые файлы).
  - Пользователь хочет иметь возможность отредактировать ИМЕННО ЭТОТ текст
    перед запуском шага. Для этого мы:
      1) Выносим сборку дефолтного текста в отдельные функции (`build_*`).
      2) Храним пользовательский override в поле `Project.gpt_text_overrides`
         (JSON: {step_code -> текст}).
      3) Возвращаем «эффективный» текст — override если задан, иначе дефолт
         (`get_effective_text`).
      4) Даём UI (см. `app/telegram/bot.py`) отправить файл с дефолтом,
         принять отредактированную версию и сохранить через `set_override`.

Поддерживаемые шаги (имеют мастер-промт + один батч-текст в GPT):
  - `plan`   — Шаг 1 «План»
  - `script` — Шаг 2 «Закадровый текст»
  - `split`  — Шаг 3 «Разбивка на блоки»
  - `img_pr` — Шаг 5 «Промты картинок» (батч на все кадры разом)

Шаги `hero` (4) и `anim_pr` (7) собирают текст ПО-РАЗНОМУ для каждой
итерации (вариация героя / каждый кадр) — для них override текста на
проекте плохо подходит. Если будем расширять — нужен механизм с
плейсхолдерами `{{N}} / {{VOICEOVER}}` и подстановкой.
Шаги 6 «Картинки» и 8 «Видео» не шлют текст в GPT (генерация в outsee),
поэтому override для них тоже не нужен.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.prompt_library import (
    STEP_HUMAN_NAMES,
    get_project_prompt,
)

# Шаги, для которых поддерживается edit-override «сопр. сообщения».
SUPPORTED_STEPS: tuple[str, ...] = ("plan", "script", "split", "img_pr")


def is_supported(step_code: str) -> bool:
    return step_code in SUPPORTED_STEPS


# --------------------------------------------------------------------------- #
# Сборка дефолтного текста по шагам
# --------------------------------------------------------------------------- #

def _build_plan_default(project: Project, *, topic: str | None = None) -> str:
    """Шаг 1 «План» (xlsx-flow): к чату прикладывается project.xlsx.
    Возвращает «сопр. сообщение», которое уходит в GPT вместе с файлом.

    Сборка совпадает с `_run_plan_xlsx` в `app/telegram/bot.py`:
       Тема + содержимое мастер-промта + инструкция по xlsx.
    """
    master = get_project_prompt(project, "plan").strip()
    actual_topic = topic if topic is not None else (project.topic or "")
    return (
        f"Тема ролика: {actual_topic}\n\n"
        f"{master}\n\n"
        "Прикреплённый файл — текущий project.xlsx этого ролика. "
        "Заполни его согласно инструкции выше и пришли мне обратно как "
        ".xlsx (без обрезок и компрессии). Кратким текстом ответь — что "
        "сделал — но главное верни файл."
    )


def _build_script_default(project: Project, *, prompt_file_name: str = "prompt.txt") -> str:
    """Шаг 2 — закадровый текст. В xlsx-flow к чату прикладываются
    `prompt.txt` (мастер-промт + тема) и `project.xlsx`. Возвращаем
    «сопр. сообщение» — основной текст письма в чат.
    """
    topic = (project.topic or "").strip()
    return (
        f"Тема ролика: «{topic}».\n\n"
        f"Прикреплены 2 файла:\n"
        f"  1. {prompt_file_name} — инструкция, что именно делать.\n"
        f"  2. project.xlsx — рабочая таблица ролика (план, структура).\n\n"
        "Сделай всё, что написано в первом файле (инструкция), опираясь на "
        "второй (project.xlsx).\n\n"
        "Пришли результат обычным текстом в чат (можно с переносами "
        "строк). Без маркеров, без .txt-файлов в ответе (если всё же решишь "
        "ответить файлом — работает и этот fallback)."
    )


def _build_split_default(
    project: Project, *, prompt_file_name: str = "prompt.txt"
) -> str:
    """Шаг 3 — разбивка на блоки (xlsx-flow). К чату прикладываются
    `prompt.txt`, `project.xlsx`, `voiceover.txt`. Возвращаем chat_msg.
    """
    topic = (project.topic or "").strip()
    return (
        f"Тема ролика: «{topic}».\n\n"
        f"Прикреплены 3 файла:\n"
        f"  1. {prompt_file_name} — инструкция, что именно делать.\n"
        f"  2. project.xlsx — рабочая таблица ролика (план, структура).\n"
        f"  3. voiceover.txt — закадровый текст, который нужно разбить "
        f"на блоки.\n\n"
        "Сделай всё, что написано в первом файле (инструкция), опираясь "
        "на структуру из project.xlsx и применяя к voiceover.txt.\n\n"
        "Все результаты ЗАПИШИ В project.xlsx (в нужные листы и ячейки) и "
        "пришли мне обратно ОБНОВЛЁННЫЙ project.xlsx как .xlsx-файл "
        "(без обрезок и компрессии). Кратким текстом ответь — что сделал — "
        "но главное верни файл."
    )


def _build_img_pr_default(
    project: Project,
    *,
    voiceover_line: str = "",
    n_frames: int = 0,
) -> str:
    """Шаг 5 — промты картинок (один батч на все кадры).

    `voiceover_line` — закадровый текст по кадрам, склеенный знаком «-».
    `n_frames` — кол-во кадров. Когда вызывается из UI «получить файл
    с дефолтом», передаём фактические значения; если пусто — оставляем
    плейсхолдеры (юзер видит структуру).
    """
    image_master = get_project_prompt(project, "img_pr")
    hero_text = (project.hero_description or "").strip()
    descriptions = [d for d in (project.hero_descriptions or []) if d and d.strip()]
    if not hero_text and descriptions:
        hero_text = descriptions[0]
    hero_section = ""
    if hero_text:
        hero_section = (
            "\nЭталонное описание главного героя (использовать в кадрах "
            "где он появляется):\n"
            + hero_text
            + "\n"
        )

    n_frames_str = str(n_frames) if n_frames > 0 else "<N>"
    voiceover_str = voiceover_line if voiceover_line else "<закадровый текст по кадрам>"
    return (
        image_master.strip()
        + "\n\n"
        + hero_section
        + "\n---\n"
        + f"Кадров: {n_frames_str}.\n"
        + "Закадровый текст по кадрам (между блоками знак «-»):\n"
        + voiceover_str
        + "\n\n"
        + "Верни одним сообщением ровно "
        + n_frames_str
        + " промтов в том же порядке, разделяя их знаком «-». "
        + "Без нумерации, без пояснений, без заголовков. "
        + "Внутри самих промтов знак «-» не используй (если нужен дефис "
        + "— замени на пробел или подчёркивание)."
    )


# --------------------------------------------------------------------------- #
# Публичные функции
# --------------------------------------------------------------------------- #

def build_default_text(project: Project, step_code: str, **ctx) -> str:
    """Собирает дефолтный текст «сопр. сообщения» для шага.

    `**ctx` — контекстные данные, специфичные для шага (для img_pr —
    `voiceover_line`, `n_frames`; для script — `prompt_file_name`).
    """
    if step_code == "plan":
        return _build_plan_default(project, **ctx)
    if step_code == "script":
        return _build_script_default(project, **ctx)
    if step_code == "split":
        return _build_split_default(project, **ctx)
    if step_code == "img_pr":
        return _build_img_pr_default(project, **ctx)
    raise ValueError(f"build_default_text: шаг {step_code!r} не поддерживается")


def get_override(project: Project, step_code: str) -> str | None:
    """Возвращает пользовательский override-текст для шага или None."""
    overrides = getattr(project, "gpt_text_overrides", None) or {}
    val = overrides.get(step_code)
    if isinstance(val, str) and val.strip():
        return val
    return None


def has_override(project: Project, step_code: str) -> bool:
    return get_override(project, step_code) is not None


def get_effective_text(project: Project, step_code: str, **ctx) -> str:
    """Возвращает текст, который реально пойдёт в GPT.

    1. Если в `project.gpt_text_overrides[step_code]` есть непустой текст —
       вернёт его.
    2. Иначе соберёт дефолт из мастер-промта + контекста проекта.
    """
    override = get_override(project, step_code)
    if override is not None:
        return override
    return build_default_text(project, step_code, **ctx)


async def set_override(
    session: AsyncSession,
    project: Project,
    step_code: str,
    text: str,
) -> None:
    """Сохраняет пользовательский override-текст для шага.

    Пустая строка → удаляет override (см. `clear_override`).
    """
    if not text or not text.strip():
        await clear_override(session, project, step_code)
        return
    if not is_supported(step_code):
        raise ValueError(f"set_override: шаг {step_code!r} не поддерживается")
    overrides = dict(getattr(project, "gpt_text_overrides", None) or {})
    overrides[step_code] = text
    project.gpt_text_overrides = overrides
    await session.flush()


async def clear_override(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> None:
    """Удаляет пользовательский override для шага."""
    overrides = dict(getattr(project, "gpt_text_overrides", None) or {})
    if step_code in overrides:
        overrides.pop(step_code, None)
        project.gpt_text_overrides = overrides
        await session.flush()


def step_human_name(step_code: str) -> str:
    return STEP_HUMAN_NAMES.get(step_code, step_code)
