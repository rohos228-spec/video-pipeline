
# Universal Excel #5 Prompt Blueprint

Единая структура Blocks v2 для ноды `enrich_5`.

## Активный шаблон
`prompts/steps/05e_enrich_5/template.md`

## Пресеты вариантов
`prompts/step-presets/enrich_5.json`

## Правила
1. Техническая часть всегда описывает: что принимает, откуда читает, с чем взаимодействует, куда пишет и на что обратить внимание.
2. Полный исходный prompt не удаляется и хранится в `prompts/blocks/enrich_source_full/`.
3. Reworked-файл не обязан копировать весь старый текст: он фиксирует структуру, пресет и ссылку на полный исходник.
4. Новые варианты добавляются через блоки `prompts/blocks/<category>/<name>.md` и preset в `step-presets`.

## Категории по умолчанию
- `enrich_role` → `xlsx_editor`
- `enrich_edit_rules` → `sheet_safe_edits`
- `enrich_source_policy` → `xlsx_task_only`
- `enrich_output_contract` → `return_full_xlsx`
- `enrich_self_check` → `no_structure_damage_gate`
