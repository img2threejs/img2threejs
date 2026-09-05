"""forge/_shared/targets.py resolution tests (task 3.12, `establish-the-emission-target-contract`).

Every test here pins IMG2_HOME to a temp/empty directory (the `test_search_specs.py:243` pattern) --
the un-isolated registry read is exactly what turned PR #106's CI red.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))

import targets  # noqa: E402


def _write_plugin(home: Path, plugin_id: str, *, capabilities, steps, requires=None, harness_version="0.5.0"):
    (home / "harness").mkdir(parents=True, exist_ok=True)
    (home / "harness" / "package.json").write_text(json.dumps({"version": harness_version}), encoding="utf-8")
    plugin_dir = home / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1,
        "name": plugin_id,
        "version": "0.1.0",
        "description": "test fixture plugin",
        "capabilities": capabilities,
        "requires": requires or {"harness": ">=0.1.0", "coreApi": 1},
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "steps.json").write_text(json.dumps(steps), encoding="utf-8")

    registry_path = home / "plugins.json"
    registry = {"plugins": []}
    if registry_path.is_file():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["plugins"].append({
        "id": plugin_id, "repo": f"img2threejs/{plugin_id}", "ref": "v0.1.0",
        "resolvedSha": f"sha-{plugin_id}", "addedAt": "2026-01-01",
    })
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


def _target_step(kind: str, *, path: str, deterministic=True, timeout=None, version=1):
    row = {
        "id": f"emit-{kind}",
        "command": "true",
        "after": [],
        "provides": {"version": version, "from": "sculpt-spec", "to": kind, "artifact": {"kind": kind, "path": path}},
        "deterministic": deterministic,
    }
    if timeout is not None:
        row["timeoutSeconds"] = timeout
    return row


class TargetsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "img2home-empty"
        self.home.mkdir()


class NoRegistryResolvesToTheReferenceTargetOnly(TargetsTestBase):
    def test_empty_home_still_resolves_threejs_ts(self):
        target = targets.resolve_target("threejs-ts", home=self.home)
        self.assertIsNone(target.plugin_id)
        self.assertEqual(target.kind, "threejs-ts")

    def test_empty_home_refuses_an_unknown_kind_naming_it(self):
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("glb", home=self.home)
        self.assertIn("glb", str(ctx.exception))


class PluginTargetResolution(TargetsTestBase):
    def test_installed_plugin_resolves_by_kind(self):
        _write_plugin(
            self.home, "echo-plugin",
            capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/echo-plugin/echo.json")],
        )
        target = targets.resolve_target("echo", home=self.home)
        self.assertEqual(target.plugin_id, "echo-plugin")
        self.assertEqual(target.artifact_path, ".img2/artifacts/echo-plugin/echo.json")
        self.assertEqual(target.resolved_sha, "sha-echo-plugin")
        self.assertEqual(target.plugin_version, "0.1.0")

    def test_two_plugins_providing_one_kind_is_refused_naming_both(self):
        _write_plugin(
            self.home, "echo-a", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/echo-a/echo.json")],
        )
        _write_plugin(
            self.home, "echo-b", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/echo-b/echo.json")],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        message = str(ctx.exception)
        self.assertIn("echo-a", message)
        self.assertIn("echo-b", message)

    def test_plugin_flag_disambiguates(self):
        _write_plugin(
            self.home, "echo-a", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/echo-a/echo.json")],
        )
        _write_plugin(
            self.home, "echo-b", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/echo-b/echo.json")],
        )
        target = targets.resolve_target("echo", plugin="echo-b", home=self.home)
        self.assertEqual(target.plugin_id, "echo-b")

    def test_a_missing_plugin_flag_target_is_refused_naming_it(self):
        _write_plugin(
            self.home, "echo-a", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/echo-a/echo.json")],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", plugin="not-installed", home=self.home)
        self.assertIn("not-installed", str(ctx.exception))

    def test_capability_edge_with_no_matching_step_is_refused(self):
        _write_plugin(
            self.home, "half-declared", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("no installed plugin provides", str(ctx.exception))

    def test_step_provides_a_kind_with_no_manifest_edge_is_refused(self):
        _write_plugin(
            self.home, "mismatched", capabilities=[{"from": "sculpt-spec", "to": "other-kind"}],
            steps=[_target_step("echo", path=".img2/artifacts/mismatched/echo.json")],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("no matching manifest capability edge", str(ctx.exception))

    def test_provides_version_newer_than_max_is_refused(self):
        _write_plugin(
            self.home, "too-new", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/too-new/echo.json", version=99)],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("newer than this base reads", str(ctx.exception))

    def test_escaping_artifact_path_is_refused(self):
        _write_plugin(
            self.home, "escaper", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path="../../etc/passwd")],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("must resolve under", str(ctx.exception))

    def test_missing_deterministic_declaration_is_refused(self):
        _write_plugin(
            self.home, "no-determinism", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[{
                "id": "emit-echo", "command": "true", "after": [],
                "provides": {"version": 1, "from": "sculpt-spec", "to": "echo",
                             "artifact": {"kind": "echo", "path": ".img2/artifacts/no-determinism/echo.json"}},
            }],
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("deterministic", str(ctx.exception))

    def test_harness_version_mismatch_is_refused(self):
        _write_plugin(
            self.home, "too-strict", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/too-strict/echo.json")],
            requires={"harness": ">=99.0.0", "coreApi": 1},
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("requires harness", str(ctx.exception))

    def test_core_api_mismatch_is_refused(self):
        _write_plugin(
            self.home, "wrong-core-api", capabilities=[{"from": "sculpt-spec", "to": "echo"}],
            steps=[_target_step("echo", path=".img2/artifacts/wrong-core-api/echo.json")],
            requires={"harness": ">=0.1.0", "coreApi": 2},
        )
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("coreApi", str(ctx.exception))

    def test_non_sculpt_spec_capabilities_are_ignored(self):
        # A plugin providing a capability from an unrelated kind (e.g. image -> threejs-code, the
        # old hello-cube shape) is not a target and must not appear in target resolution at all.
        _write_plugin(
            self.home, "not-a-target", capabilities=[{"from": "image", "to": "threejs-code"}],
            steps=[{"id": "step", "command": "true", "after": []}],
        )
        with self.assertRaises(targets.TargetResolutionError):
            targets.resolve_target("threejs-code", home=self.home)


class MalformedRegistryFailsLoudOnlyWhenAskedFor(TargetsTestBase):
    """D5: fail-loud is scoped to selection time. `resolve_target` (called only when --target was
    requested) DOES read a malformed registry and must fail loud naming the problem -- this module
    never silently swallows it. The no-target path never calling into this module at all is
    emit_target.py's own responsibility, tested in test_emit_target.py."""

    def test_malformed_plugins_json_fails_loud_when_a_target_is_requested(self):
        (self.home / "plugins.json").write_text("not valid json {{{", encoding="utf-8")
        with self.assertRaises(targets.TargetResolutionError) as ctx:
            targets.resolve_target("echo", home=self.home)
        self.assertIn("not valid JSON", str(ctx.exception))


class PathEscapesMirrorsTheHarness(unittest.TestCase):
    """Mirrors img2.mjs's artifactPathEscapes -- the doctor-time and runtime checks must never
    disagree about what escapes."""

    def test_a_conforming_path_does_not_escape(self):
        self.assertFalse(targets.path_escapes(".img2/artifacts/echo/echo.json", "echo"))

    def test_an_absolute_path_escapes(self):
        self.assertTrue(targets.path_escapes("/etc/passwd", "echo"))

    def test_a_tilde_path_escapes(self):
        self.assertTrue(targets.path_escapes("~/echo.json", "echo"))

    def test_a_dotdot_escape_is_refused(self):
        self.assertTrue(targets.path_escapes("../../etc/passwd", "echo"))

    def test_another_plugins_prefix_escapes(self):
        self.assertTrue(targets.path_escapes(".img2/artifacts/other-plugin/echo.json", "echo"))

    def test_a_bare_filename_with_no_prefix_escapes(self):
        # The full workspace-relative prefix is part of the declared value itself (D6) -- a bare
        # filename is not silently joined under the prefix, it is refused.
        self.assertTrue(targets.path_escapes("echo.json", "echo"))


if __name__ == "__main__":
    unittest.main()
