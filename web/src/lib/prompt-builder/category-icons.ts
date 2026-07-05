import {
  Ban,
  Camera,
  Clapperboard,
  FileText,
  Film,
  Globe,
  Layers,
  Lightbulb,
  Mic,
  Palette,
  Scan,
  Sparkles,
  Sun,
  User,
  type LucideIcon,
} from "lucide-react";

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  world: Globe,
  visual_style: Palette,
  lighting: Sun,
  composition: Scan,
  background_density: Layers,
  camera_framing: Camera,
  camera_motion: Film,
  negative: Ban,
  voice_tone: Mic,
  narrative_structure: Clapperboard,
  forbidden_phrases: Ban,
  character_anatomy: User,
  script_role: User,
  source_policy: FileText,
  script_mode_selector: Layers,
  script_domain_skills: Lightbulb,
  script_narrative_structure: Clapperboard,
  script_continuity_rules: Sparkles,
  script_voice_tone: Mic,
  script_anti_gpt_patterns: Ban,
  script_output_contract: FileText,
  script_self_check: Scan,
  script_segmentation_rules: Layers,
  script_source_full: FileText,
  plan_role: User,
  plan_structure: Clapperboard,
  plan_voice_tone: Mic,
  plan_output_contract: FileText,
  plan_self_check: Scan,
  split_role: User,
  split_rules: Layers,
  split_output_contract: FileText,
  split_self_check: Scan,
  enrich_role: User,
  enrich_edit_rules: Layers,
  enrich_source_policy: FileText,
  enrich_output_contract: FileText,
  enrich_self_check: Scan,
  anim_motion_layers: Film,
  anim_output_contract: FileText,
  anim_negative: Ban,
  plan_source_full: FileText,
  split_source_full: FileText,
  hero_source_full: FileText,
  hero_style_source_full: FileText,
  items_source_full: FileText,
  enrich_source_full: FileText,
  anim_source_full: FileText,
  img_input_rules: FileText,
  img_scene_interpretation: Lightbulb,
  img_hero_policy: User,
  img_diversity_rules: Scan,
  img_context_logic: FileText,
  img_composition_discipline: Scan,
  img_prop_text_rules: Ban,
  img_output_contract: FileText,
  img_self_check: Scan,
  img_source_full: FileText,
  role: User,
  technical: Sparkles,
  features: Lightbulb,
  rules: Ban,
  narrative: Clapperboard,
  output: Layers,
};

export function iconForCategory(id: string): LucideIcon {
  return CATEGORY_ICONS[id] ?? Sparkles;
}

export function abbrevLabel(label: string, fallback = "BLK"): string {
  const letters = label.replace(/[^a-zA-Zа-яА-ЯёЁ0-9]/g, "");
  if (letters.length >= 3) return letters.slice(0, 3).toUpperCase();
  if (letters.length > 0) return letters.toUpperCase().padEnd(3, "·");
  return fallback.slice(0, 3).toUpperCase();
}

export function categoryAccent(index: number): string {
  const hues = [263, 220, 199, 42, 160, 280, 320, 210];
  return `hsl(${hues[index % hues.length]!} 52% 58%)`;
}
