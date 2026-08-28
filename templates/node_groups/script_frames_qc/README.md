# Промты группы `script_frames_qc`

Изолированы от общих `prompts/05_excel_gpt` и `templates/excel_gpt_agents`.

Источник правды — **эта папка**. Ноды группы читают/пишут только отсюда.
В пикере «Работа с GPT» у чужих нод эти имена **не показываются**.
На нодах группы список — только эти пять файлов (`?group_id=script_frames_qc`).

| файл | нода |
|------|------|
| `script_writer_ru.md` | fw_script |
| `main_action_from_bits_ru.md` | fw_action |
| `scenes_to_frames_ru.md` | fw_shots |
| `frame_prompts_continuity_ru.md` | fw_frames |
| `prompts_qc_continuity_ru.md` | fw_qc |

Каталог кадров T/X: `templates/shot_templates/shot_templates.json`.
