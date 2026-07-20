/**
 * Каталог моделей/опций — копия из outsee.io create/image JS
 * (__next_static_chunks_app_image_page / create_page / 8152).
 * Порядок ключей = порядок в выпадающем списке.
 */

export type OutseeMediaType = "image" | "video";

export type OutseeImageModel = {
  slug: string;
  /** id проекта в Studio (snake_case) */
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  /** hidden на outsee — в picker не показываем, но маппинг оставляем */
  hidden?: boolean;
  resolutions: string[];
  aspects: string[];
  hasDetail?: boolean;
  chips: string[];
};

export type OutseeVideoModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  resolutions: string[];
};

const OUTSEE_ORIGIN = "https://outsee.io";

/** Aspects for nano-banana* / seedream / gpt-image-2 (image page). */
export const OUTSEE_ASPECTS_FULL = [
  "1:1",
  "16:9",
  "9:16",
  "4:3",
  "5:4",
  "3:4",
  "4:5",
  "2:3",
  "3:2",
  "21:9",
] as const;

export const OUTSEE_ASPECTS_BASIC = [
  "1:1",
  "16:9",
  "9:16",
  "4:3",
  "3:4",
  "2:3",
  "3:2",
  "21:9",
] as const;

export const OUTSEE_DETAIL_LEVELS = [
  { id: "low", label: "Низкое", hint: "дешевле" },
  { id: "medium", label: "Среднее", hint: "баланс" },
  { id: "high", label: "Высокое", hint: "детальнее" },
] as const;

