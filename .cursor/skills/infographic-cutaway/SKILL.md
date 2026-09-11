---
name: infographic-cutaway
description: >-
  ШАБЛОН: редакционная инфографика-разрез (Guardian / National Geographic cutaway) —
  объект распилен на слои, изометрия, микроподписи с выносками, врезка-механизм.
  Включи, когда нужен постер-объяснялка под GPT Image 2 / Nano Banana Pro.
---

# Шаблон: инфографика-разрез (editorial cutaway)

Один портретный лист: объект разрезан на слои, слои разложены «лесенкой» в одной
изометрии, микроподписи на волосяных выносках, легенда, врезка с механизмом.
Референс — газетный разворот-объяснялка (Titanic «Inside the ship»).

Тип листа уже выбран человеком. Объект берётся из его текста, сюжет не досочинять.

## Что делать

1. Разложи объект на **4–7 слоёв** (палубы, этажи, оболочки, стадии, глубины),
   от внешнего к внутреннему.
2. Придумай **1 врезку** — узел крупным планом, 3–5 нумерованных шагов.
3. Собери список подписей (см. «Бюджет текста»).
4. Выдай один `PROMPT` (английский, ≤ 4700 знаков) + `NEGATIVE PROMPT`.

## Каркас листа (не менять)

| Зона | Что там |
|---|---|
| Верх-лево | Заголовок 2–4 слова + легенда (3–4 свотча) |
| Тело | 4–7 слоёв, диагональ сверху-слева вниз-вправо, перекрытие ~15% |
| Левый край | Имена слоёв (A, B, C… или словами) серым курсивным серифом |
| Право-низ | Врезка: подзаголовок + 3–5 нумерованных шагов по 3 строки |
| Угол | Эталон масштаба с подписью «to scale» |

Верхний слой — объект целиком снаружи, каждый следующий на уровень глубже,
внутренности сверху. Все слои под одним углом.

## Проекция и рендер

Аксонометрия ~20°, длинная ось из нижнего-левого в верхний-правый, линии не
сходятся, точки схода нет. Плоские заливки, волосяной контур 0.5pt, затемнение
только во внутренних углах, тонкая тень под слоем. Без градиентов, бликов,
3D-рендера, фотореализма.

## Палитра (печатная, не больше восьми цветов)

```text
#FFFFFF  бумага — фон и воздух между слоями
#1B1B1B  корпус, внешняя оболочка, силуэты
#B23A28  красная окись — днище, фундамент, «горячая» зона
#C8A981  дерево — палубы, полы, тёплая структура
#E0A090  терракота — первый класс / первая функция
#A9CFC4  мята — третий класс / вторая функция
#D8D5CE  серый — техника, машинные зоны
#E8A33D  охра — трубы и узлы, что бросается в глаза
#D0021B  красные точки на концах выносок
```

## Типографика (её обычно и теряют)

- Заголовок: slab serif в духе Guardian Egyptian, чёрный, обычный регистр,
  2–4 слова, ~4% высоты листа.
- Имена слоёв: серифный курсив, серый, вдвое крупнее подписей.
- Подписи: узкий гротеск (Helvetica Neue / Univers) 6–7pt, тёмно-серый,
  1–3 слова, выключка влево.
- Абзац врезки: тот же гротеск 7pt, 3 строки, ~40 знаков, рваный край.
- Шаги — чёрный кружок с белой цифрой; легенда — свотч 8×8 + строка.
- Выноска: волосяная серая линия, один излом, красная точка 1.5px.

## Бюджет текста (иначе будет каша)

- **Читаемых подписей 20–30**, перечислить в промпте буквально. На тридцати
  2–4 всё равно выйдут закорючками — это потолок модели.
- Каждая ≤ 3 слов, без повторов; длинные фразы только в абзаце врезки.
- Всё сверх списка — выноски с точками **без слов**.
- Обязательные фразы: `each label appears exactly once`,
  `all lettering crisp and correctly spelled, no gibberish text`.

## Картинка-референс: осторожно

Референс перебивает тему: с картинкой Титаника и текстом про самолёт выйдут пять
кораблей с идеальной вёрсткой. По умолчанию генерируй **без референса** — текста
хватает. Если он нужен, добавь `use the attached image only as a layout and
typography reference; the subject is [ОБЪЕКТ], not a [что на референсе]` и
продублируй запрет в NEGATIVE.

