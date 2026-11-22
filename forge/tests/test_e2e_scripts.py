"""Offline tests for scripts/e2e: stubbed subprocess runner, no network, Jev, model or install."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "e2e"
sys.path.insert(0, str(SCRIPTS))
import provision_home  # noqa: E402
import route_one  # noqa: E402

HARNESS_SHA = "a" * 40
PLUGIN_SHAS = {name: format(index + 1, "x") * 40 for index, name in enumerate(provision_home.REQUIRED_PLUGINS)}
DOCTOR_OK = json.dumps({"fails": 0, "warns": 2, "findings": [
    {"level": "WARN", "plugin": "jev", "message": "multi-capability provider"}]})
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.src = self.base / "harness-src"
        (self.src / ".git").mkdir(parents=True)
        self.node = self.base / "bin" / "node"
        self.node.parent.mkdir()
        self.node.write_bytes(b"#!/bin/sh\n")
        self.node.chmod(0o755)
        self.plugin_dirs = {name: self.base / "plugins" / name for name in provision_home.REQUIRED_PLUGINS}
        for path in self.plugin_dirs.values():
            path.mkdir(parents=True)
        self.home = self.base / "iso"
        self.calls = []
        self.doctor = (0, DOCTOR_OK, "")
        self.fail_on = None
        self.head = HARNESS_SHA

    def specs(self, **override):
        values = {name: "%s=%s@%s" % (name, self.plugin_dirs[name], PLUGIN_SHAS[name])
                  for name in provision_home.REQUIRED_PLUGINS}
        values.update(override)
        return list(values.values())

    def runner(self, argv, *, env, cwd, timeout):
        self.calls.append({"argv": list(argv), "env": dict(env), "cwd": cwd, "timeout": timeout})
        img2_home = Path(env["HOME"]) / ".img2"
        if "install" in argv:
            (img2_home / "harness/bin").mkdir(parents=True)
            (img2_home / "harness/bin/img2.mjs").write_bytes(b"launcher")
            (img2_home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": []}))
        if "add" in argv:
            registry = json.loads((img2_home / "plugins.json").read_text())
            identifier = argv[argv.index("--plugin") + 1]
            registry["plugins"].append({"id": identifier, "resolvedSha": argv[argv.index("--ref") + 1]})
            (img2_home / "plugins.json").write_text(json.dumps(registry))
        if self.fail_on and self.fail_on in argv:
            return 1, "", "boom"
        if "doctor" in argv:
            return self.doctor
        if "rev-parse" in argv:
            return 0, self.head + "\n", ""
        return 0, "", ""

    def run_provision(self, specs=None, **kwargs):
        plugins = provision_home.parse_plugins(specs or self.specs())
        return provision_home.provision(
            home=self.home, harness_src=self.src, harness_ref=HARNESS_SHA, plugins=plugins,
            node=self.node, git="/usr/bin/git", runner=self.runner, clock=lambda: 1.0, **kwargs)

    def test_exact_argv_arrays_and_sanitized_environment(self):
        code, record = self.run_provision()
        self.assertEqual(code, 0, record["problems"])
        root = self.home.resolve()
        img2_home = root / "home/.img2"
        installed = str(img2_home / "harness/bin/img2.mjs")
        node, source = str(self.node), str(root / "harness-source")
        expected = [
            ["/usr/bin/git", "clone", "--no-hardlinks", "--no-checkout", str(self.src), source],
            ["/usr/bin/git", "-C", source, "checkout", "--detach", HARNESS_SHA],
            [node, source + "/bin/img2.mjs", "install", "--from", source, "--yes", "--home", str(img2_home)],
        ]
        for name in provision_home.REQUIRED_PLUGINS:
            expected.append([node, installed, "add", str(self.plugin_dirs[name]), "--ref", PLUGIN_SHAS[name],
                             "--plugin", name, "--allow-any-source", "--yes", "--home", str(img2_home)])
        expected += [
            [node, installed, "sync", "--home", str(img2_home)],
            [node, installed, "sync", "--check", "--home", str(img2_home)],
            ["/usr/bin/git", "-C", str(img2_home / "harness"), "rev-parse", "HEAD"],
            [node, installed, "doctor", "--json", "--home", str(img2_home)],
        ]
        self.assertEqual([call["argv"] for call in self.calls], expected)
        for call in self.calls:
            self.assertEqual(call["env"]["HOME"], str(root / "home"))
            self.assertEqual(call["env"]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(call["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertTrue(call["env"]["XDG_CONFIG_HOME"].startswith(str(root)))
            self.assertTrue(call["env"]["TMPDIR"].startswith(str(root)))
            self.assertTrue(call["env"]["BLENDER_USER_CONFIG"].startswith(str(root)))
            self.assertNotIn("IMG2_HOME", call["env"])
            self.assertEqual(call["env"]["PATH"].split(":")[1], str(self.node.parent))
            self.assertEqual(call["timeout"], provision_home.COMMAND_TIMEOUT)
            self.assertIsInstance(call["argv"], list)

    def test_provision_json_records_commands_doctor_and_pins(self):
        code, _ = self.run_provision()
        self.assertEqual(code, 0)
        record = json.loads((self.home / "provision.json").read_text())
        self.assertEqual(record["status"], "ok")
        self.assertEqual([c["id"] for c in record["commands"]][:3], ["clone-harness", "pin-harness-source", "install"])
        self.assertTrue(all(c["exitCode"] == 0 for c in record["commands"]))
        self.assertEqual(record["doctor"]["warns"], 2)
        self.assertTrue(record["doctor"]["green"])
        self.assertEqual(record["doctor"]["findings"][0]["level"], "WARN")
        pins = record["pins"]
        self.assertEqual(pins["harnessSha"], HARNESS_SHA)
        self.assertEqual(pins["harnessExecutableSha256"], hashlib.sha256(b"launcher").hexdigest())
        self.assertEqual(pins["nodeSha256"], hashlib.sha256(b"#!/bin/sh\n").hexdigest())
        self.assertEqual(set(pins["plugins"]), set(provision_home.REQUIRED_PLUGINS))
        self.assertEqual(pins["plugins"]["jev"]["resolvedSha"], PLUGIN_SHAS["jev"])
        self.assertEqual((record["grants"], record["modelCalls"]), (0, 0))

    def test_refuses_non_empty_home_before_running_anything(self):
        self.home.mkdir()
        (self.home / "leftover").write_text("x")
        with self.assertRaises(provision_home.Refusal):
            self.run_provision()
        self.assertEqual(self.calls, [])
        self.assertEqual([p.name for p in self.home.iterdir()], ["leftover"])

    def test_accepts_empty_existing_home(self):
        self.home.mkdir()
        code, _ = self.run_provision()
        self.assertEqual(code, 0)

    def test_refuses_real_home_and_real_img2(self):
        for forbidden in (Path.home(), Path.home() / ".img2", Path.home() / "sub"):
            with self.assertRaises(provision_home.Refusal):
                provision_home.check_home(forbidden)

    def test_refuses_malformed_plugin_specs(self):
        bad = ["jev", "jev=/p", "jev=/p@xyz", "jev=@" + "a" * 40, "Jev=/p@" + "a" * 40, "jev=/p@" + "A" * 40]
        for spec in bad:
            with self.assertRaises(provision_home.Refusal, msg=spec):
                provision_home.parse_plugin_spec(spec)

    def test_refuses_missing_or_duplicate_default_plugins(self):
        with self.assertRaises(provision_home.Refusal):
            provision_home.parse_plugins(self.specs()[:-1])
        with self.assertRaises(provision_home.Refusal):
            provision_home.parse_plugins(self.specs() + [self.specs()[0]])

    def test_plugin_path_may_contain_equals_and_at(self):
        identifier, path, sha = provision_home.parse_plugin_spec("jev=/tmp/a@b=c/jev@" + "a" * 40)
        self.assertEqual((identifier, path, sha), ("jev", "/tmp/a@b=c/jev", "a" * 40))

    def test_failed_step_stops_and_exits_nonzero(self):
        self.fail_on = "install"
        code, record = self.run_provision()
        self.assertEqual(code, 1)
        self.assertEqual(record["commands"][-1]["id"], "install")
        self.assertFalse(any("add" in call["argv"] for call in self.calls))
        self.assertIsNone(record["doctor"])
        self.assertTrue((self.home / "provision.json").exists())

    def test_red_doctor_exits_nonzero(self):
        self.doctor = (1, json.dumps({"fails": 1, "warns": 0, "findings": [
            {"level": "FAIL", "plugin": "cs2", "message": "bad"}]}), "")
        code, record = self.run_provision()
        self.assertEqual(code, 1)
        self.assertFalse(record["doctor"]["green"])
        self.assertEqual(record["status"], "failed")

    def test_unparsable_doctor_is_red_even_with_exit_zero(self):
        self.doctor = (0, "not json", "")
        code, record = self.run_provision()
        self.assertEqual(code, 1)
        self.assertFalse(record["doctor"]["parsed"])

    def test_registry_sha_mismatch_is_a_problem(self):
        original = self.runner

        def lying(argv, *, env, cwd, timeout):
            result = original(argv, env=env, cwd=cwd, timeout=timeout)
            if "add" in argv and "cs2" in argv:
                path = Path(env["HOME"]) / ".img2/plugins.json"
                registry = json.loads(path.read_text())
                registry["plugins"][-1]["resolvedSha"] = "f" * 40
                path.write_text(json.dumps(registry))
            return result
        plugins = provision_home.parse_plugins(self.specs())
        code, record = provision_home.provision(
            home=self.home, harness_src=self.src, harness_ref=HARNESS_SHA, plugins=plugins,
            node=self.node, git="/usr/bin/git", runner=lying)
        self.assertEqual(code, 1)
        self.assertTrue(any("cs2" in problem for problem in record["problems"]))


def make_decision_workspace(workspace, *, decider="jev:jev-1.14.0", owner="pixi2d:" + "3" * 64):
    """Faithful but trimmed fixture of a real routed run (adversarial-route-card openmoji-1F7EB).

    Only the fields scripts/e2e/route_one.py reads are kept; shapes, key names and
    the content-addressed file naming are copied from that run's real artifacts.
    """
    meta = workspace / ".img2threejs"
    (meta / "route").mkdir(parents=True)
    (meta / "route-inputs/object-inventory").mkdir(parents=True)
    (meta / "decisions/routing").mkdir(parents=True)

    def store(directory, prefix, value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = meta / directory / (prefix + digest + ".json")
        path.write_bytes(raw)
        return ".img2threejs/%s/%s%s.json" % (directory, prefix, digest), digest

    inventory_ref, _ = store("route-inputs/object-inventory", "", {
        "kind": "img2.object-inventory", "version": 1, "imageSha256": "7" * 64,
        "objects": [{"objectId": "whole-image", "ownerRoute": owner, "label": "whole-image"}]})
    record_ref, record_digest = store("decisions/routing", "", {
        "kind": "img2.decision-record", "group": "routing", "decider": decider, "status": "answered",
        "usage": {"input_tokens": 2816, "output_tokens": 166}})
    decision_ref, decision_digest = store("route", "decision-", {
        "kind": "img2.route-decision", "version": 1, "runId": "run-1", "phase": "decided",
        "suspendedAt": None, "inventoryRef": inventory_ref, "objects": [{
            "objectId": "whole-image", "status": "resolved", "decider": decider, "selected": [owner],
            "consultations": [{"decider": decider, "decisionRef": record_ref, "outcome": "answered",
                               "reasonCode": "answered", "receiptSha256": record_digest}]}]})
    (meta / "route/journal.jsonl").write_text("{}\n")
    return {"kind": "img2.route-command", "version": 1, "mode": "decision-only", "status": "decided",
            "executionAuthorized": False, "decisionRef": decision_ref, "sha256": decision_digest}


class RouteOneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "iso"
        img2_home = self.home / "home/.img2"
        (img2_home / "harness/bin").mkdir(parents=True)
        (img2_home / "harness/bin/img2.mjs").write_bytes(b"launcher")
        node = self.base / "bin/node"
        node.parent.mkdir()
        node.write_bytes(b"node")
        self.jev = img2_home / "plugins/jev"
        (self.home / "provision.json").write_text(json.dumps({
            "status": "ok", "img2Home": str(img2_home),
            "pins": {"harnessRoot": str(img2_home / "harness"), "harnessSha": HARNESS_SHA,
                     "harnessExecutable": str(img2_home / "harness/bin/img2.mjs"),
                     "harnessExecutableSha256": "b" * 64, "node": str(node),
                     "plugins": {"jev": {"installedDir": str(self.jev)}}}}))
        self.image = self.base / "reference.png"
        self.image.write_bytes(PNG)
        self.workspace = self.base / "ws"
        self.calls = []
        self.status = (2, json.dumps({"kind": "img2.jev-command", "version": 1, "status": "request-input",
                                      "reasonCode": "consent-required"}), "")
        self.route = None

    def runner(self, argv, *, env, cwd, timeout):
        self.calls.append({"argv": list(argv), "env": env, "cwd": cwd, "timeout": timeout})
        if "decision-status" in argv:
            return self.status
        if str(route_one.ROUTE_SCRIPT) in argv:
            return 0 if self.route.get("status") == "decided" else 3, json.dumps(self.route), ""
        raise AssertionError("unexpected command %r" % argv)

    def run_main(self, *extra, kind="3d"):
        out = io.StringIO()
        argv = ["--home", str(self.home), "--workspace", str(self.workspace), "--image", str(self.image),
                "--label", "red-cube", "--kind", kind, "--run-id", "run-1", *extra]
        code = route_one.main(argv, runner=self.runner, out=out)
        return code, out.getvalue()

    def granted(self, budget=256000):
        self.status = (0, json.dumps({"kind": "img2.jev-command", "version": 1, "status": "consent-updated",
                                      "granted": True, "purposes": ["routing"], "tokenBudget": budget,
                                      "sha256": "c" * 64}), "")

    def test_not_authorized_prints_exact_consent_command_exits_20_and_never_routes(self):
        code, text = self.run_main()
        self.assertEqual(code, route_one.EXIT_CONSENT)
        self.assertEqual(code, 20)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("decision-status", self.calls[0]["argv"])
        self.assertFalse(any(str(route_one.ROUTE_SCRIPT) in call["argv"] for call in self.calls))
        workspace = str(self.workspace.resolve())
        self.assertIn("consent authorize-decisions --workspace %s --run-id run-1 --token-budget 256000 "
                      "--accept-image-disclosure --accept-paid-calls" % workspace, text)
        self.assertIn(str(self.jev / "tools/jev.py"), text)
        self.assertNotIn("consent-updated", text)

    def test_consent_command_honours_token_budget_argument(self):
        _, text = self.run_main("--token-budget", "512000")
        self.assertIn("--token-budget 512000 ", text)

    def test_budget_below_one_dispatch_is_refused(self):
        code, text = self.run_main("--token-budget", "1000")
        self.assertEqual(code, route_one.EXIT_REFUSED)
        self.assertEqual(self.calls, [])

    def test_declined_grant_is_not_authorized(self):
        self.status = (0, json.dumps({"kind": "img2.jev-command", "status": "consent-updated",
                                      "granted": False, "purposes": [], "tokenBudget": 256000}), "")
        code, text = self.run_main()
        self.assertEqual(code, 20)
        self.assertIn("declined", text)

    def test_grant_below_dispatch_reserve_is_not_authorized(self):
        self.granted(budget=1000)
        code, _ = self.run_main()
        self.assertEqual(code, 20)
        self.assertEqual(len(self.calls), 1)

    def test_probe_failure_is_not_authorized(self):
        self.status = (1, "", "traceback")
        code, _ = self.run_main()
        self.assertEqual(code, 20)
        self.assertEqual(len(self.calls), 1)

    def test_authorized_3d_route_argv_and_summary(self):
        self.granted()
        original = self.runner

        def routing(argv, **kwargs):
            if str(route_one.ROUTE_SCRIPT) in argv:
                self.route = make_decision_workspace(self.workspace.resolve())
            return original(argv, **kwargs)
        out = io.StringIO()
        code = route_one.main(["--home", str(self.home), "--workspace", str(self.workspace),
                               "--image", str(self.image), "--label", "red-cube", "--kind", "3d",
                               "--run-id", "run-1", "--intent", "rig"], runner=routing, out=out)
        self.assertEqual(code, 0, out.getvalue())
        route_call = self.calls[1]
        self.assertEqual(route_call["argv"][1:], [
            str(route_one.ROUTE_SCRIPT), "--workspace", str(self.workspace.resolve()), "--run-id", "run-1",
            "--image", "ref.png", "--objects", "objects.json", "--expected-output", "3d", "--intent", "rig",
            "--harness-root", str(self.home / "home/.img2/harness"), "--harness-sha", HARNESS_SHA,
            "--harness-executable", str(self.home / "home/.img2/harness/bin/img2.mjs"),
            "--harness-sha256", "b" * 64])
        self.assertEqual(route_call["env"]["IMG2_HOME"], str(self.home / "home/.img2"))
        proposals = json.loads((self.workspace / "objects.json").read_text())
        self.assertEqual(proposals["objects"], [{"objectId": "red-cube", "label": "red-cube", "role": "subject",
                                                  "approxBox": [0, 0, 1, 1], "observations": {}}])
        self.assertEqual((self.workspace / "ref.png").read_bytes(), PNG)
        text = out.getvalue()
        self.assertIn("status: decided", text)
        self.assertIn("owner route [whole-image]: pixi2d:" + "3" * 64, text)
        self.assertIn("decided by: Jev (jev-1.14.0)", text)
        self.assertIn("journal: %s" % (self.workspace.resolve() / ".img2threejs/route/journal.jsonl"), text)
        self.assertIn("decision record: .img2threejs/decisions/routing/", text)
        self.assertIn("tokens in/out=2816/166", text)
        self.assertIn("nothing was built", text)

    def test_2d_route_has_no_objects_file_and_no_objects_flag(self):
        self.granted()
        self.route = {"kind": "img2.route-command", "status": "request-input", "reasonCode": "explicit-2d-unavailable"}
        code, text = self.run_main(kind="2d")
        self.assertEqual(code, route_one.EXIT_UNRESOLVED)
        argv = self.calls[1]["argv"]
        self.assertNotIn("--objects", argv)
        self.assertEqual(argv[argv.index("--expected-output") + 1], "2d")
        self.assertFalse((self.workspace / "objects.json").exists())
        self.assertIn("reason: explicit-2d-unavailable", text)

    def test_credential_refusal_is_reported_with_login_command(self):
        self.granted()
        self.route = {"kind": "img2.route-command", "status": "request-input", "reasonCode": "credential-missing",
                      "requestedCommand": "img2-jev login"}
        code, text = self.run_main()
        self.assertEqual(code, 21)
        self.assertIn("requested command: img2-jev login", text)

    def test_fallback_model_is_labelled_not_jev(self):
        self.granted()
        original = self.runner

        def routing(argv, **kwargs):
            if str(route_one.ROUTE_SCRIPT) in argv:
                self.route = make_decision_workspace(self.workspace.resolve(), decider="model:host-model-1")
            return original(argv, **kwargs)
        out = io.StringIO()
        code = route_one.main(["--home", str(self.home), "--workspace", str(self.workspace), "--image", str(self.image),
                               "--label", "red-cube", "--kind", "3d", "--run-id", "run-1"], runner=routing, out=out)
        self.assertEqual(code, 0)
        self.assertIn("FALLBACK MODEL host-model-1 (not Jev; not Jev acceptance)", out.getvalue())

    def test_dry_run_executes_nothing_and_creates_no_workspace(self):
        code, text = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.workspace.exists())
        self.assertIn("--accept-image-disclosure", text)
        self.assertIn("--accept-paid-calls", text)
        self.assertIn("forge/route.py", text)
        self.assertIn("decision-status", text)

    def test_rerun_on_same_workspace_is_allowed_but_changed_input_is_refused(self):
        code, _ = self.run_main()
        self.assertEqual(code, 20)
        code, _ = self.run_main()
        self.assertEqual(code, 20)
        code, text = self.run_main("--intent", "rig")
        self.assertEqual(code, route_one.EXIT_REFUSED)
        self.assertIn("not empty", text)

    def test_foreign_non_empty_workspace_is_refused(self):
        self.workspace.mkdir()
        (self.workspace / "other.txt").write_text("x")
        code, _ = self.run_main()
        self.assertEqual(code, route_one.EXIT_REFUSED)
        self.assertEqual(self.calls, [])

    def test_failed_provisioning_record_is_refused(self):
        record = json.loads((self.home / "provision.json").read_text())
        record["status"] = "failed"
        (self.home / "provision.json").write_text(json.dumps(record))
        code, _ = self.run_main()
        self.assertEqual(code, route_one.EXIT_REFUSED)
        self.assertEqual(self.calls, [])

    def test_bad_label_and_non_image_are_refused(self):
        out = io.StringIO()
        code = route_one.main(["--home", str(self.home), "--workspace", str(self.workspace), "--image", str(self.image),
                               "--label", "Red Cube", "--kind", "3d", "--run-id", "r"], runner=self.runner, out=out)
        self.assertEqual(code, route_one.EXIT_REFUSED)
        self.image.write_bytes(b"GIF89a")
        code, _ = self.run_main()
        self.assertEqual(code, route_one.EXIT_REFUSED)

    def test_box_is_validated_and_written(self):
        self.assertEqual(route_one.parse_box("0.1,0.2,0.9,0.8"), [0.1, 0.2, 0.9, 0.8])
        for bad in ("1,2,3", "0.5,0,0.4,1", "a,b,c,d", "0,0,2,1"):
            with self.assertRaises(route_one.Refusal):
                route_one.parse_box(bad)
        self.run_main("--box", "0.1,0.2,0.9,0.8")
        proposals = json.loads((self.workspace / "objects.json").read_text())
        self.assertEqual(proposals["objects"][0]["approxBox"], [0.1, 0.2, 0.9, 0.8])


if __name__ == "__main__":
    unittest.main()
