/**
 * Каталог outsee Create — дословная копия из JS:
 * - registry `o` + chip options `HH`/`d` — chunk 8152
 * - video Nn tables — chunk 517 module 90228
 * - picker meta ej/ek — create/image pages
 *
 * Не выдумывать опции: только то, что отдаёт HH(model, chip).
 */

export type OutseeMediaType = "image" | "video" | "audio";

export type OutseeFeedKind = "all" | "image" | "video" | "audio";

export type OutseeChip =
  | "aspect"
  | "resolution"
  | "detail"
  | "duration"
  | "audio"
  | "orientation"
  | "quality"
  | "instrumental"
  | "image-input";

export type OutseeAudioModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  /** Временно подключено через Grsai API — в меню с «+» (сейчас пусто) */
  grsaiWired?: boolean;
  chips: OutseeChip[];
  defaults: { instrumental?: boolean; voice?: string; speed?: number };
};

export type OutseeImageModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  /** registry.hidden — не в S0 picker */
  hidden?: boolean;
  advanced?: boolean;
  /** Временно подключено через Grsai API — в меню с «+» */
  grsaiWired?: boolean;
  chips: OutseeChip[];
  defaults: {
    aspectRatio?: string;
    imageResolution?: string;
    detailLevel?: string;
  };
};

/** Image: Grsai dashboard/models — временно wired, в picker с «+». */
export const GRSAI_WIRED_SLUGS = new Set([
  "gpt-image-2",
  "gpt-image-2-vip",
  "nano-banana-2",
  "nano-banana-2-lite",
  "nano-banana-pro",
  "nano-banana-fast",
  "nano-banana",
  "nano-banana-pro-vt",
  "nano-banana-pro-cl",
  "nano-banana-2-cl",
  "nano-banana-2-2k-cl",
  "nano-banana-2-4k-cl",
  "nano-banana-pro-vip",
  "nano-banana-pro-4k-vip",
]);

/** Video: Grsai Sora2 / Veo docs — в picker с «+». */
export const GRSAI_WIRED_VIDEO_SLUGS = new Set([
  "sora-2",
  "sora2-portrait",
  "sora2-landscape",
  "veo3.1-fast",
  "veo3.1-pro",
  "veo-3-1-lite",
  "veo-3-fast",
]);

/**
 * Audio: на Grsai моделей нет — Set пустой.
 * Все модели Create (Suno / ElevenLabs) доступны через пайплайн.
 */
export const GRSAI_WIRED_AUDIO_SLUGS = new Set<string>([]);

export type OutseeVideoModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  hidden?: boolean;
  advanced?: boolean;
  /** Временно подключено через Grsai API — в меню с «+» */
  grsaiWired?: boolean;
  chips: OutseeChip[];
  defaults: {
    aspectRatio?: string;
    resolution?: string;
    duration?: number;
    generateAudio?: boolean;
    motionQuality?: string;
    /** sora size small|large */
    soraSize?: string;
  };
  /** Nn table (если пусто — HH вернёт [] / override) */
  nn: {
    resolutions: string[];
    durations: number[];
    aspectRatios: string[];
  };
};

const OUTSEE_ORIGIN = "https://outsee.io";

/** gpt-image-2 aspects = n.P из module 20674 */
const GPT_IMAGE_2_ASPECTS = [
  "1:1",
  "16:9",
  "9:16",
  "4:3",
  "3:4",
  "3:2",
  "2:3",
  "21:9",
] as const;

/** nano-banana* — точный порядок из HH/d */
const NANO_BANANA_ASPECTS = [
  "16:9",
  "9:16",
  "1:1",
  "4:3",
  "5:4",
  "3:4",
  "4:5",
  "21:9",
] as const;

/** seedream / прочие image — из HH/d */
const SEEDREAM_ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] as const;

