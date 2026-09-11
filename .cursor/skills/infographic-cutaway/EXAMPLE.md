# Примеры подстановки

Здесь показано, **как заполняются слоты**, а не какие бывают темы. Предмет, эпоху,
фон и язык из примера в новый запрос не переносить: спрашивают про улей — рисуем
улей, а не корабль и не самолёт.

---

## A. «покажи мне корабль эпохи возрождения, фон стол, текст русский»

| Слот | Значение |
|---|---|
| ОБЪЕКТ | каррака XVI века, три мачты, высокие надстройки |
| РЕЖИМ | слои: внешний вид → верхняя палуба → пушечная палуба → трюм → набор корпуса |
| ЯЗЫК | русский |
| ФОН | стол (тёмный орех, циркуль, перо, свиток, свеча) |
| МАНЕРА | гравюра (палитра сепия + штриховка) |
| ВРЕЗКА | «Пушка», 4 шага |

Результат — `example-ship.png`.

### PROMPT

```text
Editorial cutaway infographic poster in British broadsheet science-graphics style,
but engraved like a Renaissance atlas plate. Subject: a Renaissance sailing ship,
a 16th century carrack with high forecastle and sterncastle, three masts and square
sails.

Background: the whole infographic is a printed sheet lying flat on a dark walnut
desk, photographed from directly above; around the sheet lie a few period props -
brass dividers, a quill pen, a rolled chart, a candle stub - casting soft shadows on
the wood. The sheet itself is perfectly flat and unwarped, the drawing on it is not
distorted.

Structure: the ship is sliced into five horizontal layers - exterior, upper deck,
gun deck, hold, hull framing. Each layer is drawn as its own axonometric cutaway
slab of the same ship; all five slabs share one identical orientation and angle and
cascade from upper-left to lower-right with about 15 percent overlap, like
peeled-away levels of one ship. The top slab shows the complete exterior with
rigging and sails; every lower slab exposes one level deeper with its interior seen
from above - cabins, cannons, barrels, cargo, ribs and keel. Left margin carries the
layer names in grey italic serif. Lower-right corner holds a detail inset titled
"Пушка" explaining how a cannon is fired, in four numbered steps, each step a black
circle with a white numeral plus a three-line caption. A small human figure labelled
"для масштаба" sits near the lower right.

Projection: strict parallel axonometric, about 20 degrees elevation, long hull axis
running lower-left to upper-right, no perspective convergence, no vanishing point.

Render: flat fills with fine engraved hatching, hairline 0.5pt sepia outlines, faint
shading only inside corners, thin soft shadow under each slab, no gradients, no gloss.

Palette, strict: aged paper #EFE3C8 sheet, sepia ink #3B2A1A lines, oxide red
#B23A28 hull below waterline, oak tan #C8A981 decks, muted teal #A9CFC4 water, warm
grey #D8D5CE metal, gold ochre #E8A33D brass, small #D0021B dots.

Typography: ALL TEXT IN RUSSIAN, Cyrillic letters only, correct Russian spelling, no
Latin letters anywhere. Headline "Устройство корабля" upper-left in black slab serif,
sentence case; under it a legend of four colour swatches labelled Экипаж, Офицеры,
Груз, Оружие; all callout labels in tiny condensed grotesque sans, dark sepia, one or
two words, left aligned, each connected to its target by a hairline leader line with
a single bend ending in a small red dot. Each label appears exactly once. Lettering
crisp and large enough to read, no gibberish.

Labels to render, exactly these Russian words: Нос, Корма, Мачта, Парус, Штурвал,
Каюта, Камбуз, Трюм, Бочки, Пушка, Ядра, Якорь, Канаты, Руль, Киль, Трап, Фонарь, Флаг.

Density: densely packed and technical yet legible, hundreds of tiny drawn details -
sailors, ropes, crates, planks - but only the listed words appear as text.
```

### NEGATIVE PROMPT

```text
modern ships, steamships, aeroplanes, aircraft, jet engines, perspective distortion,
vanishing point, 3d render, photorealistic ship, gradients, neon colours, Latin
letters, English words, gibberish text, garbled letters, oversized text, watermark, logo
```

Вышло: пять срезов карраки в гравюрной сепии на столе с пером и циркулем, русские
подписи читаемые, врезка «Пушка» с четырьмя шагами. Сорвалось одно слово из
восемнадцати («Мачта» → «Пачта») — обычная норма для кириллицы.

---

## B. «разрез пассажирского самолёта, как тот Титаник»

| Слот | Значение |
|---|---|
| ОБЪЕКТ | четырёхдвигательный пассажирский лайнер |
| РЕЖИМ | слои: exterior → upper deck → main cabin → cargo deck → structure |
| ЯЗЫК | английский, 30 подписей |
| ФОН | белая бумага |
| МАНЕРА | газетная |
| ВРЕЗКА | Propulsion, 4 шага по турбовентилятору |

Подписи: Cockpit, Crew rest, Upper deck, First class, Business class, Economy cabin,
Galley, Lavatory, Overhead bins, Emergency exit, Cargo hold, Baggage containers,
Landing gear, Fuel tank, Wing spar, Flaps, Aileron, Winglet, Tail fin, Rudder, Radar
nose, Avionics bay, Air conditioning, Boarding door, Window row, Engine pylon, Fan
blades, Combustor, Turbine, Thrust reverser.

Результат — `example-jet.png`. Из тридцати подписей три вышли закорючками и две
задвоились: отсюда потолок 20–30 и требование `each label appears exactly once`.

---

## C. «как устроен улей, по-русски»

| Слот | Значение |
|---|---|
| ОБЪЕКТ | деревянный улей с пчелиной семьёй |
| РЕЖИМ | слои: крыша → медовая надставка → расплодная камера → летковое дно → подставка |
| ЯЗЫК | русский, 14 подписей |
| ФОН | тёплая бумага `#F7F3E8` |
| МАНЕРА | атлас (тушь + штриховка, зелень травы) |
| ВРЕЗКА | «Как рождается пчела»: яйцо → личинка → куколка → пчела |

Результат — `example-hive.png`. Живое существо и деревянный ящик держат тот же
каскад, что корабль: шаблон не про технику, а про способ показать устройство.

