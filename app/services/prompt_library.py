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
    "plan":       "01_plan",
    "script":     "02_script",
    "split":      "03_razbivka",
    "hero":       "04_hero",
    # `hero_style` — НЕ отдельная кнопка в меню; это вспомогательная
    # библиотека стилей для шага «4. Hero». Бот сам показывает picker
    # перед запуском Hero-генерации, выбор сохраняется в
    # project.prompt_overrides["hero_style"]. Используем общую
    # инфраструктуру библиотеки промтов (prompt_picker, on_prompt_picker_cb).
    "hero_style": "04_hero_style",
    # 4b. «Предметы» — генерация реф-картинок предметов.
    "items":      "04b_items",
    # Слоты «Доп работа с EXCEL» (xlsx round-trip с ChatGPT) — каждый
    # слот имеет свою папку, чтобы юзер мог хранить разные промты.
    "enrich_1":   "05a_enrich_1",
    "enrich_2":   "05b_enrich_2",
    "enrich_3":   "05c_enrich_3",
    "enrich_4":   "05d_enrich_4",
    "enrich_5":   "05e_enrich_5",
    # Папки оставлены с историческими номерами (05/07), чтобы не ломать
    # уже существующие промты в `prompts/`. Меню-нумерация шагов
    # переехала, но имя папки на диске не зависит от позиции в меню.
    "img_pr":     "05_image_prompts",
    "anim_pr":    "07_animation",
}

# Человеческое имя шага (для текстовых сообщений в TG).
STEP_HUMAN_NAMES: dict[str, str] = {
    "plan":       "1. План",
    "script":     "2. Закадровый текст",
    "split":      "3. Разбивка на блоки",
    "hero":       "4. Персонажи (Объекты)",
    "hero_style": "4. Hero — стиль персонажа",
    "items":      "4. Предметы (Объекты)",
    # Все слоты — суб-шаги одного wrapper-шага «5. Доп работа с EXCEL»,
    # поэтому в названии номер шага не указываем (он зависит от
    # n_slots, и для UX-промтов важен номер слота, а не позиция в меню).
    "enrich_1":   "Доп работа с EXCEL #1",
    "enrich_2":   "Доп работа с EXCEL #2",
    "enrich_3":   "Доп работа с EXCEL #3",
    "enrich_4":   "Доп работа с EXCEL #4",
    "enrich_5":   "Доп работа с EXCEL #5",
    "img_pr":     "6. Промты картинок",
    "anim_pr":    "8. Промты анимации",
}

# Шаги без мастер-промта — для красоты в списках и проверок.
STEPS_WITHOUT_PROMPT: set[str] = {"img", "video", "audio", "assemble"}

DEFAULT_NAME = "default"

# Запрещённые символы в имени файла (path traversal / fs-unsafe).
_UNSAFE_CHARS_RE = re.compile(r'[/\\:\*\?"<>|\x00]')


def _sanitize_name(raw: str) -> str:
    """Убирает из строки символы, опасные для файловой системы.
    Пробелы, кириллица, цифры, `_`, `-` — остаются.
    Усекает результат до 40 байт UTF-8."""
    name = _UNSAFE_CHARS_RE.sub("_", raw).strip().strip(".")
    # Схлопываем подряд идущие подчёркивания
    name = re.sub(r"_{2,}", "_", name)
    if not name:
        return ""
    # Усекаем по байтам UTF-8 без обрезания посередине символа
    encoded = name.encode("utf-8")
    if len(encoded) <= 40:
        return name
    truncated = encoded[:40].decode("utf-8", errors="ignore")
    return truncated


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
    """Имя варианта: любые символы кроме path-traversal.
    Пробелы, кириллица, спецсимволы — допустимы.

    Лимит UTF-8: 40 байт (это ~20 кириллических симв, ~40 латинских),
    чтобы имя гарантированно влезало в Telegram callback_data
    (макс 64 байта; префиксы вида `prm:<pid>:<step>:sel:<name>` уже
    занимают ~24 байта)."""
    if not name or not name.strip():
        return False
    if len(name.encode("utf-8")) > 40:
        return False
    if ".." in name:
        return False
    return not _UNSAFE_CHARS_RE.search(name)


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
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    if not clean:
        raise ValueError(f"некорректное имя промта: {name!r}")
    return step_dir(step_code) / f"{clean}.md"


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


def _batch_aware_variant_exists(
    step_code: str, name: str, *, batch_slug: str | None
) -> bool:
    """Существует ли файл варианта для шага, с учётом batch-snapshot и
    mass-global. Для одиночных (batch_slug=None) — только глобальный.
    """
    if prompt_path(step_code, name).exists():
        return True
    if not batch_slug:
        return False
    # Lazy import чтобы избежать циклов.
    from app.services import mass_prompts as _mp
    folder = STEP_FOLDERS.get(step_code, "")
    for src in (
        _mp.batch_snapshot_dir(batch_slug),
        _mp.mass_global_prompts_dir(),
    ):
        if (src / folder / f"{name}.md").exists():
            return True
    return False


def resolve_project_prompt_name(
    overrides: dict | None,
    step_code: str,
    *,
    batch_slug: str | None = None,
) -> str:
    """Какой вариант выбран в проекте для шага. Если override не задан или
    указанного файла нет (с учётом batch-tiers) — возвращаем `default`.
    """
    overrides = overrides or {}
    chosen = overrides.get(step_code)
    if not chosen:
        return DEFAULT_NAME
    clean = _sanitize_name(chosen) if not is_valid_prompt_name(chosen) else chosen
    if not clean:
        return DEFAULT_NAME
    if not _batch_aware_variant_exists(step_code, clean, batch_slug=batch_slug):
        return DEFAULT_NAME
    return clean


def get_project_prompt(project, step_code: str) -> str:
    """Прочитать выбранный для проекта мастер-промт с диска.

    Для batch-проектов (`project.batch_slug` задан): читает с учётом
    приоритетов snapshot > mass-global > global single. Одиночные —
    только из global single.
    """
    overrides = getattr(project, "prompt_overrides", None) or {}
    batch_slug = getattr(project, "batch_slug", None)
    name = resolve_project_prompt_name(
        overrides, step_code, batch_slug=batch_slug,
    )
    if batch_slug:
        from app.services import mass_prompts as _mp
        try:
            return _mp.read_variant_for_batch(batch_slug, step_code, name)
        except FileNotFoundError:
            pass
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
