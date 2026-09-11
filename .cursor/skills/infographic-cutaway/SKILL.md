---
name: infographic-cutaway
description: >-
  ШАБЛОН: редакционная инфографика-разрез — любой предмет, процесс или существо
  разобрано на слои, аксонометрия, микроподписи на выносках, легенда, врезка.
  Тема, фон, эпоха и язык подписей задаются запросом. Под GPT Image 2 / Nano Banana Pro.
---

# Шаблон: инфографика-разрез (editorial cutaway)

Один лист-объяснялка: предмет разобран на части, части разложены в одной
аксонометрии, микроподписи на волосяных выносках, легенда, врезка с механизмом.

**Предмет — любой.** Корабль, улей, замок, мозг, метро, кофемашина, вулкан,
рецепт, битва, миф. Транспорт в примерах — случайность, не специализация.

## Неизменно (это и есть стиль)

1. Каскад срезов сверху-слева вниз-вправо, все под одним углом, перекрытие ~15%.
2. Параллельная аксонометрия ~20°, без перспективы и точек схода.
3. Плоские заливки, волосяной контур 0.5pt, тонкая тень под срезом.
4. Подписи 1–3 слова на выносках с красной точкой; легенда; врезка с
   нумерованными шагами; эталон масштаба.

## Разбор запроса (заполни 6 слотов)

| Слот | Откуда | Если не сказано |
|---|---|---|
| ОБЪЕКТ | прямо из текста | — |
| РЕЖИМ | по природе объекта, таблица ниже | слои |
| ЯЗЫК | сказано «текст русский» и т.п. | язык запроса |
| ФОН | «на столе», «на пергаменте» | белая бумага |
| МАНЕРА | эпоха/тон объекта | газетная |
| ВРЕЗКА | самый интересный узел | любой механизм объекта |

## Режимы разбора

| Режим | Для чего | Что рисуем |
|---|---|---|
| Слои | палубы, этажи, оболочки | 4–7 горизонтальных срезов |
| Взрыв-схема | механизм, прибор, оружие | детали разнесены, пунктир сборки |
| Продольный разрез | здание, нора, дерево, вулкан | один большой срез + 2–3 врезки |
| Стадии | процесс, рецепт, ритуал, битва | 4–7 кадров одной сцены во времени |
| Зоны | город, лес, тело, планета | план сверху, раскрашенные области |

## Фон (вставь фразу в промпт)

| Вариант | Фраза |
|---|---|
| Бумага | `pure white paper background, generous margins` |
| Стол | `the poster is a printed sheet lying flat on a dark wooden desk seen from directly above, a few [РЕКВИЗИТ] beside it casting soft shadows; the sheet stays perfectly flat and unwarped` |
| Старая бумага | `aged parchment sheet, worn edges, faint foxing stains` |
| Синька | `dark blueprint paper, white drafting lines` |

## Манера и палитра (одна на лист)

```text
газетная  бумага #FFFFFF · контур #1B1B1B · окись #B23A28 · дерево #C8A981
          терракота #E0A090 · мята #A9CFC4 · серый #D8D5CE · охра #E8A33D
гравюра   бумага #EFE3C8 · сепия #3B2A1A · окись #B23A28 · дуб #C8A981
          вода #A9CFC4 · металл #D8D5CE · латунь #E8A33D + штриховка hatching
синька    фон #1B3A5C · линии #E8F1F8 · охра #E8A33D · точки #FF6B5A
атлас     бумага #F7F3E8 · тушь #2C2C2C · зелень #7FA05A · охра · терракота
```

Точки выносок #D0021B (на синьке #FF6B5A). Больше восьми цветов не вводить.

## Язык подписей

- Русский: `ALL TEXT IN RUSSIAN, Cyrillic only, correct Russian spelling, no Latin
  letters anywhere`. Слова короче 10 знаков, 12–20 штук — кириллицу модель
  ломает чаще латиницы.
- Английский: список подписей как есть, 20–30 штук.
- Смешивать языки на листе нельзя.

## Типографика

