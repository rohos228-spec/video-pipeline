# Индекс переработанных промтов

Нода: `split`
Активный шаблон: `prompts/steps/03_razbivka/template.md`
Blueprint: `prompts/03_razbivka/_universal_split_prompt_blueprint.md`
Пресеты UI/API: `prompts/step-presets/split.json`

## Переработанные варианты

| Исходник | Reworked файл | Пресет | Полный исходник |
|---|---|---|---|
| `default.md` | `reworked_default_blocks_v2.md` | `default` | `prompts/blocks/split_source_full/default_full.md` |
| `norm.md` | `reworked_norm_blocks_v2.md` | `norm` | `prompts/blocks/split_source_full/norm_full.md` |

## Блоки

- `split_role` → `voiceover_segmenter`
- `split_rules` → `microthought_cells`
- `forbidden_phrases` → `ai_cliches_ru`
- `split_output_contract` → `xlsx_row49`
- `split_self_check` → `no_broken_words_gate`
