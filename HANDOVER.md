# Handover — video-pipeline (локальный Windows-запуск)

> **🟢 Канонная ветка на 2026-05-22:** `vetka-final`
> (переходно: `devin/1779156871-combine-A-and-C-physical-clicks` до Phase 0 rename).
>
> Старый default `devin/windows-installer` будет переименован в
> `legacy/windows-installer-pre-2026-05-22` и зафризится.
>
> Локально:
> `git fetch --all --prune && git checkout vetka-final && git pull --ff-only`.

> **⚠️ TODO после Phase 0** — пропущены 2 outsee anti-dup фикса из old default
> при автоматическом merge:
> - `eb259cd` fix(outsee): не кликаем Generate если карточка уже есть
> - `2e7954f` fix(outsee video): анти-дубликат для генерации видео
>
> Cherry-pick дал бы конфликты (canonical outsee.py имеет +2680 строк с
> physical CDP кликами). Если будут случаи дубль-генерации картинок/видео при
> retry — портировать вручную: добавить `_count_id_tokens_in_page` перед
> Generate в `generate_image()` и `generate_video()`. См.
> `git show eb259cd 2e7954f` на ветке `legacy/windows-installer-pre-2026-05-22`.

Прочитать это ВНИМАТЕЛЬНО перед первым ответом пользователю. Это не код-ревью
и не новый проект — это **живая отладка на машине пользователя**.

---

## 🆕 Что нового на 2026-05-23 (PR #39)

Этот PR — большой инфраструктурный апдейт (12+ commits). После его merge'а:

### Новые инструменты для ИИ-агентов
- **`AGENTS.md`** — правила игры для любого ИИ-агента в репо (350 строк):
  канон-ветка, naming, запреты, lock-матрица, hand-off протокол.
- **`.cursor/rules/*.mdc`** (6 файлов) — Cursor Rules с globs по областям.
- **`docs/E4_MIGRATION_GUIDE.md`** — 7-шаговый алгоритм миграции
  handler'ов из bot.py + таблица 13 групп.
- **`docs/TRIAGE_2026-05-23.md`** — отчёт по 25 OPEN PR'ам с действиями.
- **`docs/CALLBACK_INVENTORY.md`** — auto-generated, какой callback где
  используется.

### AI-агент в Telegram (Phase I)
- Команда `/ai <запрос>` — Cursor/Devin-стиль агент через aitunnel.ru
  + `gpt-4o-mini` (~0.20₽/сессия).
- 19 tools: read/edit/search/db_query/git_*/gh_*/run_pytest/ruff/mypy.
- HITL на каждую правку файла (4 кнопки ✅🔁✏️❌, таймаут 30 мин).
- Safety: whitelist путей (`.env*`, `data/state.db*`, `.git/**`),
  secret-scan, SELECT-only `db_query`, никаких shell/delete/push.
- Audit в БД: AISession/AIMessage/AIToolCall. CLI: `scripts/ai_dump.py`.
- Подробнее: `app/ai_agent/`, AGENTS.md §16.

### Расширенный /debug в Telegram (Phase G)
```
/debug status / project <id> / locks / logs <id> [N]
/debug ai [sid] / selftest / api
```
- `selftest` проверяет SQLite WAL / FFmpeg / CDP Chrome / AI-агент
  баланс / orchestrator_api / disk space.
- CLI: `scripts/project_dump.py` для полного JSON-дампа проекта.

### CI workflow (Phase B + B.2)
- 13 step'ов: ruff (BLOCKING — 0 errors), mypy strict для моих модулей
  (BLOCKING), pytest, 4 smoke (imports, orchestrator_api /health,
  AI tools registry, CB enum), audit_buttons (FAIL если callback > 64
  байт), detect-secrets (паттерны `sk-aitunnel-*`, `sk-*`, `socks5://`).

### Phase E.4 foundation (без модификации bot.py)
- `app/telegram/callback_registry.py` — **58 CB-префиксов** покрывают
  100% callback'ов в репо. AST-инвариант в CI.
- `app/telegram/keyboards/` — типизированные фабрики
  (common/main_menu/project_menu/hitl_buttons/wizard) с 64-byte guard.
- `scripts/migrate_callback_to_cb.py` — auto-rewrite tool: inline
  `callback_data="x:y"` → `make_callback(CB.X_Y, ...)`. Dry-run + apply.
