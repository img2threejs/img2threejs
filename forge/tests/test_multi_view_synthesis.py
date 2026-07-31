"""Regression tests for the fail-closed multi-view synthesis pipeline."""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from importlib import import_module
from importlib.util import find_spec
from io import StringIO
from pathlib import Path
import sys
from unittest.mock import patch

from forge.stage1b_multi_view.brief_enhancer import (
    enhance_spec_with_brief,
    get_brief_summary,
    load_brief_from_file,
)
from forge.stage1b_multi_view.benchmark import main as benchmark_main
from forge.stage1b_multi_view.benchmark import measure_synthesis
from forge.stage1b_multi_view.build_enhancer import (
    enhance_build_with_brief,
    generate_geometry_from_brief,
    get_brief_for_build,
)
from forge.stage1b_multi_view.depth_estimator import estimate_depth
from forge.stage1b_multi_view.feature_detector import (
    Feature,
    FeatureSet,
    clear_feature_cache,
    detect_features,
)
from forge.stage1b_multi_view.feature_matcher import (
    FeatureMatch,
    MatchResult,
    _calculate_match_confidence,
    match_pair,
)
from forge.stage1b_multi_view.integrate import (
    enhance_intake_record_with_synthesis,
    get_synthesis_summary,
    run_synthesis_for_intake,
)
from forge.stage1b_multi_view.hybrid_synthesis import combine_results, hybrid_synthesis
from forge.stage1b_multi_view.pose_estimator import CameraPose, PoseEstimationResult
from forge.stage1b_multi_view.review_enhancer import (
    aggregate_multi_view_scores,
    compare_multi_view,
    enhance_review_with_multi_view,
    generate_multi_view_report,
)
from forge.stage4_review.divine_eye import evaluate as evaluate_divine_eye
from forge.stage1b_multi_view.synthesize import synthesize_geometry_brief
from forge.stage1b_multi_view.validate_release_evidence import validate_release_evidence
from forge.stage1b_multi_view.validate_geometry_brief import validate_geometry_brief
from forge.stage1b_multi_view.view_counter import (
    cluster_unnamed_views,
    count_views,
    detect_named_views,
    detect_view_angles,
    group_duplicate_views,
)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_rgb_png(path: Path, width: int, height: int, pixel_fn) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            scanlines.extend(pixel_fn(x, y))
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + chunk(b"IEND", b"")
    )


def block_image(path: Path, offset: int = 0) -> None:
    write_rgb_png(
        path,
        64,
        64,
        lambda x, y: (200, 40, 40)
        if 12 + offset <= x < 52 + offset and 12 <= y < 52
        else (255, 255, 255),
    )


def textured_image(path: Path, offset: int = 0) -> None:
    write_rgb_png(
        path,
        160,
        160,
        lambda x, y: (
            ((x - offset) % 160 * 73 + y * 151 + ((x - offset) % 160) * y * 13) % 251,
            (((x - offset) % 160 * 73 + y * 151 + ((x - offset) % 160) * y * 13) * 17) % 256,
            (((x - offset) % 160 * 73 + y * 151 + ((x - offset) % 160) * y * 13) * 31) % 256,
        ),
    )


