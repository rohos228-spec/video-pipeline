#!/usr/bin/env python3
"""Собрать data/knowledge_index.json для /api/knowledge/search.

Из корня репо:
  python scripts/build_knowledge_index.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.services.knowledge_index import build_index, index_path

    payload = build_index(write=True)
    print(f"entries={payload.get('count')} -> {index_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
