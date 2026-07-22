---
name: image-prompt-fill-scene-slots
description: >-
  Извлекает сюжетные слоты image prompt из брифа/voiceover/кадра. Use when: нужно заполнить MAIN_SUBJECT/SETTING/ACTION/FOCAL_POINT без стиля; разложить сцену на переменные для шаблона.
---

# Fill scene slots

Только **сюжетные переменные**. Стиль не трогай.

## When to use
- Есть тема / закадровый текст / описание кадра → нужно разложить на слоты.
- Оркестратор вызвал шаг 1.

## When NOT to use
- Уже просят финальный PROMPT (это compose-positive).
- Нужно выбрать visual style (это apply-style-pack).

## Instructions
1. Прочитай бриф пользователя.
2. Заполни словарь слотов (ключи зависят от pack; базовый минимум всегда):
   - `MAIN_SUBJECT` — главный объект/фигура
   - `ACTION_OR_STATE` — действие или состояние
   - `SETTING` — место/среда
   - `FOCAL_POINT` — на чём глаз
   - `MOOD` / `EMOTIONAL_MOOD` — настроение
   - доп. слоты pack’а (если pack уже известен) — см. `SLOT_KEYS` в pack
3. **Запрещено выдумывать** персонажей, документы, локации вне брифа.
4. Если чего-то критичного нет — поставь `null` и пометь `missing: [...]`.
5. Не вставляй STYLE_CORE / цвета / текстуры стиля в слоты.

## Output
```yaml
slots:
  MAIN_SUBJECT: ...
  ACTION_OR_STATE: ...
  SETTING: ...
  FOCAL_POINT: ...
  MOOD: ...
missing: []
notes: "1 предложение"
```

