# Style pack: plasticine

STYLE_LABEL = Claymation Plasticine 2D-Look Miniature Illustration
STYLE_CORE = claymation / plasticine 2D-look + handcrafted miniature scene + stop-motion charm.
STYLE_LOCK_RULE = not sharp vector, not photorealism, not glossy 3D, not realistic anatomy, not neon, not cluttered realism.
QUALITY_VECTOR = handmade charm, clear narrative, soft rounded shapes, visible fingerprints, matte clay surface.
RENDERING_VECTOR = plasticine-like forms, simplified stylized proportions, soft edges, handcrafted props, miniature environment.
TEXTURE_VECTOR = fingerprints, matte clay, soft sculpted surfaces, dusty handmade ground, clay clouds, rough handmade objects.
LINEWORK_VECTOR = no sharp vector lines; forms separated by sculpted edges and soft shadows.
LIGHT_VECTOR = soft shadows, diffused light, muted gentle atmosphere.
COLOR_VECTOR = muted earthy colors, gray, dusty green, dark blue, beige, brown, cloudy tones.
COMPOSITION_VECTOR = central narrative, simple environment, clear foreground subject, symbolic props, no clutter.
SUBJECT_RULES = any stylized person, creature, object, workplace, educational situation or symbolic miniature scene.
TEXT_RULE = no readable text, no letters, no numbers, no logos; signs and labels must be blank.
GORE_RULE = not applicable; keep gentle, not scary.

PROMPT_TEMPLATE =
Create a single claymation-style 2D-look illustration of [MAIN_SUBJECT], [ACTION_OR_STATE], in [SETTING]. The scene should feel handmade, slightly melancholic but not scary, with a clear central narrative. Build everything from plasticine-like forms: soft rounded edges, visible fingerprints, matte texture, slight imperfections and simple stylized proportions. Add [BACKGROUND_OBJECT], [SYMBOLIC_PROP] and [GROUND_OR_PATH] only if they support the story. Use [COLOR_PALETTE], muted earthy colors, soft shadows and a handcrafted miniature atmosphere. The focal point is [FOCAL_POINT], clearly readable through pose, placement and simplified shapes. All signs, labels or boards must remain blank. Final style lock: claymation / plasticine 2D-look, handcrafted miniature scene, soft rounded edges, fingerprints, matte texture, slight imperfections, muted earthy colors, soft shadows, vintage educational illustration mood.

NEGATIVE_CORE = text, letters, numbers, logos, brand names, readable signs, sharp digital vector art, photorealism, glossy 3D render, realistic human anatomy, bright neon colors, modern city background, clutter, low detail, blurry, watermark, frame

SLOT_KEYS = MAIN_SUBJECT, ACTION_OR_STATE, SETTING, BACKGROUND_OBJECT, SYMBOLIC_PROP, GROUND_OR_PATH, COLOR_PALETTE, FOCAL_POINT, MOOD, CONTEXT_SPECIFIC_NEGATIVES
