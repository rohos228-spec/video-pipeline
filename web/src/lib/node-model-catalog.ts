import type { VibecodeSnapshotModel } from "./vibecode-models-snapshot";
import { VIBECODE_MODELS_SNAPSHOT } from "./vibecode-models-snapshot";

export type ModelChannel = "stable";
export type ModelKind = "text" | "image" | "video";
export type ModelVendorId =
  | "anthropic"
  | "openai"
  | "gemini"
  | "deepseek"
  | "xai"
  | "images"
  | "video"
  | "other";

export type RawVibecodePricing = {
  currency?: string;
  input_usd_per_m?: number | null;
  output_usd_per_m?: number | null;
  cache_read_usd_per_m?: number | null;
  cache_create_usd_per_m?: number | null;
  usd_per_image?: number | null;
};

export type DisplayPricing = {
  currency: string;
  markup: number;
  input_usd_per_m?: number;
  output_usd_per_m?: number;
  cache_read_usd_per_m?: number;
  cache_create_usd_per_m?: number;
  usd_per_image?: number;
};

export type CatalogModel = {
  id: string;
  label: string;
  vendor: ModelVendorId;
  kind: ModelKind;
  online: boolean;
  resolution?: string | null;
  pricing: DisplayPricing;
  api_model: string;
  provider: string;
  image_generator?: string | null;
  video_generator?: string | null;
  channel?: ModelChannel;
};

export type CatalogVendor = {
  id: ModelVendorId | string;
  label: string;
  icon: string;
  count: number;
  models: CatalogModel[];
};

export type ModelCatalogPayload = {
  channel: ModelChannel;
  markup?: number;
  markup_stable?: number;
  vendors: CatalogVendor[];
  models: CatalogModel[];
};

export const PRICE_MARKUP = 3;
export const PRICE_MARKUP_STABLE = PRICE_MARKUP;
export const DEFAULT_TEXT_MODEL_ID = "gpt-5.6-sol";
export const DEFAULT_IMAGE_MODEL_ID = "gpt-image-2-vip";
export const DEFAULT_VIDEO_MODEL_ID = "veo-3-1-lite";
export const IMAGE_NODE_TYPES = new Set(["images", "hero", "items", "hitl_images"]);
export const VIDEO_NODE_TYPES = new Set(["videos", "hitl_videos"]);
export const IMAGE_MODEL_ALIASES: Record<string, string> = { "gpt-image-2": "gpt-image-2-vip" };
export const HIDDEN_IMAGE_IDS = new Set(["gpt-image-2", "nano-banana-pro"]);

export const VENDOR_META: Record<
  Exclude<ModelVendorId, "other">,
  { id: Exclude<ModelVendorId, "other">; label: string; icon: string }
> = {
  anthropic: { id: "anthropic", label: "Anthropic", icon: "A" },
  openai: { id: "openai", label: "OpenAI", icon: "hex" },
  gemini: { id: "gemini", label: "Gemini", icon: "spark" },
  deepseek: { id: "deepseek", label: "DeepSeek", icon: "D" },
  xai: { id: "xai", label: "xAI", icon: "X" },
  images: { id: "images", label: "Изображения", icon: "image" },
  video: { id: "video", label: "Видео", icon: "image" },
};

const VENDOR_ORDER: Exclude<ModelVendorId, "other">[] = [
  "anthropic",
  "openai",
  "gemini",
  "deepseek",
  "xai",
  "images",
  "video",
];

type SnapshotRow = VibecodeSnapshotModel;

function vendorOf(id: string, isImage: boolean, isVideo = false): ModelVendorId {
  if (isVideo) return "video";
  if (isImage) return "images";
  const mid = id.toLowerCase();
  if (mid.startsWith("claude")) return "anthropic";
  if (mid.startsWith("gemini")) return "gemini";
  if (mid.startsWith("deepseek")) return "deepseek";
  if (mid.startsWith("grok")) return "xai";
  return "openai";
}

function resolutionBadge(displayName: string, modelId: string): string | null {
  const name = `${displayName} ${modelId}`.toLowerCase();
  if (name.includes("1k/2k/4k") || modelId === "nano-banana-2" || modelId === "nano-banana-pro") {
    return "1K/2K/4K";
  }
  return null;
}

export function markupForChannel(_channel?: ModelChannel | string | null): number {
  return PRICE_MARKUP;
}

export function applyMarkup(
  pricing: RawVibecodePricing | null | undefined,
  _channel: ModelChannel | string = "stable",
): DisplayPricing {
  const factor = PRICE_MARKUP;
  const out: DisplayPricing = { currency: pricing?.currency || "usd", markup: factor };
  const keys = [
    "input_usd_per_m",
    "output_usd_per_m",
    "cache_read_usd_per_m",
    "cache_create_usd_per_m",
    "usd_per_image",
  ] as const;
  for (const key of keys) {
    const raw = pricing?.[key];
    if (typeof raw === "number" && Number.isFinite(raw)) {
      out[key] = Math.round(raw * factor * 1e6) / 1e6;
    }
  }
  return out;
}

