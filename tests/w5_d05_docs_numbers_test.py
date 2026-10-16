import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _coverage():
    from app.services.node_coverage import node_coverage
    return node_coverage()


def test_node_coverage_doc_states_the_registry_recovery_count():
    text = (DOCS / "NODE_COVERAGE.md").read_text(encoding="utf-8")
    m = re.search(r"автоматическое восстановление у (\d+):", text)
    assert m and int(m.group(1)) == _coverage()["summary"]["with_automatic_recovery"] == 17


def test_node_coverage_doc_lists_exactly_the_profile_compatible_nodes():
    from app.orchestrator.node_spec import registered_capability_specs
    text = (DOCS / "NODE_COVERAGE.md").read_text(encoding="utf-8")
    start = text.index("Совместимость с профилями (`profile_compatible`")
    sentence = text[start:text.index(".", start + 150)]
    named = set(re.findall(r"`([a-z_]+)`", sentence.split("animation_prompts")[0]))
    specs = registered_capability_specs()
    named &= set(specs)
    actual = {t for t, s in specs.items() if s.profile_compatible}
    assert named == actual, (named, actual)


def test_split_and_image_prompts_rows_no_longer_claim_a_forbidden_code():
    text = (DOCS / "NODE_COVERAGE.md").read_text(encoding="utf-8")
    for node in ("split", "image_prompts"):
        row = next(line for line in text.splitlines() if line.startswith(f"| `{node}` | под управлением |")
                   and "Не меньше" in line or line.startswith(f"| `{node}` | под управлением | Набор"))
        assert "destructive_rewrite" not in row and "native_skips_filled_rows" not in row
        assert "`retry_same` и смена профиля" in row
    rows = {r["node_type"]: r for r in _coverage()["nodes"]}
    for node in ("split", "image_prompts"):
        assert rows[node]["forbidden"] is None and rows[node]["recovery"]["actions"] == ["retry_same", "retry_profile"]
