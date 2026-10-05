#!/usr/bin/env python3
"""The rig scope's dispatch mechanism -- that a registry domain declaring `rigSteps` reaches the
checklist, dispatches after the FINAL scope, and blocks completion while pending.

The `animated-character` domain itself moved to the plugin-character plugin
(`extract-animated-character`): its `domain.json` is now the canonical rig-step ORDER, pinned by
the plugin's own two-file drift pin (`tests/test_domain_declaration.py` there), so this file no
longer asserts order or command text against an in-repo module -- a fixture the test writes cannot
pin the real order, and pretending it can is a tautology. What the BASE owns, and what this file
tests, is the MECHANISM: the splice, the scope ordering, the dispatcher, and completion semantics.
Those are legitimately tested against a fixture domain. One test reads the *installed* plugin's
declaration when present, so the real order is still checkable from the base without depending on it.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_shared"))

from workflow_state import (  # noqa: E402
    WorkflowStateError,
    new_state,
    next_entry,
    recompute,
    status_payload,
)

# The fixture rig domain: exercises the mechanism, deliberately NOT the real animated-character
# order (the plugin owns that). Ids are fixture-local so no one mistakes this for the real track.
FIXTURE_RIG_STEPS = [
    ["fix-rig-first", "Read {plugin_dir}/contract.md completely"],
    ["fix-rig-mid", "Run python3 {plugin_dir}/tools/mid.py --out mid.json"],
    ["fix-rig-last", "Run python3 {plugin_dir}/tools/last.py --payload p.json"],
]

_TMP_HOME: tempfile.TemporaryDirectory | None = None
_OLD_HOME: str | None = None


def setUpModule() -> None:
    """Pin IMG2_HOME to a scratch home carrying TWO fixture plugins, the registry's plugin path.

    Two, not one, and both installed rather than in-repo: after the character extraction there is
    no in-repo domain left, so "a domain with rig steps" and "a domain without them" must both be
    expressible through the same mechanism or the second case has no subject. `setup-fixture-dom`
    is what `profile="character"` used to stand in for here -- and it is the better subject, since
    the property is about the mechanism and naming a real domain made it read as that domain's.
    """
    global _TMP_HOME, _OLD_HOME
    _TMP_HOME = tempfile.TemporaryDirectory()
    home = Path(_TMP_HOME.name)
    rows = []
    for plugin_id, domain in (
        ("rig-fixture", {
            "id": "rig-fixture-dom",
            "setupSteps": [["fixture-contract-read", "Read {plugin_dir}/contract.md completely"]],
            "setupAnchorBefore": "local-spec-search",
            "rigSteps": FIXTURE_RIG_STEPS,
        }),
        ("setup-fixture", {
            "id": "setup-fixture-dom",
            "setupSteps": [["setup-fixture-read", "Read {plugin_dir}/other.md completely"]],
            "setupAnchorBefore": "local-spec-search",
        }),
    ):
        plugin_dir = home / "plugins" / plugin_id
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "domain.json").write_text(json.dumps(domain), encoding="utf-8")
        rows.append({"id": plugin_id, "repo": "x", "ref": "v1", "resolvedSha": "sha",
                     "addedAt": "2026-01-01"})
    (home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": rows}), encoding="utf-8")
    _OLD_HOME = os.environ.get("IMG2_HOME")
    os.environ["IMG2_HOME"] = str(home)


def tearDownModule() -> None:
    if _OLD_HOME is None:
        os.environ.pop("IMG2_HOME", None)
    else:
        os.environ["IMG2_HOME"] = _OLD_HOME
    if _TMP_HOME is not None:
        _TMP_HOME.cleanup()


def rig_ids(state: dict) -> list[str]:
    return [step["id"] for step in state["checklist"] if step["scope"] == "rig"]


class RigDomainProfile(unittest.TestCase):
    def setUp(self) -> None:
        self.state = new_state("subject.glb", profile="rig-fixture-dom", spec="spec.json")

    def test_the_profile_is_accepted(self) -> None:
        self.assertEqual(self.state["profile"], "rig-fixture-dom")

    def test_every_declared_rig_step_reaches_the_checklist(self) -> None:
        self.assertEqual(rig_ids(self.state), [step_id for step_id, _ in FIXTURE_RIG_STEPS])

    def test_it_keeps_the_setup_contribution_too(self) -> None:
        ids = [step["id"] for step in self.state["checklist"]]
        self.assertIn("fixture-contract-read", ids)

    def test_a_domain_declaring_no_rig_steps_gets_none(self) -> None:
        """Rigging is opt-in; a domain that contributes setup steps and no rig steps gets none.

        Spelled `profile="character"` while an in-repo `character` domain existed. It does not, and
        the fixture domain is the better subject anyway: this is a property of the MECHANISM, and
        naming a real domain made it look like a property of that domain.
        """
        self.assertEqual(rig_ids(new_state("subject.png", profile="setup-fixture-dom")), [])

    def test_profiles_without_rig_steps_get_none(self) -> None:
        self.assertEqual(rig_ids(new_state("subject.png", profile="generic")), [])

    def test_an_unknown_profile_is_still_refused(self) -> None:
        with self.assertRaises(WorkflowStateError):
            new_state("subject.glb", profile="animated")


class TheDispatcherActuallyReachesThem(unittest.TestCase):
    """The half the first version of this suite missed, and the reason it is written this way now.

    The original tests asserted the rig steps were IN the checklist and in the right ORDER. Both
    passed while `next_entry()` had no branch for scope "rig" at all: the steps sat pending forever,
    `recompute()` declared the build `complete` after the final scope, and `next.py` never once asked
    for a rig step. Presence in a list is not reachability, and asserting the list is not asserting
    the behaviour. These tests drive the real state machine against the fixture domain.
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
        dispatched, _state = self.drain("rig-fixture-dom")
        self.assertEqual(dispatched, [step_id for step_id, _ in FIXTURE_RIG_STEPS])

    def test_the_build_is_not_complete_while_a_rig_step_is_pending(self) -> None:
        """The exact bug this suite exists for: `complete` reached with rig steps still pending."""
        state = new_state("subject.glb", profile="rig-fixture-dom", spec="spec.json")
        state["currentPass"] = "complete"
        for step in state["checklist"]:
            if step["scope"] != "rig":
                step["status"] = "done"
        recompute(state)
        self.assertNotEqual(state["status"], "complete")
        self.assertEqual(next_entry(state)["scope"], "rig")

    def test_rig_steps_come_after_the_final_scope(self) -> None:
        """Rigging is additive to a finished mesh; binding one whose parts still move freezes
        geometry that has not settled. This ordering is also why gate participation is rig-aware
        (run_gates.py): the plugin-gates sweep in FINAL runs before any rig step can produce."""
        dispatched, _state = self.drain("rig-fixture-dom")
        state = new_state("subject.glb", profile="rig-fixture-dom", spec="spec.json")
        order = [step["scope"] for step in state["checklist"]]
        self.assertLess(max(i for i, s in enumerate(order) if s == "final"),
                        min(i for i, s in enumerate(order) if s == "rig"))
        self.assertTrue(dispatched)

    def test_a_build_with_no_rig_steps_still_completes(self) -> None:
        dispatched, state = self.drain("setup-fixture-dom")
        self.assertEqual(dispatched, [])
        self.assertEqual(state["status"], "complete")

    def test_rig_steps_are_visible_in_status(self) -> None:
        """Invisible in status output is unreachable in practice: nobody knows to run them."""
        state = new_state("subject.glb", profile="rig-fixture-dom", spec="spec.json")
        pending = status_payload(state)["pending"]
        for step_id, _ in FIXTURE_RIG_STEPS:
            self.assertIn(step_id, pending)


