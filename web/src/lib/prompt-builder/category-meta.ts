import { humanizeSlug } from "@/lib/format-labels";

import type { BlockKindMeta } from "./types";



/** Градация уровней для отображения на пайплайне */

export const CATEGORY_TIER_LABELS: Record<number, string> = {

  1: "① Вход и смысл",

  2: "② Мир и герой",

  3: "③ Визуальный стиль",

  4: "④ Кадр и камера",

  5: "⑤ Свет",

  6: "⑥ Текст в кадре",

  7: "⑦ Запреты",

  8: "⑧ Формат вывода",

  10: "⑨ Сценарий · роль",

  11: "⑩ Сценарий · структура",

  12: "⑪ Сценарий · голос",

  13: "⑫ Сценарий · вывод",

};



type CategoryDef = {

  label: string;

  description: string;

  tier?: number;

};



/** Подписи категорий blocks — совпадают с разделами steps template.md */

const CATEGORY_META: Record<string, CategoryDef> = {

  // —— Image prompts (img_pr) ——

  img_input_rules: {

    label: "Вход: 1 ячейка → 1 промт",

    description: "Сегментация voiceover, без смешивания мыслей",

    tier: 1,

  },

  img_scene_interpretation: {

    label: "Реализм и абстракт",

    description: "5 способов визуализации, не «комната по умолчанию»",

    tier: 1,

  },

  img_hero_policy: {

    label: "Главный герой (ГГ)",

    description: "Референс, консистентность, эмоция",

    tier: 1,

  },

  img_diversity_rules: {

    label: "Разнообразие кадров",

    description: "План, POV, среда, погода — не повторять",

    tier: 1,

  },

  img_context_logic: {

    label: "Только из source",

    description: "Без вымысла, без случайных улик",

    tier: 1,

  },

  world: {

    label: "Мир персонажей",

    description: "Коты, люди или по стиль-гайду",

    tier: 2,

  },

  character_anatomy: {

    label: "Анатомия и одежда",

    description: "Пальцы, зубы, шерсть, костюм",

    tier: 2,

  },

  visual_style: {

    label: "Визуальный стиль",

    description: "Pixel, trash polka, clay, textile, noir…",

    tier: 3,

  },

  composition: {

    label: "Композиция 9:16",

    description: "Формат кадра, фокус, rule of thirds",

    tier: 4,

  },

  camera_framing: {

    label: "План и ракурс",

    description: "Medium / full / close-up, POV",

    tier: 4,

  },

  background_density: {

    label: "Глубина среды",

    description: "Передний, средний, задний план",

    tier: 4,

  },

  img_composition_discipline: {

    label: "Дисциплина кадра",

    description: "Foreground, неповтор реквизита (polka)",

    tier: 4,

  },

  lighting: {

    label: "Свет",

    description: "Chiaroscuro, noir, soft, moonlit",

    tier: 5,

  },

  img_prop_text_rules: {

    label: "Текст на предметах",

    description: "Blank или RU на бумагах/плакатах",

    tier: 6,

  },

  negative: {

    label: "Негатив (--no)",

    description: "Negative prompt, запреты стиля",

    tier: 7,

  },

  img_output_contract: {

    label: "Формат ответа",

    description: "xlsx, нумерация, PROMPT/NEGATIVE пары",

    tier: 8,

  },

  img_self_check: {

    label: "Самопроверка",

    description: "Чеклист перед выдачей промта",

    tier: 8,

  },

  img_source_full: {

    label: "Полный исходник",

    description: "Legacy .md целиком, для справки",

    tier: 8,

  },

  camera_motion: {

    label: "Движение камеры",

    description: "Push-in, pan, static",

    tier: 4,

  },

  voice_tone: { label: "Тон озвучки", description: "Документальный, драматичный", tier: 12 },

  narrative_structure: { label: "Драматургия", description: "Hook и структура", tier: 11 },

  forbidden_phrases: { label: "Запреты текста", description: "AI-клише", tier: 7 },

  script_role: { label: "Роль и задача", description: "Кто пишет voiceover", tier: 10 },

  source_policy: { label: "Источник и факты", description: "XLSX, без выдумок", tier: 10 },

  script_mode_selector: { label: "Режим сценария", description: "Герой, тема, процесс…", tier: 10 },

  script_domain_skills: { label: "Навыки по материалу", description: "Био, история, наука…", tier: 11 },

  script_narrative_structure: { label: "Каркас рассказа", description: "Хук → финал / CTA", tier: 11 },

  script_continuity_rules: { label: "Связность речи", description: "Текст для уха", tier: 12 },

  script_voice_tone: { label: "Голос и тон", description: "Документальный диктор", tier: 12 },

  script_anti_gpt_patterns: { label: "Анти-GPT фильтр", description: "Клише и контрасты", tier: 12 },

  script_output_contract: { label: "Формат вывода", description: "voiceover.txt, лимит", tier: 13 },

  script_self_check: { label: "Самопроверка", description: "Перед выдачей", tier: 13 },

  script_segmentation_rules: { label: "Ячейки 110–140", description: "Только long-form", tier: 13 },

  script_source_full: { label: "Полный исходник", description: "Legacy script целиком", tier: 13 },

  plan_role: { label: "Роль планера", description: "Кто строит план", tier: 10 },

  plan_structure: { label: "Структура 60с", description: "Хук → CTA", tier: 11 },

  plan_voice_tone: { label: "Тон плана", description: "Живой, ясный", tier: 12 },

  plan_output_contract: { label: "Вывод плана", description: "xlsx + тайминги", tier: 13 },

  plan_self_check: { label: "Проверка плана", description: "Логика и xlsx", tier: 13 },

  split_role: { label: "Разметчик", description: "Делит voiceover", tier: 10 },

  split_rules: { label: "Микромысли", description: "45–100 символов", tier: 11 },

  split_output_contract: { label: "Строка 49", description: "C49, D49…", tier: 13 },

  split_self_check: { label: "Проверка ячеек", description: "Без разрывов", tier: 13 },

  enrich_role: { label: "Excel-редактор", description: "Точечная задача", tier: 10 },

  enrich_edit_rules: { label: "Правки", description: "Не ломать листы", tier: 11 },

  enrich_source_policy: { label: "Только задача", description: "Без выдумок", tier: 11 },

  enrich_output_contract: { label: "Вернуть xlsx", description: "Полный файл", tier: 13 },

  enrich_self_check: { label: "Проверка xlsx", description: "Структура цела", tier: 13 },

  anim_motion_layers: { label: "Слои движения", description: "Перед/серед/даль", tier: 4 },

  anim_output_contract: { label: "Формат Veo", description: "Один промт", tier: 13 },

  anim_negative: { label: "Запреты видео", description: "Без shift/new", tier: 7 },

  plan_source_full: { label: "Полный исходник", description: "Legacy plan целиком", tier: 13 },

  split_source_full: { label: "Полный исходник", description: "Legacy split целиком", tier: 13 },

  hero_source_full: { label: "Полный исходник", description: "Legacy hero целиком", tier: 13 },

  hero_style_source_full: { label: "Исходник стиля", description: "Hero style целиком", tier: 13 },

  items_source_full: { label: "Полный исходник", description: "Legacy items целиком", tier: 13 },

  enrich_source_full: { label: "Полный исходник", description: "Legacy enrich целиком", tier: 13 },

  anim_source_full: { label: "Полный исходник", description: "Legacy anim целиком", tier: 13 },

};



