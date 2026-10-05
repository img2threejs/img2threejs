"""PLAN_1.5 §5.2 Half A — the Joint Admission Gate's five semantic checks.

Every check gets the real 49-bone template as its passing fixture and a deliberate mutation as its
failing one, because a gate nobody has watched reject something is not a gate yet.

Two readings are recorded here rather than left implicit, since neither is stated outright in the
plan and both change what the gate does:

- `MONOTONIC_CHAIN` compares DIRECTION, not distance from the root. Implemented as
  distance-from-root it rejected the correct template on ten bones: a clavicle reaches up to the
  shoulder and the upper arm then hangs back down toward the hips, so euclidean reach is
  legitimately non-monotonic for any chain that goes out and then down.
- `PROPORTION_LIMIT` is a fraction of the skeleton's own height rather than head units, because
  the rig carries no head unit and requiring `anatomy.proportions` would reject a humanoid authored
  with no anatomy block outright.

WHERE THE FIXTURE COMES FROM. It used to be built by shelling out to `new_sculpt_spec.py
--character`. That template is a plugin's now, so the fixture is the FROZEN spec in
`fixtures/oracle-character/` -- the same 49-bone skeleton, the same bytes, captured before anything
moved. It is a better fixture than the subprocess was: it cannot drift when the plugin changes, and
this gate's subject is content ARRIVING from a provider, which is exactly what a frozen artifact
models. The gate itself stays in the base and must: `rig` is not in `BASE_OWNED`, so a plugin writes
it wholesale and the merge admits it opaquely -- these five checks are the base's only look at it.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

_FORGE = Path(__file__).resolve().parents[1]
FROZEN_SPEC = Path(__file__).resolve().parent / "fixtures" / "oracle-character" / "spec.json"
sys.path.insert(0, str(_FORGE / "stage2_spec"))

from validate_sculpt_spec import (  # noqa: E402
    POOL_FLOOR_MIN_BONES,
    SYMMETRY_PARITY_TOLERANCE,
    _mirror_partner,
    validate_rig_admission,
)


def _tags(errors: list[str]) -> set[str]:
    return {message.split(":")[0] for message in errors}


class JointAdmissionGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))
        # If the fixture ever stopped carrying a skeleton, every rejection test below would still
        # pass -- against a spec with nothing to reject.
        assert len(cls.base["rig"]["bones"]) == 49, "the fixture lost its skeleton"

    def _run(self, mutate=None) -> tuple[dict, set[str], list[str]]:
        spec = copy.deepcopy(self.base)
        if mutate:
            mutate(spec)
        errors: list[str] = []
        warnings: list[str] = []
        validate_rig_admission(spec, errors, warnings)
        return spec, _tags(errors), warnings

    def _bones(self, spec: dict) -> dict:
        return {b["id"]: b for b in spec["rig"]["bones"]}

    # ---- the passing fixture, shared by all five ----
    def test_the_real_template_passes_every_check(self) -> None:
        _, tags, warnings = self._run()
        self.assertEqual(tags, set(), "the real 49-bone template must satisfy the gate")
        self.assertEqual(warnings, [])

    def test_pivot_track_is_a_no_op(self) -> None:
        """No `rig` key means an object spec, which this gate must not touch at all."""
        spec = copy.deepcopy(self.base)
        spec.pop("rig")
        errors: list[str] = []
        warnings: list[str] = []
        validate_rig_admission(spec, errors, warnings)
        self.assertEqual((errors, warnings), ([], []))

    # ---- NAME_UNIQUENESS ----
    def test_name_uniqueness_rejects_a_duplicate_id(self) -> None:
        _, tags, _ = self._run(lambda s: s["rig"]["bones"].append(dict(s["rig"]["bones"][3])))
        self.assertIn("NAME_UNIQUENESS", tags)

    def test_name_uniqueness_rejects_two_roots(self) -> None:
        _, tags, _ = self._run(lambda s: s["rig"]["bones"][5].update(parent=None))
        self.assertIn("NAME_UNIQUENESS", tags)

    def test_name_uniqueness_rejects_a_dangling_parent(self) -> None:
        _, tags, _ = self._run(lambda s: s["rig"]["bones"][7].update(parent="ghost"))
        self.assertIn("NAME_UNIQUENESS", tags)

    # ---- POOL_FLOOR ----
    def test_pool_floor_rejects_a_skeleton_below_four_bones(self) -> None:
        _, tags, _ = self._run(
            lambda s: s["rig"].update(bones=s["rig"]["bones"][: POOL_FLOOR_MIN_BONES - 1]))
        self.assertIn("POOL_FLOOR", tags)

    # ---- MONOTONIC_CHAIN ----
    def test_monotonic_chain_rejects_a_reversed_bone(self) -> None:
        def reverse_forearm(spec: dict) -> None:
            bones = self._bones(spec)
            upper = bones["upper-arm-l"]
            delta = [upper["tipPos"][i] - upper["jointPos"][i] for i in range(3)]
            joint = bones["forearm-l"]["jointPos"]
            bones["forearm-l"]["tipPos"] = [joint[i] - delta[i] for i in range(3)]

        _, tags, _ = self._run(reverse_forearm)
        self.assertIn("MONOTONIC_CHAIN", tags)

    def test_monotonic_chain_accepts_a_thumb_at_right_angles_to_its_palm(self) -> None:
        """The threshold is deliberately generous. A thumb sits near 90 degrees to the hand and
        is not folded back; only a substantially inverted bone should fail."""
        _, tags, _ = self._run()
        self.assertNotIn("MONOTONIC_CHAIN", tags)

    # ---- PROPORTION_LIMIT ----
    def test_proportion_limit_rejects_an_oversized_bone(self) -> None:
        def giant_femur(spec: dict) -> None:
            bones = self._bones(spec)
            joint = bones["thigh-l"]["jointPos"]
            bones["thigh-l"]["tipPos"] = [joint[0], joint[1] - 3.0, joint[2]]

        _, tags, _ = self._run(giant_femur)
        self.assertIn("PROPORTION_LIMIT", tags)

    # ---- SYMMETRY_PARITY: snaps, does NOT reject ----
    def test_symmetry_parity_snaps_instead_of_rejecting(self) -> None:
        def skew_right_arm(spec: dict) -> None:
            bones = self._bones(spec)
            bones["upper-arm-r"]["jointPos"] = [
                bones["upper-arm-r"]["jointPos"][0] + 0.4, 0.9, 0.3]

        spec, tags, warnings = self._run(skew_right_arm)
        self.assertNotIn("SYMMETRY_PARITY", tags, "§5.2 says snap on fail, not reject")
        self.assertTrue(any("SYMMETRY_PARITY" in w for w in warnings))
        bones = self._bones(spec)
        left = bones["upper-arm-l"]["jointPos"]
        right = bones["upper-arm-r"]["jointPos"]
        for axis, (expected, actual) in enumerate(zip([-left[0], left[1], left[2]], right)):
            self.assertAlmostEqual(expected, actual, places=4,
                                   msg=f"axis {axis} was not snapped to the mirror")

    def test_symmetry_parity_handles_a_side_marker_in_the_middle_of_an_id(self) -> None:
        """Digit ids carry the side in the middle (`thumb-l-1`). Matching only a trailing `-l`
        would silently skip all thirty phalanges — most of the skeleton."""
        self.assertEqual(_mirror_partner("upper-arm-l"), "upper-arm-r")
        self.assertEqual(_mirror_partner("thumb-l-1"), "thumb-r-1")
        self.assertIsNone(_mirror_partner("chest"))
        self.assertIsNone(_mirror_partner("upper-arm-r"))

        def skew_digit(spec: dict) -> None:
            self._bones(spec)["thumb-r-2"]["jointPos"] = [0.1, 0.5, 0.5]

        _, tags, warnings = self._run(skew_digit)
        self.assertNotIn("SYMMETRY_PARITY", tags)
        self.assertTrue(any("thumb-r-2" in w for w in warnings))

    def test_symmetry_tolerance_is_the_documented_value(self) -> None:
        self.assertEqual(SYMMETRY_PARITY_TOLERANCE, 0.05)


if __name__ == "__main__":
    unittest.main()
