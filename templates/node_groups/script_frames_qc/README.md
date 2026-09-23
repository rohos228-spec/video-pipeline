# Промты группы `script_frames_qc`

Изолированы от общих `prompts/05_excel_gpt` и `templates/excel_gpt_agents`.

Источник правды — **эта папка**. Ноды группы читают/пишут только отсюда.
В пикере «Работа с GPT» у чужих нод эти имена **не показываются**.
На нодах группы список — только эти файлы (`?group_id=script_frames_qc`).
Нода `fw_report` промта не берёт.

| файл | нода |
|------|------|
| `script_writer_ru.md` | fw_script |
| `main_action_from_bits_ru.md` | fw_action |
| `scenes_to_frames_ru.md` | fw_shots |
| `shots_qc_ru.md` | fw_qc |

Опционально (старые канвасы, нода не в новой группе):
`frame_prompts_continuity_ru.md` — fw_frames
`prompts_qc_continuity_ru.md` — старый QC промптов

Режиссура «Улучшить сцену» (монтаж, одна ячейка через все 6 нод):
`scene_improve_directing_ru.md` — добавляется к промтам fw_action / fw_shots / fw_qc
только в этом режиме. Источники — `docs/SCENE_DIRECTING_RULES.md`.

Нода без промта (код собирает HTML):
`fw_report` — после fw_qc, отчёт кадров.

Проверка без своего файла (правила — с промта источника, checkMode):
`fw_check_script` (после fw_script).

Каталог кадров T/X (`templates/shot_templates/`) — для монтажа, не для этой группы.
