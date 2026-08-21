# video-pipeline — операционный контекст

## Git: ветка ПК (обязательно)

Рабочие ветки машин: **`housepc`**, **`tompc`**, **`strangepc`**, **`workpc`**, плюс **`main`**.
Целевая ветка задаётся локально: `ORCHESTRATOR_GIT_BRANCH` в `.env`
(и `data/studio-pc-branch`). **Не хардкодить ПК** — смотри `.env` на этой машине.

После **любого** исправления кода (баг, монтаж, UI, backend):

1. `git checkout <ORCHESTRATOR_GIT_BRANCH> && git pull origin <ветка>`
2. правки + тесты
3. `git add … && git commit -m "…"`
4. **`git push origin <ветка>`** — сразу, без ожидания ревью
5. В ответе: **`git HEAD: <sha>` уже в `<ветка>`**

Запрещено:

- пушить в чужую ПК-ветку / не в `ORCHESTRATOR_GIT_BRANCH` (если пользователь не сказал явно)
- оставлять фикс только в `cursor/…` и говорить «PR готов, подтяни когда смержим»
- считать задачу выполненной, если на Windows у пользователя всё ещё старый `git HEAD`

Слияние ПК-веток в `main` — отдельной командой пользователя (если целевая ветка не `main`).

## Запуск и обновление
- Studio: `STUDIO.cmd` → пункт [1] → http://127.0.0.1:8765
- Первый запуск / **[5]**: выбор ветки (`housepc`/`tompc`/`strangepc`/`workpc`/`main`) → `data/studio-pc-branch` + `.env`
- **[4]** Обновить и запустить с **сохранённой** ветки (`origin/<ветка>`), не всегда main
- **[5]** Ветка — сменить (в т.ч. на main)
- **[6]** Починить; **[7]** Диагностика; [1][2][3] как раньше
- Локальный код без git reset: правки вручную, затем `stop-backend.cmd` + `STUDIO.cmd` [1]
- **НЕ** использовать скрипты из `scripts/legacy/` (старые FORCE-UPDATE / VideoPipelineStudio)
- Ветка этой машины: значение `ORCHESTRATOR_GIT_BRANCH` в `.env` (сейчас часто `main` или имя ПК)

## ChatGPT attach (`app/bots/chatgpt.py`)
- Счёт вложений — **inline** `page.evaluate` в `_count_attachment_previews` / `_attachments_upload_state`
- **Запрещено** в evaluate: `div.group/attachment`, `vpComposer*`, `_COMPOSER_ATTACHMENT_DOM_JS`, `_composer_page_eval_js` — ломает `querySelectorAll`
- `div.group\\/attachment` допустим только в Playwright-локаторах (`FILE_PREVIEW_SELECTORS`), не в evaluate
- После правок `chatgpt.py` — обязательно перезапуск бэкенда
- Успех attach в логе: `ChatGPT: drag-drop batch — [...]` и `все файлы видны`

## anim_pr
- Источник правды для пропуска/генерации: **DB** (`Frame.animation_prompt` / apply-ops).
  Excel R48 — зеркало (sync write-through / `sync_animation_prompts_from_xlsx`), не SoT для skip.
- `animation_prompt_gpt.py`: `has_animation_prompt_for_frame`, `scan_missing_animation_prompts`, sync R48↔DB
- Soft ▶ / soft retry без wipe готовых промтов: `clear_step_outputs_for_rerun(force_wipe=False)`;
  полный wipe — `reset_step` / `force_wipe=True`. Soft retry steps: `anim_pr`, `img`, `video`
- Пауза после ошибок (30 мин): снимается ручным `POST /api/projects/{id}/steps/anim_pr/run` или кнопкой в Studio

## Сбои шагов
- `reset_step` для `img` раньше удалял все PNG — теперь backup в `old/scenes/<timestamp>/`
- При CDP timeout на img/video/anim_pr — soft retry, не wipe

## Активный проект (типично)
- #13 slug `nicshe` — anim_pr / доделка картинок; логи: `data/backend.log`, `data/backend-*.log`

## Диагностика (делать самому, не переспрашивать)
1. `git status` / `git diff` — что не закоммичено
2. `STUDIO.cmd` → [6] или `logs/doctor.log`
3. Хвост `data/backend-*.log` — последний PID из scripts/run-backend
4. `pytest tests/test_chatgpt_attachment_guard.py tests/test_animation_prompt_gpt.py -q`
5. API: `POST http://127.0.0.1:8765/api/projects/13/steps/anim_pr/run`
