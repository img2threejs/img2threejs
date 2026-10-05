#!/usr/bin/env python3
"""Plan 1.5 schema gates: recessed features cannot be faked flat, and open-shell exists
for zero-thickness two-sided surfaces.

Covers two defect classes that a downstream review pass cannot catch:

1. US-004 — "the eye reads as a patch, not a recessed socket." A silhouette gate cannot
   see an interior concavity, and a dark-pixel ratio on a concave feature measures cavity
   shading, not material. A component authored as a recessed feature (its role/name/id
   contains a signal like "cavity", "canal", "nostril", the compound "eye-socket", or
   "hollow"/"concave" specifically in `role`) must be rejected if it is topologyClass
   "surface-relief" or a flat "plane-card" primitive, and accepted when built as real
   concavity: topologyClass "implicit" with a geometryDescriptor.sdf "subtract" operation.
   NOTE: bare "socket" is deliberately NOT a signal -- ATTACHMENT_ROLES already uses it for
   attachment points (a handle's hilt socket), so this file pins that collision in
   `test_bare_socket_attachment_point_is_not_treated_as_recessed` below.

2. "open-shell" — a zero-thickness two-sided surface (wing membrane, cape, leaf, fin).
   An SDF cannot represent zero thickness, so open-shell must not pair with
   geometryDescriptor.sdf, and it must carry a double-sided material (doubleSided: true)
   or it renders invisible from behind.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "implicit_character_torso_limb.json"


def import_forge_modules():
    module_names = ("generate_threejs_factory", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        from validate_sculpt_spec import (
            ATTACHMENT_ROLES,
            RECESSED_FEATURE_TOKENS,
            VALID_TOPOLOGY_CLASSES,
            component_is_recessed_feature,
            component_recessed_feature_matches,
            validate_spec,
        )
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return (
        ATTACHMENT_ROLES,
        RECESSED_FEATURE_TOKENS,
        VALID_TOPOLOGY_CLASSES,
        component_is_recessed_feature,
        component_recessed_feature_matches,
        validate_spec,
    )


(
    ATTACHMENT_ROLES,
    RECESSED_FEATURE_TOKENS,
    VALID_TOPOLOGY_CLASSES,
    component_is_recessed_feature,
    component_recessed_feature_matches,
    validate_spec,
) = import_forge_modules()


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def base_spec(component: dict, material: dict | None = None) -> dict:
    return {
        "targetName": "Test",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {},
        "silhouette": {},
        "proceduralStrategy": [],
        "materials": [material or {"id": "skin"}],
        "componentTree": [component],
    }


FROZEN_SPEC = Path(__file__).resolve().parent / "fixtures" / "oracle-character" / "spec.json"

class RecessedFeatureGateTest(unittest.TestCase):
    def test_role_name_id_tokens_are_detected(self) -> None:
        self.assertTrue(component_is_recessed_feature({"role": "eye-socket"}))
        self.assertTrue(component_is_recessed_feature({"name": "Left ear canal"}))
        self.assertTrue(component_is_recessed_feature({"id": "mouth-cavity"}))
        self.assertTrue(component_is_recessed_feature({"id": "left-nostril"}))
        self.assertFalse(component_is_recessed_feature({"id": "left-eye", "role": "eye", "name": "Eye"}))
        self.assertFalse(component_is_recessed_feature({"id": "wing-membrane", "role": "wing"}))

    def test_bare_socket_attachment_point_is_not_treated_as_recessed(self) -> None:
        """Pin the collision team-lead flagged: ATTACHMENT_ROLES already uses bare "socket"
        for an attachment POINT (e.g. a hilt socket a handle plugs into), which is not a
        concavity. This must stay false regardless of surrounding words, since "socket" was
        deliberately dropped from RECESSED_FEATURE_TOKENS in favor of the narrower compound
        "eyesocket"."""
        self.assertIn("socket", ATTACHMENT_ROLES)  # the collision this test guards against
        self.assertFalse(component_is_recessed_feature({"role": "socket"}))
        self.assertFalse(component_is_recessed_feature({"id": "hilt-socket", "role": "socket", "name": "Hilt socket"}))

    def test_mutation_attachment_socket_component_is_not_rejected_by_rule_1(self) -> None:
        """End-to-end version of the collision pin: a legitimately-authored attachment
        socket (cylinder primitive, a real attachment block, role="socket") must validate
        clean of Rule 1 -- it needs attachment metadata (a separate, pre-existing gate),
        not implicit/subtract reclassification."""
        spec = base_spec({
            "id": "hilt-socket",
            "name": "Hilt socket",
            "level": "meso",
            "role": "socket",
            "primitive": "cylinder",
            "topologyClass": "assembled-solid",
            "topologyRationale": "Machined cylindrical socket the hilt tang is inserted into.",
            "parent": "handle",
            "material": "skin",
            "attachment": {
                "parentId": "handle",
                "parentSocket": "hilt-socket",
                "localStart": [0.0, 0.0, 0.0],
                "localEnd": [0.0, 0.2, 0.0],
                "contactType": "insert",
                "embedDepth": 0.05,
                "gapTolerance": 0.002,
            },
        })
        spec["componentTree"].append({
            "id": "handle",
            "role": "handle",
            "primitive": "cylinder",
            "topologyClass": "assembled-solid",
            "topologyRationale": "Grip handle the socket is machined into.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

    def test_hollow_and_concave_scoped_to_role_do_not_false_positive_on_name(self) -> None:
        """"hollow tube" and "concave lens" are real, legitimately non-recessed parts.
        hollow/concave are only recessed-feature signals when they appear in `role`, not
        `name`/`id`, precisely so these don't get force-rejected into implicit/subtract."""
        hollow_tube = base_spec({
            "id": "hollow-tube",
            "name": "Hollow tube",
            "role": "pipe",
            "primitive": "tube",
            "topologyClass": "surface-relief",
            "topologyRationale": "Thin-walled tube with baked knurl relief.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(hollow_tube)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

        concave_lens = base_spec({
            "id": "concave-lens",
            "name": "Concave lens",
            "role": "lens",
            "primitive": "plane-card",
            "topologyClass": "surface-relief",
            "topologyRationale": "Flat lens card with a refraction-shader illusion of curvature.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(concave_lens)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

    def _implicit_eye_cavity(self, operations: list[dict]) -> dict:
        return {
            "id": "left-eye-cavity",
            "role": "cavity",
            "primitive": "sphere",
            "topologyClass": "implicit",
            "topologyRationale": "Carved from the head volume.",
            "parent": None,
            "material": "skin",
            "geometryDescriptor": {
                "sdf": {
                    "primitives": [
                        {"id": "head", "type": "sphere", "center": [0, 0, 0], "radius": 0.5},
                        {"id": "bulge", "type": "sphere", "center": [0.2, 0.1, 0.4], "radius": 0.12},
                    ],
                    "operations": operations,
                    "resolution": 32,
                }
            },
        }

    def test_mutation_implicit_recessed_feature_without_subtract_is_rejected(self) -> None:
        """A rule must verify what it advises: `implicit` alone is not "real concavity" if
        the SDF is built only from union/smooth-union -- that is a bulge sticking OUT, the
        same US-004 defect (a fake eye) in a different disguise. This is the exact gap
        team-lead found: the rule only checked topologyClass, not what the descriptor
        actually built."""
        spec = base_spec(self._implicit_eye_cavity([
            {"type": "smooth-union", "left": "head", "right": "bulge", "radius": 0.05},
        ]))
        errors, _warnings = validate_spec(spec)
        recessed_errors = [e for e in errors if "recessed feature" in e]
        self.assertTrue(recessed_errors, f"expected a recessed-feature error, got: {errors}")
        message = recessed_errors[0]
        self.assertIn("left-eye-cavity", message)
        self.assertIn("subtract", message)
        self.assertIn("smooth-union", message)  # names the actual operations found

    def test_implicit_recessed_feature_with_subtract_present_alongside_other_ops_is_accepted(self) -> None:
        """`subtract` must be PRESENT, not the ONLY operation -- a socket legitimately
        composed of a smooth-union of two shapes then subtracted is fine."""
        component = self._implicit_eye_cavity([
            {"type": "smooth-union", "left": "head", "right": "bulge", "radius": 0.05, "id": "unioned"},
            {"type": "subtract", "left": "unioned", "right": "bulge"},
        ])
        spec = base_spec(component)
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

    def test_implicit_recessed_feature_missing_sdf_defers_to_the_existing_implicit_check(self) -> None:
        """If geometryDescriptor.sdf is missing entirely, Rule 1 must not also complain --
        the pre-existing `topologyClass 'implicit' requires geometryDescriptor.sdf` check
        already covers that case, and stacking a second, confusing error on top is worse
        than one clear one."""
        spec = base_spec({
            "id": "left-eye-cavity",
            "role": "cavity",
            "primitive": "sphere",
            "topologyClass": "implicit",
            "topologyRationale": "Carved from the head volume.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)
        self.assertTrue(any("requires geometryDescriptor.sdf" in e for e in errors), errors)

    def test_mutation_assembled_solid_convex_sphere_recessed_feature_is_rejected(self) -> None:
        """The third route to US-004, found in review: a deny-list of {surface-relief,
        plane-card} still lets `topologyClass: "assembled-solid"` + a convex sphere through
        -- not surface-relief, not plane-card, and the subtract check never even runs
        because it was scoped to `implicit`. This is arguably the MOST likely authoring
        mistake of the three (assembled-solid is the common default, sphere is the obvious
        eye shape), which is why the rule is now an allow-list (must be implicit+subtract)
        rather than a deny-list (must not be X or Y)."""
        spec = base_spec({
            "id": "eye-cavity",
            "role": "cavity",
            "primitive": "sphere",
            "topologyClass": "assembled-solid",
            "topologyRationale": "Convex sphere standing in for the eye socket.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        recessed_errors = [e for e in errors if "recessed feature" in e]
        self.assertTrue(recessed_errors, f"expected a recessed-feature error, got: {errors}")
        message = recessed_errors[0]
        self.assertIn("eye-cavity", message)
        self.assertIn("assembled-solid", message)
        self.assertIn("implicit", message)
        self.assertIn("subtract", message)

    def test_hollow_and_concave_in_role_do_fire(self) -> None:
        spec = base_spec({
            "id": "eye-hole",
            "role": "hollow",
            "primitive": "plane-card",
            "topologyClass": "surface-relief",
            "topologyRationale": "Flat patch depicting a hollow via a normal map.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        self.assertTrue(any("recessed feature" in e for e in errors), errors)

    def test_mutation_flat_surface_relief_eye_socket_is_rejected(self) -> None:
        """Construct the exact recorded defect (US-004): a flat plane-card patch tagged
        surface-relief standing in for an eye socket. Must exit non-zero with an
        actionable message naming the fix."""
        spec = base_spec({
            "id": "left-eye-socket",
            "name": "Left eye socket",
            "level": "meso",
            "role": "eye-socket",
            "primitive": "plane-card",
            "topologyClass": "surface-relief",
            "topologyRationale": "Flat patch depicting the eye via a normal map.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        recessed_errors = [e for e in errors if "recessed feature" in e]
        self.assertTrue(recessed_errors, f"expected a recessed-feature error, got: {errors}")
        message = recessed_errors[0]
        self.assertIn("left-eye-socket", message)
        self.assertIn("surface-relief", message)
        self.assertIn("plane-card", message)
        self.assertIn("implicit", message)
        self.assertIn("subtract", message)

    def test_mutation_flat_plane_card_eye_is_rejected_even_without_surface_relief_class(self) -> None:
        spec = base_spec({
            "id": "right-eye-socket",
            "role": "eye-socket",
            "primitive": "plane-card",
            "topologyClass": "material-only",
            "topologyRationale": "Flat card with an albedo texture standing in for the socket.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        self.assertTrue(any("recessed feature" in e for e in errors), errors)

    def test_correctly_authored_socket_via_implicit_subtract_is_accepted(self) -> None:
        spec = base_spec({
            "id": "left-eye-socket",
            "name": "Left eye socket",
            "level": "meso",
            "role": "eye-socket",
            "primitive": "sphere",
            "topologyClass": "implicit",
            "topologyRationale": "Carved as a subtractive cavity from the head volume.",
            "parent": None,
            "material": "skin",
            "geometryDescriptor": {
                "sdf": {
                    "primitives": [
                        {"id": "head", "type": "sphere", "center": [0, 0, 0], "radius": 0.5},
                        {"id": "socket", "type": "sphere", "center": [0.2, 0.1, 0.4], "radius": 0.12},
                    ],
                    "operations": [
                        {"type": "subtract", "left": "head", "right": "socket"},
                    ],
                    "resolution": 32,
                }
            },
        })
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

    def test_non_recessed_component_is_unaffected(self) -> None:
        spec = base_spec({
            "id": "torso",
            "role": "body",
            "primitive": "capsule",
            "topologyClass": "surface-relief",
            "topologyRationale": "Body shell carrying baked panel-line relief.",
            "parent": None,
            "material": "skin",
        })
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

    def test_shallow_relief_panel_without_a_cavity_token_is_accepted(self) -> None:
        """Pins the documented escape hatch: the allow-list is strict on purpose (the token
        IS the declaration -- calling a part a cavity/canal/recess holds it to building a
        real one), but a genuinely shallow decorative relief (a 0.2mm panel line, not
        silhouette-relevant, not worth the SDF cost) is simply not a cavity and should not
        be named one. Renaming it out of the recessed vocabulary (e.g. "panel-relief"
        instead of "panel-recess") is the legitimate way out, and it must actually work: a
        surface-relief plane-card panel with no cavity/canal/recess/nostril token, and
        "hollow"/"concave" absent from role, must validate clean of Rule 1."""
        spec = base_spec({
            "id": "grip-panel-relief",
            "name": "Grip panel relief",
            "role": "panel-relief",
            "primitive": "plane-card",
            "topologyClass": "surface-relief",
            "topologyRationale": "Shallow 0.2mm decorative panel line, not silhouette-relevant.",
            "parent": None,
            "material": "skin",
        })
        self.assertFalse(component_is_recessed_feature(spec["componentTree"][0]))
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)

    def test_dimple_is_no_longer_a_recessed_feature_token(self) -> None:
        """"dimple" is shallow by definition -- the wrong word for a rule that requires a
        real carved cavity -- so it was dropped from RECESSED_FEATURE_TOKENS."""
        self.assertNotIn("dimple", RECESSED_FEATURE_TOKENS)
        self.assertFalse(component_is_recessed_feature({"role": "dimple", "name": "Dimple", "id": "cheek-dimple"}))


class OpenShellTopologyTest(unittest.TestCase):
    def test_open_shell_is_a_valid_topology_class(self) -> None:
        self.assertIn("open-shell", VALID_TOPOLOGY_CLASSES)

    def wing_component(self) -> dict:
        return {
            "id": "wing-membrane",
            "name": "Wing membrane",
            "level": "meso",
            "role": "wing",
            "primitive": "plane-card",
            "topologyClass": "open-shell",
            "topologyRationale": "Zero-thickness membrane spanning the wing struts.",
            "parent": None,
            "material": "wing-material",
        }

    def test_mutation_open_shell_without_double_sided_material_is_rejected(self) -> None:
        spec = base_spec(self.wing_component(), material={"id": "wing-material"})
        errors, _warnings = validate_spec(spec)
        shell_errors = [e for e in errors if "open-shell" in e and "doubleSided" in e]
        self.assertTrue(shell_errors, f"expected a doubleSided error, got: {errors}")
        self.assertIn("wing-membrane", shell_errors[0])

    def test_mutation_open_shell_paired_with_closed_sdf_is_rejected(self) -> None:
        component = self.wing_component()
        component["geometryDescriptor"] = {
            "sdf": {
                "primitives": [{"id": "p", "type": "sphere", "center": [0, 0, 0], "radius": 0.2}],
                "operations": [],
                "resolution": 16,
            }
        }
        spec = base_spec(component, material={"id": "wing-material", "doubleSided": True})
        errors, _warnings = validate_spec(spec)
        shell_errors = [e for e in errors if "open-shell" in e and "sdf" in e]
        self.assertTrue(shell_errors, f"expected an sdf-pairing error, got: {errors}")
        self.assertIn("zero thickness", shell_errors[0])

    def test_correctly_authored_open_shell_is_accepted(self) -> None:
        spec = base_spec(self.wing_component(), material={"id": "wing-material", "doubleSided": True})
        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("open-shell" in e for e in errors), errors)

    def test_double_sided_must_be_boolean(self) -> None:
        spec = base_spec(self.wing_component(), material={"id": "wing-material", "doubleSided": "yes"})
        errors, _warnings = validate_spec(spec)
        self.assertTrue(any("doubleSided must be boolean" in e for e in errors), errors)


class RegressionSafetyTest(unittest.TestCase):
    """The new rules must not regress any existing fixture."""

    def test_implicit_character_torso_limb_fixture_still_validates_clean(self) -> None:
        spec = load_fixture()
        errors, _warnings = validate_spec(spec)
        self.assertEqual(errors, [])

    def test_real_character_template_eye_cavity_output_is_accepted(self) -> None:
        """Contract check against worker-template's ACTUAL generated output (not an
        assumption of it): `new_sculpt_spec.py --character` authors the eye socket as
        `eye-cavity-{l,r}` with role="cavity", primitive=sphere, topologyClass=implicit --
        real concavity via the field path, exactly what Rule 1 is meant to accept. It also
        authors a SEPARATE `eye-{l,r}` component (role="detail", assembled-solid, sphere --
        the convex eyeball sitting inside the cavity). team-lead flagged that the pair IS
        the design: a rule tightened against the cavity must not start rejecting the
        eyeball half too, since neither is optional -- one recesses, the other fills it.
        This pins that contract so a future change on either side is caught here."""
        # The fixture came from `new_sculpt_spec.py --character` until that template moved to
        # plugin-character. It is the frozen spec now: identical bytes, and no subprocess.
        spec = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))

        eye_cavities = [c for c in spec["componentTree"] if str(c.get("id", "")).startswith("eye-cavity")]
        eyeballs = [
            c for c in spec["componentTree"]
            if str(c.get("id", "")).startswith("eye-") and not str(c.get("id", "")).startswith("eye-cavity")
        ]
        self.assertTrue(eye_cavities, "the frozen humanoid fixture lost its eye-cavity components")
        self.assertTrue(eyeballs, "the frozen humanoid fixture lost its eyeball detail components")
        for component in eye_cavities:
            self.assertTrue(component_is_recessed_feature(component), component)
            self.assertEqual(component.get("topologyClass"), "implicit", component)
        for component in eyeballs:
            # The eyeball is legitimately convex and must NOT match the recessed signal --
            # if it did, its assembled-solid/sphere shape would now be rejected by the
            # allow-list, which would break correct, existing anatomy.
            self.assertFalse(component_is_recessed_feature(component), component)

        errors, _warnings = validate_spec(spec)
        self.assertFalse(any("recessed feature" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
