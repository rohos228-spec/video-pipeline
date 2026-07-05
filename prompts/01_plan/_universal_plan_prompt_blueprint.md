
# Universal План ролика Prompt Blueprint

Единая структура Blocks v2 для ноды `plan`.

## Активный шаблон
`prompts/steps/01_plan/template.md`

## Пресеты вариантов
`prompts/step-presets/plan.json`

## Правила
1. Техническая часть всегда описывает: что принимает, откуда читает, с чем взаимодействует, куда пишет и на что обратить внимание.
2. Полный исходный prompt не удаляется и хранится в `prompts/blocks/plan_source_full/`.
3. Reworked-файл не обязан копировать весь старый текст: он фиксирует структуру, пресет и ссылку на полный исходник.
4. Новые варианты добавляются через блоки `prompts/blocks/<category>/<name>.md` и preset в `step-presets`.

## Категории по умолчанию
- `plan_role` → `shorts_planner`
- `plan_structure` → `viral_60s_timeline`
- `plan_voice_tone` → `human_clear_pitch`
- `forbidden_phrases` → `ai_cliches_ru`
- `plan_output_contract` → `xlsx_plan_timing`
- `plan_self_check` → `plan_quality_gate`
