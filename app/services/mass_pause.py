"""Пауза массовой генерации (на ВСЕ батчи разом).

Когда пауза активна (существует файл `data/.mass_pause`), фоновая
петля `_run_worker_loop` в `app/main.py`:
  * пропускает `serial_tick_batches` → следующие подпроекты
    не стартуют ни в одном массовом;
  * пропускает auto_advance для проектов с `batch_id is not None`
    → подпроекты массовых не двигаются *_ready → running;
  * **индивидуальные проекты продолжают работать как обычно**
    (включая running-шаги, авто-advance, и так далее).

Семантика аналогична per-batch `pause_batch_queue`, но применяется ко
всем массовым сразу.

Маркер-файл (а не флаг в БД) — намеренно:
  - переживает рестарт процесса: ребутнул Windows — остался в паузе,
    никакого внезапного возобновления массовой;
  - не требует миграции схемы;
  - виден из любого инструмента (можно удалить файл вручную).
"""

from __future__ import annotations

from pathlib import Path

# Путь относительно CWD. Бот запускается из корня репо (`python -m app.main`),
# так что `data/.mass_pause` ляжет рядом с `data/state.db`.
_MARKER = Path("data") / ".mass_pause"


def is_active() -> bool:
    return _MARKER.exists()


def set_active(active: bool) -> None:
    if active:
        _MARKER.parent.mkdir(parents=True, exist_ok=True)
        _MARKER.touch(exist_ok=True)
    else:
        _MARKER.unlink(missing_ok=True)
