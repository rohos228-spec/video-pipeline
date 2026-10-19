# AUDIT 6 — заглушки и упрощения в боевом коде

База: HEAD 850b3f47, ветка feature/orchestrator-observe-v1. Merge base с origin/main: 8c2964f2 (480 коммитов, 347 изменённых файлов в app/web/src/scripts).
Метод: grep по app/, web/src, scripts/ (NotImplementedError, TODO/FIXME, stub/placeholder/fake/synthetic/UNCALIBRATED, except/pass, пути, порты, env-переключатели, PYTEST_CURRENT_TEST, жёстко заданные id проектов), затем чтение найденных мест. Тесты и .env не читались, сеть не вызывалась, комитов нет.

Сводка сканов: NotImplementedError = 0 во всём app/web/src/scripts. TODO/FIXME = 1 (app/orchestrator/auto_advance.py:1694, унаследованный). Блоков `except ...: pass` в добавленных/изменённых py-файлах (кроме tests): 109, подробно см. A14.

Серьёзность: BLOCKS = блокирует боевой запуск; DEGRADES = ухудшает; COSMETIC = косметика.

---
## A. Код этой ветки (оркестратор, action-layer, наблюдение, медиа)

### A1. Управляемая (managed) медиа-ветка работает только на синтетическом loopback — BLOCKS (для управляемых media-нод)
- app/services/media_jobs.py:158 — `if mode != "synthetic" and raw["enabled"]: raise ValueError("media production transport has no authorized network profile")`. Боевой конфиг с media.enabled=true невозможен по построению.
- app/services/media_jobs.py:164-166, 182 — разрешены только `http://127.0.0.1|::1:<port>`; check_url отвергает любой другой origin.
- app/services/media_jobs.py:208-212 — assert_entry требует `cfg["mode"] == "synthetic"` и `settings.gpt_api_key.startswith("synthetic")`.
- app/services/actions_v1.py:286 — запуск managed-узла отказывает при `cfg.get("mode") != "synthetic"` (denied "media_configuration_changed_or_disabled").
- app/services/media_view.py:49 — в ответ UI зашито `"synthetic_only": True, "production_profile_configured": False` (константа, не вычисляется).
- app/services/node_capabilities.py:414-418 — само признаёт: "Managed route is synthetic-loopback only (no authorized production network profile)"; images/audio = human_only.
- Итог: оркестратор не может сам запускать и повторять платные media-ноды (images, audio, music, sfx, video) на боевых провайдерах; доступен только resume известной задачи (_MEDIA_RESUME_ONLY) и ручное решение.
- Работа: утвердить боевой сетевой профиль (allowlist origin kie/elevenlabs, https), убрать привязку к mode=="synthetic" в 4 местах, вычислять production_profile_configured.

### A2. Decision-цикл выключен по умолчанию — BLOCKS (для автономного решения)
- app/services/decision_config.py:188-198 — без ORCHESTRATOR_RUNTIME_CONFIG возвращается `{'mode':'production','online_enabled':False,'decision':None,...}`; синтетический режим включается только при loopback + ключе `synthetic*`.
- app/services/decision_config.py:19-24 — разрешены ровно 2 маршрута (gpt-kie, gpt-5.6-sol-vibecode). app/services/profiles_v1.py:18-23 — встроенный каталог профилей целиком синтетический (`gpt-synthetic-alt`, synthetic_only=True); боевые профили должны прийти из конфига.
- Работа: боевой runtime-конфиг владельца (файл + env) и приёмка на реальном вызове. Боевая ветка validate_config (decision_config.py:101-115) в коде есть, реальным вызовом в этом аудите не подтверждена.

### A3. Расценки ElevenLabs — оценка, не замер — DEGRADES
- app/services/media_spend.py:24-27 — `ELEVENLABS_USD_PER_CHAR = 0.0003`, `ELEVENLABS_SFX_USD_PER_SEC = 0.012`, помечены "UNCALIBRATED". Используются: media_spend.py:438, 452 (TTS), 467 (SFX, длительность зажата 0.5..22 с). Пишутся в spend_ledger как kind=actual с note "uncalibrated", в отчётах расходов выглядят как факт.
- Работа: замерить по балансу, заменить; до тех пор показывать источник "оценка" в UI.

