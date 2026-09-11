---
name: infographic-cutaway
description: >-
  ШАБЛОН: редакционная инфографика-разрез (Guardian / National Geographic cutaway) —
  объект распилен на слои, изометрия, микроподписи с выносками, врезка-механизм.
  Включи, когда нужен постер-объяснялка под GPT Image 2 / Nano Banana Pro.
  Не оркестратор — только этот тип листа.
---

# Шаблон: инфографика-разрез (editorial cutaway)

Результат: **один** портретный лист — объект разрезан на слои, слои разложены
«лесенкой» сверху вниз в одной изометрии, сотня микроподписей на волосяных
выносках, легенда, врезка с механизмом. Стиль референса — разворот-объяснялка
британской газеты (Titanic «Inside the ship»).

Ты выбран человеком как **тип листа**. Не спрашивай «а что нарисовать?» —
объект берётся из его текста.

## Что делать

1. Возьми объект/процесс из запроса человека.
2. Разложи его на **4–7 слоёв** (палубы, этажи, оболочки, стадии, глубины).
   Слои идут от внешнего/верхнего к внутреннему/нижнему.
3. Придумай **1 врезку** — механизм или подсистема крупным планом, 3–5
   пронумерованных шагов.
4. Собери список подписей (см. «Бюджет текста»).
5. Выдай один `PROMPT` (английский, сплошным блоком) + `NEGATIVE PROMPT`.
6. Ничего не досочиняй сверх фактов человека; неизвестное — не подписывай.

## Каркас листа (не менять)

| Зона | Что там |
|---|---|
| Верх-лево | Заголовок 2–4 слова + легенда (3–4 цветных свотча) |
| Тело листа | 4–7 слоёв-срезов, диагональ сверху-слева вниз-вправо, перекрытие ~15% |
| Левый край | Буквы/имена слоёв (A, B, C… или названия) курсивным серифом |
| Право-низ | Врезка: подзаголовок + абзац 3 строки + 3–5 нумерованных шагов |
| Любой угол | Объект-эталон масштаба с подписью «to scale» |

Верхний слой — целый объект снаружи. Каждый следующий — на один уровень глубже,
внутренности видны сверху. Все слои в **одной** ориентации и под одним углом.

## Проекция и рендер

- Строгая аксонометрия ~20° над горизонтом, длинная ось из нижнего-левого
  в верхний-правый угол. Параллельные линии не сходятся, точки схода нет.
- Плоские заливки, волосяной контур 0.5pt тёмно-серый, лёгкое затемнение только
  во внутренних углах, тонкая мягкая тень под каждым слоем.
- Никаких градиентов, бликов, 3D-рендера, фотореализма, тумана.

## Палитра (печатная, приглушённая)

```text
бумага      #FFFFFF   фон листа, воздух между слоями
корпус      #1B1B1B   внешняя оболочка, силуэты
низ/база    #B23A28   красная окись — днище, фундамент, «горячая» зона
дерево      #C8A981   палубы, полы, тёплая структура
сегмент 1   #E0A090   терракота — первый класс / первая функция
сегмент 2   #A9CFC4   мята — третий класс / вторая функция
сегмент 3   #D8D5CE   серый — техника, машинные зоны
акцент      #E8A33D   охра — трубы, узлы, что бросается в глаза
точки       #D0021B   красные точки на концах выносок
```

Больше восьми цветов не вводить. Никаких неона, кислоты, тёмного фона.

## Типографика (важнее всего — её обычно и теряют)

- Заголовок: **slab serif** в духе Guardian Egyptian, чёрный, обычный регистр,
  2–4 слова, ~4% высоты листа.
- Имена слоёв слева: серифный курсив, серый, крупнее подписей вдвое.
- Подписи: узкий гротеск (Helvetica Neue / Univers), 6–7pt, тёмно-серый,
  обычный регистр, **1–3 слова**, выключка влево.
- Абзац врезки: тот же гротеск 7pt, 3–4 строки, ~40 знаков в строке, рваный край.
- Номера шагов: чёрный кружок, белая цифра внутри.
- Легенда: цветной свотч 8×8 + подпись в одну строку.
- Выноска: волосяная серая линия, один излом, красная точка 1.5px на конце.
- В промпте обязательно: `all lettering crisp and correctly spelled, no gibberish text`.

## Бюджет текста (иначе модель нарисует кашу)

- **Читаемых подписей: 20–30.** Перечисли их в промпте буквально, списком.
  На тридцати штуках 2–4 всё равно выйдут закорючками — это потолок модели.
- Всё, что сверх, — только выноски с точками **без слов**.
- Каждая подпись ≤ 3 слов. Длинные фразы — только в абзаце врезки.
- Добавь `each label appears exactly once` — иначе «Cockpit» вылезет дважды.
- Не проси «сто подписей»: получишь сто нечитаемых закорючек.

