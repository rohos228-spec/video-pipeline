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
| Run application | `VideoPipelineStudio.cmd` (Windows GUI) or `python3 -m app.main` from repo root |

### Key caveats

- **Telegram optional**: set `TELEGRAM_ENABLED=false` (and leave `TELEGRAM_BOT_TOKEN` empty) for web-only mode — worker + FastAPI on `:8765`, HITL via web UI. Use `.\start-studio.ps1` on Windows. With a valid token, `.\start.ps1` runs bot + worker + web as before.
- **SQLite DB** is at `data/state.db` (auto-created on first run). Delete it to reset state: `rm -f data/state.db`.
- **No `python` alias** — use `python3` on Linux. The system has Python 3.12 which satisfies the `>=3.11,<3.13` constraint.
- **PATH**: Dev tools (`ruff`, `mypy`, `pytest`, `playwright`) install to `~/.local/bin` — ensure it's on `PATH`.
- **Paths** resolve from repo root (`pyproject.toml`), not shell CWD — safe to run `python -m app.main` even after `cd web`. Prefer `.\run-backend.ps1` on Windows.
- The app connects to Chrome via CDP on `localhost:29229` for browser automation (ChatGPT, outsee.io). This is only needed for the actual pipeline execution, not for running tests or linting.
- **Pre-existing lint/type issues**: `ruff check .` reports ~50 warnings and `mypy` reports ~178 errors — these are pre-existing in the codebase.
- Tests use in-memory SQLite and don't require external services or a `.env` file.

### Доставка кода на Windows-машину пользователя

Когда пользователь просит «скачать на винду» или аналогичное — **не давать инструкции**, а сразу сгенерировать готовый PowerShell-блок, который пользователь может целиком вставить в терминал. Пример:

```powershell
cd $env:USERPROFILE\Desktop
git clone https://github.com/rohos228-spec/video-pipeline.git
cd video-pipeline
git checkout <ветка>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd web; pnpm install; pnpm run build; cd ..
copy .env.example .env
Write-Host "Готово! Запусти: .\VideoPipelineStudio.cmd"
```

Подставить актуальную ветку. Весь блок должен быть copy-paste-ready.

### Studio UI version badge

- Build counter lives in `web/STUDIO_VERSION` (line 1 = number, line 2 = git short sha).
- **Before every commit that touches web UI**, run: `python3 scripts/bump_studio_version.py` (bumps version and rebuilds `web/out/`).
- **`web/out/` is committed to git** so Windows users get the new UI from `git pull` without npm. FastAPI serves `web/out/`.
- Bottom-left badge shows baked version; `/api/studio-version` reports `ui_stale` if `web/out` does not match `STUDIO_VERSION`.
