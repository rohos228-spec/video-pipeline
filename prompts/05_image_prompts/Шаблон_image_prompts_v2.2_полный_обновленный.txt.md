ШАБЛОН ДЛЯ СОЗДАНИЯ IMAGE PROMPTS

СТИЛЬ: Trash Polka Noir Comic Grunge Poster Illustration
ВЕРСИЯ: v2.2 — с реалистичной материальной текстурой, запретом красных кругов, жестким лимитом prompt до 5000 символов, правилом контекстной логики и ограничением detective board до редких сцен

Назначение:
Шаблон фиксирует только стиль исходного промта. Он не фиксирует коридор, силуэт, стену расследования, культовую тему или конкретную сцену. Все сюжетные элементы — переменные.

Главное изменение v2.2:
1. Красные акценты больше не должны превращаться в круги, мишени, evidence marks или обводки вокруг улики.
2. Красный используется только как рваные мазки, печатные сбои, фрагменты постера, брызги краски, царапины и графическое напряжение внутри сцены.
3. Добавлен отдельный слой реалистичной материальной текстуры: штукатурка, дерево, ткань, бумага, камень, грязь, пыль, влага, потертости, трещины, волокна бумаги, следы времени.
4. Стиль остается trash polka noir comic, но сцена должна ощущаться материальной, исторически правдоподобной и тактильной, а не плоской декоративной иллюстрацией.
5. В PROMPT ДЛЯ КАДРА 2 не создается новый промт. Туда дословно копируется только текст из строки 26 + строки 28 исходного материала. Если второго кадра нет, PROMPT ДЛЯ КАДРА 2 не заполняется.
6. Каждый готовый prompt для одного кадра должен быть не длиннее 5000 символов вместе с пробелами. Целевой безопасный лимит — до 4977 символов.
7. Детективная доска, стена расследования, линии, нити и evidence-board логика запрещены как постоянный визуальный прием. Такой кадр допустим не чаще 5% от общего числа кадров и только в самом детективном месте.
8. Логика расследования должна передаваться через материальные детали сцены, причинно-следственные признаки, позы, свет, расстояние между объектами и контекст ролика, а не через случайные доски с линиями.
9. Запрещено добавлять вымышленные детали. Каждый объект, жест, след, документ, фон и визуальный акцент должен быть связан с исходным текстом ролика, voiceover, временем, местом и данными кадра.

ОБЯЗАТЕЛЬНАЯ ЛОГИКА РАБОТЫ С ШАБЛОНОМ:

1. Нельзя удалять исходную структуру шаблона.
2. Нельзя удалять незаполненные поля.
3. Нельзя удалять пустые строки, технические блоки, правила, стиль, negative prompt, мини-форму или примеры, если пользователь прямо не попросил сократить ответ.
4. Если пользователь просит “заполнить шаблон”, нужно вернуть полный шаблон с сохранением всех блоков.
5. Заполнять можно только те поля, для которых во входных данных есть явная информация.
6. Запрещено заменять отсутствующие данные догадками.
7. Запрещено придумывать новые кадры, новые сцены, новые prompt-блоки или дополнительные варианты.
8. Запрещено автоматически создавать PROMPT_2 или PROMPT_3 по аналогии с PROMPT_1.
9. Если данных для поля нет, поле остается пустым или помечается как: нет исходных данных для заполнения.
10. Если пользователь просит “только готовые промты”, можно вывести только заполненные prompt-блоки, но нельзя создавать несуществующие кадры.

ЖЕЛЕЗНОЕ ПРАВИЛО ДЛИНЫ ГОТОВОГО PROMPT:

1. Каждый готовый prompt для одного кадра должен быть не длиннее 5000 символов вместе с пробелами.
2. Рабочий безопасный лимит: до 4977 символов вместе с пробелами, чтобы не выйти за технический предел outsee.io.
3. Если prompt получается длиннее 4977 символов, его обязательно сжать до лимита до финального вывода.
4. При сокращении удалять повторы, воду, повторные перечисления стиля, дубли negative prompt, одинаковые запреты и лишние пояснения.
5. Нельзя удалять суть кадра: главный субъект, место, время, действие, фокус, свет, палитру, стиль, историческую достоверность, контекстную логику, запрет читаемого текста и запрет вымышленных деталей.
6. Запрещено отдавать prompt длиннее 5000 символов при любых условиях.
7. Перед финальным выводом обязательно проверить длину каждого prompt вместе с пробелами.
8. Если пользователь просит полный шаблон, полный файл или полный ответ, это не отменяет лимит для каждого готового prompt.

