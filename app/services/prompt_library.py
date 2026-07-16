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

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.prompt_paths import (
    BUNDLED_PROMPTS_ROOT,
    PROMPTS_ROOT,
    ensure_user_prompts_root,
    first_existing_under_prompts,
    list_overlay_md_stems,
    overlay_exists,
    read_prompt_text,
    resolve_prompt_file,
    user_prompt_file,
    user_prompts_root,
    write_prompt_text,
)

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
    "excel_gpt":  "05_excel_gpt",
    # Папки оставлены с историческими номерами (05/07), чтобы не ломать
    # уже существующие промты в `prompts/`. Меню-нумерация шагов
    # переехала, но имя папки на диске не зависит от позиции в меню.
    "img_pr":     "05_image_prompts",
    "anim_pr":    "07_animation",
}

# Человеческое имя шага (для текстовых сообщений в TG).
STEP_HUMAN_NAMES: dict[str, str] = {
    "plan":       "1. Сценарий",
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
    "excel_gpt":  "Доп работа с Excel",
    "img_pr":     "6. Промты картинок",
    "anim_pr":    "8. Промты анимации",
    "music":      "10. Музыка",
    "audio":      "Озвучка",
}

# Шаги без мастер-промта — для красоты в списках и проверок.
STEPS_WITHOUT_PROMPT: set[str] = {"img", "video", "audio", "assemble"}

DEFAULT_NAME = "default"
_FILE_META = ".file_meta.json"

# Слоты enrich — только .md из prompts/05*_enrich_*; не blocks v2 compose.
ENRICH_STEP_CODES: frozenset[str] = frozenset(
    {*(f"enrich_{i}" for i in range(1, 6)), "excel_gpt"}
)

EXCEL_GPT_UNIFIED_STEP = "excel_gpt"


def is_excel_gpt_prompt_step(step_code: str) -> bool:
    return step_code in ENRICH_STEP_CODES


def excel_gpt_source_steps() -> tuple[str, ...]:
    return (EXCEL_GPT_UNIFIED_STEP, *(f"enrich_{i}" for i in range(1, 6)))


def excel_gpt_prompt_exists(name: str) -> bool:
    clean = _clean_variant_name(name) if name else ""
    if not clean:
        return False
    for code in excel_gpt_source_steps():
        folder = STEP_FOLDERS.get(code)
        if folder and overlay_exists(folder, f"{clean}.md"):
            return True
    return False


def resolve_excel_gpt_prompt_path(name: str) -> Path:
    """Читать из overlay (user → bundled); excel_gpt или legacy enrich_*."""
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    if not clean:
        raise ValueError(f"некорректное имя промта: {name!r}")
    folder = STEP_FOLDERS[EXCEL_GPT_UNIFIED_STEP]
    found = resolve_prompt_file(folder, f"{clean}.md")
    if found is not None:
        return found
    for code in (f"enrich_{i}" for i in range(1, 6)):
        leg_folder = STEP_FOLDERS[code]
        found = resolve_prompt_file(leg_folder, f"{clean}.md")
        if found is not None:
            return found
    return user_prompt_file(folder, f"{clean}.md")

# Макс. длина имени варианта на диске (UTF-8 байты). Раньше было 40 из‑за TG callback_data;
# в веб-студии нужны длинные осмысленные имена файлов.
MAX_PROMPT_NAME_BYTES = 255

# Для inline-кнопок Telegram (callback_data ≤ 64 байта с префиксом).
TG_CALLBACK_PROMPT_NAME_BYTES = 40

# Запрещённые символы в имени файла (path traversal / fs-unsafe).
_UNSAFE_CHARS_RE = re.compile(r'[/\\:\*\?"<>|\x00]')


def _truncate_utf8(name: str, max_bytes: int) -> str:
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _sanitize_name(raw: str, *, max_bytes: int = MAX_PROMPT_NAME_BYTES) -> str:
    """Убирает из строки символы, опасные для файловой системы.
    Пробелы, кириллица, цифры, `_`, `-` — остаются."""
    name = _UNSAFE_CHARS_RE.sub("_", raw).strip().strip(".")
    name = re.sub(r"_{2,}", "_", name)
    if not name:
        return ""
    return _truncate_utf8(name, max_bytes)


