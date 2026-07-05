# Индекс переработанных script-промтов

Этот индекс показывает, как исходные файлы из `prompts/02_script/` разложены на Blocks v2 и какие новые файлы использовать.

## Активный шаблон

```text
prompts/steps/02_script/template.md
```

Это основной рабочий шаблон для Blocks v2.

## Blueprint для создания новых вариантов

```text
prompts/02_script/_universal_script_prompt_blueprint.md
```

## Переработанные варианты

| Исходный файл | Переработанный файл | Назначение |
|---|---|---|
| `scenario_agent.md` | `reworked_scenario_agent_blocks_v2.md` | Основной pipeline-ready вариант 60 сек |
| `default.md` | `reworked_default_60s_blocks_v2.md` | Компактный дефолт 800–900 символов |
| `zakadrovyuTekst_long_story.md` | `reworked_zakadrovyu_long_cells_blocks_v2.md` | Long-form 10000–10500 символов, ячейки 110–140 |
| `Новый промт 12.05.md` | `reworked_new_prompt_universal_editor_blocks_v2.md` | Универсальный редакторский вариант по типам материала |
| `У.Зинзер 12.05.md` | `reworked_zinser_filter_blocks_v2.md` | Усиленный анти-GPT / редакторский фильтр |
| `promt_stiven_king.md` | `reworked_stephen_king_placeholder_blocks_v2.md` | Placeholder-заготовка, исходник не содержал правил |

## Библиотека новых блоков

| Категория | Блок |
|---|---|
| `script_role` | `voiceover_author` |
| `source_policy` | `xlsx_general_plan_only` |
| `script_mode_selector` | `universal_modes` |
| `script_domain_skills` | `biography_history_science_process_object` |
| `script_narrative_structure` | `short_voiceover_arc` |
| `script_continuity_rules` | `smooth_voiceover_flow` |
| `script_voice_tone` | `human_documentary_voice` |
| `script_anti_gpt_patterns` | `zinser_filter` |
| `script_output_contract` | `voiceover_txt_60s`, `long_cells_txt_10000` |
| `script_self_check` | `voiceover_quality_gate` |
| `script_segmentation_rules` | `long_cells_110_140` |
| `script_source_full` | `scenario_agent_full`, `default_full`, `long_story_full`, `universal_editor_full`, `zinser_full`, `stephen_king_placeholder_full` |

## Почему раньше казалось, что разобран один промт

Потому что первым шагом был создан один канонический активный шаблон `steps/02_script/template.md`, а содержимое шести исходников было сжато в общие блоки.

Теперь дополнительно сохранены все шесть переработанных вариантов отдельными файлами `reworked_*.md`.

## Где лежат полные исходники как блоки

Чтобы не терять полный объём правил, исходные промты также скопированы целиком в отдельную категорию:

```text
prompts/blocks/script_source_full/scenario_agent_full.md
prompts/blocks/script_source_full/default_full.md
prompts/blocks/script_source_full/long_story_full.md
prompts/blocks/script_source_full/universal_editor_full.md
prompts/blocks/script_source_full/zinser_full.md
prompts/blocks/script_source_full/stephen_king_placeholder_full.md
```

Это не короткие выжимки, а полные тексты исходников.
