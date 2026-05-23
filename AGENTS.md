# AGENTS.md

## Cursor Cloud specific instructions

### Overview

**video-pipeline** is a single Python 3.11+ application (no Docker) that automates short video generation (60–75 sec, 9:16 vertical) with a Telegram bot for HITL approvals. Entrypoint: `python -m app.main`.

### Dev commands

| Task | Command |
|------|---------|
| Install deps | `pip install -e ".[dev]"` |
| Lint | `ruff check .` |
| Tests | `python3 -m pytest tests/ -v` |
| Type check | `mypy app/ --ignore-missing-imports` |
| Seed pilot project | `python3 -m app.seed_pilot` |
| Run application | `python3 -m app.main` |

### Key caveats

- **`TELEGRAM_BOT_TOKEN` is required** to start the app. Without a valid token, the process exits at `build_bot()` with `TokenValidationError`. All other initialization (DB, prompts, backfill) runs before that point.
- **SQLite DB** is at `data/state.db` (auto-created on first run). Delete it to reset state: `rm -f data/state.db`.
- **No `python` alias** — use `python3` on Linux. The system has Python 3.12 which satisfies the `>=3.11,<3.13` constraint.
- **PATH**: Dev tools (`ruff`, `mypy`, `pytest`, `playwright`) install to `~/.local/bin` — ensure it's on `PATH`.
- **`.env` file** must exist; copy from `.env.example`. See `app/settings.py` for all config fields.
- The app connects to Chrome via CDP on `localhost:29229` for browser automation (ChatGPT, outsee.io). This is only needed for the actual pipeline execution, not for running tests or linting.
- **Pre-existing lint/type issues**: `ruff check .` reports ~50 warnings and `mypy` reports ~178 errors — these are pre-existing in the codebase.
- Tests use in-memory SQLite and don't require external services or a `.env` file.
