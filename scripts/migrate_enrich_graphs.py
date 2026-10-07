"""Перенос графов со старым типом enrich_N на excel_gpt (C10).

По умолчанию сухой проход: печатает, что изменилось бы, и ничего не пишет.

    python scripts/migrate_enrich_graphs.py --db копия.db             # сухой проход по копии
    python scripts/migrate_enrich_graphs.py --db копия.db --apply     # копия файла -> запись -> проверка
    python scripts/migrate_enrich_graphs.py --db копия.db --rollback  # вернуть исходные ноды по записям отката

Без --db берётся база из настроек (боевая). Запись и откат по ней требуют --confirm-live
и решения владельца. Резервная копия кладётся в <папка базы>/backups (или --backup-dir).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _main(db: Path, apply: bool, rollback: bool, backup_dir: Path | None) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.services.graph_migrations import run_migration

    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}", poolclass=NullPool,
                                 connect_args={"timeout": 60})
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return await run_migration(factory, db, apply=apply, rollback=rollback, backup_dir=backup_dir)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    from app.services.graph_migrations import MigrationError, format_report_ru

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="записать изменения (с резервной копией)")
    group.add_argument("--rollback", action="store_true", help="вернуть исходные ноды")
    parser.add_argument("--db", type=Path, help="путь к файлу SQLite (по умолчанию база из настроек)")
    parser.add_argument("--backup-dir", type=Path, help="куда класть резервную копию")
    parser.add_argument("--confirm-live", action="store_true", help="разрешить запись в базу из настроек")
    parser.add_argument("--json", action="store_true", help="вывести полный отчёт JSON")
    args = parser.parse_args(argv)

    live = args.db is None
    if live:
        from app.settings import settings
        db = Path(settings.sqlite_path)
    else:
        db = args.db.resolve()
    if (args.apply or args.rollback) and live and not args.confirm_live:
        print("Отказ: запись в базу из настроек (боевую) без --confirm-live не выполняется. "
              "Для проверки укажите --db <копия>.", file=sys.stderr)
        return 2
    if not db.is_file():
        print(f"Файл базы не найден: {db}", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(_main(db, args.apply, args.rollback, args.backup_dir))
    except MigrationError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["mode"] == "rollback":
        n = sum(len(r["restored"]) for r in report["restored"])
        print(f"ОТКАТ: возвращено узлов: {n}; осталось enrich_N: {report['enrich_nodes_after']}; копия: {report['backup']}")
    else:
        print(format_report_ru({**report, "dry_run": report["mode"] == "dry_run"}))
        if report["mode"] == "apply":
            state = "проверка пройдена" if report["verified"] else "ПРОВЕРКА НЕ ПРОЙДЕНА, выполнен автооткат"
            print(f"Резервная копия: {report['backup']}; осталось enrich_N: {report['enrich_nodes_after']}; {state}")
        else:
            print(f"Всего enrich_N в базе: {report['enrich_nodes_total']}")
    return 0 if report.get("verified", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