export const OUTSEE_DETAIL_LEVELS = [
  { id: "low", label: "Низкое", hint: "дешевле" },
  { id: "medium", label: "Среднее", hint: "баланс" },
  { id: "high", label: "Высокое", hint: "детальнее" },
] as const;

export const OUTSEE_CHIP_LABELS: Record<string, string> = {
  aspect: "Соотношение сторон",
  resolution: "Разрешение",
  detail: "Детализация",
  duration: "Длительность",
  audio: "Звук",
  orientation: "Ориентация",
  quality: "Качество",
  instrumental: "Вокал",
};

/** Вертикальный typetoggle create (ep=). */
export const OUTSEE_TYPE_TABS: { id: OutseeMediaType; label: string }[] = [
  { id: "image", label: "Фото" },
  { id: "video", label: "Видео" },
  { id: "audio", label: "Аудио" },
];

/** Фильтр истории create (aJ=). */
export const OUTSEE_FEED_TABS: { id: OutseeFeedKind; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "image", label: "Фото" },
  { id: "video", label: "Видео" },
  { id: "audio", label: "Аудио" },
];

export const OUTSEE_ACCENT = "#D1FE17";

/**
 * Порядок picker = Object.values(o).filter(type && !hidden) из 8152.
 * Meta (price/TOP/NEW/icon/description) — из ej create page.
 */
