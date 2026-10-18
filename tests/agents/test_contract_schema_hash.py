"""Хеш экспорта контракта закреплён за CONTRACT_VERSION.

Смена схемы без смены версии ломает пин. Новая версия без новой строки
в ``_EXPORT_SHA256`` тоже падает.
"""

from __future__ import annotations

import hashlib
import json

from app.orchestrator.contracts import CONTRACT_VERSION, export_contract_schemas

_EXPORT_SHA256 = {
    "ws3-r3.3.1": "a8880f1425ab20a266111455a8703d863f54142af242129e60bec8d5674e9bfa",
    "ws3-r3.4": "7419fb0e4ec6a544101e0417910882992049b5b068ecb7d950c16830c2c6f4e1",
}


def _canonical_export() -> tuple[str, str]:
    schema = export_contract_schemas()
    version = str(schema["version"])
    blob = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return version, digest


def test_contract_export_sha256_is_pinned_to_version() -> None:
    version, digest = _canonical_export()
    assert version == CONTRACT_VERSION
    assert _EXPORT_SHA256[version] == digest
