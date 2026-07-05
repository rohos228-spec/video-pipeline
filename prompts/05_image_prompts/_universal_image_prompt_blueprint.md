# Universal Image Prompt Blueprint

Шаблон для создания новых промтов шага `05_image_prompts` / compose `06_image_prompts` по единой структуре Blocks v2.

Активный шаблон сборки:

```text
prompts/steps/06_image_prompts/template.md
```

Пресеты вариантов (какой набор блоков у каждого `.md` слева в конструкторе):

```text
prompts/step-presets/img_pr.json
```

## Градация категорий (6 разделов активного template)

| Раздел | Подпись | Категории | Назначение |
|--------|---------|-----------|------------|
| 1 | Техническая часть | статический текст | xlsx строка 45, файл-ответ, ограничения шага |
| 2 | Вход, сцена и факты | `img_input_rules`, `img_scene_interpretation`, `img_context_logic` | Как читать ячейку, визуализировать смысл и не выдумывать |
| 3 | Герой и мир | `img_hero_policy`, `img_diversity_rules`, `world`, `character_anatomy` | ГГ, разнообразие, мир, анатомия |
| 4 | Стиль, кадр и композиция | `visual_style`, `composition`, `camera_framing`, `background_density`, `img_composition_discipline` | Стиль, 9:16, планы, среда |
| 5 | Свет, текст и запреты | `lighting`, `img_prop_text_rules`, `negative` | Свет, надписи/бумаги, negative |
| 6 | Формат и самопроверка | `img_output_contract`, `img_self_check` | xlsx, нумерация, gate перед ответом |

## Два типа промтов

### Pipeline (default, norm, pixel_v8, trash_polka_v25…)

Полный путь: закадровый текст → промты в xlsx. Нужны уровни 1–8 (часть можно `omit_slots` в пресете).

### Style-template (plasticine, knitted, noir short…)

Только стиль + свет + negative + формат пары PROMPT/NEGATIVE. Уровни 1–2 и часть 4 — `omit_slots`.

## Как создать новый вариант

1. Определи тип: pipeline или style-template.
2. Создай или выбери блоки в `prompts/blocks/<category>/<name>.md`.
3. Добавь пресет в `prompts/step-presets/img_pr.json` (`blocks`, `omit_slots`, `aliases`).
4. Сохрани переработанный вариант: `prompts/05_image_prompts/reworked_<name>_blocks_v2.md`.
5. Полный исходник (опционально): `prompts/blocks/img_source_full/<name>_full.md`.
6. Обнови `prompts/05_image_prompts/_reworked_prompts_index.md`.

## Готовый каркас нового reworked-файла

```md
# Reworked: <исходник> → Blocks v2

## Назначение
<одно предложение>

## Пресет
`<preset_id>` в `step-presets/img_pr.json`

## Переработанный промт

```md
# Шаг 6 — Image prompts

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
…

## 2. ВХОД, СЦЕНА И ФАКТЫ
{{BLOCK:img_input_rules}}

… (см. template.md)
```

## Переменные

| VAR | По умолчанию |
|-----|----------------|
| `PROMPT_LEN_MIN` | 500 |
| `PROMPT_LEN_MAX` | 4800 или 5000 |
| `ASPECT_RATIO_VIDEO` | 9:16 |
