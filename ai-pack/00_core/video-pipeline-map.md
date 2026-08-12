# video-pipeline — карта знаний

Перед правкой в незнакомой зоне **открой** [`docs/AGENT_MAP.md`](docs/AGENT_MAP.md)
и перейди по ссылкам к source of truth. Не выдумывай пути и не копируй
устаревшие `HANDOVER.md` / старые README-нарративы.

## Обязательно

1. Неизвестная тема (промпты, оркестратор, GPT-чат, Create, mass, ASR, xlsx) →
   сначала `docs/AGENT_MAP.md`, затем указанные файлы.
2. Операционка (git → `main`, Studio, anim_pr/R48, soft retry, диагностика) →
   [`.cursor/rules/video-pipeline-ops.mdc`](video-pipeline-ops.mdc) + `AGENTS.md`.
   **Не дублируй** ops здесь.
3. Неясный поиск по репо → `python scripts/build_knowledge_index.py` и/или
   `GET /api/knowledge/search?q=…` (после Phase 3), потом читай hit.
4. Промпты blocks / Telegram mass / series — **link only**:
   `docs/PROMPTS_BLOCKS.md`, `docs/MASS_CREATION.md`, `docs/SERIES_*`
   (не переписывать содержимое в чат).

## Быстрые якоря

| Тема | Старт |
|------|--------|
| **БД v2 / кнопка «База»** | `docs/DB_V2.md` + `app/services/db_v2.py` |
| Шаги пайплайна | `app/orchestrator/node_registry.py` |
| Промпты | `docs/PROMPTS_BLOCKS.md` + `app/services/prompt_composer.py` |
| GPT чат Studio | `app/services/gpt_workspace.py` |
| Create / Outsee | `app/web/routers/outsee_create.py`, `app/bots/outsee_http.py` |
| Mass Studio | `app/services/mass_factory.py` |
| Mass Telegram | `docs/MASS_CREATION.md` |
| XLSX short rows | `app/services/xlsx_v8_import.py`, `app/storage/plan_sheet_v8.py` |
| Оператор (человек) | `docs/OPERATOR_BIBLE.md` |