ПРАВИЛО ПРОТИВ ПОСТОЯННОЙ DETECTIVE BOARD / СТЕНЫ РАССЛЕДОВАНИЯ:

1. Не превращать каждый кадр в detective board, investigation wall, evidence board, cork board, clue map, board with strings, board with red lines или стену расследования.
2. Такие изображения допустимы не чаще чем в 5% от общего числа кадров и только в самых явно детективных сценах, где доска прямо нужна по контексту.
3. Если используется доска расследования, она должна быть реальным физическим объектом внутри сцены: доска на стене, пробковая доска, архивный стенд, полицейская доска или рабочая поверхность с разложенными материалами.
4. Линии, нити, карточки и связи могут быть только на самой доске или рабочей поверхности, а не поверх всей сцены как графический интерфейс.
5. Запрещены красные линии, стрелки, круги, target marks и evidence strings, наложенные поверх персонажей, лиц, улиц, комнат, документов или фона.
6. В обычных кадрах логика расследования передается через позу, свет, предметы сцены, следы, дистанцию между объектами, нечитаемые бумаги, направление взгляда, композицию и причинно-следственные детали.
7. Если в исходных данных нет доски, стены, карточек, нитей или прямого указания на investigation board, их нельзя добавлять.

МАРКЕРЫ КОНТЕКСТНОЙ ЛОГИКИ:

Использовать эти фразы как смысловые маркеры при составлении prompt, но не как видимый текст на изображении:
следы ведут к; всё начиналось с; история оказалась запутаннее; цепочка событий сходится; бытовая деталь становится уликой; поздняя легенда спорит с фактом; причина проявляется через последствия; прошлое оставило материальный след; версия не совпадает с реальностью; улика выглядит ненадёжной; связь между событиями становится видимой; расследование держится на мелких деталях; контекст раскрывается через предметы; обрывок факта меняет смысл сцены; легенда вырастает из бытового эпизода; сцена объясняет, почему следствие пошло дальше; визуальная логика ведёт зрителя от действия к причине; каждый предмет подтверждает контекст ролика; факты складываются не сразу; внешне простая сцена скрывает причинную связь; бытовой след важнее красивого символа; событие читается через последствия; деталь связывает прошлое и текущий кадр; образ должен работать как улика без буквального указателя; напряжение возникает из несостыковки; зритель должен почувствовать, что за кадром есть проверяемая цепочка событий.

ПРАВИЛО КОНТЕКСТНЫХ ДЕТАЛЕЙ И ЗАПРЕТА ВЫМЫСЛА:

1. На изображении обязательно должны быть визуальные детали, связанные с текстом ролика, voiceover и данными кадра.
2. Запрещено делать пустую атмосферную картинку без причинно-следственной связи с контекстом.
3. Каждый важный предмет, поза, жест, световой акцент и фон должны помогать понять, что происходит в сцене.
4. Детали можно брать только из исходных данных кадра, текста ролика, voiceover, локации, времени, исторической эпохи и описанного конфликта.
5. Запрещено добавлять вымышленные предметы, новых персонажей, новые документы, символы, карты, оружие, полицейские детали, современные элементы, случайные улики или новые сюжетные подсказки, которых нет в исходных данных.
6. Если контекст требует следов расследования, использовать только допустимые материальные признаки: изношенные поверхности, влажные следы, позы, дистанцию между объектами, пустые или нечитаемые бумаги, архивные карточки без текста, предметы эпохи и детали окружения.
7. Нельзя добавлять декоративные детали ради атмосферы. Любая деталь должна иметь логическую связь с кадром.
8. Логика сцены важнее декоративности: изображение должно объяснять контекст, а не просто выглядеть детективным.
9. Если данных для детали нет, деталь не добавляется.

ЖЕСТКОЕ ПРАВИЛО ДЛЯ КАДРА 1:

1. PROMPT ДЛЯ КАДРА 1 создается по основному шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА.
2. Для PROMPT_1 можно использовать данные только из КАДРА 1.
3. Нельзя переносить в PROMPT_1 данные из КАДРА 2 или КАДРА 3.
4. Нельзя добавлять в PROMPT_1 несуществующие детали.
5. Если КАДР 1 не описан, PROMPT_1 не создается.

ЖЕСТКОЕ ПРАВИЛО ДЛЯ КАДРА 2:

