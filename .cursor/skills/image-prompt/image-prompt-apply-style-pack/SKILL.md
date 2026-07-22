---
name: image-prompt-apply-style-pack
description: >-
  Подключает style pack (plasticine/knitted-2d/noir-bloody/trash-polka): векторы стиля без сюжета. Use when: выбрать стиль image prompt, загрузить STYLE_LOCK/COLOR/LIGHT/PROMPT_TEMPLATE.
---

# Apply style pack

Загружает **только стиль**. Сюжетные слоты не переписывает.

## When to use
- Нужно выбрать/подключить visual style pack.
- Оркестратор шаг 2.

## When NOT to use
- Писать финальный PROMPT (compose-positive).
- Менять сюжет.

## Packs (файлы)
Читай один файл из `references/packs/`:
- `plasticine.md`
- `knitted-2d.md`
- `noir-bloody.md`
- `trash-polka.md`

## Instructions
1. Определи pack по словам пользователя (пластилин→plasticine, вязаный/текстиль→knitted-2d, нуар/кровавый dirty→noir-bloody, trash polka/полька→trash-polka).
2. Если неясно — спроси; не смешивай два pack’а.
3. Прочитай файл pack целиком.
4. Верни активный style context:

```yaml
pack_id: plasticine|knitted-2d|noir-bloody|trash-polka
STYLE_LABEL: ...
STYLE_CORE: ...
STYLE_LOCK_RULE: ...
LIGHT_VECTOR: ...
COLOR_VECTOR: ...
COMPOSITION_VECTOR: ...
TEXTURE_VECTOR: ...
RENDERING_VECTOR: ...
TEXT_RULE: ...
GORE_RULE: ...
PROMPT_TEMPLATE: ...
NEGATIVE_CORE: ...
SLOT_KEYS: [...]
```

5. Не подставляй слоты сюжета на этом шаге.

