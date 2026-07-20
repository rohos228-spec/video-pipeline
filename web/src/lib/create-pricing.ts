/**
 * Клиентский fallback цены Create: 1 токен = $0.10.
 * Основной источник — GET /api/grsai/quote; это на случай офлайна UI.
 */

export const TOKEN_USD = 0.1;

type Media = "image" | "video" | "audio";

const IMAGE_BASE: Record<string, number> = {
  "gpt-image-2": 0.5,
  "gpt-image-2-vip": 1,
  "nano-banana": 0.3,
  "nano-banana-fast": 0.3,
  "nano-banana-2-lite": 0.3,
  "nano-banana-2": 0.8,
  "nano-banana-pro": 1.2,
  "nano-banana-pro-vt": 1.2,
  "gpt-image-1.5": 3,
  "seedream-4.5": 2,
  "seedream-5-pro": 3,
  "seedream-5-lite": 2,
};

const VIDEO_BASE: Record<string, number> = {
  "sora-2": 0.8,
  "sora2-portrait": 0.8,
  "sora2-landscape": 0.8,
  "veo3.1-fast": 4,
  "veo3.1-pro": 4,
  "veo-3-1-lite": 4,
  "veo-3-fast": 4,
  "kling-3-0": 5,
  "kling-2-6": 4,
  "seedance-1-5-pro": 3.5,
  "seedance-2-0-global": 20,
};

const AUDIO_BASE: Record<string, number> = {
  "suno-5-5": 2.5,
  "elevenlabs-v3": 1,
};

function roundTokens(n: number): number {
  if (n <= 0) return 0;
  return Math.max(0.1, Math.round(n * 10) / 10);
}

function fmtTokens(n: number): string {
  return Number.isInteger(n) ? String(n) : String(n);
}

function parseCatalogPrice(raw?: string | null): number | null {
  if (!raw) return null;
  let s = raw.trim().toLowerCase().replace(/^от\s*/, "").replace(",", ".");
  let num = "";
  for (const ch of s) {
    if ((ch >= "0" && ch <= "9") || ch === ".") num += ch;
    else if (num) break;
  }
  if (!num) return null;
  const val = Number(num);
  if (!Number.isFinite(val) || val <= 0) return null;
  if (val < 1) return val / TOKEN_USD;
  return val;
}

export function estimateCreatePrice(opts: {
  media: Media;
  model: string;
  resolution?: string;
  duration?: number;
  size?: string;
  catalogPrice?: string;
}): { tokens: number; usd: number; label: string } {
  const { media, model } = opts;
  let base = 0;
  if (media === "image") {
    base = IMAGE_BASE[model] ?? 0;
    const r = (opts.resolution || "1K").toUpperCase();
    if (r === "4K") base *= 2;
    else if (r === "2K" || r === "3K") base *= 1.5;
  } else if (media === "video") {
    base = VIDEO_BASE[model] ?? 0;
    const d = opts.duration || 10;
    if (model.startsWith("sora")) {
      if (d >= 15) base *= 1.5;
      if ((opts.size || "small") === "large") base *= 2;
    } else if (!model.startsWith("veo") && d > 0) {
      base *= Math.max(0.5, d / 5);
    }
  } else {
    base = AUDIO_BASE[model] ?? 0;
  }
  if (base <= 0) {
    const parsed = parseCatalogPrice(opts.catalogPrice);
    base = parsed && parsed > 0 ? parsed : 1;
  }
  const tokens = roundTokens(base);
  const usd = Math.round(tokens * TOKEN_USD * 100) / 100;
  return {
    tokens,
    usd,
    label: `${fmtTokens(tokens)} ток · $${usd.toFixed(2)}`,
  };
}
