# Style pack: noir-bloody

STYLE_LABEL = Noir Graphic Novel True-Crime Thriller Poster
STYLE_CORE = noir graphic novel + gritty crime thriller poster + true-crime documentary title-image mood.
STYLE_LOCK_RULE = not bright colors, not clean vector, not cute style, not photorealism, not collage, not separate panels, not glossy render.
QUALITY_VECTOR = dramatic composition, rough printed texture, tense atmosphere, heavy shadow design, cinematic graphic clarity.
RENDERING_VECTOR = black shadow masses, dirty paper highlights, blood-red accents, halftone grain, scratch marks, rough ink, newspaper-like fragments, wet reflections if needed.
TEXTURE_VECTOR = rough printed texture, halftone grain, scratch marks, rough ink, dirty paper fragments, wet pavement or rain texture when relevant.
LINEWORK_VECTOR = gritty inking, graphic shadow edges, expressive noir outlines.
LIGHT_VECTOR = exaggerated noir lighting, backlit mystery, deep black shadows, sharp dirty highlights.
COLOR_VECTOR = black, charcoal, dirty cream, dark brown, blood-red accents.
COMPOSITION_VECTOR = one unified crime thriller scene, no collage, no panels, strong focal figure or object, dramatic perspective, high tension.
SUBJECT_RULES = any detective scene, crime mystery, archive room, investigation wall, urban thriller environment, clue object or silhouette.
TEXT_RULE = no words, no readable documents, no logos, no title card.
GORE_RULE = no explicit gore unless requested.

PROMPT_TEMPLATE =
Create one unified dark noir true-crime scene, not a collage and not multiple panels. Show [MAIN_SUBJECT] in [SETTING], [ACTION_OR_STATE], with a [MOOD] investigative and dangerous atmosphere. Add [INVESTIGATION_ELEMENTS] as supporting details, but keep everything non-readable with no words, no documents that can be read and no logos. Use [TEXTURE_DETAILS], halftone grain, scratch marks, rough ink, newspaper-like fragments, black shadow masses, dirty highlights and [RED_ACCENTS]. Lighting comes from [LIGHT_SOURCE], creating exaggerated noir contrast, deep black shadows and dramatic graphic storytelling. The focal point is [FOCAL_POINT], supported by perspective, silhouette and color accents. Final style lock: noir graphic novel, gritty crime thriller poster, heavy black shadows, limited black charcoal dirty cream dark brown and blood-red palette, high contrast, rough printed texture, cinematic tension, dramatic composition.

NEGATIVE_CORE = text, words, letters, numbers, title card, captions, subtitles, readable documents, logos, watermark, collage, separate comic panels, bright colors, clean vector art, cute style, photorealism, low detail, blurry, extra detectives, gore

SLOT_KEYS = MAIN_SUBJECT, SETTING, ACTION_OR_STATE, INVESTIGATION_ELEMENTS, LIGHT_SOURCE, RED_ACCENTS, TEXTURE_DETAILS, FOCAL_POINT, MOOD, CONTEXT_SPECIFIC_NEGATIVES