1. PROMPT ДЛЯ КАДРА 2 не генерируется заново.
2. PROMPT ДЛЯ КАДРА 2 не создается по основному шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА.
3. PROMPT ДЛЯ КАДРА 2 заполняется только прямым дословным копированием текста из двух исходных строк: строка 26 + строка 28.
4. Текст из строки 26 вставляется первым.
5. Текст из строки 28 вставляется вторым, сразу после текста из строки 26.
6. Между текстом строки 26 и текстом строки 28 сохраняется обычный перенос строки.
7. Нельзя переписывать, расширять, сокращать, стилизовать, адаптировать или дополнять текст для PROMPT_2.
8. Нельзя превращать текст из строки 26 + строки 28 в новый полноценный cinematic prompt.
9. Нельзя брать данные из КАДРА 1 для заполнения PROMPT_2.
10. Нельзя брать данные из примера с Ницше для заполнения PROMPT_2.
11. Нельзя брать данные из общего описания стиля для заполнения PROMPT_2.
12. Нельзя использовать собственную фантазию для заполнения PROMPT_2.
13. Если второго кадра нет, PROMPT ДЛЯ КАДРА 2 не заполняется.
14. Если строки 26 или 28 пустые, отсутствуют или не относятся ко второму кадру, PROMPT ДЛЯ КАДРА 2 не заполняется.
15. Если пользователь не дал явно второй кадр, не создавай второй кадр и не создавай PROMPT_2.

ЖЕСТКОЕ ПРАВИЛО ДЛЯ КАДРА 3:

1. PROMPT ДЛЯ КАДРА 3 создается только если во входных данных явно есть КАДР 3.
2. Если КАДР 3 не указан, не описан или отсутствует, PROMPT_3 запрещен.
3. Нельзя создавать третий кадр автоматически.
4. Нельзя создавать третий кадр “для полноты”.
5. Нельзя создавать третий кадр “как продолжение сцены”.
6. Нельзя создавать третий кадр “по аналогии”.
7. Нельзя создавать третий кадр из примера, стиля или фантазии.
8. Если КАДРА 3 нет, нужно указать: КАДР 3: нет исходных данных для заполнения.

Краткое описание стиля:
Trash polka + dark comic book style with black, off-white, dirty cream, charcoal, dark gray and vivid blood-red accents; raw brush smears, ink splashes, spray-paint effects, distressed paper texture, ripped poster fragments, halftone dots, rough print imperfections, realistic material surfaces and intense high-contrast mixed media energy.

STYLE_LABEL =
Trash Polka Noir Comic Grunge Poster Illustration

STYLE_CORE =
trash polka + noir comic + graphic novel + grunge poster art + distressed printmaking + high-contrast mixed media illustration.

STYLE_LOCK_RULE =
not clean minimalist style, not photorealism, not glossy 3D, not cute, not pastel, not bright cheerful colors, not separate collage panels inside one image, not multiple visual frames inside one generated image, not automatic detective board, not investigation wall unless explicitly required by source data.

QUALITY_VECTOR =
intense gritty cinematic impact, strong silhouette design, poster-like composition, high visual tension, controlled chaotic energy, realistic historical-material grounding, clear context logic based only on source data.

RENDERING_VECTOR =
raw brush smears, ink splashes, spray-paint effects, distressed paper, ripped poster fragments, halftone dots, rough print imperfections, gritty comic inking, graphic overlays, dynamic red slashes.

TEXTURE_VECTOR =
distressed printmaking, torn paper, rough ink, spray texture, halftone grain, dirty cream paper, grunge scratches, smeared charcoal shadows, analog print noise, damaged archive-paper surface, imperfect screenprint texture.

REALISM_TEXTURE_VECTOR =
realistic material texture, aged cracked plaster, chipped paint, worn dark wood grain, scratched metal, damp stone, dust in corners, mud traces, faded fabric, stained paper fibers, folded paper edges, moisture stains, old varnish, worn floorboards, rough wall surface, believable grime and tactile historical wear.

RED_GRAPHIC_RULE =
Use vivid blood-red accents only as rough brush slashes, jagged paint marks, smeared ink, torn poster fragments, print interference, distressed red scratches, spray texture and abstract graphic stress marks integrated into the environment. Do not use red circles, evidence circles, target rings, circular highlight marks, red outlines around clues, red arrows pointing at evidence or literal investigation-board markings.

