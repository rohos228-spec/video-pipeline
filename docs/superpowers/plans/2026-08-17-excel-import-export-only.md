# Excel Import/Export Only — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite the only runtime SoT; `project.xlsx` changes only via explicit Export / Import buttons (no mid-pipeline Excel writes or auto-sync).

**Architecture:** Facade APIs `export_project_xlsx` / `import_project_xlsx`; default `apply_ops(export_xlsx=False)`; remove Excel writers and auto Excel→DB from orchestrator; switch readers to Frame/DB; guard with tests mapped to checklist items #1–#30 in the design spec.

**Tech Stack:** Python 3.11/3.12, SQLAlchemy/aiosqlite, FastAPI Studio, openpyxl (export/import only), pytest.

**Spec:** [`docs/superpowers/specs/2026-08-17-excel-import-export-only-design.md`](../specs/2026-08-17-excel-import-export-only-design.md)

## Global Constraints

- Excel runtime write forbidden except `export_project_xlsx`.
- Excel→DB forbidden except explicit `import_project_xlsx` (user/API Import).
- Timestamps/VO/prompts: DB only during pipeline (T1).
- Do not push to a branch other than `ORCHESTRATOR_GIT_BRANCH` from `.env`.
- Commit only when the user asks (local commits in tasks are optional gates).

## File map

| File | Responsibility |
|------|----------------|
| `app/services/db_apply.py` | `apply_ops` default `export_xlsx=False`; keep `export_project_xlsx` |
| `app/services/excel_io.py` (new) | Thin facade: `import_project_xlsx` wraps sync; re-export export |
| `app/services/project_steps.py` | Remove start_step sync / R48 sync / img bootstrap |
| `app/orchestrator/steps/*` | No Excel write; DB readers |
| `app/services/plan_timestamps.py` | ASR → Frame only |
| `app/services/xlsx_text_writeback.py` / gpt clients | Reject writeback |
| `web` baza workspace | No auto-export |
| `tests/test_excel_io_guards.py` (new) | Checklist #27–#29 |
| `docs/DB_V2.md`, `AGENT_MAP.md` | Contract update |

---

### Task 0: Mark Phase 5 checklist + facade stub

**Files:**
- Modify: `docs/superpowers/specs/2026-08-17-excel-import-export-only-design.md` ( статусы по мере закрытия)
- Create: `app/services/excel_io.py`
- Test: `tests/test_excel_io_guards.py`

**Interfaces:**
- Produces: `async def import_project_xlsx(session, project, path: Path | None = None) -> dict` ; `export_project_xlsx` re-export from `db_apply`

- [ ] **Step 1: Create facade**

```python
# app/services/excel_io.py
"""Единственные разрешённые Excel I/O точки: import / export."""
from __future__ import annotations
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Project
from app.services.db_apply import export_project_xlsx

async def import_project_xlsx(
    session: AsyncSession,
    project: Project,
    path: Path | None = None,
) -> dict:
    from app.services.chatgpt_xlsx import sync_project_xlsx
    xlsx = path or (project.data_dir / "project.xlsx")
    return await sync_project_xlsx(session, project, xlsx, keep_fields=False)

__all__ = ["export_project_xlsx", "import_project_xlsx"]
```

- [ ] **Step 2: Add failing guard test (no export from apply_ops default)**

```python
# tests/test_excel_io_guards.py
import inspect
from app.services import db_apply

def test_apply_ops_default_export_xlsx_false():
    sig = inspect.signature(db_apply.apply_ops)
    assert sig.parameters["export_xlsx"].default is False
```

- [ ] **Step 3: Run test — expect FAIL while default is True**

Run: `pytest tests/test_excel_io_guards.py::test_apply_ops_default_export_xlsx_false -v`

- [ ] **Step 4: Do not flip default yet** (Task 1). Keep test red until Task 1.

---

### Task 1: Phase 1 — stop write-through (`apply_ops` + callers)

**Files:**
- Modify: `app/services/db_apply.py` (`export_xlsx: bool = False`)
- Modify callers that pass `export_xlsx=True`:  
  `make_animation_prompts.py`, `generate_image_prompts.py`, `make_plan.py`, `make_script.py`, `split_frames.py`, `enrich_xlsx.py`, `scene_design/apply.py`, `vision_check_loop.py`, etc. (grep `export_xlsx=True`)
