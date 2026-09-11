/**
 * Помощник генерации: категории и стили с «агентами» (promptCore).
 * Источник ядер — prompts/blocks/visual_style/*.md (data/library/current).
 * Используется панелью gen-assistant-panel в окне «Генерация».
 */

export type GenStyleArt =
  | "polka"
  | "pixel"
  | "noir"
  | "clay"
  | "knit"
  | "infographic"
  | "photo"
  | "tutor"
  | "retro";

export type GenStyleDef = {
  id: string;
  /** Ключ SVG-превью плитки. */
  art: GenStyleArt;
  name: string;
  /** Путь блока в библиотеке промптов (справочно). */
  file: string;
  desc: string;
  /** Ключ цвета точки/рамки плитки. */
  color: "red" | "purple" | "gray" | "orange" | "cyan" | "blue" | "green" | "pink" | "yellow";
  tags: string[];
  promptCore: string;
};

export type GenCategoryDef = {
  id: string;
  /** Превью-представитель категории. */
  art: GenStyleArt;
  name: string;
  styles: GenStyleDef[];
};

export const GEN_ASSISTANT_CATEGORIES: GenCategoryDef[] = [
  {
    id: "cartoon",
    art: "polka",
    name: "Мульт/аниме",
    styles: [
      {
        id: "trash_polka_noir_short",
        art: "polka",
        name: "Треш-полька нуар",
        file: "visual_style/trash_polka_noir_short.md",
        desc: "Grunge poster, ink splash, blood-red акценты, distressed paper",
        color: "red",
        tags: ["true-crime", "драма", "история", "постер", "взрослые"],
        promptCore:
          "Trash Polka Noir Comic Grunge Poster Illustration: trash polka + dark comic book + grunge poster + high-contrast mixed media. Палитра: black, off-white, dirty cream, charcoal, vivid blood-red accents. Raw brush smears, ink splashes, halftone, distressed paper, gritty inking, poster-like single focal point. Не clean minimalist, не photorealism, не collage panels.",
      },
      {
        id: "micro_pixelart",
        art: "pixel",
        name: "Микро-пиксельарт",
        file: "visual_style/micro_pixelart.md",
        desc: "Cinematic pixel, subpixel shading, мягкий дизеринг",
        color: "purple",
        tags: ["игры", "технологии", "коты", "ночь", "кибер"],
        promptCore:
          "Mature cinematic pixel art: ultra detailed cinematic pixel art, pixel-painted character design, premium pixel-art animation still, near-invisible pixel grid, subpixel shading, soft dithering. Не retro 8-bit, не крупные пиксели, не photorealism, не anime, не 3D render.",
      },
      {
        id: "noir_true_crime_poster",
        art: "noir",
        name: "Нуар true-crime",
        file: "visual_style/noir_true_crime_poster.md",
        desc: "Graphic novel, halftone grain, тяжёлые чёрные тени",
        color: "gray",
        tags: ["true-crime", "детектив", "ночь", "город", "документалка"],
        promptCore:
          "Noir Graphic Novel True-Crime Thriller Poster: noir graphic novel + gritty crime thriller poster + true-crime documentary title-image mood. Heavy black shadows, rough printed texture, halftone grain, scratch marks, dirty cream highlights, blood-red accents, high contrast. Не bright colors, не clean vector, не cute, не photorealism.",
      },
      {
        id: "clay_plasticine_2d",
        art: "clay",
        name: "Пластилин",
        file: "visual_style/clay_plasticine_2d.md",
        desc: "Claymation-миниатюра, отпечатки пальцев, matte texture",
        color: "orange",
        tags: ["дети", "сказка", "уют", "еда", "обучение"],
        promptCore:
          "Claymation Plasticine 2D-Look Miniature Illustration: handcrafted miniature scene, muted earthy colors, soft rounded edges, fingerprints, matte texture, slight imperfections, stop-motion charm, soft shadows, vintage educational mood. Не sharp vector, не photorealism, не glossy 3D, не neon.",
      },
      {
        id: "textile_cut_paper_knitted",
        art: "knit",
        name: "Вязаный / войлок",
        file: "visual_style/textile_cut_paper_knitted.md",
        desc: "Textile, cut-paper, тёплая осенняя палитра, вышивка",
        color: "cyan",
        tags: ["дети", "сказка", "зима", "уют", "животные"],
        promptCore:
          "Textile Cut-Paper Family Illustration: children's book illustration, handmade textile texture, cut-paper / felt / embroidered fabric feel, stylized flat shapes, soft defined edges, layered surfaces, warm autumnal decorative palette, poetic emotional warmth. Не photorealistic humans, не glossy 3D, не anime.",
      },
      {
        id: "gritty_doc_noir_historical",
        art: "noir",
        name: "Док-нуар историч.",
        file: "visual_style/gritty_doc_noir_historical.md",
        desc: "Акварель и тушь, состаренная бумага, холодная луна",
        color: "blue",
        tags: ["история", "мистика", "документалка", "война", "тайны"],
        promptCore:
          "Gritty Documentary Noir Historical Mystery Illustration: gritty documentary noir + dark historical concept art + cinematic mystery atmosphere. Watercolor and ink texture, rough brush strokes, aged paper grain, dramatic shadows, cold moonlight, muted gray-blue palette, faded sepia, small warm lamp highlights. Одна unified atmospheric scene, не collage, не cartoon.",
      },
    ],
  },
  {
    id: "infographic",
    art: "tutor",
    name: "Инфографика",
    styles: [
      {
        id: "infographic_tutor",
        art: "tutor",
        name: "Tutor",
        file: "visual_style/infographic_tutor.md",
        desc: "Обложка урока: крупный округлый заголовок, 3D-тьютор у доски, кремовая палитра",
        color: "yellow",
        tags: ["обучение", "обложка", "3D-тьютор", "заголовок", "урок"],
        promptCore:
          "Tutor Title Card: вертикальная обложка урока. Ultra-detailed 3D animated movie style, cute fluffy animal tutor with a pointer at a dark chalkboard, warm cozy classroom, Pixar-like render, soft studio lighting. Шрифт: жирный округлый display sans (Baloo / Nunito ExtraBold look); заголовок крупно сверху по центру, 2–4 слова из темы кадра, CAPS; подзаголовок тем же шрифтом меньшим кеглем; бейдж-стикер в углу («УРОК 1», «ПРАКТИКА»). Одна иллюстрация — герой кадра: тьютор у доски с меловой схемой по теме. Цвета: кремово-бежевый фон, тёмно-графитовая доска, оранжево-рыжие акценты. Механики: заголовок и подзаголовок из темы кадра, доска меняется под смысл фрагмента, эмоция тьютора — по тону озвучки. Не photorealism, не 2D, не несколько персонажей.",
      },
      {
        id: "infographic_flat_vector",
        art: "infographic",
        name: "Flat vector",
        file: "visual_style/infographic_flat_vector.md",
        desc: "Плоские формы, иконки, стрелки, 2–4 акцентных цвета",
        color: "blue",
        tags: ["бизнес", "обучение", "технологии", "финансы", "шаги"],
        promptCore:
          "Flat Vector Infographic Illustration: clean flat vector shapes, bold simple icons, arrows and flow diagrams, limited bright palette (2–4 accent colors), clear visual hierarchy, generous white space, crisp edges. Не photorealism, не 3D render, не текстуры, не мелкая детализация.",
      },
      {
        id: "infographic_isometric_data",
        art: "infographic",
        name: "Изометрия data",
        file: "visual_style/infographic_isometric_data.md",
        desc: "Изометрические диаграммы, парящие блоки данных, сетка",
        color: "cyan",
        tags: ["данные", "технологии", "финансы", "статистика", "стартапы"],
        promptCore:
          "Isometric Data Illustration: isometric 3D bars, pie charts and floating data blocks, clean geometric grid, soft even shadows, tech palette (blue, cyan, white, one warm accent), subtle depth without perspective distortion. Не фото, не hand-drawn, не реальная перспектива.",
      },
      {
        id: "infographic_chalkboard_sketch",
        art: "infographic",
        name: "Меловая доска",
        file: "visual_style/infographic_chalkboard_sketch.md",
        desc: "Рукотворные маркерные схемы, стрелки, стик-фигуры",
        color: "green",
        tags: ["обучение", "лайфхаки", "план", "идеи", "наука"],
        promptCore:
          "Chalkboard / Whiteboard Sketch Infographic: hand-drawn marker or chalk diagrams, arrows, stick figures, underlined keywords, slightly uneven lines, chalk dust texture on dark board или маркер на белой доске. Не чистый вектор, не идеальная геометрия, не photorealism.",
      },
      {
        id: "infographic_blueprint",
        art: "infographic",
        name: "Blueprint",
        file: "visual_style/infographic_blueprint.md",
        desc: "Белые линии на синьке, размерные линии, сечения",
        color: "blue",
        tags: ["техника", "механизмы", "архитектура", "наука", "изобретения"],
        promptCore:
          "Blueprint Technical Drawing: white line schematics on blueprint blue background, dimension lines, cross-sections, grid paper texture, technical annotations, precise drafting style. Не цветные заливки, не фото, не мультяшные формы.",
      },
    ],
  },
  {
    id: "photo",
    art: "photo",
    name: "Фото/кино",
    styles: [
      {
        id: "photo_cinematic_film_still",
        art: "photo",
        name: "Кинокадр",
        file: "visual_style/photo_cinematic_film_still.md",
        desc: "Анаморфот, малая ГРИП, киношный грейдинг",
        color: "orange",
        tags: ["драма", "кино", "история", "портрет", "ночь"],
        promptCore:
          "Cinematic Film Still Photo: anamorphic lens look, shallow depth of field, filmic color grading, natural skin tones, soft halation on highlights, киношный кадр как стоп-кадр из фильма. Не мульт, не 3D render, не flat vector, не постерная графика.",
      },
      {
        id: "photo_documentary",
        art: "photo",
        name: "Документальное",
        file: "visual_style/photo_documentary.md",
        desc: "Репортаж, естественный свет, зерно 35mm",
        color: "gray",
        tags: ["реальность", "люди", "город", "репортаж", "соцтемы"],
        promptCore:
          "Documentary Photo: candid unposed moment, natural available light, 35mm film grain, honest muted colors, reportage composition, лёгкая несовершенность кадра как у реальной съёмки. Не постановочный глянец, не иллюстрация, не студийный свет.",
      },
      {
        id: "photo_macro_product",
        art: "photo",
        name: "Макро-предметка",
        file: "visual_style/photo_macro_product.md",
        desc: "Предмет крупно, студийный свет, премиальный глянец",
        color: "yellow",
        tags: ["предметы", "еда", "техника", "детали", "реклама"],
        promptCore:
          "Macro Product Photography: extreme close-up, crisp micro-details, shallow focus, clean seamless background, controlled studio light, premium advertising look. Не иллюстрация, не сцена с персонажами, не захламлённый фон.",
      },
      {
        id: "photo_night_street",
        art: "photo",
        name: "Ночная улица",
        file: "visual_style/photo_night_street.md",
        desc: "Зерно высокого ISO, неон, мокрый асфальт, смаз",
        color: "pink",
        tags: ["город", "ночь", "неон", "молодёжь", "музыка"],
        promptCore:
          "Night Street Photography: high ISO grain, neon and shop-window reflections, wet asphalt, motion blur, candid urban night scenes, contrasty available light. Не студийный свет, не иллюстрация, не дневной кадр.",
      },
    ],
  },
  {
    id: "retro",
    art: "retro",
    name: "Ретро/архив",
    styles: [
      {
        id: "retro_archive_8mm",
        art: "retro",
        name: "Хроника 8мм",
        file: "visual_style/retro_archive_8mm.md",
        desc: "Зерно, царапины, выцветшие цвета, мерцание кадра",
        color: "yellow",
        tags: ["история", "хроника", "война", "семья", "XX век"],
        promptCore:
          "Archival 8mm Film Chronicle: grainy faded footage look, scratches and dust, slight frame flicker, desaturated shifted colors, rounded frame corners, историческая хроника середины XX века. Не чистое цифровое фото, не современная цветокоррекция, не иллюстрация.",
      },
      {
        id: "retro_polaroid",
        art: "retro",
        name: "Полароид",
        file: "visual_style/retro_polaroid.md",
        desc: "Вспышка в лоб, вымытые цвета, снимок 80–90х",
        color: "orange",
        tags: ["90е", "семья", "ностальгия", "вечеринка", "личное"],
        promptCore:
          "Polaroid Snapshot: direct on-camera flash, washed-out colors, slight overexposure on faces, casual 80s–90s snapshot aesthetic, soft focus, instant-film palette. Не студийное фото, не иллюстрация, не современный глянец.",
      },
      {
        id: "retro_newspaper_print",
        art: "retro",
        name: "Газетная печать",
        file: "visual_style/retro_newspaper_print.md",
        desc: "Растр, пожелтевшая бумага, старые чернила",
        color: "gray",
        tags: ["история", "скандал", "пресса", "XX век", "криминал"],
        promptCore:
          "Vintage Newspaper Print: halftone photo dots, yellowed aged paper, column layout feel, aged black ink, slight print misregistration, старая газетная полоса или вырезка. Не чистое цифровое фото, не глянцевая печать, не иллюстрация.",
      },
      {
        id: "retro_investigation_board",
        art: "retro",
        name: "Доска расследования",
        file: "visual_style/retro_investigation_board.md",
        desc: "Пробковая доска, фото, красные нити, заметки",
        color: "red",
        tags: ["true-crime", "детектив", "тайны", "расследование", "улики"],
        promptCore:
          "Detective Investigation Board: cork board with pinned photos, red string connections, handwritten notes, newspaper clippings, evidence markers, warm desk lamp light, true-crime research wall. Не цифровой интерфейс, не чистая иллюстрация, не пустой фон.",
      },
    ],
  },
];

