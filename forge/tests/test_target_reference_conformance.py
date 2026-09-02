"""Reference-target conformance (task 3.10, `establish-the-emission-target-contract`, D8).

Two honestly-scoped proofs that the base emitter conforms through the reference target
(`--target threejs-ts`), never that production traverses it -- the no-target path stays in-process
and unchanged (D2, D8).

The envelope-parity test was written first, per the task, and failed exactly as predicted: `main()`
overloads exit 2 between a structured BLOCKED envelope on stderr and plain argparse usage-error
prose, and `emit_target.py` had no classifier at all. The lead's reconciliation: classify by
envelope, not by exit code, with zero changes to `generate_threejs_factory.py` -- implemented in
`emit_target._classify_reference_failure`. This module does not touch the emitter itself; only
`emit_target.py`'s own parsing changed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge"))
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import generate_threejs_factory as generator  # noqa: E402
import emit_target  # noqa: E402
from targets import reference_target  # noqa: E402
from forge.tests.oracle_support import assert_replay_matches_oracle  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FROZEN_SPEC = FIXTURES / "reference_target_conformance_spec.json"
ORACLE_TS = FIXTURES / "reference_target_oracle.ts"
EMITTER = ROOT / "forge" / "stage3_build" / "generate_threejs_factory.py"
FROZEN_PASS_ID = "blockout"


def _oracle_is_not_degenerate() -> None:
    """Anti-tamper guard: the recorded oracle must be a real, non-trivial TypeScript factory, not
    an empty or truncated file that would make the byte comparison pass for the wrong reason."""
    text = ORACLE_TS.read_text(encoding="utf-8")
    assert len(text.splitlines()) > 100, "oracle fixture looks truncated"
    assert "BoxGeometry" in text or "THREE." in text, "oracle fixture does not look like emitted Three.js"
    assert "export function create" in text, "oracle fixture is missing its factory export"


class ReferenceTargetOracleReplay(unittest.TestCase):
    """Regression oracle: `generate_threejs_factory.py`'s own CLI output for this frozen
    (spec, pass_id) must never silently drift. If it does, re-record the fixture in the same
    commit as the change that caused it -- do not relax this assertion."""

    def test_cli_replay_matches_the_frozen_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "replay.ts"
            assert_replay_matches_oracle(
                self,
                [
                    sys.executable, str(EMITTER), str(FROZEN_SPEC),
                    "--out", str(out), "--pass-id", FROZEN_PASS_ID, "--force",
                ],
                cwd=ROOT,
                expected_path=ORACLE_TS,
                out_path=out,
                tamper_guard=_oracle_is_not_degenerate,
            )


class ReferenceTargetByteEquality(unittest.TestCase):
    """D8's own conformance claim: the subprocess boundary changes nothing. The in-process
    `generate()` call and the CLI's `--out` file must be byte-identical for the same
    (spec, pass_id) -- this is what "the reference target conforms" actually means."""

    def test_in_process_and_subprocess_agree_byte_for_byte(self) -> None:
        spec = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))
        in_process = generator.generate(spec, FROZEN_PASS_ID)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "subprocess.ts"
            proc = subprocess.run(
                [sys.executable, str(EMITTER), str(FROZEN_SPEC), "--out", str(out), "--pass-id", FROZEN_PASS_ID],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            via_subprocess = out.read_text(encoding="utf-8")
        self.assertEqual(in_process, via_subprocess)


class ReferenceTargetBoundsParity(unittest.TestCase):
    """A stalled build trips the same first timeout a plugin target would. `_run_reference_target`
    invokes the emitter through `run_bounded` -- the identical function `_run_plugin_target` uses --
    so parity here is structural, not a parallel implementation to keep in sync by hand."""

    def test_run_bounded_kills_a_stalled_command_and_names_the_bound(self) -> None:
        with self.assertRaises(emit_target.EmitTargetError) as ctx:
            emit_target.run_bounded(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=ROOT, timeout=1, declared_env=[], target_kind="threejs-ts",
            )
        self.assertIn("timed out after 1s", str(ctx.exception))


class ReferenceTargetKindParity(unittest.TestCase):
    """The reference target's declared kind (`threejs-ts`) has no container prober, same as any
    plugin declaring a kind the base cannot structurally check -- it gets the identical
    existence/size/location verification level and the identical stated limit, not special
    treatment for being base-owned."""

    def test_unprobed_kind_gets_the_generic_verification_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "model.ts"
            artifact.write_text("// arbitrary", encoding="utf-8")
            result = emit_target.verify_artifact(artifact, reference_target().artifact_kind)
        self.assertEqual(result["level"], "existence+size+location")
        self.assertIn("no further base quality warranty applies", result["note"])


class ReferenceTargetEnvelopeParity(unittest.TestCase):
    """Scenario (spec.md): "The socket's parser handles the reference target's failure envelope" --
    WHEN the reference target is invoked against a spec that fails strict quality, THEN the
    socket's envelope parser SHALL classify the outcome as a quality block, distinguishable from a
    usage error.

    Reconciliation (decided by the lead after this test first failed exactly as task 3.10
    predicted): classify by envelope, not by exit code, with zero changes to
    `generate_threejs_factory.py`. See `emit_target._classify_reference_failure`'s docstring for
    the full reasoning; `main()`'s exit-2 overload between a BLOCKED envelope and plain argparse
    usage-error prose remains the emitter's own pre-existing surface, untouched here.
    """

    def test_a_strict_quality_block_is_classified_as_a_quality_block_not_a_usage_error(self) -> None:
        # A spec that is well-formed enough to have a completed pass (so emit_target.py's own
        # "at least one completed build pass" precondition is satisfied) but that fails strict
        # quality when the emitter's main() actually validates it -- exactly D8's scenario.
        shallow_spec = {
            "targetName": "Shallow",
            "targetId": "shallow",
            "schemaVersion": "2.1",
            "materials": [{"id": "base", "name": "Base", "baseColor": "#808080"}],
            "buildPasses": [{"id": "blockout", "acceptance": []}],
            "reviewHistory": [
                {
                    "passId": "blockout",
                    "action": "continue",
                    "visualEvidence": {"renderScreenshot": "x.png", "comparisonImage": "c.png"},
                    "aiVisionScore": 0.9,
                    "visualAcceptanceThreshold": 0.7,
                }
            ],
            "suitability": "pass",
            "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
            "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
            "proceduralStrategy": ["blockout"],
            "componentTree": [
                {
                    "id": "body", "name": "Body", "level": "macro", "role": "body",
                    "primitive": "ellipsoid", "topologyClass": "continuous-sculpt",
                    "topologyRationale": "test", "parent": None, "material": "base",
                    "dimensions": {"width": 1.0, "height": 0.6, "depth": 1.4, "units": "relative"},
                    "transform": {"position": [0, 0.5, 0], "rotation": [0, 0, 0]},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            spec_path = workspace / "spec.json"
            spec_path.write_text(json.dumps(shallow_spec), encoding="utf-8")
            with self.assertRaises(emit_target.EmitTargetError) as ctx:
                emit_target._run_reference_target(reference_target(), spec_path=spec_path, workspace=workspace)
            self.assertEqual(ctx.exception.classification, "quality-block")
            message = str(ctx.exception)
            self.assertIn("strict-quality blocked", message)
            # The cause list is surfaced verbatim, not flattened into an opaque summary.
            self.assertIn("missing preSpecAssessment", message)
            self.assertIn("refine-spec", message)  # the BLOCKED report's own nextAction

    def test_a_plain_usage_error_is_classified_as_error_not_a_quality_block(self) -> None:
        # Unit-level, at the classifier itself: coaxing generate_threejs_factory.py's real CLI into
        # its OTHER exit-2 shape (a bare argparse.error(), no JSON at all) requires a spec that
        # passes strict-quality but fails structural validation -- an edge case not worth chasing
        # through the full subprocess boundary when the classifier is the thing actually at risk.
        usage_error_stderr = b"usage: generate_threejs_factory.py [-h] --out OUT spec\ngenerate_threejs_factory.py: error: spec validation failed: componentTree must be a non-empty array\n"
        error = emit_target._classify_reference_failure(2, usage_error_stderr)
        self.assertEqual(error.classification, "error")
        self.assertIn("exited 2", str(error))
        self.assertIn("spec validation failed", str(error))

    def test_malformed_json_on_stderr_is_classified_as_error_never_a_pass(self) -> None:
        # "A malformed envelope is an error, not a pass" (gate_runner.parse_verdict's own rule) --
        # JSON that only half-parses, or parses but isn't a BLOCKED envelope, must not be mistaken
        # for a quality block either.
        half_json = b'{"status": "BLOCKED", "cause": [oops not valid json'
        self.assertEqual(emit_target._classify_reference_failure(2, half_json).classification, "error")
        wrong_shape = b'{"status": "ok", "somethingElse": true}'
        self.assertEqual(emit_target._classify_reference_failure(2, wrong_shape).classification, "error")


class ReferenceTargetSocketEndToEnd(unittest.TestCase):
    """The full socket path for `--target threejs-ts`: resolve -> action-ready -> temp-write ->
    emitter subprocess -> verify -> determinism -> rename -> provenance, with nothing pre-supplied.

    Regression for the pre-created out-path defect: `_run_reference_target` mkstemps the output
    file before the emitter runs, so its invocation must carry --force or every real
    reference-target run failed with "already exists" -- which no other test caught, because each
    one either passed --force itself or pointed the emitter at a path that did not exist yet."""

    def test_the_socket_produces_the_oracle_artifact_end_to_end(self) -> None:
        from forge.tests.test_emit_target import _action_ready_state_path
        from feature_acceptance_policy import feature_targets_for_pass

        spec = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))
        # The minimum honest completion record for the frozen pass: review_completes_pass demands
        # visual evidence, a passing vision score, a passing review per critical feature, and the
        # spec's own required layer scores.
        acceptance = spec["selfCorrectLoop"]["visualAcceptance"]
        spec["reviewHistory"] = [{
            "passId": FROZEN_PASS_ID, "action": "continue",
            "visualEvidence": {"renderScreenshot": "render.png", "comparisonImage": "compare.png"},
            "aiVisionScore": 0.9,
            "layerScores": {layer: 0.9 for layer in acceptance["requiredLayerScores"]},
            "featureReviews": [
                {"id": target["id"], "score": 0.95}
                for target in feature_targets_for_pass(spec, FROZEN_PASS_ID)
                if isinstance(target.get("id"), str)
            ],
        }]
        old_home = os.environ.get("IMG2_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            spec_path = workspace / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            _action_ready_state_path(workspace)
            os.environ["IMG2_HOME"] = str(Path(tmp) / "img2home-empty")
            try:
                rc = emit_target.main([
                    "--spec", str(spec_path), "--target", "threejs-ts", "--workspace", str(workspace),
                ])
            finally:
                if old_home is None:
                    os.environ.pop("IMG2_HOME", None)
                else:
                    os.environ["IMG2_HOME"] = old_home
            self.assertEqual(rc, 0)
            artifact = workspace / ".img2" / "artifacts" / "threejs-ts" / "model.ts"
            self.assertEqual(artifact.read_text(encoding="utf-8"), ORACLE_TS.read_text(encoding="utf-8"))
            provenance = json.loads(
                artifact.with_suffix(artifact.suffix + ".provenance.json").read_text(encoding="utf-8")
            )
            self.assertEqual(provenance["target"], "threejs-ts")
            self.assertTrue(provenance["determinismVerified"])


if __name__ == "__main__":
    unittest.main()