export const OUTSEE_IMAGE_MODELS: OutseeImageModel[] = [
  {
    slug: "nano-banana-pro",
    studioId: "nano_banana_pro",
    displayName: "Nano Banana Pro",
    description: "Grsai · лучшая banana для любых задач.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "3",
    isTop: true,
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "nano-banana-2",
    studioId: "nano_banana_2",
    displayName: "Nano Banana 2",
    description: "Grsai · самая новая версия Nano Banana.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "3",
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "nano-banana-2-lite",
    studioId: "nano_banana_2_lite",
    displayName: "Nano Banana 2 Lite",
    description: "Grsai · быстрее и дешевле Nano Banana 2.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "1.5",
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "1K" },
  },
  {
    slug: "nano-banana-fast",
    studioId: "nano_banana_fast",
    displayName: "Nano Banana Fast",
    description: "Grsai · быстрая генерация.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "1",
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "1K" },
  },
  {
    slug: "seedream-4.5",
    studioId: "seedream_4_5",
    displayName: "SeeDream 4.5",
    description: "Продвинутая модель от TikTok. Подходит для всего. 4К.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/2.jpg`,
    price: "1.8",
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "seedream-5-pro",
    studioId: "seedream_5_pro",
    displayName: "SeeDream 5 Pro",
    description: "Флагман Seedream. Высочайшая точность и контроль, до 10 референсов.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 3",
    isNew: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "seedream-5-lite",
    studioId: "seedream_5_0_lite",
    displayName: "SeeDream 5 Lite",
    description: "Новейшая модель Seedream. Быстрая генерация в высоком качестве.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/2.jpg`,
    price: "2",
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "gpt-image-2",
    studioId: "gpt_image_2",
    displayName: "GPT Image 2",
    description: "Grsai · постеры и реклама с текстом.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.03",
    isNew: true,
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "1K" },
  },
  {
    slug: "gpt-image-2-vip",
    studioId: "gpt_image_2_vip",
    displayName: "GPT Image 2 VIP",
    description: "Grsai · VIP до 4K (пиксели).",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.06",
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "topaz-image-upscale",
    studioId: null,
    displayName: "Topaz Image Upscale",
    description:
      "Официальный Topaz Image API · 3 режима (Standard 2 / Wonder 2 / Bloom Realism)",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/topaz.webp`,
    price: "от 5",
    advanced: true,
    chips: [],
    defaults: {},
  },
  {
    slug: "nano-banana",
    studioId: "nano_banana",
    displayName: "Nano Banana",
    description: "Grsai · быстрая и точная.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "1.2",
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "1K" },
  },
  {
    slug: "nano-banana-pro-vt",
    studioId: null,
    displayName: "Nano Banana Pro VT",
    description: "Grsai · Pro VT (временное подключение).",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "3",
    grsaiWired: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "gpt-image-1.5",
    studioId: "gpt_image_1_5",
    displayName: "GPT Image 1.5",
    description: "Outsee · флагман OpenAI (без Grsai +).",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "3",
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
];

/** Модель временно подключена через Grsai — в меню с «+». */
export function isGrsaiWiredSlug(slug: string, media: OutseeMediaType = "image"): boolean {
  if (media === "video") return GRSAI_WIRED_VIDEO_SLUGS.has(slug);
  if (media === "audio") return GRSAI_WIRED_AUDIO_SLUGS.has(slug);
  return GRSAI_WIRED_SLUGS.has(slug);
}

/** Create slug → Grsai API model id */
export function toGrsaiVideoModel(slug: string): string {
  const map: Record<string, string> = {
    "veo-3-1-lite": "veo3.1-fast",
    "veo-3-fast": "veo3.1-fast",
    "veo3.1-fast": "veo3.1-fast",
    "veo3.1-pro": "veo3.1-pro",
    "sora-2": "sora-2",
    "sora2-portrait": "sora2-portrait",
    "sora2-landscape": "sora2-landscape",
  };
  return map[slug] || "sora-2";
}

/**
 * Порядок picker video = Object.values(o) type=video !hidden.
 * Nn options — module 90228; UI aspect override для veo/omni — из HH/d.
 */
export const OUTSEE_VIDEO_MODELS: OutseeVideoModel[] = [
  {
    slug: "sora-2",
    studioId: null,
    displayName: "Sora 2",
    description: "Grsai · OpenAI Sora 2, звук + физика. 10/15с.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.08",
    isTop: true,
    grsaiWired: true,
    chips: ["aspect", "duration"],
    defaults: { aspectRatio: "9:16", duration: 10, soraSize: "small" },
    nn: {
      resolutions: [],
      durations: [10, 15],
      aspectRatios: ["9:16", "16:9"],
    },
  },
  {
    slug: "sora2-portrait",
    studioId: null,
    displayName: "Sora 2 Portrait",
    description: "Grsai · Sora 2 вертикаль 9:16.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.08",
    grsaiWired: true,
    chips: ["duration"],
    defaults: { aspectRatio: "9:16", duration: 10, soraSize: "small" },
    nn: { resolutions: [], durations: [10, 15], aspectRatios: ["9:16"] },
  },
  {
    slug: "sora2-landscape",
    studioId: null,
    displayName: "Sora 2 Landscape",
    description: "Grsai · Sora 2 горизонталь 16:9.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.08",
    grsaiWired: true,
    chips: ["duration"],
    defaults: { aspectRatio: "16:9", duration: 10, soraSize: "small" },
    nn: { resolutions: [], durations: [10, 15], aspectRatios: ["16:9"] },
  },
  {
    slug: "veo3.1-fast",
    studioId: null,
    displayName: "Veo 3.1 Fast",
    description: "Grsai · Google Veo 3.1 Fast.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 0.4",
    grsaiWired: true,
    chips: ["aspect"],
    defaults: { aspectRatio: "16:9", duration: 8 },
    nn: { resolutions: [], durations: [8], aspectRatios: ["16:9", "9:16"] },
  },
  {
    slug: "veo3.1-pro",
    studioId: null,
    displayName: "Veo 3.1 Pro",
    description: "Grsai · Google Veo 3.1 Pro.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 0.4",
    grsaiWired: true,
    chips: ["aspect"],
    defaults: { aspectRatio: "16:9", duration: 8 },
    nn: { resolutions: [], durations: [8], aspectRatios: ["16:9", "9:16"] },
  },
  {
    slug: "seedance-1-5-pro",
    studioId: "seedance_pro_1_5",
    displayName: "Seedance 1.5 Pro",
    description: "Отличный выбор цена — качество, идеален для базовых задач.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 3.5",
    chips: ["aspect", "resolution", "duration", "audio", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 4, generateAudio: false },
    nn: {
      resolutions: ["480p", "720p"],
      durations: [4, 8, 12],
      aspectRatios: ["1:1", "21:9", "4:3", "3:4", "16:9", "9:16"],
    },
  },
  {
    slug: "grok-imagine-video-1.5",
    studioId: null,
    displayName: "Grok Imagine 1.5",
    description: "Новейшая модель от xAI, лучшая на рынке с русской речью.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/grok.webp`,
    price: "от 3.3",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "480p", duration: 8 },
    nn: {
      resolutions: ["480p", "720p"],
      durations: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1", "3:2", "2:3"],
    },
  },
  {
    slug: "seedance-2-0-global",
    studioId: "seedance_2",
    displayName: "Seedance 2",
    description: "Лучшая видео-модель на рынке.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 20",
    isTop: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 5 },
    nn: {
      resolutions: ["720p", "1080p", "4k"],
      durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "4:3", "3:4", "1:1", "21:9"],
    },
  },
  {
    slug: "seedance-2-0-mini",
    studioId: null,
    displayName: "Seedance 2 Mini",
    description: "Новая, лёгкая версия Seedance 2.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 8",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 5 },
    nn: {
      resolutions: ["480p", "720p"],
      durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "4:3", "3:4", "1:1", "21:9"],
    },
  },
  {
    slug: "veo-3-1-lite",
    studioId: "veo_3_1_lite",
    displayName: "Veo 3.1 Lite",
    description: "Grsai · alias veo3.1-fast. Хорошая генерация русской речи.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 0.4",
    grsaiWired: true,
    chips: ["aspect", "duration"],
    defaults: { aspectRatio: "16:9", duration: 8 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [8],
      aspectRatios: ["portrait", "landscape"],
    },
  },
  {
    slug: "omni-flash",
    studioId: null,
    displayName: "Omni Flash",
    description: "Новейшая модель Google. Аудио-нативная, до 5 голосов, редактирование видео.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 14",
    isNew: true,
    chips: ["aspect", "resolution", "duration"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 4 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [4, 6, 8, 10],
      aspectRatios: ["landscape", "portrait"],
    },
  },
  {
    slug: "kling-3-0",
    studioId: "kling_3",
    displayName: "Kling 3.0",
    description: "Новейшая модель Kling. Гибкая длительность, нативное аудио, мультишот.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 16",
    chips: ["aspect", "resolution", "duration", "audio", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "1080p", duration: 5, generateAudio: false },
    nn: {
      resolutions: ["720p", "1080p", "4k"],
      durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1"],
    },
  },
  {
    slug: "kling-3-0-turbo",
    studioId: null,
    displayName: "Kling 3.0 Turbo",
    description: "Быстрая версия Kling 3.0.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 16",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 5 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1"],
    },
  },
  {
    slug: "kling-2-6",
    studioId: "kling_2_6",
    displayName: "Kling 2.6",
    description: "Подходит для всего. Лучшее соотношение цена/качество среди Kling моделей.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 9",
    chips: ["aspect", "resolution", "duration", "audio", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "1080p", duration: 5, generateAudio: false },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [5, 10],
      aspectRatios: ["16:9", "9:16", "1:1"],
    },
  },
  {
    slug: "kling-2-5-turbo",
    studioId: "kling_2_5_turbo",
    displayName: "Kling 2.5 Turbo",
    description: "Хороший выбор для генерации по первому — последнему кадру.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 8",
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "1080p", duration: 5 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [5, 10],
      aspectRatios: [], // image-to-video only — aspect chip есть в registry, options пустые
    },
  },
  {
    slug: "happyhorse-1-0",
    studioId: null,
    displayName: "HappyHorse 1.0",
    description:
      "Новейшая модель от Alibaba. Реалистичное движение, мульти-референс, редактирование видео.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/happyhorse.webp`,
    price: "от 15",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720P", duration: 5 },
    nn: {
      resolutions: ["720P", "1080P"],
      durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1", "4:3", "3:4"],
    },
  },
  {
    slug: "kling-lip-sync",
    studioId: "kling_lip_sync",
    displayName: "Kling Lip Sync",
    description: "Синхронизация губ под аудио.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "3/с",
    advanced: true,
    chips: ["resolution"],
    defaults: { aspectRatio: "16:9", resolution: "720p" },
    nn: { resolutions: ["720p", "1080p"], durations: [1], aspectRatios: [] },
  },
  {
    slug: "kling-3-0-motion-control",
    studioId: "kling_motion_3_0",
    displayName: "Motion Control 3.0",
    description: "Улучшенный контроль движения. Лучшая консистентность лица.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "3/с",
    advanced: true,
    chips: ["orientation", "quality"],
    defaults: { aspectRatio: "16:9", motionQuality: "std" },
    nn: { resolutions: ["std", "pro"], durations: [1], aspectRatios: [] },
  },
  {
    slug: "kling-motion-control",
    studioId: "kling_motion_2_6",
    displayName: "Motion Control 2.6",
    description: "Kling 2.6 · контроль движения и эмоций по вашему референсу.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "2/с",
    advanced: true,
    chips: ["orientation", "quality"],
    defaults: { aspectRatio: "16:9", motionQuality: "std" },
    nn: { resolutions: ["std", "pro"], durations: [1], aspectRatios: [] },
  },
  {
    slug: "topaz-video-upscale",
    studioId: null,
    displayName: "Topaz Video Upscale",
    description: "AI-апскейл видео до 4K · Starlight · Proteus · Astra · от 5 ток",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/topaz.webp`,
    price: "от 5",
    advanced: true,
    chips: [],
    defaults: {},
    nn: { resolutions: ["1080p", "4k"], durations: [1], aspectRatios: [] },
  },
  // hidden alias veo-3-fast → same as lite UI
  {
    slug: "veo-3-fast",
    studioId: "veo_3_fast",
    displayName: "Veo 3 Fast",
    description: "Grsai · alias veo3.1-fast.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 0.4",
    hidden: true,
    grsaiWired: true,
    chips: ["aspect", "duration"],
    defaults: { aspectRatio: "16:9", duration: 8 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [8],
      aspectRatios: ["portrait", "landscape"],
    },
  },
];

/**
 * Аудио — все модели Create (Suno / ElevenLabs).
 * На Grsai audio API нет → без «+»; «Генерировать» запускает пайплайн audio.
 */
export const OUTSEE_AUDIO_MODELS: OutseeAudioModel[] = [
  {
    slug: "suno-5-5",
    studioId: null,
    displayName: "Suno 5.5",
    description: "Улучшенное качество и персонализация.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/suno.webp`,
    price: "2.5",
    chips: ["instrumental"],
    defaults: { instrumental: false },
  },
  {
    slug: "elevenlabs-v3",
    studioId: null,
    displayName: "ElevenLabs",
    description: "Реалистичная озвучка текста. Сотни голосов, десятки языков.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/elevenlabs.webp`,
    price: "от 0.1",
    isNew: true,
    chips: [],
    defaults: { voice: "Rachel", speed: 1 },
  },
];