/** Порядок как в es={} на /image (и ej на /create). */
export const OUTSEE_IMAGE_MODELS: OutseeImageModel[] = [
  {
    slug: "nano-banana-2",
    studioId: "nano_banana_2",
    displayName: "Nano Banana 2",
    description: "Самая новая версия Nano banana.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "3",
    resolutions: ["2K", "4K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    chips: ["aspect", "resolution", "image-input"],
  },
  {
    slug: "nano-banana-pro",
    studioId: "nano_banana_pro",
    displayName: "Nano Banana Pro",
    description: "Лучшая модель на рынке. Идеальна для любых задач. Лучше просто нет.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "3",
    isTop: true,
    resolutions: ["2K", "4K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    chips: ["aspect", "resolution", "image-input"],
  },
  {
    slug: "seedream-4.5",
    studioId: "seedream_4_5",
    displayName: "Seedream 4.5",
    description: "Продвинутая модель от TikTok. Подходит для всего. 4К.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/2.jpg`,
    price: "1.8",
    resolutions: ["2K", "4K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    chips: ["aspect", "resolution", "image-input"],
  },
  {
    slug: "seedream-5-pro",
    studioId: "seedream_5_pro",
    displayName: "Seedream 5 Pro",
    description: "Флагман Seedream. Высочайшая точность и контроль, до 10 референсов.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 3",
    isNew: true,
    resolutions: ["1K", "2K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    chips: ["aspect", "resolution", "image-input"],
  },
  {
    slug: "seedream-5-lite",
    studioId: "seedream_5_0_lite",
    displayName: "Seedream 5 Lite",
    description: "Новейшая модель Seedream. Быстрая генерация в высоком качестве.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/2.jpg`,
    price: "2",
    resolutions: ["2K", "3K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    chips: ["aspect", "resolution", "image-input"],
  },
  {
    slug: "nano-banana",
    studioId: "nano_banana",
    displayName: "Nano Banana",
    description: "Быстрая и точная. Хороша для точечного редактирования ваших фото.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "1.2",
    resolutions: ["2K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    chips: ["aspect", "image-input"],
  },
  {
    slug: "gpt-image-1.5",
    studioId: "gpt_image_1_5",
    displayName: "GPT Image 1.5",
    description: "Флагманская модель OpenAI. Универсальна и надёжна.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "3",
    resolutions: ["2K"],
    aspects: ["1:1", "3:2", "2:3"],
    chips: ["aspect", "resolution", "image-input"],
  },
  {
    slug: "gpt-image-2",
    studioId: "gpt_image_2",
    displayName: "GPT Image 2",
    description: "Новейшая модель OpenAI. Идеальна для постеров и рекламы с текстом. До 4К.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 3",
    isNew: true,
    resolutions: ["1K", "2K", "4K"],
    aspects: [...OUTSEE_ASPECTS_FULL],
    hasDetail: true,
    chips: ["aspect", "resolution", "detail", "image-input"],
  },
  {
    slug: "topaz-image-upscale",
    studioId: null,
    displayName: "Topaz Upscale",
    description:
      "Официальный Topaz Image API. Точный апскейл, восстановление деталей или креативное улучшение.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/topaz.webp`,
    price: "от 5",
    resolutions: [],
    aspects: [],
    chips: [],
  },
];

/** Порядок как в ek={} на /create. */
export const OUTSEE_VIDEO_MODELS: OutseeVideoModel[] = [
  {
    slug: "seedance-2-0-global",
    studioId: "seedance_2",
    displayName: "Seedance 2",
    description: "Лучшая видео-модель на рынке.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 20",
    isTop: true,
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "seedance-2-0-mini",
    studioId: null,
    displayName: "Seedance 2 Mini",
    description: "Новая, лёгкая версия Seedance 2.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 8",
    isNew: true,
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "kling-3-0",
    studioId: "kling_3",
    displayName: "Kling 3.0",
    description: "Новейшая модель Kling. Гибкая длительность, нативное аудио, мультишот.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 16",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "kling-3-0-turbo",
    studioId: null,
    displayName: "Kling 3.0 Turbo",
    description: "Быстрая версия Kling 3.0.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 16",
    isNew: true,
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "kling-2-6",
    studioId: "kling_2_6",
    displayName: "Kling 2.6",
    description: "Подходит для всего. Лучшее соотношение цена/качество среди Kling моделей.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 9",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "kling-2-5-turbo",
    studioId: "kling_2_5_turbo",
    displayName: "Kling 2.5 Turbo",
    description: "Хороший выбор для генерации по первому — последнему кадру.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 8",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "kling-lip-sync",
    studioId: "kling_lip_sync",
    displayName: "Kling Lip Sync",
    description: "Синхронизация губ под аудио.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "3/с",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "kling-motion-control",
    studioId: "kling_motion_2_6",
    displayName: "Motion Control 2.6",
    description: "Kling 2.6 · контроль движения и эмоций по вашему референсу.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "2/с",
    resolutions: ["std", "pro"],
  },
  {
    slug: "kling-3-0-motion-control",
    studioId: "kling_motion_3_0",
    displayName: "Motion Control 3.0",
    description: "Улучшенный контроль движения. Лучшая консистентность лица.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "3/с",
    resolutions: ["std", "pro"],
  },
  {
    slug: "veo-3-fast",
    studioId: "veo_3_fast",
    displayName: "Veo 3 Fast",
    description: "Вторая по популярности модель. Хорошая генерация русской речи.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 13",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "veo-3-1-lite",
    studioId: "veo_3_1_lite",
    displayName: "Veo 3.1 Lite",
    description: "Лёгкая версия Veo 3.1. Хорошая генерация русской речи.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 13",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "omni-flash",
    studioId: null,
    displayName: "Omni Flash",
    description: "Новейшая модель Google. Аудио-нативная, до 5 голосов, редактирование видео.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 14",
    isNew: true,
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "seedance-1-5-pro",
    studioId: "seedance_pro_1_5",
    displayName: "Seedance 1.5 Pro",
    description: "Отличный выбор цена — качество, идеален для базовых задач.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 3.5",
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "grok-imagine-video-1.5",
    studioId: null,
    displayName: "Grok Imagine 1.5",
    description: "Новейшая модель от xAI, лучшая на рынке с русской речью.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/grok.webp`,
    price: "от 3.3",
    isNew: true,
    resolutions: ["720p", "1080p"],
  },
  {
    slug: "happyhorse-1-0",
    studioId: null,
    displayName: "HappyHorse 1.0",
    description:
      "Новейшая модель от Alibaba. Реалистичное движение, мульти-референс, редактирование видео.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/happyhorse.webp`,
    price: "от 10",
    isNew: true,
    resolutions: ["720p", "1080p"],
  },
];

export const OUTSEE_ACCENT = "#D1FE17";

export function studioIdToSlug(studioId: string | null | undefined, kind: OutseeMediaType): string {
  if (!studioId) return kind === "image" ? "gpt-image-2" : "veo-3-fast";
  const list = kind === "image" ? OUTSEE_IMAGE_MODELS : OUTSEE_VIDEO_MODELS;
  const hit = list.find((m) => m.studioId === studioId);
  if (hit) return hit.slug;
  return studioId.replace(/_/g, "-");
}

export function slugToStudioId(slug: string, kind: OutseeMediaType): string | null {
  const list = kind === "image" ? OUTSEE_IMAGE_MODELS : OUTSEE_VIDEO_MODELS;
  return list.find((m) => m.slug === slug)?.studioId ?? null;
}

export function aspectToStudioId(label: string): string {
  return label.replace(":", "_");
}

export function studioAspectToLabel(id: string | null | undefined): string {
  if (!id) return "9:16";
  return id.replace("_", ":");
}

export function resToStudioId(label: string): string {
  return label.toLowerCase();
}

export function studioResToLabel(id: string | null | undefined): string {
  if (!id) return "2K";
  if (id.endsWith("p")) return id;
  return id.toUpperCase();
}

export function outseeImageUrl(slug: string): string {
  return `${OUTSEE_ORIGIN}/image?model=${encodeURIComponent(slug)}`;
}

export function outseeCreateUrl(type: OutseeMediaType, slug: string): string {
  return `${OUTSEE_ORIGIN}/create?type=${type}&model=${encodeURIComponent(slug)}`;
}

export function getImageModel(slug: string): OutseeImageModel {
  return OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug) ?? OUTSEE_IMAGE_MODELS[7]!;
}

export function getVideoModel(slug: string): OutseeVideoModel {
  return OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug) ?? OUTSEE_VIDEO_MODELS[9]!;
}
