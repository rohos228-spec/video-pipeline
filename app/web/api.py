"""FastAPI app: REST + WS + static frontend serving.

Подключается к существующей SQLite-БД через `app.db.session_scope`. Ничего
не создаёт сам — миграции/таблицы делает `app.main._init_db()`.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.services.event_bus import get_bus
from app.web.routers import (
    artifacts as artifacts_router,
    frames as frames_router,
    hitl as hitl_router,
    projects as projects_router,
    prompts as prompts_router,
    runs as runs_router,
    workflows as workflows_router,
)
from app.web.settings_default import seed_default_workflow

API_PREFIX = "/api"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Лёгкая инициализация: дефолтный системный Workflow в БД, если ещё нет."""
    try:
        await seed_default_workflow()
    except Exception:  # noqa: BLE001
        logger.exception("seed_default_workflow failed (non-fatal)")
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
    app.include_router(runs_router.router, prefix=API_PREFIX)
    app.include_router(prompts_router.router, prefix=API_PREFIX)
    app.include_router(hitl_router.router, prefix=API_PREFIX)
    app.include_router(frames_router.router, prefix=API_PREFIX)
    app.include_router(artifacts_router.router, prefix=API_PREFIX)

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
        logger.info("web frontend bundle not found ({}), dev mode (use localhost:3000)", out_dir)
        return

    # Mount всю папку (включая _next/*).
    app.mount(
        "/_next",
        StaticFiles(directory=out_dir / "_next", check_dir=True),
        name="next-static",
    )

    @app.get("/")
    async def root_index() -> FileResponse:
        return FileResponse(out_dir / "index.html")

    @app.get("/{full_path:path}")
    async def catch_all(full_path: str) -> FileResponse:
        # Next static export — все маршруты как .html-файлы.
        candidate = out_dir / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        html_variant = out_dir / f"{full_path}.html"
        if html_variant.is_file():
            return FileResponse(html_variant)
        return FileResponse(out_dir / "index.html")  # SPA fallback
