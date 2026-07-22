---
name: image-prompt-orchestrate
description: >-
  Оркестратор сборки image PROMPT+NEGATIVE из модульных скилов. Use when: сделай image prompt / промт для картинки / в стиле plasticine|knitted|noir|trash polka; нужна цепочка: слоты → стиль → positive → negative → проверка.
---

# Image prompt orchestrate

Ты **дирижёр**. Не пиши промт сразу целиком — пройди модули по порядку.

## When to use
- Пользователь просит image prompt / промт для картинки / «в таком стиле».
- Нужно собрать PROMPT + NEGATIVE из сюжета + style pack.

## When NOT to use
- Правки кода пайплайна, outsee, монтажа.
- Полный xlsx img_pr на 100 кадров (это Studio Blocks v2).

## Pipeline (обязательный порядок)
1. `/image-prompt-fill-scene-slots` — вытащи сюжетные слоты из запроса (без стиля).
2. `/image-prompt-apply-style-pack` — выбери pack: `plasticine` | `knitted-2d` | `noir-bloody` | `trash-polka` (или спроси).
3. `/image-prompt-enforce-scene-rules` — единая сцена, TEXT_RULE, GORE_RULE, composition lock.
4. `/image-prompt-compose-positive` — собери PROMPT по TEMPLATE + слоты + векторы стиля.
5. `/image-prompt-compose-negative` — собери NEGATIVE.
6. `/image-prompt-self-check` — гейт; если fail → вернуться к нужному модулю.

## Defaults
- Стиль не указан → спроси одним вопросом; не угадывай.
- Aspect 9:16 если не сказано иначе.
- Сюжет только из запроса пользователя; демо-примеры из pack не копировать.

## Final user-facing output
Только после self-check pass:

```text
STYLE: …
PROMPT:
…
NEGATIVE PROMPT:
…
```

Коротко: pack + какие слоты заполнены.