## Картинка-референс: осторожно

Приложенный референс **перебивает тему**: с картинкой Титаника и текстом про
самолёт модель рисует пять кораблей с идеальной вёрсткой. Поэтому:

- по умолчанию генерируй **без референса** — текста ниже хватает на стиль;
- если референс всё же нужен, добавь в промпт:
  `use the attached image only as a layout and typography reference; the subject
  is [ОБЪЕКТ], not a [что на референсе]`, и продублируй запрет в NEGATIVE.

## Шаблон PROMPT

> Editorial cutaway infographic poster in British broadsheet science-graphics
> style. Subject: **[ОБЪЕКТ]**. Portrait sheet, pure white paper background,
> generous margins.
> **Structure:** the subject is sliced into **[N]** horizontal layers — **[СЛОЙ 1…N]**.
> Each layer is drawn as its own axonometric cutaway slab; all slabs share one
> identical orientation and angle and cascade from upper-left to lower-right with
> about 15 percent overlap, like peeled-away levels of one object. Top slab shows
> the complete exterior; every lower slab exposes one level deeper with its
> interior seen from above. Left margin carries the layer names in grey italic
> serif. Lower-right corner holds a detail inset titled **[ВРЕЗКА]** explaining
> **[МЕХАНИЗМ]** in **[3–5]** numbered steps, each step a black circle with a white
> numeral plus a three-line caption. Small **[ЭТАЛОН МАСШТАБА]** silhouette labelled
> "to scale".
> **Projection:** strict parallel axonometric, about 20 degrees elevation, long axis
> running lower-left to upper-right, no perspective convergence, no vanishing point.
> **Render:** flat vector fills, hairline 0.5pt dark grey outlines, faint ambient
> occlusion only inside corners, thin soft shadow under each slab, no gradients,
> no gloss, no photographic lighting.
> **Palette (strict):** white paper, black **#1B1B1B** shell, oxide red **#B23A28**
> base, tan **#C8A981** floors, terracotta **#E0A090**, mint **#A9CFC4**, warm grey
> **#D8D5CE**, ochre **#E8A33D** accents, small **#D0021B** dots.
> **Typography:** headline "**[ЗАГОЛОВОК]**" upper-left in black slab serif,
> Guardian-Egyptian flavour, sentence case; under it a legend of **[K]** colour
> swatches — **[ЛЕГЕНДА]**; all callout labels in tiny 6pt condensed grotesque sans,
> dark grey, one to three words, left aligned, connected to their target by a
> hairline grey leader line with a single bend ending in a 1.5px red dot. Each
> label appears exactly once. All lettering crisp, correctly spelled, no gibberish text.
> **Labels to render, exactly these:** [20–30 подписей через запятую].
> **Density:** densely packed and technical yet airy and legible, hundreds of tiny
> drawn details — figures for scale, furniture, machinery, railings — but only the
> listed words appear as text. Printed newspaper explainer, not an illustration
> poster.

## NEGATIVE (база)

```text
perspective distortion, vanishing point, fisheye, 3d render, photorealism, glossy
highlights, gradients, dark background, neon colors, gibberish text, garbled
letters, misspelled words, duplicated labels, oversized text, watermark, logo,
signature, cartoon mascots, hand-drawn sketchy lines, blurry, low detail, frame border
```

## Чек-лист перед выдачей

1. Слои в одной проекции и не «разъезжаются» по углу.
2. Заголовок ≤ 4 слов, легенда 3–4 позиции.
3. Список подписей 20–30, каждая ≤ 3 слов, без повторов.
4. Врезка одна, шаги пронумерованы.
5. Палитра — только из таблицы.
6. Формат: 3:4 (по умолчанию) или 2:3.

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

## Пример

Готовый заполненный промпт и то, что по нему вышло, — [`EXAMPLE.md`](EXAMPLE.md)
и `example.png` рядом.

## Итерация, если модель сорвалась

| Симптом | Правка промпта |
|---|---|
| Нарисован объект с референса | Убрать референс совсем либо явно «subject is X, not Y» |
| Подпись задвоилась | «each label appears exactly once, no repeated labels» |
| Текст кашей | Срезать список подписей до 15–20, добавить «few words, large enough to read» |
| Слои слились в один объект | Усилить: «clearly separated slabs, white gap between layers» |
| Появилась перспектива | Добавить «technical axonometric drawing, parallel projection only» |
| Слишком пёстро | Убрать 2 цвета сегментов, оставить дерево + серый + акцент |
| Пусто и бедно | «add hundreds of tiny interior objects, tiny human figures for scale» |