### A4. Цены Vibecode — не откалиброваны, наценка x3 — DEGRADES
- app/services/vibecode_catalog.py:15 `PRICE_MARKUP = 3.0` (оценка). app/services/api_tracker_hook.py:53-57 прямо пишет: реальная тарификация vibecode.moe неизвестна, возможна недооценка до размера наценки, снимок цен без даты.
- app/services/action_layer/node_run_cost.py:28 `UNCALIBRATED = "верхняя оценка, не откалибровано"`; :29-38 CHARS_PER_TOKEN=2.0, PROMPT_MARGIN=1.2, HINT_CHARS=1500, FOOTER_CHARS=8000, SYSTEM_CHARS=6000, MAPPING_LINE_CHARS=60, NEIGHBOR_HINT_CHARS=300, CONTINUE_INPUT_TOKENS=4000, REASONING_ALLOWANCE_TOKENS=8000, UNITS_PER_BATCH=8; :218-219 цена x PRICE_MARKUP. Используется в builtin_wave3.py:433, 464 (карточка плана, поле `uncalibrated: True`).
- app/services/api_tracker_hook.py:25-37 `_RATES` — тарифы gemini/gpt-5.6-terra/luna/deepseek без отметки замера; замерена только gpt-5.6-sol (api_tracker_hook.py:45-47, docs/KIE_COST_CALIBRATION.md, 2026-10-01, 3 пробы, погрешность около $0.0003).

### A5. Резервы денег в spend_guard — оценки — DEGRADES
- app/services/spend_guard.py:32-34 — DEFAULT_REQUEST_ESTIMATE_USD=0.05 (в комментарии: измерено 0.00005-0.008, то есть завышено), MEDIA_SUBMIT_ESTIMATE_USD=0.30 (не замерено), PLANNER_ESTIMATE_USD=0.05. Используются: actions_v1.py:588, action_layer/planner.py:568, regen_budget.py:90,100 (отсюда же REGEN_MAX_USD_PER_RUN=5.0, settings.py:405). Завышенный резерв может преждевременно упереть в spend_cap_*.
- app/services/action_layer/builtin_montage_more.py:35-46 — IMAGE_BILLED_ATTEMPTS=3, VIDEO_BILLED_ATTEMPTS=4 (в комментарии "не проверено"), _VIDEO_CLIP_SECONDS=5, _LLM_USD_IN/OUT=1.6/8.4 (замер по одной модели), _LLM_CALL_MIN=0.01.
- app/services/kie_catalog.py:29 CREDIT_USD=0.005 (из документации kie.ai, сверено 3 пробами).

### A6. Таймауты, лимиты, окна — выбраны, не измерены — DEGRADES/COSMETIC
См. таблицу D.

### A7. Env-переключатели "только для тестов" в боевом коде — DEGRADES
- app/services/media_spend.py:139 и app/services/outsee_guard.py:69 — учёт расходов отключается, если в окружении есть PYTEST_CURRENT_TEST (включается атрибутом TEST_FORCE, media_spend.py:29). Боевой процесс под pytest-окружением теряет учёт; поведение зависит от внешней переменной. Работа: явный параметр вместо env.
- app/services/observation.py:1022-1078 и app/services/media_spend.py:151 — "реальный вызов" определяется по префиксу ключа `synthetic` (`key.startswith("synthetic")`). Хрупко: ключ с таким префиксом молча не попадёт в журнал. Работа: явный флаг режима.
- app/services/action_layer/attachments.py:154 `synthetic_ref` — вложение без файла для оценки планировщика и тестов; COSMETIC.

