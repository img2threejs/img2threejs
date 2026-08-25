from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import generate_threejs_factory as generator  # noqa: E402


def spec_with(primitive: str, topology: str) -> dict:
    """One child part that declares BOTH a primitive and an attachment contract.

    This is the ordinary case, not an exotic one: the structural pass requires an attachment on
    every child appendage, so any head, ear, limb or tail that is not itself a cylinder arrives
    here.
    """
    return {
        "targetName": "Attachment Primitive",
        "targetId": "attachment-primitive",
        "schemaVersion": "2.1",
        "materials": [{"id": "base", "name": "Base", "baseColor": "#808080"}],
        "buildPasses": [{"id": "blockout", "acceptance": []}],
        "suitability": "pass",
        "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
        "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
        "proceduralStrategy": ["blockout"],
        "componentTree": [
            {
                "id": "body",
                "name": "Body",
                "level": "macro",
                "role": "body",
                "primitive": "ellipsoid",
                "topologyClass": "continuous-sculpt",
                "topologyRationale": "test",
                "parent": None,
                "material": "base",
                "dimensions": {"width": 1.0, "height": 0.6, "depth": 1.4, "units": "relative"},
                "transform": {"position": [0, 0.5, 0], "rotation": [0, 0, 0]},
            },
            {
                "id": "part",
                "name": "Part",
                "level": "macro",
                "role": "head",
                "primitive": primitive,
                "topologyClass": topology,
                "topologyRationale": "test",
                "parent": "body",
                "material": "base",
                "dimensions": {"width": 0.9, "height": 0.7, "depth": 0.8, "units": "relative"},
                "transform": {"position": [0, 0.4, 0.6], "rotation": [0, 0, 0]},
                "attachment": {
                    "parentSocket": "neck",
                    "localStart": [0.0, -0.35, -0.4],
                    "localEnd": [0.0, 0.35, 0.4],
                    "contactType": "embed",
                    "embedDepth": 0.2,
                    "gapTolerance": 0.004,
                    "confidence": 0.9,
                },
            },
        ],
    }


class AttachmentDoesNotOverridePrimitive(unittest.TestCase):
    def _emit(self, primitive: str, topology: str) -> str:
        return generator.generate(spec_with(primitive, topology), "blockout")

    def test_an_ellipsoid_with_an_attachment_does_not_take_the_endpoint_geometry_path(self):
        """The endpoint binding is what selects the geometry, so that is what is asserted.

        The emitted geometry line is a ternary carrying both branches, so grepping for
        `CylinderGeometry` proves nothing either way — what decides which branch runs is whether
        the endpoint is null. For a non-attachment primitive it is statically null.
        """
        source = self._emit("ellipsoid", "continuous-sculpt")
        self.assertIn("const endpoint_part_1 = makeAttachmentEndpoint(null);", source)
        self.assertNotIn("const endpoint_part_1 = makeAttachmentEndpoint(attachment_part_1);",
                         source)
        self.assertIn("SphereGeometry", source)

    def test_a_tapered_sweep_with_an_attachment_keeps_its_swept_geometry(self):
        source = self._emit("tapered-sweep", "continuous-sculpt")
        self.assertIn("const endpoint_part_1 = makeAttachmentEndpoint(null);", source)
        self.assertIn("buildTaperedSweepGeometry", source)

    def test_a_cylinder_with_an_attachment_still_derives_geometry_from_its_endpoints(self):
        """Negative control: the endpoint path is not removed, only scoped.

        A cylinder, cone, capsule, tube or curve-sweep IS an attachment shape, and deriving its
        radius and length from the measured endpoints is the whole point of the contract.
        """
        source = self._emit("cylinder", "assembled-solid")
        self.assertIn("const endpoint_part_1 = makeAttachmentEndpoint(attachment_part_1);", source)

    def test_every_attachment_primitive_still_takes_the_endpoint_path(self):
        for primitive, topology in (("cylinder", "assembled-solid"), ("cone", "assembled-solid"),
                                    ("capsule", "assembled-solid"), ("tube", "continuous-sculpt"),
                                    ("curve-sweep", "continuous-sculpt")):
            with self.subTest(primitive=primitive):
                source = self._emit(primitive, topology)
                self.assertIn(
                    "const endpoint_part_1 = makeAttachmentEndpoint(attachment_part_1);", source
                )

    def test_an_implicit_component_is_placed_by_its_transform_not_its_attachment(self):
        """An implicit part must still declare a `primitive`, and the fixture's is `capsule`.

        That put it in ATTACHMENT_PRIMITIVES, so a head authored at y 0.705 was emitted at its
        attachment's localStart — y -0.195, inside the body — and the model rendered with no head.
        """
        spec = spec_with("capsule", "implicit")
        part = spec["componentTree"][1]
        part["geometryDescriptor"] = {"sdf": {
            "primitives": [{"id": "ball", "type": "sphere", "radius": 0.3,
                            "transform": {"position": [0, 0, 0]}}],
            "operations": [],
            "resolution": 16,
            "bounds": {"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]},
        }}
        source = generator.generate(spec, "blockout")
        self.assertIn("polygonizeSdf", source)
        self.assertIn("const endpoint_part_1 = makeAttachmentEndpoint(null);", source)
        self.assertIn("node_part_1.position.set(0.0, 0.4, 0.6);", source)

    def test_a_non_attachment_primitive_still_carries_its_attachment_contract(self):
        """The contract survives; only the geometry override goes.

        It ships on `userData.sculptComponent`, which is what `attachment_anchor.py` reads. The
        local `const attachment_*` binding is emitted only where the endpoint path consumes it,
        because an unused local would not survive the project's typecheck.
        """
        source = self._emit("ellipsoid", "continuous-sculpt")
        self.assertIn('"parentSocket": "neck"', source)
        self.assertIn('"contactType": "embed"', source)
        self.assertIn('"gapTolerance": 0.004', source)

    def test_the_part_keeps_its_authored_dimensions(self):
        """An endpoint-derived part is not re-scaled; an authored one must be.

        The scale call is guarded by `if (!endpoint)`, so this is the other half of the same fact:
        with the endpoint statically null the authored dimensions reach the vertex data.
        """
        source = self._emit("ellipsoid", "continuous-sculpt")
        self.assertIn("mesh_part_1Geometry.scale(0.9, 0.7, 0.8);", source)


if __name__ == "__main__":
    unittest.main()