- `scripts/cb_inventory.py` — генератор CALLBACK_INVENTORY.md.
- **`bot.py` НЕ ТРОНУТ** — параллельные cursor-агенты правят его, риск
  merge-конфликтов слишком высок. Foundation готов для миграции отдельными
  PR'ами (Phase E.4 steps 3-9).

### Фиксы применённые из параллельных PR'ов
- **PR #40** (`638f1f0`): cleanup `_active_sessions` в `finally:` —
  иначе owner залочен после редкого сбоя send_message.
- **PR #41** (`a431314`): cleanup `_clarification_waits` тоже в `finally:` —
  иначе после ✏️ Clarify без текста stale-entry съест любой следующий
  обычный текст owner'а.

### Тесты
- **348 тестов** ✅ (на старте было 100 → **+248**).
- 0 ruff errors. 0 mypy strict errors на 32 модулях.

### TODO для owner'а ПЕРЕД merge

1. 🚨 **Ротировать AITunnel API key** — старый `sk-aitunnel-cNT...`
   засветился в чате 2026-05-22.
2. Переименовать `devin/1779156871-...` → `vetka-final`.
3. Settings → Branches → default = `vetka-final` + branch protection
   (CI required, 1 review, linear history, squash-only).
4. Положить новый ключ в `.env` (не в репо!): `ORCHESTRATOR_AI_API_KEY=...`.
5. Проверить `/ai статус` и `/debug selftest` в боте.

---

## Что это

Pipeline для автоматической генерации коротких видео (Shorts/Reels):
ChatGPT (web, без API) пишет план и сценарий → outsee.io (nano-banana-2,
veo-3-fast) генерит картинки/видео → пайплайн собирает финальный MP4 и
публикует в YouTube/VK (позже). Весь контроль через **Telegram HITL**:
после каждого шага бот шлёт карточку с кнопками ✅/🔁/✏️/❌, пайплайн
двигается только когда владелец подтверждает.

**Владелец и тестер** — один человек, `chat_id=279887118`, бот
`@content1400_bot`.

**Пилот-тема** зашита в `app/seed_pilot.py`: «5 фактов о рачках в стиле
киберпанк».

## Архитектура одной картинкой

```
ChatGPT web (Playwright) ──┐
                           ├── one Chrome, remote-debug 29229
outsee.io  (Playwright) ───┘
                                │
                                ▼
          Worker loop (app/main.py) ── advance_project() ── steps/*.py
                                │              │
                                ▼              ▼
                       SQLite state.db     Telegram HITL
                                                │
                                                ▼
                                      Owner presses button → DB decision
```

- **Один Chrome** живёт с `--remote-debugging-port=29229
  --user-data-dir=%USERPROFILE%\.vp_browser_data`. Наш код подключается к нему
  по CDP (`http://localhost:29229`), не запускает свой браузер.
- **SQLite** в `data/state.db`. Все стейты пайплайна, кадры, HITL-запросы,
  артефакты — там.
- **Telegram** — через **SOCKS5** (`socks5://vhGfB2:0tnzqA@45.130.61.143:8000`),
  иначе api.telegram.org недоступен у пользователя.
- **Python 3.11** в `.venv` локально на Windows.

## Файлы (быстро)

```
app/
  main.py                    # воркер-цикл + TG polling
  settings.py                # env config
  db.py                      # session_scope
  models.py                  # Project/Frame/HITLRequest/Artifact + Enums
  seed_pilot.py              # python -m app.seed_pilot

  bots/
    browser.py               # BrowserSession (CDP connect), open_page()
    chatgpt.py               # ChatGPT через web, ask_fresh()
    outsee.py                # ⭐ главный файл. generate_image / regenerate_image
    publishers.py            # YouTube/VK (пока не трогали)

  telegram/
    bot.py                   # aiogram dispatcher, on_hitl_callback,
                             # on_owner_text_reply (для ✏️ edit_prompt)

  services/
    hitl.py                  # send_hitl_photo / send_hitl_text,
                             # _keyboard(allow_edit=False/True)
    assembly.py              # финальная сборка MP4 (ffmpeg, позже)

  orchestrator/
    pipeline.py              # advance_project() — стейт-машина
    steps/
      make_plan.py           # 1. ChatGPT → план
      make_script.py         # 2. ChatGPT → сценарий
      split_frames.py        # 3. нарезка на кадры
      generate_hero.py       # 4. герой (1 картинка) — HITL 3 кнопки
      generate_images.py     # ⭐ 5. кадры, per-frame HITL 4 кнопки, ПАРАЛЛЕЛЬНО
      make_animation_prompts.py
      generate_videos.py     # пока не трогали
      publish.py             # пока не трогали

data/state.db                # SQLite
data/videos/<slug>/scenes/   # картинки кадров
```

