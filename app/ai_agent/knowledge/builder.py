"""Build project_context.md из живых источников: AGENTS.md, HANDOVER.md,
схема БД (app/models.py через SQLAlchemy), список ключевых модулей.

Этот файл — first system message в каждой сессии (3-5k токенов).
"""

from __future__ import annotations

from pathlib import Path


def _safe_read(p: Path, max_lines: int = 1000) -> str:
    if not p.exists() or not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    lines = text.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n...[truncated, {len(lines) - max_lines} more lines]"
    return text


def _build_db_schema_section() -> str:
    """Авто-извлечение схемы БД из app/models.py через SQLAlchemy reflection."""
    try:
        from app.models import Base  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return f"DB schema: <не удалось импортировать app.models: {e}>"

    parts = ["## DB schema (auto-extracted from app/models.py)\n"]
    for table in Base.metadata.sorted_tables:
        cols = []
        for c in table.columns:
            col_type = str(c.type).split("(")[0]
            pk = " PK" if c.primary_key else ""
            nullable = "" if c.nullable else " NOT NULL"
            cols.append(f"{c.name}:{col_type}{pk}{nullable}")
        parts.append(f"- **{table.name}** ({', '.join(cols)})")
    return "\n".join(parts)


def _build_key_files_section(repo_root: Path) -> str:
    """Перечень критичных файлов с одной строкой описания."""
    key_files = [
        ("AGENTS.md", "Правила игры для всех ИИ-агентов (читай первой!)"),
        ("HANDOVER.md", "Живой контекст текущей разработки"),
        ("README.md", "Краткое описание проекта"),
        ("HOW_TO_RUN.md", "Запуск на машине пользователя"),
        ("app/main.py", "Worker loop + Telegram polling"),
        ("app/models.py", "SQLAlchemy 2 модели — источник правды по схеме БД"),
        ("app/settings.py", "Pydantic settings из .env"),
        ("app/orchestrator/pipeline.py", "State machine конвейера видео"),
        ("app/orchestrator/steps/", "11 шагов пайплайна (plan, script, frames, hero, items, enrich, image_prompts, images, animation, videos, audio, assemble)"),
        ("app/telegram/bot.py", "Главный TG-бот (~8k строк, в процессе разбиения)"),
        ("app/telegram/menu.py", "Основное меню"),
        ("app/telegram/mass_menu.py", "Меню массовой генерации"),
        ("app/bots/outsee.py", "Playwright-автоматизация outsee.io (nano-banana + veo)"),
        ("app/bots/chatgpt.py", "Playwright-автоматизация web ChatGPT"),
        ("app/bots/elevenlabs.py", "Playwright-автоматизация 11Labs (с Dolphin Anty)"),
        ("app/orchestrator_api.py", "FastAPI на 127.0.0.1:8787 для управления конвейером"),
        ("app/services/visual_lab/", "Итеративный анализатор пиксель-арта (GPT-Vision + Nano Banana Pro)"),
        ("app/services/hitl.py", "HITL-карточки для Telegram (✅/🔁/✏️/❌)"),
        ("app/ai_agent/", "Сам AI-агент (Phase I) — то что ты сейчас!"),
        (".env.example", "Шаблон конфига"),
        ("prompts/", "Мастер-промпты — НЕ ТРОГАТЬ без явного запроса"),
        ("tests/", "Pytest unit + smoke тесты"),
    ]

    parts = ["## Ключевые файлы\n"]
    for rel_path, desc in key_files:
        p = repo_root / rel_path
        exists = "✓" if p.exists() else "✗"
        parts.append(f"- {exists} `{rel_path}` — {desc}")
    return "\n".join(parts)


def _build_active_rules_section(repo_root: Path) -> str:
    """Выжимка запретов из AGENTS.md (раздел «Запреты»)."""
    return """## Активные запреты (из AGENTS.md §4)

1. **Никакого UI.Vision** — только Playwright/CDP.
2. **Никаких бесконечных retry-циклов.** MAX_FAIL=3 в app/main.py.
3. **На 🔁 в HITL героя/кадра** — НЕ дёргать ChatGPT повторно, только
   outsee.regenerate_image.
4. **Не коммитить в legacy/* и main.**
5. **Не публиковать в соцсети** (SOCIAL_PUBLISH_ENABLED=false).
6. **Не хранить креды в репо.** SOCKS5, TG-токен, AITunnel-ключ —
   только в .env (gitignored).
7. **Не делать git push --force** без явного permission.
8. **Не удалять файлы / git reset --hard** без HITL-апрува.

## Когда останавливаться и спрашивать owner'а (AGENTS.md §13)

- PR > 600 LOC.
- Удаляется тест.
- Меняется `app/models.py` (схема БД).
- Появляется Alembic-миграция.
- Меняется внешний API (outsee / ChatGPT / orchestrator_api).
- Затрагиваются `prompts/*`.
"""


