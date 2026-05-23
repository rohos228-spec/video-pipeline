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


def _build_runtime_snapshot(repo_root: Path) -> str:
    """Свежие runtime-данные: failed проекты + последние коммиты.

    Best-effort: если БД нет / git недоступен — возвращаем что есть, без
    crash. Этот раздел даёт LLM context который "тёпленький" и часто
    помогает диагностировать "что-то сломалось" без вопросов.
    """
    parts: list[str] = ["## Runtime snapshot (актуальные данные)"]

    # 1. Свежие failed проекты из SQLite
    try:
        import os
        import sqlite3
        db_path_raw = os.environ.get("SQLITE_PATH", "./data/state.db").strip()
        db_path = Path(db_path_raw)
        if not db_path.is_absolute():
            db_path = (repo_root / db_path).resolve()
        if db_path.exists():
            with sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True
            ) as conn:
                conn.row_factory = sqlite3.Row
                # Project.meta — JSON, в нём может лежать last_error.
                # Если колонки нет (старая БД) — graceful empty.
                rows = conn.execute(
                    "SELECT id, slug, status, "
                    "       COALESCE(topic, '') AS topic, "
                    "       COALESCE(meta, '{}') AS meta_json "
                    "FROM projects "
                    "WHERE status IN ('failed', 'paused') "
                    "ORDER BY id DESC LIMIT 5"
                ).fetchall()
            if rows:
                import json as _json
                parts.append("\n### ⚠️ Свежие failed/paused проекты")
                for r in rows:
                    slug = (r["slug"] or "?")[:40]
                    status = r["status"]
                    topic = (r["topic"] or "")[:60]
                    parts.append(f"- #{r['id']} `{slug}` [{status}] {topic}")
                    # Распарсить meta для поиска last_error
                    try:
                        meta = _json.loads(r["meta_json"] or "{}")
                    except Exception:  # noqa: BLE001
                        meta = {}
                    last_err = str(
                        meta.get("last_error")
                        or meta.get("error")
                        or meta.get("last_failure")
                        or ""
                    )[:120].replace("\n", " ")
                    if last_err:
                        parts.append(f"  error: {last_err}")
            else:
                parts.append("\n### Свежие failed/paused проекты: НЕТ ✅")
    except Exception as e:  # noqa: BLE001
        parts.append(f"\n_DB snapshot unavailable: {e}_")

    # 2. Последние 3 коммита git
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "log", "--oneline", "-3", "--no-decorate"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        commits = out.decode("utf-8", errors="replace").strip()
        if commits:
            parts.append("\n### Последние коммиты")
            for line in commits.split("\n"):
                parts.append(f"- `{line}`")
    except Exception as e:  # noqa: BLE001
        parts.append(f"\n_git log unavailable: {e}_")

    return "\n".join(parts)


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

    # Свежие runtime-данные (best-effort, fail silent)
    runtime_section = _build_runtime_snapshot(repo_root)
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
        runtime_section,
    ]
    return "\n\n".join(s for s in sections if s)


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
Ты НЕ генеральный ассистент. Ты знаешь этот конкретный проект (см.
project_context ниже), у тебя есть tools для чтения файлов и БД, и
владелец платит токены за каждый твой ответ.

## ЖЕЛЕЗНОЕ ПРАВИЛО ИССЛЕДОВАНИЯ

Прежде чем задать пользователю ЛЮБОЙ уточняющий вопрос — ВСЕГДА сначала
собирай факты через tools. Большинство ответов уже есть в коде или БД.

Минимум исследования для типичных запросов:

### "бот зациклился / висит / падает / не работает X"
1. db_query "SELECT id, status, last_error FROM projects WHERE status='failed' ORDER BY id DESC LIMIT 5"
2. read_file HANDOVER.md (там список свежих проблем и тонкостей)
3. search_code по ключевому слову из жалобы (например 'retry', 'MAX_FAIL')
4. ТОЛЬКО потом final_answer с конкретикой (id проекта, файл:строка, что делать)

### "как работает X / объясни X"
1. search_code "X" — где упоминается
2. read_file первого matched файла
3. final_answer с цитатами кода

### "добавь / измени / поправь X"
1. search_code по имени символа / строки
2. read_file релевантный кусок
3. edit_file с конкретным diff (HITL обработает)
4. run_ruff и run_pytest после правки
5. final_answer что сделано + результаты тестов

### "какие проекты есть / статус / прогресс"
1. describe_db (если ещё не видел схему)
2. db_query с фильтрами по запросу
3. final_answer с цифрами

ЗАПРЕЩЕНО:
- Спрашивать "дай больше информации" БЕЗ предварительного исследования.
- Отвечать общими словами "посмотри логи / проверь конфиг" — это работа,
  которую ТЫ должен сделать через tools.
- Делать final_answer с одним только вопросом — это бесполезный шаг.

ИСКЛЮЧЕНИЕ: если после 3-5 tool-вызовов всё ещё непонятно — можно
final_answer с уточнением, но **с приложением фактов которые ты уже
нашёл** ("я смотрел X, нашёл Y, теперь скажи Z").

## Что делать дальше

1. **Сначала исследуй** через tools (см. правило выше).
2. **Потом думай** что нужно изменить и какие side-effects.
3. **Потом действуй** — edit_file (HITL), run_ruff, run_pytest.
4. **Всегда** завершай сессию вызовом `final_answer` с конкретным резюме
   (что сделал / нашёл / предложил, не "помог разобраться").

Соблюдай запреты (см. ниже). Если нужно нарушить — спроси через
final_answer и заверши.
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

Перед edit_file всегда сначала read_file нужный фрагмент.
Когда пользователь просит развёрнутый ответ ('напиши историю на 100 слов',
'объясни подробно', 'распиши пошагово') — пиши столько сколько просят.
LLM-агент сам решает длину ответа, не экономь tokens без явной причины.
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
