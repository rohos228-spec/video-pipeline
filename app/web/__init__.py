"""Локальный веб-UI поверх video-pipeline.

Это FastAPI-приложение, которое:
  • даёт REST для управления Workflow / Project / Run / Prompt;
  • стримит live-события прогона через WebSocket (`/ws/runs/{id}`);
  • отдаёт собранный Next.js фронтенд из ./web/out (если есть) на корне.

Поднимается из `app.main` параллельно с TG-ботом и воркером.
Слушает по умолчанию на http://localhost:8080.
"""

from __future__ import annotations

from app.web.api import create_app

__all__ = ["create_app"]
