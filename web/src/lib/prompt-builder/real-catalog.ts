import { api } from "@/lib/api";
import type { BlockVariant, PromptSelection, PromptSlot, PromptTemplate } from "./types";
import { categoryMetaFor } from "./category-meta";
import { isSlotEmpty } from "./compose";
import { COMPOSE_STEP_LABELS } from "./step-compose-map";

export type RealBlockDTO = {
  category: string;
  id: string;
  label: string;
  preview: string;
  body: string;
};

export type StepTemplateMeta = {
  step_id: string;
  block_categories: string[];
  vars: string[];
};

const DEFAULT_BLOCKS: Record<string, string> = {
  world: "cats_anthropomorphic",
  visual_style: "epic_pixel_cats_default",
  lighting: "cinematic_chiaroscuro",
  negative: "cats_pixel_default",
  voice_tone: "documentary_calm",
  composition: "vertical_9_16_character",
  background_density: "rich_three_plane_environment",
  camera_framing: "medium_full_mix",
  camera_motion: "slow_push_in",
  forbidden_phrases: "ai_cliches_ru",
  narrative_structure: "shorts_hook_insight",
  img_input_rules: "one_cell_one_prompt",
  img_scene_interpretation: "realism_and_abstract_five_ways",
  img_hero_policy: "hero_reference_strict",
  img_diversity_rules: "scene_variety",
  img_context_logic: "source_only_no_invention",
  img_composition_discipline: "trash_polka_foreground_v25",
  img_prop_text_rules: "blank_papers_default",
  img_output_contract: "xlsx_dash_separated",
  img_self_check: "pre_output_gate",
  character_anatomy: "anthro_cat_sheet",
  script_role: "voiceover_author",
  source_policy: "xlsx_general_plan_only",
  script_mode_selector: "universal_modes",
  script_domain_skills: "biography_history_science_process_object",
  script_narrative_structure: "short_voiceover_arc",
  script_continuity_rules: "smooth_voiceover_flow",
  script_voice_tone: "human_documentary_voice",
  script_anti_gpt_patterns: "zinser_filter",
  script_output_contract: "voiceover_txt_60s",
  script_self_check: "voiceover_quality_gate",
  script_segmentation_rules: "long_cells_110_140",
  script_source_full: "scenario_agent_full",
  plan_role: "shorts_planner",
  plan_structure: "viral_60s_timeline",
  plan_voice_tone: "human_clear_pitch",
  plan_output_contract: "xlsx_plan_timing",
  plan_self_check: "plan_quality_gate",
  split_role: "voiceover_segmenter",
  split_rules: "microthought_cells",
  split_output_contract: "xlsx_row49",
  split_self_check: "no_broken_words_gate",
  enrich_role: "xlsx_editor",
  enrich_edit_rules: "sheet_safe_edits",
  enrich_source_policy: "xlsx_task_only",
  enrich_output_contract: "return_full_xlsx",
  enrich_self_check: "no_structure_damage_gate",
  anim_motion_layers: "three_plane_motion",
  anim_output_contract: "veo_single_prompt",
  anim_negative: "no_style_shift",
  plan_source_full: "default_full",
  split_source_full: "default_full",
  hero_source_full: "default_full",
  hero_style_source_full: "default_full",
  items_source_full: "default_full",
  enrich_source_full: "default_full",
  anim_source_full: "default_full",
};

export const DEFAULT_VARS: Record<string, string | number> = {
  VIDEO_DURATION_SEC: 60,
  VOICEOVER_MIN_CHARS: 800,
  VOICEOVER_MAX_CHARS: 900,
  PROMPT_LEN_MIN: 500,
  PROMPT_LEN_MAX: 4800,
  VIDEO_DURATION_MAX_SEC: 8,
  ASPECT_RATIO_VIDEO: "9:16",
};

export function buildTemplateFromMeta(
  stepId: string,
  stepCode: string,
  meta: StepTemplateMeta,
): PromptTemplate {
  const slots: PromptSlot[] = meta.block_categories.map((cat) => ({
    slotId: cat,
    kind: cat,
    required: true,
    defaultBlockId: DEFAULT_BLOCKS[cat] ?? "",
  }));

  return {
    id: stepId,
    stepCode,
    label: COMPOSE_STEP_LABELS[stepId] ?? stepId,
    category: stepId,
    slots,
  };
}

export function blocksFromCatalog(items: RealBlockDTO[]): BlockVariant[] {
  return items.map((b) => ({
    id: b.id,
    kind: b.category,
    label: b.label,
    tags: [b.category],
    steps: [],
    body: b.body,
  }));
}

