
# Reworked: default.md → Blocks v2

## Назначение
Структурированная версия legacy-промта ноды `items` без потери исходных данных.

## Активный шаблон
`prompts/steps/04b_items/template.md`

## Пресет
`default` в `prompts/step-presets/items.json`

## Полный исходник
`prompts/blocks/items_source_full/default_full.md`

## Переработанный промт

```md
# Шаг 4b — Реф-картинки предметов

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: описание конкретного предмета из `project.item_descriptions[]` (один предмет за один запуск).
- Куда пишу: одну картинку 16:9 на предмет → `data/videos/<slug>/items/predmet<N>_<uuid>.png`.
- На что обратить внимание: единый визуальный стиль между всеми предметами проекта, нейтральный фон без лишних деталей, без текста и водяных знаков.

## 2. РОЛЬ И ЗАДАЧА
Ты — генератор реф-картинок предметов для видеоролика. {{VAR:ITEM_STYLE_NOTE}}

## 3. ВИЗУАЛЬНЫЙ СТИЛЬ
{{BLOCK:visual_style}}

## 4. ОСВЕЩЕНИЕ И ФОН
{{BLOCK:lighting}}

{{BLOCK:background_density}}

## 5. ЗАПРЕТЫ И ФОРМАТ ВЫВОДА
{{BLOCK:negative}}

Композиция: предмет занимает 60–70% кадра по центру, aspect 16:9. Сгенерируй один промт для image model, на английском, без markdown.
```

## Что вынесено в блоки
- `visual_style` → `epic_pixel_cats_default`
- `lighting` → `cinematic_chiaroscuro`
- `background_density` → `isolated_no_background`
- `negative` → `no_humans_no_text`