LINEWORK_VECTOR =
gritty comic inking, bold dramatic framing, rough contour lines, heavy graphic shadow masses, expressive visual storytelling.

LIGHT_VECTOR =
exaggerated noir lighting, harsh backlight or cold side light, pale winter glow or controlled amber-gray interior light, deep shadows, strong silhouette separation, believable directional light.

COLOR_VECTOR =
black, off-white, dirty beige, dirty cream, charcoal, dark gray, muted amber-gray and vivid blood-red accents.

COMPOSITION_VECTOR =
one unified scene, not a collage; chaotic collage energy translated into integrated poster composition, single focal point, dramatic perspective, high visual tension, clear spatial depth, no detective board logic unless the source data explicitly requires a real physical board.

PROMPT_LENGTH_RULE =
every finished prompt must be no longer than 5000 characters including spaces; target limit is 4977 characters; compress repetitions, duplicated style lists, repeated negative rules and filler while preserving subject, location, time, action, focus, light, palette, style, context logic, historical accuracy, no readable text and no invented details.

DETECTIVE_BOARD_RULE =
do not turn normal scenes into detective boards, investigation walls, clue maps, cork boards, evidence boards, boards with strings or boards with red lines; such imagery is allowed in no more than 5% of frames and only when explicitly justified by the source context; if present, the board must be a real physical object inside the scene and all strings, cards and links must stay on that board, not overlaid across the image.

CONTEXT_LOGIC_VECTOR =
traces lead toward the cause; everything began with a small material detail; the story is more tangled than it first appears; factual traces conflict with later legend; everyday evidence explains the event; the scene must reveal cause and consequence through objects, posture, light, distance and period-correct environment, without invented clues.

CONTEXT_DETAIL_RULE =
Every important visible detail must come from the frame data, voiceover, video context, location, time period or stated conflict. Do not add fictional clues, extra documents, new people, weapons, maps, police props, random symbols or decorative story hints. If a detail is not supported by the source data, leave it out.

MATERIAL_REALISM_RULE =
The scene must feel tactile, old, inhabited and physically believable. Every object should have a surface: paper fibers, dust, moisture, cracks, wood grain, metal patina, worn fabric, dirty floor texture, chipped paint or age marks. Do not let the image become a flat decorative poster without real materials.

SUBJECT_RULES =
any silhouette, historical figure, urban object, psychological thriller scene, investigation scene, archival scene, institutional room, crime fragment, object-based clue, abstract noir scene or documentary historical environment.

TEXT_RULE =
no letters, no words, no numbers, no readable signs, no symbols forming text. All documents, calendars, books, labels, street signs and papers must be blank, obscured, blurred, scraped, stained, folded, torn or illegible.

GORE_RULE =
psychological crime-thriller mood without gore, explicit violence or graphic injury unless the user directly requests otherwise.

HISTORICAL_DETAIL_RULE =
If the scene is historical, all objects, clothing, furniture, lighting sources and architecture must fit the period. Avoid modern hospital equipment, modern signs, modern clothing, plastic objects, contemporary furniture, digital screens, clean clinical interiors and random anachronistic details.

ANTI_SYMBOL_RULE =
Do not rely on metaphorical symbols unless the user explicitly asks. Keep the scene grounded in material objects, human posture, architecture, documents, light, texture, spatial tension and context logic from the source. Do not use investigation-board symbols as a default storytelling device.

МИНИ-ФОРМА ДЛЯ КАДРА 1:

MAIN_SUBJECT =
SETTING =
TIME_PERIOD =
ACTION_OR_STATE =
NOIR_LIGHTING =
RED_GRAPHIC_ACCENTS =
REALISM_TEXTURES =
GRUNGE_TEXTURES =
COMIC_FRAME_ELEMENTS =
FOCAL_POINT =
MOOD =
CONTEXT_LOGIC =
BOARD_USAGE =
HISTORICAL_OR_MATERIAL_DETAILS =
TEXT_RESTRICTIONS =
CONTEXT_SPECIFIC_NEGATIVES =

МИНИ-ФОРМА ДЛЯ КАДРА 2:

PROMPT_2_SOURCE_LINE_26 =
PROMPT_2_SOURCE_LINE_28 =

ВАЖНО:
КАДР 2 не заполняется через MAIN_SUBJECT, SETTING, TIME_PERIOD, ACTION_OR_STATE и другие поля.
КАДР 2 не превращается в новый полный prompt.
PROMPT_2 состоит только из дословного текста строки 26 + строки 28.

