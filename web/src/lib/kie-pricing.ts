/**
 * Динамическая цена KIE-модели по текущим настройкам.
 * Зеркало app/services/kie_catalog.py::estimate_credits (держать в синхроне).
 */

import type { KieModelSpec } from "@/lib/api";

export type KieValues = Record<string, unknown>;

export function kieDefaults(model: KieModelSpec): KieValues {
  const out: KieValues = {};
  for (const f of model.fields) {
    if (f.default !== undefined) out[f.name] = f.default;
  }
  return out;
}

export function estimateKie(
  model: KieModelSpec,
  values: KieValues,
  creditUsd: number,
): { credits: number; usd: number } {
  const p = model.pricing;
  const merged: KieValues = { ...kieDefaults(model), ...values };
  const hasFiles = (kinds: string[]) =>
    model.fields.some((f) => {
      if (!kinds.includes(f.kind)) return false;
      const v = merged[f.name];
      if (Array.isArray(v)) return v.length > 0;
      return typeof v === "string" && v.trim() !== "";
    });
  let credits: number | undefined;
  for (const r of p.rules || []) {
    const ok = Object.entries(r.when).every(([k, want]) => {
      if (k === "_has_images") return hasFiles(["images"]) === Boolean(want);
      if (k === "_has_videos") return hasFiles(["videos"]) === Boolean(want);
      const got = merged[k];
      if (typeof want === "boolean") {
        const truthy = got === true || String(got).toLowerCase() === "true";
        return truthy === want;
      }
      return String(got ?? "") === String(want);
    });
    if (ok) {
      credits = r.credits;
      break;
    }
  }
  if (credits === undefined) credits = p.default ?? 0;
  let total = credits;
  if (p.unit === "sec") {
    const d = Number(merged["duration"]) || 5;
    total = credits * Math.max(1, d);
  } else if (p.unit === "1k_chars") {
    const t = String(merged["text"] || merged["dialogue"] || "");
    total = credits * Math.max(1, Math.ceil(t.length / 1000));
  }
  const rounded = Math.round(total * 100) / 100;
  return { credits: rounded, usd: Math.round(rounded * creditUsd * 10000) / 10000 };
}

/** Главное текстовое поле модели (prompt/text/dialogue) — биндится на общий textarea. */
export function kieMainTextField(model: KieModelSpec): string | null {
  const f = model.fields.find(
    (x) =>
      (x.kind === "textarea" || x.kind === "dialogue") &&
      ["prompt", "text", "dialogue"].includes(x.name),
  );
  return f ? f.name : null;
}

export function kieFileFields(model: KieModelSpec) {
  return model.fields.filter((f) =>
    ["images", "videos", "audios"].includes(f.kind),
  );
}

export function kieChipFields(model: KieModelSpec) {
  return model.fields.filter(
    (f) =>
      !["images", "videos", "audios"].includes(f.kind) &&
      f.name !== kieMainTextField(model),
  );
}
