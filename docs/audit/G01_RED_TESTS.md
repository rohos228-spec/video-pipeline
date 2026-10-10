# G01: красные тесты legacy-набора (29 шт.)

Что сделано: каждый падавший тест разобран по коду и по `git log`, причина найдена, тест отнесён к классу:

- A - тест устарел, код изменён намеренно (коммит и документ это подтверждают) -> обновлён тест.
- B - реальная ошибка в коде с очевидным исправлением -> исправлен код, тест остался как доказательство.
- C - нужно решение владельца -> ничего не менялось, ниже по два варианта.

Платных вызовов, сети, `.env` не было. Файлы гонялись по одному процессу pytest.

Итого из 29: A = 12, B = 10 (один дефект кода, одна правка), C = 7 (3 решения владельца).
Кроме того, один зелёный до правки тест (`test_shots_vo_rejects_dangling_preposition`) держался на том же дефекте B
и после исправления потребовал обновить фикстуру (класс A, в счёт 29 не входит).

## Ключевая причина большинства A/B

Коммит `b1b8b454` (2026-10-02, "VO-cap 26-80") поднял минимум закадра на обычный кадр с 13 до 26 символов
(`SHOT_VO_MIN` в `app/services/scene_shot_grammar.py`; норма записана в `docs/plan_rules_script_frames_qc.md`, строки 227-235).
Тестовые данные писались под 13 и стали короче нормы. Тот же коммит поменял лестницу планов (R-PLAN-STEP) и убрал
запись действия шота в `main_action`. Часть тестов этого не отразила.

## Таблица

