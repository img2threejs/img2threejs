from __future__ import annotations

import unittest
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from forge._shared.pipeline_routing import resolve_pipeline_routing, validate_pipeline_routing
from forge.stage2_spec.new_pre_spec_assessment import make_payload
from forge.stage2_spec.new_sculpt_spec import make_spec
from forge.stage2_spec.validate_sculpt_spec import validate_spec
from forge.stage2_spec.validate_sculpt_spec import validate_pipeline_routing_contract


SKILL_ROOT = Path(__file__).resolve().parents[2]


def classification(kind: str, confidence: float = 0.9) -> dict:
    return {
        "kind": kind,
        "confidence": confidence,
        "evidenceRefs": ["fixture:front"],
        "provider": "fixture-classifier",
        "version": "1",
    }


class PipelineRoutingTests(unittest.TestCase):
    def test_classification_routes_reliable_weapon_and_character(self) -> None:
        weapon = resolve_pipeline_routing(classification=classification("weapon"))
        character = resolve_pipeline_routing(classification=classification("character"))

        self.assertEqual(weapon["track"], "weapon-v1.4")
        self.assertEqual(character["track"], "character-v1.5")
        self.assertEqual(weapon["source"], "classification")
        self.assertEqual(weapon["status"], "resolved")

    def test_ambiguous_or_low_confidence_classification_fails_closed(self) -> None:
        for kind, confidence in (("hybrid", 0.99), ("unknown", 0.99), ("weapon", 0.81)):
            with self.subTest(kind=kind, confidence=confidence):
                routing = resolve_pipeline_routing(classification=classification(kind, confidence))
                self.assertEqual(routing["status"], "request-input")
                self.assertTrue(routing["conflicts"])

    def test_malformed_classification_fails_closed(self) -> None:
        routing = resolve_pipeline_routing(classification={"kind": "weapon"})

        self.assertEqual(routing["status"], "request-input")
        self.assertEqual(routing["classification"]["kind"], "unknown")

    def test_explicit_track_rejects_reliable_contradiction(self) -> None:
        routing = resolve_pipeline_routing(
            explicit_track="weapon-v1.4",
            classification=classification("character"),
        )

        self.assertEqual(routing["status"], "request-input")
        self.assertIn("contradicts", routing["conflicts"][0])

    def test_explicit_track_normalizes_to_routing_metadata(self) -> None:
        routing = resolve_pipeline_routing(explicit_track="character-v1.5")

        self.assertEqual(routing["track"], "character-v1.5")
        self.assertEqual(routing["source"], "explicit")
        self.assertEqual(routing["status"], "resolved")
        self.assertEqual(routing["classification"]["kind"], "character")

    def test_legacy_cs2_routes_weapon_without_modern_classification(self) -> None:
        routing = resolve_pipeline_routing(legacy_cs2=True)

        self.assertEqual(routing["track"], "weapon-v1.4")
        self.assertEqual(routing["source"], "legacy")
        self.assertEqual(routing["status"], "resolved")

    def test_legacy_cs2_does_not_override_an_explicit_character_track(self) -> None:
        routing = resolve_pipeline_routing(
            explicit_track="character-v1.5",
            legacy_cs2=True,
        )

        self.assertEqual(routing["status"], "request-input")
        self.assertIn("contradicts legacy CS2", routing["conflicts"][0])

    def test_validator_accepts_resolved_and_rejects_malformed_contract(self) -> None:
        self.assertEqual(validate_pipeline_routing(resolve_pipeline_routing(legacy_cs2=True)), [])
        self.assertTrue(validate_pipeline_routing({"version": 1}))

    def test_assessment_and_spec_preserve_resolved_routing(self) -> None:
        assessment = make_payload("Hero", None, "moderate", is_character=True)
        spec = make_spec("Hero", None, assessment)

        self.assertEqual(assessment["pipelineRouting"]["track"], "character-v1.5")
        self.assertEqual(spec["pipelineRouting"], assessment["pipelineRouting"])

    def test_routing_selects_a_template_only_where_the_base_owns_one(self) -> None:
        # The base ships a character template and, deliberately, no weapon template: a weapon with
        # no domain plugin installed gets the skeleton and the agent infers the shape from the
        # reference, exactly as any other object does. The CS2 plugin supplies the knife recipe
        # through a spec-augmentation artifact instead.
        for kind, expected_component in (("weapon", "root"), ("character", "head")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                assessment_path = Path(directory) / "assessment.json"
                spec_path = Path(directory) / "spec.json"
                assessment_path.write_text(json.dumps({
                    "preSpecAssessment": {"objectClass": {"primaryDomain": "object"}},
                    "pipelineRouting": resolve_pipeline_routing(classification=classification(kind)),
                }))
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SKILL_ROOT / "forge/stage2_spec/new_sculpt_spec.py"),
                        "Target",
                        "--assessment",
                        str(assessment_path),
                        "--out",
                        str(spec_path),
                    ],
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                component_ids = {component["id"] for component in json.loads(spec_path.read_text())["componentTree"]}
                self.assertIn(expected_component, component_ids)

    def test_validator_rejects_unresolved_or_wrong_template_routing(self) -> None:
        spec = make_spec("Target", None)
        spec["pipelineRouting"] = resolve_pipeline_routing(classification=classification("hybrid"))
        unresolved_errors, _ = validate_spec(spec)

        self.assertIn("pipelineRouting must be resolved before validation", unresolved_errors)

        spec["pipelineRouting"] = resolve_pipeline_routing(classification=classification("character"))
        template_errors, _ = validate_spec(spec)

        self.assertIn("character-v1.5 routing requires the character template", template_errors)

    def test_legacy_cs2_intake_derives_valid_routing_without_persisting_it(self) -> None:
        errors: list[str] = []
        spec = {"cs2Intake": {"itemFamily": "knife"}}

        validate_pipeline_routing_contract(spec, errors)

        self.assertEqual(errors, [])
        self.assertNotIn("pipelineRouting", spec)

    def test_cs2_contract_accepts_any_item_family(self) -> None:
        # The knife-only family gate lived on in the base validator after cs2_manifest.py removed
        # its four plugin-side copies -- every non-knife CS2 item failed strict validation with
        # "requires the registered knife adapter". Found by a live MP9 run (test-e2e-02).
        from forge.stage2_spec.validate_sculpt_spec import validate_cs2_contract

        errors: list[str] = []
        warnings: list[str] = []
        spec = {
            "cs2Intake": {
                "itemFamily": "smg",
                "route": "procedural-finish",
                "exactnessTier": "metadata-assisted",
            }
        }
        validate_cs2_contract(spec, errors, warnings)
        self.assertNotIn("cs2Intake requires the registered knife adapter", errors)
        self.assertEqual([e for e in errors if "knife" in e], [])


if __name__ == "__main__":
    unittest.main()
