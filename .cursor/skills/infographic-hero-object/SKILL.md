---
name: infographic-hero-object
description: >-
  ШАБЛОН: журнальный постер-инфографика. Один фотореалистичный герой-объект в
  центре на насыщенном цветном фоне, вокруг крупные цифры на выносках, внизу
  полоса вариантов и диаграмма. Под GPT Image 2 / Nano Banana Pro.
---

# Постер с героем-объектом (magazine hero infographic)

Альбомный лист: на насыщенном цветном фоне один предмет, снятый как в рекламе
и частично вскрытый. Вокруг крупные цифры с абзацами, снизу полоса вариантов.

**Герой любой:** початок, двигатель, кроссовок, гранат, смартфон, гриб, зуб.

## Неизменно (это и есть стиль)

Герой в центре, фотореалистичный, крупнее прочего в 3–4 раза. Фон — радиальный
градиент одного цвета, тёмный по краям. Цифры втрое крупнее подписей. Выноски
волосяные, без стрелок. Рамок вокруг блоков нет, всё держит воздух.

## Слоты (заполни из запроса)

| Слот | Если не сказано |
|---|---|
| ГЕРОЙ | — |
| ЦВЕТ ФОНА | доминирующий цвет героя |
| ЯЗЫК | язык запроса |
| ЗАГОЛОВОК | 2 этажа: тонкие капсы + огромное слово |
| ФАКТЫ | 6–10 пар «цифра + 15 слов» |
| НИЗ | полоса из 5–7 разновидностей героя |
| ДИАГРАММА | доли в виде цветка или кольца |

## Каркас листа

| Зона | Что там |
|---|---|
| Верх-лево | Метка `[ПОСТЕР]`, двухэтажный заголовок, дек 2 строки |
| Центр | Герой во всю ширину, вскрыт: слои, срез, цветные зоны |
| Лево и верх | 3–4 крупные цифры с абзацем 3 строки |
| Право | Список долей в % с выносками к цветным зонам героя |
| Низ | Полоса миниатюр разновидностей с подписями |
| Право-низ | Диаграмма-цветок или кольцо |
| Угол | Кегль 5pt: авторы и источник |

## Герой

Студийный свет, мягкая тень под предметом, лёгкое свечение по контуру.
Предмет раскрыт: кожура отогнута, половина в разрезе, внутренности видны.
Часть поверхности перекрашена в 4–6 цветных зон под легенду процентов.

## Палитра

```text
фон      насыщенный цвет героя, радиальный градиент светлый центр → тёмные края
цифры    самый тёмный тон того же цвета
текст    белый и светлый оттенок фона
акцент   жёлтый #F5C518 или контрастный к фону
герой    натуральные цвета фотографии, не перекрашивать целиком
```

Пример для зелёного: `#0B3D14 → #4C9A2A` фон, цифры `#0F3D0F`, акцент `#F5C518`.

## Типографика

- Заголовок: первый этаж — узкие капсы 1–2 слова; второй — одно слово
  сверхжирным гротеском строчными, высотой ~12% листа, белым.
- Цифры фактов: сверхжирный узкий гротеск, ~5% высоты, единицы вдвое мельче.
- Подписи под цифрами: 7pt, 3 строки, ~30 знаков в строке.
- Секции (`ПРИМЕНЕНИЕ`, `КТО БОЛЬШЕ`): капс 8pt с разрядкой.
- Дроби вроде `2/3` — крупной наборной дробью, не картинкой.

## Бюджет текста

- 6–10 крупных цифр, 12–18 коротких подписей, 3–5 абзацев по 3 строки.
- Подпись ≤ 4 слов. Русский текст: `ALL TEXT IN RUSSIAN, Cyrillic only, correct
  spelling`, счёт подписей срезать на треть.
- Обязательно: `each label appears exactly once`, `numbers crisp and readable`.
- Референс-картинку не прикладывай: она подменяет предмет.

## Шаблон PROMPT (≤ 4700 знаков)

> Magazine poster infographic, landscape sheet, editorial science-popular style.
> Hero: one photorealistic [ГЕРОЙ], studio lit, [КАК ВСКРЫТ: peeled, half cut open,
> exploded], filling the centre of the sheet, soft shadow beneath, subtle rim glow.
> Part of its surface is divided into [K] flat colour zones used as a legend.
> Background: rich [ЦВЕТ] radial gradient, lighter behind the hero, dark towards the
> edges, subtle texture, no objects on it.
> Layout: upper-left a two-tier headline — small condensed caps "[ЭТАЖ 1]" above a
> huge bold lowercase word "[ЭТАЖ 2]" in white — plus a two-line deck. Around the
> hero [N] big statistics, each a very large ultra-bold condensed number in the
> darkest tone of the background colour with a three-line 7pt white caption beneath,
> joined to the hero by a hairline leader without arrowheads. Right side a percentage
> list titled "[СЕКЦИЯ]" linking to the coloured zones. Bottom strip of [M] small
> photoreal variants of the hero in a row with one-line captions. Lower-right a
> [цветок/кольцо] chart of shares. Tiny 5pt credit line in a corner.
> Typography: [ФРАЗА ЯЗЫКА]. Ultra-bold condensed grotesque for numbers and
> headline, light sans for captions, all-caps letterspaced section labels. Numbers
> three times larger than captions. Each label appears exactly once, all lettering
> crisp and correctly spelled, no gibberish.
> Text to render, exactly these: [ЦИФРЫ И ПОДПИСИ].
> Style: glossy magazine print, high colour saturation, deep contrast, no frames or
> boxes around blocks, everything held together by whitespace and hairlines.

## NEGATIVE

```text
flat vector hero, cartoon object, 3d render look, clip art, dull desaturated colors,
white background, boxes and frames around text, drop shadow on text, gibberish text,
garbled letters, duplicated labels, oversized paragraphs, lorem ipsum, watermark,
stock photo collage, cluttered overlapping labels, blurry
```

## Чек-лист

Герой один и фотореалистичный, фон — градиент его же цвета. Заголовок в два
этажа. Цифр 6–10, каждая с абзацем. Внизу полоса вариантов и одна диаграмма.
Выдай `PROMPT`, `NEGATIVE PROMPT`, `ASPECT` (16:9 или 4:3).

## Итерация

| Симптом | Правка |
|---|---|
| Герой стал рисунком | `photorealistic studio product photography of [ГЕРОЙ]` |
| Фон пёстрый | `single colour radial gradient background, nothing else` |
| Цифры мелкие | `numbers occupy 5 percent of sheet height, ultra bold` |
| Текст в рамках | `no boxes, no panels, labels float on the background` |
| Пусто по краям | Добавить 2 факта и полосу вариантов внизу |
