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
    """`character-v1.5` left with the humanoid template it selected.

    `character` and `hybrid` stay in VALID_KINDS deliberately. A KIND is what the classifier saw; a
    TRACK is what this repo can build from it, and after the extraction there is one of the latter.
    Dropping `character` from the vocabulary would make a correct classification come back as
    `malformed-classification`, so "a character" and "corrupt input" would read identically — a
    regression in a gate whose whole value is explaining its refusal.
    """

    def test_a_reliable_weapon_routes_to_the_one_track_the_base_owns(self) -> None:
        weapon = resolve_pipeline_routing(classification=classification("weapon"))

        self.assertEqual(weapon["track"], "weapon-v1.4")
        self.assertEqual(weapon["source"], "classification")
        self.assertEqual(weapon["status"], "resolved")

    def test_a_reliable_character_fails_closed_naming_its_provider(self) -> None:
        character = resolve_pipeline_routing(classification=classification("character"))

        self.assertEqual(character["status"], "request-input")
        self.assertEqual(character["classification"]["kind"], "character",
                         "a correct classification must not be rewritten to 'unknown'")
        # The conflict must be non-empty AND say something: `new_sculpt_spec` renders it as
        # "pipeline routing requires input: " + "; ".join(conflicts), so an empty list is an error
        # message with nothing after the colon.
        self.assertTrue(character["conflicts"])
        self.assertIn("--domain character", character["conflicts"][0])

    def test_a_provider_answers_the_one_conflict_it_can_answer(self) -> None:
        """The conflict says "its domain plugin supplies the content through --domain character".

        Before this, following that advice EXACTLY reproduced the message -- advice that cannot
        succeed, which is the failure `domains/__init__.py`'s withdrawal table exists to avoid,
        repeated one file over. A code review reproduced it with the plugin's own artifact.
        """
        routing = resolve_pipeline_routing(classification=classification("character"),
                                           provider_domain="character")
        self.assertEqual(routing["status"], "resolved")
        self.assertEqual(routing["source"], "provider")
        self.assertEqual(routing["provider"], "character")
        self.assertEqual(routing["conflicts"], [])
        # No base track exists, so the record says so rather than borrowing a track name. Inventing
        # one would be the base naming a domain, which is what the registry exists to stop.
        self.assertIsNone(routing["track"])
        self.assertEqual(validate_pipeline_routing(routing), [])

    def test_a_provider_cannot_answer_a_conflict_that_is_not_its_own(self) -> None:
        # It clears exactly one thing: "this kind has no base authoring track". Everything else --
        # a hybrid that needs a human, a shaky classification, a provider claiming a different
        # kind -- still fails closed, or a plugin could wave through work nobody vouched for.
        for label, kwargs in (
            ("wrong kind", dict(classification=classification("character"), provider_domain="cs2")),
            ("hybrid", dict(classification=classification("hybrid"), provider_domain="hybrid")),
            ("unknown", dict(classification=classification("unknown"), provider_domain="unknown")),
            ("low confidence", dict(classification=classification("character", 0.5),
                                    provider_domain="character")),
        ):
            with self.subTest(label):
                routing = resolve_pipeline_routing(**kwargs)
                self.assertEqual(routing["status"], "request-input")
                self.assertTrue(routing["conflicts"])

    def test_a_shaky_character_classification_names_BOTH_problems(self) -> None:
        """It has two, and chaining them on `elif` reported only the first.

        The record then said "its domain plugin supplies the content", implying the provider was
        the whole answer, while the real blocker was that nobody is sure it is a character at all.
        """
        conflicts = resolve_pipeline_routing(
            classification=classification("character", 0.5))["conflicts"]
        self.assertTrue(any("no base authoring track" in c for c in conflicts))
        self.assertTrue(any("confidence 0.50" in c for c in conflicts))

    def test_a_provider_record_must_not_borrow_a_track_or_hide_its_provider(self) -> None:
        good = resolve_pipeline_routing(classification=classification("character"),
                                        provider_domain="character")
        self.assertIn("pipelineRouting.track must be null when source is provider",
                      validate_pipeline_routing(dict(good, track="weapon-v1.4")))
        borrowed = dict(good)
        del borrowed["provider"]
        self.assertIn("pipelineRouting.provider must name the domain that resolved it",
                      validate_pipeline_routing(borrowed))

    def test_a_record_cannot_claim_a_provider_it_is_not_entitled_to(self) -> None:
        """Shape alone is not authority, and this is the hole the provider branch opened.

        Before this check, `validate_pipeline_routing` asserted only that `track` was null and
        `provider` was a non-empty string -- so a hand-edited spec walked a 0.40-confidence HYBRID
        through as `resolved` with zero errors. That is the "needs a human" case this module exists
        to stop, and before the provider branch existed the edit was impossible, because `track`
        had to be in VALID_TRACKS.
        """
        def record(kind, confidence, provider):
            return {"version": 1, "source": "provider", "track": None, "provider": provider,
                    "status": "resolved", "conflicts": [],
                    "classification": classification(kind, confidence)}

        for label, args in (("a hybrid that needs a human", ("hybrid", 0.40, "not-installed")),
                            ("a shaky character", ("character", 0.40, "character")),
                            ("a kind the base builds itself", ("weapon", 0.95, "weapon")),
                            ("a provider claiming another kind", ("character", 0.95, "cs2"))):
            with self.subTest(label):
                errors = validate_pipeline_routing(record(*args))
                self.assertTrue(any("cannot resolve" in e for e in errors), errors)

        self.assertEqual(validate_pipeline_routing(record("character", 0.95, "character")), [])

    def test_the_resolver_and_the_validator_cannot_disagree(self) -> None:
        # They drifted the moment the branch was written -- four conditions on one side, zero on
        # the other. One predicate now, asserted to be the one both use.
        from forge._shared.pipeline_routing import provider_may_resolve

        for kind, confidence, provider in (("character", 0.95, "character"),
                                           ("character", 0.40, "character"),
                                           ("hybrid", 0.99, "hybrid"),
                                           ("weapon", 0.99, "weapon"),
                                           ("character", 0.95, "cs2")):
            resolved = resolve_pipeline_routing(
                classification=classification(kind, confidence), provider_domain=provider)
            took_the_branch = resolved["source"] == "provider"
            self.assertEqual(took_the_branch, provider_may_resolve(kind, confidence, provider),
                             (kind, confidence, provider))

    def test_an_explicit_track_is_not_swallowed_by_the_provider_branch(self) -> None:
        # The branch returns `track: None`, so taking it would drop the caller's explicit request
        # with no conflict recorded. The contradiction goes down the normal path instead.
        routing = resolve_pipeline_routing(explicit_track="weapon-v1.4",
                                           classification=classification("character"),
                                           provider_domain="character")
        self.assertNotEqual(routing["source"], "provider")
        self.assertEqual(routing["status"], "request-input")
        self.assertTrue(routing["conflicts"])

    def test_the_character_track_is_no_longer_a_track_at_all(self) -> None:
        from forge._shared.pipeline_routing import TRACK_BY_KIND, VALID_TRACKS

        self.assertEqual(set(VALID_TRACKS), {"weapon-v1.4"})
        self.assertNotIn("character", TRACK_BY_KIND)

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

    def test_an_explicit_track_that_is_no_longer_valid_is_refused_not_honoured(self) -> None:
        """`character-v1.5` used to be a legal `--explicit_track`. A caller still passing it must
        not be quietly routed somewhere else: it falls out of VALID_TRACKS and becomes a conflict."""
        routing = resolve_pipeline_routing(explicit_track="character-v1.5")

        self.assertEqual(routing["status"], "request-input")
        self.assertIn("explicit track is invalid", routing["conflicts"])

    def test_explicit_track_normalizes_to_routing_metadata(self) -> None:
        routing = resolve_pipeline_routing(explicit_track="weapon-v1.4")

        self.assertEqual(routing["track"], "weapon-v1.4")
        self.assertEqual(routing["source"], "explicit")
        self.assertEqual(routing["status"], "resolved")
        self.assertEqual(routing["classification"]["kind"], "weapon")

    def test_legacy_cs2_routes_weapon_without_modern_classification(self) -> None:
        routing = resolve_pipeline_routing(legacy_cs2=True)

        self.assertEqual(routing["track"], "weapon-v1.4")
        self.assertEqual(routing["source"], "legacy")
        self.assertEqual(routing["status"], "resolved")

    def test_legacy_cs2_does_not_override_a_contradicting_explicit_track(self) -> None:
        # Was spelled with `character-v1.5`, the only other track that existed. The property is
        # about the legacy override, not about which track contradicts it.
        routing = resolve_pipeline_routing(explicit_track="some-other-v1", legacy_cs2=True)

        self.assertEqual(routing["status"], "request-input")
        self.assertIn("contradicts legacy CS2", routing["conflicts"][0])

    def test_validator_accepts_resolved_and_rejects_malformed_contract(self) -> None:
        self.assertEqual(validate_pipeline_routing(resolve_pipeline_routing(legacy_cs2=True)), [])
        self.assertTrue(validate_pipeline_routing({"version": 1}))

    def test_assessment_and_spec_preserve_resolved_routing(self) -> None:
        # `is_character=True` went with the track; `--cs2` is the remaining flag that sets one.
        assessment = make_payload("Karambit", None, "ultra-complex", is_cs2=True)
        spec = make_spec("Karambit", None, assessment)

        self.assertEqual(assessment["pipelineRouting"]["track"], "weapon-v1.4")
        self.assertEqual(spec["pipelineRouting"], assessment["pipelineRouting"])

    def _author(self, directory: str, kind: str):
        assessment_path = Path(directory) / "assessment.json"
        spec_path = Path(directory) / "spec.json"
        assessment_path.write_text(json.dumps({
            "preSpecAssessment": {"objectClass": {"primaryDomain": "object"}},
            "pipelineRouting": resolve_pipeline_routing(classification=classification(kind)),
        }))
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "forge/stage2_spec/new_sculpt_spec.py"), "Target",
             "--assessment", str(assessment_path), "--out", str(spec_path)],
            capture_output=True, text=True,
        )
        return result, spec_path

    def test_the_base_authors_no_template_for_any_kind_it_routes(self) -> None:
        """The base ships NO authoring template now — not for weapons, not for characters.

        A weapon has always got the generic skeleton with the agent inferring the shape from the
        reference; after the character extraction that is what every kind the base can route gets.
        Asserting `head` is ABSENT is the load-bearing half: a half-applied extraction that left
        the template reachable would produce a spec that still passed every other check here.
        """
        with tempfile.TemporaryDirectory() as directory:
            result, spec_path = self._author(directory, "weapon")
            self.assertEqual(result.returncode, 0, result.stderr)
            ids = {c["id"] for c in json.loads(spec_path.read_text())["componentTree"]}
            self.assertIn("root", ids)
            self.assertNotIn("head", ids, "the base authored a humanoid")

    def test_a_character_run_without_its_provider_fails_loud_naming_the_remedy(self) -> None:
        """The alternative — author the generic skeleton and exit 0 — is the silent downgrade.

        It is worse here than for an unknown kind: the classifier was CONFIDENT and CORRECT, so a
        run that quietly produced a spec with no rig, no anatomy and no humanoid decomposition
        would look like a successful character reconstruction to everything downstream.
        """
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._author(directory, "character")
            self.assertNotEqual(result.returncode, 0, "a character run without a provider exited 0")
            self.assertIn("has no base authoring track", result.stderr)
            self.assertIn("--domain character", result.stderr)

    def test_validator_rejects_unresolved_routing(self) -> None:
        """The second half of this test asserted `character-v1.5 routing requires the character
        template`, a coherence check that left with the template. It is not replaced by a weaker
        assertion: a spec carrying `character-v1.5` is now refused one step EARLIER, by
        `validate_pipeline_routing`, because the track is not in VALID_TRACKS. Both halves below
        assert that, one through the resolver and one through a hand-written record."""
        spec = make_spec("Target", None)
        spec["pipelineRouting"] = resolve_pipeline_routing(classification=classification("hybrid"))
        unresolved_errors, _ = validate_spec(spec)
        self.assertIn("pipelineRouting must be resolved before validation", unresolved_errors)

        spec["pipelineRouting"] = resolve_pipeline_routing(classification=classification("character"))
        character_errors, _ = validate_spec(spec)
        self.assertIn("pipelineRouting must be resolved before validation", character_errors)

        stale = dict(resolve_pipeline_routing(legacy_cs2=True), track="character-v1.5")
        self.assertIn("pipelineRouting.track must be one of weapon-v1.4",
                      validate_pipeline_routing(stale))

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
