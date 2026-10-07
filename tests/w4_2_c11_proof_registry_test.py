"""W4-2 / C11 и C03: реестр «подтверждено прогоном» по 24 нодам и честная запись способности `images`.

Класс доказательства U: читаются реестр способностей, исходный код реестра и markdown-документы.
Ни сети, ни базы, ни платных вызовов.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.services.decision_v1  # noqa: F401  (реестры)
from app.orchestrator.node_spec import capability_spec_for, coverage_level
from app.services import node_capabilities as nc
from app.services.node_coverage import node_coverage, registry_node_types

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "NODE_COVERAGE.md").read_text(encoding="utf-8")
LIVE = (ROOT / "docs" / "LIVE_RUNS.md").read_text(encoding="utf-8")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_the_proof_registry_covers_exactly_the_24_registered_node_types():
    assert set(nc.NODE_PROOF) == set(registry_node_types())
    assert len(nc.NODE_PROOF) == 24


def test_the_two_columns_are_the_data_level_and_the_run_proof():
    rows = {r["node_type"]: r for r in nc.node_proof_rows()}
    assert all(r["complete"] for r in rows.values())
    for node_type, row in rows.items():
        assert row["level"] == coverage_level(capability_spec_for(node_type)), node_type
    summary = node_coverage()["summary"]
    got = {lvl: sum(r["level"] == lvl for r in rows.values()) for lvl in ("managed", "observed", "human_only")}
    assert got == {"managed": summary["managed"], "observed": summary["observed"],
                   "human_only": summary["human_only"]}
    assert (summary["managed"], summary["observed"], summary["human_only"]) == (14, 9, 1)


def test_the_table_in_the_doc_is_exactly_the_generated_one():
    start = DOC.index("## Подтверждено по нодам")
    block = DOC[start:DOC.index("\n## ", start + 5)]
    assert nc.render_proof_table() in block, "docs/NODE_COVERAGE.md разошёлся с render_proof_table(): перегенерируйте таблицу"


@pytest.mark.parametrize("node_type", sorted(nc.NODE_PROOF))
def test_a_node_without_a_live_run_is_marked_dry_only_with_a_reason(node_type):
    entry, row = nc.NODE_PROOF[node_type], {r["node_type"]: r for r in nc.node_proof_rows()}[node_type]
    assert re.search(r"\b[UDLH]\b", entry["proof"])
    if entry["live_node"]:
        assert row["missing"] == "-"
        assert all(d in entry["proof"] and d in LIVE for d in entry["live_node"])
        assert re.search(r"\bL\b", entry["proof"])
    else:
        assert row["missing"].startswith("только сухо: ")
        assert len(entry["why_no_live"].split()) >= 5, "причина должна быть предложением, не заглушкой"
        # L без пометки «шов» нельзя приписать ноде, у которой живого прогона самой ноды нет
        assert not re.search(r"\bL\b", re.sub(r"шов [^;()]*?L \d{4}-\d{2}-\d{2}", "", entry["proof"]))
    for date in entry["live_seam"]:
        assert date in entry["proof"] and date in LIVE


def test_the_doc_never_claims_a_live_check_in_the_proof_table():
    start = DOC.index("## Подтверждено по нодам")
    block = DOC[start:DOC.index("\n## ", start + 5)]
    assert "проверено вживую" not in block.lower()


def test_images_claim_matches_the_real_check():
    spec = capability_spec_for("images")
    text = " ".join(spec.verifies)
    assert "существует и читается (проверка артефакта)" not in text
    for needed in ("привязана к этому кадру", "декодируется", "число картинок", "Качество картинки не оценивается"):
        assert needed in text, needed
    assert spec.verifier == "media_result"
    assert "Файл картинки существует и читается; журнал платных задач" not in DOC
    from app.services import media_verification
    assert "images" in media_verification.MEDIA_NODE_TYPES


def test_node_capabilities_source_has_no_leftover_unproven_images_claim():
    source = (ROOT / "app" / "services" / "node_capabilities.py").read_text(encoding="utf-8")
    assert "Файл картинки существует и читается (проверка артефакта)" not in source
