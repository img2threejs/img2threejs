"""Offline install readiness, with registry files and stubbed discovery boundaries."""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
SPEC = importlib.util.spec_from_file_location("img2_preflight", FORGE / "preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.harness = self.home / "harness"
        self.harness.mkdir()
        self.pins = {name: str(i + 1) * 40 for i, name in enumerate(preflight.CAPABILITIES)}
        self.registry = {"version": 1, "plugins": [
            {"id": name, "resolvedSha": sha} for name, sha in self.pins.items()]}
        self.lock = {"schema": 1, "plugins": [
            {"id": name, "commit": sha} for name, sha in self.pins.items()]}
        self.save()
        self.discovery = preflight.Discovery(SimpleNamespace(workspace=self.home))
        self.rows = {}
        self.discovery.query = Mock(side_effect=self.query)
        self.discovery._pinned_file = Mock(side_effect=self.pinned_file)
        self.discovery.dependency = Mock(return_value=True)
        self.check_jev = Mock(return_value=None)
        self.selectors = {"selectedPlugins": list(preflight.CAPABILITIES)}

    def save(self):
        (self.home / "plugins.json").write_text(json.dumps(self.registry))
        (self.harness / "plugins.lock.json").write_text(json.dumps(self.lock))

    def query(self, edge):
        name = next((name for name, value in preflight.EDGES.items() if value == edge), "jev")
        row = self.rows.setdefault(name, {"plugin": name, "resolvedSha": self.pins[name],
                                          "dir": str(self.home / "plugins" / name)})
        return {"status": "answered", "providers": [copy.deepcopy(row)]}

    def pinned_file(self, row, relative):
        name = row["plugin"]
        if relative == "plugin.json":
            edge = preflight.JEV_EDGE if name == "jev" else preflight.EDGES[name]
            return json.dumps({"name": name, "capabilities": [{"from": edge[0], "to": edge[1]}]}).encode()
        return json.dumps({"deps": [{"probe": "node>=18.0"}]}).encode()

    def check(self, **kwargs):
        return preflight.check_plugins(self.discovery, home=self.home, harness=self.harness,
                                       selectors=self.selectors, check_jev=self.check_jev, **kwargs)

    def main_result(self, selectors=None, auth_status="present"):
        output = io.StringIO()
        with patch.object(preflight, "img2_home", return_value=self.home), \
                patch.object(preflight, "installed_harness_root", return_value=self.harness), \
                patch.object(preflight, "read_selectors", return_value=selectors), \
                patch.object(preflight, "Discovery", return_value=self.discovery), \
                patch.object(preflight, "LiveConsent") as consent, \
                patch.object(preflight, "_approved_lock", return_value={"resolvedSha": self.pins["jev"]}), \
                redirect_stdout(output):
            consent.return_value.auth_status.return_value = {"status": auth_status}
            code = preflight.main([])
        return code, output.getvalue()

    def test_all_ready_and_default_all_cli(self):
        rows = self.check()
        self.assertEqual([row[2] for row in rows], ["ready"] * 5)
        self.assertIn("character-bpy>=4.3", [c.args[0] for c in self.discovery.dependency.call_args_list])
        code, output = self.main_result()
        self.assertEqual(code, 0)
        self.assertIn("capability name", output)
        self.assertIn("rigged character", output)
        self.assertEqual(output.count("fix: img2threejs repair"), 1)
        self.assertFalse((self.home / ".img2threejs").exists())

    def test_missing_plugin_and_wrong_pin_are_red(self):
        for change, reason in (("missing", "plugin-not-registered"), ("pin", "plugin-pin-mismatch")):
            with self.subTest(change=change):
                original = copy.deepcopy(self.registry)
                if change == "missing":
                    self.registry["plugins"] = [r for r in self.registry["plugins"] if r["id"] != "character"]
                else:
                    self.registry["plugins"][0]["resolvedSha"] = "f" * 40
                self.save()
                code, output = self.main_result()
                self.assertEqual(code, 1)
                self.assertIn(reason, output)
                self.registry = original

    def test_missing_dependency_names_probe(self):
        self.discovery.dependency.side_effect = lambda probe: probe != "character-bpy>=4.3"
        code, output = self.main_result()
        self.assertEqual(code, 1)
        self.assertIn("dependency-missing:character-bpy>=4.3", output)

    def test_unselected_plugins_are_not_probed_or_required(self):
        self.registry["plugins"] = [r for r in self.registry["plugins"] if r["id"] == "pixi2d"]
        self.save()
        selection = {"selectedPlugins": ["pixi2d"]}
        code, output = self.main_result(selection)
        self.assertEqual(code, 0)
        self.assertEqual(output.count("not installed by choice"), 4)
        self.assertEqual([call.args[0] for call in self.discovery.query.call_args_list], [preflight.EDGES["pixi2d"]])

    def test_invalid_selectors_do_not_silently_change_selection(self):
        (self.home / "runtime").mkdir()
        (self.home / "runtime/selectors.json").write_text("invalid")
        code, output = self.main_result()
        self.assertEqual(code, 1)
        self.assertEqual(output.count("selectors-invalid"), 5)
        self.discovery.query.assert_not_called()

    def test_credential_missing_names_login_without_no_account_inference(self):
        with patch.object(preflight, "LiveConsent") as consent, \
                patch.object(preflight, "_approved_lock", return_value={"resolvedSha": self.pins["jev"]}):
            consent.return_value.auth_status.return_value = {"status": "credential-missing"}
            rows = preflight.check_plugins(self.discovery, home=self.home, harness=self.harness,
                                          selectors=self.selectors)
        jev = next(row for row in rows if row[0] == "jev")
        self.assertEqual(jev[2], "blocked")
        self.assertIn("run img2-jev login", jev[3])
        self.assertIn("no-account choice is not implemented", jev[3])
        code, output = self.main_result(auth_status="credential-missing")
        self.assertEqual(code, 1)
        self.assertIn("credential-missing; run img2-jev login", output)

    def test_jev_core_pin_mismatch_never_runs_auth(self):
        with patch.object(preflight, "LiveConsent") as consent, \
                patch.object(preflight, "_approved_lock", return_value={"resolvedSha": "f" * 40}):
            reason = preflight.jev_status(self.discovery, self.query(preflight.JEV_EDGE)["providers"][0])
        self.assertIn("core-release-pin-mismatch", reason)
        consent.assert_not_called()

    def test_malformed_registry_duplicate_and_ambiguous_provider_block(self):
        self.registry["plugins"].append(copy.deepcopy(self.registry["plugins"][0]))
        self.save()
        self.assertTrue(all(row[2] == "blocked" for row in self.check()))
        self.registry["plugins"].pop()
        self.save()
        self.discovery.query.return_value = {"status": "ambiguous", "providers": [{}, {}]}
        self.discovery.query.side_effect = None
        self.assertTrue(all("provider-ambiguous" == row[3] for row in self.check()))

    def test_tampered_blocking_gate_producer_is_pin_mismatch_not_ready(self):
        plugin = self.home / "plugins" / "pixi2d"
        (plugin / "tools").mkdir(parents=True)
        files = {"plugin.json": self.pinned_file({"plugin": "pixi2d"}, "plugin.json"),
                 "route.json": self.pinned_file({"plugin": "pixi2d"}, "route.json"),
                 "gates.json": json.dumps([{"blocking": True, "actor": "program",
                                            "command": "python3 {plugin_dir}/tools/pixi2d.py"}]).encode(),
                 "tools/pixi2d.py": b"print('gate')\n"}
        for relative, data in files.items():
            (plugin / relative).write_bytes(data)
        git = ("git", "-C", str(plugin), "-c", "user.name=t", "-c", "user.email=t@t")
        for command in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "pin")):
            subprocess.run((*git, *command), check=True, capture_output=True)
        sha = subprocess.run((*git, "rev-parse", "HEAD"), check=True, capture_output=True,
                             text=True).stdout.strip()
        self.pins["pixi2d"] = sha
        self.registry["plugins"][-1]["resolvedSha"] = sha
        self.lock["plugins"][-1]["commit"] = sha
        self.save()
        self.rows["pixi2d"] = {"plugin": "pixi2d", "resolvedSha": sha, "dir": str(plugin),
                               "gateRunner": {"argv": ["python3"]}}
        self.discovery._pinned_file = preflight.Discovery._pinned_file.__get__(self.discovery)
        pixi = lambda: next(row for row in self.check() if row[0] == "pixi2d")
        self.assertEqual(pixi()[2:], ("ready", "pinned; dependencies available"))
        (plugin / "tools/pixi2d.py").write_bytes(b"print('tampered')\n")
        self.assertEqual(pixi()[2:], ("blocked", "provider-pin-mismatch"))


if __name__ == "__main__":
    unittest.main()