export const GEN_ASSISTANT_ALL_STYLES: GenStyleDef[] =
  GEN_ASSISTANT_CATEGORIES.flatMap((c) => c.styles);

/** Цвета плиток в палитре окна генерации (dark + cyan accent). */
export const GEN_STYLE_COLORS: Record<GenStyleDef["color"], string> = {
  red: "#f87171",
  purple: "#c084fc",
  gray: "#9ca3af",
  orange: "#fb923c",
  cyan: "#22d3ee",
  blue: "#60a5fa",
  green: "#4ade80",
  pink: "#f472b6",
  yellow: "#facc15",
};

/** Сборка промпта: запрос + агент стиля + формат кадра. */
export function assembleGenPrompt(opts: {
  request: string;
  agentText: string;
  aspect: string;
}): string {
  const parts: string[] = [];
  const req = opts.request.trim();
  if (req) parts.push(`Запрос: ${req}`);
  if (opts.agentText.trim()) parts.push(opts.agentText.trim());
  parts.push(
    opts.aspect === "9:16"
      ? "Aspect ratio: 9:16. Vertical, rule of thirds, читаемый силуэт для shorts."
      : `Aspect ratio: ${opts.aspect}.`,
  );
  return parts.join("\n\n");
}

/** Текст варианта k из N: тот же промпт + указание варьировать ракурс. */
export function genPromptVariant(base: string, idx: number, total: number): string {
  if (total <= 1) return base;
  return `${base}\n\n-- Вариант ${idx + 1} из ${total}: то же содержание и стиль, другой ракурс и мелкие детали кадра.`;
}
