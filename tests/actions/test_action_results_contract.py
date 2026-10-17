"""Контракт результатов R2: примеры из md, схема, метаданные, html."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.orchestrator.actions.contracts import (
    CONTRACT_VERSION,
    ActionResult,
    ReportResult,
    metadata_of,
    schema_canonical_bytes,
)

_REPO = Path(__file__).resolve().parents[2]
_MD = _REPO / "docs" / "fable" / "action_results_contract.md"
_SCHEMA = _REPO / "docs" / "fable" / "action_results_contract.schema.json"

# sha256 канонического экспорта закреплён за CONTRACT_VERSION.
_SCHEMA_SHA256 = {
    "ws2-r2.0": "850ad63329195bd6d58b7b313550e2cc3994521616c57476df655ddd7c1337aa",
}


def _examples() -> list[dict]:
    text = _MD.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    assert blocks, "в action_results_contract.md нет JSON-примеров"
    return [json.loads(block) for block in blocks]


def test_markdown_examples_match_models() -> None:
    examples = _examples()
    assert len(examples) >= 8
    for example in examples:
        ActionResult.model_validate(example)


def test_schema_export_sha256_is_pinned_to_version() -> None:
    raw = schema_canonical_bytes()
    assert _SCHEMA.read_bytes() == raw
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)
    assert payload["version"] == CONTRACT_VERSION
    assert _SCHEMA_SHA256[CONTRACT_VERSION] == digest


def test_unknown_name_is_paid_and_destructive() -> None:
    meta = metadata_of("not_in_registry")
    assert meta.name == "not_in_registry"
    assert meta.paid is True
    assert meta.destructive is True
    assert meta.read_only is False
    assert meta.needs_approval is True


def test_report_html_with_script_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReportResult(
            report_id=1,
            html="<p>ok</p><script>alert(1)</script>",
            version=1,
        )


def test_revert_node_to_git_has_medium_risk() -> None:
    meta = metadata_of("revert_node_to_git")
    assert meta.risk == "medium"
    assert meta.paid is False
    assert meta.destructive is False
    assert meta.needs_approval is False


def test_prompt_create_master_is_destructive() -> None:
    meta = metadata_of("prompt.create_master")
    assert meta.destructive is True
    assert meta.needs_approval is True


def test_suggest_scene_variants_tier_is_paid() -> None:
    """Опечатка paid+paid живёт только в документе #344."""

    meta = metadata_of("montage.suggest_scene_variants")
    assert meta.paid is True
    assert meta.destructive is False
    assert meta.risk == "high"
