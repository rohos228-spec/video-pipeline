# Reworked: knitted_2d → Blocks v2

## Назначение
Textile cut-paper / вязаный 2D

## Пресет
knitted_2d в step-presets/img_pr.json

## Переработанный промт

```md
# Шаг 6 — Image prompts по кадрам (Blocks v2)

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: приложенный project.xlsx + закадровый текст по кадрам и число кадров, уже посчитанные ботом.
- Куда пишу: обновлённый project.xlsx, лист «план», строка 45 «промт для картинки N» — по одному промту на кадр, колонки C..N (кадр 1 → C45, кадр 2 → D45, и т.д.).
- На что обратить внимание: единый мир/стиль по всем кадрам ролика, длина промта в лимите, ответ обязателен с вложением .xlsx.

## 2. ВХОД, СЦЕНА И ФАКТЫ
{{BLOCK:img_input_rules}}

{{BLOCK:img_scene_interpretation}}

{{BLOCK:img_context_logic}}

## 3. ГЕРОЙ И МИР
{{BLOCK:img_hero_policy}}

{{BLOCK:img_diversity_rules}}

{{BLOCK:world}}

{{BLOCK:character_anatomy}}

## 4. СТИЛЬ, КАДР И КОМПОЗИЦИЯ
{{BLOCK:visual_style}}

{{BLOCK:composition}}

{{BLOCK:camera_framing}}

{{BLOCK:background_density}}

{{BLOCK:img_composition_discipline}}

## 5. СВЕТ, ТЕКСТ И ЗАПРЕТЫ
{{BLOCK:lighting}}

{{BLOCK:img_prop_text_rules}}

{{BLOCK:negative}}

## 6. ФОРМАТ ВЫВОДА И САМОПРОВЕРКА
{{BLOCK:img_output_contract}}

{{BLOCK:img_self_check}}

Длина промта: {{VAR:PROMPT_LEN_MIN}}–{{VAR:PROMPT_LEN_MAX}} символов. Aspect: {{VAR:ASPECT_RATIO_VIDEO}}.
```
