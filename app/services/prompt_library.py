"""Файловая библиотека мастер-промтов по этапам пайплайна.

Структура на диске:
  prompts/
    01_plan/        → шаг 1 «План» (PLAN_SHORTS)
    02_script/      → шаг 2 «Закадровый текст» (SCRIPT_SHORTS)
    03_razbivka/    → шаг 3 «Разбивка на блоки» (RAZBIVKA_SLOV)
    04_hero/        → шаг 4 «Hero» (HERO_SHORTS)
    05_image_prompts/ → шаг 5 «Промты картинок» (IMAGE_SHORTS)
    07_animation/   → шаг 7 «Промты анимации» (VIDEO_SHORTS)

В каждой папке лежит `default.md` (дефолтный мастер-промт) + любые
дополнительные `<имя>.md` файлы — это варианты, между которыми проект
может переключаться. Имя файла без расширения = имя варианта.

В `Project.prompt_overrides` (JSON) сохраняется выбор юзера:
  {"plan": "horror_v2", "script": "default", ...}

Если override не указан или файл по нему не найден — берётся `default.md`.
Если и `default.md` нет — RuntimeError.

Также модуль безопасно валидирует имена вариантов (no path traversal,
ASCII + цифры + `_-`), чтобы юзер из TG не мог записать файл вне папки.
"""

from __future__ import annotations

import re
from pathlib import Path

# Корень папки `prompts/` — два уровня вверх от текущего файла:
# app/services/prompt_library.py  →  ../../prompts/
PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent / "prompts"

# Карта step_code (как в menu.py StepDef.code) → имя папки в `prompts/`.
# Шаги, у которых нет мастер-промта, тут не перечисляются.
# Ключи совпадают с `StepDef.code` в `app/telegram/menu.py`.
STEP_FOLDERS: dict[str, str] = {
    "plan":    "01_plan",
    "script":  "02_script",
    "split":   "03_razbivka",
    "hero":    "04_hero",
    "img_pr":  "05_image_prompts",
    "anim_pr": "07_animation",
}

# Человеческое имя шага (для текстовых сообщений в TG).
STEP_HUMAN_NAMES: dict[str, str] = {
    "plan":    "1. План",
    "script":  "2. Закадровый текст",
    "split":   "3. Разбивка на блоки",
    "hero":    "4. Hero",
    "img_pr":  "5. Промты картинок",
    "anim_pr": "7. Промты анимации",
}

# Шаги без мастер-промта — для красоты в списках и проверок.
STEPS_WITHOUT_PROMPT: set[str] = {"img", "video", "audio", "assemble"}

DEFAULT_NAME = "default"

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def step_folder_name(step_code: str) -> str | None:
    """Имя папки в `prompts/` для данного шага (или None если без промта)."""
    return STEP_FOLDERS.get(step_code)


def step_dir(step_code: str) -> Path:
    """Абсолютный путь к папке промтов для шага. Создаёт её при отсутствии."""
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        raise ValueError(f"step_code {step_code!r} не имеет мастер-промта")
    path = PROMPTS_ROOT / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_valid_prompt_name(name: str) -> bool:
    """Имя варианта: только латиница/цифры/_/- длиной 1..64."""
    return bool(name) and bool(_NAME_RE.match(name))


def list_prompts(step_code: str) -> list[str]:
    """Список доступных вариантов (имена файлов без `.md`), отсортированный.
    `default` всегда идёт первым (если присутствует)."""
    d = step_dir(step_code)
    names = sorted(p.stem for p in d.glob("*.md"))
    # default — всегда первым.
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def prompt_path(step_code: str, name: str) -> Path:
    """Путь к файлу `<step_dir>/<name>.md`. Не проверяет существование."""
    if not is_valid_prompt_name(name):
        raise ValueError(f"некорректное имя промта: {name!r}")
    return step_dir(step_code) / f"{name}.md"


def read_prompt(step_code: str, name: str) -> str:
    p = prompt_path(step_code, name)
    if not p.exists():
        raise FileNotFoundError(f"prompt file not found: {p}")
    return p.read_text(encoding="utf-8")


def write_prompt(step_code: str, name: str, content: str) -> Path:
    p = prompt_path(step_code, name)
    p.write_text(content, encoding="utf-8")
    return p


def delete_prompt(step_code: str, name: str) -> bool:
    """Удалить файл варианта. `default` удалять нельзя.
    Возвращает True если файл был удалён."""
    if name == DEFAULT_NAME:
        raise ValueError("default удалять нельзя")
    p = prompt_path(step_code, name)
    if not p.exists():
        return False
    p.unlink()
    return True


def resolve_project_prompt_name(
    overrides: dict | None, step_code: str
) -> str:
    """Какой вариант выбран в проекте для шага. Если override не задан или
    указанного файла нет — возвращаем `default`."""
    overrides = overrides or {}
    chosen = overrides.get(step_code)
    if not chosen:
        return DEFAULT_NAME
    if not is_valid_prompt_name(chosen):
        return DEFAULT_NAME
    if not prompt_path(step_code, chosen).exists():
        return DEFAULT_NAME
    return chosen


def get_project_prompt(project, step_code: str) -> str:
    """Прочитать выбранный для проекта мастер-промт с диска.

    Проект приводится к dict-like через `getattr(project, "prompt_overrides", {})`
    — так удобно работать и со SQLAlchemy-моделью, и с обычным dict.
    """
    overrides = getattr(project, "prompt_overrides", None) or {}
    name = resolve_project_prompt_name(overrides, step_code)
    return read_prompt(step_code, name)


def make_template_for_new(step_code: str, name: str) -> str:
    """Стартовый шаблон для нового файла, чтобы юзеру было что заполнять."""
    folder = STEP_FOLDERS.get(step_code, "?")
    return (
        f"# Master-prompt для шага «{step_code}» (вариант: {name})\n"
        f"# Файл: prompts/{folder}/{name}.md\n"
        "#\n"
        "# Замени этот текст на свой мастер-промт целиком.\n"
        "# Можно использовать markdown — он уйдёт в ChatGPT как обычный текст.\n"
        "# Бот добавит технический блок (генератор/aspect/2K) и контекст\n"
        "# (план/сценарий/закадровый кадр/etc.) сам перед отправкой.\n"
        "\n"
        "Опиши задачу для модели здесь...\n"
    )
