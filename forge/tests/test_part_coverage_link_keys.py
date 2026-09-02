#!/usr/bin/env python3
"""The assembly gate must accept the same `mapsTo.ref` spellings the spec validator accepts.

Run: python3 forge/tests/test_part_coverage_link_keys.py

`validate_sculpt_spec._detail_link_keys` registers a detail's target under BOTH the bare feature id
and the owner-prefixed `<componentId>/<featureId>` (likewise `<materialId>/<overrideId>`), and the
prefixed form is what an author reaches for once two components own a feature of the same name.
`check_part_coverage.collect_local_feature_keys` registered only the bare id, so a spec that cleared
`--strict-quality` still drew "unresolved mapsTo" warnings from the assembly gate: two gates
disagreeing about one documented field. Measured on the widebody-coupe spec, 18 warnings against a
spec the validator had accepted.

The second assertion is the one that actually bit: the lookup runs the incoming ref through `norm`,
which strips every non-alphanumeric character, so a registered key containing a literal "/" can
never match `wing-plane/wing-edge-chamfer` -- it arrives as `wingplanewingedgechamfer`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_FORGE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_FORGE_ROOT / "stage4_review"), str(_FORGE_ROOT / "stage2_spec")]

import check_part_coverage  # noqa: E402
import validate_sculpt_spec  # noqa: E402


SPEC = {
    "componentTree": [
        {
            "id": "wing-plane",
            "name": "Rear wing main plane",
            "localFeatures": [{"id": "wing-edge-chamfer", "kind": "bevel"}],
        },
        {
            "id": "hood",
            "name": "Hood",
            "localFeatures": [{"id": "wing-edge-chamfer", "kind": "bevel"}],
        },
    ],
    "materials": [
        {"id": "body-metal", "localOverrides": [{"id": "scratch-lines", "kind": "scratch"}]},
    ],
}


class AssemblyGateAcceptsValidatorLinkKeys(unittest.TestCase):
    def setUp(self) -> None:
        self.keys = check_part_coverage.collect_local_feature_keys(SPEC)

    def test_bare_feature_and_override_ids_still_resolve(self) -> None:
        for ref in ("wing-edge-chamfer", "scratch-lines", "wing-plane", "body-metal"):
            with self.subTest(ref=ref):
                self.assertIn(check_part_coverage.norm(ref), self.keys)

    def test_owner_prefixed_refs_resolve(self) -> None:
        """The exact form that produced the 18 spurious warnings."""
        for ref in (
            "wing-plane/wing-edge-chamfer",
            "hood/wing-edge-chamfer",
            "body-metal/scratch-lines",
        ):
            with self.subTest(ref=ref):
                self.assertIn(
                    check_part_coverage.norm(ref),
                    self.keys,
                    f"{ref!r} normalises to {check_part_coverage.norm(ref)!r}, which the gate must hold",
                )

    def test_no_registered_key_carries_a_separator_the_lookup_would_strip(self) -> None:
        for key in self.keys:
            self.assertEqual(key, check_part_coverage.norm(key), "key is not in normalised form")

    def test_agrees_with_the_spec_validator_on_the_same_spec(self) -> None:
        validator_keys = validate_sculpt_spec._detail_link_keys(SPEC)
        unreachable = [
            key for key in validator_keys
            if check_part_coverage.norm(key) not in self.keys
        ]
        self.assertEqual(
            unreachable, [], "validator accepts refs the assembly gate would warn about"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