### A8. Дубликаты — COSMETIC
- app/services/montage_scene_direct.py:448 и app/services/montage_scene_improve.py:1892 — две одноимённые `improve_cell_scene`. Боевые вызовы только на improve (montage_action_gpt.py:365, 640); версия в direct нигде не вызывается (мёртвый код), из direct используются только build_kadry/pick_scene_characters (montage_scene_improve.py:1907). Работа: удалить мёртвую копию.
- Мелкие дубли хелперов: `_is_sqlite_locked` x3 (audio_align_job.py:31, audio_align_run.py:39, montage_board_apply.py:102); `_find_shot1_video/_find_shot2_video` (montage_board.py:90,104 и montage_board_assets.py:461,475); `_checkpoint_path/load_checkpoint/save_checkpoint` (img_pr_batches.py:133-155 и scene_design/runner.py:178,204).

### A9. Английские тексты в ответах API оператору — DEGRADES
- 60 HTTPException с английским detail в изменённых роутерах: app/web/routers/projects.py (16, напр. :199,215,241,251), prompt_files.py (10), artifacts.py (9, :47,49,92,118,140,143), project_ops.py (9), hitl.py (8, :67-102), node_groups.py (4), media_recovery.py:31, api.py:473. Рядом русские сообщения (action_layer/messages_ru.py, русские blockers в media_view.py:42-45) — смешение языков. Работа: коды ошибок + русский каталог в web/src.
- app/services/media_jobs.py (MediaPolicyError: "media route/source denied", "media policy: ...") попадает в denied/blockers — английские причины в UI.

### A10. Привязка к машине и конкретным проектам в скриптах — COSMETIC (не боевой путь), но 1 опасный
- scripts/_emit_chrono_dyn_canvas.py:9,12 — абсолютные `C:\Users\Admin\Desktop\video-pipeline\...` и `C:\Users\Admin\.cursor\...`; scripts/_chrono_dyn_scene_report.py:10-13,240 — PROJECT_ID=60 и имя проекта.
- scripts/_peek_img_pr_state.py:12 project_id=63; scripts/live_media_probe.py:57 PROBE_PROJECT_ID=900001; scripts/e2e_test_agentic_loop.py:126 PROJECT_ID=70100.
- Опасно: scripts/e2e_text_nodes_recovery.py:93-94 `DELETE FROM frames/hitl_requests WHERE project_id=2` и scripts/e2e_live_excel_gpt_recovery.py:79,88,91 (UPDATE workflow_runs WHERE project_id=2) — при запуске на боевой БД затронут реальный проект #2. Работа: защита "только временная БД"/параметр.

### A11. Фиксированные порты и хосты — COSMETIC
- app/services/agent_harness.py:23 HARNESS_HTTP_BASE="http://127.0.0.1:8765" (дефолт аргументов, :720, :855); app/settings.py:291 FLEET_HUB_URL тот же порт; app/settings.py:39 BROWSER_CDP_URL ...:29229 и дубль app/monitor/browser_watcher.py:32 "http://localhost:29229"; app/bots/morelogin.py:44 :40000. Все переопределяются, но дефолт жёсткий.

### A12. Заглушки в UI — боевых не найдено
- Просмотрены disabled/onClick в web/src. `prompt-files-panel.tsx:794` (`<button disabled>` при отсутствии ссылки на файл) и `:719` (пункт "Пока пусто") — штатные состояния, не заглушки. Постоянно отключённых кнопок и эндпоинтов с готовыми ответами в новых компонентах (action-layer, orchestrator, change-control) не найдено.
- Молчаливые `.catch(() => undefined)`: command-bar.tsx:40 (каталог действий не загрузился — командная строка без подсказок без сообщения), attachment-picker.tsx:69, flow-canvas.tsx:948, gen-assistant-panel.tsx:392,406,476, studio-workspace.tsx:451. DEGRADES, мелко.

### A13. Флаги, по умолчанию скрывающие готовый код
- app/settings.py:329 SCENE_DESIGN_ENABLED=False — нода scene_design работает как pass-through без GPT (orchestrator/steps/scene_design.py:150,284); включается группой (node_groups.py:262,348) или meta проекта. При приёмке шаг выглядит выполненным без работы.
- app/settings.py:380 CHAT_CODE_ACTIONS_ENABLED=False — правка кода из чата выключена (намеренно).
- Унаследованные: SOCIAL_PUBLISH_ENABLED=False (settings.py:243), TELEGRAM_ENABLED=False (settings.py:29).

