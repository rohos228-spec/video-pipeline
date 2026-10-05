"""Structural graph hash, per-node configuration revision and stored-value schemes.

Pure functions only: no database, no provider.
"""
import copy
import hashlib
import json
import unittest
from pathlib import Path

from app.services import revisions

NODES = [
    {"id": "n_plan", "type": "plan", "position": {"x": 1, "y": 2},
     "data": {"label": "Plan", "description": "d", "modelId": "gpt-a"}},
    {"id": "n_script", "type": "script", "position": {"x": 3, "y": 4}, "data": {"label": "Script"}},
    {"id": "n_gpt", "type": "excel_gpt", "position": {"x": 5, "y": 6},
     "data": {"label": "GPT", "slotIndex": 1, "groupId": "g1"}},
]
EDGES = [
    {"id": "e0", "source": "n_plan", "target": "n_script", "sourceHandle": "out", "targetHandle": "in"},
    {"id": "e1", "source": "n_script", "target": "n_gpt", "data": {"kind": "after"}},
]


def nodes(**changes):
    out = copy.deepcopy(NODES)
    for index, fn in changes.items():
        fn(out[int(index)])
    return out


class StructureHash(unittest.TestCase):
    def test_cosmetic_changes_do_not_change_the_structure(self):
        base = revisions.structure_hash(NODES, EDGES)
        moved = copy.deepcopy(NODES)
        moved[0]["position"] = {"x": 99, "y": 99}
        moved[0]["data"]["label"] = "Renamed"
        moved[1]["data"]["description"] = "text"
        moved[2]["selected"] = True
        edges = copy.deepcopy(EDGES)
        edges[0]["id"] = "other-id"
        edges[0]["sourceHandle"] = "x"
        edges[1]["animated"] = True
        self.assertEqual(base, revisions.structure_hash(moved, edges))
        self.assertEqual(base, revisions.structure_hash(list(reversed(NODES)), list(reversed(EDGES))))

    def test_node_configuration_is_not_structure(self):
        base = revisions.structure_hash(NODES, EDGES)
        changed = copy.deepcopy(NODES)
        changed[0]["data"]["modelId"] = "gpt-b"
        changed[1]["data"]["outputMode"] = "text"
        changed[1]["data"]["promptVariant"] = "v2"
        self.assertEqual(base, revisions.structure_hash(changed, EDGES))

    def test_what_decides_which_node_runs_changes_the_structure(self):
        base = revisions.structure_hash(NODES, EDGES)
        variants = {}
        variants["node id"] = (nodes(**{"1": lambda n: n.update(id="n_script2")}), EDGES)
        variants["node type"] = (nodes(**{"1": lambda n: n.update(type="split")}), EDGES)
        variants["group"] = (nodes(**{"2": lambda n: n["data"].update(groupId="g2")}), EDGES)
        variants["slot"] = (nodes(**{"2": lambda n: n["data"].update(slotIndex=2)}), EDGES)
        variants["disabled"] = (nodes(**{"1": lambda n: n["data"].update(disabled=True)}), EDGES)
        variants["scene marker"] = (nodes(**{"2": lambda n: n["data"].update(sd_agent="world")}), EDGES)
        variants["edge removed"] = (NODES, EDGES[:1])
        variants["edge added"] = (NODES, EDGES + [{"id": "e2", "source": "n_plan", "target": "n_gpt"}])
        kind = copy.deepcopy(EDGES)
        kind[1]["data"]["kind"] = "gate"
        variants["edge kind"] = (NODES, kind)
        retarget = copy.deepcopy(EDGES)
        retarget[0]["target"] = "n_gpt"
        variants["edge target"] = (NODES, retarget)
        variants["node removed"] = (NODES[:2], EDGES[:1])
        seen = {base}
        for name, (n, e) in variants.items():
            value = revisions.structure_hash(n, e)
            self.assertNotIn(value, seen, name)
            seen.add(value)

    def test_malformed_entries_are_hashed_not_raised(self):
        value = revisions.structure_hash(NODES + [None, {"type": "x"}, "junk"], EDGES + [{"source": "a"}])
        self.assertEqual(len(value), 64)
        self.assertNotEqual(value, revisions.structure_hash(NODES, EDGES))
        self.assertEqual(revisions.structure_hash(None, None), revisions.structure_hash([], []))

    def test_graph_version_is_the_structure_hash(self):
        self.assertEqual(revisions.graph_version(NODES, EDGES), revisions.structure_hash(NODES, EDGES))


class GraphVersionSchemes(unittest.TestCase):
    def test_legacy_value_is_recognised_and_rejected_with_a_reason(self):
        legacy = revisions.legacy_graph_version(NODES, EDGES)
        self.assertNotEqual(legacy, revisions.graph_version(NODES, EDGES))
        self.assertEqual(revisions.graph_version_status(NODES, EDGES, legacy), "legacy_scheme")
        self.assertIn("legacy scheme 1", revisions.graph_mismatch_reason(NODES, EDGES, legacy))

    def test_current_changed_and_empty_values(self):
        current = revisions.graph_version(NODES, EDGES)
        self.assertEqual(revisions.graph_version_status(NODES, EDGES, current), "ok")
        self.assertIsNone(revisions.graph_mismatch_reason(NODES, EDGES, current))
        self.assertEqual(revisions.graph_version_status(NODES, EDGES[:1], current), "changed")
        self.assertIn("graph revision changed", revisions.graph_mismatch_reason(NODES, EDGES[:1], current))
        for stored in (None, "", "0" * 64, "graph-v1"):
            self.assertEqual(revisions.graph_version_status(NODES, EDGES, stored), "changed")

    def test_legacy_value_still_moves_with_configuration_but_the_new_one_does_not(self):
        changed = copy.deepcopy(NODES)
        changed[0]["data"]["label"] = "Renamed"
        self.assertNotEqual(revisions.legacy_graph_version(NODES, EDGES),
                            revisions.legacy_graph_version(changed, EDGES))
        self.assertEqual(revisions.graph_version(NODES, EDGES), revisions.graph_version(changed, EDGES))


