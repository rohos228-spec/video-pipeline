"""FastAPI app: REST + WS + static frontend serving.

Подключается к существующей SQLite-БД через `app.db.session_scope`. Ничего
не создаёт сам — миграции/таблицы делает `app.main._init_db()`.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.services.event_bus import get_bus
from app.web.routers import (
    artifacts as artifacts_router,
    config_presets as config_presets_router,
    frames as frames_router,
    generation_options as generation_options_router,
    hitl as hitl_router,
    project_ops as project_ops_router,
    projects as projects_router,
    prompt_files as prompt_files_router,
    prompt_studio as prompt_studio_router,
    prompts as prompts_router,
    runs as runs_router,
    sidebar_layout as sidebar_layout_router,
    workflows as workflows_router,
)
from app.web.settings_default import seed_default_workflow

API_PREFIX = "/api"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Идемпотентная инициализация:
    1) create_all таблиц (в т.ч. новых: workflows, workflow_runs, node_runs);
    2) seed дефолтного Workflow.

    Безопасно повторно вызывается. В дев-режиме (uvicorn `--reload` без app.main)
    этот lifespan единственный гарантирует наличие новых таблиц.
    """
    try:
        from app.db import engine
        from app.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:  # noqa: BLE001
        logger.exception("create_all failed (non-fatal — possibly already exists)")
    try:
        await seed_default_workflow()
    except Exception:  # noqa: BLE001
        logger.exception("seed_default_workflow failed (non-fatal)")

    from app.services.pipeline_worker import ensure_pipeline_worker_started
    from app.telegram.noop_bot import get_worker_bot

    ensure_pipeline_worker_started(get_worker_bot(None))
    logger.info("web lifespan: pipeline worker ensured (same process as API)")

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="video-pipeline web",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    # Локальный фронт ходит с localhost:3000 в dev — открываем CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(workflows_router.router, prefix=API_PREFIX)
    app.include_router(projects_router.router, prefix=API_PREFIX)
    app.include_router(project_ops_router.router, prefix=API_PREFIX)
    app.include_router(generation_options_router.router, prefix=API_PREFIX)
    app.include_router(config_presets_router.router, prefix=API_PREFIX)
    app.include_router(sidebar_layout_router.router, prefix=API_PREFIX)
    app.include_router(runs_router.router, prefix=API_PREFIX)
    app.include_router(prompts_router.router, prefix=API_PREFIX)
    app.include_router(prompt_studio_router.router, prefix=API_PREFIX)
    app.include_router(prompt_files_router.router, prefix=API_PREFIX)
    app.include_router(hitl_router.router, prefix=API_PREFIX)
    app.include_router(frames_router.router, prefix=API_PREFIX)
    app.include_router(artifacts_router.router, prefix=API_PREFIX)
    app.include_router(artifacts_router.files_router, prefix=API_PREFIX)

    @app.api_route(f"{API_PREFIX}/{{rest:path}}", methods=["POST", "PUT", "PATCH", "DELETE"])
    async def api_write_not_found(rest: str) -> None:
        """Не даём GET catch-all отвечать 405 на неизвестные POST /api/*."""
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="API route not found — перезапустите Studio (start-studio.ps1)",
        )

    # ── WebSocket: live-стрим событий выбранного канала ──
    @app.websocket("/ws/{channel:path}")
    async def ws_channel(ws: WebSocket, channel: str) -> None:
        """Клиент подписывается на канал (например, `runs.42`, `global`,
        `hitl.7`). Сервер шлёт JSON-сообщения каждое полученное событие.
        """
        await ws.accept()
        bus = get_bus()
        try:
            async with bus.subscribe(channel) as queue:
                # Сразу шлём «hello», чтобы клиент знал, что подписка живая.
                await ws.send_text(json.dumps({"type": "subscribed", "channel": channel}))
                while True:
                    # Параллельно ждём сообщения из bus и пинг от клиента.
                    get_event_task = asyncio.create_task(queue.get())
                    recv_task = asyncio.create_task(ws.receive_text())
                    done, pending = await asyncio.wait(
                        {get_event_task, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if get_event_task in done:
                        try:
                            evt = get_event_task.result()
                        except Exception:  # noqa: BLE001
                            break
                        await ws.send_text(json.dumps(evt, default=str))
                    if recv_task in done:
                        # Игнорируем содержимое (пока что), но обработка нужна
                        # для детекта disconnect.
                        try:
                            recv_task.result()
                        except WebSocketDisconnect:
                            break
                        except Exception:
                            break
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001
            logger.exception("ws channel={} crashed", channel)
            try:
                await ws.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    # ── Health ──
    @app.get(f"{API_PREFIX}/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{API_PREFIX}/studio-version")
    async def studio_version() -> dict[str, str | int]:
        from app.web.studio_version import read_studio_version

        return read_studio_version()

    # ── Статика Next.js (export → ./web/out) ──
    _mount_frontend(app)

    return app


def _mount_frontend(app: FastAPI) -> None:
    """Если есть собранный Next.js (`web/out` после `next build && next export`
    либо `web/.next` через middleware), отдаём как статику.

    В dev режиме фронт работает отдельно на http://localhost:3000 → CORS
    разрешён, эту функцию можно игнорировать.
    """
    repo_root = Path(__file__).resolve().parents[2]
    out_dir = repo_root / "web" / "out"
    if not out_dir.is_dir():
        logger.warning(
            "web frontend bundle not found ({}). Run: cd web && npm run build",
            out_dir,
        )
        _mount_frontend_missing_help(app, out_dir)
        return

    # Mount всю папку (включая _next/*).
    app.mount(
        "/_next",
        StaticFiles(directory=out_dir / "_next", check_dir=True),
        name="next-static",
    )

    def _patch_index_html_version(html: str) -> str:
        """Подменяет v102 и др. в отданном index.html на web/STUDIO_VERSION (git pull)."""
        from app.web.studio_version import read_studio_version_label

        label = read_studio_version_label()
        html = re.sub(
            r'(title="UI:\s*)v\d+[^"]*(")',
            rf"\1{label}\2",
            html,
            count=1,
        )
        html = re.sub(
            r"(>)\s*v\d+\s*·\s*[0-9a-fA-F]{4,}\s*(<)",
            rf"\1{label}\2",
            html,
        )
        return html

    def _html_response(path: Path) -> HTMLResponse:
        body = path.read_text(encoding="utf-8")
        body = _patch_index_html_version(body)
        return HTMLResponse(
            content=body,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/", response_model=None)
    async def root_index():
        return _html_response(out_dir / "index.html")

    @app.get("/{full_path:path}", response_model=None)
    async def catch_all(full_path: str):
        # /api/* обслуживают FastAPI-роутеры — не отдаём index.html (иначе в браузере
        # «открывается проект» вместо JSON на /api/studio-version).
        if full_path == "api" or full_path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        # Next static export — все маршруты как .html-файлы.
        candidate = out_dir / full_path
        if candidate.is_file():
            if candidate.suffix.lower() in {".html", ".htm"}:
                return _html_response(candidate)
            return FileResponse(candidate)
        html_variant = out_dir / f"{full_path}.html"
        if html_variant.is_file():
            return _html_response(html_variant)
        return _html_response(out_dir / "index.html")  # SPA fallback


def _mount_frontend_missing_help(app: FastAPI, out_dir: Path) -> None:
    """Показываем понятную страницу, если web/out ещё не собран."""
    from fastapi.responses import HTMLResponse

    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Video Pipeline</title>
<style>
body{{font-family:Segoe UI,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;line-height:1.5}}
code{{background:#f0f0f0;padding:2px 6px;border-radius:4px}}
ol{{padding-left:1.2rem}}
</style></head><body>
<h1>API работает, UI не собран</h1>
<p>Бэкенд запущен, но папки <code>{out_dir}</code> нет.</p>
<h2>Windows (из корня video-pipeline)</h2>
<ol>
<li>Меню: <strong>6. Build Web UI</strong> или <strong>* Quick start</strong></li>
<li>Или в PowerShell:<br>
<code>cd web; npm install; npm run build; cd ..</code></li>
<li>Запустите Studio: <strong>2. Start Studio</strong></li>
<li>Откройте <a href="http://127.0.0.1:8765">http://127.0.0.1:8765</a></li>
</ol>
<p>API health: <a href="/api/health">/api/health</a></p>
</body></html>"""

    @app.get("/")
    async def frontend_missing_root() -> HTMLResponse:
        return HTMLResponse(html)

    @app.get("/{full_path:path}")
    async def frontend_missing_catch(full_path: str) -> HTMLResponse:
        return HTMLResponse(html)
