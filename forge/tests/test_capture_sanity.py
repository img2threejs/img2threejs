#!/usr/bin/env python3
"""Unit tests for the pre-flight capture check.

Each case is a capture defect that actually cost a full correction loop on a real
reconstruction, because nothing surfaced it and the resulting numbers read as model defects:
an auto-framed camera pushed off by an oversized shadow catcher, a contact shadow counted as
foreground, and a pinned near/far pair that clipped the model once orbited.

`check()` is exercised directly on measurement dicts so the cases stay readable and no PNG
fixtures are needed; `measure()` is covered by the real-capture runs recorded in the changelog.

Run: python3 forge/tests/test_capture_sanity.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

from capture_sanity import check  # noqa: E402


def measurement(subject=0.21, largest=1.0, bbox_fraction=(0.98, 0.60), path="render.png"):
    return {
        "path": path,
        "resolution": [1600, 900],
        "subjectFraction": subject,
        "largestComponentFraction": largest,
        "bbox": [0, 0, 1, 1],
        "bboxFraction": list(bbox_fraction),
        "warnings": [],
        "maskStats": {},
    }


class CaptureSanityTest(unittest.TestCase):
    def test_a_well_framed_single_component_capture_is_usable(self):
        self.assertEqual(check(measurement(), None), [])

    def test_subject_too_small_is_reported_as_framing_not_model(self):
        failures = check(measurement(subject=0.008), None)
        self.assertEqual(len(failures), 1)
        self.assertIn("FRAMING failure, not a model failure", failures[0])

    def test_whole_frame_subject_is_reported_as_a_mask_fallback(self):
        """The segmenter falls back to whole-frame coverage on a near-empty capture, so a
        clipped or blank frame arrives as subjectFraction 1.0 rather than 0.0."""
        failures = check(measurement(subject=1.0), None)
        self.assertEqual(len(failures), 1)
        self.assertIn("whole-frame coverage", failures[0])

    def test_empty_frame_is_reported_and_short_circuits(self):
        failures = check(measurement(subject=0.0), None)
        self.assertEqual(len(failures), 1)
        self.assertIn("effectively empty", failures[0])
        self.assertIn("frustum", failures[0])

    def test_a_second_foreground_component_is_flagged_as_a_shadow(self):
        """The measured case: a contact shadow held 10% of the foreground and inflated the
        render's bbox height 22% while width matched to 0.6%, dragging IoU to 0.686."""
        failures = check(measurement(largest=0.89), None)
        self.assertEqual(len(failures), 1)
        self.assertIn("not the subject shares the frame", failures[0])

    def test_framing_mismatch_against_the_reference_is_flagged(self):
        reference = measurement(bbox_fraction=(0.98, 0.60), path="reference.png")
        render = measurement(bbox_fraction=(0.98, 0.90))  # 50% taller than the reference
        failures = check(render, reference)
        self.assertEqual(len(failures), 1)
        self.assertIn("subject height", failures[0])

    def test_matching_framing_against_the_reference_passes(self):
        reference = measurement(bbox_fraction=(0.9838, 0.6067), path="reference.png")
        render = measurement(bbox_fraction=(0.9856, 0.6033))
        self.assertEqual(check(render, reference), [])

    def test_defects_are_reported_together_not_first_only(self):
        """A capture is usually wrong in more than one way at once; reporting only the first
        would cost a loop per defect, which is the failure mode this gate exists to end."""
        failures = check(measurement(subject=0.005, largest=0.5), None)
        self.assertEqual(len(failures), 2)


if __name__ == "__main__":
    unittest.main()
