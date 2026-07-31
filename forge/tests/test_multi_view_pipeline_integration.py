from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from forge.stage2_spec.new_pre_spec_assessment import make_payload
from forge.stage2_spec.new_sculpt_spec import make_spec
from forge.stage2_spec.validate_sculpt_spec import validate_spec
from forge.stage3_build.generate_threejs_factory import generate
from forge.stage4_review.divine_eye import evaluate_multiple


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_png(path: Path, offset: int) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    width = height = 24
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            foreground = 4 + offset <= x < 18 + offset and 4 <= y < 18
            rows.extend((40, 120, 220) if foreground else (255, 255, 255))
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


class MultiViewPipelineIntegrationTest(unittest.TestCase):
    def test_multiple_images_flow_into_spec_and_generated_factory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            front = root / "controller-front.png"
            rear = root / "controller-back.png"
            write_png(front, 0)
            write_png(rear, 1)

            assessment = make_payload(
                "Controller",
                [str(front), str(rear)],
                "moderate",
            )
            spec = make_spec("Controller", str(front), assessment)
            factory = generate(spec, "blockout")

        self.assertEqual(assessment["sourceImages"], [str(front), str(rear)])
        self.assertEqual(assessment["multiViewSynthesis"]["status"], "proceed")
        self.assertEqual(spec["sourceImages"], [str(front), str(rear)])
        self.assertIn("sourceImages", factory)

    def test_assessment_preserves_agent_geometry_without_unverified_metric_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            front = root / "controller-front.png"
            rear = root / "controller-rear.png"
            write_png(front, 0)
            write_png(rear, 1)
            assessment = make_payload(
                "Controller",
                [str(front), str(rear)],
                "moderate",
                multi_view_analysis={
                    "calibrated": True,
                    "confidence": 0.9,
                    "components": {
                        "root": {
                            "dimensions": {"width": 2.0, "height": 1.0, "depth": 0.5},
                            "confidence": 0.85,
                            "visibleIn": ["front", "rear"],
                        }
                    },
                },
            )

        self.assertEqual(assessment["multiViewSynthesis"]["status"], "evidence-only")
        self.assertEqual(
            assessment["multiViewSynthesis"]["brief"]["components"]["root"]["dimensions"]["width"],
            2.0,
        )

    def test_single_image_remains_backward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "controller-front.png"
            write_png(image, 0)
            assessment = make_payload("Controller", [str(image)], "moderate")
            spec = make_spec("Controller", str(image), assessment)

        self.assertEqual(assessment["sourceImage"], str(image))
        self.assertEqual(assessment["sourceImages"], [str(image)])
        self.assertEqual(assessment["multiViewSynthesis"]["status"], "proceed")
        self.assertEqual(spec["sourceImage"], str(image))
        self.assertEqual(spec["sourceImages"], [str(image)])

    def test_review_rejects_when_one_required_view_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            front = root / "front.png"
            rear = root / "rear.png"
            render = root / "front-render.png"
            write_png(front, 0)
            write_png(rear, 1)
            write_png(render, 0)
            result = evaluate_multiple(
                {"front": front, "rear": rear},
                {"front": render},
            )

        self.assertFalse(result["complete"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["missingViews"], ["rear"])

    def test_brief_dimensions_override_blockout_dimensions(self) -> None:
        assessment = {
            "sourceImages": ["front.png", "rear.png"],
            "multiViewSynthesis": {
                "status": "evidence-only",
                "brief": {
                    "components": {
                        "root": {
                            "dimensions": {"width": 2.0, "height": 3.0, "depth": 4.0},
                            "curvature": {"profile": "ellipsoid", "confidence": 0.8},
                            "confidence": 0.8,
                        }
                    }
                },
            },
        }
        spec = make_spec("Controller", "front.png", assessment)
        factory = generate(spec, "blockout")

        self.assertEqual(spec["componentTree"][0]["multiViewDimensions"], {"width": 2.0, "height": 3.0, "depth": 4.0})
        self.assertEqual(spec["componentTree"][0]["multiViewCurvature"]["profile"], "ellipsoid")
        self.assertIn("scale.set(2.0, 3.0, 4.0)", factory)
        self.assertIn("SphereGeometry(0.5, 64, 40)", factory)

    def test_validator_rejects_inconsistent_multi_view_provenance(self) -> None:
        spec = make_spec("Controller", "front.png")
        spec["sourceImages"] = ["rear.png"]
        spec["multiViewSynthesis"] = {"status": "evidence-only", "viewCount": 2, "confidence": 0.3}

        errors, _warnings = validate_spec(spec)

        self.assertIn("sourceImage must match the first sourceImages entry", errors)

    def test_validator_rejects_invalid_multi_view_curvature_confidence(self) -> None:
        spec = make_spec("Controller", "front.png")
        spec["componentTree"][0]["multiViewCurvature"] = {
            "profile": "ellipsoid",
            "confidence": 1.2,
        }

        errors, _warnings = validate_spec(spec)

        self.assertIn("component 'root' multiViewCurvature.confidence must be a number from 0 to 1", errors)


if __name__ == "__main__":
    unittest.main()