## Текущий стейт (на момент хэндовера)

**Что работает:**
- План → HITL → сценарий → кадры (split) → герой (1 картинка) → HITL по
  герою с 3 кнопками (✅/🔁/❌). Кнопка 🔁 использует outsee «Повторить»
  вместо повторного ChatGPT.
- Telegram через SOCKS5 ок.
- ChatGPT-кэш hero_description (на retry не дёргает ChatGPT повторно).
- Лог из outsee подробный: `outsee.generate_image: открываю страницу /
  textarea найдена / Generate кликнут, жду картинку / _wait_image_url: ждём
  N сек, big imgs=X, net image responses=Y`.

**Что переписали сегодня:**
- `outsee._wait_image_url` теперь ловит картинку **3 путями**:
  1. `<img>` в блоке «Результат генерации» (основной);
  2. сетевой listener `page.on('response', ...)` — ловит любой `image/*`
     ответ ≥50 KB, не из `/_next/`, `/static/`, `/assets/`, `/logo`,
     `favicon`;
  3. DOM fallback — любая новая большая `<img>` ≥200×200.
  Приоритет: (1) > (2) > (3). Таймаут 600 сек (nano-banana может считать
  5–7 минут).
- `_wait_button_enabled` — перед кликом Generate ждёт пока кнопка не
  перестанет быть `disabled` (outsee её блокирует пока идёт предыдущая
  генерация).
- `GENERATE_BUTTON_SELECTORS` теперь сначала пробует `:not([disabled])`,
  потом fallback на обычные.
- **`generate_images.py` переписан на параллельную логику (ВАРИАНТ Б)**:
  Phase 1 — все промты для всех кадров ChatGPT подряд, Phase 2 — все
  картинки outsee по очереди, каждая готовая картинка СРАЗУ шлётся в TG
  как HITL-карточка с 4 кнопками, бот НЕ ждёт решения и идёт к
  следующему кадру. `_apply_pending_regens()` ловит 🔁/✏️-решения,
  возвращает кадр в `image_prompt_ready`, фоновой цикл подхватывает
  и перегенерирует.
- Edit-prompt flow: на ✏️ бот шлёт сообщение «Ответь новым промтом»
  с текущим промтом в `<pre>`; `on_owner_text_reply` в bot.py ловит
  ответ по `edit_ask_message_id` в payload, обновляет `frame.image_prompt`,
  ставит `decision=edit_prompt`.

**Что НЕ трогали (не работает → работать будет позже):**
- generate_videos (veo-3-fast) — пока не тестили.
- generate_audio.
- Финальная сборка, публикация.

**Ветка:** `devin/1777068495-sqlite-pilot` (ещё не слита в main).
Пользователь **не делает pull** — правим через file-share.

## File-share (как доставляем фиксы)

```
URL: https://file-share-jhzzsgct.devinapps.com
Папка на VM: /tmp/file-share/
Деплой: deploy(frontend, dir='/tmp/file-share') → тот же URL
```

Процедура:
1. Меняем код в `/home/ubuntu/repos/video-pipeline/app/...`
2. `cp app/.../file.py /tmp/file-share/file.py`
3. `deploy(frontend, dir='/tmp/file-share')`
4. Говорим пользователю:
   ```powershell
   Invoke-WebRequest -Uri "https://file-share-jhzzsgct.devinapps.com/file.py?v=N" -OutFile "app\...\file.py" -TimeoutSec 60
   ```
   (обязательно `?v=N` — у Windows с Invoke-WebRequest кеш бывает;
   и всегда `-TimeoutSec 60`, иначе висит).
5. После — обязательно `Get-ChildItem -Recurse -Directory -Filter __pycache__
   | Remove-Item -Recurse -Force`, иначе Python грузит старый байткод.

## ⛔ ЯВНЫЕ ЗАПРЕТЫ от пользователя (не нарушать)

