"""Атомарная запись файла: временный файл в том же каталоге и os.replace.

Прямой open('w') по назначению запрещён: последний писатель не должен
затирать файл без версии.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_text(dest: Path, text: str, *, version: int) -> None:
    """Пишет text и version во временный файл и публикует его через os.replace."""
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps({"version": int(version), "text": text}, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(tmp, target)
