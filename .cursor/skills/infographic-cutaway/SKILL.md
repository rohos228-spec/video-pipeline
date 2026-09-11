---
name: infographic-cutaway
description: >-
  ШАБЛОН: инфографика-разрез. Любой предмет разобран на слои, аксонометрия,
  подписи на выносках, легенда, врезка. Тема, фон, эпоха, язык — из запроса.
  Под GPT Image 2 / Nano Banana Pro.
---

# Инфографика-разрез (editorial cutaway)

**Предмет любой:** корабль, улей, замок, мозг, метро, вулкан, рецепт, битва.
Тема берётся из запроса, не из примеров.

## Неизменно (это и есть стиль)

Каскад срезов сверху-слева вниз-вправо, один угол, перекрытие ~15%. Аксонометрия
~20° без перспективы. Плоские заливки, контур 0.5pt, тень под срезом. Подписи
1–3 слова на выносках с красной точкой, легенда, врезка с шагами, эталон масштаба.

## Слоты (заполни из запроса)

| Слот | Если не сказано |
|---|---|
| ОБЪЕКТ | — |
| РЕЖИМ (таблица ниже) | слои |
| ЯЗЫК | язык запроса |
| ФОН | белая бумага |
| МАНЕРА | газетная |
| ВРЕЗКА | любой узел объекта |

## Режимы

| Режим | Для чего | Что рисуем |
|---|---|---|
| Слои | палубы, этажи, оболочки | 4–7 срезов |
| Взрыв-схема | механизм, прибор | детали врозь + пунктир |
| Разрез | здание, нора, дерево | один срез + 2–3 врезки |
| Стадии | процесс, рецепт, битва | 4–7 кадров во времени |
| Зоны | город, лес, тело | план сверху, области |

## Фон

| Вариант | Фраза |
|---|---|
| Бумага | `pure white paper background, generous margins` |
| Стол | `printed sheet lying flat on a dark wooden desk seen from directly above, a few [РЕКВИЗИТ] beside it casting soft shadows; sheet perfectly flat and unwarped` |
| Старина | `aged parchment sheet, worn edges, faint foxing stains` |
| Синька | `dark blueprint paper, white drafting lines` |

## Манера и палитра (одна на лист, до 8 цветов)

```text
газетная #FFFFFF · #1B1B1B · #B23A28 · #C8A981 · #E0A090 · #A9CFC4 · #D8D5CE · #E8A33D
гравюра  #EFE3C8 · #3B2A1A · #B23A28 · #C8A981 · #A9CFC4 · #D8D5CE · #E8A33D + hatching
синька   #1B3A5C фон · #E8F1F8 линии · #E8A33D · точки #FF6B5A
атлас    #F7F3E8 · #2C2C2C тушь · #7FA05A зелень · #E8A33D · #E0A090
```

Первый цвет — бумага, второй — контур. Точки #D0021B.

## Язык и бюджет подписей

- Русский: `ALL TEXT IN RUSSIAN, Cyrillic only, correct Russian spelling, no Latin
  letters`. 12–20 подписей, слова короче 10 знаков: кириллицу ломает чаще.
- Английский: 20–30. Языки не смешивать. Каждая ≤ 3 слов, без повторов.
- Длинные фразы — только в шагах врезки. Сверх списка — точки без слов.
- 2–4 подписи всё равно поплывут. Обязательно: `each label appears exactly once`,
  `lettering crisp, no gibberish`.
- Референс-картинку не прикладывай — перебивает тему (Титаник + текст про самолёт
  = пять кораблей). Нужна: `subject is [ОБЪЕКТ], not a [что там]`.

## Типографика

- Заголовок: slab serif, чёрный, 2–4 слова, ~4% высоты.
- Имена срезов слева: серый серифный курсив, вдвое крупнее подписей.
- Подписи: узкий гротеск 6–7pt, влево. Легенда: свотч 8×8 + строка.
- Врезка: кружок с белой цифрой + 3 строки 7pt. Выноска: излом, точка 1.5px.

## Шаблон PROMPT (≤ 4700 знаков)

> Editorial cutaway infographic poster in [МАНЕРА] style. Subject: [ОБЪЕКТ +
> 1–2 детали]. Portrait sheet. [ФРАЗА ФОНА].
> Structure: the subject is divided into [N] [срезы/детали/стадии/зоны] — [СПИСОК].
> Each one is drawn as its own axonometric cutaway slab of the same subject; all
> share one identical orientation and angle and cascade from upper-left to
> lower-right with about 15 percent overlap, like peeled-away levels. The top slab
> shows the complete exterior; every lower slab exposes one level deeper with its
> interior seen from above — [ЧТО ВНУТРИ, 4–6 предметов]. Left margin carries the
> layer names in grey italic serif. Lower-right corner holds a detail inset titled
> "[ВРЕЗКА]" explaining [МЕХАНИЗМ] in four numbered steps, each a black circle with
> a white numeral plus a three-line caption. Small [ЭТАЛОН] silhouette labelled
> "[to scale / для масштаба]".
> Projection: strict parallel axonometric, about 20 degrees elevation, long axis
> running lower-left to upper-right, no perspective convergence, no vanishing point.
> Render: flat fills [+ fine engraved hatching], hairline 0.5pt outlines, faint
> shading only inside corners, thin soft shadow under each slab, no gradients.
> Palette, strict: [ПАЛИТРА], small [#D0021B] dots.
> Typography: [ФРАЗА ЯЗЫКА]. Headline "[ЗАГОЛОВОК]" upper-left in black slab serif,
> sentence case; under it a legend of [K] colour swatches labelled [ЛЕГЕНДА]; all
> callout labels in tiny condensed grotesque sans, one to three words, left aligned,
> each connected to its target by a hairline leader line with a single bend ending
> in a small red dot. Each label appears exactly once. Lettering crisp and large
> enough to read, no gibberish.
> Labels to render, exactly these: [СПИСОК ПОДПИСЕЙ].
> Density: densely packed and technical yet legible, hundreds of tiny drawn details
> — [МЕЛОЧЬ: люди, утварь, механика] — but only the listed words appear as text.

## NEGATIVE

```text
[ЧУЖИЕ ОБЪЕКТЫ: старине — modern machinery, aeroplanes; технике — antique sailing
ships], perspective distortion, vanishing point, 3d render, photorealism,
gradients, neon colors, gibberish text, garbled letters, misspelled words,
duplicated labels, oversized text, watermark, logo, blurry, frame border
```

## Чек-лист

Слоты взяты из запроса, от примеров ничего не осталось. `PROMPT` ≤ 4700 знаков,
подписи на одном языке и по бюджету. В NEGATIVE запрещена чужая эпоха и техника.
Выдай `PROMPT`, `NEGATIVE PROMPT`, `ASPECT` (3:4 или 2:3). Примеры —
[`EXAMPLE.md`](EXAMPLE.md), предмет оттуда не переносить.

## Итерация

| Симптом | Правка |
|---|---|
| Предмет не из запроса | Запретить в NEGATIVE, subject повторить в начале |
| Кириллица кашей | Список до 12, `large enough to read` |
| Дубли | `each label appears exactly once` |
| Срезы слиплись | `clearly separated slabs, gap between layers` |
| Перспектива | `parallel projection only` |
| Лист поплыл на столе | `sheet stays perfectly flat, drawing unwarped` |
| Бедно | `add hundreds of tiny interior objects and figures` |
