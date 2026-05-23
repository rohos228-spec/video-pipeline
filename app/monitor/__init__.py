"""Pipeline Monitor — система мониторинга и диагностики video-pipeline.

Модули:
  - log_sink: структурированное JSON-логирование в data/monitor/
  - browser_watcher: периодические скриншоты Chrome + Playwright-события
  - action_tracker: обёртки ключевых методов ботов с таймингами
  - __main__: запуск мониторинга (python -m app.monitor)
"""
