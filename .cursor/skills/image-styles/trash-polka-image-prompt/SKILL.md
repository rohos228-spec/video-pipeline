---
name: trash-polka-image-prompt
description: >-
  Собирает image PROMPT+NEGATIVE в стиле Trash Polka Noir Comic Grunge Poster. Use when: trash polka, полька кровавая, grunge poster, ink splashes, blood-red accents, comic noir mixed media.
---

# Trash Polka noir comic image prompt

Скил собирает **универсальный** image prompt в фиксированном стиле.
Сюжет всегда переменный; стиль — константа.

## When to use
- Нужен image prompt в этом визуальном стиле (один кадр или серия).
- Пользователь дал сюжет/кадр/voiceover и просит «в стиле …» / «сделай промт».
- Нужно превратить сырой сюжет в пару `PROMPT` + `NEGATIVE PROMPT` без привязки к чужому примеру из исходника.

## When NOT to use
- Нужен полный pipeline-промт шага img_pr по всему xlsx (десятки кадров, герой, мир) — бери Blocks v2 / пресет в Studio, не этот скил.
- Нужен другой стиль (plasticine / knitted / noir / trash-polka) — вызови соответствующий скил.
- Просят изменить код outsee/монтажа, а не текст image prompt.

## Universal workflow (одинаковый для всех style-skills)
1. **Пойми задачу** одной фразой: кто/что, где, что происходит, настроение.
2. **Не копируй сюжет из примера стиля.** Стиль фиксирован; сюжет — переменные.
3. **Заполни мини-форму** (слоты ниже). Пустые слоты, которые не нужны сцене, опусти или поставь нейтрально («none» / не упоминай в PROMPT).
4. **Собери PROMPT** по шаблону из `references/style-spec.md`: подставь слоты, сохрани STYLE LOCK в конце.
5. **Собери NEGATIVE** = NEGATIVE_CORE + TEXT/BRANDING + CONTEXT_SPECIFIC_NEGATIVES.
6. **Проверь:**
   - один кадр / одна сцена (не коллаж, не несколько панелей — если стиль это запрещает);
   - нет читаемого текста/логотипов (если TEXT_RULE запрещает);
   - сюжетные детали только из запроса пользователя;
   - стиль не «уплыл» в photoreal / anime / clean vector (см. STYLE_LOCK_RULE).
7. **Отдай** пользователю блок:
   - `STYLE:` …
   - `PROMPT:` …
   - `NEGATIVE PROMPT:` …
   - кратко: какие слоты заполнены.

## Inputs (если данных мало)
Спроси только то, без чего нельзя собрать сцену. Дефолты:
- aspect / кадр: вертикаль 9:16, если не сказали иначе;
- текст на объектах: blank / non-readable;
- gore/violence: выкл, пока явно не попросили.

## Decision rules
- Есть готовый закадровый текст кадра → MAIN_SUBJECT / ACTION / SETTING бери из него, не выдумывай новых персонажей.
- Пользователь дал только тему → сделай одну сильную сцену, не серию.
- Конфликт «стиль vs сюжет» → побеждает STYLE_LOCK; сюжет упрощай, стиль не ломай.


## Style identity
- **STYLE_LABEL:** Trash Polka Noir Comic Grunge Poster Illustration
- **Суть:** trash polka + noir comic + grunge poster, black/off-white/dirty cream/charcoal + vivid blood red, raw brush/ink/spray, one unified scene not collage panels.
- Полные векторы (STYLE_CORE, LOCK, LIGHT, COLOR, PROMPT/NEGATIVE templates) — в `references/style-spec.md`.

## Mini-form slots
Заполни перед сборкой (ненужное опусти):

```text
MAIN_SUBJECT =
SETTING =
ACTION_OR_STATE =
NOIR_LIGHTING =
RED_GRAPHIC_ACCENTS =
GRUNGE_TEXTURES =
COMIC_FRAME_ELEMENTS =
FOCAL_POINT =
MOOD =
CONTEXT_SPECIFIC_NEGATIVES =
```

## Build rules
1. Подставь слоты в шаблон `PROMPT` / `NEGATIVE PROMPT` из `references/style-spec.md`.
2. Сохрани финальный **Final style lock** из исходника стиля.
3. Не добавляй читаемый текст/логотипы, если STYLE_SPEC запрещает.
4. Не тащи чужие сюжетные якоря из демо-примера стиля.

## Output format
```text
STYLE: <STYLE_LABEL>

PROMPT:
<один абзац или плотный блок>

NEGATIVE PROMPT:
<comma-separated>
```

## Done means
- [ ] Есть PROMPT и NEGATIVE
- [ ] Сюжет из запроса, не из демо-примера стиля
- [ ] STYLE LOCK соблюдён
- [ ] Нет запрещённого текста/логотипов (по правилам стиля)

## Source in repo
Исходный style-template лежит в `prompts/05_image_prompts/` (и дубль в `04_hero_style/`).
Полные векторы и шаблоны подстановки — `references/style-spec.md`.