/**
 * Порядок picker image как ej на /create (все модели, включая «hidden»).
 */
const IMAGE_PICKER_ORDER = [
  "gpt-image-2",
  "gpt-image-2-vip",
  "nano-banana-2",
  "nano-banana-2-lite",
  "nano-banana-pro",
  "nano-banana-fast",
  "nano-banana",
  "nano-banana-pro-vt",
  "seedream-4.5",
  "seedream-5-pro",
  "seedream-5-lite",
  "gpt-image-1.5",
  "topaz-image-upscale",
] as const;

export function pickerImageModels(): OutseeImageModel[] {
  const by = new Map(OUTSEE_IMAGE_MODELS.map((m) => [m.slug, m]));
  return IMAGE_PICKER_ORDER.map((s) => by.get(s)).filter(Boolean) as OutseeImageModel[];
}

export function pickerVideoModels(): OutseeVideoModel[] {
  return OUTSEE_VIDEO_MODELS.filter((m) => !m.hidden);
}

export function pickerVideoModelsAll(): OutseeVideoModel[] {
  return OUTSEE_VIDEO_MODELS.filter((m) => !m.hidden);
}

export function pickerAudioModels(): OutseeAudioModel[] {
  return [...OUTSEE_AUDIO_MODELS];
}

export function pickerModelsForType(type: OutseeMediaType) {
  if (type === "image") return pickerImageModels();
  if (type === "video") return pickerVideoModelsAll();
  return pickerAudioModels();
}

