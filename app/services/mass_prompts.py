"""Управление мастер-промтами и «сопр. сообщениями» на уровне массовой
генерации.

Архитектура три-уровневого хранилища:

  1. **Global single** — `prompts/<step_folder>/<name>.md`
     Используется одиночными проектами (single). Это базовый default.

  2. **Global mass** — `data/mass_template_prompts/<step_folder>/<name>.md`
     Поверх (1) для новых массовых проектов. При создании нового батча
     эти файлы наслаиваются ПОВЕРХ snapshot'а, в результате будущие
     батчи получают эти промты как default. Одиночные проекты этот
     слой НЕ видят.

  3. **Local batch** — `data/batches/<slug>/prompts/<step_folder>/<name>.md`
     Snapshot конкретного батча — приоритетен при чтении в sub'ах
     этого батча. Изменения тут влияют ТОЛЬКО на текущий батч.

Чтение в sub-проектах: snapshot (3) > global mass (2) > global single (1).
Чтение в одиночных: только (1).

Параллельно есть «сопр. сообщения» (gpt_text_overrides). Тоже три уровня:

  - **Local batch**: `batch.settings_snapshot["gpt_text_overrides"][step]`
    (наследуется в `Project.gpt_text_overrides` при создании sub'ов).
  - **Global mass**: `data/mass_template_text_overrides/<step>.md`
    (читается при создании нового батча → попадает в snapshot).
  - **Default**: формируется из мастер-промта + контекста проекта.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from app.services.prompt_library import (
    DEFAULT_NAME,
    PROMPTS_ROOT,
    STEP_FOLDERS,
    is_valid_prompt_name,
)
from app.settings import settings

# === Каталоги ===


def _data_dir() -> Path:
    return Path(settings.data_dir)


def mass_global_prompts_dir() -> Path:
    """Глобальный mass-уровень: применяется ко всем новым батчам."""
    return _data_dir() / "mass_template_prompts"


def mass_global_text_overrides_dir() -> Path:
    """Глобальный mass-уровень для «сопр. сообщений»."""
    return _data_dir() / "mass_template_text_overrides"


def batch_snapshot_dir(batch_slug: str) -> Path:
    """Локальный snapshot конкретного батча (на диске)."""
    return _data_dir() / "batches" / batch_slug / "prompts"


def _step_dir_in(parent: Path, step_code: str) -> Path:
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        raise ValueError(f"step_code {step_code!r} не имеет мастер-промта")
    p = parent / folder
    p.mkdir(parents=True, exist_ok=True)
    return p


def _prompt_file_in(parent: Path, step_code: str, name: str) -> Path:
    if not is_valid_prompt_name(name):
        raise ValueError(f"некорректное имя промта: {name!r}")
    return _step_dir_in(parent, step_code) / f"{name}.md"


# === Прочтение/листинг ===


def list_variants_for_batch(batch_slug: str, step_code: str) -> list[str]:
    """Имена доступных вариантов для батча (union snapshot + mass-global +
    global). Возвращается отсортированный список, `default` всегда первым.
    """
    seen: set[str] = set()
    snap = batch_snapshot_dir(batch_slug)
    mass_global = mass_global_prompts_dir()
    for src in (snap, mass_global, PROMPTS_ROOT):
        d = src / STEP_FOLDERS.get(step_code, "")
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            seen.add(p.stem)
    names = sorted(seen)
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def read_variant_for_batch(
    batch_slug: str, step_code: str, name: str
) -> str:
    """Читает вариант с учётом приоритетов: snapshot > mass-global > global."""
    for src in (
        batch_snapshot_dir(batch_slug),
        mass_global_prompts_dir(),
        PROMPTS_ROOT,
    ):
        p = src / STEP_FOLDERS.get(step_code, "") / f"{name}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"prompt file not found for batch {batch_slug}, step {step_code}, name {name}"
    )


def read_variant_global(step_code: str, name: str) -> str | None:
    """Читает вариант из mass-global уровня (если есть)."""
    p = _prompt_file_in(mass_global_prompts_dir(), step_code, name)
    return p.read_text(encoding="utf-8") if p.exists() else None


# === Запись ===


def write_variant_local(
    batch_slug: str, step_code: str, name: str, content: str
) -> Path:
    """Сохраняет вариант в snapshot конкретного батча."""
    p = _prompt_file_in(batch_snapshot_dir(batch_slug), step_code, name)
    p.write_text(content, encoding="utf-8")
    logger.info(
        "mass_prompts: local write batch={} step={} name={} ({} симв)",
        batch_slug, step_code, name, len(content),
    )
    return p


def write_variant_global(
    step_code: str, name: str, content: str,
    *, also_write_to_batch_slug: str | None = None,
) -> Path:
    """Сохраняет вариант в mass-global уровень.

    Если задан `also_write_to_batch_slug` — дополнительно копирует в
    snapshot текущего батча (чтобы изменение применилось сразу).
    """
    p = _prompt_file_in(mass_global_prompts_dir(), step_code, name)
    p.write_text(content, encoding="utf-8")
    logger.info(
        "mass_prompts: global write step={} name={} ({} симв)",
        step_code, name, len(content),
    )
    if also_write_to_batch_slug:
        write_variant_local(also_write_to_batch_slug, step_code, name, content)
    return p


def delete_variant_local(
    batch_slug: str, step_code: str, name: str
) -> bool:
    """Удаляет вариант из snapshot батча. `default` удалять нельзя."""
    if name == DEFAULT_NAME:
        raise ValueError("default удалять нельзя")
    p = _prompt_file_in(batch_snapshot_dir(batch_slug), step_code, name)
    if p.exists():
        p.unlink()
        return True
    return False


def delete_variant_global(step_code: str, name: str) -> bool:
    """Удаляет вариант из mass-global уровня. `default` удалять нельзя."""
    if name == DEFAULT_NAME:
        raise ValueError("default удалять нельзя")
    p = _prompt_file_in(mass_global_prompts_dir(), step_code, name)
    if p.exists():
        p.unlink()
        return True
    return False


# === Снапшот при создании батча ===


def overlay_mass_global_into_snapshot(snapshot_dir: Path) -> None:
    """При создании батча: после копирования из `prompts/` (через
    `_copy_prompts_snapshot`), вызываем эту функцию чтобы наслоить mass-
    global поверх snapshot'а. Файлы из `data/mass_template_prompts/`
    перезаписывают одноимённые в snapshot'е.
    """
    src = mass_global_prompts_dir()
    if not src.exists():
        return
    for step_folder in src.iterdir():
        if not step_folder.is_dir():
            continue
        dst_folder = snapshot_dir / step_folder.name
        dst_folder.mkdir(parents=True, exist_ok=True)
        for f in step_folder.glob("*.md"):
            shutil.copy2(f, dst_folder / f.name)
            logger.info(
                "mass_prompts: overlay {} → {}",
                f, dst_folder / f.name,
            )


# === Сопр. сообщения (gpt_text_overrides) ===


def read_text_override_global(step_code: str) -> str | None:
    """Читает «сопр. сообщение» из mass-global уровня (если есть)."""
    p = mass_global_text_overrides_dir() / f"{step_code}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def write_text_override_global(step_code: str, content: str) -> Path:
    """Сохраняет «сопр. сообщение» в mass-global. Будет наследоваться
    новыми батчами.
    """
    d = mass_global_text_overrides_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{step_code}.md"
    p.write_text(content, encoding="utf-8")
    logger.info(
        "mass_prompts: text-global write step={} ({} симв)",
        step_code, len(content),
    )
    return p


def delete_text_override_global(step_code: str) -> bool:
    p = mass_global_text_overrides_dir() / f"{step_code}.md"
    if p.exists():
        p.unlink()
        return True
    return False
