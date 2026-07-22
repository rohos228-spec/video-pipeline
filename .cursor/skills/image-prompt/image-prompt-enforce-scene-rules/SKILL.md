---
name: image-prompt-enforce-scene-rules
description: >-
  Проверяет/правилa сцены image prompt: одна сцена не коллаж, TEXT_RULE, GORE_RULE, composition. Use when: перед сборкой PROMPT нужно зафиксировать запреты и композиционный каркас.
---

# Enforce scene rules

Накладывает **правила сцены** из pack + универсальные.

## When to use
- После слотов + pack, до compose-positive.
- Пользователь просит «без текста / без коллажа / без жести».

## Rules to apply
Из pack: `TEXT_RULE`, `GORE_RULE`, `COMPOSITION_VECTOR`, `STYLE_LOCK_RULE`.

Универсально (если pack не противоречит мягче):
1. **One scene** — не collage, не несколько панелей/кадров в одном изображении.
2. **No readable text** — буквы/цифры/логотипы/подписи выкл (если TEXT_RULE так говорит).
3. **Single focal point** — один главный акцент.
4. **No invented plot** — только слоты из fill-scene-slots.
5. **Gore** — только если пользователь явно просил И GORE_RULE позволяет.

## Output
```yaml
scene_rules:
  unified_scene: true
  allow_readable_text: false
  allow_gore: false
  focal_point: <from slots>
  composition_notes: <1-2 lines from COMPOSITION_VECTOR>
  lock_reminders:
    - ...
```