class InstalledPluginOrderIsCheckedWhenPresent(unittest.TestCase):
    """The canonical rig order lives in the installed plugin's domain.json (the
    plugin's own suite pins it with a two-file pin). This base-side check reads the REAL installed
    declaration when one exists, so the load-bearing sequence stays checkable from here without
    making the base depend on the plugin."""

    def test_installed_rig_order_invariants(self) -> None:
        home = Path(os.environ.get("IMG2_REAL_HOME") or Path.home() / ".img2")
        declaration = home / "plugins" / "character" / "domain.json"
        if not declaration.is_file():
            self.skipTest("plugin-character not installed; the plugin's own suite pins the order")
        ids = [step_id for step_id, _ in json.loads(declaration.read_text(encoding="utf-8"))["rigSteps"]]
        order = {step_id: index for index, step_id in enumerate(ids)}
        self.assertLess(order["mesh-repair"], order["mesh-freeze"])
        self.assertLess(order["mesh-freeze"], order["rig-bind"])
        self.assertLess(order["rig-bind"], order["mesh-parity-verify"])
        self.assertEqual(ids[0], "rig-contract-read")
        self.assertEqual(ids[-1], "rig-gates")


class SetupStepAssetsAgreement(unittest.TestCase):
    """Every BASE-relative path an installed plugin's setupSteps name must exist in this checkout.

    Doctor cannot file-check domain rows, so this is the enforcement point: renaming a base asset a
    plugin depends on must fail here, not in a user's run.

    It used to enumerate three paths by hand, two of them `grimoire/character/*.md`. Those pages are
    plugin-character's now and its step names them `{plugin_dir}`-relative, which this check must
    NOT follow -- a `{plugin_dir}` path belongs to the plugin's own suite, and resolving it here
    against the base would be the same category error in reverse. So the list is derived from the
    declaration rather than transcribed: whatever base-relative paths the installed plugins name,
    those are what this checks. Hand-transcription is what made the old version assert the existence
    of files this change deletes.
    """

    @staticmethod
    def _looks_like_a_base_asset(token: str) -> bool:
        """A path with a file extension whose first segment is a directory THIS repo ships.

        Both halves are load-bearing, and each was added after a false positive:

          `family/subtype`  cs2 prose, no extension -- a slash is not a path.
          `runtime/scripts/export_mesh_buffers.mjs`  plugin-character's mesh-freeze step. It has an
                extension, and `runtime/` is not a directory here: the file is not in this repo at
                all. That is a REAL defect, and deliberately not this check's -- a base-asset
                agreement cannot speak for a path the base never had. It belongs to the plugin's own
                declaration test and to the A11 capture, where it is recorded as a blocker.
        """
        first = token.split("/", 1)[0]
        return bool(Path(token).suffix) and (ROOT.parent / first).is_dir()

    def _base_relative_paths(self) -> list[tuple[str, str]]:
        home = Path(os.environ.get("IMG2_REAL_HOME") or Path.home() / ".img2")
        registry = home / "plugins.json"
        if not registry.is_file():
            return []
        out = []
        for row in json.loads(registry.read_text(encoding="utf-8")).get("plugins") or []:
            declaration = home / "plugins" / (row or {}).get("id", "") / "domain.json"
            if not declaration.is_file():
                continue
            entry = json.loads(declaration.read_text(encoding="utf-8"))
            for key in ("setupSteps", "passSteps", "rigSteps"):
                for _step_id, command in entry.get(key, []):
                    for token in command.split():
                        token = token.strip("(),")
                        if (not token.startswith("{") and token[:1].isalpha()
                                and self._looks_like_a_base_asset(token)):
                            out.append((row["id"], token))
        return out

    def test_every_base_relative_path_an_installed_plugin_names_exists(self) -> None:
        named = self._base_relative_paths()
        if not named:
            self.skipTest("no installed plugin declares a domain; nothing to check from here")
        for plugin_id, rel in named:
            self.assertTrue((ROOT.parent / rel).exists(), f"{plugin_id} names {rel}, which is missing")

    def test_the_asset_the_character_domain_still_depends_on_is_here(self) -> None:
        # One base asset survives the extraction as a plugin dependency: the landmark extractor.
        # Named explicitly so that its deletion fails here even with no plugin installed.
        self.assertTrue((ROOT.parent / "forge/stage1_intake/extract_landmarks.py").is_file())

    def test_the_moved_grimoire_pages_are_gone_from_the_base(self) -> None:
        # The other half of the same agreement, and the half that catches a half-applied move: if
        # these come back, two checkouts own the same pages and only one of them is read.
        for rel in ("grimoire/character/reconstruction.md",
                    "grimoire/character/likeness_maximization.md",
                    "grimoire/character/structure_decomposition.md",
                    "grimoire/character/head_construction.md"):
            self.assertFalse((ROOT.parent / rel).exists(), f"{rel} is still in the base")


if __name__ == "__main__":
    unittest.main()