class NodeConfigRevision(unittest.TestCase):
    def test_own_configuration_changes_it_cosmetics_and_other_nodes_do_not(self):
        base = revisions.node_config_revision(NODES[0], {})
        own = copy.deepcopy(NODES[0])
        own["data"]["modelId"] = "gpt-b"
        self.assertNotEqual(base, revisions.node_config_revision(own, {}))
        cosmetic = copy.deepcopy(NODES[0])
        cosmetic["data"]["label"] = "Another label"
        cosmetic["data"]["description"] = "x"
        cosmetic["position"] = {"x": 0, "y": 0}
        self.assertEqual(base, revisions.node_config_revision(cosmetic, {}))
        # Another node's configuration is outside this node's revision.
        meta = {"excel_gpt_nodes": {"n_gpt": {"outputMode": "text"}},
                "prompt_slot_variants": {"n_gpt": {"main": "v2"}}}
        self.assertEqual(base, revisions.node_config_revision(NODES[0], meta))

    def test_per_node_project_configuration_is_part_of_the_revision(self):
        node = NODES[2]
        base = revisions.node_config_revision(node, {})
        for meta in ({"excel_gpt_nodes": {"n_gpt": {"outputMode": "text"}}},
                     {"prompt_slot_variants": {"n_gpt": {"main": "v2"}}}):
            self.assertNotEqual(base, revisions.node_config_revision(node, meta))
        label_only = {"excel_gpt_nodes": {"n_gpt": {"label": "x"}}}
        self.assertEqual(revisions.node_config_revision(node, {"excel_gpt_nodes": {"n_gpt": {}}}),
                         revisions.node_config_revision(node, label_only))

    def test_structure_keys_are_not_double_counted_as_configuration(self):
        moved = copy.deepcopy(NODES[2])
        moved["data"]["groupId"] = "g9"
        self.assertEqual(revisions.node_config_revision(NODES[2], {}), revisions.node_config_revision(moved, {}))


class StoredValues(unittest.TestCase):
    def test_old_and_unknown_schemes_are_rejected_with_a_reason(self):
        self.assertIsNone(revisions.stored_revision_problem({"revision_scheme": revisions.REVISION_SCHEME}))
        legacy = revisions.stored_revision_problem({"topic": "a" * 64, "prompts": "b" * 64})
        self.assertIn("legacy scheme 1", legacy)
        self.assertIn("legacy scheme 1", revisions.stored_revision_problem(None))
        self.assertIn("not supported", revisions.stored_revision_problem({"revision_scheme": 99}))
        self.assertIn("legacy scheme 1", revisions.stored_revision_problem({"revision_scheme": "2"}))

    def test_unresolved_parts_never_compare_equal(self):
        first = revisions._safe("x", lambda: 1 / 0)
        second = revisions._safe("x", lambda: 1 / 0)
        self.assertNotEqual(revisions._h(first), revisions._h(second))
        self.assertEqual(first["unresolved"], "x")


class ActionLayerPlanHashesAreIndependent(unittest.TestCase):
    """The approval plan hash must not depend on revisions or on any configuration hash."""

    GOLDEN = None

    def registry(self):
        class Action:
            def fingerprint(self):
                return "fp-1"

        class Registry:
            def find(self, action_id):
                return Action() if action_id == "known" else None
        return Registry()

    def plan(self):
        from app.services.action_layer.plan import Plan, PlanStep
        return Plan([PlanStep(0, "known", {"project_id": 3}, []), PlanStep(1, "missing", {}, [0])], "text")

    def test_plan_hash_is_pinned_and_unaffected_by_revision_state(self):
        from app.services.action_layer.plan import PLAN_HASH_VERSION, plan_hash
        body = {"v": PLAN_HASH_VERSION, "steps": [
            {"i": 0, "a": "known", "p": {"project_id": 3}, "d": [], "f": "fp-1"},
            {"i": 1, "a": "missing", "p": {}, "d": [0], "f": "unknown"}]}
        expected = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        before = plan_hash(self.plan(), self.registry())
        self.assertEqual(before, expected)
        # Changing the revision scheme constants must not move it.
        original = (revisions.REVISION_SCHEME, revisions.GRAPH_SCHEME)
        revisions.REVISION_SCHEME, revisions.GRAPH_SCHEME = 77, 77
        try:
            self.assertEqual(plan_hash(self.plan(), self.registry()), expected)
        finally:
            revisions.REVISION_SCHEME, revisions.GRAPH_SCHEME = original

    def test_action_layer_does_not_import_the_revision_module(self):
        root = Path(__file__).resolve().parents[2] / "app" / "services" / "action_layer"
        for path in root.glob("*.py"):
            self.assertNotIn("revisions", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
