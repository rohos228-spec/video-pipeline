import type { BlockVariant } from "./types";

type Patch = Pick<BlockVariant, "criteria" | "pairsWell" | "requires">;

/** Критерии и связи — меняются независимо от текста блока */
export const BLOCK_CRITERIA_PATCH: Record<string, Patch> = {
  role_shorts_writer: {
    criteria: { media: "text", pipeline: "plan" },
  },
  role_xlsx_agent: {
    criteria: { media: "excel", pipeline: "enrich" },
  },
  role_image_prompter: {
    criteria: { media: "image", pipeline: "img_pr" },
  },
  tech_60sec_vertical: {
    criteria: { media: "text", pipeline: "plan" },
  },
  tech_voiceover_chars: {
    criteria: { media: "text" },
  },
  tech_xlsx_row52: {
    criteria: { media: "excel" },
    requires: { media: "excel" },
  },
  tech_image_len: {
    criteria: { media: "image" },
  },
  tech_anim_8sec: {
    criteria: { media: "animation" },
  },
  feat_pixelart_cinematic: {
    criteria: { style_family: "pixelart", media: "image", tone: "calm", world: "cats" },
    pairsWell: ["feat_anthro_cats", "neg_no_humans"],
  },
  feat_knitted_2d: {
    criteria: { style_family: "textile", media: "image", tone: "playful", world: "humans" },
    pairsWell: ["rules_no_style_mix", "neg_no_text_logos"],
  },
  feat_clay_plasticine: {
    criteria: { style_family: "clay", media: "image", tone: "playful", world: "humans" },
    pairsWell: ["rules_no_style_mix"],
  },
  feat_trash_polka: {
    criteria: { style_family: "noir", media: "image", tone: "dark", world: "humans" },
    pairsWell: ["rules_no_style_mix", "neg_no_text_logos"],
  },
  feat_dark_bloody: {
    criteria: { style_family: "noir", media: "image", tone: "horror", world: "humans" },
  },
  feat_camera_slow_push: {
    criteria: { media: ["image", "animation"] },
    pairsWell: ["feat_pixelart_cinematic"],
  },
  feat_anthro_cats: {
    criteria: { world: "cats", media: "image" },
    pairsWell: ["feat_pixelart_cinematic", "neg_no_humans"],
  },
  rules_anti_doubles: {
    criteria: { media: "excel" },
  },
  rules_table_v7: {
    criteria: { media: "excel" },
    requires: { media: "excel" },
  },
  rules_no_style_mix: {
    criteria: { media: "image" },
  },
  rules_ai_cliches_ru: {
    criteria: { media: "text" },
  },
  rules_character_agent: {
    criteria: { media: "excel" },
  },
  narr_hook_insight: {
    criteria: { media: "text", tone: "calm" },
  },
  narr_detective: {
    criteria: { media: "text", tone: "calm" },
  },
  narr_documentary_calm: {
    criteria: { tone: "calm", media: "text" },
  },
  narr_steven_king: {
    criteria: { tone: "horror", media: "text" },
  },
  neg_no_text_logos: {
    criteria: { media: ["image", "animation"] },
  },
  neg_no_humans: {
    criteria: { world: "cats", media: "image" },
    requires: { world: "cats" },
  },
};

export function applyBlockCriteria(blocks: BlockVariant[]): BlockVariant[] {
  return blocks.map((b) => {
    const patch = BLOCK_CRITERIA_PATCH[b.id];
    if (!patch) return b;
    return { ...b, ...patch, criteria: { ...b.criteria, ...patch.criteria } };
  });
}
