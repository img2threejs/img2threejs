#!/usr/bin/env python3
"""Tests for the mesh freeze/parity gate, built around the defect it exists to catch.

`TheDefectThisCatches.test_one_nudged_vertex_is_caught_and_located` is the whole argument for the
module: it moves a SINGLE float in a SINGLE position buffer -- the smallest possible version of "the
animation pipeline broke the mesh" -- and requires the gate not merely to fail but to say which
mesh, which attribute, which element, and what the value was before and after. A gate that only says
"something changed" would not have shortened a single one of the investigations this module exists
to end.

The other load-bearing test is `LegalAdditions`: adding skinIndex and skinWeight between freeze and
verify must PASS. If it did not, the gate would fail every successful rig and would be turned off
within a day, so that test defends the design decision as hard as the failure test defends the
mechanism.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

from mesh_parity import (  # noqa: E402
    FROZEN_ATTRIBUTES,
    FROZEN_BUFFERS,
    INDEX_KEY,
    KIND_ATTRIBUTE_ADDED,
    KIND_ATTRIBUTE_MISSING,
    KIND_COUNT,
    KIND_DUPLICATE,
    KIND_HASH,
    KIND_MESH_ADDED,
    KIND_MESH_MISSING,
    SCHEMA_VERSION,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNEVALUATED,
    Manifest,
    ParityReport,
    freeze,
    main,
    verify,
)


def cube(name: str, origin: float = 0.0) -> dict:
    """One small closed mesh: 8 positions, 8 normals, 8 uvs, 36 indices.

    Small enough to reason about by hand and large enough that a single changed float is genuinely
    lost in the buffer unless something points at it.
    """
    corners = [
        (x, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)
    ]
    position: list[float] = []
    normal: list[float] = []
    uv: list[float] = []
    for x, y, z in corners:
        position.extend([x + origin, y, z])
        normal.extend([x - 0.5, y - 0.5, z - 0.5])
        uv.extend([x, y])
    index = [
        0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1,
        2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3,
    ]
    return {
        "name": name,
        "attributes": {"position": position, "normal": normal, "uv": uv},
        INDEX_KEY: index,
    }


def payload(*names: str) -> dict:
    """A fresh payload every call; nothing here is shared between tests."""
    chosen = names or ("torso", "arm")
    return {"meshes": [cube(name, origin=offset) for offset, name in enumerate(chosen)]}


def mesh_of(document: dict, name: str) -> dict:
    for mesh in document["meshes"]:
        if mesh["name"] == name:
            return mesh
    raise AssertionError(f"no mesh named {name!r} in the payload")


def failures_of(report: ParityReport, kind: str) -> list:
    return [failure for failure in report.failures if failure.kind == kind]


class TheDefectThisCatches(unittest.TestCase):
    """One vertex moved during the animation pass. The reason this module exists."""

    def test_the_gate_passes_the_geometry_it_froze(self) -> None:
        # A gate that fails everything proves nothing about the thing it failed, so this comes
        # first: the unchanged payload must pass, cleanly and with the meshes actually compared.
        document = payload()
        report = verify(freeze(document), document)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.status, STATUS_PASS)
        self.assertEqual(report.failure_count, 0)
        self.assertEqual(report.meshes_compared, 2)

    def test_one_nudged_vertex_is_caught_and_located(self) -> None:
        document = payload()
        manifest = freeze(document)

        broken = payload()
        moved = mesh_of(broken, "arm")
        original = moved["attributes"]["position"][13]
        moved["attributes"]["position"][13] = original + 1e-6

        report = verify(manifest, broken)

        self.assertFalse(report.ok)
        self.assertEqual(report.status, STATUS_FAIL)
        self.assertEqual(report.failure_count, 1, report.summary())

        failure = report.failures[0]
        self.assertEqual(failure.mesh, "arm")
        self.assertEqual(failure.attribute, "position")
        self.assertEqual(failure.kind, KIND_HASH)

        # Naming the mesh and the attribute is not enough. A 24-float buffer -- or a 200k-float one
        # -- needs the element, or the reader is back to diffing numbers by eye.
        self.assertEqual(failure.differing_elements, 1)
        self.assertEqual(len(failure.differences), 1)
        difference = failure.differences[0]
        self.assertEqual(difference.index, 13)
        self.assertEqual(difference.frozen, original)
        self.assertEqual(difference.current, original + 1e-6)
        self.assertIn("13", failure.detail)
        self.assertIn("arm", report.summary())
        self.assertIn("position", report.summary())

    def test_the_untouched_mesh_in_the_same_payload_is_not_implicated(self) -> None:
        # Blaming the whole model for one bad buffer is how a gate loses its audience.
        manifest = freeze(payload())
        broken = payload()
        mesh_of(broken, "arm")["attributes"]["position"][0] += 0.5
        report = verify(manifest, broken)
        self.assertEqual({failure.mesh for failure in report.failures}, {"arm"})

    def test_a_wholesale_rebuild_reads_differently_from_a_nudge(self) -> None:
        # The differing-element count is the difference between "someone moved a vertex" and
        # "someone regenerated the limb", which are different bugs with different owners.
        manifest = freeze(payload())
        rebuilt = payload()
        mesh = mesh_of(rebuilt, "arm")
        mesh["attributes"]["position"] = [
            value * 1.5 + 0.01 for value in mesh["attributes"]["position"]
        ]
        report = verify(manifest, rebuilt)
        failure = failures_of(report, KIND_HASH)[0]
        self.assertEqual(failure.differing_elements, 24)
        # The list of reported differences is capped; the count is not.
        self.assertLessEqual(len(failure.differences), failure.differing_elements)


class LegalAdditions(unittest.TestCase):
    """Adding skinIndex/skinWeight is the entire point of the implementation phase."""

    def test_adding_skin_attributes_between_freeze_and_verify_passes(self) -> None:
        document = payload()
        manifest = freeze(document)

        rigged = payload()
        for mesh in rigged["meshes"]:
            vertices = len(mesh["attributes"]["position"]) // 3
            mesh["attributes"]["skinIndex"] = [0, 1, 0, 0] * vertices
            mesh["attributes"]["skinWeight"] = [0.6, 0.4, 0.0, 0.0] * vertices

        report = verify(manifest, rigged)

        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.status, STATUS_PASS)
        self.assertEqual(report.failure_count, 0)
        # And they are listed, not merely tolerated: a rig that silently added nothing and a rig
        # that added skinning look the same to a gate that only reports failures.
        self.assertEqual(
            sorted(report.additions),
            ["arm.skinIndex", "arm.skinWeight", "torso.skinIndex", "torso.skinWeight"],
        )
        self.assertIn("added (legal)", report.summary())

    def test_skin_attributes_are_not_in_the_frozen_set(self) -> None:
        # The pass above is a consequence of this, not a coincidence.
        self.assertNotIn("skinIndex", FROZEN_BUFFERS)
        self.assertNotIn("skinWeight", FROZEN_BUFFERS)
        self.assertEqual(FROZEN_ATTRIBUTES, ("position", "normal", "uv"))
        self.assertEqual(FROZEN_BUFFERS, ("position", "normal", "uv", INDEX_KEY))

    def test_any_non_frozen_attribute_is_legal_not_just_the_skin_pair(self) -> None:
        manifest = freeze(payload("torso"))
        coloured = payload("torso")
        vertices = len(mesh_of(coloured, "torso")["attributes"]["position"]) // 3
        mesh_of(coloured, "torso")["attributes"]["color"] = [1.0, 1.0, 1.0] * vertices
        report = verify(manifest, coloured)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.additions, ("torso.color",))

    def test_adding_a_frozen_class_attribute_is_not_legal(self) -> None:
        # uv was never frozen for this mesh, so producing one afterwards is new surface data, not an
        # addition the gate is here to permit.
        bare = payload("torso")
        del mesh_of(bare, "torso")["attributes"]["uv"]
        manifest = freeze(bare)
        report = verify(manifest, payload("torso"))
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_ATTRIBUTE_ADDED)[0]
        self.assertEqual((failure.mesh, failure.attribute), ("torso", "uv"))

    def test_dropping_a_frozen_attribute_fails_naming_it(self) -> None:
        manifest = freeze(payload("torso"))
        stripped = payload("torso")
        del mesh_of(stripped, "torso")["attributes"]["normal"]
        report = verify(manifest, stripped)
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_ATTRIBUTE_MISSING)[0]
        self.assertEqual((failure.mesh, failure.attribute), ("torso", "normal"))


class MeshSetChanges(unittest.TestCase):
    def test_removing_a_mesh_fails_naming_it(self) -> None:
        manifest = freeze(payload("torso", "arm"))
        report = verify(manifest, payload("torso"))
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_MESH_MISSING)[0]
        self.assertEqual(failure.mesh, "arm")
        self.assertEqual(report.meshes_compared, 1)

    def test_adding_a_mesh_fails_naming_it(self) -> None:
        # Implementation may add a skeleton. It may not add geometry -- a mesh that appears during
        # rigging is either a duplicated limb or a debug helper, and both ship.
        manifest = freeze(payload("torso"))
        report = verify(manifest, payload("torso", "arm"))
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_MESH_ADDED)[0]
        self.assertEqual(failure.mesh, "arm")

    def test_reordering_meshes_passes_because_matching_is_by_name(self) -> None:
        document = payload("torso", "arm", "head", "leg")
        manifest = freeze(document)
        shuffled = payload("torso", "arm", "head", "leg")
        random.Random(20250825).shuffle(shuffled["meshes"])
        self.assertNotEqual(
            [mesh["name"] for mesh in shuffled["meshes"]],
            [mesh["name"] for mesh in document["meshes"]],
            "the shuffle must actually reorder, or this test proves nothing",
        )
        report = verify(manifest, shuffled)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.meshes_compared, 4)

    def test_a_renamed_mesh_reads_as_one_removed_and_one_added(self) -> None:
        manifest = freeze(payload("torso", "arm"))
        renamed = payload("torso", "arm")
        mesh_of(renamed, "arm")["name"] = "arm.001"
        report = verify(manifest, renamed)
        self.assertFalse(report.ok)
        self.assertEqual(failures_of(report, KIND_MESH_MISSING)[0].mesh, "arm")
        self.assertEqual(failures_of(report, KIND_MESH_ADDED)[0].mesh, "arm.001")


class DuplicateNames(unittest.TestCase):
    """Two meshes with one name cannot be told apart, so they are never silently zipped."""

    def test_duplicate_names_in_the_payload_are_a_reported_failure(self) -> None:
        manifest = freeze(payload("torso", "arm"))
        doubled = payload("torso", "arm")
        doubled["meshes"].append(cube("arm", origin=9.0))
        report = verify(manifest, doubled)
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_DUPLICATE)[0]
        self.assertEqual(failure.mesh, "arm")
        self.assertIn("2", failure.detail)
        # The ambiguous name is excluded from comparison entirely rather than compared against
        # whichever copy happened to be last.
        self.assertEqual(report.meshes_compared, 1)
        self.assertEqual(failures_of(report, KIND_HASH), [])

    def test_duplicate_names_in_the_manifest_are_a_reported_failure(self) -> None:
        manifest = freeze(payload("torso", "arm"))
        doubled = dataclasses.replace(manifest, meshes=manifest.meshes + (manifest.meshes[1],))
        report = verify(doubled, payload("torso", "arm"))
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_DUPLICATE)[0]
        self.assertEqual(failure.mesh, "arm")

    def test_freeze_refuses_to_record_duplicate_names(self) -> None:
        # freeze is a producer, not a gate: there is no correct manifest to emit here, so it stops.
        doubled = payload("torso")
        doubled["meshes"].append(cube("torso", origin=9.0))
        with self.assertRaises(ValueError):
            freeze(doubled)


class CountsAreCheckedIndependentlyOfHashes(unittest.TestCase):
    def test_a_vertex_count_change_fails_even_when_the_hashes_were_recomputed(self) -> None:
        # The scenario: someone shortens a mesh and re-runs freeze to make the hashes agree, but the
        # recorded counts still come from the real freeze. Comparing counts against the manifest's
        # RECORDED counts rather than against the length of its own buffers is what catches that.
        manifest = freeze(payload("torso"))

        short = payload("torso")
        mesh = mesh_of(short, "torso")
        mesh["attributes"]["position"] = mesh["attributes"]["position"][:-3]
        mesh["attributes"]["normal"] = mesh["attributes"]["normal"][:-3]
        mesh["attributes"]["uv"] = mesh["attributes"]["uv"][:-2]
        mesh[INDEX_KEY] = [value for value in mesh[INDEX_KEY] if value != 7]

        recomputed = freeze(short)
        spliced = dataclasses.replace(
            recomputed,
            meshes=(
                dataclasses.replace(
                    recomputed.meshes[0],
                    vertex_count=manifest.meshes[0].vertex_count,
                    index_count=manifest.meshes[0].index_count,
                ),
            ),
        )

        report = verify(spliced, short)
        self.assertFalse(report.ok)
        # Every hash agrees -- the buffers really are the ones that were hashed.
        self.assertEqual(failures_of(report, KIND_HASH), [], report.summary())
        counts = failures_of(report, KIND_COUNT)
        self.assertEqual({failure.mesh for failure in counts}, {"torso"})
        details = " ".join(failure.detail for failure in counts)
        self.assertIn("vertexCount 8 -> 7", details)
        self.assertIn("indexCount 36 -> 30", details)

    def test_a_truncated_buffer_reports_a_count_change_not_a_hash_mismatch(self) -> None:
        # Reporting both would double every truncation, and "the hash differs" adds nothing once the
        # length already differs.
        manifest = freeze(payload("torso"))
        short = payload("torso")
        short_mesh = mesh_of(short, "torso")
        short_mesh["attributes"]["normal"] = short_mesh["attributes"]["normal"][:-3]
        report = verify(manifest, short)
        self.assertFalse(report.ok)
        self.assertEqual(report.failure_count, 1, report.summary())
        failure = report.failures[0]
        self.assertEqual((failure.mesh, failure.attribute, failure.kind), ("torso", "normal", KIND_COUNT))


class IndexBufferChanges(unittest.TestCase):
    def test_a_rewired_index_buffer_fails_naming_index(self) -> None:
        # The weld/rewire case: every vertex is exactly where it was, so a position-only gate would
        # pass this while the surface has been re-stitched into a different shape.
        manifest = freeze(payload("torso"))
        rewired = payload("torso")
        mesh = mesh_of(rewired, "torso")
        mesh[INDEX_KEY][4], mesh[INDEX_KEY][5] = mesh[INDEX_KEY][5], mesh[INDEX_KEY][4]

        report = verify(manifest, rewired)

        self.assertFalse(report.ok)
        self.assertEqual(report.failure_count, 1, report.summary())
        failure = report.failures[0]
        self.assertEqual(failure.attribute, INDEX_KEY)
        self.assertEqual(failure.kind, KIND_HASH)
        self.assertEqual(failure.mesh, "torso")
        self.assertEqual(failure.differing_elements, 2)
        self.assertEqual(failure.differences[0].index, 4)

    def test_the_vertex_data_really_is_untouched_in_that_case(self) -> None:
        # Otherwise the test above might be catching a position change by accident.
        manifest = freeze(payload("torso"))
        rewired = payload("torso")
        mesh = mesh_of(rewired, "torso")
        mesh[INDEX_KEY][4], mesh[INDEX_KEY][5] = mesh[INDEX_KEY][5], mesh[INDEX_KEY][4]
        self.assertEqual(
            mesh["attributes"]["position"],
            mesh_of(payload("torso"), "torso")["attributes"]["position"],
        )


class HonestyWhenThereIsNothingToCheck(unittest.TestCase):
    def test_verify_without_a_manifest_is_unevaluated_and_not_ok(self) -> None:
        report = verify(None, payload())
        self.assertEqual(report.status, STATUS_UNEVALUATED)
        self.assertFalse(report.ok)
        self.assertEqual(report.failure_count, 0)
        self.assertTrue(report.reason)
        # Not-ok with zero failures is the whole point: nothing is wrong and nothing is proven.
        self.assertNotEqual(report.status, STATUS_PASS)
        self.assertNotEqual(report.status, STATUS_FAIL)

    def test_an_empty_manifest_is_unevaluated_not_a_pass(self) -> None:
        empty = Manifest(schema_version=SCHEMA_VERSION, frozen_attributes=FROZEN_BUFFERS, meshes=())
        report = verify(empty, payload())
        self.assertEqual(report.status, STATUS_UNEVALUATED)
        self.assertFalse(report.ok)

    def test_a_manifest_from_another_schema_is_unevaluated(self) -> None:
        manifest = freeze(payload("torso"))
        report = verify(dataclasses.replace(manifest, schema_version=99), payload("torso"))
        self.assertEqual(report.status, STATUS_UNEVALUATED)
        self.assertFalse(report.ok)
        self.assertIn("99", report.reason or "")

    def test_a_manifest_frozen_over_a_different_attribute_set_is_unevaluated(self) -> None:
        manifest = freeze(payload("torso"))
        narrowed = dataclasses.replace(manifest, frozen_attributes=("position",))
        report = verify(narrowed, payload("torso"))
        self.assertEqual(report.status, STATUS_UNEVALUATED)
        self.assertFalse(report.ok)

    def test_a_manifest_whose_hash_contradicts_its_own_buffer_says_so(self) -> None:
        # If the scan finds no differing element after the hash said there was one, the payload is
        # not the liar -- the manifest is.
        manifest = freeze(payload("torso"))
        fingerprint = manifest.meshes[0]
        tampered = dataclasses.replace(
            fingerprint,
            attribute_hashes={**fingerprint.attribute_hashes, "position": "00" * 32},
        )
        report = verify(dataclasses.replace(manifest, meshes=(tampered,)), payload("torso"))
        self.assertFalse(report.ok)
        failure = failures_of(report, KIND_HASH)[0]
        self.assertEqual(failure.differing_elements, 0)
        self.assertIn("manifest", failure.detail)


class JsonRoundTripStability(unittest.TestCase):
    """Why the hash is over packed bytes and not over the JSON text."""

    def test_a_json_round_trip_does_not_change_a_single_hash(self) -> None:
        document = payload("torso", "arm")
        before = freeze(document)
        after = freeze(json.loads(json.dumps(document)))
        for left, right in zip(before.meshes, after.meshes):
            self.assertEqual(left.name, right.name)
            self.assertEqual(left.attribute_hashes, right.attribute_hashes)
        self.assertTrue(verify(before, json.loads(json.dumps(document))).ok)

    def test_different_json_spellings_of_the_same_number_hash_the_same(self) -> None:
        # This is what packed-byte hashing buys. `1`, `1.0` and `1e0` are three legal spellings of
        # one double; hashing the text would call each of them a mesh change.
        terse = json.loads(
            '{"meshes": [{"name": "m", "attributes": {"position": [1, 2, 3, 0, 5e-1, 0]},'
            ' "index": [0, 1, 0]}]}'
        )
        verbose = json.loads(
            '{"meshes": [{"name": "m", "attributes": {"position": [1.0, 2.0, 3.0, 0.0, 0.5, 0.0]},'
            ' "index": [0.0, 1.0, 0.0]}]}'
        )
        self.assertNotEqual(json.dumps(terse), json.dumps(verbose))
        self.assertEqual(
            freeze(terse).meshes[0].attribute_hashes,
            freeze(verbose).meshes[0].attribute_hashes,
        )
        self.assertTrue(verify(freeze(terse), verbose).ok)

    def test_a_manifest_survives_being_written_and_read_back(self) -> None:
        document = payload("torso", "arm")
        manifest = freeze(document)
        restored = Manifest.from_dict(json.loads(json.dumps(manifest.to_dict())))
        self.assertTrue(verify(restored, document).ok)
        # And still fails on the same defect after the round trip.
        broken = payload("torso", "arm")
        mesh_of(broken, "torso")["attributes"]["position"][0] += 1e-9
        self.assertFalse(verify(restored, broken).ok)

    def test_the_frozen_bytes_are_bit_exact_not_approximate(self) -> None:
        # -0.0 and 0.0 compare equal in float and are different bytes. The gate reports the change,
        # because a sign flip on a normal component is a real, visible difference.
        base = json.loads('{"meshes": [{"name": "m", "attributes": {"position": [0.0, 1.0, 2.0]}}]}')
        flipped = json.loads(
            '{"meshes": [{"name": "m", "attributes": {"position": [-0.0, 1.0, 2.0]}}]}'
        )
        report = verify(freeze(base), flipped)
        self.assertFalse(report.ok)
        self.assertEqual(report.failures[0].differences[0].index, 0)


class Cli(unittest.TestCase):
    """The CLI's exit code is the gate as CI sees it, so it is checked, not assumed.

    stdout and stderr are captured rather than let through: the report JSON is the CLI's product,
    not this test suite's, and the exit-2 case is SUPPOSED to write an error line -- printing either
    here would bury the test results they are meant to be checked by.
    """

    @staticmethod
    def run_cli(argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(argv)

    def test_freeze_then_verify_exits_zero_and_a_broken_payload_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "good.json"
            bad = root / "bad.json"
            manifest = root / "manifest.json"

            document = payload("torso", "arm")
            good.write_text(json.dumps(document), encoding="utf-8")
            broken = payload("torso", "arm")
            mesh_of(broken, "arm")["attributes"]["position"][2] += 0.25
            bad.write_text(json.dumps(broken), encoding="utf-8")

            self.assertEqual(self.run_cli(["freeze", str(good), "--out", str(manifest)]), 0)
            self.assertTrue(manifest.exists())
            self.assertEqual(self.run_cli(["verify", str(manifest), str(good)]), 0)
            self.assertEqual(self.run_cli(["verify", str(manifest), str(bad)]), 1)

    def test_an_unreadable_input_exits_two_rather_than_reporting_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "nope.json"
            self.assertEqual(self.run_cli(["freeze", str(missing)]), 2)


if __name__ == "__main__":
    unittest.main()