/**
 * Копия HH/d(model, chip) из chunk 8152.
 */
export function chipOptions(slug: string, chip: OutseeChip): string[] {
  const image = OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug);
  const video = OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug);

  if (chip === "quality") {
    if (
      slug === "kling-motion-control" ||
      slug === "kling-3-0-motion-control" ||
      slug === "kling-lip-sync"
    ) {
      return video?.nn.resolutions?.length ? video.nn.resolutions : ["std", "pro"];
    }
    return ["std", "pro"];
  }
  if (chip === "orientation") return ["video", "image"];
  if (chip === "detail") return slug === "gpt-image-2" ? ["low", "medium", "high"] : [];

  if (video) {
    if (chip === "aspect") {
      // HH override: veo / omni → 16:9 / 9:16 (не portrait/landscape)
      if (slug === "veo-3-fast" || slug === "veo-3-1-lite" || slug === "omni-flash") {
        return ["16:9", "9:16"];
      }
      return [...video.nn.aspectRatios];
    }
    if (chip === "resolution") return [...video.nn.resolutions];
    if (chip === "duration") return video.nn.durations.map(String);
    return [];
  }

  if (image) {
    if (chip === "aspect") {
      if (slug === "gpt-image-1.5") return ["1:1", "3:2", "2:3"];
      if (slug === "gpt-image-2" || slug === "gpt-image-2-vip") return [...GPT_IMAGE_2_ASPECTS];
      if (slug.startsWith("nano-banana")) return [...NANO_BANANA_ASPECTS];
      return [...SEEDREAM_ASPECTS];
    }
    if (chip === "resolution") {
      if (slug === "gpt-image-2") return ["1K"];
      if (slug === "gpt-image-2-vip") return ["1K", "2K", "4K"];
      if (
        slug === "nano-banana-2" ||
        slug === "nano-banana-pro" ||
        slug === "nano-banana-pro-vt"
      ) {
        return ["1K", "2K", "4K"];
      }
      if (slug === "nano-banana-2-lite" || slug === "nano-banana-fast" || slug === "nano-banana") {
        return ["1K", "2K"];
      }
      if (slug === "seedream-4.5") return ["2K", "4K"];
      if (slug === "seedream-5-pro") return ["1K", "2K"];
      if (slug === "seedream-5-lite") return ["2K", "3K"];
      if (slug === "gpt-image-1.5") return ["2K"];
      return ["1K", "2K"];
    }
  }
  return [];
}

