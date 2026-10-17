"""Pin sha256 замороженных файлов контура B.

Хеш считается после замены ``\\r\\n`` на ``\\n``. У текущих LF-блобов
нормализованный дайджест совпадает с прежним пином.


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
        "sha256": "7420c4d794dc8c4257a64280c8d1602daed813cb0c28bfa7c4b948513a7991c6",
        "commit": "1396ea43",
    },
    "precision_live_label_ambiguous.json": {
        "sha256": "80ce327f7b926316e79aeab80e89687c28b7193cbcae2006bada7e5938366ec2",
        "commit": "1396ea43",
    },
    "precision_live_labels_s0_s7.json": {
        "sha256": "2ef72c4cee8d9a1269b0aa49e9fbaf5c8edacfddb63408bf8ef1648dfa46c7fd",
        "commit": "19d050c2",
    },
    "precision_live_holdout.json": {
        "sha256": "e75380daf69bccd0b524cdb4759c14ca8b6756a78919cb536a1fb8b36c099de5",
        "commit": "5d1fa048",
    },
    "precision_live_holdout3.json": {
        "sha256": "2b4486b374a8f543b3f3462440b4c7eb152dd16b2320cbefa1539e32c0380b95",
        "commit": "pending",
    },
}


class FrozenDatasetError(RuntimeError):
    """Файл на диске не совпал с зафиксированным sha256."""


def file_sha256(path: Path) -> str:
    """sha256 после ``\\r\\n`` → ``\\n``. Пин не зависит от autocrlf."""
    payload = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


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
