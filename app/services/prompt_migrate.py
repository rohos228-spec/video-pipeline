"""CLI: миграция пользовательских промтов prompts/ → data/prompts/."""

from __future__ import annotations

import argparse

from app.services.prompt_paths import migrate_user_prompts_to_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate user prompts to data/prompts/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = migrate_user_prompts_to_data(dry_run=args.dry_run)
    print(stats)


if __name__ == "__main__":
    main()