export function categoryMetaFor(ids: string[]): BlockKindMeta[] {

  return ids.map((id) => {

    const m = CATEGORY_META[id];

    const tier = m?.tier;

    return {

      id,

      label: m?.label ?? humanizeSlug(id),

      short: (m?.label ?? humanizeSlug(id)).slice(0, 8),

      color: "hsl(0 0% 55%)",

      description: m?.description ?? humanizeSlug(id),

      tier,

      tierLabel: tier != null ? CATEGORY_TIER_LABELS[tier] : undefined,

    };

  });

}



export function categoryMetaOne(id: string): BlockKindMeta {

  return categoryMetaFor([id])[0]!;

}



export function sortCategoriesByTier(kinds: BlockKindMeta[]): BlockKindMeta[] {

  return [...kinds].sort((a, b) => {

    const ta = a.tier ?? 99;

    const tb = b.tier ?? 99;

    if (ta !== tb) return ta - tb;

    return a.label.localeCompare(b.label, "ru");

  });

}



export function groupCategoriesByTier(

  kinds: BlockKindMeta[],

): { tier: number; tierLabel: string; items: BlockKindMeta[] }[] {

  const sorted = sortCategoriesByTier(kinds);

  const groups: { tier: number; tierLabel: string; items: BlockKindMeta[] }[] = [];

  for (const k of sorted) {

    const tier = k.tier ?? 99;

    const tierLabel = k.tierLabel ?? CATEGORY_TIER_LABELS[tier] ?? "Прочее";

    const last = groups[groups.length - 1];

    if (last && last.tier === tier) last.items.push(k);

    else groups.push({ tier, tierLabel, items: [k] });

  }

  return groups;

}


