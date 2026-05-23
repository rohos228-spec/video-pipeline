# Компонентные промты (blocks v2)

## Структура

```
prompts/
  _vars.md
  blocks/<category>/<name>.md
  steps/<step_id>/template.md
  styles/<preset>.json
```

## Сборка

`app/services/prompt_composer.py` подставляет `{{BLOCK:category}}` и `{{VAR:NAME}}`.

В проекте (`prompt_overrides`):

```json
{
  "use_blocks_v2": true,
  "style_profile": "cats_pixelart_short",
  "blocks": { "world": "cats_anthropomorphic", ... },
  "vars": { "VIDEO_DURATION_SEC": 60 }
}
```

## API

- `GET /api/prompt-studio/catalog`
- `POST /api/prompt-studio/compose`
- `PATCH /api/prompt-studio/projects/{id}/prompt-config`

## UI

Студия ноды (правая панель) → вкладки Настройки / Промты GPT / Результаты.