def step_folder_name(step_code: str) -> str | None:
    """Имя папки в `prompts/` для данного шага (или None если без промта)."""
    return STEP_FOLDERS.get(step_code)


def step_dir(step_code: str) -> Path:
    """Папка промтов шага в data/prompts/ (запись пользователя)."""
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        raise ValueError(f"step_code {step_code!r} не имеет мастер-промта")
    path = user_prompts_root() / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_meta_path(step_code: str) -> Path:
    return step_dir(step_code) / _FILE_META


def load_file_meta(step_code: str) -> dict[str, Any]:
    path = _file_meta_path(step_code)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_file_meta(step_code: str, data: dict[str, Any]) -> None:
    path = _file_meta_path(step_code)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def touch_prompt_meta(step_code: str, name: str, size: int) -> float:
    """Записать стабильную дату сохранения (не mtime файла)."""
    saved_at = datetime.now(timezone.utc).timestamp()
    touch_prompt_meta_at(step_code, name, saved_at, size)
    return saved_at


def touch_prompt_meta_at(step_code: str, name: str, saved_at: float, size: int) -> None:
    meta = load_file_meta(step_code)
    meta[name] = {"saved_at": saved_at, "size": size}
    _save_file_meta(step_code, meta)


def get_prompt_saved_at(step_code: str, name: str) -> float | None:
    entry = load_file_meta(step_code).get(name)
    if isinstance(entry, dict) and entry.get("saved_at") is not None:
        return float(entry["saved_at"])
    return None


def rename_prompt_meta(step_code: str, old_name: str, new_name: str) -> None:
    meta = load_file_meta(step_code)
    if old_name in meta:
        meta[new_name] = meta.pop(old_name)
        _save_file_meta(step_code, meta)


def remove_prompt_meta(step_code: str, name: str) -> None:
    meta = load_file_meta(step_code)
    if name in meta:
        meta.pop(name, None)
        _save_file_meta(step_code, meta)


def is_valid_prompt_name(name: str, *, max_bytes: int = MAX_PROMPT_NAME_BYTES) -> bool:
    """Имя варианта: любые символы кроме path-traversal.
    Пробелы, кириллица, спецсимволы — допустимы."""
    if not name or not name.strip():
        return False
    if len(name.encode("utf-8")) > max_bytes:
        return False
    if ".." in name:
        return False
    return not _UNSAFE_CHARS_RE.search(name)


def prompt_name_fits_telegram_callback(name: str) -> bool:
    """Имя влезает в callback_data inline-кнопки Telegram."""
    return is_valid_prompt_name(name, max_bytes=TG_CALLBACK_PROMPT_NAME_BYTES)


def list_prompts(step_code: str) -> list[str]:
    """Список доступных вариантов (имена файлов без `.md`), отсортированный.
    `default` всегда идёт первым (если присутствует)."""
    if is_excel_gpt_prompt_step(step_code):
        return list_excel_gpt_prompts()
    return _list_prompts_in_dir(step_code)


def list_excel_gpt_prompts() -> list[str]:
    """Все .md для «Работа с GPT» — единый список из 05_excel_gpt + legacy enrich_*."""
    merged: dict[str, None] = {}
    for code in excel_gpt_source_steps():
        for n in _list_prompts_in_dir(code):
            merged.setdefault(n, None)
    names = sorted(merged.keys())
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def _list_prompts_in_dir(step_code: str) -> list[str]:
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        return []
    names = list_overlay_md_stems(folder)
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def prompt_path(step_code: str, name: str) -> Path:
    """Путь к .md для чтения (overlay) или будущей записи (user)."""
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    if not clean:
        raise ValueError(f"некорректное имя промта: {name!r}")
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        raise ValueError(f"step_code {step_code!r} не имеет мастер-промта")
    found = resolve_prompt_file(folder, f"{clean}.md")
    if found is not None:
        return found
    return user_prompt_file(folder, f"{clean}.md")