МИНИ-ФОРМА ДЛЯ КАДРА 3:

MAIN_SUBJECT =
SETTING =
TIME_PERIOD =
ACTION_OR_STATE =
NOIR_LIGHTING =
RED_GRAPHIC_ACCENTS =
REALISM_TEXTURES =
GRUNGE_TEXTURES =
COMIC_FRAME_ELEMENTS =
FOCAL_POINT =
MOOD =
CONTEXT_LOGIC =
BOARD_USAGE =
HISTORICAL_OR_MATERIAL_DETAILS =
TEXT_RESTRICTIONS =
CONTEXT_SPECIFIC_NEGATIVES =

ВАЖНО:
КАДР 3 используется только если пользователь явно дал третий кадр.
Если третьего кадра нет, эта мини-форма не заполняется.

СТИЛЬ:
Trash Polka Noir Comic Grunge Poster Illustration

PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1 ИЛИ ЯВНО СУЩЕСТВУЮЩЕГО КАДРА 3:

Create one unified scene, not a collage, not multiple visual panels inside one image, in a trash polka + noir dark comic book + graphic novel style.

Show [MAIN_SUBJECT] in [SETTING], during [TIME_PERIOD], [ACTION_OR_STATE], with a [MOOD] crime-thriller and psychological tension atmosphere. The scene must feel historically grounded, physically believable and material, not symbolic, not theatrical, not modernized.

Context logic: [CONTEXT_LOGIC]. Every visible detail must come from the source frame, video text, voiceover, location, time period or stated conflict. Do not add fictional clues, new objects, extra characters, random detective props or decorative story hints.

Composition: use one cinematic frame with a single strong focal point: [FOCAL_POINT]. Build the scene with dramatic perspective, rule of thirds, strong silhouette design, bold noir framing and clear spatial depth. The chaotic trash polka energy must be integrated into one unified poster-like composition, not split into collage panels. Board usage: [BOARD_USAGE]. Do not create a detective board, investigation wall, clue map, strings or red connecting lines unless the source data explicitly requires a real physical board inside the scene.

Lighting: use [NOIR_LIGHTING] to separate the subject from the environment. Keep the light believable and directional, with cold side light, pale sky glow, controlled amber-gray interior tones, deep shadows and heavy graphic shadow masses.

Style: emphasize trash polka aesthetics through [GRUNGE_TEXTURES], raw brush smears, ink splashes, spray-paint effects, distressed paper texture, ripped poster fragments, halftone dots, rough print imperfections, analog print noise, high-contrast graphic overlays and gritty comic inking.

Red accents: use [RED_GRAPHIC_ACCENTS] only as jagged red brush slashes, smeared paint, distressed print interference, torn poster fragments, red ink splashes, rough scratches and abstract stress marks integrated into the environment. Do not use red evidence circles, target rings, circular highlight marks, red outlines around clues or literal investigation-board markings.

Realism texture: add [REALISM_TEXTURES] with tactile historical material detail: aged cracked plaster, chipped paint, worn wood grain, scratched metal, damp stone, dust, mud, faded fabric, stained paper fibers, folded edges, moisture stains, subtle grime and believable surface wear. The scene must feel old, inhabited and physically real beneath the graphic style.

Historical/material details: include [HISTORICAL_OR_MATERIAL_DETAILS]. All objects must belong to the scene and time period and must be supported by source data. Avoid random modern details, invented clues, extra documents, new props, clean clinical looks, plastic objects, contemporary furniture, digital screens or modern signage.

Text rule: [TEXT_RESTRICTIONS]. Everything must remain non-readable: no letters, no words, no numbers and no symbols forming text. All documents, calendars, books, labels, street signs and papers must be blank, obscured, blurred, scraped, stained, folded, torn or illegible.

Final style lock: unified cinematic frame, trash polka, noir comic, graphic novel, grunge poster art, distressed printmaking, realistic historical texture, high-contrast mixed media illustration, gritty comic inking, rough contour lines, heavy shadow masses, halftone grain, dirty paper surface, raw brush smears, ink splashes, spray-paint distress, black, off-white, dirty cream, charcoal, dark gray, muted amber-gray and vivid blood-red slashes only, no red circles, no readable text, no automatic detective board, no invented details.

PROMPT ДЛЯ КАДРА 2:

[PROMPT_2_SOURCE_LINE_26]
[PROMPT_2_SOURCE_LINE_28]