/** Chip order на create: aspect → resolution → detail → duration → audio */
export const DOCK_CHIP_ORDER: OutseeChip[] = [
  "aspect",
  "resolution",
  "detail",
  "duration",
  "audio",
];

export function dockChipsForModel(slug: string, mediaType: OutseeMediaType): OutseeChip[] {
  if (mediaType === "audio") {
    const model = OUTSEE_AUDIO_MODELS.find((m) => m.slug === slug);
    if (!model) return [];
    return (["instrumental"] as OutseeChip[]).filter((c) => model.chips.includes(c));
  }
  const model =
    mediaType === "image"
      ? OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug)
      : OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug);
  if (!model) return [];
  return DOCK_CHIP_ORDER.filter((c) => model.chips.includes(c));
}

export function getImageModel(slug: string): OutseeImageModel {
  return (
    OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug) ??
    OUTSEE_IMAGE_MODELS.find((m) => m.slug === "gpt-image-2")!
  );
}

export function getVideoModel(slug: string): OutseeVideoModel {
  return (
    OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug) ??
    OUTSEE_VIDEO_MODELS.find((m) => m.slug === "kling-3-0")!
  );
}

export function getAudioModel(slug: string): OutseeAudioModel {
  return OUTSEE_AUDIO_MODELS.find((m) => m.slug === slug) ?? OUTSEE_AUDIO_MODELS[0]!;
}