class ViewCounterTests(unittest.TestCase):
    def test_count_views_supports_one_to_many_inputs(self) -> None:
        for count in range(1, 8):
            self.assertEqual(count_views([Path(f"view-{index}.png") for index in range(count)]), count)

    def test_token_boundaries_prevent_false_view_names(self) -> None:
        result = detect_named_views([Path("frontier.png"), Path("leftover.png")])
        self.assertEqual(set(result), {"frontier", "leftover"})

    def test_repeated_named_views_are_preserved(self) -> None:
        result = detect_named_views(
            [Path("dragon-front.png"), Path("dragon-front-detail.png")]
        )
        self.assertEqual(set(result), {"front", "front-2"})

    def test_three_quarter_and_side_views_remain_distinct(self) -> None:
        paths = [
            Path("house-iso-front-right.png"),
            Path("house-iso-front-left.png"),
            Path("house-oblique-back-right.png"),
            Path("house-three-quarter-back-left.png"),
            Path("house-front.png"),
            Path("house-front-detail.png"),
            Path("house-back.png"),
            Path("house-side-elevation.png"),
        ]

        named = detect_named_views(paths)
        grouped = group_duplicate_views(paths, named)

        self.assertEqual(
            set(named),
            {
                "three-quarter-front-right",
                "three-quarter-front-left",
                "three-quarter-back-right",
                "three-quarter-back-left",
                "front",
                "front-2",
                "back",
                "side",
            },
        )
        self.assertEqual(
            set(grouped),
            {
                "three-quarter-front-right",
                "three-quarter-front-left",
                "three-quarter-back-right",
                "three-quarter-back-left",
                "front",
                "back",
                "side",
            },
        )

    def test_duplicate_view_group_prefers_higher_quality_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "controller-front.png"
            detailed = root / "controller-front-2.png"
            first.write_bytes(b"small")
            detailed.write_bytes(b"much-larger-reference")
            named_views = detect_named_views([first, detailed])
            grouped = group_duplicate_views([first, detailed], named_views)

        self.assertEqual(grouped["front"], detailed)

    def test_unnamed_images_get_content_cluster_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "capture-a.png"
            duplicate = root / "capture-b.png"
            different = root / "capture-c.png"
            block_image(first)
            block_image(duplicate)
            block_image(different, 16)
            clusters = cluster_unnamed_views([first, duplicate, different])
            names = detect_named_views([first, duplicate, different])

        self.assertEqual(len(clusters), 2)
        self.assertEqual({first, duplicate}, set(clusters["auto-1"]))
        self.assertEqual(set(names), {"auto-1", "auto-1-2", "auto-2"})

    def test_named_and_explicit_angle_views_get_expected_angles(self) -> None:
        views = {
            "front": Path("front.png"),
            "back": Path("rear.png"),
            "capture-45deg": Path("capture-45deg.png"),
            "unknown": Path("unknown.png"),
        }

        angles = detect_view_angles(views)

        self.assertEqual(angles["front"], 0)
        self.assertEqual(angles["back"], 180)
        self.assertEqual(angles["capture-45deg"], 45.0)
        self.assertNotIn("unknown", angles)


class FeatureMatcherTests(unittest.TestCase):
    def test_edge_features_match_without_opencv_descriptors(self) -> None:
        first = FeatureSet(
            features=[Feature(10, 10), Feature(20, 20), Feature(30, 30)],
            image_path=Path("front.png"),
            feature_count=3,
            detection_method="edge",
        )
        second = FeatureSet(
            features=[Feature(11, 10), Feature(21, 20), Feature(31, 30)],
            image_path=Path("rear.png"),
            feature_count=3,
            detection_method="edge",
        )

        result = match_pair(first, second)

        self.assertGreater(result.match_count, 0)
        self.assertGreater(result.confidence, 0.0)

    @unittest.skipUnless(find_spec("cv2") is not None, "requires OpenCV")
    def test_auto_uses_descriptor_features_when_opencv_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "textured.png"
            textured_image(image_path)
            result = detect_features(image_path)

        self.assertIn(result.detection_method, {"sift", "orb"})
        self.assertGreater(result.feature_count, 20)

    def test_feature_detection_reuses_unchanged_image_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "textured.png"
            textured_image(image_path)
            clear_feature_cache()
            first = detect_features(image_path)
            second = detect_features(image_path)

        self.assertIs(first, second)

    def test_match_confidence_is_bounded_when_descriptor_distance_is_large(self) -> None:
        first = FeatureSet([Feature(0, 0)], Path("first.png"), 1, "sift")
        second = FeatureSet([Feature(1, 0)], Path("second.png"), 1, "sift")
        confidence = _calculate_match_confidence(
            [FeatureMatch(first.features[0], second.features[0], 300.0, -2.0)],
            first,
            second,
        )

        self.assertEqual(confidence, 0.0)