ВАЖНО:
Это единственный допустимый формат PROMPT_2.
PROMPT_2 нельзя переписывать.
PROMPT_2 нельзя расширять.
PROMPT_2 нельзя превращать в полноценный cinematic prompt.
PROMPT_2 нельзя генерировать по стилевому шаблону.
PROMPT_2 должен быть только дословной склейкой строки 26 и строки 28 с переносом строки между ними.

NEGATIVE PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1 ИЛИ ЯВНО СУЩЕСТВУЮЩЕГО КАДРА 3:

text, letters, words, numbers, readable signs, captions, subtitles, logo, watermark, collage panels, multiple visual panels inside one image, evidence circles, red circles, target rings, circular highlight marks, red outlines around clues, red arrows pointing at evidence, literal evidence-board strings, automatic detective board, investigation wall, clue map, board with strings, board with red lines, invented clues, extra documents, new characters, random detective props, clean minimalist style, photorealism, glossy 3D render, cute style, pastel palette, bright cheerful colors, low detail, blurry, gore, explicit violence, modern objects, modern clothing, modern hospital equipment, fluorescent lighting, contemporary furniture, clean hospital look, surreal symbols, fantasy imagery, readable handwriting, readable documents, book titles, file labels, street signs, paper text, [CONTEXT_SPECIFIC_NEGATIVES]

NEGATIVE PROMPT ДЛЯ КАДРА 2:

Не создавать, если пользователь не дал отдельный negative prompt для кадра 2.
Не генерировать автоматически.
Не копировать negative prompt от кадра 1.
Не придумывать negative prompt для кадра 2.
Если нужен negative prompt для кадра 2, он должен быть явно дан пользователем.

ФОРМАТ ВЫВОДА, ЕСЛИ ЕСТЬ ТОЛЬКО КАДР 1:

КАДР 1 / PROMPT_1:
[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1]

КАДР 1 / NEGATIVE PROMPT_1:
[заполненный negative prompt]

КАДР 2 / PROMPT_2:
нет исходных данных для заполнения

КАДР 3 / PROMPT_3:
нет исходных данных для заполнения

ФОРМАТ ВЫВОДА, ЕСЛИ ЕСТЬ КАДР 1 И ДАННЫЕ ДЛЯ КАДРА 2 В СТРОКАХ 26 И 28:

КАДР 1 / PROMPT_1:
[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1]

КАДР 1 / NEGATIVE PROMPT_1:
[заполненный negative prompt]

КАДР 2 / PROMPT_2:
[дословный текст строки 26]
[дословный текст строки 28]

КАДР 3 / PROMPT_3:
нет исходных данных для заполнения

ФОРМАТ ВЫВОДА, ЕСЛИ ЕСТЬ КАДР 1, КАДР 2 И ЯВНО ДАН КАДР 3:

КАДР 1 / PROMPT_1:
[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1]

КАДР 1 / NEGATIVE PROMPT_1:
[заполненный negative prompt]

КАДР 2 / PROMPT_2:
[дословный текст строки 26]
[дословный текст строки 28]

КАДР 3 / PROMPT_3:
[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 3]

КАДР 3 / NEGATIVE PROMPT_3:
[заполненный negative prompt]

КОНТРОЛЬНАЯ ПРОВЕРКА ПЕРЕД ОТВЕТОМ:

Перед финальным ответом проверь:

1. Не удалены ли исходные блоки шаблона.
2. Не удалены ли пустые поля.
3. Не создан ли PROMPT_2 как новый промт.
4. PROMPT_2 состоит только из строки 26 + строки 28.
5. Не добавлены ли в PROMPT_2 слова, которых не было в строках 26 и 28.
6. Не создан ли PROMPT_3 без явно данного третьего кадра.
7. Не скопированы ли данные КАДРА 1 в КАДР 2.
8. Не использован ли пример с Ницше как источник для КАДРА 2.
9. Не создан ли negative prompt для КАДРА 2 автоматически.
10. Если второго кадра нет, указано ли: нет исходных данных для заполнения.
11. Если третьего кадра нет, указано ли: нет исходных данных для заполнения.
12. Проверена ли длина каждого готового prompt: не более 5000 символов вместе с пробелами, целевой лимит до 4977 символов.
13. Удалены ли повторы, вода и дубли правил, если prompt был длиннее лимита.
14. Не превращен ли обычный кадр в detective board, investigation wall или доску с нитями без прямого основания в исходных данных.
15. Если доска расследования есть, является ли она физическим объектом внутри сцены, а не графической накладкой поверх изображения.
16. Есть ли в кадре контекстная логика из текста ролика, voiceover и данных кадра.
17. Не добавлены ли вымышленные предметы, новые персонажи, случайные улики, современные детали или декоративные подсказки без исходных данных.

