# Autonomous audit report — Studio / Web

**Machine:** `C:\Users\Love Space\video-pipeline`  
**Branch:** `devin/windows-installer`  
**Date:** 2026-05-27  
**Scope:** `web/src/**`, `app/web/**`, orchestrator fixes already merged (video skip, `project_state`), Guardian scripts, tests.

**Red line (not modified in this pass):** `app/bots/outsee.py`, `elevenlabs.py`, `app/orchestrator/steps/generate_*.py`.

---

## Executive summary

| Area | Result |
|------|--------|
| Studio API audit (`run-studio-audit.ps1`) | **All checks passed** (after backend restart) |
| Playwright e2e | **6 passed** |
| Web-focused pytest | **12 passed** (`test_web_*`, `test_studio_version`, `test_web_dry_run_step`) |
| Full `pytest tests/` | **202 passed, 10 failed** (pre-existing, mostly orchestrator/outsee) |
| Production web build | **OK** (`npm run build` → `web/out`) |

---

## Bugs found and fixed (this session)

### 1. `GET /api/projects/steps/catalog` → 500

**Cause:** `list_step_codes()` used `st.label`; `StepDef` has `title`, not `label`.  
**Fix:** `app/services/project_steps.py` — `"label": st.title`.  
**Test:** `tests/test_web_api_integration.py::test_steps_catalog`.

### 2. `patch-toast-errors.py` broke build

**Cause:** Replacement for `` toast.error(`Ошибка: ${String(e)}`) `` dropped closing `)`.  
**Fix:** `hitl-banner.tsx` + script `NEW2` now includes `)`.  
**Lesson:** run `npm run build` after mass patch.

### 3. (Earlier in conversation) Outsee double F1 after stop

**Cause:** Skip required `status == video_generated` even when clip existed.  
**Fix:** `generate_videos.py`, `artifact_recovery.py` — skip when file on disk; status only after download.

### 4. (Earlier) UI crashes / disabled controls

| Bug | Fix |
|-----|-----|
| `project!.id` when project null | `node-result-resolver.ts` |
| «Перезапустить» disabled with V-menu open | `studio-workspace.tsx` `effectiveNodeKey` |
| Nodes flash «Ожидание» on run poll | `node-run-status.ts` `inferNodeStatusFromProject` |
| `frames_ready` with videos + `no_hero` | `project_state.py` + `GET /api/projects/{id}` recompute |

### 5. Toast `String(e)` / `[object Object]`

**Fix:** `web/src/lib/error-message.ts` + 18 files via `patch-toast-errors.py` (after script fix).

---

## Test inventory

### Guardian

```powershell
cd "C:\Users\Love Space\video-pipeline"
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-studio-audit.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-studio-audit.ps1 -E2E
```

Checks: health, projects, studio-version (`ui_stale`), **steps catalog**, **workflows**, **ensure-run**, dry_run plan, dry_run video forbidden (400), project #15 status vs video assets.

### Playwright (`web/e2e/`)

| Spec | Tests |
|------|-------|
| `studio-smoke.spec.ts` | home, project select, V-menu + run toolbar |
| `studio-projects.spec.ts` | API ↔ sidebar |
| `studio-workflows.spec.ts` | default workflow nodes, catalog labels |

### Pytest (web)

- `tests/test_web_api_integration.py` — catalog, workflows, recompute, ensure-run, dry_run matrix
- `tests/test_web_dry_run_step.py` — 3 tests
- `tests/test_studio_version.py` — studio version endpoint

---

## Full pytest failures (documented, not fixed — red line / out of scope)

| Test file | Count | Notes |
|-----------|-------|-------|
| `test_outsee_url_resolve.py` | 2 | Outsee URL preference logic |
| `test_auto_advance_parity.py` | 6 | Enrich/hero auto-advance parity |
| `test_assembly_sync.py` | 1 | `cut_clip` resize assertion |
| `test_chatgpt_xlsx.py` | 1 | override-only message |

**Total:** 10 failed, 202 passed (~67s).

---

## Outsee logs clarification (not a bug)

On step «Видео», one frame cycle logs **F1 then F2** in sequence — that is one generation attempt inside Outsee, not two full retries without error. Real duplicate generation after ⏹ was the skip/status issue (fixed above).

---

## Static review notes (no change required)

- `flow-canvas.tsx` `defaultWorkflow!.id` in `queryFn` is safe: `enabled: !!defaultWorkflow`.
- `studio-workspace.tsx` `projectId!` in queries guarded by `enabled: projectId != null`.
- Dry-run correctly blocks `hero`, `items`, `img`, `video`, `audio` (`studio_dry_run.py`).

---

## Files touched (audit session)

- `app/services/project_steps.py`
- `web/src/components/hitl/hitl-banner.tsx`
- `scripts/guardian/patch-toast-errors.py`
- `scripts/guardian/run-studio-audit.ps1`
- `web/e2e/studio-workflows.spec.ts`
- `tests/test_web_api_integration.py` (import cleanup)
- `docs/AUTONOMOUS-AUDIT-REPORT.md` (this file)

Plus earlier session: `studio-workspace.tsx`, `node-result-resolver.ts`, `node-run-status.ts`, `project_state.py`, `projects.py`, `generate_videos.py`, `artifact_recovery.py`, toast patches across `web/src`, e2e helpers/specs.

---

## Operator commands

```powershell
cd "C:\Users\Love Space\video-pipeline"

# API smoke
.\STUDIO-AUDIT.cmd
# or
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-studio-audit.ps1 -E2E

# Web unit (ASGI)
.\.venv\Scripts\python.exe -m pytest tests/test_web_api_integration.py tests/test_web_dry_run_step.py tests/test_studio_version.py -q

# Rebuild + restart after web changes
powershell -ExecutionPolicy Bypass -File .\apply-local.ps1 -SkipBuild -NoBrowser
```

Logs: `data\backend.log`, `data\backend-<pid>.log`.

---

## Remaining risks / follow-ups

1. **10 failing pytest** — need product decision on enrich/hero parity and Outsee URL tests.
2. **HITL / long-run flows** — no automated e2e for approve/regenerate (needs stable test project + mock).
3. **Branch sync** — merge this tree to `C:\Users\AiCreator\Desktop\video-pipeline` per `docs/HANDOFF-STUDIO-WEB.md`.
4. User-reported UI issues without repro steps — still need checklist (screen, action, expected vs actual, Network).

---

## Autonomy note

Work completed without user prompts: tests, fixes, backend restart, build, expanded audit, e2e, and this report.

**This was NOT a full 9-hour verification.** For button-by-button, every node, full pipeline, and all generation types, use **`docs/FULL-VERIFICATION.md`** (6–9 h checklist).
