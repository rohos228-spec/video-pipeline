"""W6 SD: pins the contract mismatch found by the live sd_assemble run (no network, no app start).

The sd_assemble prompt asks the model for "освещение" in every op's fields; db_apply maps it to "lighting";
verify_scene_assembly only accepts fields from db_apply._ATTR_EXCEL_ROWS (+ characters) and reports the rest as
"unsupported assembly field checks", which made observe_scene_agent_gate raise. FIXED (W6 integration): lighting and
scene_lighting are known ignored fields (scene_contracts._IGNORED_ASSEMBLY_FIELDS); any other unknown field is still
unsupported. Behavioural tests: tests/observe_v1/test_scene_contracts.py (lighting ignored / other unknown unsupported).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "05_excel_gpt" / "sd_assemble.md"


def _prompt_op_fields() -> dict:
    text = PROMPT.read_text(encoding="utf-8")
    block = text[text.index('"ops": ['):]
    fields = block[block.index('"fields":'):]
    fields = fields[fields.index("{"):]
    depth = 0
    for i, ch in enumerate(fields):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(fields[:i + 1])
    raise AssertionError("fields block not closed")


def test_prompt_asks_lighting_in_op_fields():
    assert "освещение" in _prompt_op_fields()


def test_alias_resolves_to_lighting_which_verifier_does_not_cover():
    from app.services import db_apply
    fields = db_apply.normalize_fields({"освещение": "x", "место": "y"}, db_apply.FIELD_ALIASES, scope="t")
    assert "lighting" in fields
    assert "lighting" not in db_apply._ATTR_EXCEL_ROWS
    assert "lighting" not in db_apply._ATTR_FIELD_KEYS


def test_other_prompt_op_fields_are_all_covered():
    from app.services import db_apply, scene_contracts
    fields = db_apply.normalize_fields(
        {k: "v" for k in _prompt_op_fields() if k != "персонажи"}, db_apply.FIELD_ALIASES, scope="t")
    uncovered = {k for k in fields if k not in db_apply._ATTR_EXCEL_ROWS and k != "characters"}
    # the only prompt field apply does not store is lighting, and the verifier now knows it as ignored
    assert uncovered == {"lighting"}
    assert uncovered <= scene_contracts._IGNORED_ASSEMBLY_FIELDS
    assert scene_contracts._IGNORED_ASSEMBLY_FIELDS == {"lighting", "scene_lighting"}
