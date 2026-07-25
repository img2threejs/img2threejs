from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from forge.stage1_intake.cs2_manifest import (
    build_classification_record,
    build_manifest,
    persist_manifest,
    validate_manifest,
)


def write_png(path: Path, width: int = 128, height: int = 128) -> None:
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            value = 40 if 28 <= x < 100 and 12 <= y < 116 else 240
            row.extend((value, value, value))
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", payload) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Cs2ManifestTests(unittest.TestCase):
    def test_image_only_knife_manifest_requires_authoritative_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "knife.png"
            write_png(reference)
            manifest = build_manifest(reference, None)
            self.assertEqual(manifest["state"], "request-input")
            self.assertEqual(manifest["exactnessTier"], "image-only")
            self.assertTrue(validate_manifest(manifest))

    def test_classified_knife_proceeds_and_preserves_heuristic_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "knife.png"
            write_png(reference)
            classification = build_classification_record(
                "knife", "karambit", 0.98, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["itemFamily"], "knife")
            self.assertEqual(manifest["route"], "reference-projection")
            self.assertIn("heuristicSignal", manifest["warnings"])
            self.assertTrue(validate_manifest(manifest))

    def test_unsupported_family_never_receives_knife_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "rifle.png"
            write_png(reference)
            classification = build_classification_record("rifle", "ak47", 0.99, ["view:front:subject"])
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "unsupported-family")
            self.assertNotIn("componentAdapter", manifest)

    def test_manifest_write_is_atomic_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "knife.png"
            output = Path(directory) / "cs2-intake.json"
            write_png(reference)
            classification = build_classification_record("knife", "karambit", 0.9, ["view:front:subject"])
            manifest = build_manifest(reference, classification)
            persist_manifest(manifest, output)
            self.assertEqual(json.loads(output.read_text())["schemaVersion"], 1)
            self.assertFalse(output.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
