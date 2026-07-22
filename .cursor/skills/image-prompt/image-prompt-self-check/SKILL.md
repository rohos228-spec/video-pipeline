---
name: image-prompt-self-check
description: >-
  Финальный гейт image prompt: стиль, слоты, no-text, одна сцена, есть PROMPT+NEGATIVE. Use when: проверить готовый image prompt перед отдачей пользователю.
---

# Self-check image prompt

Гейт качества. Если fail — укажи какой модуль повторить.

## Checklist
- [ ] Есть STYLE_LABEL / pack_id
- [ ] Есть PROMPT и NEGATIVE
- [ ] Сюжет только из слотов пользователя (нет чужого демо-сюжета pack’а)
- [ ] STYLE_LOCK не нарушен (не photoreal/anime/clean vector — по pack)
- [ ] Одна сцена / не collage-panels (если pack требует)
- [ ] TEXT_RULE соблюдён
- [ ] GORE_RULE соблюдён
- [ ] Focal point назван в PROMPT

## Output
```yaml
pass: true|false
fails: []   # что сломано
retry_skill: null|image-prompt-fill-scene-slots|...
```

Если `pass: true` — можно отдавать финальный блок пользователю (через orchestrate).

