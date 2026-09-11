# Пример: «Inside the jet»

Запрос человека: *«разрез пассажирского самолёта, как тот Титаник»*.

Разбор по скиллу:

- **Слои (5):** Exterior → Upper deck → Main cabin → Cargo deck → Structure and systems
- **Врезка:** Propulsion — турбовентиляторный двигатель, 4 шага
- **Легенда (4):** First class / Business class / Economy / Crew areas
- **Эталон масштаба:** автобус
- **Подписей:** 30, каждая ≤ 3 слов
- **Формат:** 3:4, без картинки-референса

Результат — `example.png`.

## PROMPT

```text
Editorial cutaway infographic poster in British broadsheet science-graphics style,
like a Guardian or National Geographic newspaper explainer spread. Subject: a large
four-engine passenger airliner, wings and swept tail clearly visible. Portrait sheet,
pure white paper background, generous margins.

Structure: the aircraft is sliced into five horizontal layers - exterior, upper deck,
main cabin, cargo deck, structure and systems. Each layer is drawn as its own
axonometric cutaway slab of the same aeroplane; all five slabs share one identical
orientation and angle and cascade from upper-left to lower-right with about 15 percent
overlap, like peeled-away levels of one aircraft. The top slab shows the complete
exterior with engines and landing gear; every lower slab exposes one level deeper with
its interior seen from above - rows of seats, galleys, stairs, cargo containers, wing
spars, fuel tanks. Left margin carries the layer names in grey italic serif.
Lower-right corner holds a detail inset titled "Propulsion" explaining the turbofan
engine in four numbered steps, each step a black circle with a white numeral plus a
three-line caption. A small bus silhouette labelled "to scale" sits near the lower right.

Projection: strict parallel axonometric technical drawing, about 20 degrees elevation,
long fuselage axis running lower-left to upper-right, no perspective convergence,
no vanishing point.

Render: flat vector fills, hairline 0.5pt dark grey outlines, faint ambient occlusion
only inside corners, thin soft shadow under each slab, no gradients, no gloss,
no photographic lighting.

Palette, strict: white paper, black #1B1B1B shell, oxide red #B23A28 belly, tan #C8A981
floors, terracotta #E0A090 first-class zones, mint #A9CFC4 economy zones, warm grey
#D8D5CE machinery, ochre #E8A33D engines and accents, small #D0021B dots.

Typography: headline "Inside the jet" upper-left in black slab serif of
Guardian-Egyptian flavour, sentence case; under it a legend of four colour swatches -
First class, Business class, Economy, Crew areas; all callout labels in tiny 6pt
condensed grotesque sans, dark grey, one to three words, left aligned, each connected
to its target by a hairline grey leader line with a single bend ending in a 1.5px red
dot. Each label appears exactly once. All lettering crisp, correctly spelled,
no gibberish text.

Labels to render, exactly these: Cockpit, Crew rest, Upper deck, First class, Business
class, Economy cabin, Galley, Lavatory, Overhead bins, Emergency exit, Cargo hold,
Baggage containers, Landing gear, Fuel tank, Wing spar, Flaps, Aileron, Winglet, Tail
fin, Rudder, Radar nose, Avionics bay, Air conditioning, Boarding door, Window row,
Engine pylon, Fan blades, Combustor, Turbine, Thrust reverser.

Density: densely packed and technical yet airy and legible, hundreds of tiny drawn
details - seats, trolleys, tiny human figures for scale, ducts, ribs - but only the
listed words appear as text. Printed newspaper explainer, not an illustration poster.
```

## NEGATIVE PROMPT

```text
ships, boats, hull with portholes, perspective distortion, vanishing point, fisheye,
3d render, photorealism, glossy highlights, gradients, dark background, neon colors,
gibberish text, garbled letters, misspelled words, duplicated labels, oversized text,
watermark, logo, signature, blurry, low detail, frame border
```

## Что вышло и чего ждать

Совпало: каскад слоёв, легенда со свотчами, курсивные имена слоёв слева, выноски
с красными точками, врезка с нумерованными шагами, автобус «to scale».

Разошлось (нормальный разброс модели, не баг промпта):

- из 30 подписей 3–4 вышли закорючками, пара задвоилась — отсюда правило
  «20–30 подписей + each label appears exactly once»;
- проекция ближе к трёхчетвертному ракурсу, чем к чистой аксонометрии;
- палитра светлее заявленной, терракота и мята применены точечно.

Если нужна строгая аксонометрия — усилить формулировку до
`flat technical axonometric drawing, parallel projection only, no camera perspective`.
