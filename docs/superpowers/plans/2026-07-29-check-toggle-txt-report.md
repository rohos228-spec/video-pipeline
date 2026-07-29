# Check Toggle + TXT Report — Implementation Plan

> **For agentic workers:** implement task-by-task. Checkboxes for tracking.

**Goal:** Тумблер «Проверка» на excel_gpt: промты со стрелок, всегда `check_report.txt`, Чинить/Только отчёт, verdict для Ок/Не ок.

**Architecture:** `checkMode`/`checkFix` в `meta.excel_gpt_nodes`; resolve собирает `sourcePrompts`; run подменяет master на промты источников + TXT footer; client пишет `check_report.txt` (+ analysis.json из парсера); emit/`gateStatus` как сейчас.

**Tech Stack:** Python 3.11, FastAPI, React/Next Studio UI, pytest.

## Global Constraints

- Deliverable отчёта — `.txt`, не JSON-first для пользователя.
- JSON `vp.check.v1` — только fallback → конвертация в txt.
- Без новой ноды в палитре.
- Push в `main` после готовности; bump `STUDIO_VERSION` при UI.

---

## Task 1: TXT report module

- [ ] Парсер/рендер `check_report.txt` + footer в `check_analysis.py`
- [ ] Тесты parse txt / JSON→txt / verdict
- [ ] Commit

## Task 2: Operator meta + resolve

- [ ] `checkMode`, `checkFix`, `collect_source_prompts`, resolve/canRun, branching, reply name
- [ ] `gate_allows_successors` учитывает checkMode
- [ ] Тесты resolve
- [ ] Commit

## Task 3: Run path

- [ ] `enrich_xlsx` + `gpt_operator_client`: сбор промтов, footer, запись отчёта, ignore rewrite при report_only
- [ ] Тесты stub API
- [ ] Commit

## Task 4: UI + ship

- [ ] Тумблеры в V-меню и settings; types; bump Studio; push `main`
