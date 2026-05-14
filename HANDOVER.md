# Handover — video-pipeline (локальный Windows-запуск)

> **🟢 Канонная ветка на 2026-05-14:** `devin/windows-installer`
> Это default-ветка репо. На ней есть и одиночный пайплайн, и массовая
> генерация («🎬 Массовое создание» в `/menu` → `mass:*`, меню «⚙ Настройки
> массовой», парити single↔mass #1-#8, доки `docs/MASS_CREATION.md`,
> тесты `tests/test_auto_advance_parity.py`).
> Локально работать так:
> `git fetch --all --prune && git checkout devin/windows-installer && git pull --ff-only`.
> Ветка `main` — старый skeleton, **в неё не коммитить**.

Прочитать это ВНИМАТЕЛЬНО перед первым ответом пользователю. Это не код-ревью
и не новый проект — это **живая отладка на машине пользователя**.

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
