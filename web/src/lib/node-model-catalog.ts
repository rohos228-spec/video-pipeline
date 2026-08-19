import type { VibecodeSnapshotModel } from "./vibecode-models-snapshot";
import { VIBECODE_MODELS_SNAPSHOT } from "./vibecode-models-snapshot";

export type ModelChannel = "stable";
export type ModelKind = "text" | "image";
export type ModelVendorId =
  | "anthropic"
  | "openai"
  | "gemini"
  | "xai"
  | "moonshot"
  | "images"
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
export const DEFAULT_IMAGE_MODEL_ID = "gpt-image-2";
export const IMAGE_NODE_TYPES = new Set(["images", "hero", "items", "hitl_images"]);

export const VENDOR_META: Record<
  Exclude<ModelVendorId, "other">,
  { id: Exclude<ModelVendorId, "other">; label: string; icon: string }
> = {
  anthropic: { id: "anthropic", label: "Anthropic", icon: "A" },
  openai: { id: "openai", label: "OpenAI", icon: "hex" },
  gemini: { id: "gemini", label: "Gemini", icon: "spark" },
  xai: { id: "xai", label: "xAI", icon: "X" },
  moonshot: { id: "moonshot", label: "Moonshot", icon: "moon" },
  images: { id: "images", label: "Изображения", icon: "image" },
};

const VENDOR_ORDER: Exclude<ModelVendorId, "other">[] = [
  "anthropic",
  "openai",
  "gemini",
  "xai",
  "moonshot",
  "images",
];

type SnapshotRow = VibecodeSnapshotModel;

function vendorOf(id: string, isImage: boolean): ModelVendorId {
  if (isImage) return "images";
  const mid = id.toLowerCase();
  if (mid.startsWith("claude")) return "anthropic";
  if (mid.startsWith("gemini")) return "gemini";
  if (mid.startsWith("grok")) return "xai";
  if (mid.startsWith("kimi")) return "moonshot";
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
  return IMAGE_NODE_TYPES.has(nodeType || "") ? DEFAULT_IMAGE_MODEL_ID : DEFAULT_TEXT_MODEL_ID;
}

function normalizeSnapshot(row: SnapshotRow): CatalogModel {
  const isImage = Boolean(row.is_image);
  const pricing = applyMarkup(row.pricing as RawVibecodePricing);
  return {
    id: row.id,
    label: row.display_name,
    vendor: vendorOf(row.id, isImage),
    kind: isImage ? "image" : "text",
    online: true,
    resolution: resolutionBadge(row.display_name, row.id),
    pricing,
    api_model: row.id,
    provider: "vibecode",
    channel: "stable",
  };
}

export function localCatalog(): ModelCatalogPayload {
  const models = VIBECODE_MODELS_SNAPSHOT.map((row) => normalizeSnapshot(row));
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

export function findCatalogModel(
  catalog: ModelCatalogPayload | null | undefined,
  modelId: string | null | undefined,
): CatalogModel | undefined {
  if (!catalog || !modelId) return undefined;
  return catalog.models.find((m) => m.id === modelId);
}

export function vendorForModel(model: CatalogModel | undefined): ModelVendorId {
  return (model?.vendor as ModelVendorId) || "openai";
}
