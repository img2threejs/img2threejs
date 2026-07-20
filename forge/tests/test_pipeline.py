#!/usr/bin/env python3
"""End-to-end integration tests for the Three.js Object Sculptor pipeline.

Pure stdlib. Runs each CLI script as a subprocess and asserts the gate behavior
described in SKILL.md / references. Also generates a tiny real PNG (struct+zlib)
to exercise the image-consuming scripts without any third-party deps.

Run: python3 forge/tests/test_pipeline.py   (from skill root)
  or: python3 -m unittest discover -s forge/tests
"""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
SCRIPTS = SKILL / "forge"


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True,
    )


def write_png(path, w=64, h=64):
    """Write a minimal valid RGB PNG with a simple gradient (no PIL)."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 per scanline
        for x in range(w):
            raw += bytes(((x * 4) % 256, (y * 4) % 256, ((x + y) * 2) % 256))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.assessment = self.dir / "assessment.json"
        self.spec = self.dir / "object-sculpt-spec.json"
        self.ref = self.dir / "ref.png"
        self.render = self.dir / "render.png"
        write_png(self.ref)
        write_png(self.render)

    def test_probe_image(self):
        r = run("stage1_intake/probe_image.py", self.ref)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("64", r.stdout)  # reports dimensions

    def test_assessment_and_spec(self):
        r = run("stage2_spec/new_pre_spec_assessment.py", "Oak", "--complexity", "complex",
                "--out", self.assessment)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.assessment.exists())
        self.assertIn("qualityContract", json.loads(self.assessment.read_text()))

        r = run("stage2_spec/new_sculpt_spec.py", "Oak", "--assessment", self.assessment,
                "--out", self.spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        spec = json.loads(self.spec.read_text())
        self.assertEqual(spec["schemaVersion"], "2.0")
        self.assertEqual(spec["targetName"], "Oak")

    def test_normal_validate_passes_strict_fails_on_shallow(self):
        run("stage2_spec/new_pre_spec_assessment.py", "Oak", "--complexity", "complex",
            "--out", self.assessment)
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--assessment", self.assessment,
            "--out", self.spec)
        # normal validation of a structurally-sound starter succeeds
        self.assertEqual(run("stage2_spec/validate_sculpt_spec.py", self.spec).returncode, 0)
        # strict quality gate must BLOCK a shallow starter spec
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("strict quality failure", strict.stdout + strict.stderr)

    def test_orchestrator_starts_at_blockout(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        r = run("stage3_build/orchestrate_passes.py", "status", self.spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("blockout", r.stdout)
        # a future pass must be locked
        locked = run("stage3_build/orchestrate_passes.py", "check", self.spec,
                     "--pass-id", "material-pass")
        self.assertNotEqual(locked.returncode, 0)

    def test_generate_factory_emits_typescript(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        out = self.dir / "createObjectModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("import * as THREE from 'three'", ts)
        self.assertIn("sculptRuntime", ts)
        # generating a locked future pass must fail
        locked = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out,
                     "--pass-id", "lighting-pass")
        self.assertNotEqual(locked.returncode, 0)

    def test_comparison_sheet_packages_without_scoring(self):
        cmp = self.dir / "cmp.png"
        r = run("stage4_review/make_comparison_sheet.py", "--reference", self.ref,
                "--render", self.render, "--out", cmp, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(cmp.exists() and cmp.stat().st_size > 0)

    def test_append_review_gate_and_record(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        # GATE: continue on a visual pass WITHOUT screenshot evidence must be refused.
        no_evidence = run("stage4_review/append_review.py", self.spec, "--pass-id", "blockout",
                          "--fidelity", "0.8", "--action", "continue",
                          "--summary", "no evidence", "--ai-vision-score", "0.8",
                          "--in-place")
        self.assertNotEqual(no_evidence.returncode, 0)
        self.assertIn("render-screenshot", no_evidence.stdout + no_evidence.stderr)
        # WITH evidence: the review is recorded.
        cmp = self.dir / "cmp.png"
        run("stage4_review/make_comparison_sheet.py", "--reference", self.ref,
            "--render", self.render, "--out", cmp)
        layers = json.dumps({
            "silhouetteProportion": 0.82, "componentStructure": 0.78,
            "formDetail": 0.75, "materialSurface": 0.7, "lightingCamera": 0.8,
        })
        # every critical feature target of this pass needs an AI-vision review entry
        spec = json.loads(self.spec.read_text())
        targets = spec.get("selfCorrectLoop", {}).get("featureReviewTargets", [])
        reviews = [
            {"id": t.get("id"), "score": 0.8, "visible": True, "notes": "acceptable"}
            for t in targets if t.get("tier") == "critical"
        ] or [{"id": "overall-silhouette", "score": 0.8, "visible": True, "notes": "ok"}]
        freviews = self.dir / "features.json"
        freviews.write_text(json.dumps(reviews))
        r = run("stage4_review/append_review.py", self.spec, "--pass-id", "blockout",
                "--fidelity", "0.8", "--action", "continue",
                "--summary", "Blockout silhouette acceptable.",
                "--render-screenshot", self.render, "--comparison-image", cmp,
                "--ai-vision-score", "0.8", "--layer-scores-json", layers,
                "--feature-reviews-json", freviews,
                "--camera-view", "front", "--in-place")
        self.assertEqual(r.returncode, 0, r.stderr)
        spec = json.loads(self.spec.read_text())
        self.assertTrue(len(spec.get("reviewHistory", [])) >= 1)

    def test_pbr_extraction_runs(self):
        # low-detail synthetic image: either passes or refuses (non-zero) — both are valid,
        # but it must not crash and must respect the confidence gate.
        r = run("stage1_intake/extract_pbr_evidence.py", self.ref, "--out-dir", self.dir / "pbr",
                "--material-id", "bark", "--target-threshold", "0.7",
                "--report", self.dir / "pbr-report.json")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        self.assertTrue((self.dir / "pbr-report.json").exists() or r.returncode == 1)

    # ---- Track A / Track B upgrade coverage ----

    def _fresh_spec(self, complexity="moderate"):
        run("stage2_spec/new_pre_spec_assessment.py", "Widget", "--complexity", complexity,
            "--out", self.assessment)
        run("stage2_spec/new_sculpt_spec.py", "Widget", "--assessment", self.assessment,
            "--out", self.spec)
        return json.loads(self.spec.read_text())

    def test_new_schema_fields_present(self):
        spec = self._fresh_spec("complex")
        pre = spec["preSpecAssessment"]
        self.assertIn("detailInventory", pre)
        self.assertIn("anatomy", pre)
        self.assertIn("primaryDomain", pre["objectClass"])
        self.assertIn("referenceCamera", spec)
        # targetMinDetails scales with complexity
        self.assertEqual(pre["detailInventory"]["targetMinDetails"], 10)

    def test_detail_inventory_gate_fires_on_empty(self):
        self._fresh_spec("moderate")
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("detailInventory has 0 details", strict.stdout + strict.stderr)

    def test_detail_inventory_backward_compatible(self):
        # A spec with NO detailInventory (pre-upgrade shape) must not trigger the detail gate.
        spec = self._fresh_spec("moderate")
        spec["preSpecAssessment"].pop("detailInventory", None)
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotIn("detailInventory", strict.stdout + strict.stderr)

    def test_character_gate_requires_anatomy(self):
        spec = self._fresh_spec("moderate")
        spec["preSpecAssessment"]["objectClass"]["primaryDomain"] = "character"
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertIn("anatomy.applies is not true", strict.stdout + strict.stderr)

    def test_character_track_skipped_for_objects(self):
        # primaryDomain unassessed/object must not trigger character warnings.
        self._fresh_spec("moderate")
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotIn("anatomy.applies", strict.stdout + strict.stderr)

    def test_new_upgrade_scripts_help(self):
        for script in ("stage1_intake/build_detail_inventory.py", "stage1_intake/extract_landmarks.py",
                       "stage1_intake/solve_camera_pose.py", "stage1_intake/delight_albedo.py",
                       "stage3_build/bake_projected_texture.py"):
            r = run(script, "--help")
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")

    def test_build_detail_inventory_slices_zones(self):
        out = self.dir / "di.json"
        zones = self.dir / "zones"
        r = run("stage1_intake/build_detail_inventory.py", self.ref, "--mode", "grid-3x3",
                "--out-dir", zones, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists())
        crops = list(zones.glob("*.png")) if zones.exists() else []
        self.assertGreaterEqual(len(crops), 1)

    def test_delight_reference_writes_png(self):
        out = self.dir / "albedo.png"
        r = run("stage1_intake/delight_albedo.py", self.ref, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists() and out.stat().st_size > 0)

    # ---- v1.2 character generator ----

    def test_character_flag_builds_humanoid_tree(self):
        run("stage2_spec/new_sculpt_spec.py", "Person", "--character", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        ids = {c["id"] for c in spec["componentTree"]}
        for part in ("root", "head", "torso", "neck", "hair", "glasses-frame-l", "arm-l"):
            self.assertIn(part, ids)
        # all parts flattened to root (no cascading non-uniform parent scale)
        for c in spec["componentTree"]:
            if c["id"] != "root":
                self.assertEqual(c["parent"], "root")
        # distinct per-part colors (skin vs hair vs shirt), not a single fallback
        colors = {m["id"]: m.get("color") for m in spec["materials"] if m["id"] in ("skin", "hair", "shirt")}
        self.assertEqual(len({colors["skin"], colors["hair"], colors["shirt"]}), 3)
        # palette has >= 2 entries so the generator does not fall back to beige
        for m in spec["materials"]:
            if m["id"] in ("skin", "hair", "shirt"):
                self.assertGreaterEqual(len(m.get("colorVariation", {}).get("palette", [])), 2)

    def test_character_autodetect_from_domain(self):
        run("stage2_spec/new_pre_spec_assessment.py", "Person", "--complexity", "complex", "--out", self.assessment)
        a = json.loads(self.assessment.read_text())
        a["preSpecAssessment"]["objectClass"]["primaryDomain"] = "character"
        self.assessment.write_text(json.dumps(a))
        run("stage2_spec/new_sculpt_spec.py", "Person", "--assessment", self.assessment, "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        self.assertIn("head", {c["id"] for c in spec["componentTree"]})

    def test_character_factory_generates(self):
        run("stage2_spec/new_sculpt_spec.py", "Person", "--character", "--out", self.spec)
        out = self.dir / "createCharacterModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("createPersonModel", ts)
        self.assertIn('meshes["head"]', ts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
