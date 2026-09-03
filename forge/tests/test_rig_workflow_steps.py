#!/usr/bin/env python3
"""The Stage R checklist -- that it exists, and that its ORDER cannot drift.

This file exists because of a specific failure: every forge/stage5_rig/ module was callable and
nothing in the workflow ever told anyone to call one. `next.py` walks the checklist, so a gate
absent from the checklist is a gate that never runs, and a gate that never runs reports a clean
verdict forever.

The order assertions are the load-bearing half. "Repair, then freeze, then bind, then verify" is
not a stylistic preference -- moving the freeze after the bind would let the bind rewrite vertices
and then certify the rewritten buffer, so the manifest would attest to the damage instead of
catching it. A reordering like that is invisible in review and total in effect, which is exactly
the class of defect this suite is for.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_shared"))

# Profiles resolve through the domain registry, which reads installed plugins from IMG2_HOME.
# Pinned to an empty temp home so a developer's real ~/.img2 (malformed or colliding domain.json)
# can never fail these tests -- the test_search_specs.py pattern.
_TMP_HOME: tempfile.TemporaryDirectory | None = None
_OLD_HOME: str | None = None


def setUpModule() -> None:
    global _TMP_HOME, _OLD_HOME
    _TMP_HOME = tempfile.TemporaryDirectory()
    _OLD_HOME = os.environ.get("IMG2_HOME")
    os.environ["IMG2_HOME"] = _TMP_HOME.name


def tearDownModule() -> None:
    if _OLD_HOME is None:
        os.environ.pop("IMG2_HOME", None)
    else:
        os.environ["IMG2_HOME"] = _OLD_HOME
    if _TMP_HOME is not None:
        _TMP_HOME.cleanup()

from domains.animated_character import DOMAIN as ANIMATED_CHARACTER  # noqa: E402
from workflow_state import (  # noqa: E402
    WorkflowStateError,
    new_state,
    next_entry,
    recompute,
    status_payload,
)


def rig_ids(state: dict) -> list[str]:
    return [step["id"] for step in state["checklist"] if step["scope"] == "rig"]


class AnimatedCharacterProfile(unittest.TestCase):
    def setUp(self) -> None:
        self.state = new_state("subject.glb", profile="animated-character", spec="spec.json")

    def test_the_profile_is_accepted(self) -> None:
        self.assertEqual(self.state["profile"], "animated-character")

    def test_every_rig_step_reaches_the_checklist(self) -> None:
        self.assertEqual(rig_ids(self.state), [step_id for step_id, _command in ANIMATED_CHARACTER["rigSteps"]])

    def test_it_keeps_the_character_steps_too(self) -> None:
        """An animated character is still a character; the anatomy contract must not be lost."""
        ids = [step["id"] for step in self.state["checklist"]]
        self.assertIn("character-contract-read", ids)
        self.assertIn("character-landmarks", ids)

    def test_a_plain_character_build_gets_no_rig_steps(self) -> None:
        """Rigging is opt-in. A static character must not be blocked on animation gates."""
        self.assertEqual(rig_ids(new_state("subject.png", profile="character")), [])

    def test_profiles_without_rig_steps_get_none(self) -> None:
        # "cs2" left this list when the domain moved to its installed plugin (a registry profile
        # would need IMG2_HOME pinned here); generic and character cover the no-rigSteps rule.
        for profile in ("generic", "character"):
            with self.subTest(profile=profile):
                self.assertEqual(rig_ids(new_state("subject.png", profile=profile)), [])

    def test_an_unknown_profile_is_still_refused(self) -> None:
        with self.assertRaises(WorkflowStateError):
            new_state("subject.glb", profile="animated")


class StageROrderIsLoadBearing(unittest.TestCase):
    def setUp(self) -> None:
        self.ids = rig_ids(new_state("subject.glb", profile="animated-character", spec="spec.json"))

    def index(self, step_id: str) -> int:
        self.assertIn(step_id, self.ids)
        return self.ids.index(step_id)

    def test_repair_precedes_the_freeze(self) -> None:
        """A mesh may legitimately need fixing; freezing a broken mesh certifies the breakage."""
        self.assertLess(self.index("mesh-repair"), self.index("mesh-freeze"))

    def test_the_freeze_precedes_every_rig_step(self) -> None:
        """After the freeze the geometry is evidence. Binding before it leaves nothing to compare."""
        freeze = self.index("mesh-freeze")
        self.assertLess(freeze, self.index("rig-payload-validate"))
        self.assertLess(freeze, self.index("rig-bind"))

    def test_parity_is_verified_after_the_bind(self) -> None:
        """Binding is the only moment "implementation did not touch the mesh" can be falsified."""
        self.assertLess(self.index("rig-bind"), self.index("mesh-parity-verify"))

    def test_the_glb_reference_precedes_binding(self) -> None:
        """Rigging refers FROM the GLB, so its skeleton must be read before anything is bound."""
        self.assertLess(self.index("glb-rig-reference"), self.index("rig-bind"))

    def test_the_contract_is_read_first(self) -> None:
        self.assertEqual(self.ids[0], "rig-contract-read")

    def test_the_gates_run_last(self) -> None:
        self.assertEqual(self.ids[-1], "rig-gates")


class TheDispatcherActuallyReachesThem(unittest.TestCase):
    """The half the first version of this suite missed, and the reason it is written this way now.

    The original tests asserted the rig steps were IN the checklist and in the right ORDER. Both
    passed while `next_entry()` had no branch for scope "rig" at all: the steps sat pending forever,
    `recompute()` declared the build `complete` after the final scope, and `next.py` never once asked
    for a rig step. Presence in a list is not reachability, and asserting the list is not asserting
    the behaviour -- which is precisely the defect the Stage R wiring exists to end, reproduced one
    layer up. These tests drive the real state machine instead.
    """

    def drain(self, profile: str) -> tuple[list[str], dict]:
        """Walk the state machine the way next.py does, returning the rig steps it dispatched."""
        state = new_state("subject.glb", profile=profile, spec="spec.json")
        dispatched: list[str] = []
        for _ in range(200):
            entry = next_entry(state)
            if entry is None:
                break
            if entry["id"] == "await-pass-transition":
                state["currentPass"] = "complete"
                continue
            for step in state["checklist"]:
                if step["id"] == entry["id"]:
                    step["status"] = "done"
            if entry["scope"] == "rig":
                dispatched.append(entry["id"])
        else:
            self.fail("state machine did not terminate")
        recompute(state)
        return dispatched, state

    def test_every_rig_step_is_actually_dispatched(self) -> None:
        dispatched, _state = self.drain("animated-character")
        self.assertEqual(dispatched, [step_id for step_id, _command in ANIMATED_CHARACTER["rigSteps"]])

    def test_the_build_is_not_complete_while_a_rig_step_is_pending(self) -> None:
        """The exact bug: `complete` was reached with all nine rig steps still pending."""
        state = new_state("subject.glb", profile="animated-character", spec="spec.json")
        state["currentPass"] = "complete"
        for step in state["checklist"]:
            if step["scope"] != "rig":
                step["status"] = "done"
        recompute(state)
        self.assertNotEqual(state["status"], "complete")
        self.assertEqual(next_entry(state)["scope"], "rig")

    def test_rig_steps_come_after_the_final_scope(self) -> None:
        """Rigging is additive to a finished mesh; binding one whose parts still move freezes
        geometry that has not settled."""
        dispatched, _state = self.drain("animated-character")
        state = new_state("subject.glb", profile="animated-character", spec="spec.json")
        order = [step["scope"] for step in state["checklist"]]
        self.assertLess(max(i for i, s in enumerate(order) if s == "final"),
                        min(i for i, s in enumerate(order) if s == "rig"))
        self.assertTrue(dispatched)

    def test_a_plain_character_build_still_completes(self) -> None:
        """Adding the rig scope must not strand a profile that has no rig steps."""
        dispatched, state = self.drain("character")
        self.assertEqual(dispatched, [])
        self.assertEqual(state["status"], "complete")

    def test_rig_steps_are_visible_in_status(self) -> None:
        """Invisible in status output is unreachable in practice: nobody knows to run them."""
        state = new_state("subject.glb", profile="animated-character", spec="spec.json")
        pending = status_payload(state)["pending"]
        for step_id, _command in ANIMATED_CHARACTER["rigSteps"]:
            self.assertIn(step_id, pending)


class StepsNameTheirTooling(unittest.TestCase):
    def setUp(self) -> None:
        self.commands = dict(ANIMATED_CHARACTER["rigSteps"])

    def test_each_gate_step_names_the_script_that_runs_it(self) -> None:
        for step_id, script in (
            ("glb-rig-reference", "glb_rig_reference.py"),
            ("mesh-freeze", "mesh_parity.py"),
            ("mesh-parity-verify", "mesh_parity.py"),
            ("rig-payload-validate", "validate_rig_payload.py"),
            ("clip-measure", "clip_features.py"),
            ("rig-gates", "rig_gates.py"),
        ):
            with self.subTest(step=step_id):
                self.assertIn(script, self.commands[step_id])

    def test_freeze_and_verify_use_the_matching_subcommands(self) -> None:
        self.assertIn("mesh_parity.py freeze", self.commands["mesh-freeze"])
        self.assertIn("mesh_parity.py verify", self.commands["mesh-parity-verify"])

    def test_the_bind_step_states_what_it_may_add(self) -> None:
        """The whole mesh-immutability rule lives in this string; a rewrite that drops it is a bug."""
        command = self.commands["rig-bind"]
        for token in ("skinIndex", "skinWeight", "frozen"):
            self.assertIn(token, command)

    def test_the_payload_validator_states_its_own_limits(self) -> None:
        """A green structural payload is not evidence the mesh survives animation."""
        self.assertIn("structural payload integrity ONLY", self.commands["rig-payload-validate"])
        # The CLI takes --payload, and a sculpt spec is a different schema from a rig payload.
        self.assertIn("--payload", self.commands["rig-payload-validate"])


if __name__ == "__main__":
    unittest.main()
