#!/usr/bin/env python3
"""Parent-local frame contract: the rebase converter and the frame-sanity gate.

The whimsical-hearth-house incident: a machine-authored spec placed every
child's `transform.position` (and `attachment.localStart/localEnd`) in
object-frame absolute coordinates. The generator correctly applies positions
as parent-local (grimoire/readiness/joint_attachment.md), so every child was
displaced by its parent's own offset and the model rendered as floating
parts — while `validate_sculpt_spec.py --strict-quality` passed the spec.

Two defenses under test:
- `rebase_component_frames.py` converts an object-frame spec to the
  parent-local contract (never in place, refuses rotated parents, reports
  every changed field).
- `validate_component_frame_sanity` flags implausible parent-local offsets as
  `quality:` warnings so --strict-quality catches the next object-frame spec
  at validation time, not at render time.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

if __package__:
    from .showcase_test_support import showcase_root
else:
    from showcase_test_support import showcase_root

ROOT = Path(__file__).resolve().parent.parent


def import_frame_modules():
    module_names = ("rebase_component_frames", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec")]
    try:
        import rebase_component_frames
        import validate_sculpt_spec
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return rebase_component_frames, validate_sculpt_spec


REBASE, VALIDATE = import_frame_modules()


def component(
    component_id: str,
    parent: str | None,
    position: list[float],
    dims: tuple[float, float, float],
    *,
    primitive: str = "box",
    rotation: list[float] | None = None,
    attachment: dict | None = None,
) -> dict:
    return {
        "id": component_id,
        "parent": parent,
        "primitive": primitive,
        "dimensions": {"width": dims[0], "height": dims[1], "depth": dims[2]},
        "transform": {
            "position": list(position),
            "rotation": rotation or [0.0, 0.0, 0.0],
            "scale": [1, 1, 1],
        },
        **({"attachment": attachment} if attachment else {}),
    }


def object_frame_spec() -> dict:
    """base at y 0.25 carrying a body at y 2.5 carrying a cap at y 5.2 —
    all three authored in object-frame absolute coordinates."""
    return {
        "targetId": "frame-fixture",
        "componentTree": [
            component("root", None, [0, 0, 0], (1, 1, 1)),
            component("base", "root", [0, 0.25, 0], (10, 0.5, 8)),
            component(
                "body",
                "base",
                [0, 2.5, 0],
                (4, 4, 4),
                attachment={
                    "parentSocket": "base-surface",
                    "localStart": [0, 2.5, 0],
                    "localEnd": [0, 2.54, 0],
                },
            ),
            component("cap", "body", [0, 5.2, 0], (1, 1, 1)),
        ],
    }


class RebaseComponentFramesTest(unittest.TestCase):
    def test_rebases_positions_and_attachments_to_parent_local(self) -> None:
        rebased, report = REBASE.rebase_component_frames(object_frame_spec())
        by_id = {c["id"]: c for c in rebased["componentTree"]}
        # child of root: unchanged (root sits at the origin)
        self.assertEqual(by_id["base"]["transform"]["position"], [0, 0.25, 0])
        # each deeper child loses exactly its parent's object-frame offset
        self.assertEqual(by_id["body"]["transform"]["position"], [0.0, 2.25, 0.0])
        self.assertEqual(by_id["cap"]["transform"]["position"], [0.0, 2.7, 0.0])
        self.assertEqual(by_id["body"]["attachment"]["localStart"], [0.0, 2.25, 0.0])
        self.assertEqual(by_id["body"]["attachment"]["localEnd"], [0.0, 2.29, 0.0])
        changed = {(change["componentId"], change["field"]) for change in report["changes"]}
        self.assertIn(("body", "transform.position"), changed)
        self.assertIn(("body", "attachment.localStart"), changed)
        self.assertIn(("cap", "transform.position"), changed)

    def test_input_spec_is_not_mutated(self) -> None:
        spec = object_frame_spec()
        snapshot = json.dumps(spec, sort_keys=True)
        REBASE.rebase_component_frames(spec)
        self.assertEqual(json.dumps(spec, sort_keys=True), snapshot)

    def test_refuses_rotated_parent(self) -> None:
        spec = object_frame_spec()
        spec["componentTree"][2]["transform"]["rotation"] = [0.0, 0.4, 0.0]
        with self.assertRaisesRegex(ValueError, "non-zero rotation"):
            REBASE.rebase_component_frames(spec)

    def test_refuses_unknown_parent(self) -> None:
        spec = object_frame_spec()
        spec["componentTree"][3]["parent"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown parent"):
            REBASE.rebase_component_frames(spec)

    def test_cli_refuses_in_place_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps(object_frame_spec()), encoding="utf-8")
            exit_code = REBASE.main(
                [
                    "--input",
                    str(spec_path),
                    "--output",
                    str(spec_path),
                    "--report",
                    str(Path(tmp) / "report.json"),
                ]
            )
        self.assertEqual(exit_code, 2)


class FrameSanityGateTest(unittest.TestCase):
    def frame_warnings(self, spec: dict) -> list[str]:
        warnings: list[str] = []
        VALIDATE.validate_component_frame_sanity(spec, warnings)
        return warnings

    def test_object_frame_spec_is_flagged(self) -> None:
        warnings = self.frame_warnings(object_frame_spec())
        self.assertTrue(warnings, "object-frame cap offset must be flagged")
        self.assertTrue(all(w.startswith("quality: frame-sanity:") for w in warnings))
        self.assertIn("rebase_component_frames.py", warnings[0])
        self.assertIn("'cap'", warnings[0])

    def test_strict_quality_escalates_the_warning(self) -> None:
        # the quality: prefix is the contract that --strict-quality escalates
        errors, warnings = VALIDATE.validate_spec(object_frame_spec())
        self.assertTrue(any(w.startswith("quality: frame-sanity:") for w in warnings))

    def test_rebased_spec_is_clean(self) -> None:
        rebased, _ = REBASE.rebase_component_frames(object_frame_spec())
        self.assertEqual(self.frame_warnings(rebased), [])

    def test_children_of_the_root_container_are_exempt(self) -> None:
        # the unitless 1x1x1 root must not make every top-level part a finding
        spec = {
            "componentTree": [
                component("root", None, [0, 0, 0], (1, 1, 1)),
                component("base", "root", [0, 40.0, 0], (10, 0.5, 8)),
            ]
        }
        self.assertEqual(self.frame_warnings(spec), [])

    def test_base_pivot_parent_allows_full_extent_reach(self) -> None:
        # a cone capping a 5.2-tall cylinder: the cylinder's pivot sits at its
        # attachment localStart (its base), so the cone's legitimate local
        # offset is the tower's FULL height plus its own half extent.
        spec = {
            "componentTree": [
                component("root", None, [0, 0, 0], (1, 1, 1)),
                component("island", "root", [0, 0.2, 0], (10, 0.5, 8), primitive="cylinder"),
                component("tower", "island", [2.9, 2.5, 0.2], (2.4, 5.2, 2.4), primitive="cylinder"),
                component("tower-roof", "tower", [0, 6.2, 0], (3.0, 2.4, 3.0), primitive="cone"),
            ]
        }
        self.assertEqual(self.frame_warnings(spec), [])
        # ...but a center-pivoted box parent with the same offset is flagged
        spec["componentTree"][2]["primitive"] = "box"
        self.assertTrue(self.frame_warnings(spec))

    def test_missing_dimensions_are_skipped(self) -> None:
        spec = object_frame_spec()
        del spec["componentTree"][3]["dimensions"]
        offenders = [w for w in self.frame_warnings(spec) if "'cap'" in w]
        self.assertEqual(offenders, [])


class ShippedSpecsStayGreenTest(unittest.TestCase):
    def test_shipped_showcase_specs_have_no_frame_sanity_findings(self) -> None:
        root = showcase_root()
        spec_paths = sorted(root.glob("src/demos/*/object-sculpt-spec.json"))
        if not spec_paths:
            raise unittest.SkipTest("showcase checkout has no object-sculpt-spec.json demos")
        for spec_path in spec_paths:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            warnings: list[str] = []
            VALIDATE.validate_component_frame_sanity(spec, warnings)
            self.assertEqual(
                warnings, [], f"conforming shipped spec regressed: {spec_path.parent.name}"
            )


if __name__ == "__main__":
    unittest.main()
