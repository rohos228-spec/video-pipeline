/** Имена .md-файлов в prompts/05a_enrich_* … 05e_enrich_* (без расширения). */

export const ENRICH_PROMPT_FILES: Record<string, string[]> = {
  enrich_1: [
    "default",
    "XLSX_Agent_V7_1_Optimized.txt",
    "XLSX_Agent_V7_2_Row52_Strengthened.txt",
    "_reworked_prompts_index",
    "_universal_enrich_1_prompt_blueprint",
    "agent_table_rules_V6_1_1_plan_only.txt",
    "pravilo_zapolneniya_V5_bez_kotov.txt",
    "reworked_default_blocks_v2",
    "reworked_xlsx_agent_v7_2_row52_strengthened_txt_blocks_v2",
    "reworked_zapolnenie_tablicy_blocks_v2",
    "От клода",
    "Правило_заполнения_таблицы_V7_обновлено.txt",
    "заполнение таблицы",
  ],
  enrich_2: [
    "default",
    "_reworked_prompts_index",
    "_universal_enrich_2_prompt_blueprint",
    "reworked_default_blocks_v2",
    "агент по созданию персонажей.txt",
  ],
  enrich_3: [
    "default",
    "GPT_Agent_Excel_Plan_Enhanced.txt",
    "_reworked_prompts_index",
    "_universal_enrich_3_prompt_blueprint",
    "reworked_default_blocks_v2",
  ],
  enrich_4: [
    "default",
    "_reworked_prompts_index",
    "_universal_enrich_4_prompt_blueprint",
    "reworked_default_blocks_v2",
  ],
  enrich_5: [
    "default",
    "GPT_Agent_Excel_Plan_Enhanced_ANTI_DUBLES_FIXED.txt",
    "_reworked_prompts_index",
    "_universal_enrich_5_prompt_blueprint",
    "reworked_default_blocks_v2",
    "reworked_gpt_agent_excel_plan_enhanced_anti_dubles_fixed_txt_blocks_v2",
  ],
};

export function enrichPromptFileNames(stepCode: string): string[] {
  return ENRICH_PROMPT_FILES[stepCode] ?? [];
}

/** Короткий заголовок чипа V-menu для файла промта. */
export function enrichPromptChipTitle(fileName: string): string {
  const base = fileName.replace(/\.txt$/i, "").replace(/^_+/, "");
  if (base === "default") return "default";
  if (base.length <= 28) return base;
  return `${base.slice(0, 25)}…`;
}

export function enrichPromptSlotId(fileName: string): string {
  return `pf_${fileName.replace(/[^a-zA-Z0-9а-яА-ЯёЁ_-]+/g, "_").slice(0, 48)}`;
}
