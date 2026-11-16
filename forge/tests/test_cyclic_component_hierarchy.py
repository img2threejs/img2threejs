from __future__ import annotations

import copy
import inspect
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
from validate_sculpt_spec import validate_components, validate_spec


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


    def test_self_cycle_with_two_feeders_is_reported_once(self):
        spec = spec_with("box", "assembled-solid")
        body, part = spec["componentTree"]
        body["parent"] = "body"
        feeder = copy.deepcopy(part)
        feeder["id"] = "another_part"
        feeder["name"] = "Another Part"
        spec["componentTree"].append(feeder)
        errors, _ = validate_spec(spec)
        cycles = [error for error in errors if "component parent cycle detected" in error]
        self.assertEqual(cycles, ["component parent cycle detected: 'body' -> 'body'"])

    def test_two_node_cycle_with_feeder_is_reported_once(self):
        spec = spec_with("box", "assembled-solid")
        body, part = spec["componentTree"]
        body["parent"] = "part"
        feeder = copy.deepcopy(part)
        feeder["id"] = "another_part"
        feeder["name"] = "Another Part"
        spec["componentTree"].append(feeder)
        errors, _ = validate_spec(spec)
        cycles = [error for error in errors if "component parent cycle detected" in error]
        self.assertEqual(cycles, ["component parent cycle detected: 'body' -> 'part' -> 'body'"])

    @staticmethod
    def _reverse_ordered_valid_spec(depth: int) -> dict:
        spec = spec_with("box", "assembled-solid")
        body, part = spec["componentTree"]
        children = []
        for index in range(depth - 1):
            node = copy.deepcopy(part)
            node["id"] = f"node_{index}"
            node["name"] = f"Node {index}"
            node["parent"] = f"node_{index + 1}" if index < depth - 2 else "body"
            children.append(node)
        spec["componentTree"] = children + [body]
        return spec

    def test_reverse_ordered_1800_node_spec_validates_and_emits_parent_first(self):
        spec = self._reverse_ordered_valid_spec(1800)
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [])
        included = generator.filter_components_for_pass(spec, spec["componentTree"], "blockout")
        self.assertEqual(len(included), 1800)
        expected = ["body"] + [f"node_{index}" for index in range(1798, -1, -1)]
        self.assertEqual([node["id"] for node in included], expected)

    def test_validator_ancestor_hops_are_linear_not_quadratic(self):
        # Count actual parent-edge traversals, not elapsed wall-clock time.
        # With 200 reverse-ordered nodes: 199 after caching vs 19,900 before.
        source, start_line = inspect.getsourcelines(validate_components)
        hop_lines = [
            start_line + offset for offset, line in enumerate(source)
            if line.strip() == "curr = parent_map.get(curr)"
        ]
        self.assertEqual(len(hop_lines), 1)
        hop_line = hop_lines[0]
        hops = 0

        def trace(frame, event, arg):
            nonlocal hops
            if frame.f_code is not validate_components.__code__:
                return None
            if event == "line" and frame.f_lineno == hop_line:
                hops += 1
            return trace

        previous_trace = sys.gettrace()
        try:
            sys.settrace(trace)
            errors, _ = validate_spec(self._reverse_ordered_valid_spec(200))
        finally:
            sys.settrace(previous_trace)
        self.assertEqual(errors, [])
        self.assertEqual(hops, 199)

if __name__ == "__main__":
    unittest.main()
