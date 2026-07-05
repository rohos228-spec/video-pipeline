
# Universal Character Sheet Prompt Blueprint

Единая структура Blocks v2 для ноды `hero`.

## Активный шаблон
`prompts/steps/04_hero/template.md`

## Пресеты вариантов
`prompts/step-presets/hero.json`

## Правила
1. Техническая часть всегда описывает: что принимает, откуда читает, с чем взаимодействует, куда пишет и на что обратить внимание.
2. Полный исходный prompt не удаляется и хранится в `prompts/blocks/hero_source_full/`.
3. Reworked-файл не обязан копировать весь старый текст: он фиксирует структуру, пресет и ссылку на полный исходник.
4. Новые варианты добавляются через блоки `prompts/blocks/<category>/<name>.md` и preset в `step-presets`.

## Категории по умолчанию
- `world` → `cats_anthropomorphic`
- `visual_style` → `epic_pixel_cats_default`
- `character_anatomy` → `anthro_cat_sheet`
- `composition` → `vertical_9_16_character`
- `lighting` → `cinematic_chiaroscuro`
- `background_density` → `isolated_no_background`
- `negative` → `no_humans_no_text`
