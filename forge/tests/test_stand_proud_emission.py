#!/usr/bin/env python3
"""The generator emits the clearance march, so `standProud` is enforced and not merely declared.

This is `hug` from the hand-written humanoid demo, generalised out of that one file and into the
skill. In the demo the garment gets a measured per-vertex clearance and works; the hair got a prose
invariant and broke. Widening the hair masses by hand took closure from 42.2% to 40.9%, worse on all
six views, with dark coverage DOWN.

Run: python3 forge/tests/test_stand_proud_emission.py
"""
import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage3_build"))

from generate_threejs_factory import generate, stand_proud_ring_stack  # noqa: E402
from showcase_test_support import showcase_root  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stand_proud_hair_on_head.json"


def spec(hair_extra: dict | None = None, head_extra: dict | None = None) -> dict:
    head = {"id": "head", "name": "Head", "primitive": "ellipsoid", "material": "skin",
            "dimensions": {"width": 0.4, "height": 0.5, "depth": 0.42}}
    head.update(head_extra or {})
    hair = {"id": "hair-crown", "name": "Hair crown", "primitive": "ellipsoid", "material": "hair",
            "parent": "head", "role": "hair",
            "dimensions": {"width": 0.41, "height": 0.3, "depth": 0.43}}
    hair.update(hair_extra or {})
    return {
        "targetName": "ProudTest",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {},
        "silhouette": {},
        "proceduralStrategy": [],
        "materials": [{"id": "skin"}, {"id": "hair"}],
        "componentTree": [head, hair],
    }


PROUD = {"againstComponentId": "head", "clearance": 0.012, "maxPush": 0.04}


class RingStackDerivation(unittest.TestCase):
    def test_an_authored_ring_stack_is_used_directly(self) -> None:
        rings = stand_proud_ring_stack({
            "primitive": "lathe",
            "geometryDescriptor": {"ringStack": {"rings": [[1.0, 0.4, 0.5], [0.0, 0.3, 0.35]]}},
        })
        self.assertEqual(rings[0][0], 0.0, "rings must come back sorted bottom to top")
        self.assertEqual(rings[-1][0], 1.0)
        self.assertEqual(len(rings[0]), 4, "a missing z offset must be filled, not dropped")

    def test_parallel_z_offsets_are_merged(self) -> None:
        rings = stand_proud_ring_stack({
            "primitive": "lathe",
            "geometryDescriptor": {
                "ringStack": {"rings": [[0.0, 0.4, 0.5], [1.0, 0.3, 0.35]], "zOffsets": [0.06, -0.03]}
            },
        })
        self.assertEqual(rings[0][3], 0.06)
        self.assertEqual(rings[1][3], -0.03)

    def test_an_ellipsoid_is_synthesised_at_unit_scale(self) -> None:
        """This function returns the UNIT form, because `geometry_for` authors an ellipsoid at
        radius 0.5 and knows nothing about the component's dimensions.

        Applying the dimensions is the EMISSION layer's job, and getting that split wrong was a real
        defect: the unit stack went out unscaled, claiming a skull radius of 0.5 where the geometry
        sat at 0.2. `TheStackDescribesTheRealSurface` below covers the scaled result; this covers
        only the unit form this function is responsible for.
        """
        rings = stand_proud_ring_stack({"primitive": "ellipsoid"})
        self.assertAlmostEqual(rings[0][0], -0.5, places=6)
        self.assertAlmostEqual(rings[-1][0], 0.5, places=6)
        widest = max(rings, key=lambda r: r[1])
        self.assertAlmostEqual(widest[1], 0.5, places=3)

    def test_a_shape_with_no_describable_surface_returns_none(self) -> None:
        """Better a visible skip than a clearance that silently never ran."""
        self.assertIsNone(stand_proud_ring_stack({"primitive": "plane-card"}))
        self.assertIsNone(stand_proud_ring_stack({"primitive": "extrude"}))


