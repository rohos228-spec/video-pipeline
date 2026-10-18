# Промты группы `script_frames_qc`

Группа «Каркас → площадка → шоты + QC». Цепочка нод:

**Каркас → Площадка → Шоты → QC кадров → Стиль → Отчёт**

Изолированы от общих `prompts/05_excel_gpt` и `templates/excel_gpt_agents`.
В пикере «Работа с GPT» у чужих нод эти имена **не показываются**.
На нодах группы список — только эти файлы (`?group_id=script_frames_qc`).
Нода `fw_report` промта не берёт.

| нода (id) | подпись | промт (вариант) | файл |
|-----------|---------|-----------------|------|
| `n_excel_gpt_fw_script` | Каркас | `scene_skeleton_agent` | `prompts/scene_design/scene_skeleton_agent.md` |
| `n_excel_gpt_fw_action` | Площадка | `action` | `prompts/scene_design/action.md` |
| `n_excel_gpt_fw_shots` | Шоты | `scenes_to_frames_ru` | `templates/node_groups/script_frames_qc/scenes_to_frames_ru.md` |
| `n_excel_gpt_fw_qc` | GPT: QC кадров | `shots_qc_ru` | `templates/node_groups/script_frames_qc/shots_qc_ru.md` |
| `n_excel_gpt_fw_style` | Стиль | `style_noir_watercolour_16_09` (по умолчанию) | `templates/node_groups/script_frames_qc/styles/style_*.md` |
| `n_excel_gpt_fw_report` | Отчёт: кадры | — | код собирает HTML |

id нод (`fw_*`) не меняли — на суффиксы завязан раннер. Поменялись подписи
и промты. Каркас и площадка берут промты агентов scene_design прямо из
`prompts/scene_design/` (копии в этой папке нет; правка в Studio пишет туда же).
Карта имён — `SCRIPT_FRAMES_QC_SHARED_PROMPTS` в `app/services/prompt_library.py`.

- **Каркас** — закадр целиком → сцены (закадр, место, персонажи, предметы,
  речь, сцена) + база персонажей и предметов.
- **Площадка** — только стартовая площадка (зоны, предметы, проходы, люди) и
  `меняет`. Не главное_действие, не кадры, не камера.
- **Шоты** — кадры: план, ракурс, высота, старт/конец, точность
  («одна картинка» / «старт/конец»), промт / промт_старт / промт_конец по
  заготовкам родительского, конечного и дочернего кадра (до 4700 символов).

Вне цепочки (лежат здесь, нодами группы не ставятся):
`script_writer_ru.md` — старый сценарист (биты), бывший промт fw_script;
`main_action_from_bits_ru.md` — пишет только `главное_действие`, не площадку;
`frame_prompts_continuity_ru.md` — fw_frames (старые канвасы);
`prompts_qc_continuity_ru.md` — старый QC промптов.
Проверка `fw_check_script` в новую группу не входит (на старых канвасах
остаётся как была).

Режиссура «Улучшить сцену» (монтаж, одна ячейка):
`scene_improve_directing_ru.md` — добавляется к промтам fw_action / fw_shots / fw_qc
только в этом режиме. Источники — `docs/SCENE_DIRECTING_RULES.md`.

Площадка (план места сверху) проверяется и чинится кодом
(`app/services/scene_plan.py`). Правила — `docs/SCENE_DIRECTING_RULES.md`
§ «Площадка» и `docs/NODE_GROUP_RULES.md`.

Каталог кадров T/X (`templates/shot_templates/`) — для монтажа, не для этой группы.

**Стиль** (`fw_style`, код, без GPT) — выбранный в Studio файл
`styles/style_*.md` дописывается после сцены к промту главного кадра (K1
VO-родителя; поле `промт_картинки`, иначе `промт_старт`) и в
`frames.image_prompt` этой строки. Исходная сцена — в `промт_без_стиля`,
вариант — в `стиль`; повторный прогон стиль не дублирует. Лимит 4877
(`OUTSEE_PROMPT_TARGET_BODY_CHARS`): режется сцена по предложению, стиль
никогда; стиль длиннее 4677 — ошибка. Отчёт: `excel_gpt_uploads/n_excel_gpt_fw_style/style_report.json`.
Варианты: `style_noir_watercolour_16_09` (§7 агента «нуар акварель 16.09»),
`style_archival_noir_watercolor_aug` (STYLE_HEAD+TAIL img_pr, август),
`style_knitted_textile` (KNITTED head+tail). Дети (K2/K3) не трогаются.
