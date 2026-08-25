#!/usr/bin/env python3
"""Tests for the stage 4 hair gate and its wiring into the pass orchestrator.

The gate's whole reason for existing is the HARD/SOFT split. Two failures look similar in a coverage
number and want opposite responses:

  bald patch          the skull shows through. Always wrong. HARD.
  coverage shortfall  less hair than the reference. Often the right compromise. SOFT.

Reading the second as the first is what produced the recorded regression: a shortfall was answered
by widening the masses, the widening pushed them off the skull, and closure went 42.2% to 40.9% --
worse on all six views -- while crown scalp exposure rose 14.9 points on the worst one.

Run: python3 forge/tests/test_hair_gate.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage3_build"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

from hair_gate import BAND_SHORTFALL_SOFT, compare_views, hair_gate  # noqa: E402
from orchestrate_passes import next_required_evidence, spec_has_hair  # noqa: E402
from test_hair_evidence import HEAD_ROWS, head_image  # noqa: E402

PASSING_EXPOSURE = {"verdict": "pass", "exposedFraction": 0.0, "hardMax": 0.05}
FAILING_EXPOSURE = {"verdict": "fail", "exposedFraction": 0.31, "hardMax": 0.05}


class HardBeatsSoft(unittest.TestCase):
    def test_a_bald_patch_fails_even_when_every_view_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path}, FAILING_EXPOSURE)
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(report["softSignals"], [])
        self.assertTrue(any("skull is uncovered" in h for h in report["hardFailures"]))

    def test_a_coverage_shortfall_alone_is_review_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "ref.png"
            render = Path(directory) / "out.png"
            head_image(reference, hair_rows=HEAD_ROWS - 4)
            head_image(render, hair_rows=HEAD_ROWS // 3)
            report = hair_gate({"front": reference}, {"front": render}, PASSING_EXPOSURE)
        self.assertEqual(report["verdict"], "review")
        self.assertEqual(report["hardFailures"], [])
        self.assertTrue(report["softSignals"])

    def test_an_identical_pair_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path}, PASSING_EXPOSURE)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["hardFailures"], [])
        self.assertEqual(report["softSignals"], [])

    def test_the_report_says_a_shortfall_does_not_authorise_widening(self) -> None:
        """The instruction that would have prevented the recorded regression."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path}, PASSING_EXPOSURE)
        self.assertIn("never on its own authorises widening", report["note"])
        self.assertIn("42.2%", report["note"])


