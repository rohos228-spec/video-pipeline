
# Reworked: XLSX_Agent_V7_2_Row52_Strengthened.txt.md → Blocks v2

## Назначение
Структурированная версия legacy-промта ноды `enrich_1` без потери исходных данных.

## Активный шаблон
`prompts/steps/05a_enrich_1/template.md`

## Пресет
`xlsx_agent_v7_2_row52_strengthened_txt` в `prompts/step-presets/enrich_1.json`

## Полный исходник
`prompts/blocks/enrich_source_full/xlsx_agent_v7_2_row52_strengthened_txt_full.md`

## Переработанный промт

```md
# Доп. работа с Excel #1

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: приложенный `project.xlsx` в его текущем состоянии, лист «{{VAR:ENRICH_1_SHEET}}».
- Куда пишу: **обязательно** обновлённый `project.xlsx` целиком, приложенный файлом в ответ.
- Внимание: не переименовывать листы, не удалять и не сдвигать уже заполненные строки/колонки.

## 2. РОЛЬ И ЗАДАЧА
{{BLOCK:enrich_role}}

{{VAR:ENRICH_1_TASK}}

## 3. ПРАВИЛА РЕДАКТИРОВАНИЯ
{{BLOCK:enrich_edit_rules}}

## 4. ИСТОЧНИКИ И ОГРАНИЧЕНИЯ
{{BLOCK:enrich_source_policy}}

## 5. ФОРМАТ ВЫВОДА
{{BLOCK:enrich_output_contract}}

## 6. САМОПРОВЕРКА
{{BLOCK:enrich_self_check}}
```

## Что вынесено в блоки
- `enrich_role` → `xlsx_editor`
- `enrich_edit_rules` → `sheet_safe_edits`
- `enrich_source_policy` → `xlsx_task_only`
- `enrich_output_contract` → `return_full_xlsx`
- `enrich_self_check` → `no_structure_damage_gate`
