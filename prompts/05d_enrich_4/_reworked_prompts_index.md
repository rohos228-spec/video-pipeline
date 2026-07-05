# Индекс переработанных промтов

Нода: `enrich_4`
Активный шаблон: `prompts/steps/05d_enrich_4/template.md`
Blueprint: `prompts/05d_enrich_4/_universal_enrich_4_prompt_blueprint.md`
Пресеты UI/API: `prompts/step-presets/enrich_4.json`

## Переработанные варианты

| Исходник | Reworked файл | Пресет | Полный исходник |
|---|---|---|---|
| `default.md` | `reworked_default_blocks_v2.md` | `default` | `prompts/blocks/enrich_source_full/default_full.md` |

## Блоки

- `enrich_role` → `xlsx_editor`
- `enrich_edit_rules` → `sheet_safe_edits`
- `enrich_source_policy` → `xlsx_task_only`
- `enrich_output_contract` → `return_full_xlsx`
- `enrich_self_check` → `no_structure_damage_gate`
