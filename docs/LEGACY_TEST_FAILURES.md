# Legacy-набор тестов: разбор падений

Команда (из корня репозитория, venv `.venv`):

```
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*' \
  python -m pytest tests -q -p no:cacheprovider --ignore=tests/observe_v1 --tb=no -rf
```

`GIT_CONFIG_*` нужен, потому что каталог репозитория принадлежит другому пользователю Windows
(git: dubious ownership) - без этого падает `test_ai_pack_verify_ok`. Платных/реальных запросов
к провайдерам не делалось.

## Счётчики (реальные прогоны)

| Прогон | failed | passed |
|---|---|---|
| baseline `12d61fad` (прежний полный прогон) | ~94 | - |
| HEAD до правок (прежний полный прогон, `_cmp/h_full.txt`) | 69 | - |
| HEAD до правок, повтор только падавших файлов (`_cmp/h_before_utf.txt`) | 61 | 406 |
| HEAD после первого раунда правок (`_cmp/h_final.txt`) | 40 | 2662 |
| после второго раунда, полный прогон (`$CLAUDE_JOB_DIR/tmp/final_legacy.txt`) | 22 | 2693 |
| после второго раунда + правка `test_video_regen_delete` (файл прогнан отдельно, полный прогон не повторялся) | **21** | - |

Полный прогон «после» выполнен дважды подряд с одинаковым списком из 40 падений
(кроме одного сетевого флейка `test_ensure_public_yandex_does_not_fallback`, он исправлен, см. ниже).
Полный прогон «до» с теми же переменными окружения повторно не делался; 61 - это повтор
только файлов из старого списка падений.

Классы: (a) окружение, (b) устаревший тест / тест, не совпадающий с текущим дизайном,
(c) подозрение на реальный баг продукта.

## Таблица групп