class HelperEmission(unittest.TestCase):
    def test_nothing_is_emitted_when_no_component_declares_it(self) -> None:
        source = generate(spec(), "blockout")
        self.assertNotIn("applyStandProud", source)
        self.assertNotIn("ringStackDistance", source)

    def test_the_helper_and_the_call_are_both_emitted(self) -> None:
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("function applyStandProud(", source)
        self.assertIn("function ringStackDistance(", source)
        self.assertIn("applyStandProud(", source.split("function applyStandProud(")[-1])

    def test_the_march_travels_along_the_vertex_spoke(self) -> None:
        """Along its OWN radial direction, not the field gradient: that is what keeps a ring a ring
        rather than letting neighbouring vertices settle at different radii."""
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("const spokeLength = Math.hypot(p.x, p.z);", source)
        self.assertIn("p.x += sx * move;", source)
        self.assertIn("p.z += sz * move;", source)

    def test_travel_is_capped_at_maxPush(self) -> None:
        """Required, not a safeguard: an uncapped march walks inner vertices out the far side."""
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("maxPush - travelled", source)
        self.assertIn("if (move <= 0) break;", source)

    def test_normals_are_recomputed_after_moving_vertices(self) -> None:
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        tail = source.split("function applyStandProud(")[-1]
        self.assertIn("position.needsUpdate = true;", tail)
        self.assertIn("geometry.computeVertexNormals();", tail)

    def test_the_march_runs_in_the_target_frame(self) -> None:
        """Clearance is measured against the target's surface, so both world matrices are needed."""
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("target.matrixWorld", source)
        self.assertIn("marcher.matrixWorld", source)
        self.assertIn(".invert()", source)

    def test_the_declared_numbers_reach_the_call(self) -> None:
        source = generate(spec(hair_extra={"standProud": {
            "againstComponentId": "head", "clearance": 0.0175, "maxPush": 0.0625}}), "blockout")
        self.assertIn("0.0175", source)
        self.assertIn("0.0625", source)

    def test_a_vertex_on_the_axis_marches_axially_instead_of_being_skipped(self) -> None:
        """The axis IS the crown, which is where a bald patch shows most. An earlier version
        skipped these vertices, leaving the exact failure the march exists to prevent."""
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("const onAxis = spokeLength < 1e-9;", source)
        self.assertIn("const sy = onAxis ? (p.y >= midHeight ? 1 : -1) : 0;", source)
        self.assertIn("p.y += sy * move;", source)
        self.assertNotIn("if (spokeLength < 1e-9) continue;", source)


