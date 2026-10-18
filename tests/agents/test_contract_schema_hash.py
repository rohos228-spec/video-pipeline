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
    "ws3-r3.5": "b391021322f9982fcea9f801fa8aad893cff4cc79efb6ea8863c7ce69f7be7f2",
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


def test_contract_version_covers_partial_write_stop_key_and_partial_changes() -> None:
    schema = export_contract_schemas()
    assert schema["version"] == CONTRACT_VERSION == "ws3-r3.5"
    note = next(item["note"] for item in schema["changelog"] if item["version"] == "ws3-r3.5")
    assert "partial_write" in note
    assert "project_{id}.stop" in note
    assert "partial-changes" in note
    assert "deletion_forbidden" in note
    rules = schema["rules"]
    assert "partial_write" in rules["retained_event_types"]
    assert rules["stop_key"] == "project_{id}.stop"
    assert rules["partial_changes_path"].endswith("/partial-changes")
    assert rules["block_outcomes"]["partial_write"] == "waiting_approval"
    assert rules["block_outcomes"]["deletion_forbidden"] == "failed"
    assert "partial_write" in schema["enums"]["EventType"]
