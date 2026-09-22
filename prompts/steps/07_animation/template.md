
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
