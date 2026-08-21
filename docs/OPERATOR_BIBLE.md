# Операторская библия (коротко)

Шпаргалка для человека. Детали для агента — [`AGENT_MAP.md`](AGENT_MAP.md).

## Studio (http://127.0.0.1:8765)

| Нужно | Куда |
|-------|------|
| Запуск | `STUDIO.cmd` → пункт **[1]** |
| Остановить бэкенд | `STUDIO.cmd` → **[2]** (или `stop-backend.cmd`) |
| Обновление с GitHub | `STUDIO.cmd` → **[4]** (`origin/<ORCHESTRATOR_GIT_BRANCH>`, не всегда main) |
| Новый проект + пресет настроек | кнопка **Новый проект** → сохранить/выбрать конфигурацию |
| Настройки проекта / авто / режим | справа Inspector → **Настройки** |
| Массовые ролики из Excel | Inspector → **Фабрика видео** (Excel тем → очередь) |
| Промпты пайплайна | вкладка / Studio **Промты GPT** (Prompt Builder) |
| Свободный чат с файлами | GPT workspace в Studio (готовые файлы справа/внизу) |
| Create (картинка/видео Outsee/Grsai) | экран **Create** — настройки **общие** на всю Studio |

Остановка бэкенда: `stop-backend.cmd`.

## Где лежат файлы

| Что | Путь |
|-----|------|
| Ключи / провайдеры | `.env` в корне репо |
| Промпты (blocks / steps / legacy) | `prompts/` |
| Пресеты мастера проекта | `data/generation_config_presets.json` |
| Глобальные настройки Create | `data/outsee_create_settings.json` |
| Чаты GPT workspace | `data/gpt_workspace/` |
| БД | `data/state.db` |
| Шаблон short Excel | `templates/project_template_v8.xlsx` |

`data/` локальная (не в git) — переживает обновление кода при `git pull` кода, но не уезжает на GitHub.

## Документы (не читать всё подряд)

| Тема | Файл |
|------|------|
| Как устроены промпт-блоки | [`PROMPTS_BLOCKS.md`](PROMPTS_BLOCKS.md) |
| Массовый режим Telegram `/mass` | [`MASS_CREATION.md`](MASS_CREATION.md) |
| Сериальный пайплайн | `SERIES_*.md` |
| Карта для ИИ-агента | [`AGENT_MAP.md`](AGENT_MAP.md) |
| Модели на нодах (кто какой ключ) | [`NODE_MODELS.md`](NODE_MODELS.md) |

**Не опираться на** `HANDOVER.md` (устарел).

## Два разных «mass»

1. **Studio → Фабрика видео** — Excel тем в проекте, очередь lanes.
2. **Telegram `/mass`** — batch-проекты; см. `MASS_CREATION.md`.

## Поиск по знаниям репо

```text
python scripts/build_knowledge_index.py
```

Потом в браузере или curl:

```text
GET http://127.0.0.1:8765/api/knowledge/search?q=anim_pr
```
