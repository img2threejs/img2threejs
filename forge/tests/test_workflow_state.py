from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))

from workflow_state import (  # noqa: E402
    WorkflowStateError,
    is_stale_complete,
    mark_steps,
    new_state,
    reset_for_resume,
    set_current_pass,
    status_payload,
    sync_from_spec,
)


class WorkflowStateTest(unittest.TestCase):
    def test_generic_state_starts_with_mandatory_image_analysis(self):
        state = new_state("reference.png")
        payload = status_payload(state)
        self.assertEqual(payload["currentStep"], "image-analysis")
        self.assertEqual(payload["loop"]["passCount"], 0)
        self.assertEqual(payload["loop"]["maxPerPass"], 3)
        self.assertIn("part-coverage", payload["pending"])
        ids = [entry["id"] for entry in state["checklist"]]
        self.assertLess(ids.index("reference-suitability"), ids.index("reference-admission"))
        self.assertLess(ids.index("detail-inventory"), ids.index("projection-route"))
        self.assertLess(ids.index("projection-route"), ids.index("spec-authoring"))
        self.assertLess(ids.index("material-evidence"), ids.index("strict-validation"))

    def test_material_reference_wiring_is_in_the_setup_checklist(self):
        """The material track is executable, not prose.

        Both steps were dropped once already: merge 7214452 resolved this file to a revision
        whose `material-evidence` step only *described* running a script, and which had no
        wiring step at all, so `material_region_analysis.py` and `apply_material_analysis.py`
        became unreachable from the checklist that drives the pipeline. This test is what makes
        that regression fail loudly instead of silently.
        """
        state = new_state("reference.png")
        by_id = {entry["id"]: entry for entry in state["checklist"]}
        self.assertIn("material-evidence", by_id)
        self.assertIn("material-spec-wiring", by_id)
        self.assertIn("material_region_analysis.py", by_id["material-evidence"]["command"])
        self.assertIn("apply_material_analysis.py", by_id["material-spec-wiring"]["command"])
        ids = [entry["id"] for entry in state["checklist"]]
        self.assertLess(ids.index("spec-authoring"), ids.index("material-evidence"))
        self.assertLess(ids.index("material-evidence"), ids.index("material-spec-wiring"))
        self.assertLess(ids.index("material-spec-wiring"), ids.index("strict-validation"))

    def test_cs2_state_includes_classification_and_manifest_before_pre_spec(self):
        state = new_state("knife.png", profile="cs2")
        ids = [entry["id"] for entry in state["checklist"]]
        self.assertLess(ids.index("cs2-contract-read"), ids.index("cs2-authoritative-classification"))
        self.assertLess(ids.index("cs2-authoritative-classification"), ids.index("pre-spec-assessment"))
        self.assertLess(ids.index("cs2-manifest"), ids.index("pre-spec-assessment"))
        self.assertLess(ids.index("pass-gate-check"), ids.index("cs2-review"))
        self.assertLess(ids.index("cs2-review"), ids.index("ai-review-recorded"))

    def test_character_state_requires_contract_landmarks_and_route_decision(self):
        state = new_state("character.png", profile="character")
        ids = [entry["id"] for entry in state["checklist"]]
        self.assertLess(ids.index("character-contract-read"), ids.index("character-landmarks"))
        self.assertLess(ids.index("character-landmarks"), ids.index("pre-spec-assessment"))
        self.assertLess(ids.index("character-landmarks"), ids.index("projection-route"))

    def test_pass_commands_follow_executable_gate_order(self):
        state = new_state("reference.png", spec="spec.json")
        entries = [entry for entry in state["checklist"] if entry["scope"] == "pass"]
        ids = [entry["id"] for entry in entries]
        commands = {entry["id"]: entry["command"] for entry in entries}
        self.assertIn("generate_threejs_factory.py", commands["build-current-pass"])
        self.assertLess(ids.index("build-current-pass"), ids.index("tier1-diagnostics"))
        self.assertLess(ids.index("tier1-diagnostics"), ids.index("pass-gate-check"))
        self.assertLess(ids.index("pass-gate-check"), ids.index("ai-review-recorded"))

    def test_skipping_a_mandatory_step_requires_reason(self):
        state = new_state("reference.png")
        with self.assertRaises(WorkflowStateError):
            mark_steps(state, ["image-analysis"], status="skipped")
        mark_steps(state, ["image-analysis"], status="skipped", reason="analysis supplied externally")
        entry = next(item for item in state["checklist"] if item["id"] == "image-analysis")
        self.assertEqual(entry["status"], "skipped")

    def test_completing_a_mandatory_step_requires_evidence(self):
        state = new_state("reference.png")
        with self.assertRaises(WorkflowStateError):
            mark_steps(state, ["image-analysis"], status="done")
        mark_steps(state, ["image-analysis"], status="done", evidence=["analysis.json"])
        entry = next(item for item in state["checklist"] if item["id"] == "image-analysis")
        self.assertEqual(entry["evidence"], ["analysis.json"])

    def test_new_pass_archives_and_resets_pass_checklist(self):
        state = new_state("reference.png")
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        mark_steps(state, setup_ids, status="done", evidence=["setup-evidence.json"])
        set_current_pass(state, "blockout")
        mark_steps(
            state,
            ["build-current-pass", "render-capture", "review-contract-read", "tier1-diagnostics"],
            status="done",
            evidence=["shot.png"],
        )
        set_current_pass(state, "structural-pass")
        self.assertEqual(state["passHistory"][0]["passId"], "blockout")
        pass_entries = [entry for entry in state["checklist"] if entry["scope"] == "pass"]
        self.assertTrue(all(entry["status"] == "pending" for entry in pass_entries))

    def test_checklist_cannot_be_completed_out_of_order(self):
        state = new_state("reference.png")
        with self.assertRaisesRegex(WorkflowStateError, "out-of-order"):
            mark_steps(state, ["strict-validation"], status="done", evidence=["spec.json"])

    def test_refine_review_resets_same_pass_checklist_once(self):
        state = new_state("reference.png")
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        pass_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "pass"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        mark_steps(state, pass_ids, status="done", evidence=["review.json"])
        self.assertEqual(status_payload(state)["currentStep"], "await-pass-transition")

        spec = {"reviewHistory": [{"passId": "blockout", "action": "refine-code"}]}
        sync_from_spec(state, spec, "blockout")
        self.assertEqual(status_payload(state)["currentStep"], "build-current-pass")
        self.assertIn("do not regenerate", status_payload(state)["nextCommand"])
        self.assertTrue(
            all(entry["status"] == "pending" for entry in state["checklist"] if entry["scope"] == "pass")
        )
        archived = len(state["passHistory"])
        sync_from_spec(state, spec, "blockout")
        self.assertEqual(len(state["passHistory"]), archived)

    def test_new_pass_and_refine_spec_regenerate_with_force(self):
        state = new_state("reference.png", spec="spec.json")
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        self.assertNotIn("--force", status_payload(state)["nextCommand"])
        set_current_pass(state, "structural-pass")
        self.assertIn("--force", status_payload(state)["nextCommand"])
        sync_from_spec(
            state,
            {"reviewHistory": [{"passId": "structural-pass", "action": "refine-spec"}]},
            "structural-pass",
        )
        self.assertIn("--force", status_payload(state)["nextCommand"])

    def test_per_pass_refine_limit_is_a_hard_stop(self):
        state = new_state("reference.png", max_per_pass=3, max_total=6)
        spec = {
            "reviewHistory": [
                {"passId": "blockout", "action": "refine-code"},
                {"passId": "blockout", "action": "refine-spec"},
                {"passId": "blockout", "action": "refine-code"},
            ]
        }
        sync_from_spec(state, spec, "blockout")
        payload = status_payload(state)
        self.assertEqual(payload["status"], "stopped")
        self.assertEqual(payload["loop"]["passCount"], 3)
        self.assertIn("max-correction-loops-reached", payload["stopReason"])
        self.assertIsNone(payload["nextCommand"])

    def test_total_refine_limit_is_a_hard_stop(self):
        state = new_state("reference.png", max_per_pass=4, max_total=6)
        spec = {
            "reviewHistory": [
                {"passId": "blockout", "action": "refine-code"},
                {"passId": "blockout", "action": "refine-spec"},
                {"passId": "structural-pass", "action": "refine-code"},
                {"passId": "structural-pass", "action": "refine-spec"},
                {"passId": "form-refinement", "action": "refine-code"},
                {"passId": "form-refinement", "action": "refine-spec"},
            ]
        }
        sync_from_spec(state, spec, "material-pass")
        payload = status_payload(state)
        self.assertEqual(payload["status"], "stopped")
        self.assertIn("max-total-correction-loops-reached", payload["stopReason"])

    def test_next_cli_reads_state_before_a_spec_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            init = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "forge" / "state.py"),
                    "init",
                    "--state",
                    str(state_path),
                    "--reference",
                    "reference.png",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            result = subprocess.run(
                [sys.executable, str(ROOT / "forge" / "next.py"), "--state", str(state_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("LOCAL_STATE status=active step=image-analysis", result.stdout)
            self.assertIn("pending mandatory steps", result.stdout)

    def test_next_cli_emits_only_state_ordered_build_command(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            spec_path = Path(directory) / "spec.json"
            state = new_state("reference.png", spec=str(spec_path))
            setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
            mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
            state_path.write_text(json.dumps(state), encoding="utf-8")
            spec_path.write_text(
                json.dumps({"buildPasses": [{"id": "blockout", "acceptance": []}], "reviewHistory": []}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "forge" / "next.py"), "--state", str(state_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("generate_threejs_factory.py", result.stdout)
            self.assertNotIn("orchestrate_passes.py check", result.stdout)

    def test_next_cli_rejects_a_spec_that_differs_from_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            stored_spec = Path(directory) / "stored.json"
            other_spec = Path(directory) / "other.json"
            state_path.write_text(
                json.dumps(new_state("reference.png", spec=str(stored_spec))),
                encoding="utf-8",
            )
            other_spec.write_text(json.dumps({"buildPasses": []}), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "forge" / "next.py"),
                    str(other_spec),
                    "--state",
                    str(state_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("does not match stored spec", result.stderr)

    def test_state_cli_does_not_expose_manual_pass_bypass(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "forge" / "state.py"), "set-pass", "complete"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_next_cli_returns_nonzero_at_loop_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            spec_path = Path(directory) / "spec.json"
            state = new_state("reference.png", spec=str(spec_path), max_per_pass=2, max_total=6)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            spec_path.write_text(
                json.dumps(
                    {
                        "buildPasses": [{"id": "blockout", "acceptance": []}],
                        "reviewHistory": [
                            {"passId": "blockout", "action": "refine-code"},
                            {"passId": "blockout", "action": "refine-spec"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "forge" / "next.py"), "--state", str(state_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("STOP: max-correction-loops-reached", result.stdout)

    def test_skill_router_keeps_mandatory_state_and_reference_gates_visible(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("forge/next.py --state .img2threejs/state.json", skill)
        self.assertIn("MUST read `grimoire/intake/cs2_intake_contract.md` completely", skill)
        self.assertIn("MUST read\n   `grimoire/review/gates_reference.md`", skill)
        self.assertIn("forge/stage4_review/diagnose_render.py", skill)
        self.assertIn("forge/stage4_review/diagnose_render_multi_angle.py", skill)
        self.assertIn("forge/stage4_review/check_part_coverage.py", skill)

    def test_reset_for_resume_resets_complete_state(self):
        state = new_state("reference.png", spec="spec.json")
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        pass_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "pass"]
        final_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "final"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        mark_steps(state, pass_ids, status="done", evidence=["blockout.json"])
        set_current_pass(state, "complete")
        mark_steps(state, final_ids, status="done", evidence=["final.json"])
        self.assertEqual(state["status"], "complete")
        reset_for_resume(state)
        self.assertEqual(state["status"], "active")
        self.assertNotEqual(state["currentStep"], "complete")

    def test_reset_for_resume_resets_stopped_state(self):
        state = new_state("reference.png", max_per_pass=2, max_total=6)
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        pass_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "pass"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        mark_steps(state, pass_ids, status="done", evidence=["blockout.json"])
        spec = {
            "reviewHistory": [
                {"passId": "blockout", "action": "refine-code"},
                {"passId": "blockout", "action": "refine-spec"},
            ]
        }
        sync_from_spec(state, spec, "blockout")
        self.assertEqual(state["status"], "stopped")
        reset_for_resume(state)
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["stopReason"], "")

    def test_reset_for_resume_is_noop_on_active(self):
        state = new_state("reference.png")
        self.assertEqual(state["status"], "active")
        reset_for_resume(state)
        self.assertEqual(state["status"], "active")

    def test_is_stale_complete_returns_false_for_active(self):
        state = new_state("reference.png")
        self.assertFalse(is_stale_complete(state))

    def test_is_stale_complete_returns_false_for_stopped(self):
        state = new_state("reference.png", max_per_pass=1, max_total=1)
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        spec = {"reviewHistory": [{"passId": "blockout", "action": "refine-code"}]}
        sync_from_spec(state, spec, "blockout")
        self.assertEqual(state["status"], "stopped")
        self.assertFalse(is_stale_complete(state))

    def test_is_stale_complete_returns_true_when_pass_not_complete(self):
        state = new_state("reference.png", spec="spec.json")
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        pass_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "pass"]
        mark_steps(state, pass_ids, status="done", evidence=["blockout.json"])
        # Manually set status to complete without updating currentPass
        state["status"] = "complete"
        state["currentStep"] = "complete"
        self.assertTrue(is_stale_complete(state))

    def test_status_payload_includes_stale_complete(self):
        state = new_state("reference.png", spec="spec.json")
        setup_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "setup"]
        mark_steps(state, setup_ids, status="done", evidence=["setup.json"])
        set_current_pass(state, "blockout")
        pass_ids = [entry["id"] for entry in state["checklist"] if entry["scope"] == "pass"]
        mark_steps(state, pass_ids, status="done", evidence=["blockout.json"])
        state["status"] = "complete"
        state["currentStep"] = "complete"
        payload = status_payload(state)
        self.assertTrue(payload["staleComplete"])

    def test_reset_cli_refuses_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = new_state("reference.png")
            state["status"] = "complete"
            state["currentStep"] = "complete"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "forge" / "state.py"), "reset", "--state", str(state_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("WARNING", result.stderr)
            self.assertIn("--force", result.stderr)

    def test_reset_cli_with_force_resets_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = new_state("reference.png")
            state["status"] = "complete"
            state["currentStep"] = "complete"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "forge" / "state.py"),
                    "reset",
                    "--state",
                    str(state_path),
                    "--force",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=active", result.stdout)

    def test_reset_cli_is_noop_on_active(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = new_state("reference.png")
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "forge" / "state.py"), "reset", "--state", str(state_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("already active", result.stderr)

    def test_all_direct_router_references_exist(self):
        for relative in (
            "grimoire/intake/cs2_intake_contract.md",
            "grimoire/intake/local_spec_search.md",
            "grimoire/review/gates_reference.md",
            "grimoire/review/self_correction.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
