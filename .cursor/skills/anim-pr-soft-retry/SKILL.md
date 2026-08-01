---
name: anim-pr-soft-retry
description: Recover or fix animation prompt (anim_pr) generation using xlsx R48 as source of truth and soft retry without wiping scenes. Use when anim_pr is stuck, paused, missing prompts, or after CDP timeout.
---

# anim_pr soft retry

## Source of truth

- Skip/generate decisions come from **column R48 in project.xlsx**, not stale DB fields.
- Key helpers: `sync_animation_prompts_from_xlsx`, `scan_missing_animation_prompts` in `animation_prompt_gpt.py`.
- Soft retry step codes include `anim_pr`, `img`, `video` (`step_failure_policy._SOFT_RETRY_STEP_CODES`).

## Workflow

1. Confirm project id/slug (often `#13` / `nicshe` in ops notes).
2. Check whether failure is CDP timeout / soft-retry eligible — prefer retry, not wipe.
3. Sync/scan from xlsx before trusting DB skip flags.
4. Clear 30-minute error pause by:
   - Studio UI run button for `anim_pr`, or
   - `POST /api/projects/{id}/steps/anim_pr/run`
5. Run:
   ```bash
   python3 -m pytest tests/test_animation_prompt_gpt.py -q
   ```
6. Tail `data/backend-*.log` for attach/GPT/xlsx sync errors.

## Never

- Do not wipe scene PNGs to “unstick” anim_pr after CDP timeout
- Do not treat DB alone as truth when R48 disagrees
---