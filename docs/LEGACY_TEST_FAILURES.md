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
| `test_text_llm_provider` (3) + `test_db_v2_api::test_orchestrator_chat_settings_actions` | 4 | tokenrouter/Kimi удалён из каталога (`_PROVIDERS={"kie","vibecode"}`), остались мёртвые ветки | b + c-минор | исправлено: убраны ветки tokenrouter в `text_llm_catalog.py` и текст ошибки в `gpt_client.py`; тесты проверяют, что tokenrouter отклоняется и не выбирается из env; db_v2-тест использует `vibecode`. Остатки tokenrouter в `settings.py`/`gpt_api.py`/`error_catalog.py` теперь недостижимы, не удалялись |
| Промпты: `test_plan_validation`, `test_chatgpt_xlsx` | 2 | Контракт `PROMPT_CONTRACT.md` запрещает «заполни лист план/приложи xlsx»; footer в коде уже DB/текстовый | c (промпты) + b | исправлено: `prompts/blocks/plan_output_contract/xlsx_plan_timing.md` и `prompts/steps/01_plan/template.md` указывают «Общий план», без xlsx; ассерты `test_chatgpt_xlsx` приведены к текущему footer. Меняет текст промтов - стоит просмотреть владельцу |
| `test_prompt_composer::test_compose_step_no_dangling_placeholders_with_defaults` | 1 | `{{VAR:EXCEL_GPT_SHEET}}`/`EXCEL_GPT_TASK` не имели значений по умолчанию | c | исправлено: дефолты в `DEFAULT_VARS` (`"план"` / `""`, как `ENRICH_n_*`); код нигде не задаёт эти переменные, откуда их должен брать excel_gpt - вопрос владельцу |
| Зависели от порядка: `test_scene_design_agent_chunks` (2), `test_scene_space_migrate`, `test_step_cancel::test_stop_flag_visible_across_fresh_import`, `test_xlsx_preview_used_range`, `test_shot_menu_xlsx_fallback` | 6 | Утечка: `tests/test_project_root.py::test_settings_db_not_under_web` делал `importlib.reload(app.settings)` и подменял глобальный `app.settings.settings` без восстановления | b (изоляция) | исправлено: тест создаёт `Settings()` без reload. Подтверждено: со старой версией файла 4 из 5 проверенных падают в сочетании с `test_project_root`, с новой - проходят; в полном прогоне все 6 зелёные |
| `test_auto_advance_parity` (3 оставшихся: `enrich_cap_2`, `enrich_cap_5`, `auto_mode_advances...`) | 3 | `_next_running_with_enrich_cap` (auto_advance.py:416) больше нигде не вызывается - продвижение идёт через граф, с пустым графом `next` = None; `maybe_auto_advance` в тесте возвращает False | b/c неясно | открыт (нужно решение владельца: cap enrich-слотов теперь только через граф?) |
| Промпты проверки: `test_check_analysis` (2) | 2 | Нет `prompts/check_operator/scene_design/default.md` и в промте `videos` нет check-id `clips_present` | c (контент промптов) | открыт |
| Web-grep: `test_canvas_node_merge`, `test_xlsx_studio_full_sheet` (2) | 3 | Ассерты по подстрокам исходников `web/src` (`fitView=...`, `XLSX_STUDIO_MAX_COLS = 120`, `setTab("excel")`) - UI переписан | b | открыт (`web/` не трогался по условию задачи) |
| `test_check_report_txt::test_check_fix_writeback_applies_tsv` | 1 | Excel writeback намеренно отключён (`xlsx_text_writeback.py:560`), тест на старый путь | b | открыт |
| `test_excel_gpt_upload_preview::test_resolve_operator_upload_replaces_edge_xlsx` | 1 | `resolve_operator` не возвращает загруженный `new.xlsx` в `files` (ветка DB-SoT `project_file`) | b/c неясно | открыт |
| `test_montage_board_frames` (3), `test_montage_scene_anchors` (1) | 4 | Разделение сцены создаёт 3 кадра вместо ожидаемых 1/32/2 и 2 якоря вместо 1 - дизайн split изменился (`montage_board_frames.py`) | b/c неясно | открыт |
| `test_shot_menu` (4), `test_scene_design_cells` (1), `test_scene_grammar_batches` (1) | 6 | Логика VO-ячеек/хронометража/диагностики усечённого JSON изменилась (`—` вместо текста, 1.5 вместо 3.0, `ok` вместо `json_truncated`) | b/c неясно | открыт |
| `test_pipeline_step_order::...img_pr_explicit_wipes_image_prompt`, `test_run_step_modes::...keeps_images` | 2 | `start_step(img_pr, explicit_ui_start=True)` вызывает очистку с `force_wipe=False` и не стирает `image_prompt`; `auto_mode` после ▶ остаётся False | b/c неясно | открыт |

## Баги продукта (класс c)

Исправлены: `artifact_recovery.py` / `vision_check_loop.py` (`await session.delete`, было сделано до этого раунда; в предыдущей версии
документа значились как открытые - устарело), `generation_storage.py` (кэш списка), `generate_hero.py` (вечный цикл волн),
tokenrouter-остатки в `text_llm_catalog.py`/`gpt_client.py`, `prompt_composer.DEFAULT_VARS`, контракт плана в `prompts/`.

Открыты:

1. `prompts/check_operator/` - нет промта проверки для ноды `scene_design`, в промте `videos` нет check-id `clips_present`
   (`test_check_analysis`, 2). Нужен авторский контент проверок, не угадывался.
2. `app/orchestrator/auto_advance.py:416` `_next_running_with_enrich_cap` - мёртвый код: `enrich_slots_count`
   больше не ограничивает цепочку enrich вне графа/excel_gpt-цепочки (`test_auto_advance_parity`, 3). Нужно решение владельца; не менялось.
3. Недостижимые остатки tokenrouter в `app/settings.py`, `app/services/gpt_api.py`, `app/services/error_catalog.py` - можно удалить отдельным шагом.

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
