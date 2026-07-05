
# Reworked: default.md → Blocks v2

## Назначение
Структурированная версия legacy-промта ноды `anim_pr` без потери исходных данных.

## Активный шаблон
`prompts/steps/07_animation/template.md`

## Пресет
`default` в `prompts/step-presets/anim_pr.json`

## Полный исходник
`prompts/blocks/anim_source_full/default_full.md`

## Переработанный промт

```md
# Шаг 7 — Animation prompts

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: project.xlsx, картинка текущего кадра (Nano Banana, {{VAR:ASPECT_RATIO_VIDEO}}) и ячейка закадрового текста этого кадра.
- Куда пишу: обновлённый project.xlsx, лист «план», строка 48 «промт для видео» — один промт на кадр.
- Внимание: не добавляй/не убирай объекты и не меняй стиль относительно референсной картинки.

## 2. СТИЛЬ
{{BLOCK:visual_style}}

## 3. ДВИЖЕНИЕ КАМЕРЫ
{{BLOCK:camera_motion}}

## 4. СЛОИ ДВИЖЕНИЯ
{{BLOCK:anim_motion_layers}}

## 5. СВЕТ
{{BLOCK:lighting}}

## 6. ДЛИТЕЛЬНОСТЬ И ФОРМАТ
{{BLOCK:anim_output_contract}}

## 7. ЗАПРЕТЫ
{{BLOCK:anim_negative}}
```

## Что вынесено в блоки
- `visual_style` → `epic_pixel_cats_default`
- `camera_motion` → `slow_push_in`
- `anim_motion_layers` → `three_plane_motion`
- `lighting` → `cinematic_chiaroscuro`
- `anim_output_contract` → `veo_single_prompt`
- `anim_negative` → `no_style_shift`
