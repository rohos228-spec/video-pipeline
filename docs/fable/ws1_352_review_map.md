# WS1 · карта блокеров 1–3 ревью #352

Снято с HEAD `cursor/r0a-pipeline-base-v2-dfad` после правок WS1.
Соседние ветки не переписывались.

| Ветка | PR | HEAD |
|---|---|---|
| `cursor/r0c-b-keep-frames-900c` | #354 | `60790928` |
| `cursor/r0c-b-no-physical-deletes-900c` | #365 | `c2aa2b34` |
| `cursor/ws1-db-apply-shots-keep` | #368 | `e091a8eb` |

#368 собран на более раннем #354. Для «не удалять кадры» источник — #365 и линия #354 `60790928`. Из #368 берётся только merge шотов в apply-ops (`4b367cf6`). Diff #368 против `60790928` целиком не переносить.

Имени `test_delete_coverage_child_marks_not_deletes` не было ни в одном из трёх PR. На #352 оно добавлено в `c5fe3fae`. Близкий тест #365 — `test_delete_coverage_child_marks_parent_shot_chain` (`c60e1e61`, есть и в #354/#368).

`_remove_kadry_id` на #352 больше не вырезает шот: помечает `активен: False`. В #365 функция удалена (`60c35b1e`). При пересборке #354/#365 этот шим надо свести, а не оставить оба поведения.

## Уже закрыто в соседних PR

| Место | Коммит | Тест / смысл | Где |
|---|---|---|---|
| `delete_coverage_child` помечает цепочку шота родителя | `c60e1e61` | `test_delete_coverage_child_marks_parent_shot_chain` | #354 #365 #368 |
| чужое место в align → `активен: False` | `c7ff9375` | `test_align_shots_marks_foreign_place_inactive` | #365 |
| ребёнок без шота не теряет свои `кадры` | `79eeda3a` | `test_promote_child_without_shot_keeps_old_kadry` | #365 |
| trim/cap помечает мосты | `bbdd47f2` | `test_trim_to_cap_marks_bridges` | #354 #365 #368 |
| catalog fill не выкидывает несовпавший шот | `ab743c11` | `test_fill_kadry_keeps_unmatched` | #354 #365 #368 |
| пустая цепочка сцен не режет поздние шоты | `eaf5ba1b` | поздние сцены остаются | #354 #365 #368 |
| merge GPT-шотов не теряет несовпавшие | `8aede38f` | unmatched в `merge_gpt_shots_with_action` | #354 #365 #368 |
| срез scene-plan не выкидывает не-dict / вне среза | `e54c456a` | slice keeps non-dict | #354 #365 #368 |
| `_clear_shot_action` помечает, не чистит лестницу | `913bcbfe` | `test_clear_shot_action_marks_shots_inactive` | #365 |
| вырезалка id снята | `60c35b1e` | `test_remove_kadry_id_is_gone` | #365 |
| rewrite VO оставляет пропавшие шоты | `d750b708`, `c2aa2b34` | `keep_soft_kadry` / `merge_kadry_keep_missing` | #365 |
| relink не теряет id вне новой лестницы | `99f93683` | `merge_kadry_keep_missing` | #365 |
| склейка flattened coverage держит лишние id | `e4ee9c81` | extra ids | #365 |
| живые next-рёбра при пересборке VO | `709c69a7` | keep live next-edges | #365 |
| архив свёрнутых coverage-кадров | `352fd3e9` | archive collapsed | #354 #365 #368 |
| монтажные кадры помечаются и возвращаются | `9b156985` | mark + restore | #354 #365 #368 |
| `_drop_frame_rows` помечает | `b0a0c446` | mark instead of delete | #354 #365 #368 |
| откат promotion при восстановлении головы | `e7950892` | restore deleted head | #354 #365 |
| leftover в архив, не delete | `d9b2a7d7` | `_archive_frames_as_leftover` | #354 #365 #368 |
| apply-ops: пропавшие `кадры` с `активен: False` | `4b367cf6` | merge в `db_apply` | #368 |
| `ensure_single_seed_vo_cell` не удаляет лишние ячейки | уже на #352 | `tests/test_seed_vo_cell.py::test_seed_keeps_split_cells` | #352 |

На #352 `FrameEdge(next)` при пересборке VO по-прежнему снимается (`vo_shot_expand`). Это закрывает `709c69a7` в #365, здесь не дублировалось.

## Закрыто на #352, потому что покрытия не было

| Место | Коммит | Тест |
|---|---|---|
| `delete_coverage_child` физически удалял Frame / PromptVersion / FrameText / FrameEdge | `c5fe3fae` | `test_delete_coverage_child_marks_not_deletes` |
| `strip_non_scene_kadry` выкидывал `кадры`/`shots` | `dceb911e` | `test_strip_non_scene_kadry_archives_and_keeps_list` |
| `apply_shot_voiceover_to_cells` схлопывал QC-куски на меньшее число кадров | `dceb911e` | `test_apply_voiceover_keeps_qc_pieces_when_ladder_is_longer` |
| `ensure_wide_before_dialogue` перенумеровывал все K и переносил `parent_id` | `dceb911e` | `test_ensure_wide_keeps_ids_and_clears_insert_prompts` |
| `_renumber_scene` / порог переписывал уникальные id | `dceb911e` | `test_renumber_scene_does_not_rewrite_unique_ids` |
| `_promote_first_child` не копировал `кадры` родителя | `dceb911e` | `test_promote_first_child_keeps_head_kadry` |
| `merge_montage_scenes` терял цепочку правой сцены | `dceb911e` | `test_merge_archives_right_scene_chain` |
| `stage_qc` принимал кандидата без `якорь_n` | `dceb911e` | `test_stage_qc_copies_anchor_and_rejects_shorter_candidate` |

Обрубленный закадр на кадр по-прежнему режется по клаузам (`test_apply_shot_voiceover_uses_clauses_not_word_split`). QC-лестница длиннее числа кадров не схлопывается.
