# Source Full: 04_hero_style/norm.md

Полный исходный промт сохранён без сокращений.

---

{

  "master_visual_prompt_core": {

    "purpose": "Создавать изображения только антропоморфных котов-персонажей в едином стиле mature cinematic pixel art, без фона и без описания окружения.",

    "main_rule": "В кадре могут быть только антропоморфные коты. Никаких людей, человеческих персонажей или других антропоморфных животных.",

    "background_rule": {

      "instruction": "Фон не описывать и не добавлять.",

      "allowed": [

        "no background",

        "transparent background",

        "plain empty background",

        "isolated character"

      ],

      "forbidden": [

        "environment",

        "scenery",

        "landscape",

        "room",

        "city",

        "interior",

        "props in background",

        "crowd in background"

      ]

    },

    "visual_style": {

      "core_style": "mature cinematic pixel art",

      "required_style_terms": [

        "ultra detailed cinematic pixel art",

        "pixel-painted cinematic character design",

        "premium pixel-art animation still",

        "premium animated poster quality",

        "near-invisible pixel grid",

        "subpixel shading",

        "soft dithering",

        "polished pixel rendering",

        "crisp pixel-painted texture",

        "ultra crisp pixel-level details",

        "stylized filmic color grading",

        "pixel-art tone mapping",

        "detailed fur pixel rendering",

        "clear stylized material separation",

        "dramatic light and shadow"

      ],

      "style_description": "Пиксельность должна быть тонкой и дорогой: не крупные пиксели, не retro sprite, не 8-bit, а почти незаметная pixel-painted фактура на шерсти, одежде, тенях и световых градиентах.",

      "forbidden_styles": [

        "photorealism",

        "hyperrealism",

        "raw photo",

        "live-action",

        "3D render",

        "videogame art",

        "AAA game art",

        "anime",

        "cel shading",

        "flat cartoon",

        "cute mascot",

        "chibi",

        "kawaii",

        "retro 8-bit",

        "sprite art",

        "large pixels",

        "primitive pixel art",

        "mobile game look"

      ]

    },

    "character_world_rules": {

      "only_intelligent_characters": "anthropomorphic cats",

      "mandatory_phrase": "anthropomorphic cat as the only intelligent character type, no humans present, no human characters, only anthropomorphic cats can appear as intelligent characters",

      "forbidden_character_types": [

        "humans",

        "human faces",

        "human bodies",

        "human hands",

        "human silhouettes",

        "background humans",

        "anthropomorphic dogs",

        "anthropomorphic foxes",

        "anthropomorphic wolves",

        "anthropomorphic birds",

        "anthropomorphic reptiles",

        "anthropomorphic mice",

        "anthropomorphic rabbits",

        "any non-cat anthropomorphic creatures",

        "mixed species crowd"

      ]

    },

    "cat_character_design": {

      "mandatory_features": [

        "clearly readable anthropomorphic cat anatomy",

        "cat muzzle",

        "cat ears",

        "visible whiskers",

        "fur-covered face and body",

        "cat-like silhouette",

        "feline facial structure",

        "serious mature cinematic expression",

        "not cute",

        "not mascot-like",

        "not childish"

      ],

      "body_rules": [

        "all visible body parts covered with fur",

        "no bare human skin",

        "no exposed skin on hands, arms, neck, chest, legs or fingers",

        "fully clothed character",

        "no nudity",

        "no semi-nude look",

        "no wild naked animal look"

      ],

      "hands_and_fingers": {

        "required": [

          "human-shaped fingers",

          "clear palm structure",

          "functional hands able to hold objects",

          "fur-covered hands",

          "fur-covered fingers",

          "subtle claws may exist but must not replace human-shaped fingers"

        ],

        "forbidden": [

          "animal paws",

          "shapeless paws",

          "paw mittens",

          "beast hands",

          "non-human fingers",

          "bare human hands",

          "skin fingers"

        ]

      },

      "teeth": {

        "required": [

          "human-like teeth",

          "even tooth size",

          "regular human-style tooth row",

          "no visible animal fangs"

        ],

        "forbidden": [

          "cat teeth",

          "feline teeth",

          "fangs",

          "long canines",

          "sharp animal teeth",

          "saber teeth",

          "protruding fangs",

          "enlarged canines",

          "oversized canines",

          "uneven teeth",

          "mixed tooth sizes"

        ]

      }

    },

    "fur_rendering": {

      "main_rule": "Шерсть — ключевой элемент качества персонажа.",

      "required_description": [

        "каждый волосок читается как pixel-painted фактура",

        "тонкий subpixel shading по прядям",

        "soft dithering в тенях шерсти",

        "отдельные пряди через ручную пиксельную штриховку",

        "объёмный мех без пластиковой гладкости",

        "микроконтраст между прядями",

        "тёмные прослойки в глубине меха",

        "rim light по краю силуэта",

        "шерсть связана со светом и формой тела"

      ],

      "forbidden": [

        "smooth fur",

        "plastic fur",

        "low detail fur",

        "blurry fur",

        "flat fur",

        "hairless body parts"

      ]

    },

    "clothing_and_materials": {

      "main_rule": "Все персонажи должны быть одеты. Одежда должна соответствовать роли и эпохе, но не превращаться в фотореализм.",

      "required": [

        "fully clothed",

        "clear stylized material separation",

        "pixel-painted fabric texture",

        "visible folds",

        "worn edges",

        "subpixel shading on fabric",

        "contact shadows between clothing and fur"

      ],

      "allowed_material_logic": [

        "linen",

        "wool",

        "leather",

        "rough fabric",

        "belts",

        "robes",

        "tunics",

        "cloaks",

        "historical clothing if needed",

        "work clothing if needed",

        "ritual clothing if needed"

      ],

      "forbidden": [

        "nude",

        "naked",

        "semi-nude",

        "exposed body",

        "bare skin",

        "modern clothing unless explicitly requested",

        "plastic-looking clothes",

        "flat fabric without texture"

      ]

    },

    "lighting_on_character": {

      "main_rule": "Свет должен подчёркивать форму персонажа, шерсть, одежду и лицо, без описания фона.",

      "required_terms": [

        "strong chiaroscuro",

        "low-key cinematic lighting",

        "deep black levels",

        "complex shadow gradients",

        "contact shadows",

        "soft ambient occlusion",

        "rim light",

        "stylized light bounce",

        "dramatic light and shadow"

      ],

      "character_light_details": [

        "свет подчёркивает кошачью морду и уши",

        "тонкий rim light отделяет силуэт",

        "блики ложатся на усы и шерсть",

        "тени в складках одежды",

        "мягкая окклюзия под подбородком, руками и одеждой",

        "цветовые рефлексы на шерсти и ткани"

      ]

    },

    "composition_without_background": {

      "main_rule": "Композиция строится вокруг персонажа, силуэта, лица, жеста и одежды, без окружения.",

      "required": [

        "vertical 9:16 composition",

        "strong readable character silhouette",

        "rule of thirds",

        "golden ratio",

        "clear focal point on face, eyes, hands or gesture",

        "premium animated poster quality character framing"

      ],

      "allowed_shots": [

        "full body character portrait",

        "medium shot",

        "medium close-up",

        "close-up character portrait",

        "character turnaround style only if requested"

      ],

      "forbidden": [

        "weak focal point",

        "boring centered pose",

        "flat frontal pose without depth",

        "character lost in details",

        "background-dependent composition"

      ]

    },

    "positive_prompt_template": "Изолированный персонаж без фона: серьёзный anthropomorphic cat в стиле mature cinematic pixel art, no background, transparent background, isolated character, ultra detailed cinematic pixel art, pixel-painted cinematic character design, premium pixel-art animation still, near-invisible pixel grid, subpixel shading, soft dithering, polished pixel rendering, crisp pixel-painted texture. Персонаж полностью одет, все видимые части тела покрыты шерстью, кошачья морда, кошачьи уши, усы, выразительный зрелый взгляд, human-shaped fingers с шерстью на пальцах и ладонях, human-like teeth of even size, no animal fangs. Шерсть объёмная и живая: отдельные пряди читаются как pixel-painted фактура, мягкий subpixel shading, тёмные прослойки между прядями, rim light по краю силуэта, контактные тени под одеждой и руками. Одежда с clear stylized material separation, pixel-painted fabric texture, складки, потёртые края, микроконтраст материалов. Кинематографичный свет по персонажу: strong chiaroscuro, low-key cinematic lighting, complex shadow gradients, soft ambient occlusion, stylized light bounce, dramatic light and shadow. Vertical 9:16 composition, strong readable character silhouette, focal point on face and gesture, premium animated poster quality, one consistent cinematic pixel art style only.",

    "negative_prompt_core": "humans, people, human characters, human faces, human bodies, human hands, human silhouettes, background humans, non-cat anthropomorphic animals, anthropomorphic dogs, anthropomorphic foxes, anthropomorphic wolves, anthropomorphic birds, anthropomorphic reptiles, anthropomorphic mice, anthropomorphic rabbits, mixed species crowd, exposed body, unclothed character, bare skin, exposed skin, hairless body parts, skin arms, skin hands, skin fingers, animal paws, shapeless paws, paw mittens, non-human fingers, beast hands, animal teeth, cat teeth, feline teeth, fangs, long canines, sharp animal teeth, saber teeth, protruding fangs, enlarged canines, oversized canines, visible fangs, uneven teeth, mixed tooth sizes, background, scenery, environment, landscape, room, city, crowd, props in background, cartoon look, cute mascot, kawaii, chibi, childish design, goofy face, exaggerated cartoon eyes, simple face, sticker look, vector look, mobile game look, cheap frame, hyperrealism, photorealism, raw photorealistic photo, photographic realism, documentary photo, live-action look, 3D render look, videogame art, game cinematic look, large-pixel retro pixel art look, flat pixel art, visible large pixels, sprite art, game sprite, primitive pixel art, retro 8-bit look, anime style, cel shading, inconsistent art style, mixed rendering styles, flat lighting, simple shading, flat shadows, poor focal point, bad anatomy, deformed hands, extra fingers, extra limbs, broken limbs, distorted face, blurry, muddy details, noisy image, text, watermark, logo, frame, border"

  }

}