export function selectionFromProject(
  template: PromptTemplate,
  promptOverrides: Record<string, unknown> | undefined,
): PromptSelection {
  const po = promptOverrides ?? {};
  const projectBlocks =
    po.blocks && typeof po.blocks === "object"
      ? (po.blocks as Record<string, string>)
      : {};
  const projectVars =
    po.vars && typeof po.vars === "object" ? (po.vars as Record<string, string | number>) : {};

  const slots: Record<string, string> = {};
  for (const slot of template.slots) {
    slots[slot.slotId] = projectBlocks[slot.slotId] ?? slot.defaultBlockId;
  }
  for (const [slotId, blockId] of Object.entries(projectBlocks)) {
    if (slotId.startsWith("extra_") && blockId) {
      slots[slotId] = blockId;
    }
  }

  return {
    templateId: template.id,
    slots,
    vars: { ...DEFAULT_VARS, ...projectVars },
  };
}

export function extraSlotsFromProject(
  template: PromptTemplate,
  promptOverrides: Record<string, unknown> | undefined,
): PromptSlot[] {
  const po = promptOverrides ?? {};
  const projectBlocks =
    po.blocks && typeof po.blocks === "object"
      ? (po.blocks as Record<string, string>)
      : {};
  const templateKinds = new Set(template.slots.map((s) => s.kind));
  const extras: PromptSlot[] = [];

  for (const [slotId, blockId] of Object.entries(projectBlocks)) {
    if (!slotId.startsWith("extra_") || !blockId) continue;
    const match = slotId.match(/^extra_(.+)_\d+$/);
    const kind = match?.[1] ?? "";
    if (!kind || !templateKinds.has(kind)) continue;
    extras.push({ slotId, kind, required: false, defaultBlockId: blockId });
  }
  return extras;
}

/**
 * Убирает дубликаты extra_* для категорий, которые уже есть в шаблоне,
 * и схлопывает несколько extra одного kind в один.
 */
export function normalizePromptSlotState(
  template: PromptTemplate,
  selection: PromptSelection,
): { selection: PromptSelection; extras: PromptSlot[]; changed: boolean } {
  const slots = { ...selection.slots };
  let changed = false;

  for (const [slotId, blockId] of Object.entries(slots)) {
    if (slotId.startsWith("extra_") && !blockId) {
      delete slots[slotId];
      changed = true;
    }
  }

  const extras = extraSlotsFromProject(template, { blocks: slots });
  return {
    selection: { ...selection, slots },
    extras,
    changed,
  };
}

/** Один terminal на категорию — для neural-графа (без дублей extra). */
export function displaySlotsForGraph(
  template: PromptTemplate,
  allSlots: PromptSlot[],
  slotValues: Record<string, string>,
): PromptSlot[] {
  const seen = new Set<string>();
  const out: PromptSlot[] = [];

  for (const slot of template.slots) {
    if (isSlotEmpty(slotValues, slot)) continue;
    out.push(slot);
    seen.add(slot.kind);
  }
  for (const slot of allSlots) {
    if (seen.has(slot.kind)) continue;
    if (isSlotEmpty(slotValues, slot)) continue;
    out.push(slot);
    seen.add(slot.kind);
  }
  return out;
}

/** Сколько категорий заполнено (уникальных kind). */
export function filledSlotsCountForTemplate(
  template: PromptTemplate,
  promptOverrides: Record<string, unknown> | undefined,
): number {
  const sel = selectionFromProject(template, promptOverrides);
  const { selection: normalized } = normalizePromptSlotState(template, sel);
  let n = 0;
  for (const slot of template.slots) {
    const id = normalized.slots[slot.slotId];
    if (id && String(id).trim()) n++;
  }
  return n;
}

export function blocksMapFromSelection(
  template: PromptTemplate,
  slots: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const slot of template.slots) {
    const v = slots[slot.slotId];
    if (v) out[slot.slotId] = v;
  }
  for (const [slotId, v] of Object.entries(slots)) {
    if (slotId.startsWith("extra_") && v) out[slotId] = v;
  }
  return out;
}

export async function loadRealPromptBuilder(stepId: string, stepCode: string) {
  const [catalog, meta] = await Promise.all([
    api.promptStudioCatalog(),
    api.promptStudioStepMeta(stepId),
  ]);

  const template = buildTemplateFromMeta(stepId, stepCode, meta);
  const allowed = new Set(meta.block_categories);
  const blocks = blocksFromCatalog(
    (catalog.blocks ?? []).filter(
      (b) => allowed.has(b.category) && b.category !== "script_source_full",
    ),
  );
  const categoryKinds = categoryMetaFor(meta.block_categories);
  const blockCategoryIndex = catalog.block_categories ?? {};
  const allCatalogBlocks = blocksFromCatalog(
    (catalog.blocks ?? []).filter((b) => b.category !== "script_source_full"),
  );

  return {
    template,
    blocks,
    categoryKinds,
    meta,
    stylePresets: catalog.style_presets,
    blockCategoryIndex,
    allCatalogBlocks,
    stepBlockCategories: catalog.step_block_categories ?? {},
  };
}