class SynthesisTests(unittest.TestCase):
    def test_multiple_views_report_evidence_not_metric_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"view-{index}.png" for index in range(3)]
            for index, path in enumerate(paths):
                block_image(path, index)

            brief = synthesize_geometry_brief(paths)

        self.assertEqual(brief["status"], "evidence-only")
        self.assertLess(brief["confidence"], 0.5)
        self.assertEqual(brief["components"], {})
        self.assertFalse(brief["evidence"]["calibratedDepthAvailable"])
        self.assertEqual(validate_geometry_brief(brief), [])

    def test_intake_status_is_not_false_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"view-{index}.png" for index in range(2)]
            for index, path in enumerate(paths):
                block_image(path, index)
            result = run_synthesis_for_intake(paths, "Test Item")

        self.assertEqual(result["status"], "evidence-only")

    def test_calibrated_pair_emits_metric_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            front = root / "controller-front.png"
            rear = root / "controller-rear.png"
            textured_image(front)
            textured_image(rear, 5)
            brief = synthesize_geometry_brief(
                [front, rear],
                calibration={
                    "cameraMatrix": [[100.0, 0.0, 80.0], [0.0, 100.0, 80.0], [0.0, 0.0, 1.0]],
                    "focalLengthPx": 100.0,
                    "baselineUnits": 0.1,
                },
            )

        self.assertEqual(brief["status"], "complete")
        self.assertTrue(brief["evidence"]["calibratedDepthAvailable"])
        self.assertIn("root", brief["components"])
        self.assertGreater(brief["components"]["root"]["dimensions"]["width"], 0.0)

    def test_agent_calibration_claim_without_depth_does_not_promote_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"view-{index}.png" for index in range(2)]
            for index, path in enumerate(paths):
                block_image(path, index)
            result = hybrid_synthesis(
                paths,
                {
                    "calibrated": True,
                    "confidence": 0.9,
                    "components": {
                        "root": {
                            "dimensions": {"width": 2.0, "height": 1.0, "depth": 0.5},
                            "curvature": {"profile": "ellipsoid", "confidence": 0.8},
                            "confidence": 0.85,
                            "visibleIn": ["view-0", "view-1"],
                        }
                    },
                },
            )

        self.assertEqual(result["status"], "evidence-only")
        self.assertFalse(result["calibrated"])
        self.assertTrue(result["agentCalibrationClaim"])
        self.assertEqual(result["components"]["root"]["dimensions"]["width"], 2.0)
        self.assertEqual(result["components"]["root"]["curvature"]["profile"], "ellipsoid")

    def test_hybrid_falls_back_to_agent_evidence_when_deterministic_fails(self) -> None:
        module = import_module("forge.stage1b_multi_view.hybrid_synthesis")
        with patch.object(
            module,
            "synthesize_geometry_brief",
            side_effect=RuntimeError("feature extraction unavailable"),
        ):
            result = hybrid_synthesis(
                [Path("controller-front.png"), Path("controller-rear.png")],
                {
                    "confidence": 0.9,
                    "components": {
                        "shell": {
                            "dimensions": {"width": 2.0},
                            "confidence": 0.8,
                            "visibleIn": ["front", "rear"],
                        }
                    },
                },
            )

        self.assertEqual(result["status"], "evidence-only")
        self.assertEqual(result["synthesisMode"], "hybrid")
        self.assertIn("deterministicFailure", result)
        self.assertIn("shell", result["components"])

    def test_hybrid_reports_material_dimension_conflicts(self) -> None:
        result = combine_results(
            {
                "confidence": 0.9,
                "components": {
                    "shell": {
                        "dimensions": {"width": 2.0},
                        "confidence": 0.9,
                        "visibleIn": ["front", "rear"],
                    }
                },
                "evidence": {"calibratedDepthAvailable": True},
            },
            {
                "confidence": 0.8,
                "components": {
                    "shell": {
                        "dimensions": {"width": 3.0},
                        "confidence": 0.8,
                        "visibleIn": ["front", "rear"],
                    }
                },
            },
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["conflicts"][0]["dimension"], "width")
        self.assertEqual(result["conflicts"][0]["action"], "resolve-with-capture-evidence")


