#!/usr/bin/env node
/**
 * Проверка, что web/src/lib/outsee-catalog.ts содержит эталонные
 * опции outsee HH/d + Nn (chunks 8152 / 517).
 */
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const src = fs.readFileSync(path.join(root, "web/src/lib/outsee-catalog.ts"), "utf8");
const flat = src.replace(/\s+/g, " ");

function must(label, cond) {
  if (!cond) {
    console.error(`FAIL ${label}`);
    process.exit(1);
  }
}

must(
  "nano aspects order",
  flat.includes(
    'NANO_BANANA_ASPECTS = [ "16:9", "9:16", "1:1", "4:3", "5:4", "3:4", "4:5", "21:9", ]',
  ),
);
must(
  "gpt-image-2 aspects (no 5:4/4:5)",
  flat.includes(
    'GPT_IMAGE_2_ASPECTS = [ "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", ]',
  ) && !/"5:4"/.test(flat.slice(flat.indexOf("GPT_IMAGE_2_ASPECTS"), flat.indexOf("NANO_BANANA_ASPECTS"))),
);
must(
  "seedream aspects",
  flat.includes('const SEEDREAM_ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]'),
);

for (const [label, needle] of [
  ["nano-banana-2 res", 'return ["2K", "4K"]'],
  ["seedream-5-pro res", 'return ["1K", "2K"]'],
  ["seedream-5-lite res", 'return ["2K", "3K"]'],
  ["gpt-image-2 res", 'return ["1K", "2K", "4K"]'],
  ["veo/omni aspect override", 'return ["16:9", "9:16"]'],
  ["kling-3-0 nn res", 'resolutions: ["720p", "1080p", "4k"]'],
  ["kling-2-6 dur", "durations: [5, 10]"],
  ["omni dur", "durations: [4, 6, 8, 10]"],
  ["happyhorse res", 'resolutions: ["720P", "1080P"]'],
  ["gpt2 chips", 'chips: ["aspect", "resolution", "detail", "image-input"]'],
  ["veo chips", 'chips: ["aspect", "duration"]'],
  ["kling3 audio chip", 'chips: ["aspect", "resolution", "duration", "audio", "image-input"]'],
  ["motion chips", 'chips: ["orientation", "quality"]'],
]) {
  must(label, flat.includes(needle));
}

must(
  "nano-banana chips without resolution",
  /slug: "nano-banana",[\s\S]*?chips: \["aspect", "image-input"\]/.test(src),
);

must(
  "picker image order starts with nano-banana-pro",
  flat.indexOf('slug: "nano-banana-pro"') < flat.indexOf('slug: "nano-banana-2"') &&
    flat.indexOf('slug: "nano-banana-2"') < flat.indexOf('slug: "seedream-4.5"'),
);

console.log("ok — outsee chip options parity");
