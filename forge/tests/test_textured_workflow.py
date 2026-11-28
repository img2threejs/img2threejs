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
from textured_chain_support import pipeline, record_model  # noqa: E402
from workflow_state import (  # noqa: E402
    WorkflowStateError, load_state, mark_steps, new_state, save_state, status_payload, textured_apply_decision, validate_state,
)

ORDER = ["reference-admission", "generate", "inspect", "normalize", "render", "likeness", "decide", "deliver"]


def fake_likeness(ws, generation, verdict, mean, *, jev=True, flagged=()):
    gen = C.gen_dir(ws, generation)
    if not (gen / "generate.json").is_file():
        pipeline(ws, generation, write_state=False)
    record = C.read_json(gen / "likeness.json") if (gen / "likeness.json").is_file() else {}
    record.update(decider="jev-1.14.0" if jev else "model:m", provider="jev" if jev else "host-model",
                  presentedAsJev=jev, summary={
        "overall": {"verdict": verdict, "probabilities": {"same": 0.5, "similar": 0.4, "different": 0.1}},
        "meanScore": mean, "scores": {"a": mean}, "defects": {d: 0.8 for d in flagged},
        "flaggedDefects": sorted(flagged), "defectFlagThreshold": 0.5})
    C.write_json(gen / "likeness.json", record)


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

    def test_validation_rejects_a_max_generations_outside_1_to_8(self):
        for bad in (0, 9, 99, -1):
            state = new_state("ref.png", profile="textured")
            state["textured"]["maxGenerations"] = bad
            state["textured"]["generation"] = 1
            with self.subTest(cap=bad), self.assertRaises(WorkflowStateError):
                validate_state(state)

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
        (self.ws / ".img2threejs").mkdir()
        save_state(self.ws / ".img2threejs" / "state.json", new_state("r.png", profile="textured", max_generations=4))

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


