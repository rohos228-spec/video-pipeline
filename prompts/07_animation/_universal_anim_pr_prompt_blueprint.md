
# Universal Animation Prompt Blueprint

Единая структура Blocks v2 для ноды `anim_pr`.

## Активный шаблон
`prompts/steps/07_animation/template.md`

## Пресеты вариантов
`prompts/step-presets/anim_pr.json`

## Правила
1. Техническая часть всегда описывает: что принимает, откуда читает, с чем взаимодействует, куда пишет и на что обратить внимание.
2. Полный исходный prompt не удаляется и хранится в `prompts/blocks/anim_source_full/`.
3. Reworked-файл не обязан копировать весь старый текст: он фиксирует структуру, пресет и ссылку на полный исходник.
4. Новые варианты добавляются через блоки `prompts/blocks/<category>/<name>.md` и preset в `step-presets`.

## Категории по умолчанию
- `visual_style` → `epic_pixel_cats_default`
- `camera_motion` → `slow_push_in`
- `anim_motion_layers` → `three_plane_motion`
- `lighting` → `cinematic_chiaroscuro`
- `anim_output_contract` → `veo_single_prompt`
- `anim_negative` → `no_style_shift`