- Modify: baza UI auto-export (`web` — search `dbExportXlsx` / `export-xlsx` after save)
- Test: `tests/test_excel_io_guards.py`

**Interfaces:**
- Consumes: Task 0 test
- Produces: apply_ops never touches xlsx unless caller explicitly exports via `excel_io.export_project_xlsx`

- [ ] **Step 1: Grep all `export_xlsx=True` and list call sites**

Run: `rg -n "export_xlsx\\s*=\\s*True" app web`

- [ ] **Step 2: Change default in `apply_ops` to `False`; remove `True` from orchestrator/scene_design/vision callers**

- [ ] **Step 3: Baza UI — remove automatic export after patch; keep button that calls `POST .../export-xlsx`**

- [ ] **Step 4: Extend test — apply_ops does not change mtime**

```python
import asyncio
from pathlib import Path
# use existing project fixture pattern from tests/test_db_apply* if present
async def test_apply_ops_does_not_write_xlsx(tmp_path, ...):
    xlsx = ...  # copy minimal xlsx
    mtime_before = xlsx.stat().st_mtime
    await apply_ops(session, project, ops=[...], export_xlsx=False)  # default
    assert xlsx.stat().st_mtime == mtime_before
```

- [ ] **Step 5: Run** `pytest tests/test_excel_io_guards.py -v` → PASS  
  Mark checklist #1 #2 → `closed` in spec.

---

### Task 2: Phase 1 — kill mid-pipeline Excel writers

**Files:**
- Modify: `app/orchestrator/steps/generate_audio.py` — remove `write_asr_timestamps_to_r15`; ensure Frame ts updated
- Modify: `app/orchestrator/steps/assemble.py` — remove `write_plan_durations`
- Modify: `app/services/montage_board_regen.py` — DB persist only, no `write_plan_*`
- Modify: `app/services/animation_prompt_gpt.py` — `save_animation_prompt_shot2` attrs/DB only
- Modify: `app/services/gpt_client.py`, `gpt_operator_client.py` — do not call `writeback_project_xlsx`; raise/log clear error
- Modify: `app/services/sheet_cell_edit.py` — gate: only used from Import UI path OR deprecate endpoint
- Modify: `app/services/reset_step.py` — stop `clear_plan_*` / avoid Excel wipe as SoT (DB clear only; Excel updated on next Export)
- Grep: `write_plan_`, `writeback_project_xlsx`, `merge_gpt_`, `write_frame(`, `write_general(`

**Interfaces:**
- Produces: ASR timestamps on `Frame.start_ts`/`end_ts` only

- [ ] **Step 1: Confirm audio already writes Frame timestamps; if not, add before removing R15 write**

- [ ] **Step 2: Remove R15/R50/R45–R64 write call sites listed in checklist #3–#12**

- [ ] **Step 3: GPT writeback → raise `RuntimeError("Excel writeback disabled; use apply-ops")` or return structured reject**

- [ ] **Step 4: Test**

```python
def test_writeback_project_xlsx_disabled():
    with pytest.raises(Exception):  # specific type once chosen
        writeback_project_xlsx(...)  # or assert gpt path rejects
```

- [ ] **Step 5: Mark checklist #3–#12 closed when callers gone** (`rg` shows only `export_project_xlsx` / tests / import).

---

### Task 3: Phase 2 — kill auto Excel→DB

**Files:**
- Modify: `app/services/project_steps.py` — remove `sync_project_xlsx`, `sync_animation_prompts_from_xlsx`, `bootstrap_frames_for_image_step`
- Modify: `app/services/step_failure_policy.py` — remove R48 sync on soft-retry
- Modify: `app/orchestrator/steps/generate_images.py` — remove bootstrap/apply from xlsx; fail with clear message if no DB prompts
- Modify: `app/main.py` — remove startup `sync_project_xlsx` backfill (or gate behind never-auto)
- Modify: `app/services/xlsx_step_runners.py` — `sync_after_*` no-op or delete call path
- Modify: `app/services/reset_step.py` — no `sync_animation_prompts_from_xlsx`
- Modify: `app/web/routers/project_ops.py` — keep sync only on explicit Import/reload endpoints; document
- Wire Import button → `excel_io.import_project_xlsx`

