
# Reworked: default.md → Blocks v2

## Назначение
Структурированная версия legacy-промта ноды `plan` без потери исходных данных.

## Активный шаблон
`prompts/steps/01_plan/template.md`

## Пресет
`default` в `prompts/step-presets/plan.json`

## Полный исходник
`prompts/blocks/plan_source_full/default_full.md`

## Переработанный промт

```md
# Шаг 1 — Общий план ролика

## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ
- Откуда читаю: тема ролика (из чата) + приложенный `project.xlsx` (текущий, ещё пустой лист «план»).
- Куда пишу: возвращаю **тот же файл** `project.xlsx` целиком, с заполненным листом «план» (не переименовывать лист, не менять структуру таблицы).
- Внимание: план должен быть развёрнутым текстом (после синка с ботом — не короче ~200 символов) и содержать явные тайминги по блокам; ответ обязателен с вложением .xlsx.

## 2. РОЛЬ И ЗАДАЧА
{{BLOCK:plan_role}}

## 3. ТЕМА
{{VAR:PROJECT_TOPIC}}

## 4. ДРАМАТУРГИЯ
{{BLOCK:plan_structure}}

## 5. ТОН
{{BLOCK:plan_voice_tone}}

## 6. ЗАПРЕТЫ
{{BLOCK:forbidden_phrases}}

## 7. ФОРМАТ И САМОПРОВЕРКА
{{BLOCK:plan_output_contract}}

{{BLOCK:plan_self_check}}
```

## Что вынесено в блоки
- `plan_role` → `shorts_planner`
- `plan_structure` → `viral_60s_timeline`
- `plan_voice_tone` → `human_clear_pitch`
- `forbidden_phrases` → `ai_cliches_ru`
- `plan_output_contract` → `xlsx_plan_timing`
- `plan_self_check` → `plan_quality_gate`
