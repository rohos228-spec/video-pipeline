---
name: safe-step-reset
description: Safely reset or recover pipeline steps (img/video/anim_pr) without destructive wipes. Use when resetting a step, recovering after CDP timeout, or cleaning failed generations.
---

# Safe step reset

## Policy

- CDP timeout on `img` / `video` / `anim_pr` → **soft retry**, not wipe.
- `reset_step` for `img` must keep PNG backup under `old/scenes/<timestamp>/` (not silent delete-all).
- Prefer re-run of the failed step over deleting project media.

## Workflow

1. Identify step code and project id.
2. Check logs for timeout vs real content failure.
3. If soft-retry eligible: re-run step (Studio button or `POST /api/projects/{id}/steps/{code}/run`).
4. If reset is required for `img`: verify backup path exists after reset; never invent a wipe-all shortcut.
5. For anim_pr: sync from xlsx R48 before deciding what is “missing”.
6. Report what was retried/reset and what media was preserved.

## Never

- Do not use `scripts/legacy/` reset/update helpers
- Do not `rm -rf data/videos/.../scenes` as a fix
- Do not clear SQLite (`data/state.db`) unless the user explicitly requested a full reset
---