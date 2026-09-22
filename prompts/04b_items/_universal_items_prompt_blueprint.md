
# Universal Предметы Prompt Blueprint

Единая структура Blocks v2 для ноды `items`.

## Активный шаблон
`prompts/steps/04b_items/template.md`

## Пресеты вариантов
`prompts/step-presets/items.json`

## Правила
1. Техническая часть всегда описывает: что принимает, откуда читает, с чем взаимодействует, куда пишет и на что обратить внимание.
2. Полный исходный prompt не удаляется и хранится в `prompts/blocks/items_source_full/`.
3. Reworked-файл не обязан копировать весь старый текст: он фиксирует структуру, пресет и ссылку на полный исходник.
4. Новые варианты добавляются через блоки `prompts/blocks/<category>/<name>.md` и preset в `step-presets`.

## Категории по умолчанию
- `visual_style` → `epic_pixel_cats_default`
- `lighting` → `cinematic_chiaroscuro`
- `background_density` → `isolated_no_background`
- `negative` → `no_humans_no_text`