class GenerationBudgetTest(unittest.TestCase):
    """The cap comes from the state; spending history is append-only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name).resolve()
        self.ref = write_png(self.ws / "ref.png", 8, 8, (1, 2, 3))
        (self.ws / "out").mkdir()
        (self.ws / "out" / "model.fbx").write_bytes(b"fbx")

    def tearDown(self):
        self.tmp.cleanup()

    def write_state(self, cap):
        (self.ws / ".img2threejs").mkdir(exist_ok=True)
        save_state(self.ws / ".img2threejs" / "state.json", new_state("r.png", profile="textured", max_generations=cap))

    def record(self, gen, *, scenario="film", credits="65", extra=()):
        return run_tool("generate.py", "record", "--workspace", str(self.ws), "--gen", str(gen), "--input", str(self.ref),
                        "--task-id", f"t{gen}-{scenario}", "--model", "tripo-p1", "--scenario", scenario, "--credits", credits,
                        "--model-file", str(self.ws / "out" / "model.fbx"), *extra)

    def total(self, gen):
        return json.loads((C.gen_dir(self.ws, gen) / "generate.json").read_text())["creditsTotal"]

    def test_max_generations_can_lower_the_state_cap_but_never_raise_it(self):
        self.write_state(2)
        self.assertEqual(self.record(1, scenario="a").returncode, 0)
        self.assertEqual(self.record(2, scenario="b").returncode, 0)
        raised = self.record(3, scenario="c", extra=("--max-generations", "9"))
        self.assertEqual(raised.returncode, 2)
        self.assertEqual(len(C.generation_receipts(self.ws)), 2)
        self.assertFalse((C.gen_dir(self.ws, 3) / "generate.json").exists())

    def edit_cap(self, value):
        path = self.ws / ".img2threejs" / "state.json"
        state = json.loads(path.read_text())
        state["textured"]["maxGenerations"] = value
        path.write_text(json.dumps(state))

    def test_the_cap_fails_closed_on_an_edited_out_of_range_or_non_int_value(self):
        self.write_state(2)
        for bad in (0, 99, 9, -1, "3", 2.5, True, None):
            self.edit_cap(bad)
            with self.subTest(cap=bad):
                refused = self.record(1, scenario="a")
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("generation cap", refused.stderr)
                self.assertFalse((C.gen_dir(self.ws, 1) / "generate.json").exists())

    def test_a_missing_unreadable_or_invalid_state_refuses_recording(self):
        self.write_state(2)
        self.assertEqual(self.record(1, scenario="a").returncode, 0)
        self.assertEqual(self.record(2, scenario="b").returncode, 0)
        for label, state_arg in (("missing", str(self.ws / "nowhere.json")), ("directory", str(self.ws / "out"))):
            with self.subTest(state=label):
                refused = self.record(3, scenario="c", extra=("--state", state_arg))
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("generation cap", refused.stderr)
        (self.ws / ".img2threejs" / "state.json").write_text("{not json")
        self.assertEqual(self.record(3, scenario="c").returncode, 2)
        (self.ws / ".img2threejs" / "state.json").unlink()
        self.assertEqual(self.record(3, scenario="c").returncode, 2)
        self.assertFalse((C.gen_dir(self.ws, 3) / "generate.json").exists())

    def test_a_state_with_the_default_cap_still_records(self):
        self.write_state(4)
        self.assertEqual(self.record(1, scenario="a", extra=("--max-generations", "3")).returncode, 0)

    def test_a_lower_override_applies(self):
        self.write_state(4)
        self.assertEqual(self.record(1, scenario="a").returncode, 0)
        lowered = self.record(2, scenario="b", extra=("--max-generations", "1"))
        self.assertEqual(lowered.returncode, 2)
        self.assertFalse((C.gen_dir(self.ws, 2) / "generate.json").exists())
        self.assertEqual(len(C.generation_receipts(self.ws)), 1)

    def test_overwrite_replaces_an_unjudged_record_and_keeps_every_superseded_receipt_in_the_total(self):
        self.write_state(4)
        self.assertEqual(self.record(1, scenario="a").returncode, 0)
        for index, scenario in enumerate(("b", "c", "d"), start=2):
            done = self.record(1, scenario=scenario, extra=("--overwrite",))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.total(1), 65 * index)
        archived = sorted(p.name for p in C.gen_dir(self.ws, 1).glob("superseded-*.json"))
        self.assertEqual(archived, ["superseded-1.json", "superseded-2.json", "superseded-3.json"])
        refused = self.record(2, scenario="e")
        self.assertEqual(refused.returncode, 2)
        self.assertFalse((C.gen_dir(self.ws, 2) / "generate.json").exists())
        self.assertEqual(self.total(1), 65 * 4)
        status = json.loads(run_tool("generate.py", "status", "--workspace", str(self.ws)).stdout)
        self.assertEqual(status["creditsTotal"], 65 * 4)
        self.assertEqual(V.credits_total(self.ws), (65 * 4, 1))

    def test_a_gap_in_the_archive_does_not_hide_later_receipts(self):
        self.write_state(4)
        self.assertEqual(self.record(1, scenario="a").returncode, 0)
        for scenario in ("b", "c", "d"):
            self.assertEqual(self.record(1, scenario=scenario, extra=("--overwrite",)).returncode, 0)
        (C.gen_dir(self.ws, 1) / "superseded-2.json").unlink()
        self.assertEqual(sorted(p.name for p in C.superseded_receipts(C.gen_dir(self.ws, 1))),
                         ["superseded-1.json", "superseded-3.json"])
        self.assertEqual(V.credits_total(self.ws), (65 * 3, 1))
        self.assertEqual(sum(r["generator"]["credits"] for r in C.generation_receipts(self.ws)), 65 * 3)
        # numeric, not lexical, order: superseded-10 follows superseded-9
        directory = C.gen_dir(self.ws, 1)
        for number in (9, 10):
            (directory / f"superseded-{number}.json").write_text(json.dumps({"generator": {"credits": number}}))
        self.assertEqual([p.name for p in C.superseded_receipts(directory)][-2:], ["superseded-9.json", "superseded-10.json"])

    def test_overwrite_is_refused_once_a_likeness_or_a_decision_exists(self):
        self.write_state(4)
        self.assertEqual(self.record(1, scenario="a").returncode, 0)
        fake_likeness(self.ws, 1, "different", 1.0)
        refused = self.record(1, scenario="b", extra=("--overwrite",))
        self.assertEqual(refused.returncode, 2)
        self.assertIn("already has a likeness record", refused.stderr)
        self.assertEqual(self.total(1), 65)
        self.assertEqual(list(C.gen_dir(self.ws, 1).glob("superseded-*.json")), [])
        (C.gen_dir(self.ws, 1) / "likeness.json").unlink()
        state = new_state("r.png", profile="textured", max_generations=4)
        state["textured"]["history"].append({"generation": 1, "outcome": "regenerate"})
        save_state_path = self.ws / ".img2threejs" / "state.json"
        save_state_path.write_text(json.dumps(state))
        refused = self.record(1, scenario="c", extra=("--overwrite",))
        self.assertIn("already has a recorded decision", refused.stderr)


class AssetContainmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name).resolve() / "ws"
        self.outside = Path(self.tmp.name).resolve() / "outside"
        self.outside.mkdir()
        (self.outside / "evil.glb").write_bytes(b"x")
        (C.gen_dir(self.ws, 1) / "raw").mkdir(parents=True)
        (C.gen_dir(self.ws, 1) / "raw" / "model.glb").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_contained_asset_resolves(self):
        self.assertEqual(C.contained_asset(self.ws, 1, "raw/model.glb"), C.gen_dir(self.ws, 1) / "raw" / "model.glb")

    def test_escaping_paths_are_refused(self):
        link = C.gen_dir(self.ws, 1) / "raw" / "link.glb"
        link.symlink_to(self.outside / "evil.glb")
        dirlink = C.gen_dir(self.ws, 1) / "linkdir"
        dirlink.symlink_to(self.outside)
        for bad in (str(self.outside / "evil.glb"), "../../../../../outside/evil.glb", "raw/link.glb",
                    "linkdir/evil.glb", "", ".", None):
            with self.subTest(path=bad), self.assertRaises(C.TexturedError):
                C.contained_asset(self.ws, 1, bad)

    def test_a_symlinked_generation_directory_is_refused(self):
        other = self.ws / ".img2threejs" / "textured" / "gen-2"
        other.symlink_to(self.outside)
        with self.assertRaises(C.TexturedError):
            C.contained_asset(self.ws, 2, "evil.glb")

    def test_inspect_and_normalize_cli_refuse_a_crafted_receipt_before_touching_bpy(self):
        (C.gen_dir(self.ws, 1) / "generate.json").write_text(json.dumps({"asset": {"path": str(self.outside / "evil.glb")}}))
        for tool in ("inspect.py", "normalize.py"):
            done = run_tool(tool, "--workspace", str(self.ws), "--gen", "1")
            self.assertEqual(done.returncode, 2, tool)
            self.assertIn("outside", done.stderr)


class ModelReferenceContainmentTest(unittest.TestCase):
    """A model's external buffers/textures must stay inside gen-N before any importer opens it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name).resolve() / "ws"
        self.raw = C.gen_dir(self.ws, 1) / "raw"
        self.raw.mkdir(parents=True)
        self.secret = self.ws / "README.md"
        self.secret.write_text("secret")
        self.outside = Path(self.tmp.name).resolve() / "outside"
        self.outside.mkdir()
        (self.outside / "x.bin").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def gltf(self, **parts):
        path = self.raw / "model.gltf"
        path.write_text(json.dumps({"asset": {"version": "2.0"}, **parts}))
        return path

    def check(self, path):
        C.require_contained_model(self.ws, 1, path)

    def test_relative_contained_and_embedded_references_pass(self):
        (self.raw / "tex").mkdir()
        (self.raw / "tex" / "a.png").write_bytes(b"p")
        (self.raw / "m.bin").write_bytes(b"b")
        self.check(self.gltf(buffers=[{"uri": "m.bin", "byteLength": 1}, {"uri": "data:application/octet-stream;base64,AA==", "byteLength": 1}],
                             images=[{"uri": "tex/a.png"}, {"uri": "tex%2Fa.png"}, {"bufferView": 0}]))

    def test_gltf_references_that_leave_the_generation_are_refused(self):
        (self.raw / "link.bin").symlink_to(self.outside / "x.bin")
        (self.raw / "linkdir").symlink_to(self.outside)
        for uri in (str(self.secret), "../../../../README.md", "..%2F..%2FREADME.md", "file:///etc/passwd", f"file://{self.secret}",
                    "http://example.com/a.bin", "https://example.com/a.bin", "link.bin", "linkdir/x.bin", "\\\\host\\share\\a.bin",
                    "/etc/passwd", "C:/Windows/a.bin", ""):
            for key in ("buffers", "images"):
                with self.subTest(uri=uri, key=key), self.assertRaises(C.TexturedError):
                    self.check(self.gltf(**{key: [{"uri": uri}]}))

    def test_a_non_string_uri_and_a_malformed_document_are_refused(self):
        for document in ({"buffers": [{"uri": 5}]}, {"buffers": "x"}):
            with self.subTest(document=document), self.assertRaises(C.TexturedError):
                self.check(self.gltf(**document))
        broken = self.raw / "model.gltf"
        broken.write_text("{nope")
        with self.assertRaises(C.TexturedError):
            self.check(broken)

    def test_glb_embedded_buffers_pass_and_external_ones_are_refused(self):
        import struct

        def glb(document):
            body = json.dumps(document).encode()
            body += b" " * (-len(body) % 4)
            return b"glTF" + struct.pack("<II", 2, 20 + len(body)) + struct.pack("<I", len(body)) + b"JSON" + body

        embedded = self.raw / "model.glb"
        embedded.write_bytes(glb({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}]}))
        self.check(embedded)
        embedded.write_bytes(glb({"asset": {"version": "2.0"}, "buffers": [{"uri": str(self.secret), "byteLength": 4}]}))
        with self.assertRaises(C.TexturedError):
            self.check(embedded)
        embedded.write_bytes(b"not a glb at all, but long enough")
        with self.assertRaises(C.TexturedError):
            self.check(embedded)

    def test_obj_mtllib_and_mtl_maps_are_checked(self):
        obj = self.raw / "model.obj"
        (self.raw / "m.mtl").write_text("newmtl a\nmap_Kd -s 1 1 1 tex.png\n")
        obj.write_text("mtllib m.mtl\nv 0 0 0\n")
        self.check(obj)
        for body in (f"mtllib {self.secret}\n", "mtllib ../../../../../README.md\n"):
            obj.write_text(body)
            with self.subTest(body=body), self.assertRaises(C.TexturedError):
                self.check(obj)
        obj.write_text("mtllib m.mtl\n")
        (self.raw / "m.mtl").write_text(f"newmtl a\nmap_Kd {self.secret}\n")
        with self.assertRaises(C.TexturedError):
            self.check(obj)

    def test_inspect_and_normalize_cli_refuse_an_external_buffer_before_touching_bpy(self):
        self.gltf(buffers=[{"uri": str(self.secret), "byteLength": 6}])
        (C.gen_dir(self.ws, 1) / "generate.json").write_text(json.dumps({"asset": {"path": "raw/model.gltf"}}))
        for tool in ("inspect.py", "normalize.py"):
            done = run_tool(tool, "--workspace", str(self.ws), "--gen", "1")
            self.assertEqual(done.returncode, 2, tool + done.stderr)
            self.assertIn("refusing to import", done.stderr)


