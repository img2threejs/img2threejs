from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import validate_sculpt_spec as vss  # noqa: E402


def conforming_build_passes() -> list:
    return [
        {"id": "blockout", "acceptance": ["silhouette reads correctly"]},
        {"id": "structural-pass", "acceptance": ["hierarchy present"]},
        {"id": "material-pass", "acceptance": ["materials match reference"]},
    ]


class UnknownPassIdentifierIsRefused(unittest.TestCase):
    """Scenario: An unknown pass identifier is refused."""

    def test_out_of_set_build_pass_id_fails_naming_it(self):
        spec = {"buildPasses": [{"id": "blockout"}, {"id": "wield-pass"}]}
        errors: list[str] = []
        vss.validate_build_passes(spec, errors, [])
        self.assertTrue(any("wield-pass" in error for error in errors), errors)
        self.assertNotIn("blockout", " ".join(error for error in errors if "wield-pass" not in error))


class SelfConsistencyAloneIsInsufficient(unittest.TestCase):
    """Scenario: Self-consistency alone is insufficient."""

    def test_agreeing_but_unimplemented_pass_order_still_fails(self):
        spec = {
            "buildPasses": [{"id": "blockout"}, {"id": "wield-pass"}],
            "sculptPipeline": {"passOrder": ["blockout", "wield-pass"]},
        }
        errors: list[str] = []
        build_pass_ids = vss.validate_build_passes(spec, errors, [])
        vss.validate_sculpt_pipeline(spec, build_pass_ids, errors, [])
        self.assertTrue(any("wield-pass" in error for error in errors), errors)
        # Self-consistency between buildPasses and passOrder is real here -- both fields agree --
        # and the validator must still reject it, which is the point of this scenario.
        self.assertEqual(build_pass_ids, ["blockout", "wield-pass"])


class ConformingSpecIsUnaffected(unittest.TestCase):
    """Scenario: A conforming spec is unaffected."""

    def test_every_declared_id_in_the_base_set_passes_unchanged(self):
        spec = {
            "buildPasses": conforming_build_passes(),
            "sculptPipeline": {"passOrder": ["blockout", "structural-pass", "material-pass"]},
        }
        errors: list[str] = []
        build_pass_ids = vss.validate_build_passes(spec, errors, [])
        vss.validate_sculpt_pipeline(spec, build_pass_ids, errors, [])
        self.assertEqual(errors, [])
        self.assertEqual(build_pass_ids, ["blockout", "structural-pass", "material-pass"])


class RenamingAPassDoesNotBypassItsGates(unittest.TestCase):
    """Scenario: Renaming a pass does not bypass its gates."""

    def test_renaming_a_visual_pass_out_of_the_set_fails_validation(self):
        spec = {"buildPasses": [{"id": "blockout"}, {"id": "structural-pass-renamed"}]}
        errors: list[str] = []
        vss.validate_build_passes(spec, errors, [])
        self.assertTrue(any("structural-pass-renamed" in error for error in errors), errors)
        # A rejected spec never reaches generation, so the pass's render/comparison/vision
        # requirements are never reachable to be skipped in the first place.


class PluginSuppliedPassOrderIsValidatedIdentically(unittest.TestCase):
    """Scenario: A plugin-supplied pass order is validated identically."""

    def test_pass_order_field_is_checked_by_the_same_rule_as_build_passes(self):
        # sculptPipeline.passOrder is the field a plugin supplies pass ordering through, distinct
        # from the base's own buildPasses. It must be rejected by the same VALID_PIPELINE_PASS_IDS
        # rule, not merely by disagreeing with buildPasses.
        spec = {
            "buildPasses": conforming_build_passes(),
            "sculptPipeline": {"passOrder": ["blockout", "plugin-only-pass", "material-pass"]},
        }
        errors: list[str] = []
        build_pass_ids = vss.validate_build_passes(spec, errors, [])
        vss.validate_sculpt_pipeline(spec, build_pass_ids, errors, [])
        self.assertTrue(any("plugin-only-pass" in error for error in errors), errors)


class ThePermittedSetHasExactlyOneAuthority(unittest.TestCase):
    """Scenario: The validator consumes the defined set / an unconsumed definition is not left behind."""

    def test_amending_the_set_changes_validator_behaviour_with_no_other_edit(self):
        original = vss.VALID_PIPELINE_PASS_IDS
        try:
            vss.VALID_PIPELINE_PASS_IDS = original | {"custom-domain-pass"}
            spec = {"buildPasses": [{"id": "blockout"}, {"id": "custom-domain-pass"}]}
            errors: list[str] = []
            vss.validate_build_passes(spec, errors, [])
            self.assertEqual(errors, [])
        finally:
            vss.VALID_PIPELINE_PASS_IDS = original

        # And with the set restored, the same identifier is refused again -- proving the
        # validator reads the module-level definition rather than a copy taken at import time.
        spec = {"buildPasses": [{"id": "blockout"}, {"id": "custom-domain-pass"}]}
        errors = []
        vss.validate_build_passes(spec, errors, [])
        self.assertTrue(any("custom-domain-pass" in error for error in errors), errors)


class TheBaseCharacterTemplateIsLegal(unittest.TestCase):
    """Regression, not a spec scenario: proportion-lock and feature-placement are the base's own
    character-track pass ids (new_sculpt_spec.py make_character_build_passes), shipped since v1.2.
    They must validate cleanly, not merely be tolerated by a special case, or a later cleanup of
    VALID_PIPELINE_PASS_IDS silently re-breaks the character track again.

    Residual this change exposes but does not fix (deferred to the VISUAL_PASS_IDS/data-driven
    gate-registry hygiene already recorded in the proposal): a VISUAL pass renamed to a legal
    non-visual id (e.g. form-refinement -> proportion-lock) would pass this identifier check while
    dropping its render/comparison/vision gate, since VISUAL_PASS_IDS and VALID_PIPELINE_PASS_IDS
    are separate sets and this validator only enforces the latter.
    """

    def test_character_template_build_passes_and_pass_order_validate_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "spec.json"
            result = subprocess.run(
                [sys.executable, str(ROOT / "forge" / "stage2_spec" / "new_sculpt_spec.py"),
                 "Person", "--character", "--out", str(out)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            spec = json.loads(out.read_text())
        errors, _warnings = vss.validate_spec(spec)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
