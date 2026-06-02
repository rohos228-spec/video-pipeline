# QA FINAL REPORT

**Дата:** 2026-06-02  
**Репозиторий:** `C:\Users\Love Space\video-pipeline`  
**Полный лог:** `data\qa-final-run.log`

---

## Итог

| Блок | Результат |
|------|-----------|
| Guardian API audit | **PASS** |
| Web pytest (12) | **PASS** |
| API matrix (44) | **PASS** |
| Playwright e2e (11) | **PASS** |
| pytest tests/ (212) | **202 pass / 10 fail** (outsee, auto_advance, chatgpt_xlsx — вне web) |
| QA-SMOKE #17 | **assembled** — GPT-цепочка уже была пройдена |
| QA-FULL #18 | **hero_ready** — script+split OK; **img_pr/img/enrich — HTTP 400** (граф/предусловия) |
| Проект #15 | **assembled**, 10 videos — регрессия OK |

**Общий вердикт Studio UI/API:** работает.  
**Блокер полного bot-пайплайна на #18:** шаги `enrich_*`, `img_pr`, `img` отклоняются API 400 при `hero_ready` (нужна проверка графа workflow / отключённых нод / enrich_ready вручную в Studio).

---

## Автоматизация (прогнано)

- `scripts/guardian/run-studio-audit.ps1`
- `tests/test_web_api_integration.py` + dry_run + studio_version
- `scripts/guardian/run-full-verification.py --skip-live`
- `web` → `npm run test:e2e` (11 тестов)
- `pytest tests/` полный набор

Повтор одной командой:

```powershell
cd "C:\Users\Love Space\video-pipeline"
.\.venv\Scripts\python.exe scripts\guardian\run-final-qa.py
```

---

## Live pipeline

### #17 qa-smoke (no_hero)

Статус: **assembled**. Все GPT-шаги пропущены — уже выполнены ранее.

### #18 qa-full (hero)

| Шаг | Результат |
|-----|-----------|
| plan | уже plan_ready |
| script | **OK** → script_ready |
| split | **OK** → hero_ready |
| enrich_1..3 | **400** |
| img_pr, anim_pr | **400** |
| hero | уже hero_ready |
| img | **400**, цепочка остановлена |

Текущий статус API: `hero_ready`, `enrich_slots_count=3`.

---

## Известные pytest fail (не чинились)

- `test_outsee_url_resolve.py` (2)
- `test_auto_advance_parity.py` (6)
- `test_assembly_sync.py` (1)
- `test_chatgpt_xlsx.py` (1)

---

## Файлы

- `docs/QA-RUN-API-2026-06-02.json`
- `docs/QA-RUN-FULL-2026-06-02.md`
- `docs/FULL-VERIFICATION.md` — чеклист на ручной Outsee/HITL