class ReviewTests(unittest.TestCase):
    def test_identical_images_use_real_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            render = root / "render.png"
            block_image(reference)
            block_image(render)

            result = compare_multi_view(
                {"front-primary": render},
                {"front-primary": reference},
                evaluator=evaluate_divine_eye,
            )

        self.assertTrue(result["complete"])
        self.assertTrue(result["passed"])
        self.assertGreater(result["aggregatedScore"], 0.9)
        self.assertEqual(
            result["perViewResults"]["front-primary"]["status"],
            "compared",
        )

    def test_missing_view_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "front.png"
            render = root / "front-render.png"
            rear = root / "rear.png"
            block_image(reference)
            block_image(render)
            block_image(rear)

            result = compare_multi_view(
                {"front-primary": render},
                {"front-primary": reference, "rear": rear},
                evaluator=evaluate_divine_eye,
            )

        self.assertFalse(result["complete"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["missingViews"], ["rear"])
        self.assertEqual(result["worstView"], "rear")
        self.assertEqual(result["worstScore"], 0.0)
        self.assertLessEqual(result["aggregatedScore"], 0.5)

    def test_aggregation_counts_missing_or_error_views(self) -> None:
        score = aggregate_multi_view_scores(
            {
                "front-primary": {"status": "compared", "score": 0.9},
                "rear": {"status": "missing", "score": 0.0},
            }
        )
        self.assertAlmostEqual(score, 0.45)


class CalibrationTests(unittest.TestCase):
    def test_uncalibrated_pose_does_not_generate_depth(self) -> None:
        match = FeatureMatch(Feature(10, 10), Feature(15, 10), 5, 0.9)
        match_result = MatchResult("front", "side", [match], 3, 0.9)
        pose = CameraPose(
            rotation=(0, 0, 0),
            translation=(0.1, 0, 0),
            confidence=0.9,
            view_name="side",
            calibrated=False,
        )
        poses = PoseEstimationResult(
            poses={},
            relative_poses={("front", "side"): pose},
            confidence=0.9,
        )

        result = estimate_depth(
            {("front", "side"): match_result},
            poses,
            focal_length_px=1000,
            baseline_units=0.1,
        )

        self.assertEqual(result.point_cloud, [])
        self.assertEqual(result.confidence, 0.0)

    def test_calibrated_pose_produces_depth_confidence(self) -> None:
        match = FeatureMatch(Feature(10, 10), Feature(15, 10), 5, 0.9)
        match_result = MatchResult("front", "side", [match, match, match], 3, 0.9)
        pose = CameraPose(
            rotation=(0, 0, 0),
            translation=(0.1, 0, 0),
            confidence=0.9,
            view_name="side",
            calibrated=True,
        )
        poses = PoseEstimationResult(
            poses={},
            relative_poses={("front", "side"): pose},
            confidence=0.9,
        )

        result = estimate_depth(
            {("front", "side"): match_result},
            poses,
            focal_length_px=1000,
            baseline_units=0.1,
        )

        self.assertGreater(result.confidence, 0.0)


class PerformanceTests(unittest.TestCase):
    def test_six_view_synthesis_meets_local_budget_and_reports_warm_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"view-{index}.png" for index in range(6)]
            for index, path in enumerate(paths):
                block_image(path, index)
            result = measure_synthesis(paths)

        self.assertEqual(result["viewCount"], 6)
        self.assertLess(result["coldSeconds"], 5.0)
        self.assertGreaterEqual(result["warmSeconds"], 0.0)

    def test_benchmark_cli_emits_machine_readable_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "view.png"
            block_image(image_path)
            output = StringIO()
            with patch.object(sys, "argv", ["benchmark.py", str(image_path)]), redirect_stdout(output):
                exit_code = benchmark_main()

        self.assertEqual(exit_code, 0)
        self.assertIn('"viewCount": 1', output.getvalue())


