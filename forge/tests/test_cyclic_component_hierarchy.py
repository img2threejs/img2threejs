from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_threejs_factory as generator
from test_attachment_does_not_override_primitive import spec_with
from validate_sculpt_spec import validate_spec


class TestCyclicComponentHierarchy(unittest.TestCase):
    def test_self_parent_cycle_caught_by_validator(self):
        spec = spec_with("box", "assembled-solid")
        spec["componentTree"][1]["parent"] = "part"  # self-parent
        errors, _ = validate_spec(spec)
        cycle_errors = [e for e in errors if "component parent cycle detected" in e]
        self.assertTrue(len(cycle_errors) > 0)
        self.assertIn("'part' -> 'part'", cycle_errors[0])

    def test_two_node_cycle_caught_by_validator(self):
        spec = spec_with("box", "assembled-solid")
        spec["componentTree"][0]["parent"] = "part"
        spec["componentTree"][1]["parent"] = "body"
        errors, _ = validate_spec(spec)
        cycle_errors = [e for e in errors if "component parent cycle detected" in e]
        self.assertTrue(len(cycle_errors) > 0)
        self.assertIn("'part'", cycle_errors[0])
        self.assertIn("'body'", cycle_errors[0])

    def test_generator_raises_value_error_on_parent_cycle(self):
        spec = spec_with("box", "assembled-solid")
        spec["componentTree"][1]["parent"] = "part"
        # Bypassing spec validation or calling filter_components_for_pass directly
        with self.assertRaises(ValueError) as ctx:
            generator.filter_components_for_pass(spec, spec["componentTree"], "blockout")
        self.assertIn("Cyclic component parent hierarchy detected", str(ctx.exception))
        self.assertIn("'part' -> 'part'", str(ctx.exception))

        # End-to-end generate also rejects with validation error
        with self.assertRaises(ValueError) as ctx_gen:
            generator.generate(spec, "blockout")
        self.assertIn("spec validation failed", str(ctx_gen.exception))
        self.assertIn("component parent cycle detected", str(ctx_gen.exception))

    def test_deep_component_hierarchy_does_not_exhaust_recursion(self):
        spec = spec_with("box", "assembled-solid")
        components = []
        depth = 1200
        for i in range(depth):
            cid = f"node_{i}"
            pid = f"node_{i-1}" if i > 0 else None
            components.append({
                "id": cid,
                "name": f"Node {i}",
                "level": "macro",
                "role": "part",
                "primitive": "box",
                "topologyClass": "assembled-solid",
                "parent": pid,
            })
        spec["componentTree"] = components
        # Should filter without RecursionError
        filtered = generator.filter_components_for_pass(spec, components, "blockout")
        self.assertEqual(len(filtered), depth)
        self.assertEqual(filtered[0]["id"], "node_0")
        self.assertEqual(filtered[-1]["id"], f"node_{depth-1}")

    def test_forward_references_produce_parent_first_order(self):
        spec = spec_with("box", "assembled-solid")
        components = [
            {"id": "child", "level": "macro", "parent": "parent_node"},
            {"id": "parent_node", "level": "macro", "parent": "root_node"},
            {"id": "root_node", "level": "macro", "parent": None},
        ]
        spec["componentTree"] = components
        filtered = generator.filter_components_for_pass(spec, components, "blockout")
        emitted_ids = [c["id"] for c in filtered]
        self.assertEqual(emitted_ids, ["root_node", "parent_node", "child"])


if __name__ == "__main__":
    unittest.main()