def read_prompt(step_code: str, name: str) -> str:
    if is_excel_gpt_prompt_step(step_code):
        p = resolve_excel_gpt_prompt_path(name)
        if not p.is_file():
            raise FileNotFoundError(f"prompt file not found: {p}")
        return p.read_text(encoding="utf-8")
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        raise ValueError(f"step_code {step_code!r} не имеет мастер-промта")
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    return read_prompt_text(folder, f"{clean}.md")


def write_prompt(step_code: str, name: str, content: str) -> Path:
    if is_excel_gpt_prompt_step(step_code):
        step_code = EXCEL_GPT_UNIFIED_STEP
    folder = STEP_FOLDERS[step_code]
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    p = write_prompt_text(folder, f"{clean}.md", content=content)
    touch_prompt_meta(step_code, name, len(content.encode("utf-8")))
    return p


def delete_prompt(step_code: str, name: str) -> bool:
    """Удалить файл варианта. `default` удалять нельзя.
    Возвращает True если файл был удалён."""
    if name == DEFAULT_NAME:
        raise ValueError("default удалять нельзя")
    if is_excel_gpt_prompt_step(step_code):
        p = resolve_excel_gpt_prompt_path(name)
        if not p.is_file():
            return False
        p.unlink()
        return True
    p = prompt_path(step_code, name)
    if not p.exists():
        return False
    p.unlink()
    return True


def _clean_variant_name(raw: str) -> str:
    """Имя .md без расширения, безопасное для `prompt_path`."""
    if not raw or not str(raw).strip():
        return ""
    name = str(raw).strip()
    if not is_valid_prompt_name(name):
        name = _sanitize_name(name)
    return name if name else ""


def _variant_from_studio_meta(meta: dict | None, step_code: str) -> str | None:
    """Вариант из Node Studio: `meta.prompt_slot_variants[node][slot]`.

    Зеркало `web/src/lib/prompt-slot-storage.ts` → `activeVariantForSlot`:
    сначала слот `main`, иначе любой слот с существующим файлом для шага.
    """
    if not meta or step_code not in STEP_FOLDERS:
        return None
    slot_variants = meta.get("prompt_slot_variants")
    if not isinstance(slot_variants, dict):
        return None
    found_other: str | None = None
    for slots in slot_variants.values():
        if not isinstance(slots, dict):
            continue
        for slot_id, variant in slots.items():
            clean = _clean_variant_name(str(variant or ""))
            if not clean:
                continue
            exists = (
                excel_gpt_prompt_exists(clean)
                if is_excel_gpt_prompt_step(step_code)
                else overlay_exists(
                    STEP_FOLDERS[step_code], f"{clean}.md"
                )
            )
            if not exists:
                continue
            if slot_id == "main":
                return clean
            if found_other is None:
                found_other = clean
    return found_other


def resolve_project_prompt_name(
    overrides: dict | None,
    step_code: str,
    *,
    meta: dict | None = None,
) -> str:
    """Какой вариант .md использовать для шага."""
    return resolve_project_prompt_with_source(overrides, step_code, meta=meta)[0]


PROMPT_SOURCE_LABELS: dict[str, str] = {
    "slot": "слот ноды",
    "preferred": "слот ноды",
    "override": "оверрайд проекта",
    "global": "глобально активный",
    "default": "default",
}