class DomainRigRowsTest(unittest.TestCase):
    def rows(self, domain, home):
        old = os.environ.get("IMG2_HOME")
        os.environ["IMG2_HOME"] = home
        try:
            return [row["id"] for row in new_state("r.png", profile="textured", domain_id=domain)["checklist"] if row["scope"] == "rig"]
        finally:
            if old is None:
                del os.environ["IMG2_HOME"]
            else:
                os.environ["IMG2_HOME"] = old

    def test_the_installed_character_plugins_domain_appends_its_rig_rows(self):
        with tempfile.TemporaryDirectory() as home:
            plugin = Path(home, "plugins", "character")
            plugin.mkdir(parents=True)
            Path(home, "plugins.json").write_text(json.dumps({"plugins": [{"id": "character"}]}))
            plugin.joinpath("domain.json").write_text(json.dumps({
                "id": "animated-character",
                "rigSteps": [["rig-contract-read", "Read {plugin_dir}/reference/animation-contract.md"],
                             ["rig-gates", "Run python3 {plugin_dir}/tools/gate_rigging.py"]]}))
            self.assertEqual(self.rows("animated-character", home), ["rig-contract-read", "rig-gates"])
            self.assertEqual(self.rows("character", home), [])   # the in-repo domain declares no rig track
            state = new_state("r.png", profile="textured", domain_id="character")
            self.assertEqual(state["domain"], "character")

    def test_state_init_says_when_the_chosen_domain_adds_no_rig_rows(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
            env = {**os.environ, "HOME": home, "IMG2_HOME": home + "/img2"}
            done = subprocess.run([sys.executable, str(ROOT / "forge/state.py"), "init", "--state", "s.json", "--reference", "r.png",
                                   "--profile", "textured", "--domain", "character"], capture_output=True, text=True, cwd=work, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("no rig rows", done.stderr)


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
            gen, ref, _ = pipeline(ws)
            record_model(self, ws)
            D.main(["--workspace", str(ws)])
            self.assertEqual(V.main(["--workspace", str(ws), "--gen", "1", "--reference", str(ref)]), 0)
            final = ws / ".img2threejs/textured/final"
            for name in ("model.glb", "comparison.png", "REPORT.md", "renders/front.png", "reference.png", "delivery.json"):
                self.assertTrue((final / name).is_file(), name)
            report = (final / "REPORT.md").read_text()
            self.assertIn("NOT by Jev", report)
            self.assertIn("What is NOT done", report)
            self.assertIn("T1", report)
            with self.assertRaises(C.TexturedError):
                V.main(["--workspace", str(ws), "--gen", "2", "--reference", str(ref)])
            self.assertEqual((final / "model.glb").read_bytes(), (gen / "normalized.glb").read_bytes())


if __name__ == "__main__":
    unittest.main()
