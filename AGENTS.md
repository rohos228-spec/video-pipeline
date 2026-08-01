# AGENTS.md

> **Карта системы (промпты, оркестратор, GPT-чат, Create, mass, xlsx):** [`docs/AGENT_MAP.md`](docs/AGENT_MAP.md). Операторская шпаргалка: [`docs/OPERATOR_BIBLE.md`](docs/OPERATOR_BIBLE.md).
> **БД v2 (карточки кадров, дробный порядок, связи, кнопка «База»):** [`docs/DB_V2.md`](docs/DB_V2.md).

## Cursor Cloud specific instructions

### Git: merge to `main` immediately (mandatory)

The user's production copy tracks **`origin/main` only** (`STUDIO.cmd` → update pulls `main`). Every code fix is **incomplete** until it is pushed to `main`.

Agent workflow after any code change:

1. Commit on `main` (or merge feature branch into `main` in the same session).
2. `git push origin main`.
3. Tell the user the new **`git HEAD` sha** — they must see it after `git pull`, not an old sha like `eb95b9e`.

Do **not** stop at an open PR on a side branch. Optional PR is fine **after** `main` is updated.

### Overview

**video-pipeline** is a single Python 3.11+ application (no Docker) that automates short video generation (60–75 sec, 9:16 vertical) with a Telegram bot for HITL approvals. Entrypoint: `python -m app.main`.

### Dev commands

| Task | Command |
|------|---------|
| Install deps | `pip install -e ".[dev]"` |
| ASR NVIDIA (монтаж) | `pip install -e ".[nvidia]"` + `ASR_BACKEND=nvidia` |
| Предзагрузка Parakeet | `python3 scripts/download_nvidia_asr.py` (если WinError 32) |
| Lint | `ruff check .` |
| Tests | `python3 -m pytest tests/ -v` |
| Type check | `mypy app/ --ignore-missing-imports` |
| Seed pilot project | `python3 -m app.seed_pilot` |
| Run application | `STUDIO.cmd` (Windows) or `python3 -m app.main` from repo root |

### Key caveats

- **Telegram optional**: set `TELEGRAM_ENABLED=false` (and leave `TELEGRAM_BOT_TOKEN` empty) for web-only mode — worker + FastAPI on `:8765`, HITL via web UI. Use `STUDIO.cmd` → пункт 1 on Windows. With a valid token, `python -m app.main` in `.venv` runs bot + worker + web.
- **SQLite DB** is at `data/state.db` (auto-created on first run). Delete it to reset state: `rm -f data/state.db`.
- **No `python` alias** — use `python3` on Linux. The system has Python 3.12 which satisfies the `>=3.11,<3.13` constraint.
- **PATH**: Dev tools (`ruff`, `mypy`, `pytest`, `playwright`) install to `~/.local/bin` — ensure it's on `PATH`.
- **Paths** resolve from repo root (`pyproject.toml`), not shell CWD — safe to run `python -m app.main` even after `cd web`. On Windows use `STUDIO.cmd` or `scripts\run-backend.ps1`.
- The app connects to Chrome via CDP on `localhost:29229` for browser automation (Outsee CDP fallback, ElevenLabs). GPT **text** is API-only (`GPT_API_KEY`). **Outsee media** via Developer API: `OUTSEE_API_KEY` (profile outsee.io) → Bearer `https://outsee.io/api/v1/...` (`app/bots/outsee_http.py`). Set `IMAGE_PROVIDER=outsee` / `VIDEO_PROVIDER=outsee`. Fallback CDP: `OUTSEE_HTTP_FALLBACK_CDP=true`. Alternative: `IMAGE_PROVIDER=grsai` / `VIDEO_PROVIDER=grsai` (`GRSAI_API_KEY` — другой ключ).
- Studio Create: provider **outsee** → `POST /api/outsee/generate`; provider **grsai** → `/api/grsai/generate`.
- **Pre-existing lint/type issues**: `ruff check .` reports ~50 warnings and `mypy` reports ~178 errors — these are pre-existing in the codebase.
- Tests use in-memory SQLite and don't require external services or a `.env` file.
- **Parallel projects**: `WORKER_MAX_PARALLEL` (default `1`) — top-N window in `app/services/gen_queue.py` + concurrent advances in `_run_worker_loop`. `1` keeps legacy serial behavior; `paused`/`user_stop` on an earlier slot still hard-blocks the rest of the queue.
- **API xlsx write-back**: `outputMode=project_file` → `app/services/xlsx_text_writeback.py` (downloaded `.xlsx` or TSV `# Лист:` blocks) writes `project.xlsx`, then `sync_project_xlsx`.
- **Vision checks**: image inputs (`png/jpg/webp/gif`) go to the model as multimodal parts in `app/services/gpt_api.py` (chat `image_url` / Responses `input_image`), up to 8 images / 4MB each.
- **Browser ChatGPT retired for text**: all text/xlsx/check/verdict/anim_pr/hero-prompt/music-prompt go through `app/services/gpt_client.py` → `gpt_api`. CDP remains only for Outsee (image/video/music gen) and ElevenLabs TTS. Need `GPT_API_KEY` + `GPT_BASE_URL` (kie: `https://api.kie.ai`, path `/codex/v1/responses`, model `gpt-5-6-sol`).

