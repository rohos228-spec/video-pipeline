
# Universal Разбивка Prompt Blueprint

Единая структура Blocks v2 для ноды `split`.

## Активный шаблон
`prompts/steps/03_razbivka/template.md`

## Пресеты вариантов
`prompts/step-presets/split.json`

## Правила
1. Техническая часть всегда описывает: что принимает, откуда читает, с чем взаимодействует, куда пишет и на что обратить внимание.
2. Полный исходный prompt не удаляется и хранится в `prompts/blocks/split_source_full/`.
3. Reworked-файл не обязан копировать весь старый текст: он фиксирует структуру, пресет и ссылку на полный исходник.
4. Новые варианты добавляются через блоки `prompts/blocks/<category>/<name>.md` и preset в `step-presets`.

## Категории по умолчанию
- `split_role` → `voiceover_segmenter`
- `split_rules` → `microthought_cells`
- `forbidden_phrases` → `ai_cliches_ru`
- `split_output_contract` → `xlsx_row49`
- `split_self_check` → `no_broken_words_gate`
