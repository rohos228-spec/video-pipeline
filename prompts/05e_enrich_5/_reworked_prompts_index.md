# Индекс переработанных промтов

Нода: `enrich_5`
Активный шаблон: `prompts/steps/05e_enrich_5/template.md`
Blueprint: `prompts/05e_enrich_5/_universal_enrich_5_prompt_blueprint.md`
Пресеты UI/API: `prompts/step-presets/enrich_5.json`

## Переработанные варианты

| Исходник | Reworked файл | Пресет | Полный исходник |
|---|---|---|---|
| `default.md` | `reworked_default_blocks_v2.md` | `default` | `prompts/blocks/enrich_source_full/default_full.md` |
| `GPT_Agent_Excel_Plan_Enhanced_ANTI_DUBLES_FIXED.txt.md` | `reworked_gpt_agent_excel_plan_enhanced_anti_dubles_fixed_txt_blocks_v2.md` | `gpt_agent_excel_plan_enhanced_anti_dubles_fixed_txt` | `prompts/blocks/enrich_source_full/gpt_agent_excel_plan_enhanced_anti_dubles_fixed_txt_full.md` |

## Блоки

- `enrich_role` → `xlsx_editor`
- `enrich_edit_rules` → `sheet_safe_edits`
- `enrich_source_policy` → `xlsx_task_only`
- `enrich_output_contract` → `return_full_xlsx`
- `enrich_self_check` → `no_structure_damage_gate`
