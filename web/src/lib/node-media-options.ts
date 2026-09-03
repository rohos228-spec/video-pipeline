/** Опции разрешения / качества / aspect для карточек в NodeModelPicker. */

export type MediaQualityId = "low" | "medium" | "high";

export const NODE_IMAGE_QUALITY_OPTIONS: { id: MediaQualityId; label: string }[] = [
  { id: "low", label: "мало" },
  { id: "medium", label: "средне" },
  { id: "high", label: "максимум" },
];

/** GPT Image 2 — полный список из Outsee create UI. */
export const GPT_IMAGE_ASPECTS = [
  "1:1",
  "16:9",
  "9:16",
  "4:3",
  "3:4",
  "3:2",
  "2:3",
  "21:9",
] as const;

/** Nano Banana* — полный список из Outsee create UI. */
export const NANO_BANANA_ASPECTS = [
  "16:9",
  "9:16",
  "1:1",
  "4:3",
  "5:4",
  "3:4",
  "4:5",
  "21:9",
] as const;

const SEEDREAM_ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] as const;

export type NodeMediaOptions = {
  resolutions: string[];
  qualities: { id: MediaQualityId; label: string }[];
  aspects: string[];
  defaultResolution: string;
  defaultQuality: MediaQualityId;
  defaultAspect: string;
};

function normalizeModelId(modelId: string): string {
  const id = (modelId || "").trim();
  if (id === "gpt-image-2") return "gpt-image-2-vip";
  return id;
}

export function mediaOptionsForModel(modelId: string | null | undefined): NodeMediaOptions | null {
  const id = normalizeModelId(modelId || "");
  if (!id) return null;

  if (id === "gpt-image-2-vip") {
    return {
      resolutions: ["1K", "2K", "4K"],
      qualities: NODE_IMAGE_QUALITY_OPTIONS,
      aspects: [...GPT_IMAGE_ASPECTS],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "flux-2-pro") {
    return {
      resolutions: ["1K", "2K"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "z-image") {
    return {
      resolutions: ["1K", "2K"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1", "4:3", "3:4"],
      defaultResolution: "1K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "qwen3-image") {
    return {
      resolutions: ["2K"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "topaz-image-upscale") {
    return {
      resolutions: ["2K", "4K"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1"],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id.startsWith("recraft")) {
    return {
      resolutions: ["1K", "2K"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1"],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id.startsWith("nano-banana")) {
    const resolutions = ["1K", "2K", "4K"];
    return {
      resolutions,
      qualities: NODE_IMAGE_QUALITY_OPTIONS,
      aspects: [...NANO_BANANA_ASPECTS],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id.startsWith("seedream")) {
    return {
      resolutions: ["1K", "2K"],
      qualities: [],
      aspects: [...SEEDREAM_ASPECTS],
      defaultResolution: "2K",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "veo-3-1-lite") {
    return {
      resolutions: ["720p"],
      qualities: [],
      aspects: ["16:9", "9:16"],
      defaultResolution: "720p",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "kling-2-6") {
    return {
      resolutions: ["720p", "1080p"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1"],
      defaultResolution: "1080p",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id.startsWith("kling")) {
    return {
      resolutions: ["720p", "1080p"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1"],
      defaultResolution: "1080p",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id.startsWith("seedance")) {
    return {
      resolutions: ["720p", "1080p"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1", "21:9"],
      defaultResolution: "1080p",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id.startsWith("wan") || id.startsWith("hailuo") || id.startsWith("pixverse")) {
    return {
      resolutions: ["720p", "1080p"],
      qualities: [],
      aspects: ["16:9", "9:16", "1:1"],
      defaultResolution: "720p",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  if (id === "topaz-video-upscale") {
    return {
      resolutions: ["1080p", "4k"],
      qualities: [],
      aspects: ["16:9", "9:16"],
      defaultResolution: "1080p",
      defaultQuality: "medium",
      defaultAspect: "16:9",
    };
  }

  return null;
}

export function clampMediaOption(
  value: string | null | undefined,
  allowed: string[],
  fallback: string,
): string {
  const v = (value || "").trim();
  if (v && allowed.includes(v)) return v;
  if (fallback && allowed.includes(fallback)) return fallback;
  return allowed[0] || fallback || "";
}

export function aspectToProjectId(aspect: string): string {
  return aspect.replace(":", "_");
}

export function resolutionToProjectId(resolution: string): string {
  return resolution.trim().toLowerCase();
}

export function qualityLabel(id: string | null | undefined): string {
  return NODE_IMAGE_QUALITY_OPTIONS.find((q) => q.id === id)?.label ?? id ?? "";
}