ПРИМЕР ЗАПОЛНЕНИЯ ДЛЯ СЦЕНЫ С ФРИДРИХОМ НИЦШЕ:

MAIN_SUBJECT =
Friedrich Nietzsche sitting alone in a late-19th-century psychiatric clinic room, physically fragile, withdrawn, with a remote tired stare, iconic large mustache, thinning hair, pale skin and dark worn period clothing.

SETTING =
a modest German psychiatric ward interior with a narrow iron bed, small wooden table, washstand, cracked plaster walls, closed wooden door and tall window.

TIME_PERIOD =
late 1880s or early 1890s.

ACTION_OR_STATE =
seated on a simple wooden chair, slightly hunched, hands resting in his lap or loosely gripping the chair, silent and mentally distant.

NOIR_LIGHTING =
cold rain-washed side light from the window, pale winter sky glow, muted amber-gray interior tones, deep soft shadows and realistic chiaroscuro.

RED_GRAPHIC_ACCENTS =
rough blood-red brush slashes, distressed print interference, torn red poster fragments and smeared red ink stress marks integrated into the room, with no circles or target marks.

REALISM_TEXTURES =
aged cracked plaster, chipped paint, worn dark wood grain, scratched chair legs, iron bedframe patina, thin institutional bedding, dusty corners, damp floor texture, faded fabric, paper fibers, moisture stains, rough ink scratches and analog print noise.

GRUNGE_TEXTURES =
distressed paper texture, halftone grain, rough print imperfections, smeared charcoal shadows, spray texture, ripped poster fragments and dirty cream paper.

COMIC_FRAME_ELEMENTS =
bold dramatic framing, gritty comic inking, rough contour lines, heavy graphic shadow masses, strong silhouette design and poster-like composition.

FOCAL_POINT =
Nietzsche seated alone in the room.

MOOD =
archival crime-thriller mood of control, silence, breakdown and disputed legacy.

CONTEXT_LOGIC =
the scene shows a material trace of breakdown and institutional control through posture, room details, period furniture and silence, without invented clues.

BOARD_USAGE =
no detective board, no investigation wall, no strings, no clue map; context is shown through physical environment and posture.

HISTORICAL_OR_MATERIAL_DETAILS =
period-appropriate wooden furniture, iron bed, old institutional bedding, no modern medical equipment, no modern lighting, no plastic objects.

TEXT_RESTRICTIONS =
all papers and surfaces must be blank, obscured or illegible.

CONTEXT_SPECIFIC_NEGATIVES =
caricature face, exaggerated madness, comic parody, modern psychiatric ward, clean white hospital, readable medical documents, readable wall labels.

ПРИМЕР ГОТОВОГО PROMPT_1:

Create one unified scene, not a collage, not multiple visual panels inside one image, in a trash polka + noir dark comic book + graphic novel style.

Show Friedrich Nietzsche sitting alone in a late-19th-century psychiatric clinic room, physically fragile, withdrawn, with a remote tired stare, iconic large mustache, thinning hair, pale skin and dark worn period clothing, in a modest German psychiatric ward interior with a narrow iron bed, small wooden table, washstand, cracked plaster walls, closed wooden door and tall window, during late 1880s or early 1890s, seated on a simple wooden chair, slightly hunched, hands resting in his lap or loosely gripping the chair, silent and mentally distant, with an archival crime-thriller mood of control, silence, breakdown and disputed legacy. The scene must feel historically grounded, physically believable and material, not symbolic, not theatrical, not modernized.

Context logic: the scene shows a material trace of breakdown and institutional control through posture, room details, period furniture and silence, without invented clues. Every visible detail must come from the source frame, video text, voiceover, location, time period or stated conflict. Do not add fictional clues, new objects, extra characters, random detective props or decorative story hints.

Composition: use one cinematic frame with a single strong focal point: Nietzsche seated alone in the room. Build the scene with dramatic perspective, rule of thirds, strong silhouette design, bold noir framing and clear spatial depth. The chaotic trash polka energy must be integrated into one unified poster-like composition, not split into collage panels. Board usage: no detective board, no investigation wall, no strings, no clue map; context is shown through physical environment and posture. Do not create a detective board, investigation wall, clue map, strings or red connecting lines unless the source data explicitly requires a real physical board inside the scene.