class ThePortsAgree(unittest.TestCase):
    """`ringStackDistance` in the emitted TypeScript reimplements `ScalpField.distance` in Python.

    Two implementations of one formula in two languages, and until this class existed nothing held
    them together -- every other test here is a substring check, so a fix applied to one side would
    have left the other silently wrong. That is not hypothetical: the scale defect this file's
    `TheStackDescribesTheRealSurface` covers was exactly a divergence between what Python computed
    and what the browser then measured against.

    The TS body is transliterated here rather than executed. That is a weaker lock than running it,
    but it is a real one: it fails the moment the two formulas stop matching numerically, which is
    the drift being guarded against.
    """

    @staticmethod
    def ts_ring_stack_distance(rings, x, y, z):
        """Line-for-line transliteration of the emitted `ringStackDistance`."""
        y_min, y_max = rings[0][0], rings[-1][0]
        rx, rz, zc = rings[0][1], rings[0][2], rings[0][3]
        if y >= y_max:
            rx, rz, zc = rings[-1][1], rings[-1][2], rings[-1][3]
        elif y > y_min:
            for lo, hi in zip(rings, rings[1:]):
                if lo[0] <= y <= hi[0]:
                    span = hi[0] - lo[0]
                    t = (y - lo[0]) / span if span > 1e-9 else 0
                    rx = lo[1] + (hi[1] - lo[1]) * t
                    rz = lo[2] + (hi[2] - lo[2]) * t
                    zc = lo[3] + (hi[3] - lo[3]) * t
                    break
        dx = x / rx
        dz = (z - zc) / rz
        f = dx * dx + dz * dz - 1
        gx = (2 * x) / (rx * rx)
        gz = (2 * (z - zc)) / (rz * rz)
        grad = math.hypot(gx, gz)
        radial = -min(rx, rz) if grad < 1e-12 else f / grad
        axial = max(y_min - y, y - y_max)
        return math.hypot(max(radial, 0), max(axial, 0)) + min(max(radial, axial), 0)

    def test_the_transliteration_matches_the_emitted_source(self) -> None:
        """Guards the transliteration itself: if the emitted body changes shape, this must be
        revisited rather than quietly continuing to test a stale copy."""
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        body = source.split("function ringStackDistance(")[1].split("\n}")[0]
        for fragment in (
            "const f = dx * dx + dz * dz - 1;",
            "const gx = (2 * x) / (rx * rx);",
            "const radial = grad < 1e-12 ? -Math.min(rx, rz) : f / grad;",
            "const axial = Math.max(yMin - y, y - yMax);",
            "return Math.hypot(Math.max(radial, 0), Math.max(axial, 0)) "
            "+ Math.min(Math.max(radial, axial), 0);",
        ):
            self.assertIn(fragment, body)

    def test_the_two_implementations_agree_across_the_domain(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
        from scalp_field import ScalpField  # noqa: PLC0415

        rings = [[-0.25, 0.12, 0.16, 0.01], [-0.10, 0.19, 0.21, 0.03],
                 [0.00, 0.20, 0.22, 0.02], [0.12, 0.19, 0.21, 0.0],
                 [0.25, 0.15, 0.17, -0.02]]
        field = ScalpField(rings)

        checked = 0
        for xi in range(-6, 7):
            for yi in range(-8, 9):
                for zi in range(-6, 7):
                    x, y, z = xi * 0.05, yi * 0.05, zi * 0.05
                    with self.subTest(point=(x, y, z)):
                        self.assertAlmostEqual(
                            self.ts_ring_stack_distance(rings, x, y, z),
                            field.distance(x, y, z),
                            places=12,
                        )
                    checked += 1
        self.assertGreater(checked, 2000, "the sweep must actually cover the domain")

    def test_they_agree_on_the_signs_too(self) -> None:
        """The magnitude is a first-order estimate; the SIGN is what gates rely on."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
        from scalp_field import ScalpField  # noqa: PLC0415

        rings = [[0.0, 0.2, 0.3, 0.0], [1.0, 0.1, 0.15, 0.05]]
        field = ScalpField(rings)
        for x, y, z in ((0, 0.5, 0), (0.5, 0.5, 0), (0, 2.0, 0), (0, -1.0, 0), (0.05, 0.9, 0.05)):
            with self.subTest(point=(x, y, z)):
                self.assertEqual(
                    self.ts_ring_stack_distance(rings, x, y, z) < 0,
                    field.distance(x, y, z) < 0,
                )


class SaturationIsReported(unittest.TestCase):
    """A vertex can exhaust `maxPush` and still be inside the target.

    That is the cap doing its job, but the clearance the function promised was NOT achieved, and
    saying nothing there hides the very defect the caller asked to be protected from. Measured by
    executing the emitted logic against the shipped fixture: 2 of 8 sampled hair vertices sat 0.059
    inside the skull against a 0.04 cap and could never have reached clear.
    """

    def test_unresolved_vertices_are_counted(self) -> None:
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("let unresolved = 0;", source)
        self.assertIn("unresolved += 1;", source)

    def test_the_count_is_left_on_the_geometry_for_a_gate_to_read(self) -> None:
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("geometry.userData.standProud = {", source)
        self.assertIn("unresolved,", source)

    def test_a_warning_names_the_consequence_and_the_fix(self) -> None:
        source = generate(spec(hair_extra={"standProud": PROUD}), "blockout")
        self.assertIn("console.warn(", source)
        self.assertIn("render as bare patches", source)
        self.assertIn("Raise maxPush", source)


class UnresolvableDeclarations(unittest.TestCase):
    """Two layers, and they catch different things.

    A malformed declaration -- unknown target, non-numeric bounds -- never reaches the generator,
    because stage 2 validation rejects the spec first. Those cases are covered in
    test_stand_proud.py, at the layer that owns them. What survives validation and still cannot be
    marched is a target whose SHAPE exposes no surface to measure against, and only the generator
    knows that, because only the generator knows which primitives it can describe as a ring stack.
    """

    def test_a_malformed_declaration_never_reaches_the_generator(self) -> None:
        for hair_extra, expected in (
            ({"standProud": dict(PROUD, againstComponentId="ghost")}, "references missing component"),
            ({"standProud": {"againstComponentId": "head", "clearance": "thin", "maxPush": 0.04}},
             "clearance must be a positive number"),
            ({"standProud": {"againstComponentId": "head", "clearance": 0.05, "maxPush": 0.01}},
             "below its clearance"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as caught:
                    generate(spec(hair_extra=hair_extra), "blockout")
                self.assertIn(expected, str(caught.exception))

    def test_a_target_whose_shape_has_no_ring_stack_is_reported_in_the_output(self) -> None:
        """Valid to the schema, unmarchable in practice. A visible skip beats a silent no-op."""
        source = generate(
            spec(hair_extra={"standProud": PROUD}, head_extra={"primitive": "plane-card"}),
            "blockout",
        )
        self.assertIn("// SKIPPED", source)
        self.assertIn("exposes no ring stack", source)
        self.assertNotIn("function applyStandProud(", source)


class ForwardReference(unittest.TestCase):
    def test_a_component_may_stand_proud_of_one_declared_after_it(self) -> None:
        """The pass is deferred to after the whole tree is parented, so ordering cannot break it."""
        payload = spec(hair_extra={"standProud": PROUD})
        payload["componentTree"].reverse()
        source = generate(payload, "blockout")
        self.assertIn("function applyStandProud(", source)
        self.assertNotIn("// SKIPPED", source)
        # The call must come after both nodes exist, which is what the meshes/nodes lookup asserts.
        self.assertIn('if (meshes["hair-crown"] && nodes["head"])', source)


class TheStackDescribesTheRealSurface(unittest.TestCase):
    """Where the emitted stack SAYS the surface is, against where the geometry actually puts it.

    Every other test in this file checks emitted strings or that the file typechecks. None of them
    could see this, and it was wrong: `geometry.scale(width, height, depth)` bakes the factors into
    the vertex data and leaves the pivot at scale 1, so nothing in the scene graph carries them. The
    stack was being emitted from the UNIT form, claiming a skull radius of 0.5 where the geometry
    sat at 0.2, and claiming rings at 0.12..0.20 where the scaled lathe sat at 0.048..0.080. The
    march would have pushed hair to roughly two and a half times the skull's real radius.
    """

    def emitted_rings(self, spec_payload: dict, marcher_id: str) -> list[list[float]]:
        """Pull the ring stack literal back out of the generated source."""
        source = generate(spec_payload, "structural-pass")
        marker = f'if (meshes["{marcher_id}"]'
        start = source.index(marker)
        rings_at = source.index('"rings"', start)
        open_bracket = source.index("[", rings_at)
        depth = 0
        for index in range(open_bracket, len(source)):
            if source[index] == "[":
                depth += 1
            elif source[index] == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(source[open_bracket:index + 1])
        raise AssertionError("could not recover the emitted ring stack")

    def test_an_authored_stack_is_scaled_to_the_geometry_it_describes(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        width = head["dimensions"]["width"]
        authored = head["geometryDescriptor"]["ringStack"]["rings"]

        rings = self.emitted_rings(payload, "hair-crown")
        widest_authored = max(r[1] for r in authored)
        widest_emitted = max(r[1] for r in rings)
        self.assertAlmostEqual(widest_emitted, widest_authored * width, places=6)

    def test_an_ellipsoid_target_reports_its_real_half_extent_not_0_5(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        head["primitive"] = "ellipsoid"
        del head["geometryDescriptor"]["ringStack"]

        rings = self.emitted_rings(payload, "hair-crown")
        widest = max(r[1] for r in rings)
        self.assertAlmostEqual(widest, 0.5 * head["dimensions"]["width"], places=3)
        self.assertNotAlmostEqual(widest, 0.5, places=3)

    def test_the_stack_height_is_scaled_too(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        height = head["dimensions"]["height"]
        authored = head["geometryDescriptor"]["ringStack"]["rings"]

        rings = self.emitted_rings(payload, "hair-crown")
        self.assertAlmostEqual(max(r[0] for r in rings), max(r[0] for r in authored) * height, places=6)
        self.assertAlmostEqual(min(r[0] for r in rings), min(r[0] for r in authored) * height, places=6)

    def test_the_z_offset_is_scaled_by_depth_not_by_width(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        depth = head["dimensions"]["depth"]
        authored_offsets = head["geometryDescriptor"]["ringStack"]["zOffsets"]

        rings = self.emitted_rings(payload, "hair-crown")
        self.assertAlmostEqual(rings[0][3], authored_offsets[0] * depth, places=6)

    def test_an_explicit_transform_scale_wins_over_dimensions(self) -> None:
        """`scale_vector` prefers transform.scale, so the stack has to prefer it identically or the
        two descriptions of the same geometry disagree."""
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        head["transform"] = {"scale": [2.0, 3.0, 4.0]}
        authored = head["geometryDescriptor"]["ringStack"]["rings"]

        rings = self.emitted_rings(payload, "hair-crown")
        self.assertAlmostEqual(max(r[1] for r in rings), max(r[1] for r in authored) * 2.0, places=6)
        self.assertAlmostEqual(max(r[0] for r in rings), max(r[0] for r in authored) * 3.0, places=6)

    def test_a_target_with_no_dimensions_is_left_at_unit_scale(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        authored = list(head["geometryDescriptor"]["ringStack"]["rings"])
        del head["dimensions"]

        rings = self.emitted_rings(payload, "hair-crown")
        self.assertAlmostEqual(max(r[1] for r in rings), max(r[1] for r in authored), places=6)


class EmittedSourceTypechecks(unittest.TestCase):
    """The emitted TypeScript is the deliverable, so it has to actually compile.

    Generated through the Python API rather than the CLI: the CLI's strict-quality gate is about
    whether a SPEC is complete enough to build from, which is a different question from whether the
    code path under test emits valid TypeScript, and `--allow-nonstrict` cannot be combined with
    `--pass-id`. The fixture's hair is `meso`, so it needs a pass that admits meso -- under
    `blockout` the hair is correctly filtered out and there would be nothing to typecheck.
    """

    def test_a_factory_using_standProud_typechecks(self) -> None:
        root = showcase_root()
        destination = root / "src" / "__stand_proud_smoke__.ts"
        self.assertFalse(destination.exists(), "a previous run left its smoke source behind")

        source = generate(json.loads(FIXTURE.read_text()), "structural-pass")
        self.assertIn("function applyStandProud(", source)
        self.assertNotIn("// SKIPPED", source)

        try:
            destination.write_text(source, encoding="utf-8")
            result = subprocess.run(
                ["npx", "tsc", "--noEmit"], cwd=root, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            if destination.exists():
                destination.unlink()

    def test_the_fixture_carries_both_a_ring_stack_target_and_two_marchers(self) -> None:
        """Guards the fixture itself: a fixture that quietly stopped exercising the path would let
        the typecheck above pass while proving nothing."""
        spec_payload = json.loads(FIXTURE.read_text())
        marchers = [c for c in spec_payload["componentTree"] if "standProud" in c]
        self.assertEqual(len(marchers), 2)
        head = next(c for c in spec_payload["componentTree"] if c["id"] == "head")
        self.assertIn("ringStack", head["geometryDescriptor"])

    def test_the_fixture_ring_stack_matches_the_geometry_it_claims_to_describe(self) -> None:
        """A ring stack is a SECOND description of a shape the generator builds another way, so the
        two can disagree with nothing to notice.

        This fixture disagreed. Its lathe profile and its ring stack were authored independently and
        the stack was 10-33% wider in z than the revolve it stood for, because a lathe is circular
        before `geometry.scale` and the stack had already baked ellipticity in. Checking only rx --
        the axis that had just been fixed -- did not see it.
        """
        spec_payload = json.loads(FIXTURE.read_text())
        head = next(c for c in spec_payload["componentTree"] if c["id"] == "head")
        width = head["dimensions"]["width"]
        depth = head["dimensions"]["depth"]
        height = head["dimensions"]["height"]
        profile = head["geometryDescriptor"]["latheProfile"]["points"]
        rings = sorted(head["geometryDescriptor"]["ringStack"]["rings"])

        self.assertEqual(len(rings), len(profile), "one ring per profile point, or they cannot align")
        for (radius, y), ring in zip(profile, rings):
            with self.subTest(y=y):
                self.assertAlmostEqual(ring[0] * height, y * height, places=9)
                # A revolve is circular; the ellipse comes from the dimensions, not the stack.
                self.assertAlmostEqual(ring[1], ring[2], places=9)
                self.assertAlmostEqual(ring[1] * width, radius * width, places=9)
                self.assertAlmostEqual(ring[2] * depth, radius * depth, places=9)

    def test_a_lathe_target_with_an_elliptical_stack_is_refused(self) -> None:
        """The rule, not just the fixture: a stack that cannot describe a revolve is reported."""
        payload = json.loads(FIXTURE.read_text())
        head = next(c for c in payload["componentTree"] if c["id"] == "head")
        head["geometryDescriptor"]["ringStack"]["rings"][2][2] += 0.02

        source = generate(payload, "structural-pass")
        self.assertIn("// SKIPPED", source)
        self.assertIn("circular before scaling", source)
        self.assertNotIn("function applyStandProud(", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
