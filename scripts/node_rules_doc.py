#!/usr/bin/env python3
"""Пересобрать docs/NODE_GROUP_RULES.md из app/services/node_rules.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.node_rules import rules_markdown  # noqa: E402

DOC = ROOT / "docs" / "NODE_GROUP_RULES.md"


def main() -> None:
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(rules_markdown(), encoding="utf-8")
    print(DOC)


if __name__ == "__main__":
    main()
