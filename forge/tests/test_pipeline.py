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
    command = [sys.executable, str(SCRIPTS / script), *map(str, args)]
    # These tests exercise legacy codegen details using intentionally shallow fixtures.
    # Production CLI calls must not use this explicit non-production escape hatch.
    if script == "stage3_build/generate_threejs_factory.py" and "--pass-id" not in args:
        command.append("--allow-nonstrict")
    return subprocess.run(
        command,
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
        self.assertEqual(spec["schemaVersion"], "2.1")
        self.assertEqual(spec["targetName"], "Oak")

    def test_assessment_embeds_local_spec_search_results(self):
        r = run(
            "stage2_spec/new_pre_spec_assessment.py",
            "Karambit Fade",
            "--out",
            self.assessment,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        assessment = json.loads(self.assessment.read_text())
        search = assessment["localSpecSearch"]
        # The cs2 collection ships with the CS2 plugin; a base checkout searches the generic one.
        self.assertEqual(search["collection"], "core_3d")
        self.assertEqual(search["query"], "Karambit Fade")
        self.assertGreater(len(search["matches"]), 0)
        self.assertTrue(search["matches"][0]["source_refs"])
        self.assertTrue(search["matches"][0]["evidence_refs"])

        r = run(
            "stage2_spec/new_sculpt_spec.py",
            "Karambit Fade",
            "--assessment",
            self.assessment,
            "--out",
            self.spec,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        spec = json.loads(self.spec.read_text())
        self.assertEqual(spec["localSpecSearch"]["collection"], "core_3d")
        self.assertTrue(spec["localSpecSearch"]["matches"])

    def test_generic_assessment_uses_generic_spec_collection(self):
        r = run(
            "stage2_spec/new_pre_spec_assessment.py",
            "wooden chair",
            "--out",
            self.assessment,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        assessment = json.loads(self.assessment.read_text())
        self.assertEqual(assessment["localSpecSearch"]["collection"], "core_3d")

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

    def test_topology_rejects_missing_classification(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        del spec["componentTree"][0]["topologyClass"]
        del spec["componentTree"][0]["topologyRationale"]
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("missing or invalid topologyClass", strict.stdout + strict.stderr)

    def test_topology_rejects_restated_rationale(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["topologyClass"] = "assembled-solid"
        spec["componentTree"][0]["topologyRationale"] = "Assembled Solid"
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("restates the enum value", strict.stdout + strict.stderr)

    def test_topology_rejects_disallowed_continuous_sculpt_pairing(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["primitive"] = "box"
        spec["componentTree"][0]["topologyClass"] = "continuous-sculpt"
        spec["componentTree"][0]["topologyRationale"] = "Smooth organic bulge with no seams."
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("disallowed primitive", strict.stdout + strict.stderr)

    def test_topology_rejects_disallowed_fiber_strand_pairing(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["primitive"] = "plane-card"
        spec["componentTree"][0]["topologyClass"] = "fiber-strand"
        spec["componentTree"][0]["topologyRationale"] = "Thin repeated strand form."
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("disallowed primitive", strict.stdout + strict.stderr)

    def test_topology_accepts_all_six_classes_with_allowed_primitives(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        cases = [
            ("continuous-sculpt", "lathe"),
            ("assembled-solid", "box"),
            ("conforming-shell", "plane-card"),
            ("surface-relief", "box"),
            ("fiber-strand", "tube"),
            ("material-only", "plane-card"),
        ]
        for topology_class, primitive in cases:
            with self.subTest(topology_class=topology_class):
                spec["componentTree"][0]["primitive"] = primitive
                spec["componentTree"][0]["topologyClass"] = topology_class
                spec["componentTree"][0]["topologyRationale"] = (
                    f"Test fixture citing observed evidence for {topology_class}."
                )
                self.spec.write_text(json.dumps(spec))
                strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
                self.assertNotIn(
                    "topologyClass", strict.stdout + strict.stderr,
                    f"unexpected topology failure for {topology_class}/{primitive}: {strict.stdout}{strict.stderr}",
                )
                self.assertNotIn(
                    "disallowed primitive", strict.stdout + strict.stderr,
                    f"unexpected disallowed-pairing failure for {topology_class}/{primitive}",
                )

    def test_color_material_recipe_accepts_full_opacity_alpha_format(self):
        # Regression: lab_to_rgba() in extract_part_color_recipe.py renders full opacity
        # as "1.0" (round(1.0, 3) -> the float 1.0, not the bare int 1). RGBA_PATTERN must
        # accept that exact format, or every real extracted recipe fails its own validator.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["colorMaterialRecipe"] = {
            "dominantAlbedo": "rgba(21, 87, 78, 1.0)",
            "secondaryAlbedo": "rgba(34, 67, 67, 1.0)",
            "materialClass": "glass",
            "materialClassConfidence": 0.6,
        }
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotIn(
            "must be an 'rgba(r, g, b, a)' string", strict.stdout + strict.stderr,
            f"1.0-alpha rgba string was wrongly rejected: {strict.stdout}{strict.stderr}",
        )

    def test_diagnose_render_tier1_help_and_identical_images(self):
        r = run("stage4_review/diagnose_render.py", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Identical reference/render images should trivially pass every Tier-1 check.
        r2 = run("stage4_review/diagnose_render.py", "--reference", self.ref, "--render", self.ref, "--json")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        result = json.loads(r2.stdout)
        self.assertTrue(result["passed"])
        self.assertEqual(result["checks"]["silhouetteIoU"], 1.0)

    def test_orchestrator_refuses_current_pass_without_passing_tier1(self):
        # Plan 1.3 Workstream D: the current (unlocked) VISUAL pass must be blocked
        # until a passing tier1Result is recorded for it.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        blocked = run("stage3_build/orchestrate_passes.py", "check", self.spec, "--pass-id", "blockout")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("Tier 1 diagnostics have not passed", blocked.stdout + blocked.stderr)

        # Recording a PASSING tier1 result (identical ref/render) unblocks it.
        run("stage4_review/diagnose_render.py", "--reference", self.ref, "--render", self.ref,
            "--pass-id", "blockout", "--spec", self.spec, "--map-stripped-render", self.render,
            "--in-place")
        unblocked = run("stage3_build/orchestrate_passes.py", "check", self.spec, "--pass-id", "blockout")
        self.assertEqual(unblocked.returncode, 0, unblocked.stderr)

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

    def test_generate_factory_blocks_strict_quality_before_writing_output(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        out = self.dir / "blocked-production-factory.ts"
        report = self.dir / "blocked-report.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "stage3_build/generate_threejs_factory.py"),
                str(self.spec),
                "--out",
                str(out),
                "--pass-id",
                "blockout",
                "--blocked-report",
                str(report),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED", result.stderr)
        self.assertIn("strict-quality", result.stderr)
        self.assertFalse(out.exists())
        payload = json.loads(report.read_text())
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["gate"], "strict-quality")

    def test_generate_factory_default_path_is_byte_stable(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        first = self.dir / "default-a.ts"
        second = self.dir / "default-b.ts"
        first_result = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", first)
        second_result = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", second)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_generate_factory_emits_f3_f4_material_and_environment(self):
        # Plan 1.3 F.3/F.4: previously-missing MeshPhysicalMaterial properties and the
        # environment map must both appear in the generated code (codegen-output test,
        # not a rendering test — fully automatable per the plan's acceptance criteria).
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        out = self.dir / "createObjectModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        for prop in ("sheen:", "sheenColor:", "iridescence:", "iridescenceIOR:",
                     "ior:", "attenuationDistance:", "anisotropy:", "anisotropyRotation:",
                     "specularIntensity:", "specularColor:", "emissive:", "emissiveIntensity:"):
            self.assertIn(prop, ts, f"missing material property in generated code: {prop}")
        self.assertIn("import { RoomEnvironment }", ts)
        self.assertIn("PMREMGenerator", ts)
        self.assertIn("Environment(renderer: THREE.WebGLRenderer)", ts)

    def test_generate_factory_emits_ws5_pbr_constraints_and_dense_maps(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        out = self.dir / "createObjectModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        for marker in ("clampAlbedoChannel", "clampPbrF0", "clampPbrIor", "clampPbrMetalness",
                        "binaryMetalness", "material.displacementMap = textures.height",
                        "material.normalMap = textures.normal"):
            self.assertIn(marker, ts, f"missing WS5 generator rule: {marker}")
        self.assertIn("clampAlbedoChannel(Number(match[1]))", ts)
        self.assertIn("iridescenceIOR: clampPbrIor", ts)

    def test_dense_component_enables_height_maps_when_shared_material_is_plain(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        component = spec["componentTree"][0]
        component["geometryDensity"] = "dense"
        spec["materials"][0].pop("geometryDensity", None)
        spec["materials"][0].pop("denseMesh", None)
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createOakModel.ts"
        result = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("}, options, true)", out.read_text())

    def test_generate_factory_emits_auto_framing(self):
        # Plan 1.3 §3.2: auto-framing by bounding box is a prerequisite for the Divine
        # Eye — an object framed unlike the reference makes every silhouette comparison
        # meaningless. The generated code must expose a frame<Type>Camera helper that
        # positions the camera from the object's Box3 and updates the projection matrix.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        out = self.dir / "createObjectModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("Camera(", ts)
        self.assertIn("new THREE.Box3().setFromObject", ts)
        self.assertIn("camera.updateProjectionMatrix()", ts)

    def test_generate_factory_emits_presentation_composer_only(self):
        # Plan 1.3 §3.2c / R-POSTFX: DOF+bloom live in a SEPARATE presentation composer
        # (opt-in via options), never wired into the model factory itself — the Eye's
        # evaluation render must stay post-fx-free. Codegen-output test.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        out = self.dir / "createObjectModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("PresentationComposer", ts)
        self.assertIn("EffectComposer", ts)
        self.assertIn("BokehPass", ts)
        self.assertIn("UnrealBloomPass", ts)
        self.assertIn("R-POSTFX", ts)
        # the composer must be gated on options.dof / options.bloom (opt-in, not forced)
        self.assertIn("if (options.dof)", ts)
        self.assertIn("if (options.bloom)", ts)

    def test_generate_factory_builds_real_extrude_lathe_tube_geometry(self):
        # Plan 1.3 F.5: the original bug — primitive: "extrude" (e.g. a knife blade
        # tapering to a sharp point) used to validate fine but silently render as a
        # generic box. Confirms all three previously-unimplemented primitives now
        # produce real geometry-builder calls instead of raising or falling back.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["primitive"] = "extrude"
        spec["componentTree"][0]["topologyClass"] = "continuous-sculpt"
        spec["componentTree"][0]["topologyRationale"] = "Blade tapering to a single sharp point."
        spec["componentTree"][0]["geometryDescriptor"]["profile2D"] = {
            "points": [[-0.05, 0.0], [0.05, 0.0], [0.0, 0.6]],  # converges to a real point
            "depth": 0.02,
        }
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createOakModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("buildExtrudeGeometry", ts)
        self.assertIn("bevelEnabled: false", ts)
        self.assertIn("shape.lineTo", ts)
        self.assertNotIn("BoxGeometry(1, 1, 1, 8, 8, 8)", ts)  # the old silent fallback

        for primitive, builder in (("lathe", "buildLatheGeometry"), ("tube", "buildTubeGeometry")):
            with self.subTest(primitive=primitive):
                spec["componentTree"][0]["primitive"] = primitive
                self.spec.write_text(json.dumps(spec))
                r2 = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out, "--force")
                self.assertEqual(r2.returncode, 0, r2.stderr)
                self.assertIn(builder, out.read_text())

    def test_generate_factory_omits_unused_geometry_helpers(self):
        # Regression: a real showcase integration build failed TypeScript's
        # noUnusedLocals because buildLatheGeometry/buildTubeGeometry were emitted
        # unconditionally even when no component in the pass used lathe/tube. Only
        # the primitives actually present should get a helper function.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["primitive"] = "extrude"
        spec["componentTree"][0]["topologyClass"] = "continuous-sculpt"
        spec["componentTree"][0]["topologyRationale"] = "Blade tapering to a single sharp point."
        spec["componentTree"][0]["geometryDescriptor"]["profile2D"] = {
            "points": [[-0.05, 0.0], [0.05, 0.0], [0.0, 0.6]],
            "depth": 0.02,
        }
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createOakModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("buildExtrudeGeometry", ts)
        self.assertNotIn("function buildLatheGeometry", ts)
        self.assertNotIn("function buildTubeGeometry", ts)

    def test_instanced_cluster_component_now_implemented(self):
        # instanced-cluster used to fall through geometry_for() and fail loudly; it now
        # resolves to its base geometry (default box) so a component may declare it without
        # crashing. The instancing itself is applied by repetition systems (test below).
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["primitive"] = "instanced-cluster"
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createOakModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BoxGeometry", out.read_text())

    def test_repetition_system_emits_instanced_mesh(self):
        # Repeated parts (teeth/fasteners/spokes) must render as ONE THREE.InstancedMesh
        # (single draw call), not a per-instance Mesh clone loop (real-time perf principle).
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        first_mat = (spec.get("materials") or [{}])[0].get("id", "base")
        spec["repetitionSystems"] = [{
            "id": "teeth", "level": "macro", "parent": "root", "count": 8,
            "primitive": "box", "material": first_mat,
            "instanceScale": [0.05, 0.05, 0.05],
            "placement": {"mode": "radial", "axis": [0, 0, 1], "radius": 0.5, "startAngleDeg": 0},
        }]
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createOakModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("new THREE.InstancedMesh(geo, mat, 8)", ts)
        self.assertIn("setMatrixAt", ts)
        self.assertNotIn("new THREE.Mesh(geo, mat)", ts)  # no leftover clone loop

    def test_geometry_for_raises_for_unimplemented_primitive(self):
        # The GeometryNotImplementedError guard still protects any FUTURE primitive added to
        # VALID_PRIMITIVES without a geometry_for() branch (never a silent box fallback).
        import importlib.util
        gen_path = SCRIPTS / "stage3_build" / "generate_threejs_factory.py"
        sys.path.insert(0, str(SCRIPTS / "stage3_build"))
        mod_spec = importlib.util.spec_from_file_location("gen_factory_test", gen_path)
        gen = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(gen)
        with self.assertRaises(gen.GeometryNotImplementedError):
            gen.geometry_for("some-future-unimplemented-primitive")

    def test_generate_factory_builds_curve_sweep(self):
        # F.6: curve-sweep sweeps a thin cross-section along a 3D spine so a curved form
        # reads correctly from every angle (the karambit-blade fix). Must emit a real
        # extrudePath + CatmullRomCurve3, only when a component uses curve-sweep.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["componentTree"][0]["primitive"] = "curve-sweep"
        spec["componentTree"][0]["topologyClass"] = "continuous-sculpt"
        spec["componentTree"][0]["topologyRationale"] = "Hooked blade curving through space."
        spec["componentTree"][0]["geometryDescriptor"]["curveSweep"] = {
            "spine": [[-0.5, -0.4, 0.0], [0.0, 0.1, 0.0], [0.5, -0.2, 0.0]],
            "crossSection": {"points": [[-0.04, -0.02], [0.04, -0.02], [0.04, 0.02], [-0.04, 0.02]]},
            "closed": False,
        }
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createOakModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("buildCurveSweepGeometry", ts)
        self.assertIn("extrudePath", ts)
        self.assertIn("CatmullRomCurve3", ts)
        self.assertIn("bevelEnabled: false", ts)
        # conditional emission: a box-only spec must NOT carry the curve-sweep helper
        spec["componentTree"][0]["primitive"] = "box"
        self.spec.write_text(json.dumps(spec))
        r2 = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out, "--force")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertNotIn("function buildCurveSweepGeometry", out.read_text())

    def test_ground_blade_uv_uses_actual_y_bounds(self):
        # ground-blade UVs must map v across the blade's ACTUAL Y bounds, not a hardcoded
        # ±0.12. An off-origin blade (y~0.4) with the old formula clamped v→1 so every face sampled
        # the bright spine-rim row → white/washed tip facets. Corrected UV = (y - yMin) / yH.
        run("stage2_spec/new_sculpt_spec.py", "Blade", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        c = spec["componentTree"][0]
        c["primitive"] = "ground-blade"
        c["topologyClass"] = "continuous-sculpt"
        c["topologyRationale"] = "Blade lofted along beveled stations to a sharp point."
        c["geometryDescriptor"]["bladeSpec"] = {
            "stations": [[0, 0.55, 0.30], [0.5, 0.56, 0.31], [1.0, 0.50, 0.40]],
            "thickness": 0.05,
        }
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createBladeModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("yMin = Math.min(yMin, s[2])", ts)   # bounds from actual stations
        self.assertIn("(pos[t + 1] - yMin) / yH", ts)      # v spans real height
        self.assertNotIn("+ 0.12) / 0.24", ts)             # old hardcoded formula gone

    def test_extrude_supports_oval_hole_via_shape_holes(self):
        # a cutout (wire-cutter oval hole) is done via THREE.Shape.holes — dep-free, no
        # three-bvh-csg. The generator must emit hole-handling + oval-loop support on extrude.
        run("stage2_spec/new_sculpt_spec.py", "Blade", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        c = spec["componentTree"][0]
        c["primitive"] = "extrude"
        c["topologyClass"] = "continuous-sculpt"
        c["topologyRationale"] = "Flat blade plate with an oval wire-cutter cutout."
        c["geometryDescriptor"]["profile2D"] = {
            "points": [[-0.05, 0.0], [0.05, 0.0], [0.0, 0.6]],
            "depth": 0.02,
            "ovalHoles": [{"cx": 0.0, "cy": 0.3, "rx": 0.02, "ry": 0.035}],
        }
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createBladeModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("shape.holes.push(path)", ts)   # cutout via Shape.holes
        self.assertIn("ovalLoop", ts)                  # oval-hole authoring helper
        self.assertNotIn("three-bvh-csg", ts)          # no CSG dependency

    def test_flatness_pre_check_flags_thin_continuous_sculpt_extrude(self):
        # G.1: a continuous-sculpt form (asserted to be a volumetric 3D shape) built as a
        # THIN straight extrude (flat slab) must be flagged before render; the same form
        # with real thickness must not.
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        c = spec["componentTree"][0]
        c["primitive"] = "extrude"
        c["topologyClass"] = "continuous-sculpt"
        c["topologyRationale"] = "Hooked blade silhouette, single continuous form."
        # thin slab (depth ≪ diagonal) under a continuous-sculpt claim → HIGH flatness risk
        c["geometryDescriptor"]["profile2D"] = {
            "points": [[0.0, 0.0], [0.5, 0.6], [1.0, 0.0], [0.5, -0.05]],
            "depth": 0.02,
        }
        self.spec.write_text(json.dumps(spec))
        strict = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("flatness risk", strict.stdout + strict.stderr)
        # same profile with real depth (not a thin slab) must NOT trip the flatness gate
        c["geometryDescriptor"]["profile2D"]["depth"] = 0.5
        self.spec.write_text(json.dumps(spec))
        strict2 = run("stage2_spec/validate_sculpt_spec.py", self.spec, "--strict-quality")
        self.assertNotIn("flatness risk", strict2.stdout + strict2.stderr)

    def test_generate_factory_emits_color_gradient_codegen(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        spec["materials"][0]["colorGradient"] = {
            "type": "linear",
            "axis": [1.0, 0.0],
            "stops": [
                {"offset": 0.0, "color": "rgba(60, 45, 30, 1.0)"},
                {"offset": 1.0, "color": "rgba(200, 155, 120, 1.0)"},
            ],
        }
        self.spec.write_text(json.dumps(spec))
        out = self.dir / "createObjectModel.ts"
        r = run("stage3_build/generate_threejs_factory.py", self.spec, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        ts = out.read_text()
        self.assertIn("sampleColorGradient", ts)
        self.assertIn("colorGradient", ts)

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
                "--map-stripped-render", self.render,
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
                       "stage3_build/bake_projected_texture.py", "stage1_intake/extract_part_color_recipe.py"):
            r = run(script, "--help")
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")

    def test_extract_part_color_recipe_cache_hit_and_invalidation(self):
        # Plan 1.3 Workstream E: same crop + unchanged script -> cache hit on 2nd run.
        r1 = run("stage1_intake/extract_part_color_recipe.py", self.ref,
                 "--component-id", "root")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertFalse(json.loads(r1.stdout)["cacheHit"])

        r2 = run("stage1_intake/extract_part_color_recipe.py", self.ref,
                  "--component-id", "root")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertTrue(json.loads(r2.stdout)["cacheHit"])

        cache_file = self.ref.parent / ".cache" / "color_recipe_cache.json"
        self.assertTrue(cache_file.exists())

        # --no-cache bypasses the cache entirely, even with an existing hit available.
        r3 = run("stage1_intake/extract_part_color_recipe.py", self.ref,
                  "--component-id", "root", "--no-cache")
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertFalse(json.loads(r3.stdout)["cacheHit"])

    def test_extract_part_color_recipe_patches_spec(self):
        run("stage2_spec/new_sculpt_spec.py", "Oak", "--out", self.spec)
        r = run("stage1_intake/extract_part_color_recipe.py", self.ref,
                "--component-id", "root", "--spec", self.spec, "--in-place",
                "--allow-low-confidence")
        self.assertEqual(r.returncode, 0, r.stderr)
        recipe = json.loads(r.stdout)
        self.assertIn("dominantAlbedo", recipe)
        self.assertTrue(recipe["dominantAlbedo"].startswith("rgba("))
        self.assertIn("materialClass", recipe)
        spec = json.loads(self.spec.read_text())
        self.assertIn("colorMaterialRecipe", spec["componentTree"][0])
        self.assertEqual(spec["componentTree"][0]["colorMaterialRecipe"]["dominantAlbedo"], recipe["dominantAlbedo"])

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
        # Plan 1.5: the default character template is a general humanoid, not a hardcoded bust
        # of one reference person -- it must include a lower body and full arms, and must NOT
        # include that person's specific traits (glasses/headphones/chest decal) unless the
        # caller opts in with --accessories. See new_sculpt_spec.py make_character_component_tree.
        run("stage2_spec/new_sculpt_spec.py", "Person", "--character", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        ids = {c["id"] for c in spec["componentTree"]}
        for part in (
            "root", "head", "abdomen", "chest", "neck", "hair",  # spine is two segments (US-001)
            "pelvis", "thigh-l", "shin-l", "foot-l",  # lower body
            "upper-arm-l", "forearm-l", "hand-l",  # full arm, not a single "arm-l" capsule
            "eye-l", "eye-cavity-l",  # eyes as recessed cavities, not brows alone
        ):
            self.assertIn(part, ids)
        for accessory in ("glasses-frame-l", "hp-band", "shirt-decal"):
            self.assertNotIn(accessory, ids)
        # Plan 1.5 WS-E: now that a non-uniformly-scaled parent can no longer distort a
        # nested child (generate_threejs_factory.py bakes dimensions into geometry, not the
        # pivot Group's scale), the template authors a REAL parent chain instead of flattening
        # every part onto "root". Anatomically sensible parents, not a flat list.
        by_id = {c["id"]: c for c in spec["componentTree"]}
        expected_parents = {
            # US-001 split the single torso into abdomen + chest so the waist can flex, and
            # parented the spine to the pelvis. The pelvis is now the only root-parented body
            # part, which is also what made derive_character_rig's hand-picked bone root and its
            # torso joint exception unnecessary.
            "pelvis": "root", "abdomen": "pelvis", "chest": "abdomen",
            "neck": "chest", "head": "neck",
            # US-002 inserted a clavicle between chest and upper arm so the shoulder can shrug
            # and the arm's rotation origin sits on the joint instead of inside the ribcage.
            "clavicle-l": "chest", "upper-arm-l": "clavicle-l",
            "forearm-l": "upper-arm-l", "hand-l": "forearm-l",
            "thigh-l": "pelvis", "shin-l": "thigh-l", "foot-l": "shin-l",
            "hair": "head", "eye-l": "head", "eye-cavity-l": "head",
        }
        for child_id, expected_parent in expected_parents.items():
            self.assertEqual(by_id[child_id]["parent"], expected_parent,
                              f"{child_id} should be parented on {expected_parent!r}")
        # not every part is nested this deep -- confirm the tree isn't secretly still flat
        non_root_parents = {c["parent"] for c in spec["componentTree"] if c["id"] != "root"}
        self.assertTrue(non_root_parents - {"root"}, "componentTree is still flattened onto root")
        # distinct per-part colors (skin vs hair vs shirt), not a single fallback
        colors = {m["id"]: m.get("color") for m in spec["materials"] if m["id"] in ("skin", "hair", "shirt")}
        self.assertEqual(len({colors["skin"], colors["hair"], colors["shirt"]}), 3)
        # palette has >= 2 entries so the generator does not fall back to beige
        for m in spec["materials"]:
            if m["id"] in ("skin", "hair", "shirt"):
                self.assertGreaterEqual(len(m.get("colorVariation", {}).get("palette", [])), 2)

    def test_character_accessories_flag_restores_bust_traits(self):
        run("stage2_spec/new_sculpt_spec.py", "Person", "--character", "--accessories",
            "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        ids = {c["id"] for c in spec["componentTree"]}
        for accessory in ("glasses-frame-l", "hp-band", "shirt-decal"):
            self.assertIn(accessory, ids)

    def test_digits_are_baseline_not_a_flag(self):
        """US-003 replaced the `--fingers` flag with real digits in the default template.

        The flag added ONE box called "fingers-l" plus one called "thumb-l" — a mitten, opt-in.
        A humanoid that cannot curl a finger is not finished, so five digits of three phalanges
        each are now baseline and the flag is gone. This asserts both halves: the digits exist
        without any flag, and the old mitten ids do not come back.
        """
        run("stage2_spec/new_sculpt_spec.py", "Person", "--character", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        ids = {c["id"] for c in spec["componentTree"]}
        for side in ("l", "r"):
            for digit in ("thumb", "index", "middle", "ring", "little"):
                for phalanx in (1, 2, 3):
                    self.assertIn(f"{digit}-{side}-{phalanx}", ids)
        self.assertNotIn("fingers-l", ids)
        self.assertNotIn("thumb-l", ids)
        by_id = {c["id"]: c for c in spec["componentTree"]}
        self.assertEqual(by_id["middle-l-1"]["parent"], "hand-l")
        self.assertEqual(by_id["middle-l-2"]["parent"], "middle-l-1")
        self.assertEqual(by_id["middle-l-3"]["parent"], "middle-l-2")

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

    def test_character_template_survives_an_augmentation_file(self):
        """Both contributions apply; neither silences the other.

        These two blocks used to be joined by an `elif`: a character run handed an augmentation
        file skipped `apply_character_template` entirely, so the emitted spec lost `rig` and still
        exited 0 -- the silent data loss PR #106's review demonstrated by diffing the same
        assessment run with and without an augmentation. This pins the fix from both sides.
        """
        aug = self.dir / "spec-augmentation.json"
        aug.write_text(
            json.dumps(
                {
                    "kind": "spec-augmentation-v1",
                    "provenance": {"provider": "fixture", "version": "0.0.1"},
                    "specSections": {"fixtureSection": {"marker": True}},
                }
            ),
            encoding="utf-8",
        )
        run("stage2_spec/new_sculpt_spec.py", "Person", "--character", "--augmentation", aug, "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        self.assertIn("rig", spec, "the character template was skipped: `rig` is missing")
        self.assertIn("fixtureSection", spec, "the augmentation merge was skipped")
        self.assertEqual(spec["specAugmentation"]["provider"], "fixture")

    def test_a_domain_run_with_a_missing_augmentation_fails_loud(self):
        """02-how-it-works draws the missing-provider case as FAIL LOUD; the code must match.

        Before this test, a resolved --domain whose spec-augmentation.json was absent (emit step
        failed, or an actor:agent step skipped) silently wrote the generic skeleton and exited 0 --
        a whole run could finish with no domain contribution and nobody would know.
        """
        r = run(
            "stage2_spec/new_sculpt_spec.py", "Knife",
            "--domain", "fixturedom",
            "--augmentation", self.dir / "does-not-exist.json",
            "--out", self.spec,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does-not-exist.json", r.stderr)
        self.assertIn("fixturedom", r.stderr)
        self.assertFalse(self.spec.exists(), "no generic skeleton may be written for a failed domain run")

    def test_a_generic_run_still_skips_a_missing_augmentation(self):
        r = run(
            "stage2_spec/new_sculpt_spec.py", "Crate",
            "--augmentation", self.dir / "does-not-exist.json",
            "--out", self.spec,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.spec.exists())

    def test_cs2_track_skipped_for_objects(self):
        run("stage2_spec/new_sculpt_spec.py", "Crate", "--out", self.spec)
        spec = json.loads(self.spec.read_text())
        self.assertNotIn("skin-finish", {m["id"] for m in spec["materials"]})

    def test_a_domain_looking_target_name_is_not_auto_detected(self):
        # Declared breaking change. The base used to infer the CS2 domain from the target's name
        # (a " | " separator, or any of 17 keywords including bare "fade" and "karambit"). That is
        # name-similarity resolution, which PLUGIN_CONTRACT.md section 13 forbids, so it is gone: a
        # domain now has to be declared, not guessed from what the thing is called.
        r = run("stage2_spec/new_pre_spec_assessment.py", "Karambit | Doppler", "--out", self.assessment)
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(self.assessment.read_text())
        self.assertNotIn("domain", payload["preSpecAssessment"]["objectClass"])
        self.assertEqual(payload["localSpecSearch"]["collection"], "core_3d")

    def test_a_declared_domain_is_still_honoured(self):
        r = run("stage2_spec/new_pre_spec_assessment.py", "Karambit Doppler", "--cs2", "--out", self.assessment)
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(self.assessment.read_text())
        self.assertEqual(payload["preSpecAssessment"]["objectClass"]["domain"], "cs2")

    def test_cs2_textures_gitignored_and_never_tracked(self):
        gitignore = (SKILL / ".gitignore").read_text()
        self.assertIn("cs2_textures/", gitignore)
        tracked = subprocess.run(
            ["git", "-C", str(SKILL), "ls-files", "--", "cs2_textures", "**/cs2_textures"],
            capture_output=True, text=True,
        )
        self.assertEqual(tracked.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
