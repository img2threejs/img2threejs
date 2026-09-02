"""The base's side of the domain contract: pulling a domain's spec augmentation.

The base authors a skeleton and lets the agent infer the shape from the reference. An installed
domain plugin publishes an authoritative recipe as a workspace artifact, and the base merges it here.
These tests pin the base's rules for that merge; the recipe itself belongs to whichever plugin
publishes it, and is tested there.

The rule that matters most is raise-only. A plugin is installed to raise the bar, so the merge must
make it structurally unable to lower one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_shared"))

from spec_augmentation import SpecAugmentationError, merge_spec_augmentation  # noqa: E402


def artifact(**parts):
    base = {"kind": "spec-augmentation-v1", "provenance": {"provider": "testdomain", "version": "1.0.0"}}
    base.update(parts)
    return base


class RaiseOnly(unittest.TestCase):
    """A plugin may raise a floor and may never lower one."""

    def test_a_lower_numeric_floor_is_clamped_not_applied(self) -> None:
        spec = {"qualityContract": {"minimumSpecDepth": {"macroComponents": 8}}}
        record = merge_spec_augmentation(spec, artifact(qualityFloors={"minimumSpecDepth": {"macroComponents": 5}}))
        self.assertEqual(spec["qualityContract"]["minimumSpecDepth"]["macroComponents"], 8)
        self.assertTrue(record["clamped"], "a clamped floor must be recorded, not silently applied")

    def test_a_lower_target_min_details_is_clamped(self) -> None:
        spec = {"preSpecAssessment": {"detailInventory": {"targetMinDetails": 20}}}
        merge_spec_augmentation(spec, artifact(qualityFloors={"targetMinDetails": 16}))
        self.assertEqual(spec["preSpecAssessment"]["detailInventory"]["targetMinDetails"], 20)

    def test_a_looser_quality_tier_is_refused(self) -> None:
        spec = {"qualityContract": {"qualityBar": "complex"}}
        merge_spec_augmentation(spec, artifact(qualityFloors={"qualityBar": "moderate"}))
        self.assertEqual(spec["qualityContract"]["qualityBar"], "complex")

    def test_raising_is_allowed(self) -> None:
        spec = {"qualityContract": {"qualityBar": "moderate", "minimumSpecDepth": {"macroComponents": 2}}}
        merge_spec_augmentation(spec, artifact(qualityFloors={
            "qualityBar": "ultra-complex", "minimumSpecDepth": {"macroComponents": 5}}))
        self.assertEqual(spec["qualityContract"]["qualityBar"], "ultra-complex")
        self.assertEqual(spec["qualityContract"]["minimumSpecDepth"]["macroComponents"], 5)


class RaiseOnlyCoversEveryPathToAFloor(unittest.TestCase):
    """The clamp must guard the VALUE, not just the qualityFloors partition.

    assessmentPatch merges into preSpecAssessment, and `detailInventory.targetMinDetails` -- the
    exact number strict validation reads -- lives there. Before this test existed, a patch could
    drop the floor 40 -> 2 with `clamped: []` reporting nothing, and plugin-cs2's own emit tool sent
    the value through BOTH partitions with the unclamped one winning (found in review of PR #106 by
    running the code, not by reading it).
    """

    def test_assessment_patch_cannot_lower_the_detail_floor(self) -> None:
        spec = {"preSpecAssessment": {"detailInventory": {"targetMinDetails": 40}}}
        record = merge_spec_augmentation(
            spec, artifact(assessmentPatch={"detailInventory": {"targetMinDetails": 2}})
        )
        self.assertEqual(spec["preSpecAssessment"]["detailInventory"]["targetMinDetails"], 40)
        self.assertTrue(record["clamped"], "the kept-over-proposed decision must leave a record")

    def test_assessment_patch_may_still_raise_the_detail_floor(self) -> None:
        spec = {"preSpecAssessment": {"detailInventory": {"targetMinDetails": 12}}}
        record = merge_spec_augmentation(
            spec, artifact(assessmentPatch={"detailInventory": {"targetMinDetails": 60}})
        )
        self.assertEqual(spec["preSpecAssessment"]["detailInventory"]["targetMinDetails"], 60)
        self.assertEqual(record["clamped"], [])

    def test_assessment_patch_other_detail_inventory_keys_still_merge(self) -> None:
        spec = {"preSpecAssessment": {"detailInventory": {"targetMinDetails": 40}}}
        merge_spec_augmentation(
            spec, artifact(assessmentPatch={"detailInventory": {"expectedFinishes": ["anodized"]}})
        )
        self.assertEqual(
            spec["preSpecAssessment"]["detailInventory"]["expectedFinishes"], ["anodized"]
        )
        self.assertEqual(spec["preSpecAssessment"]["detailInventory"]["targetMinDetails"], 40)

    def test_a_non_dict_detail_inventory_patch_is_refused_not_applied(self) -> None:
        # A non-dict value used to fall through to the plain-assignment branch and clobber the
        # whole dict the floor lives in -- strict validation then saw no positive targetMinDetails
        # and disabled the detail gate entirely, with `clamped` reporting nothing.
        spec = {"preSpecAssessment": {"detailInventory": {"targetMinDetails": 40}}}
        with self.assertRaises(SpecAugmentationError):
            merge_spec_augmentation(spec, artifact(assessmentPatch={"detailInventory": 0}))
        self.assertEqual(spec["preSpecAssessment"]["detailInventory"]["targetMinDetails"], 40)

    def test_a_non_dict_object_class_patch_is_refused_not_applied(self) -> None:
        spec = {"preSpecAssessment": {"objectClass": {"domain": "base"}}}
        with self.assertRaises(SpecAugmentationError):
            merge_spec_augmentation(
                spec, artifact(assessmentPatch={"objectClass": "weapon"}), domain_id="testdomain"
            )
        self.assertEqual(spec["preSpecAssessment"]["objectClass"], {"domain": "base"})

    def test_a_kept_tier_is_recorded_like_a_kept_number(self) -> None:
        spec = {"qualityContract": {"qualityBar": "ultra-complex"}}
        record = merge_spec_augmentation(spec, artifact(qualityFloors={"qualityBar": "simple"}))
        self.assertEqual(spec["qualityContract"]["qualityBar"], "ultra-complex")
        self.assertTrue(record["clamped"], "a tier kept over a looser proposal must leave a record")

    def test_a_malformed_base_tier_is_refused_not_silently_replaced(self) -> None:
        spec = {"qualityContract": {"qualityBar": "Ultra-Complex"}}
        with self.assertRaises(SpecAugmentationError) as ctx:
            merge_spec_augmentation(spec, artifact(qualityFloors={"qualityBar": "simple"}))
        self.assertIn("Ultra-Complex", str(ctx.exception))


class WhatAnArtifactMayNotDo(unittest.TestCase):
    def test_it_may_not_set_a_base_owned_section(self) -> None:
        with self.assertRaises(SpecAugmentationError) as ctx:
            merge_spec_augmentation({}, artifact(specSections={"qualityContract": {"qualityBar": "simple"}}))
        self.assertIn("base-owned", str(ctx.exception))

    def test_it_may_not_set_the_resolved_domain_marker(self) -> None:
        # The marker records which provider the base resolved. An artifact claiming it would let a
        # plugin relabel the run as some other domain.
        with self.assertRaises(SpecAugmentationError) as ctx:
            merge_spec_augmentation({}, artifact(assessmentPatch={"objectClass": {"domain": "somewhere-else"}}))
        self.assertIn("domain resolution", str(ctx.exception))

    def test_an_unknown_partition_is_refused_not_ignored(self) -> None:
        with self.assertRaises(SpecAugmentationError):
            merge_spec_augmentation({}, artifact(qualityFlrs={}))

    def test_an_unknown_floor_key_is_refused(self) -> None:
        with self.assertRaises(SpecAugmentationError):
            merge_spec_augmentation({}, artifact(qualityFloors={"nope": 1}))

    def test_an_unsupported_artifact_kind_is_refused(self) -> None:
        with self.assertRaises(SpecAugmentationError):
            merge_spec_augmentation({}, {"kind": "something-else", "provenance": {"provider": "p", "version": "1"}})

    def test_provenance_is_required(self) -> None:
        with self.assertRaises(SpecAugmentationError):
            merge_spec_augmentation({}, {"kind": "spec-augmentation-v1"})


class WhatAnArtifactMayDo(unittest.TestCase):
    def test_domain_sections_are_accepted_opaquely(self) -> None:
        # The base cannot validate a finish recipe or a rig, and does not try. It only refuses keys
        # it owns -- a deny-list, so a domain nobody has written yet needs no base change.
        spec = {}
        merge_spec_augmentation(spec, artifact(specSections={
            "componentTree": [{"id": "root"}], "somethingDomainSpecific": {"anything": True}}))
        self.assertEqual(spec["componentTree"], [{"id": "root"}])
        self.assertEqual(spec["somethingDomainSpecific"], {"anything": True})

    def test_the_base_records_which_provider_augmented_the_spec(self) -> None:
        spec = {}
        merge_spec_augmentation(spec, artifact(specSections={"componentTree": []}), domain_id="testdomain")
        self.assertEqual(spec["specAugmentation"]["provider"], "testdomain")
        self.assertEqual(spec["specAugmentation"]["version"], "1.0.0")
        self.assertEqual(spec["preSpecAssessment"]["objectClass"]["domain"], "testdomain")


if __name__ == "__main__":
    unittest.main()