### A14. except/pass в новом коде
Критичных нет: action_layer/attachments.py:370,401,413 — удаление файла/хвоста; media_spend.py и api_tracker_hook.py (8 мест: 73,97,181,346,388...) — осознанный fire-and-forget (docstring media_spend.py:3-7). Риск: сбой записи расхода невидим (нет счётчика пропусков) — DEGRADES. Прочие места по числу: outsee.py 11, gpt_api.py 10, vo_shot_expand.py 6, scene_space/rewrite.py 5, elevenlabs_api.py 4 — по месту не разбирались.

---
## B. Унаследованный (upstream) код

- app/orchestrator/auto_advance.py:1694-1696 (blame 77ebca9af, 2026-05-13): "Все остальные случаи (visual + AUTO_REVIEW_VISUAL=1) — TODO. Пока — auto-approve." Визуальное ревью hero/images/videos/audio/final без vision всегда одобряется. DEGRADES. Флаги AUTO_REVIEW_VISUAL / AUTO_REVIEW_VISUAL_KINDS (auto_advance.py:65,83) по умолчанию выключены.
- app/services/gpt_operator_client.py:1-5, 189-215, 300-314 — stub-оператор: если `gpt_api_enabled()` ложно (нет ключа/базы, settings.py:227-229), проверки возвращают детерминированный вердикт "pass" ("gpt-operator/api stub (нет боевого провайдера)"), текстовые шаги пишут файл-заглушку. Файл на ветке менялся (+158 строк), stub остался. Риск: пустой ключ превращает QC-гейт в всегда-зелёный без ошибки. DEGRADES (качество), потенциально BLOCKS для QC. Работа: в боевом режиме бросать ошибку.
- app/services/gpt_api.py:1643 — только лог "empty-ops stub", не заглушка.
- Селекторы UI-ботов смешивают EN/RU (bots/outsee.py:213-236, elevenlabs.py:71-92, chatgpt.py:607-613) — нужно для обоих языков интерфейса, не дефект.
- Жёсткие хосты: bots/chrome_cdp.py:53 ...:29229.

## C. Что искали и не нашли
NotImplementedError; эндпоинты с вшитыми ответами; `href="#"`; пустые onClick; mock/dummy/fake в боевом коде (fake только в имени scripts/e2e_action_layer_fake_paid.py).

---
## D. Константы-оценки (не замеры)

