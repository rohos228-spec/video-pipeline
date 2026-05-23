"""Запуск бота С мониторингом — drop-in замена `python -m app.main`.

    python -m app.monitor              # бот + мониторинг
    python -m app.monitor --no-browser # бот + логи, без скриншотов Chrome
    python -m app.monitor --interval 5 # скриншоты каждые 5 сек (дефолт 10)
    python -m app.monitor --dir ./mon  # папка для данных мониторинга

Что делает:
  1. Инициализирует JSON-лог (data/monitor/logs/) и событийный лог
     (data/monitor/events/).
  2. Monkey-patch'ит ChatGPTBot, OutseeBot, advance_project — все
     вызовы записываются в events.jsonl с таймингами и параметрами.
  3. Запускает BrowserWatcher — скриншоты Chrome каждые N сек +
     консольные ошибки, HTTP-ошибки, навигации.
  4. Запускает обычный app.main (TG-бот + воркер).
  5. По Ctrl+C корректно останавливает всё.

Данные пишутся в:
  data/monitor/
    logs/          ← ротируемые логи (текст + JSONL)
    events/        ← бизнес-события (events_YYYY-MM-DD.jsonl)
    screenshots/   ← скриншоты Chrome (PNG)
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from loguru import logger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m app.monitor",
        description="video-pipeline бот + мониторинг в реальном времени",
    )
    p.add_argument(
        "--dir",
        default="data/monitor",
        help="папка для данных мониторинга (дефолт: data/monitor)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="интервал скриншотов в секундах (дефолт: 10)",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="не подключаться к Chrome (только логи + action tracking)",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    monitor_dir = Path(args.dir)

    from app.monitor.log_sink import close as close_logs
    from app.monitor.log_sink import init as init_logs

    init_logs(monitor_dir)

    logger.info("=" * 60)
    logger.info("MONITOR MODE: video-pipeline с диагностикой")
    logger.info("  данные → {}", monitor_dir.resolve())
    logger.info("  скриншоты: {}", "ВЫКЛ" if args.no_browser else f"каждые {args.interval}с")
    logger.info("=" * 60)

    watcher = None
    if not args.no_browser:
        from app.monitor.browser_watcher import BrowserWatcher
        from app.settings import settings

        watcher = BrowserWatcher(
            cdp_url=settings.browser_cdp_url,
            screenshot_interval=args.interval,
        )

    from app.monitor.action_tracker import patch_all

    patch_all(watcher=watcher)

    if watcher is not None:
        # Стартуем watcher до бота — чтобы скриншоты шли с первой секунды.
        # Если Chrome не запущен — watcher просто логирует warning и
        # продолжает (не крашит бота).
        await watcher.start()

    from app.monitor.log_sink import emit_event

    emit_event("monitor_started", detail={
        "monitor_dir": str(monitor_dir.resolve()),
        "screenshot_interval": args.interval,
        "browser_watch": not args.no_browser,
    })

    try:
        from app.main import main as app_main

        await app_main()
    except KeyboardInterrupt:
        logger.info("monitor: Ctrl+C — останавливаюсь")
    except Exception:
        logger.exception("monitor: app.main упал")
        raise
    finally:
        emit_event("monitor_stopped")
        if watcher is not None:
            await watcher.stop()
        close_logs()


if __name__ == "__main__":
    asyncio.run(main())
