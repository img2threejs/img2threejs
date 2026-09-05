from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from forge.stage1_intake.probe_glb import probe_glb
from forge.stage4_review.render_bridge import (
    init_manifest,
    record_capture,
    record_reference_capture,
    validate_manifest,
    write_manifest,
)

PROFILE = Path(__file__).resolve().parents[2] / "docs" / "specs" / "render-profile.v2.example.json"


def write_png(path: Path, width: int = 8, height: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    rows = b"".join(b"\x00" + bytes([220, 80, 80]) * width for _ in range(height))
    path.write_bytes(
        signature
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def write_triangle_glb(path: Path) -> None:
    positions = struct.pack(
        "<9f",
        -1.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
    )
    document = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Root", "mesh": 0}],
        "meshes": [{"name": "Triangle", "primitives": [{"attributes": {"POSITION": 0}}]}],
        "materials": [{"name": "TestMaterial"}],
        "buffers": [{"byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [-1.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            }
        ],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    bin_chunk = positions + b"\x00" * ((4 - len(positions) % 4) % 4)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )


class GlbReferenceProbeTest(unittest.TestCase):
    def test_probe_extracts_reference_inventory_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.glb"
            write_triangle_glb(path)
            result = probe_glb(path)
            self.assertEqual(result["kind"], "glb-reference")
            self.assertEqual(result["referenceReadiness"], "pass")
            self.assertEqual(result["scene"]["meshCount"], 1)
            self.assertEqual(result["scene"]["materialCount"], 1)
            self.assertEqual(result["meshes"][0]["name"], "Triangle")
            self.assertEqual(result["bounds"]["min"], [-1.0, 0.0, 0.0])
            self.assertEqual(result["bounds"]["max"], [1.0, 1.0, 0.0])
            self.assertEqual(result["semanticDecomposition"]["status"], "insufficient")
            self.assertEqual(result["semanticDecomposition"]["reliableSemanticBoundary"], "multipart-glb-required")

    def test_render_manifest_requires_browser_glb_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            glb = root / "reference.glb"
            write_triangle_glb(glb)
            manifest_path = root / "manifest.json"
            manifest = init_manifest(
                None,
                "http://127.0.0.1:5173/#/character",
                manifest_path,
                (620, 1000),
                1.0,
                "captures",
                reference_glb=glb,
            )
            write_manifest(manifest_path, manifest)
            incomplete = validate_manifest(manifest_path, require_complete=True)
            self.assertFalse(incomplete["passed"])
            self.assertTrue(any("GLB reference baseline" in error for error in incomplete["errors"]))

            reference_shot = root / "reference" / "hero.png"
            procedural_shot = root / "captures" / "hero.png"
            write_png(reference_shot, 16, 16)
            write_png(procedural_shot, 16, 16)
            record_reference_capture(manifest_path, manifest, "hero", reference_shot)
            record_capture(manifest_path, manifest, "hero", procedural_shot)
            write_manifest(manifest_path, manifest)
            complete = validate_manifest(manifest_path, require_complete=False)
            self.assertTrue(complete["passed"])

    def test_v2_manifest_declares_shared_profile_and_six_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            glb = root / "reference.glb"
            write_triangle_glb(glb)
            manifest = init_manifest(
                None,
                "http://127.0.0.1:5173/#/character",
                root / "manifest.json",
                (620, 1000),
                1.0,
                "captures",
                reference_glb=glb,
                render_profile=PROFILE,
            )
            self.assertEqual(manifest["fidelityTrack"], "glb-mediated-v2")
            self.assertEqual(
                list(manifest["captures"][0]["passes"]),
                ["beauty", "alpha-silhouette", "semantic-id", "depth", "normal", "roughness-material-id"],
            )


class GlbContainerRefusesDuplicateBinChunks(unittest.TestCase):
    """`parse_glb` promises to refuse more than one BIN chunk; the duplicate check must be a
    seen-flag, not payload truthiness -- a zero-length first BIN chunk is falsy and let a second
    one through."""

    def test_a_second_bin_chunk_after_an_empty_one_is_refused(self) -> None:
        from forge._shared.glb_container import parse_glb

        payload = json.dumps({"asset": {"version": "2.0"}}).encode("utf-8")
        payload += b" " * ((4 - len(payload) % 4) % 4)
        chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
        chunks += struct.pack("<II", 0, 0x004E4942)
        chunks += struct.pack("<II", 4, 0x004E4942) + b"\x00\x00\x00\x00"
        blob = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "double-bin.glb"
            path.write_bytes(blob)
            with self.assertRaises(ValueError) as ctx:
                parse_glb(path)
        self.assertIn("more than one BIN chunk", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