Lighting: use cold rain-washed side light from the window, pale winter sky glow, muted amber-gray interior tones, deep soft shadows and realistic chiaroscuro to separate the subject from the environment. Keep the light believable and directional, with cold side light, pale sky glow, controlled amber-gray interior tones, deep shadows and heavy graphic shadow masses.

Style: emphasize trash polka aesthetics through distressed paper texture, halftone grain, rough print imperfections, smeared charcoal shadows, spray texture, ripped poster fragments and dirty cream paper, raw brush smears, ink splashes, spray-paint effects, distressed paper texture, ripped poster fragments, halftone dots, rough print imperfections, analog print noise, high-contrast graphic overlays and gritty comic inking.

Red accents: use rough blood-red brush slashes, distressed print interference, torn red poster fragments and smeared red ink stress marks integrated into the room, with no circles or target marks, only as jagged red brush slashes, smeared paint, distressed print interference, torn poster fragments, red ink splashes, rough scratches and abstract stress marks integrated into the environment. Do not use red evidence circles, target rings, circular highlight marks, red outlines around clues or literal investigation-board markings.

Realism texture: add aged cracked plaster, chipped paint, worn dark wood grain, scratched chair legs, iron bedframe patina, thin institutional bedding, dusty corners, damp floor texture, faded fabric, paper fibers, moisture stains, rough ink scratches and analog print noise with tactile historical material detail: aged cracked plaster, chipped paint, worn wood grain, scratched metal, damp stone, dust, mud, faded fabric, stained paper fibers, folded edges, moisture stains, subtle grime and believable surface wear. The scene must feel old, inhabited and physically real beneath the graphic style.

Historical/material details: include period-appropriate wooden furniture, iron bed, old institutional bedding, no modern medical equipment, no modern lighting, no plastic objects. All objects must belong to the scene and time period and must be supported by source data. Avoid random modern details, invented clues, extra documents, new props, clean clinical looks, plastic objects, contemporary furniture, digital screens or modern signage.

Text rule: all papers and surfaces must be blank, obscured or illegible. Everything must remain non-readable: no letters, no words, no numbers and no symbols forming text. All documents, calendars, books, labels, street signs and papers must be blank, obscured, blurred, scraped, stained, folded, torn or illegible.

Final style lock: unified cinematic frame, trash polka, noir comic, graphic novel, grunge poster art, distressed printmaking, realistic historical texture, high-contrast mixed media illustration, gritty comic inking, rough contour lines, heavy shadow masses, halftone grain, dirty paper surface, raw brush smears, ink splashes, spray-paint distress, black, off-white, dirty cream, charcoal, dark gray, muted amber-gray and vivid blood-red slashes only, no red circles, no readable text, no automatic detective board, no invented details.

ПРИМЕР ГОТОВОГО NEGATIVE PROMPT_1:

text, letters, words, numbers, readable signs, captions, subtitles, logo, watermark, collage panels, multiple visual panels inside one image, evidence circles, red circles, target rings, circular highlight marks, red outlines around clues, red arrows pointing at evidence, literal evidence-board strings, automatic detective board, investigation wall, clue map, board with strings, board with red lines, invented clues, extra documents, new characters, random detective props, clean minimalist style, photorealism, glossy 3D render, cute style, pastel palette, bright cheerful colors, low detail, blurry, gore, explicit violence, modern objects, modern clothing, modern hospital equipment, fluorescent lighting, contemporary furniture, clean hospital look, surreal symbols, fantasy imagery, readable handwriting, readable documents, book titles, file labels, street signs, paper text, caricature face, exaggerated madness, comic parody, modern psychiatric ward, clean white hospital, readable medical documents, readable wall labels

ПРИМЕР ДЛЯ PROMPT_2:

Если строка 26 содержит:
[пример текста строки 26]

И строка 28 содержит:
[пример текста строки 28]

То PROMPT_2 должен быть строго таким:

КАДР 2 / PROMPT_2:
[пример текста строки 26]
[пример текста строки 28]

Нельзя превращать это в:

Create one unified scene...
Show...
Composition...
Lighting...
Style...

Такой вариант для PROMPT_2 запрещен, потому что он является новым сгенерированным промтом.