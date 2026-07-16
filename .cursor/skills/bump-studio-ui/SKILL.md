---
name: bump-studio-ui
description: Bump Studio UI version and rebuild committed web/out after web UI changes. Use before any commit that touches web/src, Prompt Builder, or studio frontend assets.
---

# Bump Studio UI

## When

Any commit that changes Studio frontend (`web/`) must bump the version badge and rebuild static output.

## Steps

1. Finish UI code changes under `web/`.
2. From repo root:
   ```bash
   python3 scripts/bump_studio_version.py
   ```
   This bumps `web/STUDIO_VERSION` and rebuilds `web/out/`.
3. Stage both source and baked output (`web/out/` is committed on purpose).
4. Optional checks:
   ```bash
   cd web && npx tsc --noEmit
   ```
5. Confirm badge contract: `/api/studio-version` should not report `ui_stale` after deploy/restart.

## Prompt Builder reminders (if touched)

- Keep 3-column layout: presets / center / catalog
- Drag into empty slot is add-only
- Persist via PATCH step-presets APIs
- Do not invent a parallel preset storage

## Never

- Do not commit web UI changes without running the bump script
- Do not delete `web/out/` from git tracking
---