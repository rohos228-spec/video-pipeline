# Эконом-режим: статус 18.10.2026 ~14:12 МСК
main = 4776dd9a (#352 ac62863e, #356 a63f8a39, #379 4776dd9a).
Модель: claude-fable-5 на Kie недоступен с 13:41 (api_error / SSL EOF), ревью шли на claude-opus-5-5 (Kie). Kie с ПК нестабилен (429, SSL EOF).
Проверки: box /workspace/vp-box, venv /workspace/vp-venv, гейт /workspace/gate_sub.sh + /workspace/gnew.py, логи /workspace/gates/.
Вердикты моделей: C:\Users\Admin\fable\eco\t{2,2b,4,5}_b{1,2,3}.md.

ЗАДАЧА 3 #385: стоит на ветке #353 (base cursor/ws2-r0b-dryrun-af2a), 17 конфликтов с main (несёт старый перенос #342 на R0a 915b932f). Мёрж только после #353 (задача 7).
ЗАДАЧА 4 #386 @a7093a62: merge-tree чистый, гейт 120 файлов: 0 новых имён после фикса паритета grammar (не запушен; правка в /workspace/wt386: local_merge_trivial_shots как R0a + test_inactive_shots). Ревью Opus: BLOCKER —
  frame_tombstones.frame_is_active/shot_is_active — параллельный предикат (не activity_flags), карантин не учитывается во frame_is_active -> платная генерация кадра с карантин:True;
  merge_kadry_keep_missing переносит только активен False (удалён/карантин оживают);
  vo_shot_expand: inactive_ids по старым id, после переименования K{i} живой шот гаснет;
  chronology._append_missing_kadry «прилипает» активен False после restore; camera_expand._retain_missing_scenes теряет kids без id;
  montage_board_regen._regen_paid_blocked при ошибке -> False (пропускает платную генерацию);
  66 xfail в conftest (нестрогие) маскируют известные потери в зоне PR.
ЗАДАЧА 5 #388 @f542e50e: merge-tree чистый, гейт 136 файлов: 1 новое имя (r15_drift — флак, отдельно зелёный). Ревью Opus: BLOCKER —
  тот же параллельный предикат frame_tombstones; import_skip_reason смотрит только «удалён» (активен False оживает при импорте xlsx);
  «общий вид» гасит всю сцену (scene_shot_grammar:795-804); fold_inactive_shot_voiceover теряет закадр; montage_board_apply: paid_frame_skipped -> skip, правка теряется;
  restore не снимает leftover (кадр не возвращается); _archive_frames_as_leftover затирает пометку.
  #386 и #388 конфликтуют между собой (10 файлов) и оба трогают frame_tombstones (как и #365).
ЗАДАЧА 9 #371: в main нет только тесты + grammar.py; конфликт с паритетом R0a в grammar: тесты #371 требуют «порядок неактивного шота зарезервирован», R0a (main) двигает порядок у всех — решение по источнику истины нужно.
Не трогал: 6 (#365), 7 (#353 правка PATCH), 8 (WS3, 9–30 конфликтов, откат #389), 10 (WS4 — база #347 не в main), 11 (WS5).
Расход этого исполнителя: 100.24 credits ≈ $0.50 ≈ 42 ₽.