def _build_tooling_section() -> str:
    return """## Команды для проверки

```bash
ruff check . && ruff format --check . && mypy app && pytest -q
```

Все 4 должны быть зелёные. Mypy сейчас в warn-режиме.

## Стиль коммитов
`<тип>(<scope>): <короткое описание>`
типы: feat | fix | chore | docs | test | refactor
scope: ai | telegram | bots | orchestrator | api | tests | docs
"""


def build_project_context(repo_root: Path | None = None) -> str:
    """Собрать полный project_context.md для system prompt.

    Размер ~3-5k токенов. Кешировать НЕ обязательно — собирается за <50ms.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]

    branch = _detect_current_branch(repo_root)

    sections = [
        f"""# Project: video-pipeline

Каноничная ветка: **vetka-final** (переходно — `devin/1779156871-combine-A-and-C-physical-clicks`).
Текущая git-ветка: `{branch}`.

Это автоматический pipeline генерации коротких видео (60-75 сек, 9:16, Shorts/Reels).
ChatGPT (web Playwright) пишет план и сценарий → outsee.io (nano-banana-2 + veo-3-fast)
генерит картинки и видео → ffmpeg собирает финальный MP4 → опционально публикация.
Весь контроль через **Telegram HITL** (`@content1400_bot`, chat_id=279887118).

Стек: Python 3.11+, aiogram 3, SQLAlchemy 2, SQLite (WAL), aiohttp,
Playwright (через CDP к существующему Chrome), faster-whisper, ffmpeg-python.
Без Docker.
""",
        _build_db_schema_section(),
        _build_key_files_section(repo_root),
        _build_active_rules_section(repo_root),
        _build_tooling_section(),
    ]
    return "\n\n".join(sections)


def _detect_current_branch(repo_root: Path) -> str:
    """Текущая git-ветка (для информации в контексте)."""
    try:
        import subprocess

        out = subprocess.check_output(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
        return "<detached>"


def build_system_prompt(
    repo_root: Path,
    *,
    mode: str = "hitl_edit",
    include_edit_hint: bool = True,
) -> str:
    """Собрать полный system prompt для LLM.

    Состоит из 3 частей:
    1. Роль и правила.
    2. Project context (auto-built).
    3. Hint про tools.
    """
    ctx = build_project_context(repo_root)

    role = """Ты — AI-агент внутри Telegram-бота проекта video-pipeline.

Твоя задача:
- Помогать owner'у разобраться в коде, ответить на вопросы.
- Найти и исправить баги.
- Подсказать как добавить фичу.
- Сделать рефакторинг.

Принципы работы:
1. **Сначала исследуй** — прочитай нужные файлы (`read_file`), поищи по коду
   (`search_code`), посмотри схему БД (`describe_db` / `db_query`).
2. **Потом думай** — что именно надо изменить, какие side-effects.
3. **Потом действуй** — внеси правки (edit_file), проверь линтером (run_ruff),
   запусти тесты (run_pytest).
4. **Всегда** завершай сессию вызовом `final_answer` с понятным резюме для
   owner'а.

Соблюдай запреты (см. секцию ниже). Если нужно нарушить — спроси через
`final_answer` и заверши сессию (owner откроет новую с уточнением).
"""

    edit_hint = ""
    if include_edit_hint and mode == "hitl_edit":
        edit_hint = """
## Режим: HITL-edit

Любая правка файла (edit_file, write_file) требует подтверждения owner'а.
- Сделай правку.
- Бот покажет owner'у diff с кнопками ✅/🔁/✏️/❌.
- Tool вернёт тебе результат: approved/rejected/clarified.
- Если rejected — попробуй другой подход или объясни через final_answer.
- Если clarified — owner написал текстовое уточнение, читай его и пробуй снова.

Будь экономен с tool-вызовами: каждый шаг стоит токенов и времени.
Перед edit_file всегда сначала read_file нужный фрагмент.
"""
    elif mode == "qa":
        edit_hint = """
## Режим: Q&A (read-only)

Только read-tools доступны. Если ты считаешь что нужна правка — расскажи об
этом в final_answer, но не пытайся вызвать edit_file (его нет в tools).
"""
    elif mode == "auto":
        edit_hint = """
## Режим: AUTO в feature-ветке

Ты в изолированной ветке `agent/ai-*`. Можешь делать правки без HITL.
В конце выполни git_commit, открой PR через gh_pr_create, и расскажи об
этом в final_answer. Owner ревьюит PR вручную.
"""

    return f"{role}\n{edit_hint}\n\n---\n\n{ctx}"


__all__ = [
    "build_project_context",
    "build_system_prompt",
]
