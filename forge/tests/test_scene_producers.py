"""Consumer-boundary negatives, not a visual benchmark or independent corpus.

The real pinned Chrome/Three scene smoke is a separate integration prerequisite.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from stage6_compose.compose import CompositionError
from stage6_compose.geometry import _crosses, box_iou, contact_gap, intersection_count, triangles
from stage6_compose.measure import Inputs, _inventory, _prior, _scene_sources, _template_data, image_metrics


def cube(low, high):
    a, b = low, high
    points = [(a,a,a), (b,a,a), (b,b,a), (a,b,a), (a,a,b), (b,a,b), (b,b,b), (a,b,b)]
    faces = [(0,2,1), (0,3,2), (4,5,6), (4,6,7), (0,1,5), (0,5,4),
             (3,7,6), (3,6,2), (0,4,7), (0,7,3), (1,2,6), (1,6,5)]
    return [tuple(points[i] for i in face) for face in faces]


def png(width=16, height=16, *, figure=False, empty_alpha=False):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind+payload)&0xffffffff)
    rows = []
    for y in range(height):
        pixels = bytearray()
        for x in range(width):
            value = 20 if figure and 3 <= x < width-3 and 3 <= y < height-3 else 255
            pixels.extend((value, value, value, 0 if empty_alpha else 255))
        rows.append(b'\0'+pixels)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width,height,8,6,0,0,0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))


class SceneProducerNegatives(unittest.TestCase):
    def test_contained_closed_object_is_interpenetration_without_surface_crossings(self):
        self.assertGreater(intersection_count(cube(-2, 2), cube(-0.5, 0.5)), 0)

    def test_coincident_closed_meshes_cannot_evade_interpenetration(self):
        self.assertGreater(intersection_count(cube(-1, 1), cube(-1, 1)), 0)

    def test_cavity_is_not_mistaken_for_filled_container_volume(self):
        hollow = cube(-2, 2) + [tuple(reversed(tri)) for tri in cube(-1, 1)]
        self.assertEqual(intersection_count(hollow, cube(-0.25, 0.25)), 0)

    def test_crossing_triangles_are_not_replaced_with_box_only_claim(self):
        a = [((-1,0,-1), (1,0,-1), (0,0,1))]
        b = [((0,-1,0), (0,1,0), (0,0,-0.5))]
        self.assertGreater(intersection_count(a, b), 0)

    def test_room_support_boundary_is_not_penetration_but_sunken_prop_is(self):
        room = [((-2,0,-3), (-2,0,3), (2,0,3)), ((-2,0,-3), (2,0,3), (2,0,-3)),
                ((-2,0,-3), (2,0,-3), (2,3,-3)), ((-2,0,-3), (2,3,-3), (-2,3,-3))]
        def prop(bottom):
            return [tuple((x + .65, y + .05 + bottom, z + 1) for x,y,z in tri)
                    for tri in cube(-.05, .05)]
        self.assertEqual(intersection_count(room, prop(0)), 0)
        self.assertGreater(intersection_count(room, prop(-.001)), 0)

    def test_crossing_at_opposite_triangle_vertices_still_has_positive_interior(self):
        first = ((0,0,0), (-1,2,0), (1,2,0))
        second = ((0,1,0), (0,-1,-1), (0,-1,1))
        self.assertTrue(_crosses(first, second))
        self.assertTrue(_crosses(second, first))

    def test_plane_sections_touching_at_one_point_do_not_cross_interiors(self):
        first = ((-1,0,0), (1,0,0), (0,1,0))
        second = ((0,1,0), (0,2,-1), (0,2,1))
        self.assertFalse(_crosses(first, second))
        self.assertFalse(_crosses(second, first))

    def test_missing_support_hit_is_not_zero_contact_gap(self):
        support = [((20,0,20), (21,0,20), (20,0,21))]
        with self.assertRaisesRegex(CompositionError, 'missing-support-ray-hit'):
            contact_gap(cube(-1, 1), support)

    def test_empty_offscreen_and_nonfinite_boxes_cannot_pass(self):
        for box in ([0,0,0,1], [-2,0,-1,1], [0,0,float('nan'),1]):
            with self.subTest(box=box), self.assertRaises(CompositionError):
                box_iou(box, [0,0,1,1])

    def test_empty_and_background_only_masks_are_not_perfect_matches(self):
        for image in (png(), png(empty_alpha=True)):
            with self.subTest(alpha=image[-20:]), self.assertRaisesRegex(CompositionError, 'empty-or-unreliable-foreground'):
                image_metrics(image, image)

    def test_missing_render_figure_cannot_compare_background_as_interior(self):
        with self.assertRaisesRegex(CompositionError, 'empty-or-unreliable-foreground'):
            image_metrics(png(figure=True), png())

    def test_explicit_reference_mask_cannot_hide_empty_runtime_object_mask(self):
        reference = {'width':16,'height':16,'mask':[True]*256}
        render = {'width':16,'height':16,'mask':[False]*256}
        with self.assertRaisesRegex(CompositionError, 'empty-or-invalid-explicit-mask'):
            image_metrics(png(), png(), reference_mask=reference, render_mask=render)

    def test_explicit_mask_with_same_area_but_wrong_dimensions_is_rejected(self):
        mask = {'width':8,'height':32,'mask':[True]*256}
        with self.assertRaisesRegex(CompositionError, 'empty-or-invalid-explicit-mask'):
            image_metrics(png(), png(), reference_mask=mask)

    def test_degenerate_or_nonfinite_geometry_never_returns_nonintersection(self):
        for value in ([], [[[0,0,0],[0,0,0],[0,0,0]]], [[[0,0,0],[1,0,0],[0,float('inf'),0]]]):
            with self.subTest(value=value), self.assertRaises(CompositionError):
                triangles(value)

    def test_duplicate_routed_inventory_cannot_shrink_exact_pair_coverage(self):
        row = {'objectId':'chair','artifactObjectId':'chair'}
        with self.assertRaisesRegex(CompositionError, 'invalid-inventory'):
            _inventory({'kind':'img2.object-inventory','version':1,'objects':[row,row]})

    def test_unknown_class_cannot_fall_back_to_generic_scale(self):
        with self.assertRaisesRegex(CompositionError, 'unknown-size-prior-class'):
            _prior({'objectId':'thing','role':'prop','classId':'unclassified'}, {})

    def test_script_changes_cannot_enter_via_composed_manifest(self):
        with self.assertRaisesRegex(CompositionError, 'untrusted-scene-module'):
            _template_data(b'globalThis.claimedIoU = 1; export function createComposedScene() {}')

    def test_saved_score_bundle_without_placement_preparation_is_rejected(self):
        with self.assertRaisesRegex(CompositionError, 'missing-placement-preparation'):
            _scene_sources(None, None, {'objects':[], 'silhouetteIoU':1}, [], {}, {}, {})

    def test_one_input_path_cannot_change_digest_inside_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, 'evidence.json')
            first = b'{"mode":"surface"}'
            second = b'{"mode":"free"}'
            path.write_bytes(first)
            inputs = Inputs(temporary)
            try:
                inputs.read({'path':'evidence.json','sha256':hashlib.sha256(first).hexdigest()})
                path.write_bytes(second)
                with self.assertRaisesRegex(CompositionError, 'conflicting-reference'):
                    inputs.read({'path':'evidence.json','sha256':hashlib.sha256(second).hexdigest()})
            finally:
                inputs.close()


if __name__ == '__main__':
    unittest.main()
