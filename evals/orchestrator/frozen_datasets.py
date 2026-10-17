"""Pin sha256 замороженных файлов контура B.

Запросы и сплит — ``a79f70a9``. Перенос меток — ``1396ea43``.
Уточнение s0–s7 — ``19d050c2``. Расхождение хеша — ошибка, файл не переписывается.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Коммит указан у правила, которое заморозило файл.
FROZEN_SHA256: dict[str, dict[str, str]] = {
    "precision_live_queries.json": {
        "sha256": "d1bad8208aed10b2517ca30c3b41d51abfa2aec8345632b41fa8b96db05d17c1",
        "commit": "a79f70a9",
    },
    "precision_live_split.json": {
        "sha256": "5100543355874e70397a2a804d11630225dd202144fe3267cfb7650854da118f",
        "commit": "a79f70a9",
    },
    "precision_live_labels_transferred.json": {
        "sha256": "f3b72c16e24368e92e47f4919a6cede156d35004ec21dd811000885899986098",
        "commit": "1396ea43",
    },
    "precision_live_label_ambiguous.json": {
        "sha256": "80ce327f7b926316e79aeab80e89687c28b7193cbcae2006bada7e5938366ec2",
        "commit": "1396ea43",
    },
    "precision_live_labels_s0_s7.json": {
        "sha256": "12bdb1ee6e785593783fe7fb60f25073d9b445d36563e5d5da5e1938132ca0ea",
        "commit": "19d050c2",
    },
}


class FrozenDatasetError(RuntimeError):
    """Файл на диске не совпал с зафиксированным sha256."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_frozen(path: Path) -> str:
    pin = FROZEN_SHA256.get(path.name)
    if pin is None:
        raise FrozenDatasetError(f"{path.name} не входит в замороженный набор")
    digest = file_sha256(path)
    if digest != pin["sha256"]:
        raise FrozenDatasetError(
            f"{path.name}: sha256 {digest} != {pin['sha256']} (коммит {pin['commit']})"
        )
    return digest
