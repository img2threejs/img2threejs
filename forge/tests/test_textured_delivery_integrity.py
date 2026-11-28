"""Defensive hash-chain, containment and atomic-delivery counterexamples."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "_shared")]
sys.path.append(str(HERE.parent / "textured"))
import _common as C
import decide as D
import deliver as V
from textured_chain_support import pipeline, record_model


class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name).resolve()

    def deliver(self, reference, *extra):
        return V.main(["--workspace", str(self.workspace), "--reference", str(reference), *extra])

    def previous_final(self):
        final = C.textured_root(self.workspace) / "final"
        final.mkdir()
        (final / "previous.txt").write_bytes(b"previous successful delivery")
        return final

    def test_unchanged_accepted_pipeline_delivers_exact_bytes_and_honest_provenance(self):
        directory, reference, _ = pipeline(self.workspace)
        record_model(self, self.workspace)
        D.main(["--workspace", str(self.workspace)])
        self.assertEqual(self.deliver(reference), 0)
        final = C.textured_root(self.workspace) / "final"
        self.assertEqual((final / "model.glb").read_bytes(), (directory / "normalized.glb").read_bytes())
        self.assertEqual((final / "comparison.png").read_bytes(), (directory / "sheet-front.png").read_bytes())
        self.assertIn("NOT by Jev", (final / "REPORT.md").read_text())

    def test_judged_likeness_is_immutable_even_if_a_new_answer_is_valid(self):
        import likeness
        directory, _, _ = pipeline(self.workspace)
        path = directory / "likeness.json"
        before = path.read_bytes()
        questions = likeness.build_questions("generic")
        answers = {key: ("different" if row["type"] == "choice" else 1 if row["type"] == "score" else 0.8)
                   for key, row in questions.items()}
        with self.assertRaises(C.TexturedError):
            likeness.write_likeness(self.workspace, 1, "generic", "jev-1.14.0", "jev",
                directory / "sheet-front.png", questions, likeness.normalize_answers(questions, answers))
        self.assertEqual(path.read_bytes(), before)

    def test_valid_model_answers_without_live_user_authority_cannot_be_recorded(self):
        import likeness
        from types import SimpleNamespace
        from decision_control import DecisionError
        import route_no_account
        directory, reference, _ = pipeline(self.workspace)
        (directory / "likeness.json").unlink()
        questions = likeness.build_questions("generic")
        answers = {key: ("same" if row["type"] == "choice" else 3 if row["type"] == "score" else 0.1)
                   for key, row in questions.items()}
        answer_path = self.workspace / "answers.json"
        C.write_json(answer_path, answers)
        args = SimpleNamespace(model_id="fixture", gen=1, kind="generic", view="front", feature=[],
            answers=str(answer_path), run_id="unattested", reference=str(reference))
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"IMG2_HOME": str(Path(home).resolve())}), \
                patch.object(route_no_account, "credential_status", return_value="credential-missing"):
            with self.assertRaises((DecisionError, C.TexturedError)):
                likeness.cmd_record_model(args, self.workspace)
        self.assertFalse((directory / "likeness.json").exists())

    def test_render_basenames_cannot_collide_on_case_insensitive_delivery_filesystems(self):
        import _chain
        directory, _, _ = pipeline(self.workspace)
        rows = []
        for folder, name in (("left", "Front.png"), ("right", "front.png")):
            target = directory / "renders" / folder / name
            target.parent.mkdir()
            target.write_bytes((directory / "renders/front.png").read_bytes())
            rows.append({"path": C.workspace_relative(self.workspace, target), "sha256": C.sha256_file(target)})
        with self.assertRaises(C.TexturedError):
            _chain.render_sources(self.workspace, 1, {"views": rows})

    def test_changed_pipeline_inputs_cannot_be_accepted(self):
        for changed in ("raw/model.fbx", "normalized.glb", "renders/front.png", "sheet-front.png", "inspect.json"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory(dir=self.workspace) as owned:
                directory, reference, _ = pipeline(Path(owned))
                target = directory / changed
                target.write_bytes(target.read_bytes() + b"owned change")
                state = Path(owned) / ".img2threejs/state.json"
                before = state.read_bytes()
                with self.assertRaises(C.TexturedError):
                    D.main(["--workspace", owned])
                self.assertEqual(state.read_bytes(), before)

    def test_changed_reference_or_accepted_receipt_cannot_be_delivered(self):
        for changed in ("reference", "generate.json", "likeness.json"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory(dir=self.workspace) as owned:
                workspace = Path(owned)
                directory, reference, _ = pipeline(workspace, accepted=True)
                target = reference if changed == "reference" else directory / changed
                if target.suffix == ".json":
                    record = C.read_json(target)
                    record["ownedChange"] = True
                    C.write_json(target, record)
                else:
                    target.write_bytes(target.read_bytes() + b"owned change")
                with self.assertRaises(C.TexturedError):
                    V.main(["--workspace", owned, "--reference", str(reference)])
                self.assertFalse((C.textured_root(workspace) / "final").exists())

    def test_explicit_generation_never_bypasses_the_accepted_generation(self):
        _, reference, _ = pipeline(self.workspace)
        with self.assertRaises(C.TexturedError):
            self.deliver(reference, "--gen", "1")
        self.assertFalse((C.textured_root(self.workspace) / "final").exists())

    def test_render_paths_outside_generation_and_basename_collisions_are_refused(self):
        directory, reference, _ = pipeline(self.workspace, accepted=True)
        outside = self.workspace / "owned-outside.png"
        outside.write_bytes((directory / "renders/front.png").read_bytes())
        original = C.read_json(directory / "render.json")
        for rows in ([{"path": str(outside), "sha256": C.sha256_file(outside)}],
                     [*original["views"], *original["views"]]):
            C.write_json(directory / "render.json", {**original, "views": rows})
            with self.assertRaises(C.TexturedError):
                self.deliver(reference)
            self.assertFalse((C.textured_root(self.workspace) / "final").exists())
        self.assertTrue(outside.is_file())

    def test_preflight_and_staging_failure_preserve_the_previous_delivery(self):
        _, reference, _ = pipeline(self.workspace, accepted=True)
        final = self.previous_final()
        with self.assertRaises(C.TexturedError):
            self.deliver(self.workspace / "missing-reference.png")
        self.assertEqual((final / "previous.txt").read_bytes(), b"previous successful delivery")
        with patch.object(V, "write_report", side_effect=OSError("owned simulated report failure")):
            with self.assertRaises(OSError):
                self.deliver(reference)
        self.assertEqual((final / "previous.txt").read_bytes(), b"previous successful delivery")

    def test_activation_failure_restores_previous_delivery_and_cleans_staging(self):
        _, reference, _ = pipeline(self.workspace, accepted=True)
        final = self.previous_final()
        replace = os.replace
        def fail_activation(source, target):
            if Path(source).name.startswith(".delivery-stage-") and Path(target) == final:
                raise OSError("owned simulated activation failure")
            return replace(source, target)
        with patch.object(V.os, "replace", side_effect=fail_activation):
            with self.assertRaises(OSError):
                self.deliver(reference)
        self.assertEqual((final / "previous.txt").read_bytes(), b"previous successful delivery")
        self.assertEqual(sorted(p.name for p in final.parent.iterdir()), ["final", "gen-1"])


if __name__ == "__main__":
    unittest.main()
