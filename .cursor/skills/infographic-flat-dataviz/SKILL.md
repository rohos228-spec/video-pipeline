---
name: infographic-flat-dataviz
description: >-
  ШАБЛОН: плоский двухцветный отчёт-инфографика. Карта с пузырями, кольца,
  точечные матрицы, иконки с процентами, крупные цифры в колонках. Без фото
  и градиентов. Под GPT Image 2 / Nano Banana Pro.
---

# Плоский отчёт-инфографика (flat data report)

Высокая простыня: сверху карта или главный кадр, ниже колонки с блоками
статистики. Плоский вектор в двух тонах одного цвета плюс чёрный и акцент.

**Тема любая:** разливы нефти, сон подростков, рынок кофе, аварии, миграция.

## Неизменно (это и есть стиль)

Плоский вектор без объёма и градиентов. Два тона цвета несут графику, чёрный —
самое тяжёлое, акцент — ровно один объект на листе. Цифры в 6–8 раз крупнее
подписей, колонки на волосяных линиях. Заголовок секции: капс с разрядкой,
кружок со стрелкой слева, линия снизу.

## Слоты (заполни из запроса)

| Слот | Если не сказано |
|---|---|
| ТЕМА | — |
| ЯЗЫК | язык запроса |
| ЦВЕТ | синий (палитра ниже) |
| ВЕРХНИЙ КАДР | карта с пузырями |
| БЛОКИ | 5–7 из словаря ниже |
| ГЛАВНАЯ ЦИФРА | самый крупный факт темы |

## Словарь блоков (комбинируй, не повторяй)

| Блок | Как выглядит |
|---|---|
| Карта | контур мира точечным растром, круги по величине, подписи внутри |
| Кольцо | бублик на 3–5 долей, % внутри и снаружи |
| Точки | сетка мелких кружков, часть закрашена |
| Иконки | ряд плоских монолинейных значков, у каждого % и подпись |
| Столбик | вертикальная шкала причин, выноски влево и вправо |
| Люди | ряды пиктограмм-человечков, часть тёмная |
| Шкала | вертикаль с отметками глубины/высоты и цифрами |
| Сравнение | два силуэта рядом с числами |

## Каркас листа

| Зона | Что там |
|---|---|
| Верх | Кадр во всю ширину: карта или главная схема |
| Полоса | Тонкая линия-разделитель, ниже начинается сетка |
| Тело | 3 колонки, в каждой 2–3 блока сверху вниз |
| Центр | Главная цифра листа, крупнее всех остальных вдвое |
| Низ | Пиктограммы людей или итоговая цифра |

## Палитра (два тона + чёрный + акцент)

```text
синяя    #A9DCEF фон · #2E86AB средний · #123A5A тёмный · #111111 · акцент #E8531F
зелёная  #DCEFD3 · #6BAF5C · #1E4620 · #111111 · акцент #E8531F
песочная #F3E7CE · #C9A227 · #4A3B14 · #111111 · акцент #D64545
```

Фон листа — самый светлый тон, не белый. Больше четырёх цветов не вводить.

## Типографика

- Цифры: сверхжирный узкий гротеск капсом, тёмный тон, единицы измерения
  средним тоном той же строкой мельче вдвое.
- Заголовки секций: 8pt капс с разрядкой, тёмный тон, кружок-стрелка слева.
- Подписи: 7pt обычный гротеск, тёмно-серый, 1–4 слова.
- Абзацы: только справа от главного факта, 3–4 строки по ~40 знаков.
- Проценты у иконок ставь слева от значка, подпись под ним.

## Бюджет текста

- 10–16 чисел, 12–20 подписей, не больше 2 абзацев на весь лист.
- Подпись ≤ 4 слов. Русский: `ALL TEXT IN RUSSIAN, Cyrillic only, correct
  spelling`, числа и проценты оставить арабскими, подписей не больше 14.
- Обязательно: `each label appears exactly once`, `numbers crisp and readable`.
- Проценты в блоке должны складываться правдоподобно — перечисли их сам.

## Шаблон PROMPT (≤ 4700 знаков)

> Flat vector data-visualisation infographic sheet about [ТЕМА], portrait format,
> editorial report style. Background [СВЕТЛЫЙ ТОН], everything drawn in flat two-tone
> [ЦВЕТ] plus black and a single [АКЦЕНТ] highlight, no gradients, no shadows,
> no photographs, no 3d.
> Top band: [ВЕРХНИЙ КАДР — e.g. a dotted-halftone world map with proportional
> circles sized by value, the largest one in [АКЦЕНТ], numbers set inside the circles].
> Below a hairline divider the sheet splits into three columns separated by hairlines,
> holding these blocks: [БЛОКИ ИЗ СЛОВАРЯ с параметрами]. Centre column carries the
> single biggest figure of the sheet, "[ГЛАВНАЯ ЦИФРА]", twice larger than any other
> number, with a short caption beneath. Bottom [ФИНАЛЬНЫЙ БЛОК].
> Every section starts with an all-caps letterspaced heading preceded by a small
> circled arrow and underlined by a hairline.
> Icons are simple monoline flat pictograms — [СПИСОК ИКОНОК] — each with its
> percentage beside it and a two-word label under it.
> Typography: [ФРАЗА ЯЗЫКА]. Ultra-bold condensed grotesque for figures, light sans
> for captions; figures six times larger than captions. Each label appears exactly
> once, all lettering crisp and correctly spelled, no gibberish.
> Text to render, exactly these: [ЦИФРЫ, ПРОЦЕНТЫ, ПОДПИСИ].
> Style: clean printed report, generous whitespace, strict grid, hairline rules,
> data-driven, no decorative illustration.

## NEGATIVE

```text
photographs, photorealism, 3d render, gradients, drop shadows, bevel, glossy,
skeuomorphic icons, rainbow palette, more than four colors, hand-drawn style,
gibberish text, garbled letters, duplicated labels, lorem ipsum, dense paragraphs,
watermark, logo, frame border, blurry
```

## Чек-лист

Два тона одного цвета плюс чёрный и один акцент, фон не белый. Блоки не
повторяются. Главная цифра одна. Все числа перечислены в промпте.
Выдай `PROMPT`, `NEGATIVE PROMPT`, `ASPECT` (3:4 или 9:16).

## Итерация

| Симптом | Правка |
|---|---|
| Появились фото и объём | `flat vector only, no gradients, no shadows` |
| Цветная каша | Перечислить 4 hex и `use only these four colors` |
| Числа мелкие | `figures six times larger than captions` |
| Блоки повторились | Задать блоки поимённо в порядке сверху вниз |
| Пусто | Добавить блок «Люди» и «Точки» в нижнюю колонку |
| Кириллица кашей | Подписей до 12, слова короче, `large enough to read` |
