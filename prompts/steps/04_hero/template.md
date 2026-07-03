# Шаг 4 — Character Sheet (Hero)

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: описание героя, собранное из плана/сценария проекта — {{VAR:HERO_DESCRIPTION}}.
- Куда пишу: одну картинку character sheet {{VAR:ASPECT_RATIO_HERO}} — сохраняется в `data/videos/<slug>/characters/`.
- На что обратить внимание: строгая консистентность персонажа на всех ракурсах (лицо/причёска/одежда/цвета одинаковы), чистый однотонный фон без декора и текста.

## 2. РОЛЬ И ЗАДАЧА
Ты — генератор character sheet для одного персонажа. Описание героя: {{VAR:HERO_DESCRIPTION}}.

## 3. МИР
{{BLOCK:world}}

## 4. ВИЗУАЛЬНЫЙ СТИЛЬ
{{BLOCK:visual_style}}

## 5. АНАТОМИЯ И КОМПОЗИЦИЯ ЛИСТА
{{BLOCK:character_anatomy}}

{{BLOCK:composition}}

## 6. СВЕТ И ФОН
{{BLOCK:lighting}}

{{BLOCK:background_density}}

## 7. ЗАПРЕТЫ И ФОРМАТ ВЫВОДА
{{BLOCK:negative}}

Сгенерируй один промт для Nano Banana / image model (на английском), без markdown.