| Группа | Тестов | Причина (одна строка) | Класс | Статус |
|---|---|---|---|---|
| Нет ключей провайдеров (`GptApiUnavailable`, `OUTSEE_API_KEY пуст`, `kie: нет API ключа`): `test_media_polling_retry` (2), `test_outsee_video_fallback` (3), `test_montage_regen_api` (2), `test_montage_board_ops` (1) | 8 | Тесты мокают транспорт, но гейты проверяют наличие ключа; на dev-машине его даёт локальный `.env`, в чистом чекауте его нет | a | исправлено: фикстура `fake_provider_keys` в `tests/conftest.py` (фиктивные ключи, хосты `*.invalid`), подключена в 7 файлов; перепрогнано, проходят |
| `test_gen_queue_order` | 4 | Очередь после a3e88809 пропускает paused/user_stop слоты (`gen_queue_incomplete_earlier`, docstring), тесты ждали блокировку; reconcile-тест строился на paused-слоте; tick-тест шёл в реальный LLM verdict-review и в БД observer | b + a | исправлено: ожидания приведены к документированному поведению (2 теста переименованы/перевёрнуты на `is None`, в reconcile первый слот сделан runnable), verdict-review и `managed_project_enabled` замоканы; 16/16 проходят |
| `test_auto_advance_parity` (6) | 6 | Заглушка сессии падала на `execute()`, а `_apply_approve` теперь грузит граф (`load_graph_for_project`) и вызывает `managed_project_enabled` (Observe-v1) | b | частично исправлено: заглушка возвращает пустой результат, `managed_project_enabled` замокан - 3 из 6 проходят. Остались 3 (см. ниже) |
| `test_assembly_sync::test_cut_clip_does_not_resize` | 1 | grep по исходнику `-vf`, а `_cut_clip` теперь легитимно использует `-vf` для замедления и scale при заданных target_w/h | b | исправлено: поведенческая проверка (перехват команды ffmpeg), проходит |
| `test_bug_report` | 1 | Отчёты пишутся в `docs/bug-reports/`, тест ждал `баги/` | b | исправлено |
| `test_nvidia_asr_mono` | 1 | Имя mono-файла теперь содержит хэш `voice_<hash>_mono16k.wav` | b | исправлено (regexp) |
| `test_project_gen_models` | 1 | `seedance_2` переименован в `seedance_2_5` в `generation_options.VIDEO_GENERATORS` | b | исправлено |
| `test_vibecode_catalog` | 1 | В группе `openai` теперь 5 моделей, а не 4 (каталог расширен) | b | исправлено (счётчик) |
| `test_outsee_image_ui_parity` | 1 | Каталог `IMAGE_GENERATORS` сокращён (нет `gpt_image_1_5`, `nano_banana_pro` и др.) | b | исправлено: ожидаемая таблица slug'ов = текущий каталог |
| `test_outsee_veo_settings` | 2 (1 стабильный + 1 флейк) | После bf1c5355 без Yandex S3 используются публичные fallback-хосты и ошибка называется `frame upload failed`; тесты мокали не все хосты (`0x0`, `catbox`) и **ходили в реальную сеть** (реальная загрузка на catbox/0x0) | b + a | исправлено: замоканы все хосты, обновлён regexp |
| `test_check_forward_files::test_explicit_forward_paths` | 1 | Вместе с явно выбранным файлом теперь пересылаются отчёты проверки (emitKinds) | b | исправлено: первый - выбранный файл, лишнего `skip.png` нет, добавки только текстовые |
| `test_studio_launcher_encoding` | 2 | `scripts/studio.ps1` без UTF-8 BOM и с символом U+2014 (ломает PS 5.1) | c (скрипт запуска, не `app/`) | исправлено: добавлен BOM, тире заменено на `-`; 8/8 проходят |
| `test_start_step_resets_downstream_noderuns` | 1 | monkeypatch на удалённый символ `project_steps.sync_project_xlsx` | b | исправлено (`raising=False`) |
| `test_prompt_history::test_api_modified_uses_meta_not_mtime` | 1 | `_prompt_modified` без meta-времени возвращает mtime файла, тест ждал `None` | b | исправлено |
| `test_ai_pack::test_ai_pack_verify_ok` | 1 | `git rev-parse` в `scripts/verify_ai_pack.py` падает на dubious ownership | a | исправлено двояко: `GIT_CONFIG_*` в окружении и толерантный вызов git в скрипте (строка HEAD информативная) |
| `test_artifact_recovery_newer_disk` | 1 | `session.delete(a)` без `await` в `artifact_recovery.py` | c | исправлено в продукте раньше (`await session.delete`), тест проходит |
| `test_create_history_cache` | 1 | **Баг продукта:** `list_generation_files` кэшировал под сигнатурой, снятой ДО скана, а скан сам пишет sidecar (`update_sidecar`) и сдвигает mtime/gen - кэш не попадал на повторе | c | исправлено: сигнатура пересчитывается после скана (`generation_storage.py`) |
| `test_scene_regen_delete`, `test_video_regen_delete` | 2 | Мок `session.delete` был `MagicMock`, а `vision_check_loop` корректно делает `await session.delete(...)` (`AsyncSession.delete` - корутина) | b | исправлено: `AsyncMock` (в старом списке не значились - вскрылись после await-фикса продукта) |
| `test_hero_outbound_attachments::test_run_excel_missing_ref_file_exits_without_loop` | 1 | **Баг продукта:** после волны `_run_excel` безусловно ставил `generating_hero` поверх `frames_ready`, выставленного дочерней сессией (нет файлов рефов) - выход по статусу не срабатывал, цикл волн вечный. Тест ещё и не знал про отдельную `SessionLocal` | c + b | исправлено: `generate_hero.py` выходит, если после refresh статус `frames_ready`; тест подставляет заглушку `app.db.SessionLocal` |
| `test_split_xlsx_keep_existing` (2), `test_script_voiceover_attach` (1) | 3 | Раннеры split/script перешли на DB-SoT: `telegram_style_ask_with_files`, ответ - apply-ops/текст, `project.xlsx` не скачивается и (для script) не прикладывается (`xlsx_step_runners.py`) | b | исправлено: тесты переписаны под текущий поток |
| `test_text_llm_provider` (3) + `test_db_v2_api::test_orchestrator_chat_settings_actions` | 4 | tokenrouter/Kimi удалён из каталога (`_PROVIDERS={"kie","vibecode"}`), остались мёртвые ветки | b + c-минор | исправлено: убраны ветки tokenrouter в `text_llm_catalog.py` и текст ошибки в `gpt_client.py`; тесты проверяют, что tokenrouter отклоняется и не выбирается из env; db_v2-тест использует `vibecode`. Остатки tokenrouter в `settings.py`/`gpt_api.py`/`error_catalog.py` позже удалены (поля `TOKENROUTER_*` убраны, `.env` с ними не ломает загрузку: `extra="ignore"`; из `tests/test_gpt_api.py` убраны monkeypatch на `tokenrouter_api_key`) |
| Промпты: `test_plan_validation`, `test_chatgpt_xlsx` | 2 | Контракт `PROMPT_CONTRACT.md` запрещает «заполни лист план/приложи xlsx»; footer в коде уже DB/текстовый | c (промпты) + b | исправлено: `prompts/blocks/plan_output_contract/xlsx_plan_timing.md` и `prompts/steps/01_plan/template.md` указывают «Общий план», без xlsx; ассерты `test_chatgpt_xlsx` приведены к текущему footer. Меняет текст промтов - стоит просмотреть владельцу |
| `test_prompt_composer::test_compose_step_no_dangling_placeholders_with_defaults` | 1 | `{{VAR:EXCEL_GPT_SHEET}}`/`EXCEL_GPT_TASK` не имели значений по умолчанию | c | исправлено: дефолты в `DEFAULT_VARS` (`"план"` / `""`, как `ENRICH_n_*`); код нигде не задаёт эти переменные, откуда их должен брать excel_gpt - вопрос владельцу |
| Зависели от порядка: `test_scene_design_agent_chunks` (2), `test_scene_space_migrate`, `test_step_cancel::test_stop_flag_visible_across_fresh_import`, `test_xlsx_preview_used_range`, `test_shot_menu_xlsx_fallback` | 6 | Утечка: `tests/test_project_root.py::test_settings_db_not_under_web` делал `importlib.reload(app.settings)` и подменял глобальный `app.settings.settings` без восстановления | b (изоляция) | исправлено: тест создаёт `Settings()` без reload. Подтверждено: со старой версией файла 4 из 5 проверенных падают в сочетании с `test_project_root`, с новой - проходят; в полном прогоне все 6 зелёные |
| `test_auto_advance_parity` (3 оставшихся: `enrich_cap_2`, `enrich_cap_5`, `auto_mode_advances...`) | 3 | `_next_running_with_enrich_cap` мёртв после 6e3202c9 («always follow canvas edges»: следующий шаг только по графу); третий тест упирался в `clamp_status_to_data` на пустой БД | b | исправлено: cap-тесты переписаны на граф канваса (цепочка enrich_1..N -> image_prompts, `enrich_slots_count` на выбор не влияет, + тест «нет ребра - статус не меняется»), auto_mode-тест отключает гейты данных; 9/9 проходят. Сама `_next_running_with_enrich_cap` не удалялась (см. ниже) |
| Промпты проверки: `test_check_analysis` (2) | 2 | Нет `prompts/check_operator/scene_design/default.md`; `videos/default.md` (локальная библиотека, fcd5f50a) переписан под TXT-отчёт по 6-кадровым сеткам со scores, check-id `clips_present` в нём больше нет | c/b неясно (контент промптов) | открыт: см. «Открытые» п.1 |
| Web-grep: `test_canvas_node_merge`, `test_xlsx_studio_full_sheet` (2) | 3 | Ассерты по подстрокам `web/src`: `fitView` теперь с `!savedViewport` (ee0c827b), `XLSX_STUDIO_MAX_COLS = 300` (87ab0bff), вкладка Excel открывается через `onClick={() => setTab("excel")}`/`promptFocus` | b | исправлено: ассерты приведены к текущему исходнику (с комментариями-доказательствами), `web/` не менялся; 2/2 и 5/5 проходят |
| `test_check_report_txt::test_check_fix_writeback_applies_tsv` | 1 | Excel writeback намеренно отключён (bd038612, `test_xlsx_text_writeback` проверяет RuntimeError), но `_apply_check_fix_writeback` вызывал его без защиты и валил весь прогон проверки | b + c-минор | исправлено: продукт ловит RuntimeError, пишет warning, удаляет копию `project_fixed.xlsx`; тест проверяет изящную деградацию (отчёт не падает, xlsx не тронут, файла нет); 13/13 проходят |
| `test_excel_gpt_upload_preview::test_resolve_operator_upload_replaces_edge_xlsx` | 1 | `project_file` (дефолт для assist) = DB SoT: xlsx со входа убирается целиком (5a9ac471) | b | исправлено: вытеснение стрелки загрузкой проверяется в `outputMode=text`, добавлен тест `project_file` (xlsx нет, warning DB SoT); 6/6 проходят |
| `test_montage_board_frames` (3: `split_scene_at_keeps_numbers`, `split_scene_all_each_frame_own_scene`, `split_all_visible_ids_hides_extra_shots`), `test_montage_scene_anchors::test_split_sends_each_anchor_to_scene_with_its_text` (1) | 4 | Причина найдена: сам split (`split_montage_scene_at/_frames`) отрабатывает верно (проверки результата и `is_shot_child` проходят), а `build_montage_board` затем вызывает `relink_shot_roles_by_scene(resplit_places=True)` (d0eea2a0, 2026-09-27 "монтаж по месту генерации"), который группирует кадры по месту картинки и склеивает обратно соседние кадры без смены места (тесты места не задают); подтверждено: с замоканным relink все 17 тестов обоих файлов проходят. Докстринг `assigned_place_run_keys`: "То же место на соседнем VO-родителе не режет", `parent_uuid` группу не закрывает | b/c неясно | **открыт (решение владельца)**: ручное «разделить сцену» в одном месте отменяется при следующей сборке доски (UX-конфликт с преднамеренной группировкой по месту). Варианты: (1) считать это дизайном и в тестах задать кадрам разные `place`; (2) защитить ручной split (напр. флаг в `camera_subdivide`, который relink уважает). Тесты не менялись, продукт не менялся |
| `test_shot_menu` (3), `test_scene_design_cells` (1), `test_scene_grammar_batches` (1) | 5 | Намеренные изменения продукта, тесты отстали: (a) `split_text_into_parts` режет закадр по смыслу, хвостовой шот без новой фразы остаётся пустым (`—`) - 4e5edc8e, закреплено в `tests/test_camera_expand.py`; (b) хронометраж кадра = len(закадр)/14 сим/сек, БД-`duration_seconds` игнорируется - 2de8f0b0 (соседние тесты там же обновлены, этот пропущен); (c) обрезанный JSON теперь спасается `db_apply.close_truncated_json` (`_salvaged_partial`, d0a2486f/78d74100, `test_db_apply_uuid_repair`), поэтому diagnose даёт `ok` | b | исправлено: тесты приведены к текущему поведению (shot_menu: входной закадр из 2-3 фраз + новый тест на одну фразу -> хвост `—`; design_cells: 1.5/2.9 с комментарием; grammar_batches: `ok`+`_salvaged_partial` и неуспасаемый обрыв остаётся `json_truncated`); 20/20, 15/15, 3/3 проходят |
| `test_pipeline_step_order::...img_pr_explicit_wipes_image_prompt`, `test_run_step_modes::...keeps_images` | 2 | Намеренно: 21765ef9 (2026-09-16) "Play resumes without wipe" - wipe только при `force_wipe=True` (комментарий в `project_steps.start_step`); `auto_mode` при ▶ включал тот же коммит, но fcd5f50a (2026-09-22) "no auto-jump on play" убрал (комментарий "▶ не включает auto_mode") | b | исправлено: img_pr-тест проверяет, что явный ▶ сохраняет промт, а `force_wipe=True` стирает; modes-тест ожидает `auto_mode is False`; 6/6 и 3/3 проходят |