| Тест | Класс | Причина | Что сделано / какое решение нужно |
|---|---|---|---|
| test_apply_ops_batches.py::test_repair_same_place_parents_makes_coverage_valid | B | `shots_coverage_ops_reason` передавал в `repair_shot_vo_lengths` КОПИЮ списка кадров (`[s for s in shots if ...]`). Функция склеивает короткий закадр с соседом и удаляет (`pop`) кадр-пустышку, но удаление шло в копии: у соседа текст менялся, а короткий кадр оставался в ops, закадр дублировался, и проверка длины ругалась на тот самый короткий кадр | Исправлен код: `app/services/apply_ops_batches.py` (около строки 1326) - репейр получает сам список ops, если там только словари. Тест не менялся |
| test_apply_ops_batches.py::test_shots_coverage_accepts_master_and_child | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_accepts_parent_on_same_place | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_allows_mixed_templates_per_scene | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_rejects_adjacent_same_plan | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_rejects_all_independent_invented_places | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_rejects_same_place_all_independent | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_rejects_same_place_same_plan | B | то же | то же |
| test_apply_ops_batches.py::test_shots_coverage_rejects_same_t_on_neighbor_scenes | B | то же | то же |
| test_apply_ops_batches.py::test_shots_vo_accepts_name_title_and_normal_chunk | B | то же (титр "Дарья Салтыкова." 16 симв. теперь корректно склеивается с соседним кадром) | то же |
| test_apply_ops_batches.py::test_shots_vo_rejects_dangling_preposition (вне 29) | A | Был зелёным только из-за дефекта B: хвост "землёй." не склеивался. После исправления короткий хвост склеивается с предыдущим кадром, обрубок исчезает | Фикстура: второй кусок "землёй и всем нажитым скарбом до нитки." (>= 26). Смысл теста (обрубок предлога отклоняется) сохранён |
| test_scene_plan.py::test_accusative_and_vo_split_at_punctuation | A | `_split_vo` режет закадр надвое, только если обе половины >= `SHOT_VO_MIN` (26). Закадр "Он бежал по улице, не разбирая дороги, к своему дому." = 53 симв., двух половин по 26 не выйдет -> `None` | Фикстура `_street()` и сам тест: закадр удлинён до 92 симв. |
| test_scene_plan.py::test_closed_door_gets_threshold_shot_and_entry_is_shown | A | та же причина: кадр "порог двери" не вставляется, пока нельзя разрезать закадр | то же |
| test_scene_plan.py::test_neighbour_cell_is_read_only_context | A | то же | то же |
| test_scene_plan.py::test_ops_use_neighbour_frames_from_whole_context | A | то же | то же |
| test_scene_plan.py::test_improve_scene_sees_next_scene_door | A | то же | то же |
| test_node_trace.py::test_code_passes_diary_explains_scene_plan_fixes | A | то же: общий закадр `VO` в файле короче нормы | Закадр удлинён (`_street` импортируется из test_scene_plan) |
| test_script_frames_qc_space_e2e.py::test_space_plan_through_six_node_group | A | (1) Харнесс `scripts/run_script_frames_qc_space_test.py` держал сцену 1 с закадром 53 симв. - порог двери не вставлялся (`S1-K2` отсутствовал). (2) Утверждение `act["S1-K1"] == "Иван бежит по улице"` расходилось с тем, что делает fake-QC: он переписывает K1 в "...к открытой двери дома", а код обязан вернуть "закрытой" | Харнесс: закадр сцены 1 удлинён (вход и второй шот). Тест: K1 начинается с "Иван бежит по улице", содержит "закрыт", не содержит "открытой" |
| test_montage_scene_improve.py::test_improve_writes_full_scene_card_and_shot_coverage | A | Фикстура GPT дала переход ДЕТАЛЬ -> СРЕДНИЙ (прыжок на 3 ступени). С b1b8b454 (R-PLAN-STEP, `docs/plan_rules_script_frames_qc.md`, строки 128-136) код выправляет такой прыжок до КРУПНЫЙ | Фикстура и ожидание: второй шот КРУПНЫЙ (легальная пара) |
| test_vo_shot_expand.py::test_apply_shot_meta_sets_action_when_empty | A | Намеренно: `_apply_shot_meta` больше НИКОГДА не пишет действие шота в `main_action` (комментарий в коде, b1b8b454) - иначе stub первого шага маскирует главное_действие | Тест проверяет, что `main_action` не появляется, а `shot01_action` пишется |
| test_vo_shot_expand.py::test_group_prompts_for_four_node_markup_exist | A | Шаблон `scenes_to_frames_ru.md` переписан (v14: "минимум на 30°", заголовка "Правило 30°" нет) - 12f4d347, 23426a95 | Проверяется "30°" |
| test_vo_shot_expand.py::test_enrich_xlsx_coverage_hook_includes_shots_frames_qc | A | Тест грепал исходник на `endswith(("_fw_shots", "_fw_frames", "_fw_qc"))`. С b1b8b454 QC вынесен в отдельную ветку (только sync полей; комментарий в коде: полная пересборка покрытия после QC сбрасывала правки плана/раскладки) | Ожидание: покрытие на `("_fw_shots", "_fw_frames")` и отдельная ветка `_fw_qc` |
| test_shot_templates.py::test_scenes_to_frames_resolves_v8_template | A | В шаблоне v14 нет фраз "видимый шаг"/"шаги действия" (вход теперь "главное_действие + площадка") | Проверяется "главное_действие" |
| test_prompt_library_topic.py::test_nii67_prompt_is_the_file_even_with_blocks_v2 | C | Тест (добавлен в b1b8b454) ждёт файл промта "Агент сценария НИИ 67" (вариант `nii67`). Такого файла в репозитории нет (`prompts/02_script/` содержит только `default.md` и `zakadrovyuTekst_long_story.md`); `make_script.py` сам отмечает, что `app/services/nii67_card.py` "never committed" | Решение. (1) Вернуть/написать `prompts/02_script/nii67.md` и модуль `nii67_card` (нужен авторский контент) - тест позеленеет без правок. (2) Считать режим НИИ 67 снятым: удалить тест и ветки `nii67` в `chatgpt_xlsx.py` и `xlsx_step_runners.py`; режим карточки НИИ 67 перестанет существовать |
| test_check_analysis.py::test_every_pipeline_node_type_resolves | C | Нет `prompts/check_operator/scene_design/default.md` (тип `scene_design` - легаси, канон `sd_agent`/`sd_assemble`). Нужен набор check-id и логика вердикта - механического шаблона нет | Решение. (1) Написать промт по образцу `images/default.md` (check-id, вердикт, footer `vp.check.v1`). (2) Исключить `scene_design` из списка типов в тесте; старые канвасы с нодой `scene_design` останутся без промта проверки |
| test_check_analysis.py::test_prompts_declare_stable_check_ids | C | `prompts/check_operator/videos/default.md` переписан под TXT-отчёт по 6-кадровым сеткам; id `clips_present`/`no_black_frames` в нём нет, а тест требует стабильные check-id | Решение. (1) Вернуть check-id в промт (старый вид: `git show ea7d49cf:prompts/check_operator/videos/default.md`). (2) Убрать `videos` из ожиданий теста; у отчётов проверки видео не будет стабильных id для сопоставления между прогонами |
| test_montage_board_frames.py::test_split_scene_at_keeps_numbers | C | Ручное "разделить сцену" (`split_montage_scene_at/_frames`) работает верно, но следующая сборка доски вызывает `relink_shot_roles_by_scene(resplit_places=True)` (`montage_board.py:1052`, d0eea2a0 "монтаж по месту генерации"), и кадры без смены места склеиваются обратно. Проверено: при отключённом relink проходят все 17 тестов обоих файлов | Решение (общее для 4 тестов). (1) Группировка по месту - дизайн: в тестах кадрам задаются разные `place`; ручной split по-прежнему отменяется при пересборке доски (UX-конфликт). (2) Защитить ручной split (флаг в `camera_subdivide`, который relink уважает): тесты не меняются, правка кода в relink |
| test_montage_board_frames.py::test_split_scene_all_each_frame_own_scene | C | то же | то же |
| test_montage_board_frames.py::test_split_all_visible_ids_hides_extra_shots | C | то же | то же |
| test_montage_scene_anchors.py::test_split_sends_each_anchor_to_scene_with_its_text | C | то же (якоря тоже склеиваются relink'ом обратно) | то же |

## Владельцу: решения

1. Ручной split сцены на монтажной доске против группировки по месту (4 теста). Варианты: (1) дизайн, тесты получают разные `place`; (2) защитить ручной split флагом.
2. Режим НИИ 67 (1 тест): вернуть файл промта и `nii67_card` либо удалить режим.
3. Промты проверки (2 теста): `scene_design` и `videos` - написать или вернуть check-id либо убрать из ожиданий.

Отдельно подтвердить норму: минимум закадра 26 симв. на обычный кадр и 10-30 на перебивку. Она зафиксирована в коде и в
`docs/plan_rules_script_frames_qc.md`, на ней основаны все исправления класса A. Если владелец вернёт 13, нужно откатить `SHOT_VO_MIN`
и прежние фикстуры, но это противоречит текущим промтам (`shots_qc_ru.md`, `scenes_to_frames_ru.md`).

## Изменённые файлы

Код: `app/services/apply_ops_batches.py` (одна правка, класс B).

Тесты и харнесс: `tests/test_apply_ops_batches.py`, `tests/test_scene_plan.py`, `tests/test_node_trace.py`,
`tests/test_script_frames_qc_space_e2e.py`, `tests/test_vo_shot_expand.py`, `tests/test_shot_templates.py`,
`tests/test_montage_scene_improve.py`, `scripts/run_script_frames_qc_space_test.py`.

Планировщик и прочее не затрагивались.
