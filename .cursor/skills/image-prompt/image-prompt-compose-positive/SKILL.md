---
name: image-prompt-compose-positive
description: >-
  Собирает финальный positive PROMPT: TEMPLATE pack + слоты + style vectors. Use when: слоты и style pack готовы; нужно написать PROMPT (без NEGATIVE).
---

# Compose positive PROMPT

Собирает **только PROMPT** (positive).

## When to use
- Есть `slots` + активный pack + scene_rules.
- Оркестратор шаг 4.

## Instructions
1. Возьми `PROMPT_TEMPLATE` из pack.
2. Подставь слоты: `[MAIN_SUBJECT]`, `[SETTING]`, … Пустой слот → удали фразу целиком, не пиши «none».
3. Вплети (коротко, без простыни) акценты из:
   - TEXTURE_VECTOR / RENDERING_VECTOR / LIGHT_VECTOR / COLOR_VECTOR
   — только если их ещё нет в TEMPLATE.
4. Закончи **Final style lock** = STYLE_CORE + ключевые слова STYLE_LABEL / COLOR.
5. Соблюдай scene_rules (одна сцена, no text, focal point).
6. Не пиши NEGATIVE здесь.

## Output
```text
PROMPT:
<готовый текст>
```