1. **Никакого UI.Vision**. Дословно: «я бы не хотел напрямую ковыряться
   в ui vision». Только Playwright/CDP.
2. **Никаких бесконечных retry-циклов.** Дословно: «я хочу что бы таких
   зацикленностей не было». В `app/main.py` стоит MAX_FAIL=3; после
   3 фейлов проект помечается `failed`, и воркер его не трогает.
3. **На 🔁 не вызывать ChatGPT повторно** — только жать «Повторить» на
   outsee (`outsee.regenerate_image`). ChatGPT тратит кредиты.
4. **На 🔁 не перезаполнять промт** — outsee сам знает предыдущий,
   «Повторить» использует его без изменений.
5. **Не коммитить в main**, не делать PR пока не разрешил. Сейчас
   работа идёт на ветке `devin/1777068495-sqlite-pilot`.

## Тонкости, на которые легко налететь

- **outsee 3 textarea**: на странице 3 textarea с одинаковыми классами
  (desktop/mobile/sidebar). `_first_visible()` перебирает все и берёт
  первую **видимую**. Если ломается — проверять `_first_visible` +
  `PROMPT_INPUT_SELECTORS`.
- **Картинка nano-banana в DOM**: user show'ал, что на странице после
  генерации в `document.querySelectorAll('img')` нет `<img>` ≥200×200.
  Скорее всего рендерится через `background-image` или canvas. Поэтому
  основной ловитель — сетевой listener (п.2 выше).
- **nano-banana может считать 5–7 минут**. Таймауты везде 600 сек.
  Если пользователь жалуется «бот молчит 3 минуты» — это нормально.
- **CHECK constraint SQLite** на enum. Если добавляем значение в
  `HITLDecision` / `FrameStatus` — **нужен полный DB reset**
  (`Remove-Item data\state.db; python -m app.seed_pilot`).
- **PowerShell пожирает кавычки** в `python -c "..."`. Для SQL-скриптов
  лучше так:
  ```powershell
  @'
  import sqlite3
  c = sqlite3.connect("data/state.db")
  c.execute("UPDATE projects SET status='frames_ready' WHERE id=1")
  c.commit()
  print("ok")
  '@ | Set-Content -Encoding UTF8 reset_status.py
  python reset_status.py
  ```
- **Колонки в `projects`**: НЕТ `hero_image_path`. Путь картинки героя
  хранится в `artifacts` (kind=hero_reference). Не писать SQL по
  несуществующим колонкам.
- **Статус проекта после фейла**: `failed`. Для продолжения руками
  откатить:
  ```sql
  UPDATE projects SET status='frames_ready' WHERE id=1
  ```

## Команды, которые пользователь гоняет

```powershell
# Стартануть Chrome с отладочным портом (если закрыл)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=29229 `
  --user-data-dir="$env:USERPROFILE\.vp_browser_data"

# Запуск бота
python -m app.main

# Сид пилота с нуля
Remove-Item -Force data\state.db
python -m app.seed_pilot
python -m app.main

# Проверить статус
python -c "import sqlite3; print(sqlite3.connect('data/state.db').execute('SELECT id, status FROM projects').fetchall())"
```

## Стиль общения с пользователем

- **Русский язык, ты/тыкать, коротко и по делу.** Никаких «Я помогу
  вам...» — просто «Чиню», «Готово», «Пришли лог».
- **Блоки кода копируемые одной строкой** (он в PowerShell, много
  косяков с переносами).
- **Никаких эмодзи** кроме тех что в UX (✅/🔁/✏️/❌) или чтобы
  передать настроение.
- **На вопрос про хрень — сначала признать косяк, потом чинить.**
  Не защищаться. Юзер часто злится, и это нормально — работа
  стрессовая, он платит, мы отвечаем делом.
- **НЕ создавать PR** в main без разрешения. Всё через file-share.

## Если не знаешь что делать

1. Посмотри логи из последнего `python -m app.main` у пользователя.
   Ищи строки `[#1] ...`, `outsee.generate_image:`, `_wait_image_url:`,
   `Ошибка на проекте`.
2. Посмотри статус проекта (команда выше).
3. Спроси у пользователя скрин Chrome-вкладки outsee если подозрение
   на UI-ломку.
4. Если нужен пересмотр архитектуры — **спроси**, не переписывай
   молча.
