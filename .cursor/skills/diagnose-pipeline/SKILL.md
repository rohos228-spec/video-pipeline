---
name: diagnose-pipeline
description: Diagnose video-pipeline Studio/backend failures (anim_pr, img, video, attach, CDP). Use when a step hangs, fails, or the user asks to check logs/doctor without guessing.
---

# Diagnose video-pipeline

Do this yourself. Do not ask the user for logs if you can read them.

## Steps

1. `git status` and `git diff` — note dirty files; do not reset user work.
2. Prefer project diagnostics:
   - Windows: `STUDIO.cmd` → [6] (doctor) or read `logs/doctor.log`
   - Tail `data/backend.log` and newest `data/backend-*.log`
3. Identify the failing step code (`anim_pr`, `img`, `video`, ChatGPT attach, etc.).
4. Run targeted tests when relevant:
   ```bash
   python3 -m pytest tests/test_chatgpt_attachment_guard.py tests/test_animation_prompt_gpt.py -q
   ```
5. For anim_pr pause/retry on a known project (often `#13`):
   ```bash
   # example — adjust project id
   curl -X POST http://127.0.0.1:8765/api/projects/13/steps/anim_pr/run
   ```
6. Summarize: root cause, what you checked, exact next fix. Prefer soft retry over wipe for CDP timeouts on `img` / `video` / `anim_pr`.

## Never

- Do not use `scripts/legacy/`
- Do not delete `data/` or wipe scene PNGs unless the user explicitly asked
- Do not read `.env` secrets into the chat
---