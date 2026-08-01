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
        # "equipment" (Zeus x27, C4, defuse kit, Kevlar) has its own real CS2 skins but no
        # geometry adapter yet -- distinct from an unrecognized/junk classification.
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "equipment.png"
            write_png(reference)
            classification = build_classification_record("equipment", "zeus-x27", 0.99, ["view:front:subject"])
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "unsupported-family")
            self.assertNotIn("componentAdapter", manifest)

    def test_unlisted_rifle_subtype_is_unsupported_subtype_not_family(self) -> None:
        # "rifle" is a supported family (AK-47 etc. have an adapter) but "ak47" (no hyphen,
        # not the registered "ak-47") has no fixture -- that must surface as
        # unsupported-subtype, distinct from an unknown family entirely.
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "rifle.png"
            write_png(reference)
            classification = build_classification_record("rifle", "ak47", 0.99, ["view:front:subject"])
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "unsupported-subtype")
            self.assertNotIn("componentAdapter", manifest)

    def test_classified_sniper_awp_proceeds_with_sniper_adapter(self) -> None:
        # AWP is a Sniper Rifle in CS2's own Market taxonomy, not a (semi-auto) Rifle -- see
        # cs2_adapters.py's _SNIPER/_RIFLE split.
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "awp.png"
            write_png(reference)
            classification = build_classification_record(
                "sniper", "awp", 0.9, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["itemFamily"], "sniper")
            self.assertEqual(manifest["componentAdapter"], "cs2-sniper-v1")
            self.assertTrue(validate_manifest(manifest))

    def test_classified_rifle_ak47_proceeds_with_rifle_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "ak47.png"
            write_png(reference)
            classification = build_classification_record(
                "rifle", "ak-47", 0.9, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["componentAdapter"], "cs2-rifle-v1")
            self.assertTrue(validate_manifest(manifest))

    def test_classified_smg_mp9_proceeds_with_smg_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "mp9.png"
            write_png(reference)
            classification = build_classification_record(
                "smg", "mp9", 0.9, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["componentAdapter"], "cs2-smg-v1")
            self.assertTrue(validate_manifest(manifest))

    def test_classified_heavy_xm1014_proceeds_with_heavy_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "xm1014.png"
            write_png(reference)
            classification = build_classification_record(
                "heavy", "xm1014", 0.9, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["componentAdapter"], "cs2-heavy-v1")
            self.assertTrue(validate_manifest(manifest))

    def test_classified_glove_sport_proceeds_with_glove_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "sport.png"
            write_png(reference)
            classification = build_classification_record(
                "glove", "sport", 0.9, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["componentAdapter"], "cs2-glove-v1")
            self.assertTrue(validate_manifest(manifest))

    def test_classified_pistol_glock18_proceeds_with_pistol_adapter(self) -> None:
        # Regression: the pistol adapter existed in cs2_adapters.py but SUPPORTED_FAMILIES
        # never included "pistol", so a Glock-18 classification could never reach `proceed`.
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "glock.png"
            write_png(reference)
            classification = build_classification_record(
                "pistol", "glock-18", 0.9, ["view:front:subject"], provider="offline-fixture"
            )
            manifest = build_manifest(reference, classification)
            self.assertEqual(manifest["state"], "proceed")
            self.assertEqual(manifest["componentAdapter"], "cs2-pistol-v1")
            self.assertTrue(validate_manifest(manifest))

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
