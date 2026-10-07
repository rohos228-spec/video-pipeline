"""Бэкап, проверка и восстановление баз Studio (state.db, tracker.db) - шаг A15.

Команды (из корня репозитория, python из .venv):
    python scripts/backup_db.py backup                 копия state.db и tracker.db в <папка базы>/backups
    python scripts/backup_db.py backup --if-older-than-h 6   только если последняя плановая копия старше 6 ч
    python scripts/backup_db.py list                   какие копии есть
    python scripts/backup_db.py verify [файл]          проверка восстановления (без файла - последняя копия каждой базы)
    python scripts/backup_db.py restore <копия> --target <база>   вернуть копию на место (Studio остановить)
    python scripts/backup_db.py loop --interval-h 6    постоянный цикл по расписанию

Пути по умолчанию: state.db из переменной окружения SQLITE_PATH, иначе <репозиторий>/data/state.db;
tracker.db из <репозиторий>/api-tracker/tracker.db. Файл .env скрипт не читает.
Текущий файл при восстановлении не удаляется: он переименовывается в <имя>.before-restore-<время>.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import schema_migrations as sm  # noqa: E402


def default_targets(root: Path = ROOT, env: dict | None = None) -> list[Path]:
    env = os.environ if env is None else env
    raw = env.get("SQLITE_PATH")
    state = Path(raw) if raw else root / "data" / "state.db"
    if not state.is_absolute():
        state = root / state
    return [state, root / "api-tracker" / "tracker.db"]


def _targets(args) -> list[Path]:
    return [Path(p) for p in args.db] if args.db else default_targets()


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_backup(args) -> int:
    code = 0
    if args.if_older_than_h is not None:
        results = sm.run_scheduled_backups(_targets(args), interval_h=args.if_older_than_h, keep=args.keep)
    else:
        results = []
        for db in _targets(args):
            item = {"db": str(db), "status": "missing"}
            results.append(item)
            if not db.is_file():
                continue
            try:
                res = sm.backup_sqlite(db, tag=args.tag, keep=args.keep)
                check = sm.verify_backup(res.path)
                item.update(status="backed_up" if check["ok"] else "backed_up_unverified", path=str(res.path),
                            journal=str(res.journal_path) if res.journal_path else None,
                            verified=check["ok"], problems=check["problems"])
            except sm.BackupError as exc:
                item.update(status="failed", error=str(exc))
    for item in results:
        if item["status"] in ("failed", "backed_up_unverified"):
            code = 1
        if item["status"] == "missing" and not args.quiet:
            print(f"нет файла (пропущено): {item['db']}")
    if not args.quiet:
        _print(results)
    return code


def cmd_list(args) -> int:
    rows = []
    for db in _targets(args):
        d = sm.default_backup_dir(db)
        for p in sm.backup_files(d, db.stem):
            rows.append({"db": str(db), "backup": str(p), "size": p.stat().st_size,
                         "age_h": round((time.time() - p.stat().st_mtime) / 3600, 2)})
    _print(rows)
    return 0


def cmd_verify(args) -> int:
    files: list[Path] = []
    if args.backup:
        files = [Path(args.backup)]
    else:
        for db in _targets(args):
            last = sm.latest_backup(sm.default_backup_dir(db), db.stem)
            if last:
                files.append(last)
    if not files:
        print("копий нет: проверять нечего")
        return 1
    bad = 0
    out = []
    for f in files:
        res = sm.verify_backup(f)
        out.append({"backup": str(f), **res})
        bad += 0 if res["ok"] else 1
    _print(out)
    return 1 if bad else 0


def cmd_restore(args) -> int:
    try:
        res = sm.restore_backup(args.backup, args.target)
    except sm.RestoreError as exc:
        print(f"ОТКАЗ: {exc}")
        return 1
    _print(res)
    print("Готово. Прежний файл сохранён рядом (см. moved_aside); Studio можно запускать.")
    return 0


def cmd_loop(args) -> int:
    print(f"цикл бэкапа: каждые {args.interval_h} ч, проверка раз в {args.check_every_s} с (Ctrl+C - выход)")
    try:
        while True:
            for item in sm.run_scheduled_backups(_targets(args), interval_h=args.interval_h, keep=args.keep):
                if item["status"] not in ("skipped", "missing"):
                    print(time.strftime("%H:%M:%S"), json.dumps(item, ensure_ascii=False))
            time.sleep(args.check_every_s)
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--db", action="append", help="файл базы (можно несколько); по умолчанию state.db и tracker.db")

    b = sub.add_parser("backup")
    common(b)
    b.add_argument("--tag", default="manual")
    b.add_argument("--keep", type=int, default=sm.KEEP_SCHEDULED)
    b.add_argument("--if-older-than-h", type=float, default=None)
    b.add_argument("--quiet", action="store_true")
    b.set_defaults(fn=cmd_backup)
    ls = sub.add_parser("list")
    common(ls)
    ls.set_defaults(fn=cmd_list)
    v = sub.add_parser("verify")
    common(v)
    v.add_argument("backup", nargs="?")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("restore")
    r.add_argument("backup")
    r.add_argument("--target", required=True)
    r.set_defaults(fn=cmd_restore)
    lp = sub.add_parser("loop")
    common(lp)
    lp.add_argument("--interval-h", type=float, default=6.0)
    lp.add_argument("--check-every-s", type=float, default=600.0)
    lp.add_argument("--keep", type=int, default=sm.KEEP_SCHEDULED)
    lp.set_defaults(fn=cmd_loop)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
