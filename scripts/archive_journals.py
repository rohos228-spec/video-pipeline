"""Архив старых строк журналов (шаг B09). По умолчанию сухой прогон: ничего не меняется.

    python scripts/archive_journals.py                         сухой прогон по data/state.db, строки старше 90 дней
    python scripts/archive_journals.py --days 120 --json       другой возраст, вывод JSON
    python scripts/archive_journals.py --apply                 реально перенести в data/archive/*.jsonl.gz и удалить
    python scripts/archive_journals.py --verify <файл.jsonl.gz>  проверить архив по манифесту

Перед --apply остановите Studio или сделайте копию (scripts/backup_db.py backup). Файл .env не читается.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import journal_archive as ja  # noqa: E402


def _default_db() -> Path:
    raw = os.environ.get("SQLITE_PATH")
    p = Path(raw) if raw else ROOT / "data" / "state.db"
    return p if p.is_absolute() else ROOT / p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None, help="путь к базе (по умолчанию SQLITE_PATH или data/state.db)")
    ap.add_argument("--days", type=int, default=ja.DEFAULT_DAYS, help="возраст строк в днях (по умолчанию 90)")
    ap.add_argument("--apply", action="store_true", help="реально перенести и удалить (без флага сухой прогон)")
    ap.add_argument("--archive-dir", type=Path, default=None)
    ap.add_argument("--table", action="append", help="только эта таблица (можно несколько раз)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify", type=Path, default=None, help="проверить файл архива и выйти")
    args = ap.parse_args(argv)
    if args.verify:
        out = ja.verify_archive(args.verify)
        print(json.dumps(out, ensure_ascii=False) if args.json else
              ("архив цел: %d строк" % out["rows"] if out["ok"] else "архив НЕ в порядке: " + "; ".join(out["problems"])))
        return 0 if out["ok"] else 1
    try:
        report = ja.archive_journals(args.db or _default_db(), days=args.days, apply=args.apply,
                                     archive_dir=args.archive_dir, tables=args.table)
    except ja.ArchiveError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json else report.summary_ru())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