- Заголовок: slab serif, чёрный, обычный регистр, 2–4 слова, ~4% высоты.
- Имена срезов слева: серифный курсив, серый, вдвое крупнее подписей.
- Подписи: узкий гротеск 6–7pt, тёмно-серый, 1–3 слова, выключка влево.
- Врезка: подзаголовок + шаги «чёрный кружок с белой цифрой» + 3 строки 7pt.
- Легенда: свотч 8×8 + строка. Выноска: линия с одним изломом и точкой 1.5px.

## Бюджет текста

- 20–30 подписей латиницей, 12–20 кириллицей. 2–4 всё равно выйдут закорючками.
- Каждая ≤ 3 слов, без повторов; длинные фразы только в шагах врезки.
- Всё сверх списка — выноски с точками **без слов**.
- Обязательно: `each label appears exactly once`, `lettering crisp and correctly
  spelled, no gibberish`.
- Референс-картинку не прикладывай: она перебивает тему (Титаник + текст про
  самолёт = пять кораблей). Нужна — пиши `subject is [ОБЪЕКТ], not a [что там]`.

## Шаблон PROMPT (≤ 4700 знаков)

> Editorial cutaway infographic poster in [МАНЕРА] style. Subject: [ОБЪЕКТ,
> 1–2 уточняющие детали]. Portrait sheet. [ФРАЗА ФОНА].
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
> Render: flat fills [+ fine engraved hatching для гравюры], hairline 0.5pt
> outlines, faint shading only inside corners, thin soft shadow under each slab,
> no gradients, no gloss.
> Palette, strict: [ПАЛИТРА из таблицы], small [#D0021B] dots.
> Typography: [ФРАЗА ЯЗЫКА]. Headline "[ЗАГОЛОВОК]" upper-left in black slab serif,
> sentence case; under it a legend of [K] colour swatches labelled [ЛЕГЕНДА]; all
> callout labels in tiny condensed grotesque sans, one to three words, left aligned,
> each connected to its target by a hairline leader line with a single bend ending
> in a small red dot. Each label appears exactly once. Lettering crisp and large
> enough to read, no gibberish.
> Labels to render, exactly these: [СПИСОК ПОДПИСЕЙ].
> Density: densely packed and technical yet legible, hundreds of tiny drawn details
> — [ЧТО МЕЛКОГО: люди, утварь, механика] — but only the listed words appear as text.

## NEGATIVE (база + запрет чужих тем)

```text
[ЧУЖИЕ ОБЪЕКТЫ: для старины — modern machinery, aeroplanes; для техники —
antique sailing ships], perspective distortion, vanishing point, 3d render,
photorealism, gradients, neon colors, gibberish text, garbled letters, misspelled
words, duplicated labels, oversized text, watermark, logo, blurry, frame border
```

## Чек-лист

1. Все 6 слотов заполнены из запроса, ничего не осталось от примеров.
2. `PROMPT` ≤ 4700 знаков; не влез — режь список подписей.
3. Подписи на одном языке, счёт по бюджету, каждая ≤ 3 слов.
4. В NEGATIVE явно запрещена чужая эпоха/техника.
5. Формат 3:4 (по умолчанию) или 2:3.

## Ответ человеку

```text
ЛИСТ: [объект] · режим [X] · фон [Y] · манера [Z] · язык [ru/en]
PROMPT:
…
NEGATIVE PROMPT:
…
ASPECT: 3:4
```

Заполненные примеры — [`EXAMPLE.md`](EXAMPLE.md). Предмет из примера не переносить.

## Итерация

| Симптом | Правка |
|---|---|
| Вышел предмет не из запроса | Запретить его в NEGATIVE, повторить subject в начале |
| Кириллица кашей | Список до 12, слова короче, «large enough to read» |
| Задвоенные подписи | «each label appears exactly once, no repeated labels» |
| Срезы слиплись | «clearly separated slabs, gap between layers» |
| Перспектива | «technical axonometric drawing, parallel projection only» |
| Лист «поплыл» на фоне-столе | «the sheet stays perfectly flat, drawing unwarped» |
| Бедно | «add hundreds of tiny interior objects, tiny figures for scale» |
