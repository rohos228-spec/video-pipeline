"""Автономная проверка пульса цикла надзора (A18). Запуск из Планировщика заданий / cron раз в минуту:

    python scripts/watchdog_check.py [--dir <папка с observer_heartbeat.json>] [--window 300] [--json]

Читает только файл пульса (папка: --dir, VP_HEARTBEAT_DIR или папка SQLITE_PATH). Если пульс старше окна
(`VP_WATCHDOG_HEARTBEAT_WINDOW_S`, по умолчанию 300 с) - одна тревога на эпизод (аудит + notification_log через
notify; в Telegram, если он включён). Когда пульс вернулся - снимает метку и пишет «восстановлено».
Код выхода: 0 - всё в порядке или нет данных/чистая остановка, 2 - молчание (тревога открыта).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir")
    ap.add_argument("--window", type=float)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from app.services import supervisor_heartbeat as hb
    result = asyncio.run(hb.check_once(directory=Path(args.dir) if args.dir else None, window=args.window))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['decision']} age_s={result['age_s']} sent={result['sent']}")
    return 2 if result["decision"] in {"silent_new", "silent_known"} else 0


if __name__ == "__main__":
    sys.exit(main())