def resolve_project_prompt_with_source(
    overrides: dict | None,
    step_code: str,
    *,
    meta: dict | None = None,
    node_key: str | None = None,
    slot_id: str | None = None,
) -> tuple[str, str]:
    """(имя варианта, источник: slot|preferred|override|global|default)."""
    overrides = overrides or {}

    if node_key and slot_id:
        slot_variants = (meta or {}).get("prompt_slot_variants")
        if isinstance(slot_variants, dict):
            node_slots = slot_variants.get(node_key)
            if isinstance(node_slots, dict):
                bound = _clean_variant_name(str(node_slots.get(slot_id) or ""))
                if bound:
                    exists = (
                        excel_gpt_prompt_exists(bound)
                        if is_excel_gpt_prompt_step(step_code)
                        else overlay_exists(STEP_FOLDERS[step_code], f"{bound}.md")
                    )
                    if exists:
                        return bound, "slot"
        if slot_id and slot_id != "main":
            preferred = _clean_variant_name(slot_id)
            if preferred:
                exists = (
                    excel_gpt_prompt_exists(preferred)
                    if is_excel_gpt_prompt_step(step_code)
                    else overlay_exists(STEP_FOLDERS[step_code], f"{preferred}.md")
                )
                if exists:
                    return preferred, "preferred"

    if is_excel_gpt_prompt_step(step_code):
        for key in excel_gpt_source_steps():
            chosen = overrides.get(key)
            if chosen:
                clean = _clean_variant_name(str(chosen))
                if clean and excel_gpt_prompt_exists(clean):
                    return clean, "override"
        from app.services.prompt_active_global import get_global_active

        global_name = get_global_active(EXCEL_GPT_UNIFIED_STEP)
        if global_name and excel_gpt_prompt_exists(global_name):
            return global_name, "global"
        for key in excel_gpt_source_steps():
            from_meta = _variant_from_studio_meta(meta, key)
            if from_meta:
                return from_meta, "slot"
        return DEFAULT_NAME, "default"

    chosen = overrides.get(step_code)
    if chosen:
        clean = _clean_variant_name(str(chosen))
        if clean and overlay_exists(STEP_FOLDERS[step_code], f"{clean}.md"):
            return clean, "override"

    from app.services.prompt_active_global import get_global_active

    global_name = get_global_active(step_code)
    if global_name:
        return global_name, "global"

    from_meta = _variant_from_studio_meta(meta, step_code)
    if from_meta:
        return from_meta, "slot"

    return DEFAULT_NAME, "default"


def read_resolved_project_prompt(
    project, step_code: str, *, node_key: str | None = None, slot_id: str | None = None
) -> tuple[str, Path, str, str]:
    """(имя варианта, путь к .md, текст, источник) — единая точка для шагов и логов."""
    overrides = getattr(project, "prompt_overrides", None) or {}
    meta = getattr(project, "meta", None) or {}
    name, source = resolve_project_prompt_with_source(
        overrides, step_code, meta=meta, node_key=node_key, slot_id=slot_id
    )
    path = prompt_path(step_code, name)
    text = read_prompt(step_code, name)
    from app.services.gpt_text_builder import inject_topic_placeholders

    topic = str(getattr(project, "topic", None) or "")
    return name, path, inject_topic_placeholders(text, topic), source


def get_project_prompt(project, step_code: str) -> str:
    """Прочитать выбранный для проекта мастер-промт с диска.

    Проект приводится к dict-like через `getattr(project, "prompt_overrides", {})`
    — так удобно работать и со SQLAlchemy-моделью, и с обычным dict.

    Если в `prompt_overrides` включена компонентная сборка (blocks / use_blocks_v2)
    и для шага есть template в `prompts/steps/` — собираем из блоков.
    """
    overrides = getattr(project, "prompt_overrides", None) or {}
    from app.services.prompt_composer import (
        STEP_CODE_TO_COMPOSE,
        compose_step,
        merge_project_prompt_config,
        project_uses_blocks_v2,
    )

    # enrich_* всегда из выбранного .md в Studio — не из blocks/steps template.
    if step_code not in ENRICH_STEP_CODES and project_uses_blocks_v2(overrides):
        step_id = STEP_CODE_TO_COMPOSE.get(step_code)
        if step_id:
            blocks, vars_ = merge_project_prompt_config(
                overrides,
                hero_description=(
                    (getattr(project, "hero_descriptions", None) or [None])[0]
                    if isinstance(getattr(project, "hero_descriptions", None), list)
                    else None
                ),
                topic=getattr(project, "topic", None),
            )
            try:
                composed = compose_step(step_id, blocks, vars_)
                from app.services.gpt_text_builder import inject_topic_placeholders

                return inject_topic_placeholders(composed, actual_topic)
            except FileNotFoundError:
                pass

    name, path, text, source = read_resolved_project_prompt(project, step_code)
    logger.info(
        "get_project_prompt: step={} variant={!r} source={} path={}",
        step_code,
        name,
        source,
        path,
    )
    return text


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
