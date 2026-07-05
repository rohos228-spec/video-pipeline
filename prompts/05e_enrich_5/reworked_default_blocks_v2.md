
# Reworked: default.md → Blocks v2

## Назначение
Структурированная версия legacy-промта ноды `enrich_5` без потери исходных данных.

## Активный шаблон
`prompts/steps/05e_enrich_5/template.md`

## Пресет
`default` в `prompts/step-presets/enrich_5.json`

## Полный исходник
`prompts/blocks/enrich_source_full/default_full.md`

## Переработанный промт

```md
# Доп. работа с Excel #5

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: приложенный `project.xlsx` в его текущем состоянии, лист «{{VAR:ENRICH_5_SHEET}}».
- Куда пишу: **обязательно** обновлённый `project.xlsx` целиком, приложенный файлом в ответ.
- Внимание: не переименовывать листы, не удалять и не сдвигать уже заполненные строки/колонки.

## 2. РОЛЬ И ЗАДАЧА
{{BLOCK:enrich_role}}

{{VAR:ENRICH_5_TASK}}

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
