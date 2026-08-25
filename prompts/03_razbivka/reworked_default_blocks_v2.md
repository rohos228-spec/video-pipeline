
# Reworked: default.md → Blocks v2

## Назначение
Структурированная версия legacy-промта ноды `split` без потери исходных данных.

## Активный шаблон
`prompts/steps/03_razbivka/template.md`

## Пресет
`default` в `prompts/step-presets/split.json`

## Полный исходник
`prompts/blocks/split_source_full/default_full.md`

## Переработанный промт

```md
# Шаг 3 — Разбивка закадрового текста на блоки

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: приложенные `project.xlsx` и `voiceover.txt` (закадровый текст из шага 2).
- Куда пишу: обновлённый `project.xlsx`, лист «план» (не переименовывать!), строка 49 «закадровый текст» — каждый блок в отдельную ячейку начиная с колонки C.
- Внимание: одна ячейка = одна законченная микромысль; нельзя переносить слово между ячейками или склеивать несколько мыслей в одну ячейку; ответ обязателен с вложением .xlsx.

## 2. РОЛЬ И ЗАДАЧА
{{BLOCK:split_role}}

## 3. ПРАВИЛА РАЗБИВКИ
{{BLOCK:split_rules}}

## 4. ЗАПРЕТЫ
{{BLOCK:forbidden_phrases}}

## 5. ФОРМАТ ВЫВОДА
{{BLOCK:split_output_contract}}

## 6. САМОПРОВЕРКА
{{BLOCK:split_self_check}}
```

## Что вынесено в блоки
- `split_role` → `voiceover_segmenter`
- `split_rules` → `microthought_cells`
- `forbidden_phrases` → `ai_cliches_ru`
- `split_output_contract` → `xlsx_row49`
- `split_self_check` → `no_broken_words_gate`