## Баги продукта (класс c)

Исправлены: `artifact_recovery.py` / `vision_check_loop.py` (`await session.delete`, было сделано до этого раунда; в предыдущей версии
документа значились как открытые - устарело), `generation_storage.py` (кэш списка), `generate_hero.py` (вечный цикл волн),
tokenrouter-остатки в `text_llm_catalog.py`/`gpt_client.py`, `prompt_composer.DEFAULT_VARS`, контракт плана в `prompts/`.

Открыты:

1. `prompts/check_operator/` (`test_check_analysis`, 2) - **не менялось, нужен авторский контент:**
   - `scene_design/default.md` отсутствует; тест (`test_every_pipeline_node_type_resolves`) требует лишь, чтобы
     промт резолвился и содержал `vp.check.v1` (footer добавляется автоматически) и слово `verdict`, но НЕ задаёт критерии:
     набор check-id и логику вердикта для дизайна сцен (legacy-тип, канон теперь `sd_agent`/`sd_assemble`) надо придумать - механического
     шаблона нет. Нужно: файл `prompts/check_operator/scene_design/default.md` по образцу `images/default.md`
     (вход / критерии `checks` по id / логика вердикта) либо решение, что тип `scene_design` исключается из теста.
   - `videos/default.md` в рабочем дереве - пользовательский промт под TXT-отчёт (scores, db_patch, 6-кадровая сетка), он
     противоречит footer'у JSON и не содержит `clips_present`/`no_black_frames`. Тест `test_prompts_declare_stable_check_ids`
     ждёт блок «Критерии» старого шаблона (`clips_present`, `no_black_frames`). Нужно решение владельца: вернуть check-id в
     промт (как в `git show ea7d49cf:prompts/check_operator/videos/default.md`) или убрать `videos` из ожиданий теста.
