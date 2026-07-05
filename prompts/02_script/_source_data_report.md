# Source Data Report for 02_script prompts

Ты спросил правильно: новые Blocks v2 файлы были слишком сильно сжаты. Этот отчёт фиксирует, где лежат полные исходники и сколько данных было в каждом.

## Полные исходники не удалены

Все исходные файлы остались на месте:

| Файл | Примерный объём | Статус |
|---|---:|---|
| `scenario_agent.md` | ~6.5k символов | сохранён полностью |
| `zakadrovyuTekst_long_story.md` | ~6.9k символов | сохранён полностью |
| `Новый промт 12.05.md` | ~33k символов | сохранён полностью |
| `У.Зинзер 12.05.md` | ~22.8k символов | сохранён полностью |
| `default.md` | ~5.2k символов | сохранён полностью |
| `promt_stiven_king.md` | ~0.4k символов | placeholder, полезных правил почти нет |

## Что было сделано сначала

Первый проход сделал короткие Blocks v2 выжимки:

| Блок | Примерный объём |
|---|---:|
| `script_anti_gpt_patterns/zinser_filter.md` | ~1.5k |
| `script_continuity_rules/smooth_voiceover_flow.md` | ~0.9k |
| `script_domain_skills/biography_history_science_process_object.md` | ~1.1k |
| `script_mode_selector/universal_modes.md` | ~1.7k |
| `script_narrative_structure/short_voiceover_arc.md` | ~1.0k |
| `script_voice_tone/human_documentary_voice.md` | ~0.8k |

Это удобно для UI, но это не полная переноска исходных промтов.

## Правильная схема хранения

Теперь нужно различать два слоя:

1. **Compact Blocks v2** — короткие управляемые блоки для Studio UI.
2. **Full Source Layer** — полные исходные правила и большие редакторские кодексы, которые нельзя терять.

## Где смотреть «сами скрипты»

Полные исходники:

```text
prompts/02_script/scenario_agent.md
prompts/02_script/zakadrovyuTekst_long_story.md
prompts/02_script/Новый промт 12.05.md
prompts/02_script/У.Зинзер 12.05.md
prompts/02_script/default.md
prompts/02_script/promt_stiven_king.md
```

Переработанные варианты:

```text
prompts/02_script/reworked_scenario_agent_blocks_v2.md
prompts/02_script/reworked_default_60s_blocks_v2.md
prompts/02_script/reworked_zakadrovyu_long_cells_blocks_v2.md
prompts/02_script/reworked_new_prompt_universal_editor_blocks_v2.md
prompts/02_script/reworked_zinser_filter_blocks_v2.md
prompts/02_script/reworked_stephen_king_placeholder_blocks_v2.md
```

Активный шаблон:

```text
prompts/steps/02_script/template.md
```

## Что нужно сделать дальше

Чтобы перенос был не сжатым, а полноценным, созданы full-блоки:

```text
prompts/blocks/script_source_full/scenario_agent_full.md
prompts/blocks/script_source_full/long_story_full.md
prompts/blocks/script_source_full/universal_editor_full.md
prompts/blocks/script_source_full/zinser_full.md
prompts/blocks/script_source_full/default_full.md
prompts/blocks/script_source_full/stephen_king_placeholder_full.md
```

И затем решить, какие из них включать в активную сборку:

- короткая ежедневная сборка — compact blocks;
- максимально строгая сборка — compact blocks + `zinser_full`;
- long-form сборка — compact blocks + `long_story_full`;
- универсальный редакторский режим — compact blocks + `universal_editor_full`.

## Важно

Категория `script_source_full` добавлена в дефолты `prompt_composer.py`, но активный `prompts/steps/02_script/template.md` пока использует compact blocks.

Это сделано специально, чтобы ежедневный 60-секундный промт не раздулся до десятков тысяч символов. Если нужен режим «включить весь Зинсер» или «включить весь long-story», нужно добавить в нужный вариант шаблона:

```md
{{BLOCK:script_source_full}}
```

и выбрать соответствующий блок: `zinser_full`, `long_story_full`, `universal_editor_full` и т.д.

