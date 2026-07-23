#!/usr/bin/env python3
"""Tests for Plan 1.3 Workstream J (property auto-binding) + §4.6 intake-correctness."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage1_intake"))

from bind_detail_properties import bind  # noqa: E402
from check_intake_correctness import decide, expose_assumptions  # noqa: E402


class PropertyBindingTest(unittest.TestCase):
    def test_glossy_binds_clearcoat(self):
        b = bind("glossy lacquered paint")
        self.assertEqual(b["materialProperties"].get("clearcoat"), 1.0)
        self.assertIn("clearcoatRoughness", b["materialProperties"])

    def test_gem_binds_transmission_ior(self):
        b = bind("translucent jade blade")
        self.assertGreater(b["materialProperties"].get("transmission", 0), 0)
        self.assertIn("ior", b["materialProperties"])
        self.assertTrue(b["materialProperties"].get("requiresEnvMap"))

    def test_brushed_metal_binds_anisotropy(self):
        b = bind("brushed steel guard")
        self.assertIn("anisotropy", b["materialProperties"])
        self.assertEqual(b["materialProperties"].get("metalness"), 1.0)

    def test_screws_hint_instancing(self):
        b = bind("row of small screws and rivets")
        self.assertEqual(b["primitiveHint"], "instanced-cluster")

    def test_logo_hint_decal(self):
        b = bind("engraved GERBER wordmark logo")
        self.assertEqual(b["primitiveHint"], "decal")

    def test_fabric_binds_sheen(self):
        b = bind("velvet cloth grip")
        self.assertIn("sheen", b["materialProperties"])

    def test_unmatched_is_empty_but_safe(self):
        b = bind("a nondescript thing")
        self.assertFalse(b["bound"])
        self.assertEqual(b["materialProperties"], {})
        self.assertIsNone(b["primitiveHint"])


class IntakeCorrectnessTest(unittest.TestCase):
    KNIFE = {"preSpecAssessment": {"objectClass": {
        "primaryType": "karambit knife", "primaryDomain": "object",
        "materialFamilies": ["jade", "polymer"]}}}

    def test_exposes_assumptions(self):
        a = expose_assumptions(self.KNIFE)
        self.assertEqual(a["primaryType"], "karambit knife")
        self.assertEqual(a["primaryDomain"], "object")

    def test_no_verdict_proceeds_but_defers(self):
        r = decide(self.KNIFE, None)
        self.assertEqual(r["action"], "proceed")
        self.assertFalse(r["confirmed"])
        self.assertIn("DEFERRED", r["reason"])

    def test_confident_contradiction_halts(self):
        verdict = {"matchesDeclaredClass": False, "confidence": 0.82, "detectedClass": "spoon"}
        r = decide(self.KNIFE, verdict)
        self.assertEqual(r["action"], "halt")
        self.assertFalse(r["confirmed"])

    def test_low_confidence_contradiction_does_not_halt(self):
        verdict = {"matchesDeclaredClass": False, "confidence": 0.3, "detectedClass": "spoon"}
        r = decide(self.KNIFE, verdict)
        self.assertEqual(r["action"], "proceed")

    def test_match_confirms_and_proceeds(self):
        verdict = {"matchesDeclaredClass": True, "confidence": 0.9, "detectedClass": "knife"}
        r = decide(self.KNIFE, verdict)
        self.assertEqual(r["action"], "proceed")
        self.assertTrue(r["confirmed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
