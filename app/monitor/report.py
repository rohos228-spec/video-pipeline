"""Генерация отчёта из собранных данных мониторинга.

    python -m app.monitor.report                    # отчёт за сегодня
    python -m app.monitor.report --date 2026-05-23  # за конкретный день
    python -m app.monitor.report --dir ./mon        # из другой папки
    python -m app.monitor.report --json              # вывод в JSON

Анализирует events.jsonl и показывает:
  - Timeline шагов пайплайна (старт/конец, длительность)
  - Ошибки и их частоту
  - Статистику по генерациям (outsee, chatgpt)
  - Ссылки на скриншоты в моменты ошибок
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


def load_events(events_dir: Path, date_str: str) -> list[dict]:
    """Загружает все события за указанную дату."""
    fpath = events_dir / f"events_{date_str}.jsonl"
    if not fpath.exists():
        return []
    events = []
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def analyze(events: list[dict]) -> dict:
    """Строит сводку из списка событий."""
    step_durations: dict[str, list[float]] = defaultdict(list)
    errors: list[dict] = []
    screenshots: list[dict] = []
    event_counts = Counter()
    project_steps: dict[int, list[dict]] = defaultdict(list)

    for ev in events:
        event_type = ev.get("event", "")
        event_counts[event_type] += 1

        pid = ev.get("project_id")
        if pid is not None:
            project_steps[pid].append(ev)

        detail = ev.get("detail", {})

        if event_type.endswith("_end") and "duration_s" in detail:
            base = event_type.removesuffix("_end")
            step_durations[base].append(detail["duration_s"])

        if "error_type" in detail or "error_msg" in detail:
            errors.append({
                "ts": ev.get("ts"),
                "event": event_type,
                "project_id": pid,
                "error_type": detail.get("error_type", ""),
                "error_msg": detail.get("error_msg", "")[:200],
                "screenshot": ev.get("screenshot"),
            })

        if ev.get("screenshot"):
            screenshots.append({
                "ts": ev.get("ts"),
                "file": ev.get("screenshot"),
                "event": event_type,
                "tab": detail.get("tab"),
                "url": detail.get("url", "")[:100],
            })

    timing_summary = {}
    for step, durations in sorted(step_durations.items()):
        timing_summary[step] = {
            "count": len(durations),
            "total_s": round(sum(durations), 1),
            "avg_s": round(sum(durations) / len(durations), 1) if durations else 0,
            "min_s": round(min(durations), 1) if durations else 0,
            "max_s": round(max(durations), 1) if durations else 0,
        }

    return {
        "total_events": len(events),
        "event_counts": dict(event_counts.most_common()),
        "timing": timing_summary,
        "errors": errors,
        "screenshots_count": len(screenshots),
        "projects_seen": sorted(project_steps.keys()),
    }


def print_report(analysis: dict, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2, default=str))
        return

    print("=" * 60)
    print("  ОТЧЁТ МОНИТОРИНГА video-pipeline")
    print("=" * 60)

    print(f"\nВсего событий: {analysis['total_events']}")
    print(f"Скриншотов: {analysis['screenshots_count']}")
    print(f"Проекты: {analysis['projects_seen'] or 'нет'}")

    print("\n--- Тайминги ---")
    timing = analysis.get("timing", {})
    if timing:
        print(f"{'Действие':<35} {'Кол-во':>6} {'Сумма':>8} {'Средн':>8} {'Мин':>8} {'Макс':>8}")
        print("-" * 75)
        for step, stats in timing.items():
            print(
                f"{step:<35} {stats['count']:>6} "
                f"{stats['total_s']:>7.1f}s {stats['avg_s']:>7.1f}s "
                f"{stats['min_s']:>7.1f}s {stats['max_s']:>7.1f}s"
            )
    else:
        print("  (нет данных)")

    print("\n--- Ошибки ---")
    errors = analysis.get("errors", [])
    if errors:
        for e in errors[:20]:
            print(
                f"  [{e.get('ts', '?')[:19]}] "
                f"#{e.get('project_id', '?')} "
                f"{e.get('error_type', '?')}: "
                f"{e.get('error_msg', '')[:100]}"
            )
        if len(errors) > 20:
            print(f"  ... и ещё {len(errors) - 20} ошибок")
    else:
        print("  (нет ошибок)")

    print("\n--- Частота событий (топ-15) ---")
    counts = analysis.get("event_counts", {})
    for ev, cnt in list(counts.items())[:15]:
        print(f"  {ev:<40} {cnt:>5}")

    print()


def main() -> None:
    p = argparse.ArgumentParser(
        prog="python -m app.monitor.report",
        description="Анализ данных мониторинга video-pipeline",
    )
    p.add_argument(
        "--dir",
        default="data/monitor",
        help="папка с данными мониторинга",
    )
    p.add_argument(
        "--date",
        default=None,
        help="дата (YYYY-MM-DD), дефолт — сегодня",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="вывод в формате JSON",
    )
    args = p.parse_args()

    monitor_dir = Path(args.dir)
    events_dir = monitor_dir / "events"

    if not events_dir.exists():
        print(f"Папка {events_dir} не найдена. Запускал ли ты мониторинг?")
        sys.exit(1)

    date_str = args.date or datetime.now(tz=UTC).strftime("%Y-%m-%d")

    events = load_events(events_dir, date_str)
    if not events:
        print(f"Нет событий за {date_str} в {events_dir}")
        sys.exit(0)

    analysis = analyze(events)
    print_report(analysis, as_json=args.json)


if __name__ == "__main__":
    main()
