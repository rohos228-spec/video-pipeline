# Индекс переработанных image-промтов (img_pr)

Активный шаблон Blocks v2: `prompts/steps/06_image_prompts/template.md`  
Blueprint: `prompts/05_image_prompts/_universal_image_prompt_blueprint.md`  
Пресеты UI: `prompts/step-presets/img_pr.json`

## Переработанные варианты

| Исходник | Reworked файл | Пресет | Тип |
|----------|---------------|--------|-----|
| `default.md` | `reworked_default_cats_pixel_blocks_v2.md` | `default` | pipeline |
| `norm.md` | `reworked_norm_nano_banana_blocks_v2.md` | `norm` | pipeline |
| `генератор…v8.txt` | `reworked_pixel_v8_cinematic_blocks_v2.md` | `pixel_v8` | pipeline |
| `новый промт 11.6 полька дарк` | `reworked_trash_polka_v25_blocks_v2.md` | `trash_polka_v25` | pipeline+discipline |
| `полька кровавая.txt` | `reworked_trash_polka_short_blocks_v2.md` | `trash_polka_short` | style-template |
| `пластилин.txt` | `reworked_plasticine_blocks_v2.md` | `plasticine` | style-template |
| `вязаный 2d стиль.txt` | `reworked_knitted_2d_blocks_v2.md` | `knitted_2d` | style-template |
| `грязный темный кровавый.txt` | `reworked_noir_bloody_blocks_v2.md` | `noir_bloody` | style-template |
| `дарк грязный зловещий.txt` | `reworked_dark_ominous_noir_blocks_v2.md` | `dark_ominous` | style-template |

## Полные исходники (img_source_full)

```text
prompts/blocks/img_source_full/default_full.md
prompts/blocks/img_source_full/norm_full.md
prompts/blocks/img_source_full/pixel_v8_full.md
prompts/blocks/img_source_full/trash_polka_v25_full.md
prompts/blocks/img_source_full/trash_polka_v23_full.md
prompts/blocks/img_source_full/plasticine_full.md
prompts/blocks/img_source_full/knitted_2d_full.md
prompts/blocks/img_source_full/polka_bloody_short_full.md
prompts/blocks/img_source_full/noir_bloody_full.md
prompts/blocks/img_source_full/dark_ominous_noir_full.md
```

## Библиотека блоков по уровням

| Уровень | Категория | Блоки |
|--------|-----------|-------|
| 1 | `img_input_rules` | `one_cell_one_prompt` |
| 1 | `img_scene_interpretation` | `realism_and_abstract_five_ways` |
| 1 | `img_hero_policy` | `hero_reference_strict`, `hero_reference_conditional` |
| 1 | `img_diversity_rules` | `scene_variety` |
| 1 | `img_context_logic` | `source_only_no_invention` |
| 2 | `world` | `cats_strict_all_figures`, `conditional_style_guide`, `cats_anthropomorphic` |
| 2 | `character_anatomy` | `v8_fingers_teeth_clothed`, `anthro_cat_sheet` |
| 3 | `visual_style` | `epic_pixel_cats_default`, `mature_cinematic_pixel_v8`, `trash_polka_noir_v25`, `clay_plasticine_2d`, … |
| 4 | `composition` | `vertical_9_16_character` |
| 4 | `camera_framing` | `medium_full_mix` |
| 4 | `background_density` | `rich_three_plane_environment` |
| 4 | `img_composition_discipline` | `trash_polka_foreground_v25` |
| 5 | `lighting` | `cinematic_chiaroscuro`, `noir_exaggerated`, `soft_diffused_educational`, `cold_moonlit_doc_noir` |
| 6 | `img_prop_text_rules` | `blank_papers_default`, `russian_on_papers_v25` |
| 7 | `negative` | `cats_pixel_default`, `trash_polka_ru_papers`, `clay_plasticine`, … |
| 8 | `img_output_contract` | `xlsx_dash_separated`, `xlsx_numbered_16fields`, `prompt_negative_pairs_v8`, `style_template_prompt_pair` |
| 8 | `img_self_check` | `pre_output_gate` |