class MutationTests(unittest.TestCase):
    def test_enhancers_do_not_mutate_callers(self) -> None:
        brief = {
            "viewCount": 3,
            "components": {"body": {"dimensions": {"width": 150}}},
        }
        spec = {"components": {"body": {"dimensions": {"width": 100}}}}
        build = {"components": {}}
        intake = {"itemName": "Test"}

        enhanced_spec = enhance_spec_with_brief(spec, brief)
        enhanced_build = enhance_build_with_brief(build, brief)
        enhanced_intake = enhance_intake_record_with_synthesis(
            intake,
            {"status": "evidence-only", "brief": brief},
        )

        self.assertEqual(spec["components"]["body"]["dimensions"]["width"], 100)
        self.assertEqual(build, {"components": {}})
        self.assertEqual(intake, {"itemName": "Test"})
        self.assertEqual(
            enhanced_spec["components"]["body"]["dimensions"]["width"],
            150,
        )
        self.assertIn("body", enhanced_build["components"])
        self.assertIn("synthesis", enhanced_intake)


class EnhancerTests(unittest.TestCase):
    def test_evidence_only_brief_keeps_provenance_without_empty_component_enhancement(self) -> None:
        brief = {"viewCount": 3, "synthesisMode": "standard", "components": {}}

        spec = enhance_spec_with_brief({"components": {"shell": {}}}, brief)
        build = enhance_build_with_brief({"components": {"shell": {}}}, brief)

        self.assertEqual(spec["components"], {"shell": {}})
        self.assertEqual(build["components"], {"shell": {}})
        self.assertEqual(spec["multiViewBrief"]["viewCount"], 3)
        self.assertEqual(build["multiViewBrief"]["viewCount"], 3)

    def test_spec_and_build_enhancers_merge_existing_and_new_components(self) -> None:
        brief = {
            "viewCount": 2,
            "synthesisMode": "basic",
            "confidence": 0.6,
            "components": {
                "shell": {
                    "dimensions": {"width": 2.0, "height": 1.0, "depth": 0.5},
                    "curvature": "rounded",
                    "confidence": 0.6,
                    "visibleIn": ["front", "rear"],
                },
                "trigger": {"dimensions": {"length": 0.3}, "confidence": 0.4},
            },
        }
        spec = enhance_spec_with_brief({"components": {"shell": {}}}, brief)
        build = enhance_build_with_brief({"components": {"shell": {}}}, brief)

        self.assertEqual(spec["components"]["shell"]["dimensions"]["width"], 2.0)
        self.assertEqual(spec["components"]["shell"]["visibleInViews"], ["front", "rear"])
        self.assertEqual(spec["components"]["trigger"]["source"], "multi-view-synthesis")
        self.assertEqual(build["components"]["shell"]["curvature"], "rounded")
        self.assertEqual(build["components"]["trigger"]["dimensions"]["length"], 0.3)
        self.assertIn("2 views", get_brief_summary(brief))
        code = generate_geometry_from_brief("shell", brief["components"]["shell"])
        self.assertIn("BoxGeometry(2.0, 1.0, 0.5)", code)

    def test_brief_loaders_handle_valid_missing_and_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "brief.json"
            invalid = root / "invalid.json"
            valid.write_text('{"viewCount": 2}', encoding="utf-8")
            invalid.write_text("not json", encoding="utf-8")

            self.assertEqual(load_brief_from_file(valid), {"viewCount": 2})
            self.assertEqual(get_brief_for_build(valid), {"viewCount": 2})
            self.assertIsNone(load_brief_from_file(root / "missing.json"))
            self.assertIsNone(get_brief_for_build(root / "missing.json"))
            self.assertIsNone(load_brief_from_file(invalid))
            self.assertIsNone(get_brief_for_build(invalid))
            self.assertIsNone(get_brief_for_build(None))

    def test_review_enhancer_adds_coverage_and_report(self) -> None:
        brief = {
            "viewCount": 2,
            "synthesisMode": "basic",
            "confidence": 0.5,
            "namedViews": {"front": "front.png", "rear": "rear.png"},
            "components": {"shell": {"visibleIn": ["front"]}},
        }
        config = enhance_review_with_multi_view({}, brief)
        report = generate_multi_view_report(
            {
                "viewCount": 2,
                "aggregatedScore": 0.7,
                "perViewResults": {"front": {"status": "compared", "score": 0.7}},
            },
            brief,
        )

        self.assertEqual(config["viewCoverage"]["componentCoverage"]["shell"]["coveragePercentage"], 50.0)
        self.assertIn("Multi-View Comparison Report", report)
        self.assertAlmostEqual(
            aggregate_multi_view_scores(
                {"front": {"score": 0.9}, "rear": {"score": 0.5}},
                {"front": 2.0, "rear": 1.0},
            ),
            (0.9 * 2.0 + 0.5) / 3.0,
        )