export function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 0.1) return value.toFixed(2).replace(/\.00$/, ".00");
  if (value >= 0.01) return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "") || "0";
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "") || "0";
}

export function formatUsdPrice(value: number | null | undefined): string {
  const n = formatUsd(value);
  return n === "—" ? n : `$${n}`;
}

export function defaultModelIdForNodeType(nodeType: string | null | undefined): string {
  if (IMAGE_NODE_TYPES.has(nodeType || "")) return DEFAULT_IMAGE_MODEL_ID;
  if (VIDEO_NODE_TYPES.has(nodeType || "")) return DEFAULT_VIDEO_MODEL_ID;
  return DEFAULT_TEXT_MODEL_ID;
}

function kindOf(row: SnapshotRow, isVideo = false): ModelKind {
  if (isVideo) return "video";
  return row.is_image ? "image" : "text";
}

const IMAGE_EXTRA: CatalogModel[] = [
  {
    id: "nano-banana-fast",
    label: "Nano Banana Fast",
    vendor: "images",
    kind: "image",
    online: true,
    resolution: "1K/2K",
    pricing: { currency: "usd", markup: PRICE_MARKUP, usd_per_image: 0.03 },
    api_model: "nano-banana-fast",
    provider: "outsee",
    image_generator: "nano_banana_fast",
    channel: "stable",
  },
];

const VIDEO_EXTRA: CatalogModel[] = [
  {
    id: "veo-3-1-lite",
    label: "Veo 3.1 Lite",
    vendor: "video",
    kind: "video",
    online: true,
    pricing: { currency: "usd", markup: PRICE_MARKUP },
    api_model: "veo-3-1-lite",
    provider: "outsee",
    video_generator: "veo_3_1_lite",
    channel: "stable",
  },
  {
    id: "kling-2-6",
    label: "Kling 2.6",
    vendor: "video",
    kind: "video",
    online: true,
    pricing: { currency: "usd", markup: PRICE_MARKUP },
    api_model: "kling-2-6",
    provider: "kie",
    video_generator: "kling_2_6",
    channel: "stable",
  },
];

function normalizeSnapshot(row: SnapshotRow): CatalogModel {
  const isImage = Boolean(row.is_image);
  const pricing = applyMarkup(row.pricing as RawVibecodePricing);
  const label = row.id === "gpt-image-2-vip" ? "GPT Image 2" : row.display_name;
  return {
    id: row.id,
    label,
    vendor: vendorOf(row.id, isImage),
    kind: kindOf(row),
    online: true,
    resolution: resolutionBadge(row.display_name, row.id),
    pricing,
    api_model: row.id,
    provider: isImage ? "outsee" : "vibecode",
    channel: "stable",
  };
}

export function localCatalog(): ModelCatalogPayload {
  const models = [
    ...VIBECODE_MODELS_SNAPSHOT.filter((row) => !HIDDEN_IMAGE_IDS.has(row.id)).map((row) =>
      normalizeSnapshot(row),
    ),
    ...IMAGE_EXTRA,
    ...VIDEO_EXTRA,
  ];
  const vendors: CatalogVendor[] = [];
  for (const vid of VENDOR_ORDER) {
    const items = models.filter((m) => m.vendor === vid);
    if (!items.length) continue;
    vendors.push({ ...VENDOR_META[vid], count: items.length, models: items });
  }
  return {
    channel: "stable",
    markup: PRICE_MARKUP,
    markup_stable: PRICE_MARKUP,
    vendors,
    models,
  };
}

export function catalogForNodeType(
  catalog: ModelCatalogPayload,
  nodeType: string | null | undefined,
): ModelCatalogPayload {
  const kind: ModelKind = IMAGE_NODE_TYPES.has(nodeType || "")
    ? "image"
    : VIDEO_NODE_TYPES.has(nodeType || "")
      ? "video"
      : "text";
  const models = catalog.models.filter((m) => m.kind === kind);
  const vendors = catalog.vendors
    .map((v) => {
      const items = v.models.filter((m) => m.kind === kind);
      return { ...v, models: items, count: items.length };
    })
    .filter((v) => v.count > 0);
  return { ...catalog, models, vendors };
}

export function findCatalogModel(
  catalog: ModelCatalogPayload | null | undefined,
  modelId: string | null | undefined,
): CatalogModel | undefined {
  if (!catalog || !modelId) return undefined;
  const want = IMAGE_MODEL_ALIASES[modelId] || modelId;
  return catalog.models.find((m) => m.id === want);
}

export function vendorForModel(model: CatalogModel | undefined): ModelVendorId {
  return (model?.vendor as ModelVendorId) || "openai";
}
