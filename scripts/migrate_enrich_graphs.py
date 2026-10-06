"""Перенос графов со старым типом enrich_N на excel_gpt (C5/C6).

По умолчанию сухой проход: печатает, что изменилось бы, и ничего не пишет.

    python scripts/migrate_enrich_graphs.py              # сухой проход
    python scripts/migrate_enrich_graphs.py --apply      # записать (запись отката лежит в meta["graph_migrations"])
    python scripts/migrate_enrich_graphs.py --rollback   # вернуть исходные ноды из записей отката

Перед --apply сделайте копию data/state.db.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.db import session_scope
from app.services.graph_migrations import migrate_stores, rollback_stores


async def _main(apply: bool, rollback: bool) -> dict:
    async with session_scope() as session:
        if rollback:
            return await rollback_stores(session)
        return await migrate_stores(session, dry_run=not apply)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="записать изменения")
    group.add_argument("--rollback", action="store_true", help="вернуть исходные ноды")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_main(args.apply, args.rollback)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
