"""Глобальная пауза воркера.

Когда пауза активна (существует файл `data/.global_pause`), фоновая
петля `_run_worker_loop` в `app/main.py` **не** продвигает ни обычные
проекты, ни массовые очереди (`serial_tick_batches`). Telegram-бот
остаётся отзывчивым — кнопка «▶ Возобновить всё» в главном меню
снимает паузу.

Маркер-файл (а не флаг в БД) — намеренно:
  - переживает рестарт процесса: ребутнул Windows — остался в паузе,
    никакого внезапного auto-advance, пока сам не снял;
  - не требует миграции схемы;
  - виден из любого инструмента (можно «удалить файл» вручную).
"""

from __future__ import annotations

from pathlib import Path

# Путь относительно CWD. Бот запускается из корня репо (`python -m app.main`),
# так что `data/.global_pause` ляжет рядом с `data/state.db`.
_MARKER = Path("data") / ".global_pause"


def is_active() -> bool:
    return _MARKER.exists()


def set_active(active: bool) -> None:
    if active:
        _MARKER.parent.mkdir(parents=True, exist_ok=True)
        _MARKER.touch(exist_ok=True)
    else:
        _MARKER.unlink(missing_ok=True)
