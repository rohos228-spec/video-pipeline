# Индекс переработанных промтов

Нода: `enrich_1`
Активный шаблон: `prompts/steps/05a_enrich_1/template.md`
Blueprint: `prompts/05a_enrich_1/_universal_enrich_1_prompt_blueprint.md`
Пресеты UI/API: `prompts/step-presets/enrich_1.json`

## Переработанные варианты

| Исходник | Reworked файл | Пресет | Полный исходник |
|---|---|---|---|
| `default.md` | `reworked_default_blocks_v2.md` | `default` | `prompts/blocks/enrich_source_full/default_full.md` |
| `XLSX_Agent_V7_2_Row52_Strengthened.txt.md` | `reworked_xlsx_agent_v7_2_row52_strengthened_txt_blocks_v2.md` | `xlsx_agent_v7_2_row52_strengthened_txt` | `prompts/blocks/enrich_source_full/xlsx_agent_v7_2_row52_strengthened_txt_full.md` |
| `заполнение таблицы.md` | `reworked_zapolnenie_tablicy_blocks_v2.md` | `zapolnenie_tablicy` | `prompts/blocks/enrich_source_full/zapolnenie_tablicy_full.md` |

## Блоки

- `enrich_role` → `xlsx_editor`
- `enrich_edit_rules` → `sheet_safe_edits`
- `enrich_source_policy` → `xlsx_task_only`
- `enrich_output_contract` → `return_full_xlsx`
- `enrich_self_check` → `no_structure_damage_gate`