### Studio UI version badge

- Build counter lives in `web/STUDIO_VERSION` (line 1 = number, line 2 = git short sha).
- **Before every commit that touches web UI**, run: `python3 scripts/bump_studio_version.py` (bumps version and rebuilds `web/out/`).
- **`web/out/` is committed to git** so Windows users get the new UI from `git pull` without npm. FastAPI serves `web/out/`.
- Bottom-left badge shows baked version; `/api/studio-version` reports `ui_stale` if `web/out` does not match `STUDIO_VERSION`.

## Working with Cursor AI (beginner checklist)

### Already in this repo (agents auto-load)

| Kind | Path | Purpose |
|------|------|---------|
| Always rule | `.cursor/rules/video-pipeline-ops.mdc` | Studio ops, attach, anim_pr, diagnostics |
| Scoped rule | `.cursor/rules/chatgpt-attach.mdc` | Safe ChatGPT attach edits |
| Scoped rule | `.cursor/rules/studio-web-ui.mdc` | UI bump + Prompt Builder contract |
| Skill | `/diagnose-pipeline` | Logs → doctor → targeted pytest |
| Skill | `/fix-chatgpt-attach` | Attach/DOM evaluate fixes |
| Skill | `/anim-pr-soft-retry` | R48 xlsx truth + soft retry |
| Skill | `/bump-studio-ui` | Version bump + `web/out` |
| Skill | `/safe-step-reset` | Reset without wiping media |

### Modes

- **Ask** — explain code / failures (read-only)
- **Plan** — multi-file design before coding
- **Agent** — implement + run tests
- **Desktop Agent** — local Chrome CDP `:29229`, live `data/backend-*.log`, `STUDIO.cmd`
- **Cloud Agent** — PR-sized work with pytest; cannot see your local ChatGPT Chrome session

### One-time setup (human clicks in Cursor / Windows)

1. Connect GitHub at [Integrations](https://cursor.com/dashboard/integrations).
2. User Rules (Customize → Rules): Russian replies, high autonomy, never commit secrets, bump Studio UI before web commits.
3. Open this repo in Cursor Desktop; confirm Skills under Customize → Skills (project skills from `.cursor/skills/`).
4. Windows: `STUDIO.cmd` → [1]; Chrome CDP via [3]; log in to ChatGPT + outsee once.
5. Optional Cloud Environment: install cmd `pip install -e ".[dev]"`; do not expect local CDP there.
6. MCP: start with GitHub integration only; skip Telegram/filesystem/SQLite MCP unless you have a clear gap.

### How to assign work

- Local attach / img / video / anim_pr debugging → Desktop Agent + skill
- “Make a PR / add tests / refactor without CDP” → Cloud Agent
- Prompt template: goal + scope + done criteria + “follow AGENTS.md and `.cursor/rules/`”