class IntakeIntegrationTests(unittest.TestCase):
    def test_intake_writes_brief_and_summarizes_each_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            front = root / "front.png"
            rear = root / "rear.png"
            output_dir = root / "output"
            block_image(front)
            block_image(rear, 1)
            result = run_synthesis_for_intake([front, rear], "Test Item", output_dir)

        # Opposing view pairs (front/rear) without calibration now produce
        # "complete" status with silhouette-based confidence.
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["outputPath"].endswith("test_item_geometry_brief.json"))
        self.assertIn("complete", get_synthesis_summary(result))
        self.assertIn("Single view", get_synthesis_summary({"status": "skipped"}))
        self.assertIn("failed (bad input)", get_synthesis_summary({"status": "failed", "error": "bad input"}))
        self.assertEqual(get_synthesis_summary({"status": "request-input"}), "Synthesis status: request-input")

    def test_intake_returns_failed_result_when_synthesis_raises(self) -> None:
        module = import_module("forge.stage1b_multi_view.integrate")
        with patch.object(module, "synthesize_geometry_brief", side_effect=ValueError("bad image")):
            result = run_synthesis_for_intake([Path("front.png"), Path("rear.png")], "Test Item")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["confidence"], 0.0)


class ReleaseEvidenceTests(unittest.TestCase):
    def test_release_evidence_accepts_empirical_multi_view_pass(self) -> None:
        errors = validate_release_evidence(
            {
                "target": "PS5 DualSense",
                "baseline": {"iterationCount": 40, "evidenceRef": "baseline-review.json"},
                "multiView": {
                    "iterationCount": 12,
                    "sourceImages": ["front.png", "rear.png", "left.png"],
                    "review": {
                        "complete": True,
                        "passed": True,
                        "perViewResults": {
                            "front": {"status": "compared", "verdict": "pass"},
                            "rear": {"status": "compared", "verdict": "pass"},
                        },
                    },
                },
            }
        )

        self.assertEqual(errors, [])

    def test_release_evidence_rejects_unproven_iteration_claim(self) -> None:
        errors = validate_release_evidence(
            {
                "target": "PS5 DualSense",
                "baseline": {"iterationCount": 2, "evidenceRef": "baseline-review.json"},
                "multiView": {
                    "iterationCount": 16,
                    "sourceImages": ["front.png"],
                    "review": {"complete": False, "passed": False, "perViewResults": {}},
                },
            }
        )

        self.assertIn("baseline.iterationCount must be an integer >= 40", errors)
        self.assertIn("multiView.iterationCount must be an integer from 1 to 15", errors)
        self.assertIn("multiView.sourceImages must contain at least two non-empty paths", errors)


if __name__ == "__main__":
    unittest.main()