class MissingExposureIsAGapNotAPass(unittest.TestCase):
    def test_omitting_the_geometric_gate_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path})
        self.assertEqual(report["verdict"], "review")
        self.assertTrue(any("scalpExposure was not supplied" in s for s in report["softSignals"]))

    def test_the_missing_channel_is_a_flag_not_just_prose(self) -> None:
        """So a caller can branch on it. Collapsed into the verdict word, "review because I could
        not check" was indistinguishable from "review, here are some notes"."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            self.assertFalse(hair_gate({"front": path}, {"front": path})["hardChannelPresent"])
            self.assertTrue(
                hair_gate({"front": path}, {"front": path}, PASSING_EXPOSURE)["hardChannelPresent"]
            )

    def test_the_cli_does_not_exit_zero_when_the_hard_channel_never_ran(self) -> None:
        """A caller that only checks for zero would have read a missing bald-patch check as a pass."""
        import hair_gate as module  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            exposure = Path(directory) / "exposure.json"
            exposure.write_text(json.dumps(PASSING_EXPOSURE))

            import contextlib, io  # noqa: PLC0415
            def run(argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    return module.main(argv)

            base = ["--reference", f"front={path}", "--render", f"front={path}"]
            self.assertEqual(run(base), 2, "no exposure report supplied")
            self.assertEqual(run(base + ["--scalp-exposure", str(exposure)]), 0)

            failing = Path(directory) / "failing.json"
            failing.write_text(json.dumps(FAILING_EXPOSURE))
            self.assertEqual(run(base + ["--scalp-exposure", str(failing)]), 1)

    def test_the_reason_is_stated_not_just_the_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path})
        joined = " ".join(report["softSignals"])
        self.assertIn("cannot reliably see a bald patch", joined)


class Deltas(unittest.TestCase):
    def build(self, reference_bands, render_bands, **overrides):
        def view(bands, **extra):
            payload = {
                "status": "measured",
                "bands": {k: {"coverage": v} for k, v in bands.items()},
                "hairline": 0.05,
                "hairFraction": 0.4,
                "shading": {"specularBandV": 0.5},
            }
            payload.update(extra)
            return payload

        return compare_views(
            {"views": {"front": view(reference_bands)}},
            {"views": {"front": view(render_bands, **overrides)}},
        )

    def test_a_shortfall_past_the_threshold_is_flagged(self) -> None:
        views = self.build({"crown": 0.9}, {"crown": 0.9 - BAND_SHORTFALL_SOFT - 0.01})
        self.assertTrue(views["front"]["bands"]["crown"]["shortfall"])

    def test_a_shortfall_under_the_threshold_is_not(self) -> None:
        views = self.build({"crown": 0.9}, {"crown": 0.9 - BAND_SHORTFALL_SOFT + 0.01})
        self.assertFalse(views["front"]["bands"]["crown"]["shortfall"])

    def test_having_MORE_hair_than_the_reference_is_not_a_shortfall(self) -> None:
        views = self.build({"crown": 0.5}, {"crown": 0.9})
        self.assertFalse(views["front"]["bands"]["crown"]["shortfall"])
        self.assertGreater(views["front"]["bands"]["crown"]["delta"], 0)

    def test_a_view_absent_from_the_render_is_reported(self) -> None:
        views = compare_views(
            {"views": {"rear": {"status": "measured", "bands": {}}}}, {"views": {}}
        )
        self.assertEqual(views["rear"]["status"], "missing-from-render")

    def test_an_unmeasurable_view_carries_both_statuses(self) -> None:
        views = compare_views(
            {"views": {"front": {"status": "no-hair-skin-split"}}},
            {"views": {"front": {"status": "measured", "bands": {}}}},
        )
        self.assertEqual(views["front"]["status"], "not-measurable")
        self.assertEqual(views["front"]["referenceStatus"], "no-hair-skin-split")


class ShadingIsNamedAsMaterial(unittest.TestCase):
    def test_a_displaced_highlight_says_adding_hair_will_not_fix_it(self) -> None:
        """The correction that four geometric attempts never found."""
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "ref.png"
            render = Path(directory) / "out.png"
            head_image(reference, hair_rows=HEAD_ROWS - 4, highlight_row=8)
            head_image(render, hair_rows=HEAD_ROWS - 4, highlight_row=HEAD_ROWS - 2)
            report = hair_gate({"front": reference}, {"front": render}, PASSING_EXPOSURE)
        joined = " ".join(report["softSignals"])
        self.assertIn("MATERIAL difference", joined)
        self.assertIn("adding hair will not move it", joined)


class Thresholds(unittest.TestCase):
    def test_the_thresholds_declare_themselves_uncalibrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path}, PASSING_EXPOSURE)
        self.assertTrue(report["thresholds"]["uncalibrated"])

    def test_what_neither_side_observed_is_carried_through(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.png"
            head_image(path, hair_rows=HEAD_ROWS // 2)
            report = hair_gate({"front": path}, {"front": path}, PASSING_EXPOSURE)
        self.assertTrue(any("rear" in n for n in report["referenceNotObserved"]))
        self.assertTrue(any("rear" in n for n in report["renderNotObserved"]))


class OrchestratorWiring(unittest.TestCase):
    def hair_spec(self) -> dict:
        return {
            "componentTree": [
                {"id": "head", "primitive": "ellipsoid", "role": "head"},
                {"id": "hair", "primitive": "tapered-sweep", "role": "hair", "parent": "head"},
            ]
        }

    def test_a_hair_subject_is_detected_by_component_role(self) -> None:
        self.assertTrue(spec_has_hair(self.hair_spec()))

    def test_a_hair_subject_is_detected_by_profile_alone(self) -> None:
        self.assertTrue(spec_has_hair({"hairProfile": {"scalpComponentId": "head"}}))

    def test_a_chair_is_not_a_hair_subject(self) -> None:
        self.assertFalse(spec_has_hair({"componentTree": [{"id": "seat", "role": "panel"}]}))
        self.assertFalse(spec_has_hair({}))

    def test_scalp_exposure_is_demanded_for_a_visual_pass_on_a_hair_subject(self) -> None:
        evidence = " ".join(next_required_evidence(self.hair_spec(), "structural-pass"))
        self.assertIn("scalp_exposure.py", evidence)
        self.assertIn("before rendering", evidence)

    def test_the_hair_gate_is_demanded_too(self) -> None:
        evidence = " ".join(next_required_evidence(self.hair_spec(), "structural-pass"))
        self.assertIn("hair_gate.py", evidence)

    def test_a_non_hair_subject_is_not_asked_for_hair_evidence(self) -> None:
        evidence = " ".join(
            next_required_evidence({"componentTree": [{"id": "seat", "role": "panel"}]},
                                   "structural-pass")
        )
        self.assertNotIn("scalp_exposure.py", evidence)
        self.assertNotIn("hair_gate.py", evidence)

    def test_the_completed_pass_demands_nothing(self) -> None:
        self.assertEqual(next_required_evidence(self.hair_spec(), "complete"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
