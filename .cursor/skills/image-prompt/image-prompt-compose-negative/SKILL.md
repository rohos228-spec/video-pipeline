---
name: image-prompt-compose-negative
description: >-
  Собирает NEGATIVE PROMPT: NEGATIVE_CORE pack + STYLE_LOCK + context negatives. Use when: positive уже есть; нужен negative list.
---

# Compose NEGATIVE PROMPT

Собирает **только NEGATIVE**.

## When to use
- После compose-positive.
- Оркестратор шаг 5.

## Instructions
1. Старт = `NEGATIVE_CORE` из pack.
2. Добавь запреты из `STYLE_LOCK_RULE` (как короткие negative-фразы).
3. Добавь `CONTEXT_SPECIFIC_NEGATIVES` из слотов (если есть).
4. Если `allow_readable_text: false` — гарантируй: text, letters, numbers, logos, watermark, captions.
5. Если `allow_gore: false` — gore, explicit violence.
6. Дедуплицируй, comma-separated, без простыни-повествования.

## Output
```text
NEGATIVE PROMPT:
<a, b, c, ...>
```