## Шаблон PROMPT

> Editorial cutaway infographic poster in British broadsheet science-graphics
> style. Subject: [ОБЪЕКТ]. Portrait sheet, pure white paper background, generous
> margins.
> Structure: the subject is sliced into [N] horizontal layers — [СЛОИ 1…N]. Each
> layer is drawn as its own axonometric cutaway slab; all slabs share one identical
> orientation and angle and cascade from upper-left to lower-right with about 15
> percent overlap, like peeled-away levels of one object. Top slab shows the
> complete exterior; every lower slab exposes one level deeper with its interior
> seen from above. Left margin carries the layer names in grey italic serif.
> Lower-right corner holds a detail inset titled [ВРЕЗКА] explaining [МЕХАНИЗМ] in
> [3–5] numbered steps, each step a black circle with a white numeral plus a
> three-line caption. Small [ЭТАЛОН МАСШТАБА] silhouette labelled "to scale".
> Projection: strict parallel axonometric, about 20 degrees elevation, long axis
> running lower-left to upper-right, no perspective convergence, no vanishing point.
> Render: flat vector fills, hairline 0.5pt dark grey outlines, faint ambient
> occlusion only inside corners, thin soft shadow under each slab, no gradients,
> no gloss, no photographic lighting.
> Palette, strict: white paper, black #1B1B1B shell, oxide red #B23A28 base, tan
> #C8A981 floors, terracotta #E0A090, mint #A9CFC4, warm grey #D8D5CE, ochre
> #E8A33D accents, small #D0021B dots.
> Typography: headline "[ЗАГОЛОВОК]" upper-left in black slab serif of
> Guardian-Egyptian flavour, sentence case; under it a legend of [K] colour
> swatches — [ЛЕГЕНДА]; all callout labels in tiny 6pt condensed grotesque sans,
> dark grey, one to three words, left aligned, each connected to its target by a
> hairline grey leader line with a single bend ending in a 1.5px red dot. Each
> label appears exactly once. All lettering crisp, correctly spelled, no gibberish
> text.
> Labels to render, exactly these: [20–30 подписей через запятую].
> Density: densely packed and technical yet airy and legible, hundreds of tiny
> drawn details — figures for scale, furniture, machinery — but only the listed
> words appear as text. Printed newspaper explainer, not an illustration poster.

## NEGATIVE (база)

```text
perspective distortion, vanishing point, fisheye, 3d render, photorealism, glossy
highlights, gradients, dark background, neon colors, gibberish text, garbled
letters, misspelled words, duplicated labels, oversized text, watermark, logo,
signature, hand-drawn sketchy lines, blurry, low detail, frame border
```

## Чек-лист

1. `PROMPT` ≤ 4700 знаков; не влез — режь список подписей и «density».
2. Слои в одной проекции, заголовок ≤ 4 слов, легенда 3–4 позиции.
3. Подписей 20–30, каждая ≤ 3 слов, без повторов.
4. Врезка одна, шаги пронумерованы; палитра только из таблицы.
5. Формат 3:4 (по умолчанию) или 2:3.

## Ответ человеку

```text
ЛИСТ: инфографика-разрез — [объект]
СЛОИ: [1] … [N]
ВРЕЗКА: [название]
PROMPT:
…
NEGATIVE PROMPT:
…
ASPECT: 3:4
```

Заполненный пример — [`EXAMPLE.md`](EXAMPLE.md), результат по нему — `example.png`.

## Итерация, если модель сорвалась

| Симптом | Правка |
|---|---|
| Нарисован объект с референса | Убрать референс или «subject is X, not Y» |
| Задвоенные подписи | «each label appears exactly once, no repeated labels» |
| Текст кашей | Список до 15–20, «few words, large enough to read» |
| Слои слиплись | «clearly separated slabs, white gap between layers» |
| Перспектива | «technical axonometric drawing, parallel projection only» |
| Пёстро | Убрать 2 цвета сегментов: дерево + серый + акцент |
| Бедно | «add hundreds of tiny interior objects, tiny human figures» |