| Константа | Значение | Определена | Используется | Статус |
|---|---|---|---|---|
| ELEVENLABS_USD_PER_CHAR | 0.0003 | media_spend.py:25 | media_spend.py:438,452 | оценка |
| ELEVENLABS_SFX_USD_PER_SEC | 0.012 | media_spend.py:27 | media_spend.py:467 | оценка |
| PRICE_MARKUP | 3.0 | vibecode_catalog.py:15 | node_run_cost.py:212-219, api_tracker_hook.py:95 | оценка |
| _RATES (кроме gpt-5.6-sol) | см. файл | api_tracker_hook.py:25-37 | api_tracker_hook.py:81-93 | не замер |
| node_run_cost: CHARS_PER_TOKEN 2.0, PROMPT_MARGIN 1.2, HINT 1500, FOOTER 8000, SYSTEM 6000, REASONING 8000, CONTINUE 4000 | | node_run_cost.py:29-38 | builtin_wave3.py:405-464 | допущения |
| DEFAULT_REQUEST_ESTIMATE_USD | 0.05 | spend_guard.py:32 | actions_v1.py:588, regen_budget.py:100 | консервативная оценка |
| MEDIA_SUBMIT_ESTIMATE_USD | 0.30 | spend_guard.py:33 | actions_v1.py:588, regen_budget.py:90 | оценка |
| PLANNER_ESTIMATE_USD | 0.05 | spend_guard.py:34 | planner.py:568 | оценка |
| REGEN_MAX_USD_PER_RUN / PER_FRAME / PER_RUN | 5.0 / 3 / 12 | settings.py:402-405 | regen_budget.py | политика |
| IMAGE_BILLED_ATTEMPTS / VIDEO_BILLED_ATTEMPTS / _VIDEO_CLIP_SECONDS / _LLM_CALL_MIN | 3 / 4 / 5 / 0.01 | builtin_montage_more.py:35-46 | оценка стоимости плана | допущения ("не проверено") |
| _LLM_USD_IN/OUT | 1.6 / 8.4 | builtin_montage_more.py:37, api_tracker_hook.py:50 | оценка LLM | замер (1 модель, 3 пробы) |
| CREDIT_USD | 0.005 | kie_catalog.py:29 | budget.py:88-108, media_spend.py | документация + 3 пробы |
| owner budget default | default-100, max_requests 100 | decision_config.py:31 | actions_v1.py:398, managed_launch.py:175 | политика |
| MAX_STEPS (модельных шагов) | 3 | decision_v1.py:26 | decision_v1.py:390-824, watchdog.py:231 | политика |
| WAIT_SECONDS | 30 | decision_v1.py:28 | decision_v1.py | выбрано |
| lease | 120 с | decision_v1.py:538 | decision_v1.py | выбрано |
| RESUME_BUDGET_S | 100.0 | media_admission.py:45 | media_admission.py | выбрано под lease 120 |
| VERIFY_BUDGET_S / PER_FILE_TIMEOUT_S / MAX_ITEMS | 90 / 25 / 200 | media_verification.py:26-29 | media_verification.py | выбрано |
| RETRY_SAME_MAX | 1 | media_recovery.py:44 | media_recovery.py | политика |
| Planner: TIMEOUT_S 60, MAX_ATTEMPTS 2, RETRY_DELAY_S 0.5, MAX_TEXT_LEN 2000, MAX_FRAMES 80, MAX_QUEUE_OPS 40 | | planner.py:40-46, planner_context.py:18-22 | action_layer | выбрано |
| media limits: max_bytes 1KB..256MB, timeout_s 1..1200, max_polls 1..100, poll 0.01..60 | | media_jobs.py:179-187 | validate_media_config | границы, не замеры |
| ACTION_LAYER_APPROVAL_TTL_S 600, MAX_STEPS 20, STEP_TIMEOUT_S 900 | | settings.py:410-412 | action_layer | выбрано |
| Вложения: MAX_PER_REQUEST 5, MAX_BASE64_BYTES 512KB, MAX_TOTAL_UPLOAD_BYTES 512MB | | attachments.py:35, context.py:46-47 | action_layer | выбрано |
| Change control: MAX_STEP_PARAM_FACTOR 2.0, PROMPT_MIN/MAX_RATIO 0.25/4.0, PROMPT_MAX_CHARS 200000, MAX_OPEN_PER_PROJECT 50 | | change_control/tiers.py:36-62 | tiers | выбрано |
| MAX_AUTO_REGEN_PER_STEP | 2 | auto_advance.py:88 | auto_advance | политика |
| Зажим SFX длительности | 0.5..22 с | media_spend.py:467 | media_spend | выбрано |
| MAX_IMPROVE_OPS / MAX_IMPROVE_IMAGE_OPS | 25 | planner_rules.py:29, builtin_montage_more.py:39 | planner | выбрано (12 кадров x2 + 1) |

---
## E. Приоритетный список работ (для A)
1. BLOCKS: A1 — боевой профиль media (media_jobs.py:158,208-212; actions_v1.py:286; media_view.py:49).
2. BLOCKS: A2 — боевой runtime-конфиг decision (decision_config.py:188-198) и приёмка на реальном вызове.
3. DEGRADES: A3/A4/A5 — замерить ElevenLabs, Vibecode (наценка x3 или x1), media-submit; сейчас отчёты о расходах содержат оценки как "actual".
4. DEGRADES: A7 — убрать PYTEST_CURRENT_TEST и префикс `synthetic` как переключатели режима.
5. DEGRADES: A9 — русификация detail в API.
6. DEGRADES: B (gpt_operator_client stub) — без ключа падать, а не отдавать "pass".
7. COSMETIC: A8, A10 (кроме DELETE project_id=2), A11, A12 (молчаливые .catch).