**Interfaces:**
- Consumes: `import_project_xlsx`
- Produces: start_step never imports

- [ ] **Step 1: Write test**

```python
def test_start_step_does_not_call_sync(monkeypatch):
    called = []
    monkeypatch.setattr("app.services.chatgpt_xlsx.sync_project_xlsx", lambda *a, **k: called.append(1))
    # invoke start_step minimal path or assert project_steps source has no sync call via importlib inspection
    import app.services.project_steps as ps
    src = inspect.getsource(ps)
    assert "sync_project_xlsx" not in src
    assert "sync_animation_prompts_from_xlsx" not in src
```

- [ ] **Step 2: Remove hooks; img without prompts raises**

Message example: `"Нет image_prompt в БД. Сделай img_pr или Импорт Excel."`

- [ ] **Step 3: `pytest tests/test_excel_io_guards.py tests/test_project_steps*.py -q`**

- [ ] **Step 4: Mark checklist #13–#20 closed**

---

### Task 4: Phase 3 — readers on DB only

**Files:**
- Modify: `app/orchestrator/steps/generate_audio.py` — VO from Frame / frame_texts
- Modify: `app/orchestrator/steps/make_animation_prompts.py` — remove R49 hydrate as SoT (optional fill only if Frame empty **and** never from xlsx — prefer fail)
- Modify: `app/services/montage_board*.py` — remove Excel prompt fallback
- Modify: `app/services/plan_timestamps.py` — readers prefer Frame; Excel read only inside `export`/`import`
- Modify: `app/services/mass_factory.py` — readiness from DB not R49 scan

- [ ] **Step 1: Grep readers**

Run: `rg -n "read_plan_voiceover|ROW_VOICEOVER|R49|read_plan_animation|write_plan_|load_workbook" app/orchestrator app/services/montage_board.py app/services/montage_board_regen.py`

- [ ] **Step 2: Replace each hit with DB access or delete fallback**

- [ ] **Step 3: Targeted pytest for audio/anim_pr/montage that use Frame fixtures without xlsx**

- [ ] **Step 4: Mark checklist #21–#26 closed**

---

### Task 5: Phase 4 — guards + docs

**Files:**
- Modify: `tests/test_excel_io_guards.py` — full #27–#29
- Modify: `docs/DB_V2.md`, `docs/AGENT_MAP.md`, `docs/PROMPT_CONTRACT.md`
- Modify: `.cursor/rules/video-pipeline-ops.mdc` — R48 mirror only after Export

- [ ] **Step 1: Add AST/grep test that `app/orchestrator/steps` does not call forbidden names**

```python
FORBIDDEN = ("write_plan_", "writeback_project_xlsx", "write_asr_timestamps_to_r15", "sync_project_xlsx")
def test_orchestrator_steps_no_forbidden_excel_io():
    root = Path("app/orchestrator/steps")
    for p in root.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for bad in FORBIDDEN:
            assert bad not in text, f"{p} contains {bad}"
```

(Adjust if a comment must mention the name — then use import-level checks.)

- [ ] **Step 2: Update docs per design §7**

- [ ] **Step 3: Mark checklist #27–#30; #30 = manual smoke note in spec**

- [ ] **Step 4: Run** `pytest tests/test_excel_io_guards.py tests/test_db_apply.py tests/test_scene_design_agents.py -q` (plus any touched)

---

### Task 6: Manual smoke (#30)

- [ ] **Step 1:** Studio: проект с DB промтами. Запустить любой шаг — `project.xlsx` mtime unchanged.
- [ ] **Step 2:** База: правка VO — файл unchanged; Экспорт — R49 mirrors; Импорт с изменённого файла — DB updates.
- [ ] **Step 3:** Mark #30 closed in spec.

---

## Execution order

0 → 1 → 2 → 3 → 4 → 5 → 6  

Do not start Task 3 until Task 1–2 green (otherwise img/audio may read stale Excel while writers already gone).
