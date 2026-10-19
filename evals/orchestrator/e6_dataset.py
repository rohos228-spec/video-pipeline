"""40 размеченных запросов E6.

Разметка выведена из детерминированных правил плана (§4) как заглушка,
пока пользователь и Фейбл не проставят ручные метки. Доля спорных — 8/40.
"""

from __future__ import annotations

from typing import Any


def _simple(text: str) -> dict[str, Any]:
    return {"text": text, "bucket": "simple", "tier": "fast", "mode": "append", "deterministic": True}


def _plan(text: str) -> dict[str, Any]:
    return {"text": text, "bucket": "plan", "tier": "strong", "mode": "steer", "deterministic": True}


def _long(text: str) -> dict[str, Any]:
    return {"text": text, "bucket": "long", "tier": "strong", "mode": "steer", "deterministic": True}


def _interrupt(text: str) -> dict[str, Any]:
    return {"text": text, "bucket": "interrupt", "tier": "fast", "mode": "interrupt", "deterministic": True}


def _ambiguous(text: str, tier: str, mode: str) -> dict[str, Any]:
    return {"text": text, "bucket": "ambiguous", "tier": tier, "mode": mode, "deterministic": False}


def e6_queries() -> list[dict[str, Any]]:
    simple = [
        _simple("Какой статус проекта?"),
        _simple("Покажи статус шага озвучки?"),
        _simple("Сколько кадров в сцене?"),
        _simple("Где лежит закадр?"),
        _simple("Что сейчас выполняется?"),
        _simple("Когда обновлялся проект?"),
        _simple("Как называется ролик?"),
        _simple("Какие сцены уже готовы?"),
        _simple("Кратко: на чём остановились?"),
        _simple("Статус проверки картинок?"),
        _simple("Что в очереди?"),
        _simple("Сколько символов в ячейке?"),
        _simple("Где монтажный лист?"),
        _simple("Какой номер проекта?"),
        _simple("Покажи последние ошибки?"),
        _simple("Кратко перескажи ход задачи?"),
    ]
    plans = [
        _plan("Составь план запуска кадров"),
        _plan("Нужен ревью плана перед запуском"),
        _plan("Покажи промпт ноды кадров"),
        _plan("Собери отчёт по длинам закадра"),
        _plan("Исправь промт секции кадров"),
        _plan("Сделай ревью результата валидаторов"),
        _plan("План: проверить код и запустить ноду"),
        _plan("Нужен отчёт, что фикс есть в коде"),
        _plan("Ревью: не удалять сцены"),
        _plan("Обнови промпт и приложи отчёт"),
    ]
    longs = [
        _long("Контекст задачи. " + ("абзац без ключевых слов. " * 80)),
        _long("Хронология. " + ("событие номер следующее. " * 70)),
        _long("Описание сцены. " + ("герой стоит у двери. " * 70)),
        _long("Заметки оператора. " + ("кадр снят, ждём. " * 80)),
    ]
    interrupts = [
        _interrupt("стой"),
        _interrupt("отмена задачи"),
    ]
    ambiguous = [
        _ambiguous("ага", "fast", "append"),
        _ambiguous("ну и ладно", "fast", "append"),
        _ambiguous("продолжай в том же духе", "fast", "append"),
        _ambiguous("ок дальше", "fast", "append"),
        _ambiguous("интересно", "fast", "append"),
        _ambiguous("угу", "fast", "append"),
        _ambiguous("потом гляну", "fast", "append"),
        _ambiguous("не уверен", "strong", "steer"),
    ]
    rows = [*simple, *plans, *longs, *interrupts, *ambiguous]
    if len(rows) != 40:
        raise RuntimeError(f"E6 dataset must have 40 rows, got {len(rows)}")
    for index, row in enumerate(rows, start=1):
        row["id"] = f"e6-{index:02d}"
    return rows
