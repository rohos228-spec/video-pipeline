"""CLI: восстановление промтов из git stash studio-обновлений."""

from __future__ import annotations

import argparse
import json

from app.services.prompt_paths import restore_prompts_from_stashes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore prompts from studio update git stashes"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = restore_prompts_from_stashes(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(f"Stash scanned: {report['stashes_scanned']}")
    print(f"Files restored: {report['files_restored']}")
    print(f"Files skipped (newer/exists): {report['files_skipped']}")
    for block in report["by_stash"]:
        restored = block.get("restored") or []
        if not restored:
            continue
        print(f"\n{block['stash']}: {block['message']}")
        for rel in restored:
            print(f"  + {rel}")


if __name__ == "__main__":
    main()