export function studioIdToSlug(studioId: string | null | undefined, kind: OutseeMediaType): string {
  if (!studioId) {
    if (kind === "image") return "gpt-image-2";
    if (kind === "audio") return "suno-5-5";
    return "kling-3-0";
  }
  if (kind === "audio") return studioId.replace(/_/g, "-");
  const list = kind === "image" ? OUTSEE_IMAGE_MODELS : OUTSEE_VIDEO_MODELS;
  const hit = list.find((m) => m.studioId === studioId);
  if (hit) return hit.slug;
  if (studioId === "veo_3_1_fast") return "veo-3-1-lite";
  return studioId.replace(/_/g, "-");
}

export function slugToStudioId(slug: string, kind: OutseeMediaType): string | null {
  if (kind === "audio") return null;
  const list = kind === "image" ? OUTSEE_IMAGE_MODELS : OUTSEE_VIDEO_MODELS;
  return list.find((m) => m.slug === slug)?.studioId ?? null;
}

export function aspectToStudioId(label: string): string {
  if (label === "portrait") return "9_16";
  if (label === "landscape") return "16_9";
  return label.replace(":", "_");
}

export function studioAspectToLabel(id: string | null | undefined): string {
  if (!id) return "16:9";
  return id.replace("_", ":");
}

export function resToStudioId(label: string): string {
  return label.toLowerCase();
}

export function studioResToLabel(id: string | null | undefined, slug?: string): string {
  if (!id) return "2K";
  if (slug === "happyhorse-1-0") {
    const u = id.toUpperCase();
    return u.endsWith("P") ? u : `${u}P`;
  }
  if (id === "std" || id === "pro" || id === "4k") return id;
  if (/^\d+p$/i.test(id)) return id.toLowerCase();
  return id.toUpperCase();
}

export function outseeCreateUrl(type: OutseeMediaType, slug: string): string {
  const t = type === "audio" ? "audio" : type;
  return `${OUTSEE_ORIGIN}/create?type=${t}&model=${encodeURIComponent(slug)}`;
}

export function outseeImageUrl(slug: string): string {
  return `${OUTSEE_ORIGIN}/image?model=${encodeURIComponent(slug)}`;
}

export function clampToOptions(value: string, options: string[], fallback?: string): string {
  if (!options.length) return value;
  if (options.includes(value)) return value;
  if (fallback && options.includes(fallback)) return fallback;
  return options[0]!;
}

export function detailLabel(id: string): string {
  return OUTSEE_DETAIL_LEVELS.find((d) => d.id === id)?.label ?? id;
}

export function supportsRelax(slug: string, mediaType: OutseeMediaType): boolean {
  // на create: безлимит-чип если у юзера есть; в Studio — как в пайплайне
  if (mediaType === "image") return true;
  return slug === "veo-3-1-lite" || slug === "veo-3-fast" || slug.includes("veo");
}
