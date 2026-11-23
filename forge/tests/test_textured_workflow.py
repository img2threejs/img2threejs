"""The `textured` checklist profile: row order, generation loop, next.py behaviour, default selection,
decision rules, generate-record rules and delivery. Stdlib only; no bpy, no network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textured_support import ROOT, run_tool, write_png  # noqa: E402

sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "textured"))
import _common as C  # noqa: E402
import decide as D  # noqa: E402
import deliver as V  # noqa: E402
import textured_profile as TP  # noqa: E402
from workflow_state import (  # noqa: E402
    WorkflowStateError, load_state, mark_steps, new_state, save_state, status_payload, textured_apply_decision,
)

ORDER = ["reference-admission", "generate", "inspect", "normalize", "render", "likeness", "decide", "deliver"]


def fake_likeness(ws, generation, verdict, mean, *, jev=True, flagged=()):
    gen = C.gen_dir(ws, generation)
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "likeness.json").write_text(json.dumps({
        "decider": "jev-1.14.0" if jev else "model:m", "presentedAsJev": jev,
        "summary": {"overall": {"verdict": verdict, "probabilities": {"same": 0.5, "similar": 0.4, "different": 0.1}},
                    "meanScore": mean, "scores": {"a": mean}, "defects": {d: 0.8 for d in flagged},
                    "flaggedDefects": sorted(flagged), "defectFlagThreshold": 0.5}}))


def advance_to_decide(state):
    for step in ORDER[:ORDER.index("decide")]:
        mark_steps(state, [step], status="done", evidence=["e"])


class ChecklistTest(unittest.TestCase):
    def test_textured_rows_are_in_the_specified_order_and_gate_each_other(self):
        state = new_state("ref.png", profile="textured")
        self.assertEqual([r["id"] for r in state["checklist"]], ORDER)
        self.assertEqual(state["profile"], "textured")
        self.assertEqual(status_payload(state)["currentStep"], "reference-admission")
        with self.assertRaises(WorkflowStateError):
            mark_steps(state, ["generate"], status="done", evidence=["e"])
        for step in ORDER[:6]:
            mark_steps(state, [step], status="done", evidence=["e"])
        self.assertEqual(state["currentStep"], "decide")
        self.assertNotIn("await-pass-transition", json.dumps(status_payload(state)))

    def test_commands_name_the_current_generation_and_the_agent_step(self):
        state = new_state("my ref.png", profile="textured")
        self.assertIn("'my ref.png'", status_payload(state)["nextCommand"])
        mark_steps(state, ["reference-admission"], status="done", evidence=["e"])
        command = status_payload(state)["nextCommand"]
        self.assertIn("AGENT STEP (generation 1)", command)
        self.assertIn("mcp__tripo_make", command)
        self.assertIn("generate.py record", command)

    def test_decide_cannot_be_marked_by_hand(self):
        state = new_state("ref.png", profile="textured")
        advance_to_decide(state)
        with self.assertRaises(WorkflowStateError):
            mark_steps(state, ["decide"], status="done", evidence=["e"])
        with self.assertRaises(WorkflowStateError):
            mark_steps(state, ["decide"], status="skipped", reason="no")

    def test_accept_moves_to_deliver_and_completes(self):
        state = new_state("ref.png", profile="textured")
        advance_to_decide(state)
        textured_apply_decision(state, outcome="accept", generation=1, evidence=["l.json"], reason="same")
        self.assertEqual(state["currentStep"], "deliver")
        self.assertEqual(state["textured"]["accepted"], 1)
        mark_steps(state, ["deliver"], status="done", evidence=["final"])
        self.assertEqual((state["status"], state["currentStep"]), ("complete", "complete"))

    def test_regenerate_reopens_generation_rows_up_to_the_cap_then_refuses(self):
        state = new_state("ref.png", profile="textured", max_generations=2)
        advance_to_decide(state)
        textured_apply_decision(state, outcome="regenerate", generation=1, evidence=["l"], reason="different")
        self.assertEqual((state["currentStep"], state["textured"]["generation"]), ("generate", 2))
        self.assertEqual(state["checklist"][0]["status"], "done")          # admission is per run
        self.assertIn("generation 2", status_payload(state)["nextCommand"])
        self.assertEqual(len(state["textured"]["history"]), 1)
        advance_to_decide(state) if False else None
        for step in ORDER[1:6]:
            mark_steps(state, [step], status="done", evidence=["e"])
        with self.assertRaises(WorkflowStateError) as ctx:
            textured_apply_decision(state, outcome="regenerate", generation=2, evidence=["l"])
        self.assertIn("cap", str(ctx.exception))
        with self.assertRaises(WorkflowStateError):
            textured_apply_decision(state, outcome="accept", generation=1, evidence=["l"])  # stale generation
        textured_apply_decision(state, outcome="accept", generation=2, evidence=["l"], accepted_generation=1)
        self.assertEqual(state["textured"]["accepted"], 1)

    def test_state_round_trips_and_validation_rejects_a_corrupt_block(self):
        state = new_state("ref.png", profile="textured")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            save_state(path, state)
            self.assertEqual(load_state(path)["textured"]["maxGenerations"], 4)
            state["textured"]["generation"] = 9
            with self.assertRaises(WorkflowStateError):
                save_state(path, state)

    def test_max_generations_and_domain_are_validated(self):
        for bad in (0, 9, True):
            with self.assertRaises(WorkflowStateError):
                new_state("r.png", profile="textured", max_generations=bad)
        with self.assertRaises(WorkflowStateError):
            new_state("r.png", profile="textured", domain_id="no-such-domain")
        with self.assertRaises(WorkflowStateError):
            new_state("r.png", profile="generic", domain_id="character")
        self.assertEqual(new_state("r.png", profile="textured", domain_id="character")["domain"], "character")

    def test_procedural_prefix_selects_the_procedural_pipelines_unchanged(self):
        explicit = new_state("r.png", profile="procedural-generic")
        plain = new_state("r.png", profile="generic")
        self.assertEqual(explicit["checklist"], plain["checklist"])
        self.assertEqual(explicit["profile"], "generic")
        self.assertEqual(new_state("r.png", profile="procedural-character")["profile"], "character")
        self.assertEqual(status_payload(explicit)["currentStep"], "image-analysis")


class DefaultProfileTest(unittest.TestCase):
    def test_default_is_textured_only_when_a_generator_tool_is_configured(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home}
            self.assertFalse(TP.generator_available(env)["available"])
            self.assertEqual(TP.default_profile(env), "generic")
            (Path(home) / ".omp" / "agent").mkdir(parents=True)
            (Path(home) / ".omp" / "agent" / "mcp.json").write_text(json.dumps({"mcpServers": {"tripo": {"command": "x"}}}))
            self.assertEqual(TP.generator_available(env)["via"], str(Path(home) / ".omp" / "agent" / "mcp.json"))
            self.assertEqual(TP.default_profile(env), "textured")
            self.assertEqual(TP.default_profile({**env, "IMG2_TEXTURED_GENERATOR": "off"}), "generic")
            self.assertEqual(TP.default_profile({"HOME": home + "/none", "IMG2_TEXTURED_GENERATOR": "tripo"}), "textured")

    def test_toml_and_other_json_shapes_are_recognised(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".codex").mkdir()
            (Path(home) / ".codex" / "config.toml").write_text('[mcp_servers.tripo]\ncommand = "x"\n')
            self.assertTrue(TP.generator_available({"HOME": home})["available"])
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".claude.json").write_text(json.dumps({"mcpServers": {"nano-brain": {}}}))
            self.assertFalse(TP.generator_available({"HOME": home})["available"])

    def run_init(self, home, *extra):
        with tempfile.TemporaryDirectory() as work:
            env = {**os.environ, "HOME": home, "IMG2_HOME": home + "/img2", "IMG2_TEXTURED_GENERATOR": ""}
            env.pop("IMG2_TEXTURED_GENERATOR")
            done = subprocess.run([sys.executable, str(ROOT / "forge/state.py"), "init", "--state", "s.json",
                                   "--reference", "ref.png", *extra], capture_output=True, text=True, cwd=work, env=env)
            return done, (json.loads(Path(work, "s.json").read_text()) if done.returncode == 0 else None)

    def test_state_py_defaults_follow_the_generator_and_honour_the_explicit_opt_out(self):
        with tempfile.TemporaryDirectory() as home:
            done, state = self.run_init(home)
            self.assertEqual((done.returncode, state["profile"]), (0, "generic"))
            self.assertIn("textured profile unavailable", done.stderr)
            Path(home, ".omp/agent").mkdir(parents=True)
            Path(home, ".omp/agent/mcp.json").write_text(json.dumps({"mcpServers": {"tripo": {}}}))
            done, state = self.run_init(home)
            self.assertEqual((done.returncode, state["profile"]), (0, "textured"))
            done, state = self.run_init(home, "--profile", "procedural-generic")
            self.assertEqual((done.returncode, state["profile"]), (0, "generic"))
            done, state = self.run_init(home, "--profile", "generic")
            self.assertEqual(state["checklist"][0]["id"], "image-analysis")
            done, state = self.run_init(home, "--domain", "character")
            self.assertEqual((state["profile"], state["domain"]), ("textured", "character"))


class NextCliTest(unittest.TestCase):
    def test_next_reports_the_textured_checklist_without_a_spec_or_pass_machinery(self):
        with tempfile.TemporaryDirectory() as work:
            state = new_state("ref.png", profile="textured")
            save_state(Path(work, "state.json"), state)
            done = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", "state.json"],
                                  capture_output=True, text=True, cwd=work)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("LOCAL_STATE status=active step=reference-admission", done.stdout)
            self.assertIn("textured: generation 1/4", done.stdout)
            for step in ORDER:
                self.assertIn(f"- {step}", done.stdout)
            after = json.loads(Path(work, "state.json").read_text())
            self.assertEqual(after["loops"]["total"], 0)   # next.py never recomputes loop counts for textured
            spec = Path(work, "spec.json")
            spec.write_text("{}")
            refused = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", "state.json", str(spec)],
                                     capture_output=True, text=True, cwd=work)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("no sculpt spec", refused.stderr)


class GenerateRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name).resolve()
        self.ref = write_png(self.ws / "ref.png", 8, 8, (1, 2, 3))
        self.other = write_png(self.ws / "ref2.png", 8, 8, (9, 9, 9))
        (self.ws / "out").mkdir()
        (self.ws / "out" / "model.fbx").write_bytes(b"fbx")
        (self.ws / "out" / "tex.png").write_bytes(b"png")

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, gen, *, model="tripo-v3.1", scenario="film", inp=None, credits="65", extra=()):
        return run_tool("generate.py", "record", "--workspace", str(self.ws), "--gen", str(gen), "--input", str(inp or self.ref),
                        "--task-id", f"task{gen}", "--model", model, "--scenario", scenario, "--credits", credits,
                        "--model-file", str(self.ws / "out" / "model.fbx"), "--model-dir", str(self.ws / "out"), *extra)

    def test_provenance_is_recorded_and_textures_travel_with_the_model(self):
        done = self.record(1)
        self.assertEqual(done.returncode, 0, done.stderr)
        receipt = json.loads((C.gen_dir(self.ws, 1) / "generate.json").read_text())
        self.assertEqual(receipt["generator"]["taskId"], "task1")
        self.assertEqual(receipt["generator"]["credits"], 65)
        self.assertEqual(receipt["asset"]["path"], "raw/model.fbx")
        self.assertTrue((C.gen_dir(self.ws, 1) / "raw" / "tex.png").is_file())
        self.assertEqual(len(receipt["input"]["sha256"]), 64)

    def test_an_unchanged_regeneration_is_refused_and_a_changed_one_accepted_with_running_credits(self):
        self.assertEqual(self.record(1).returncode, 0)
        repeat = self.record(2)
        self.assertEqual(repeat.returncode, 2)
        self.assertIn("repeats generation 1", repeat.stderr)
        changed = self.record(2, model="tripo-p1", credits="60")
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(json.loads((C.gen_dir(self.ws, 2) / "generate.json").read_text())["creditsTotal"], 125)
        self.assertEqual(self.record(3, inp=self.other).returncode, 0)

    def test_order_cap_overwrite_and_input_checks(self):
        self.assertIn("next one is 1", self.record(2).stderr)
        self.assertEqual(self.record(1).returncode, 0)
        self.assertIn("already exists", self.record(1, model="tripo-p2").stderr)
        self.assertIn("exceeds the cap of 2", self.record(3, model="tripo-p2", extra=("--max-generations", "2")).stderr)
        self.assertEqual(self.record(2, credits="-1", model="x").returncode, 2)


class DecisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def state_at(self, generation, cap=4):
        state = new_state("r.png", profile="textured", max_generations=cap)
        state["textured"]["generation"] = generation
        return state

    def test_same_accepts_different_regenerates_similar_depends_on_mean_and_defects(self):
        cases = [("same", 1.0, (), "accept"), ("different", 3.9, (), "regenerate"), ("similar", 2.5, (), "accept"),
                 ("similar", 2.0, (), "regenerate"), ("similar", 3.0, ("melted",), "regenerate")]
        for verdict, mean, flagged, expected in cases:
            fake_likeness(self.ws, 1, verdict, mean, flagged=flagged)
            outcome, chosen, reason, evidence = D.decide(self.ws, self.state_at(1))
            self.assertEqual((outcome, chosen), (expected, 1), (verdict, mean, flagged, reason))

    def test_at_the_cap_the_best_generation_by_jev_wins_and_a_pick_breaks_ties(self):
        for gen, mean in ((1, 2.1), (2, 2.35), (3, 2.3), (4, 1.2)):
            fake_likeness(self.ws, gen, "different" if gen == 4 else "similar", mean, flagged=("x",))
        outcome, chosen, reason, evidence = D.decide(self.ws, self.state_at(4))
        self.assertEqual((outcome, chosen), ("accept", 2))                    # gen 2 and 3 tie within 0.15; higher mean wins
        self.assertIn("jev-mean", reason)
        (self.ws / ".img2threejs/textured").mkdir(parents=True, exist_ok=True)
        (self.ws / ".img2threejs/textured/pick.json").write_text(json.dumps({"winner": 3}))
        self.assertEqual(D.decide(self.ws, self.state_at(4))[1], 3)

    def test_jev_scored_generations_outrank_model_answered_ones(self):
        fake_likeness(self.ws, 1, "similar", 1.9, jev=True, flagged=("x",))
        fake_likeness(self.ws, 2, "similar", 3.9, jev=False, flagged=("x",))
        self.assertEqual(D.best_generation(self.ws, [1, 2]), (1, "jev-mean"))
        fake_likeness(self.ws, 1, "similar", 1.9, jev=False, flagged=("x",))
        self.assertEqual(D.best_generation(self.ws, [1, 2]), (2, "model-mean"))

    def test_missing_likeness_blocks_the_decision(self):
        with self.assertRaises(C.TexturedError):
            D.decide(self.ws, self.state_at(1))

    def test_decide_cli_records_the_outcome_in_the_state_file(self):
        state = new_state("r.png", profile="textured", max_generations=2)
        advance_to_decide(state)
        save_state(self.ws / ".img2threejs/state.json", state)
        fake_likeness(self.ws, 1, "different", 1.0)
        done = run_tool("decide.py", "--workspace", str(self.ws))
        self.assertEqual(done.returncode, 0, done.stderr)
        after = load_state(self.ws / ".img2threejs/state.json")
        self.assertEqual((after["textured"]["generation"], after["currentStep"]), (2, "generate"))
        self.assertIn("regenerate", done.stdout)


class DeliverTest(unittest.TestCase):
    def test_delivery_assembles_files_and_a_report_that_does_not_call_a_model_answer_jev(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d).resolve()
            gen = C.gen_dir(ws, 1)
            (gen / "renders").mkdir(parents=True)
            write_png(gen / "renders" / "front.png", 16, 24, (1, 1, 1))
            write_png(gen / "sheet-front.png", 280, 210, (2, 2, 2))
            ref = write_png(ws / "ref.png", 16, 24, (3, 3, 3))
            (gen / "normalized.glb").write_bytes(b"glTF-bytes")
            C.write_json(gen / "generate.json", {"generator": {"tool": "mcp__tripo_make", "model": "tripo-v3.1", "scenario": "film",
                         "prompt": "", "taskId": "T1", "credits": 65, "then": ""},
                         "asset": {"path": "raw/m.fbx", "format": "fbx", "bytes": 3}, "input": {"sha256": "a" * 64}})
            C.write_json(gen / "inspect.json", {"facts": {"bpy": "4.5", "triangles": 10, "vertices": 8, "meshes": 1, "materialCount": 1,
                         "textureCount": 1, "uvSets": ["UVMap"], "textures": [{"name": "Color", "width": 64, "height": 64}],
                         "islands": {"count": 1}, "nonManifoldEdges": {"raw": 5, "welded": 1},
                         "skeleton": {"bones": 0, "actions": 0}}})
            C.write_json(gen / "normalize.json", {"normalization": {"bbox": {"size": [0.5, 0.4, 1.8]}, "speckRemoval": {"removedIslands": 2}},
                         "export": {"bytes": 10, "sha256": "b" * 64}})
            C.write_json(gen / "render.json", {"views": [{"path": ".img2threejs/textured/gen-1/renders/front.png"}]})
            fake_likeness(ws, 1, "similar", 2.4, jev=False)
            record = json.loads((gen / "likeness.json").read_text())
            record["summary"].update(scores={"silhouette": 2.4}, defects={"melted": 0.2})
            (gen / "likeness.json").write_text(json.dumps(record))
            done = run_tool("deliver.py", "--workspace", str(ws), "--gen", "1", "--reference", str(ref))
            self.assertEqual(done.returncode, 0, done.stderr)
            final = ws / ".img2threejs/textured/final"
            for name in ("model.glb", "comparison.png", "REPORT.md", "renders/front.png", "reference.png", "delivery.json"):
                self.assertTrue((final / name).is_file(), name)
            report = (final / "REPORT.md").read_text()
            self.assertIn("NOT by Jev", report)
            self.assertIn("What is NOT done", report)
            self.assertIn("T1", report)
            incomplete = run_tool("deliver.py", "--workspace", str(ws), "--gen", "2", "--reference", str(ref))
            self.assertEqual(incomplete.returncode, 2)
            self.assertIn("incomplete", incomplete.stderr)


if __name__ == "__main__":
    unittest.main()