2. `app/orchestrator/auto_advance.py` `_next_running_with_enrich_cap` - по-прежнему мёртвый код (после 6e3202c9 нигде не вызывается,
   enrich-слоты задаёт граф канваса); тесты переписаны, функция оставлена, т.к. вне задачи менять рантайм. Может быть удалена вместе с
   `_ENRICH_READY_TO_INDEX`. Побочный эффект рефакторинга: поле `enrich_slots_count` (Telegram/mass) на авто-продвижение больше не влияет.

## Что изменено (только тесты/харнесс/скрипты)

- `tests/conftest.py` - фикстура `fake_provider_keys` (фиктивные ключи, не перетирает существующие).
- Подключение фикстуры: `test_gen_queue_order`, `test_montage_regen_api`, `test_montage_board_ops`,
  `test_media_polling_retry`, `test_outsee_video_fallback`.
- Обновлены ожидания: `test_gen_queue_order`, `test_auto_advance_parity`, `test_assembly_sync`, `test_bug_report`,
  `test_nvidia_asr_mono`, `test_project_gen_models`, `test_vibecode_catalog`, `test_outsee_image_ui_parity`,
  `test_outsee_veo_settings`, `test_check_forward_files`, `test_start_step_resets_downstream_noderuns`,
  `test_prompt_history`.
- `scripts/studio.ps1` - BOM + ASCII-дефис; `scripts/verify_ai_pack.py` - толерантный `git rev-parse`.
- Второй раунд: продукт - `app/services/generation_storage.py`, `app/orchestrator/steps/generate_hero.py`,
  `app/services/text_llm_catalog.py`, `app/services/gpt_client.py`, `app/services/prompt_composer.py`;
  промпты - `prompts/blocks/plan_output_contract/xlsx_plan_timing.md`, `prompts/steps/01_plan/template.md`;
  тесты - `test_project_root` (изоляция), `test_scene_regen_delete`, `test_video_regen_delete`, `test_hero_outbound_attachments`,
  `test_split_xlsx_keep_existing`, `test_script_voiceover_attach`, `test_text_llm_provider`, `test_db_v2_api`, `test_chatgpt_xlsx`.
- Не затронуты: `tests/observe_v1`, `web/`.
