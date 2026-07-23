---
name: rule-no-text
description: >-
  ПРАВИЛО: на картинке нельзя читать текст. Включи вместе со style-*, если буквы/цифры/лого запрещены.
---

# Правило: без читаемого текста

Человек включил это правило сам.

## Что запрещено на картинке
- буквы, слова, цифры
- читаемые вывески, документы, подписи
- логотипы, watermark, captions, title card

Знаки/таблички, если нужны по сюжету — **пустые**, без символов.

## Что сделать в промте
- В `PROMPT` явно: no letters, no words, no numbers, no readable signs, no logos.
- В `NEGATIVE PROMPT` обязательно: text, letters, words, numbers, readable signs, captions, logo, watermark.
