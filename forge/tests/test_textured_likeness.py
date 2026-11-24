"""Likeness question set, Jev consent/budget gating, and the no-account model-answer path.

No network and no Jev installation: the transport is a fake `JevBackend` with the same seam
(`auth_present`, `consent_status`, `session`, `post`).
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textured_support import ROOT, run_tool, write_png  # noqa: E402

sys.path.insert(0, str(ROOT / "forge" / "textured"))
import _common as C  # noqa: E402
import jev_backend as J  # noqa: E402
import likeness as L  # noqa: E402
from textured_chain_support import pipeline, model_grant  # noqa: E402

SECRET = "sk-THIS-MUST-NEVER-APPEAR-123456"


class FakeSession:
    def __init__(self, backend):
        self.backend, self.attempts = backend, backend.ledger

    def previous(self, digest):
        return self.attempts.get(digest)

    def reserve(self, digest):
        spent = sum(a["charged"] for a in self.attempts.values())
        if spent + 64000 > self.backend.budget:
            raise RuntimeError("budget-exhausted")
        self.attempts[digest] = {"charged": 64000, "status": "in-flight"}
        self.backend.events.append("reserve")

    def account(self, digest, usage):
        self.attempts[digest] = {"charged": usage["input_tokens"] + usage["output_tokens"], "status": "charged"}
        self.backend.events.append("account")

    def complete(self, digest, sha):
        self.attempts[digest]["status"] = "completed"
        self.backend.events.append("complete")


class FakeBackend(J.JevBackend):
    def __init__(self, questions, *, auth=True, consent=True, purposes=("quality-review",), budget=200000, answers=None):
        self.questions, self.auth, self.consent, self.purposes, self.budget = questions, auth, consent, purposes, budget
        self.ledger, self.events, self.sent = {}, [], []
        self.answers = answers

    def auth_present(self):
        return self.auth

    def consent_status(self, workspace, run_id):
        if not self.consent:
            raise J.JevUnavailable("consent-required", "no consent")
        return {"granted": True, "purposes": list(self.purposes), "sha256": "a" * 64, "tokenBudget": self.budget}

    @contextmanager
    def session(self, workspace, run_id):
        yield FakeSession(self)

    def post(self, payload, timeout):
        self.events.append("post")
        self.sent.append(payload)
        answers = self.answers or {}
        if not answers:
            for key, q in self.questions.items():
                if q["type"] == "choice":
                    first = next(iter(q["criteria"]))
                    answers[key] = {"type": "choice", "choice": first, "confidence": 0.3,
                                    "probabilities": {k: (0.6 if k == first else 0.2) for k in q["criteria"]}}
                elif q["type"] == "score":
                    answers[key] = {"type": "score", "score": 2.5, "confidence": 0.5}
                else:
                    answers[key] = {"type": "noul", "noul": 0.2}
        return {"model": J.MODEL, "answers": answers, "usage": {"input_tokens": 7000, "output_tokens": 300}}


class QuestionSetTest(unittest.TestCase):
    def test_every_kind_has_overall_choice_scores_and_defect_checks(self):
        for kind in L.kinds():
            questions = L.build_questions(kind)
            self.assertEqual(questions["overall"]["type"], "choice")
            self.assertEqual(set(questions["overall"]["criteria"]), {"same", "similar", "different"})
            scores = [k for k, q in questions.items() if q["type"] == "score"]
            defects = [k for k, q in questions.items() if q["type"] == "noul"]
            self.assertGreaterEqual(len(scores), 4, kind)
            self.assertGreaterEqual(len(defects), 3, kind)
            self.assertTrue(all(len(questions[k]["criteria"]) == 5 for k in scores))

    def test_character_set_matches_the_vesper_shape_and_extra_features_extend_it(self):
        base = L.build_questions("character")
        self.assertEqual(sum(q["type"] == "score" for q in base.values()), 8)
        self.assertEqual(sum(q["type"] == "noul" for q in base.values()), 5)
        extended = L.build_questions("character", L.parse_extra_features(["scarf=the torn scarf"]))
        self.assertIn("score:scarf", extended)
        with self.assertRaises(C.TexturedError):
            L.build_questions("character", [("hands", "dup")])
        with self.assertRaises(C.TexturedError):
            L.build_questions("spaceship")
        with self.assertRaises(C.TexturedError):
            L.parse_extra_features(["no-separator"])

    def test_answers_must_cover_exactly_the_question_set_with_valid_ranges(self):
        questions = L.build_questions("prop")
        good = {k: ({"choice": "same"} if q["type"] == "choice" else (3 if q["type"] == "score" else 0.1)) for k, q in questions.items()}
        out = L.normalize_answers(questions, good)
        self.assertEqual(out["overall"]["probabilities"]["same"], 1.0)
        for mutate, text in ((lambda a: a.pop("overall"), "missing"),
                             (lambda a: a.update(extra=1), "unexpected"),
                             (lambda a: a.update(overall="maybe"), "must be one of"),
                             (lambda a: a.update({"score:shape_details": 4.5}), "within 0..4"),
                             (lambda a: a.update({"noul:melted_geometry": -0.1}), "within 0..1"),
                             (lambda a: a.update({"score:shape_details": True}), "number")):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.assertRaises(C.TexturedError, msg=text) as ctx:
                L.normalize_answers(questions, bad)
            self.assertIn(text, str(ctx.exception))

    def test_summary_means_scores_and_flags_defects_at_the_threshold(self):
        questions = L.build_questions("generic")
        answers = {k: ({"choice": "similar"} if q["type"] == "choice" else (2 if q["type"] == "score" else 0.5))
                   for k, q in questions.items()}
        answers["score:texture_realism"] = 4
        summary = L.summarize(L.normalize_answers(questions, answers))
        self.assertEqual(summary["overall"]["verdict"], "similar")
        self.assertAlmostEqual(summary["meanScore"], (2 * 3 + 4) / 4)
        self.assertEqual(summary["flaggedDefects"], sorted(k.split(":")[1] for k in questions if k.startswith("noul:")))

    def test_payload_has_prefixed_instructions_one_png_asset_and_no_local_paths(self):
        with tempfile.TemporaryDirectory() as d:
            sheet = write_png(Path(d) / "sheet.png", 1568, 1176, (9, 9, 9))
            payload = L.build_payload(sheet, L.build_questions("furniture"), "tag")
        self.assertEqual(payload["model"], J.MODEL)
        self.assertTrue(all(q["instructions"].startswith(L.CONTEXT) for q in payload["questions"].values()))
        (asset,) = payload["assets"].values()
        self.assertEqual((asset["type"], asset["format"]), ("image", "png"))
        self.assertTrue(base64.b64decode(asset["data_base64"]).startswith(b"\x89PNG"))
        self.assertNotIn(d, json.dumps(payload))

    def test_sheet_limits_are_enforced_before_any_send(self):
        with tempfile.TemporaryDirectory() as d:
            for size in ((1600, 800), (200, 800), (800, 1600)):
                path = write_png(Path(d) / f"{size[0]}.png", *size, (1, 1, 1))
                with self.assertRaises(C.TexturedError):
                    L.check_sheet(path)
            self.assertEqual(L.check_sheet(write_png(Path(d) / "ok.png", 280, 1568, (1, 1, 1))), (280, 1568))


class ConsentGatingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name).resolve()
        self.questions = L.build_questions("character")
        gen, _, _ = pipeline(self.ws)
        (gen / "likeness.json").unlink()

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *extra):
        return L.build_parser().parse_args(["run", "--workspace", str(self.ws), "--gen", "1", "--kind", "character",
                                            "--run-id", "run-1", *extra])

    def test_granted_run_reserves_before_posting_accounts_after_and_records_tokens(self):
        backend = FakeBackend(self.questions)
        L.cmd_run(self.args(), self.ws, backend)
        self.assertEqual(backend.events, ["reserve", "post", "account", "complete"])
        record = json.loads(L.likeness_path(self.ws, 1).read_text())
        self.assertEqual((record["decider"], record["provider"], record["presentedAsJev"]), (J.MODEL, "jev", True))
        self.assertEqual(record["usage"], {"input_tokens": 7000, "output_tokens": 300})
        self.assertEqual(record["consent"]["tokenBudget"], 200000)
        self.assertEqual(L.tokens_spent(self.ws), 7300)
        self.assertEqual(len(backend.sent), 1)

    def test_no_account_sends_nothing(self):
        backend = FakeBackend(self.questions, auth=False)
        with self.assertRaises(J.JevUnavailable) as ctx:
            L.cmd_run(self.args(), self.ws, backend)
        self.assertEqual(ctx.exception.reason, "credential-missing")
        self.assertEqual((backend.events, backend.sent), ([], []))
        self.assertFalse(L.likeness_path(self.ws, 1).exists())

    def test_missing_or_wrong_purpose_consent_sends_nothing(self):
        for backend in (FakeBackend(self.questions, consent=False), FakeBackend(self.questions, purposes=("routing",))):
            with self.assertRaises(J.JevUnavailable) as ctx:
                L.cmd_run(self.args(), self.ws, backend)
            self.assertEqual(ctx.exception.reason, "consent-required")
            self.assertEqual((backend.events, backend.sent), ([], []))

    def test_budget_exhaustion_blocks_the_send(self):
        backend = FakeBackend(self.questions, budget=70000)
        L.cmd_run(self.args(), self.ws, backend)  # first call fits one 64k reservation
        next_gen, _, _ = pipeline(self.ws, 2, write_state=False)
        (next_gen / "likeness.json").unlink()
        with self.assertRaises(RuntimeError):
            L.cmd_run(self.args("--gen", "2"), self.ws, backend)
        self.assertEqual(len(backend.sent), 1)

    def test_an_identical_request_is_not_paid_for_twice(self):
        backend = FakeBackend(self.questions)
        L.cmd_run(self.args(), self.ws, backend)
        with self.assertRaises(C.TexturedError) as ctx:
            L.cmd_run(self.args(), self.ws, backend)
        self.assertIn("already sent", str(ctx.exception))
        self.assertEqual(len(backend.sent), 1)

    def test_malformed_provider_answers_are_rejected_not_recorded(self):
        backend = FakeBackend(self.questions, answers={"overall": {"type": "choice", "choice": "same"}})
        with self.assertRaises(C.TexturedError):
            L.cmd_run(self.args(), self.ws, backend)
        self.assertFalse(L.likeness_path(self.ws, 1).exists())
        self.assertEqual(L.tokens_spent(self.ws), 7300)
        self.assertEqual(next(iter(backend.ledger.values()))["charged"], 7300)
        with self.assertRaises(C.TexturedError):
            L.cmd_run(self.args(), self.ws, backend)
        self.assertEqual(len(backend.sent), 1)
        self.assertEqual(L.tokens_spent(self.ws), 7300)

    def test_invalid_usage_keeps_full_reservation_and_never_records_a_verdict(self):
        for usage in (None, {}, {"input_tokens": -1, "output_tokens": 300},
                      {"input_tokens": True, "output_tokens": 300},
                      {"input_tokens": 7000, "output_tokens": "300"}):
            with self.subTest(usage=usage):
                backend = FakeBackend(self.questions)
                post = backend.post
                def invalid_usage(payload, timeout):
                    response = post(payload, timeout)
                    response["usage"] = usage
                    return response
                backend.post = invalid_usage
                with self.assertRaises(C.TexturedError):
                    L.cmd_run(self.args(), self.ws, backend)
                self.assertEqual(next(iter(backend.ledger.values()))["charged"], 64000)
                self.assertFalse(L.likeness_path(self.ws, 1).exists())
                with self.assertRaises(C.TexturedError):
                    L.cmd_run(self.args(), self.ws, backend)
                self.assertEqual(len(backend.sent), 1)

    def test_the_api_key_never_reaches_a_receipt_or_the_payload(self):
        os.environ["TYPESAFE_API_KEY"] = SECRET
        try:
            backend = FakeBackend(self.questions)
            L.cmd_run(self.args(), self.ws, backend)
        finally:
            del os.environ["TYPESAFE_API_KEY"]
        blob = json.dumps(backend.sent) + "".join(p.read_text() for p in self.ws.rglob("*.json*") if p.is_file())
        self.assertNotIn(SECRET, blob)

    def test_run_id_is_validated(self):
        with self.assertRaises(C.TexturedError):
            J.check_run_id("../escape")

    def test_an_unpinned_jev_plugin_is_refused_before_anything_is_loaded(self):
        with tempfile.TemporaryDirectory() as tools:
            for name in J.REQUIRED_TOOLS + ("jev",):
                Path(tools, f"{name}.py").write_text("raise SystemExit('executed')\n")
            with self.assertRaises(J.JevUnavailable) as ctx:
                J.PluginBackend(Path(tools))
        self.assertEqual(ctx.exception.reason, "provider-unpinned")
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(J.JevUnavailable) as ctx:
                J.PluginBackend(Path(empty))
            self.assertEqual(ctx.exception.reason, "not-installed")


class PluginPinTest(unittest.TestCase):
    """The plugin pin covers every file the plugin runs, and the launch gets a scrubbed environment."""

    def setUp(self):
        from jev_cli_bridge import REQUIRED_PROVIDER_FILES
        import hashlib
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.plugin = self.root / "plugin"
        self.marker = self.root / "executed.txt"
        files = {}
        for relative in sorted(REQUIRED_PROVIDER_FILES):
            body = f"VALUE = {relative!r}\n"
            if relative == "tools/jev.py":
                body = ("import json, os, sys\n"
                        "print(json.dumps({'granted': True, 'env': sorted(os.environ)}))\n")
            path = self.plugin / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            files[relative] = hashlib.sha256(body.encode()).hexdigest()
        lock = self.root / "lock.json"
        lock.write_text(json.dumps({"files": files}))
        self.original_lock, J.LOCK = J.LOCK, lock
        self.tools = self.plugin / "tools"

    def tearDown(self):
        J.LOCK = self.original_lock
        self.tmp.cleanup()

    def backend(self):
        backend = J.PluginBackend(self.tools)
        self.addCleanup(backend.close)
        return backend

    def test_a_pinned_plugin_loads(self):
        self.assertIsNotNone(self.backend())

    def test_every_required_file_is_verified_not_just_the_entry_points(self):
        from jev_cli_bridge import REQUIRED_PROVIDER_FILES
        for relative in sorted(REQUIRED_PROVIDER_FILES):
            with self.subTest(file=relative):
                path = self.plugin / relative
                original = path.read_text()
                path.write_text(original + f"open({str(self.marker)!r}, 'w').write('ran')\n")
                try:
                    with self.assertRaises(J.JevUnavailable) as ctx:
                        J.PluginBackend(self.tools)
                    self.assertEqual(ctx.exception.reason, "provider-unpinned")
                    self.assertFalse(self.marker.exists())
                finally:
                    path.write_text(original)

    def test_a_pinned_file_that_is_missing_or_a_symlink_is_refused(self):
        target = self.plugin / "tools" / "jev_contracts.py"
        moved = self.root / "elsewhere.py"
        moved.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(moved)
        with self.assertRaises(J.JevUnavailable) as ctx:
            J.PluginBackend(self.tools)
        self.assertEqual(ctx.exception.reason, "provider-unpinned")

    def test_the_consent_probe_runs_the_verified_copy_with_no_inherited_credentials(self):
        os.environ["TYPESAFE_API_KEY"] = SECRET
        os.environ["AWS_SECRET_ACCESS_KEY"] = SECRET
        os.environ["PYTHONPATH"] = str(self.root)
        try:
            backend = self.backend()
            result = backend.consent_status(self.root, "run-1")
        finally:
            for key in ("TYPESAFE_API_KEY", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH"):
                del os.environ[key]
        self.assertTrue(result["granted"])
        self.assertNotIn("TYPESAFE_API_KEY", result["env"])
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", result["env"])
        self.assertNotIn("PYTHONPATH", result["env"])
        self.assertEqual(Path(backend.tools).resolve().parent.parent, Path(backend._stage.name).resolve())

    def test_a_swap_after_the_check_does_not_change_what_runs(self):
        backend = self.backend()
        (self.plugin / "tools" / "jev.py").write_text(f"open({str(self.marker)!r}, 'w').write('ran')\n")
        backend.consent_status(self.root, "run-1")
        self.assertFalse(self.marker.exists())


class NoAccountModeTest(unittest.TestCase):
    def test_model_answers_are_journalled_as_model_decider_never_as_jev(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d).resolve()
            gen, reference, _ = pipeline(ws)
            (gen / "likeness.json").unlink()
            run_id = model_grant(self, ws, reference, "claude-opus-5")
            done = run_tool("likeness.py", "questions", "--workspace", str(ws), "--gen", "1", "--kind", "prop")
            self.assertEqual(done.returncode, 0, done.stderr)
            asked = json.loads((gen / "likeness-questions.json").read_text())
            self.assertIn("NOT Jev", asked["decider"])
            answers = {k: ({"choice": "similar"} if q["type"] == "choice" else (3 if q["type"] == "score" else 0.1))
                       for k, q in asked["questions"].items()}
            (ws / "answers.json").write_text(json.dumps(answers))
            code = L.main(["record-model-answers", "--workspace", str(ws), "--gen", "1", "--kind", "prop",
                "--answers", str(ws / "answers.json"), "--model-id", "claude-opus-5",
                "--run-id", run_id, "--reference", str(reference)])
            self.assertEqual(code, 0)
            record = json.loads((gen / "likeness.json").read_text())
            self.assertEqual(record["decider"], "model:claude-opus-5")
            self.assertEqual(record["provider"], "host-model")
            self.assertFalse(record["presentedAsJev"])
            self.assertIn("not by Jev", record["note"])
            journal = [json.loads(line) for line in (ws / ".img2threejs/textured/journal.jsonl").read_text().splitlines()]
            self.assertEqual(journal[-1]["decider"], "model:claude-opus-5")
            self.assertEqual(L.tokens_spent(ws), 0)

    def test_model_answers_with_a_wrong_key_set_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d).resolve()
            gen, reference, _ = pipeline(ws)
            (gen / "likeness.json").unlink()
            run_id = model_grant(self, ws, reference, "m")
            (ws / "answers.json").write_text(json.dumps({"overall": "same"}))
            with self.assertRaises(C.TexturedError):
                L.main(["record-model-answers", "--workspace", str(ws), "--gen", "1", "--kind", "prop",
                    "--answers", str(ws / "answers.json"), "--model-id", "m",
                    "--run-id", run_id, "--reference", str(reference)])
            self.assertFalse((gen / "likeness.json").exists())

    def test_emitted_no_account_command_executes_the_supported_parser_and_records_bound_answers(self):
        import shlex
        import textured_profile
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d).resolve()
            gen, reference, _ = pipeline(ws)
            (gen / "likeness.json").unlink()
            run_id = model_grant(self, ws, reference)
            questions = L.build_questions("generic")
            answers = {key: ("same" if row["type"] == "choice" else 3 if row["type"] == "score" else 0.1)
                       for key, row in questions.items()}
            C.write_json(ws / "answers.json", answers)
            emitted = dict(textured_profile.textured_rows(4))["likeness"]
            emitted = emitted[emitted.index("record-model-answers"):].removesuffix(")")
            argv = shlex.split(emitted.format(gen=1, reference=reference))
            values = {".": str(ws), "<answers.json>": str(ws / "answers.json"),
                      "<provider:model>": "fixture", "<run-id>": run_id}
            self.assertEqual(L.main([values.get(value, value) for value in argv]), 0)
            record = C.read_json(gen / "likeness.json")
            self.assertEqual(record["authority"]["runId"], run_id)
            self.assertEqual(record["authority"]["generation"], 1)


if __name__ == "__main__":
    unittest.main()
