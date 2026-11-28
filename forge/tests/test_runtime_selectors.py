"""Provisioned selectors.json: strict reader and the discovery fallback built on it."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
import runtime_selectors
from runtime_selectors import img2_home, installed_harness_root, read_selectors
from stage0_route import discovery
discovery_module = discovery
from decision_control import DecisionController
from stage0_route.storage import RouteError
from stage0_route.discovery import (Discovery, EDGES, JEV_EDGE, _selector, character_three_facts,
                                    resolve_character_executable)

DEPENDENCIES_LOCK = b'{"schema":1,"dependencies":[]}\n'
PLUGINS_LOCK = b'{"schema":1,"plugins":[]}\n'
THREE_FILES = ("build/three.core.js", "build/three.module.js",
               "examples/jsm/loaders/GLTFLoader.js", "examples/jsm/utils/BufferGeometryUtils.js",
               "examples/jsm/utils/SkeletonUtils.js", "package.json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SelectorsCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.top = Path(self._tmp.name)
        self.home = self.top / "home"
        self.runtime = self.home / "runtime"
        self.harness = self.home / "harness"
        self.runtime.mkdir(parents=True)
        self.harness.mkdir()
        (self.harness / "dependencies.lock.json").write_bytes(DEPENDENCIES_LOCK)
        (self.harness / "plugins.lock.json").write_bytes(PLUGINS_LOCK)
        self.node = self.runtime / "node" / "bin" / "node"
        self.node.parent.mkdir(parents=True)
        self.node.write_text("#!/bin/sh\n")
        self.node.chmod(0o755)
        patcher = patch.dict(os.environ, {"IMG2_HOME": str(self.home)}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def document(self, **env):
        return {"schema": 1, "lockDigest": sha(DEPENDENCIES_LOCK),
                "pluginsLockDigest": sha(PLUGINS_LOCK),
                "selectedPlugins": ["character", "cs2", "interior", "jev", "pixi2d"],
                "env": env or {"IMG2_CHARACTER_NODE": str(self.node)},
                "createdAt": "2026-11-22T10:00:00Z"}

    def write(self, value, *, mode=0o600, raw=False):
        path = self.runtime / "selectors.json"
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_bytes(value if raw else json.dumps(value).encode())
        path.chmod(mode)
        return path


class ReadSelectorsTest(SelectorsCase):
    def test_valid_document_is_returned_and_never_cached(self):
        document = self.document()
        self.write(document)
        self.assertEqual(read_selectors(), document)
        self.write({**document, "selectedPlugins": []})
        self.assertEqual(read_selectors()["selectedPlugins"], [])
        (self.runtime / "selectors.json").unlink()
        self.assertIsNone(read_selectors())

    def test_every_allowed_key_and_timestamp_shape_is_accepted(self):
        for name in runtime_selectors.PATH_KEYS:
            (self.runtime / name).write_text("x")
        env = {name: str(self.runtime / name) for name in runtime_selectors.PATH_KEYS}
        env["IMG2_CHARACTER_THREE_SHA256"] = "a" * 64
        for stamp in ("2026-11-22T10:00:00Z", "2026-11-22T10:00:00.123Z", "2026-11-22T10:00:00+00:00"):
            with self.subTest(stamp=stamp):
                self.write({**self.document(), "env": env, "createdAt": stamp})
                self.assertEqual(read_selectors()["env"], env)

    def test_missing_file_and_empty_env_and_subset_plugins(self):
        self.assertIsNone(read_selectors())
        self.write({**self.document(), "env": {}, "selectedPlugins": ["jev"]})
        self.assertEqual(read_selectors()["env"], {})

    def test_whole_file_is_rejected_for_any_invalid_field(self):
        good = self.document()
        outside = self.top / "outside-node"
        outside.write_text("x")
        mutations = {
            "schema-0": lambda d: d.update(schema=0), "schema-2": lambda d: d.update(schema=2),
            "schema-bool": lambda d: d.update(schema=True), "schema-float": lambda d: d.update(schema=1.0),
            "schema-str": lambda d: d.update(schema="1"),
            "schema-missing": lambda d: d.pop("schema"),
            "lock-missing": lambda d: d.pop("lockDigest"),
            "lock-short": lambda d: d.update(lockDigest="a" * 63),
            "lock-upper": lambda d: d.update(lockDigest=sha(DEPENDENCIES_LOCK).upper()),
            "lock-nonhex": lambda d: d.update(lockDigest="g" * 64),
            "lock-int": lambda d: d.update(lockDigest=1),
            "lock-wrong": lambda d: d.update(lockDigest="0" * 64),
            "plugins-lock-missing": lambda d: d.pop("pluginsLockDigest"),
            "plugins-lock-short": lambda d: d.update(pluginsLockDigest="a" * 65),
            "plugins-lock-wrong": lambda d: d.update(pluginsLockDigest="0" * 64),
            "selected-missing": lambda d: d.pop("selectedPlugins"),
            "selected-str": lambda d: d.update(selectedPlugins="character"),
            "selected-dup": lambda d: d.update(selectedPlugins=["jev", "jev"]),
            "selected-unknown": lambda d: d.update(selectedPlugins=["character", "other"]),
            "selected-nonstr": lambda d: d.update(selectedPlugins=[1]),
            "selected-tuple-like-dict": lambda d: d.update(selectedPlugins={"jev": 1}),
            "env-missing": lambda d: d.pop("env"),
            "env-list": lambda d: d.update(env=[]),
            "env-extra-key": lambda d: d["env"].update(PATH="/usr/bin"),
            "env-lowercase-key": lambda d: d.update(env={"img2_character_node": str(self.node)}),
            "env-nonstring": lambda d: d.update(env={"IMG2_CHARACTER_NODE": 1}),
            "env-empty-string": lambda d: d.update(env={"IMG2_CHARACTER_NODE": ""}),
            "env-null": lambda d: d.update(env={"IMG2_CHARACTER_NODE": None}),
            "env-relative": lambda d: d.update(env={"IMG2_CHARACTER_NODE": "node/bin/node"}),
            "env-missing-path": lambda d: d.update(env={"IMG2_CHARACTER_NODE": str(self.runtime / "none")}),
            "env-outside-runtime": lambda d: d.update(env={"IMG2_CHARACTER_NODE": str(outside)}),
            "env-runtime-itself": lambda d: d.update(env={"IMG2_CHARACTER_NODE": str(self.runtime)}),
            "env-dotdot": lambda d: d.update(
                env={"IMG2_CHARACTER_NODE": str(self.runtime / "node" / ".." / "node" / "bin" / "node")}),
            "env-sibling-prefix": lambda d: d.update(env={"IMG2_CHARACTER_NODE": str(self.runtime) + "-x"}),
            "env-nul": lambda d: d.update(env={"IMG2_CHARACTER_NODE": str(self.node) + "\0"}),
            "env-sha-short": lambda d: d.update(env={"IMG2_CHARACTER_THREE_SHA256": "a" * 63}),
            "env-sha-upper": lambda d: d.update(env={"IMG2_CHARACTER_THREE_SHA256": "A" * 64}),
            "env-sha-path": lambda d: d.update(env={"IMG2_CHARACTER_THREE_SHA256": str(self.node)}),
            "created-missing": lambda d: d.pop("createdAt"),
            "created-nonstring": lambda d: d.update(createdAt=1795000000),
            "created-no-zone": lambda d: d.update(createdAt="2026-11-22T10:00:00"),
            "created-offset": lambda d: d.update(createdAt="2026-11-22T10:00:00+01:00"),
            "created-date-only": lambda d: d.update(createdAt="2026-11-22"),
            "created-bad-month": lambda d: d.update(createdAt="2026-13-22T10:00:00Z"),
            "created-bad-day": lambda d: d.update(createdAt="2026-02-30T10:00:00Z"),
            "created-bad-hour": lambda d: d.update(createdAt="2026-11-22T25:00:00Z"),
            "created-trailing": lambda d: d.update(createdAt="2026-11-22T10:00:00Z "),
            "extra-field": lambda d: d.update(extra=1),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                document = copy.deepcopy(good)
                mutate(document)
                self.write(document)
                self.assertIsNone(read_selectors())

    def test_symlink_inside_runtime_that_stays_inside_is_allowed_but_escape_is_not(self):
        inside = self.runtime / "node-link"
        inside.symlink_to(self.node)
        self.write(self.document(IMG2_CHARACTER_NODE=str(inside)))
        self.assertIsNotNone(read_selectors())
        outside = self.top / "outside-node"
        outside.write_text("x")
        escape = self.runtime / "escape"
        escape.symlink_to(outside)
        self.write(self.document(IMG2_CHARACTER_NODE=str(escape)))
        self.assertIsNone(read_selectors())
        directory_escape = self.runtime / "dir-escape"
        directory_escape.symlink_to(self.top, target_is_directory=True)
        self.write(self.document(IMG2_CHARACTER_NODE=str(directory_escape / "outside-node")))
        self.assertIsNone(read_selectors())

    def test_unparseable_json_is_rejected(self):
        duplicate = (b'{"schema":1,"schema":1,"lockDigest":"%s","pluginsLockDigest":"%s",'
                     b'"selectedPlugins":[],"env":{},"createdAt":"2026-11-22T10:00:00Z"}'
                     % (sha(DEPENDENCIES_LOCK).encode(), sha(PLUGINS_LOCK).encode()))
        nested_duplicate = (b'{"schema":1,"lockDigest":"%s","pluginsLockDigest":"%s",'
                            b'"selectedPlugins":[],"env":{"IMG2_CHARACTER_NODE":"/a","IMG2_CHARACTER_NODE":"/a"},'
                            b'"createdAt":"2026-11-22T10:00:00Z"}'
                            % (sha(DEPENDENCIES_LOCK).encode(), sha(PLUGINS_LOCK).encode()))
        base = json.dumps(self.document(env={})).encode()
        cases = {"duplicate": duplicate, "nested-duplicate": nested_duplicate, "empty": b"",
                 "garbage": b"not json", "array": b"[]", "null": b"null", "bad-utf8": b"\xff\xfe",
                 "nan": base.replace(b'"schema": 1', b'"schema": NaN'),
                 "infinity": base.replace(b'"selectedPlugins": [', b'"selectedPlugins": [Infinity,'),
                 "neg-infinity": base.replace(b'"env": {}', b'"env": {"x": -Infinity}'),
                 "trailing": base + b" x", "truncated": base[:-3]}
        for name, raw in cases.items():
            with self.subTest(name):
                self.write(raw, raw=True)
                self.assertIsNone(read_selectors())

    def test_lock_digests_are_checked_against_the_installed_harness_bytes(self):
        self.write(self.document())
        self.assertIsNotNone(read_selectors())
        (self.harness / "dependencies.lock.json").write_bytes(DEPENDENCIES_LOCK + b" ")
        self.assertIsNone(read_selectors())
        (self.harness / "dependencies.lock.json").write_bytes(DEPENDENCIES_LOCK)
        (self.harness / "plugins.lock.json").write_bytes(PLUGINS_LOCK + b" ")
        self.assertIsNone(read_selectors())
        (self.harness / "plugins.lock.json").unlink()
        self.assertIsNone(read_selectors())
        (self.harness / "plugins.lock.json").write_bytes(PLUGINS_LOCK)
        self.assertIsNotNone(read_selectors())
        for name in ("dependencies.lock.json", "plugins.lock.json"):
            path = self.harness / name
            data = path.read_bytes()
            path.unlink()
            path.symlink_to(self.top / "elsewhere")
            (self.top / "elsewhere").write_bytes(data)
            with self.subTest(name):
                self.assertIsNone(read_selectors())
            path.unlink()
            path.write_bytes(data)

    def test_harness_root_honours_override_and_missing_harness_is_unset(self):
        self.write(self.document())
        moved = self.top / "other-harness"
        self.harness.rename(moved)
        self.assertIsNone(read_selectors())
        with patch.dict(os.environ, {"IMG2_HARNESS_DIR": str(moved)}):
            self.assertIsNotNone(read_selectors())
        with patch.dict(os.environ, {"IMG2_HARNESS_DIR": "relative/harness"}):
            self.assertIsNone(read_selectors())

    def test_file_permissions_ownership_and_type_are_enforced(self):
        for mode in (0o644, 0o640, 0o660, 0o400, 0o700, 0o666):
            with self.subTest(mode=oct(mode)):
                self.write(self.document(), mode=mode)
                self.assertIsNone(read_selectors())
        self.write(self.document())
        self.assertIsNotNone(read_selectors())
        with patch.object(runtime_selectors, "_current_uid", return_value=os.geteuid() + 1):
            self.assertIsNone(read_selectors())

    def test_selectors_symlink_fifo_directory_and_oversize_are_refused(self):
        path = self.runtime / "selectors.json"
        real = self.top / "real-selectors.json"
        real.write_bytes(json.dumps(self.document()).encode())
        real.chmod(0o600)
        path.symlink_to(real)
        self.assertIsNone(read_selectors())
        path.unlink()
        os.mkfifo(path, 0o600)
        self.assertIsNone(read_selectors())
        path.unlink()
        path.mkdir()
        self.assertIsNone(read_selectors())
        path.rmdir()
        self.write(b" " * (runtime_selectors._SELECTORS_LIMIT + 1), raw=True)
        self.assertIsNone(read_selectors())

    def test_runtime_directory_symlink_escape_is_refused(self):
        self.write(self.document())
        elsewhere = self.top / "elsewhere-runtime"
        self.runtime.rename(elsewhere)
        self.runtime.symlink_to(elsewhere, target_is_directory=True)
        self.assertIsNone(read_selectors())

    def test_home_must_exist_and_be_absolute(self):
        with patch.dict(os.environ, {"IMG2_HOME": str(self.top / "nowhere")}):
            self.assertIsNone(read_selectors())
        with patch.dict(os.environ, {"IMG2_HOME": "relative"}):
            self.assertIsNone(read_selectors())

    def test_symlinked_home_is_accepted_when_paths_are_lexically_under_it(self):
        alias = self.top / "alias"
        alias.symlink_to(self.home, target_is_directory=True)
        document = self.document(IMG2_CHARACTER_NODE=str(alias / "runtime" / "node" / "bin" / "node"))
        self.write(document)
        with patch.dict(os.environ, {"IMG2_HOME": str(alias)}):
            self.assertEqual(read_selectors(), document)

    def test_home_helpers(self):
        self.assertEqual(img2_home(), self.home)
        self.assertEqual(installed_harness_root(), self.home / "harness")
        with patch.dict(os.environ, {"IMG2_HARNESS_DIR": "/custom/harness"}):
            self.assertEqual(installed_harness_root(), Path("/custom/harness"))
        with patch.dict(os.environ, {"IMG2_HOME": ""}):
            self.assertEqual(img2_home(), Path.home() / ".img2")
            self.assertEqual(installed_harness_root(), Path.home() / ".img2" / "harness")
        with patch.dict(os.environ, {"IMG2_HARNESS_DIR": ""}):
            self.assertEqual(installed_harness_root(), self.home / "harness")


class DiscoverySelectorTest(SelectorsCase):
    def test_environment_wins_including_empty(self):
        self.write(self.document())
        with patch.dict(os.environ, {"IMG2_CHARACTER_NODE": "/real/env/node"}):
            self.assertEqual(_selector("IMG2_CHARACTER_NODE"), "/real/env/node")
        with patch.dict(os.environ, {"IMG2_CHARACTER_NODE": ""}):
            self.assertEqual(_selector("IMG2_CHARACTER_NODE"), "")
            self.assertIsNone(resolve_character_executable("character-node"))

    def test_file_fills_only_unset_allowlisted_keys(self):
        self.write(self.document())
        self.assertEqual(_selector("IMG2_CHARACTER_NODE"), str(self.node))
        self.assertIsNone(_selector("IMG2_CHARACTER_BROWSER"))
        self.assertIsNone(_selector("PATH_NOT_ALLOWED"))
        self.assertIsNone(_selector("HOME_NOT_ALLOWED"))

    def test_invalid_file_leaves_every_selector_unset(self):
        self.write({**self.document(), "lockDigest": "0" * 64})
        self.assertIsNone(_selector("IMG2_CHARACTER_NODE"))

    def test_resolve_character_executable_uses_file_then_prefers_environment(self):
        self.write(self.document())
        self.assertEqual(resolve_character_executable("character-node"), str(self.node.resolve()))
        other = self.top / "env-node"
        other.write_text("#!/bin/sh\n")
        other.chmod(0o755)
        with patch.dict(os.environ, {"IMG2_CHARACTER_NODE": str(other)}):
            self.assertEqual(resolve_character_executable("character-node"), str(other.resolve()))

    def test_unset_selector_still_falls_back_to_path(self):
        found = self.top / "bin" / "node"
        found.parent.mkdir()
        found.write_text("#!/bin/sh\n")
        found.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(found.parent)}):
            self.assertEqual(resolve_character_executable("character-node"), str(found.resolve()))
            self.write({**self.document(), "lockDigest": "0" * 64})
            self.assertEqual(resolve_character_executable("character-node"), str(found.resolve()))

    def test_file_selector_that_is_not_executable_is_unresolved_not_path_fallback(self):
        plain = self.runtime / "plain"
        plain.write_text("x")
        plain.chmod(0o644)
        found = self.top / "bin" / "node"
        found.parent.mkdir()
        found.write_text("#!/bin/sh\n")
        found.chmod(0o755)
        self.write(self.document(IMG2_CHARACTER_NODE=str(plain)))
        with patch.dict(os.environ, {"PATH": str(found.parent)}):
            self.assertIsNone(resolve_character_executable("character-node"))

    def test_bpy_and_browser_selectors_come_from_the_file(self):
        bpy = self.runtime / "bpy-venv" / "bin" / "python"
        browser = self.runtime / "chromium" / "chrome"
        for path in (bpy, browser):
            path.parent.mkdir(parents=True)
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
        self.write(self.document(IMG2_CHARACTER_BPY_PYTHON=str(bpy), IMG2_CHARACTER_BROWSER=str(browser)))
        self.assertEqual(resolve_character_executable("character-bpy"), str(bpy.parent.resolve() / bpy.name))
        self.assertEqual(resolve_character_executable("character-chromium"), str(browser.resolve()))

    def three_fixture(self):
        root = self.runtime / "three"
        for name in THREE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'{"name":"three","version":"0.186.1"}' if name == "package.json"
                             else ("// fixture " + name).encode())
        inventory = {"kind": "img2.character-three-package", "version": 1, "files": [
            {"path": name, "sha256": sha((root / name).read_bytes())} for name in sorted(THREE_FILES)]}
        pin = sha(json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode())
        return root, pin

    def test_character_three_facts_accepts_file_selectors_and_keeps_its_own_authority(self):
        root, pin = self.three_fixture()
        self.write(self.document(IMG2_CHARACTER_THREE_ROOT=str(root), IMG2_CHARACTER_THREE_SHA256=pin))
        facts = character_three_facts()
        self.assertEqual((facts["root"], facts["sha256"], facts["approval"]),
                         (str(root.resolve()), pin, "host-approved"))
        # A digest that is well-formed but wrong passes selectors validation yet not the facts check.
        self.write(self.document(IMG2_CHARACTER_THREE_ROOT=str(root), IMG2_CHARACTER_THREE_SHA256="0" * 64))
        self.assertIsNotNone(read_selectors())
        self.assertIsNone(character_three_facts())
        # Tampered bytes are caught by character_three_facts, not by the selectors reader.
        self.write(self.document(IMG2_CHARACTER_THREE_ROOT=str(root), IMG2_CHARACTER_THREE_SHA256=pin))
        (root / "build/three.core.js").write_bytes(b"tampered")
        self.assertIsNotNone(read_selectors())
        self.assertIsNone(character_three_facts())

    def test_environment_overrides_one_three_key_without_mixing_trust(self):
        root, pin = self.three_fixture()
        self.write(self.document(IMG2_CHARACTER_THREE_ROOT=str(root), IMG2_CHARACTER_THREE_SHA256=pin))
        with patch.dict(os.environ, {"IMG2_CHARACTER_THREE_SHA256": "0" * 64}):
            self.assertIsNone(character_three_facts())
        with patch.dict(os.environ, {"IMG2_CHARACTER_THREE_ROOT": ""}):
            self.assertIsNone(character_three_facts())


class EnumerateSelectionTest(SelectorsCase):
    def discovery(self, selection):
        controller = DecisionController(self.top, "selection-run")
        discovery = Discovery(controller)
        discovery.run = Mock(return_value=(0, json.dumps(
            {"harness": "fixture", "commands": ["capabilities"]}).encode()))
        queried = []

        def query(edge):
            queried.append(edge)
            return {"status": "answered", "providers": [{"plugin": "stub-" + edge[1]}]}
        discovery.query = Mock(side_effect=query)
        discovery._candidate = Mock(side_effect=lambda row, name, status, edge: {"id": name, "provider": name})
        discovery._pinned_file = Mock(side_effect=discovery_error)
        patcher = patch.object(discovery_module, "read_selectors", return_value=selection)
        patcher.start()
        self.addCleanup(patcher.stop)
        return discovery, queried

    def test_unselected_domains_are_neither_queried_nor_candidates(self):
        discovery, queried = self.discovery({"selectedPlugins": ["pixi2d", "jev"]})
        candidates, rejected = discovery.enumerate()
        self.assertEqual([c["id"] for c in candidates], ["pixi2d"])
        self.assertEqual(queried, [EDGES["pixi2d"], JEV_EDGE])
        self.assertEqual(sorted((r["id"], r["reasonCode"]) for r in rejected),
                         [("character", "plugin-not-selected"), ("cs2", "plugin-not-selected"),
                          ("interior", "plugin-not-selected")])

    def test_jev_lookup_is_independent_of_domain_download_selection_and_still_pin_guarded(self):
        discovery, queried = self.discovery({"selectedPlugins": ["pixi2d"]})
        candidates, rejected = discovery.enumerate()
        self.assertIn(JEV_EDGE, queried)
        discovery._pinned_file.assert_called()
        self.assertIsNone(discovery.jev)
        self.assertEqual(discovery.jev_status, "availability-unproven")
        self.assertEqual(discovery.availability(), {"status": "availability-unproven"})
        self.assertNotIn({"id": "jev", "reasonCode": "plugin-not-selected"}, rejected)
        self.assertEqual([c["id"] for c in candidates], ["pixi2d"])

    def test_empty_domain_selection_still_queries_explicit_jev_backend(self):
        discovery, queried = self.discovery({"selectedPlugins": []})
        candidates, rejected = discovery.enumerate()
        self.assertEqual((candidates, queried), ([], [JEV_EDGE]))
        self.assertEqual({r["id"] for r in rejected}, set(EDGES))
        self.assertTrue(all(r["reasonCode"] == "plugin-not-selected" for r in rejected))

    def test_missing_or_invalid_file_preserves_existing_behaviour(self):
        discovery, queried = self.discovery(None)
        candidates, rejected = discovery.enumerate()
        self.assertEqual(sorted(c["id"] for c in candidates), sorted(EDGES))
        self.assertEqual(queried, [*EDGES.values(), JEV_EDGE])
        self.assertFalse([r for r in rejected if r["reasonCode"] == "plugin-not-selected"])

    def test_real_reader_drives_enumerate(self):
        self.write({**self.document(), "selectedPlugins": ["character"]})
        controller = DecisionController(self.top, "real-reader-run")
        discovery = Discovery(controller)
        discovery.run = Mock(return_value=(0, json.dumps(
            {"harness": "fixture", "commands": ["capabilities"]}).encode()))
        discovery.query = Mock(return_value={"status": "answered", "providers": []})
        _, rejected = discovery.enumerate()
        self.assertEqual({(r["id"], r["reasonCode"]) for r in rejected if r["reasonCode"] == "plugin-not-selected"},
                         {("cs2", "plugin-not-selected"), ("interior", "plugin-not-selected"),
                          ("pixi2d", "plugin-not-selected")})
        self.assertEqual([c.args[0] for c in discovery.query.call_args_list], [EDGES["character"], JEV_EDGE])


def discovery_error(*args, **kwargs):
    raise RouteError("provider-unpinned")


if __name__ == "__main__":
    unittest.main()
