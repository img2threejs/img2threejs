"""Offline tests for scripts/regen_jev_release_lock.py using throwaway local git repos."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

FORGE = Path(__file__).resolve().parents[1]
SCRIPT = FORGE.parent / "scripts" / "regen_jev_release_lock.py"
SPEC = importlib.util.spec_from_file_location("img2_regen_jev_release_lock", SCRIPT)
regen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regen)

REQUIRED = sorted(regen.REQUIRED_PROVIDER_FILES)
# Identity and isolation by environment only: no git config is read or written.
GIT_ENV = {
    **{name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ},
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": os.devnull,
    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content(relative: str, generation: int = 0) -> bytes:
    return f"{relative}:{generation}\n".encode()


class Fixture:
    """A temp Jev repository plus helper files; every path lives under one temp dir."""

    def __init__(self, test: unittest.TestCase):
        self.tmp = Path(tempfile.mkdtemp(prefix="regen-jev-"))
        test.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "jev"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for relative in REQUIRED:
            self.write(relative, content(relative))
        self.commit_all("initial")
        self.plugins = self.tmp / "plugins.lock.json"
        self.output = self.tmp / "release.json"

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(self.repo), *args],
                              env=GIT_ENV, capture_output=True, check=True, stdin=subprocess.DEVNULL)
        return done.stdout.decode().strip()

    def write(self, relative: str, data: bytes) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def expected(self, commit: str, generation: int = 0) -> dict:
        return {"resolvedSha": commit, "contractVersion": regen.CONTRACT_VERSION,
                "files": {r: sha256(content(r, generation)) for r in REQUIRED}}

    def plugins_lock(self, jev_commit: str, **overrides) -> dict:
        lock = {"schema": 1, "plugins": [
            {"id": "character", "commit": "1" * 40},
            {"id": "jev", "commit": jev_commit},
            {"id": "pixi2d", "commit": "2" * 40},
        ]}
        lock.update(overrides)
        self.plugins.write_text(json.dumps(lock))
        return lock

    def seed_output(self, lock: dict) -> None:
        self.output.write_text(json.dumps(lock, indent=2))

    def cli(self, *extra: str, check: bool = False, checkout: Path | None = None) -> tuple:
        argv = ["--plugins-lock", str(self.plugins), "--checkout", str(checkout or self.repo),
                "--output", str(self.output), *extra]
        if check:
            argv.append("--check")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = regen.main(argv)
        return code, out.getvalue(), err.getvalue()


class RegenBase(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        self.commit = self.fx.head()
        self.fx.plugins_lock(self.commit)
        self.fx.seed_output(self.fx.expected("0" * 40, generation=99))

    def assert_clean_failure(self, result, needle: str):
        code, _, err = result
        self.assertEqual(code, 1, err)
        self.assertIn(needle, err)
        self.assertNotIn("Traceback", err)
        self.assertTrue(err.startswith("error: "))


class GenerationTests(RegenBase):
    def test_generates_sorted_lock_for_pinned_commit(self):
        code, out, err = self.fx.cli()
        self.assertEqual((code, err), (0, ""), out)
        raw = self.fx.output.read_text()
        self.assertEqual(json.loads(raw), self.fx.expected(self.commit))
        self.assertEqual(raw, json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n")
        self.assertEqual(list(json.loads(raw)["files"]), REQUIRED)

    def test_generation_is_deterministic_and_idempotent(self):
        self.fx.cli()
        first = self.fx.output.read_bytes()
        mtime = self.fx.output.stat().st_mtime_ns
        code, out, _ = self.fx.cli()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("unchanged"))
        self.assertEqual((self.fx.output.read_bytes(), self.fx.output.stat().st_mtime_ns), (first, mtime))

    def test_hashes_commit_objects_not_working_tree(self):
        victim = "tools/jev_policy.py"
        self.fx.write(victim, b"dirty working tree bytes\n")            # modified
        (self.fx.repo / "tools" / "jev_auth.py").unlink()                 # deleted
        self.fx.write("tools/untracked.py", b"untracked\n")
        self.fx.git("rm", "-q", "--cached", "steps.json")                  # staged deletion
        self.assertEqual(self.fx.cli()[0], 0)
        self.assertEqual(json.loads(self.fx.output.read_text()), self.fx.expected(self.commit))

    def test_hashes_pinned_commit_not_head(self):
        later = self.fx.head()
        for relative in REQUIRED:
            self.fx.write(relative, content(relative, 1))
        newer = self.fx.commit_all("newer")
        self.assertNotEqual(later, newer)
        self.assertEqual(self.fx.cli()[0], 0)
        self.assertEqual(json.loads(self.fx.output.read_text()), self.fx.expected(self.commit))
        self.fx.plugins_lock(newer)
        self.assertEqual(self.fx.cli()[0], 0)
        self.assertEqual(json.loads(self.fx.output.read_text()), self.fx.expected(newer, 1))

    def test_checkout_is_not_mutated(self):
        self.fx.write("tools/jev.py", b"dirty\n")
        snapshot = lambda: ({str(p.relative_to(self.fx.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                             for p in self.fx.repo.rglob("*") if p.is_file()})
        before = snapshot()
        self.fx.cli()
        self.fx.cli(check=True)
        self.assertEqual(snapshot(), before)

    def test_ambient_git_environment_cannot_redirect_reads(self):
        other = Fixture(self)
        for relative in REQUIRED:
            other.write(relative, b"other\n")
        other.commit_all("other")
        with patch.dict(os.environ, {"GIT_DIR": str(other.repo / ".git"),
                                     "GIT_WORK_TREE": str(other.repo)}):
            self.assertEqual(self.fx.cli()[0], 0)
        self.assertEqual(json.loads(self.fx.output.read_text()), self.fx.expected(self.commit))

    def test_replace_objects_are_ignored(self):
        data = b"replaced\n"
        blob = subprocess.run(["git", "-C", str(self.fx.repo), "hash-object", "-w", "--stdin"],
                              input=data, env=GIT_ENV, capture_output=True, check=True).stdout.decode().strip()
        original = self.fx.git("rev-parse", f"{self.commit}:plugin.json")
        self.fx.git("replace", original, blob)
        self.assertEqual(self.fx.cli()[0], 0)
        self.assertEqual(json.loads(self.fx.output.read_text()), self.fx.expected(self.commit))


class CheckTests(RegenBase):
    def test_check_succeeds_and_never_writes(self):
        self.fx.seed_output(self.fx.expected(self.commit))
        before = self.fx.output.read_bytes()
        code, out, err = self.fx.cli(check=True)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ok:", out)
        self.assertEqual(self.fx.output.read_bytes(), before)

    def test_check_is_semantic_not_byte_exact(self):
        lock = self.fx.expected(self.commit)
        lock["files"] = dict(reversed(list(lock["files"].items())))
        self.fx.output.write_text(json.dumps(lock))  # compact, unsorted
        self.assertEqual(self.fx.cli(check=True)[0], 0)

    def test_check_fails_on_stale_default_without_writing(self):
        before = self.fx.output.read_bytes()
        self.assert_clean_failure(self.fx.cli(check=True), "pin mismatch")
        self.assertEqual(self.fx.output.read_bytes(), before)

    def test_pin_mismatch_fails_before_git_is_needed(self):
        self.fx.seed_output(self.fx.expected("f" * 40))
        result = self.fx.cli(check=True, checkout=self.fx.tmp / "does-not-exist")
        self.assert_clean_failure(result, "pin mismatch")

    def test_hash_mismatch_fails_naming_file(self):
        lock = self.fx.expected(self.commit)
        lock["files"]["tools/jev_client.py"] = "0" * 64
        self.fx.seed_output(lock)
        self.assert_clean_failure(self.fx.cli(check=True), "tools/jev_client.py")

    def test_check_fails_when_commit_content_differs_from_pin_lock(self):
        for relative in REQUIRED:
            self.fx.write(relative, content(relative, 1))
        newer = self.fx.commit_all("newer")
        tampered = self.fx.expected(newer)  # lock names the right pin but one stale hash
        tampered["files"]["steps.json"] = sha256(content("steps.json", 0))
        self.fx.seed_output(tampered)
        self.fx.plugins_lock(newer)
        self.assert_clean_failure(self.fx.cli(check=True), "steps.json")

    def test_check_does_not_trust_output_pin_alone(self):
        self.fx.seed_output(self.fx.expected(self.commit, generation=5))
        self.assert_clean_failure(self.fx.cli(check=True), "stale files")


class RefusalTests(RegenBase):
    def test_missing_required_file_at_commit(self):
        self.fx.git("rm", "-q", "tools/jev_transport.py")
        commit = self.fx.commit_all("drop")
        self.fx.plugins_lock(commit)
        before = self.fx.output.read_bytes()
        self.assert_clean_failure(self.fx.cli(), "tools/jev_transport.py")
        self.assertEqual(self.fx.output.read_bytes(), before)

    def test_symlink_refused_even_when_target_is_regular(self):
        path = self.fx.repo / "tools" / "jev_auth.py"
        path.unlink()
        os.symlink("jev.py", path)
        commit = self.fx.commit_all("symlink")
        self.fx.plugins_lock(commit)
        before = self.fx.output.read_bytes()
        self.assert_clean_failure(self.fx.cli(), "symlinks and submodules are refused")
        self.assertEqual(self.fx.output.read_bytes(), before)

    def test_submodule_gitlink_refused(self):
        self.fx.git("rm", "-q", "tools/jev_consent.py")
        self.fx.git("update-index", "--add", "--cacheinfo", f"160000,{self.commit},tools/jev_consent.py")
        self.fx.git("commit", "-q", "-m", "gitlink")
        self.fx.plugins_lock(self.fx.head())
        self.assert_clean_failure(self.fx.cli(), "tools/jev_consent.py")

    def test_directory_in_place_of_file_refused(self):
        self.fx.git("rm", "-q", "tools/jev_policy.py")
        self.fx.write("tools/jev_policy.py/inner", b"x\n")
        commit = self.fx.commit_all("dir")
        self.fx.plugins_lock(commit)
        self.assert_clean_failure(self.fx.cli(), "tools/jev_policy.py")

    def test_executable_regular_blob_accepted(self):
        self.fx.git("update-index", "--chmod=+x", "tools/jev.py")
        self.fx.git("commit", "-q", "-m", "chmod")
        self.fx.plugins_lock(self.fx.head())
        self.assertEqual(self.fx.cli()[0], 0)

    def test_unknown_commit_and_non_commit_object(self):
        self.fx.plugins_lock("a" * 40)
        self.assert_clean_failure(self.fx.cli(), "git rev-parse failed")
        tree = self.fx.git("rev-parse", f"{self.commit}^{{tree}}")
        self.fx.plugins_lock(tree)
        self.assert_clean_failure(self.fx.cli(), "git rev-parse failed")

    def test_checkout_not_a_repo_or_subdirectory(self):
        plain = self.fx.tmp / "plain"
        plain.mkdir()
        self.assert_clean_failure(self.fx.cli(checkout=plain), "git ")
        self.assert_clean_failure(self.fx.cli(checkout=self.fx.repo / "tools"), "root of a git repository")
        self.assert_clean_failure(self.fx.cli(checkout=self.fx.tmp / "nope"), "not a directory")

    def test_changed_required_inventory_in_code_is_refused(self):
        # A new required file is not silently adopted into an old lock...
        with patch.object(regen, "REQUIRED_PROVIDER_FILES", regen.REQUIRED_PROVIDER_FILES | {"tools/new.py"}):
            self.fx.write("tools/new.py", b"new\n")
            self.fx.plugins_lock(self.fx.commit_all("new file"))
            self.assert_clean_failure(self.fx.cli(), "tools/new.py")
            self.assert_clean_failure(self.fx.cli(check=True), "refusing to change inventory")
        # ...and a removed one is not silently dropped.
        with patch.object(regen, "REQUIRED_PROVIDER_FILES", regen.REQUIRED_PROVIDER_FILES - {"steps.json"}):
            self.assert_clean_failure(self.fx.cli(), "steps.json")

    def test_output_lock_inventory_drift_is_refused(self):
        lock = self.fx.expected(self.commit)
        del lock["files"]["steps.json"]
        self.fx.seed_output(lock)
        self.assert_clean_failure(self.fx.cli(), "refusing to change inventory")
        lock = self.fx.expected(self.commit)
        lock["files"]["tools/extra.py"] = "a" * 64
        self.fx.seed_output(lock)
        self.assert_clean_failure(self.fx.cli(check=True), "tools/extra.py")

    def test_contract_change_is_refused(self):
        lock = self.fx.expected(self.commit)
        lock["contractVersion"] = "jev-v5"
        self.fx.seed_output(lock)
        self.assert_clean_failure(self.fx.cli(), "contractVersion")
        self.assert_clean_failure(self.fx.cli(check=True), "contractVersion")

    def test_no_flag_accepts_changes(self):
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            regen.main(["--plugins-lock", "x", "--checkout", "y", "--accept-inventory-change"])
        self.assertEqual(raised.exception.code, 2)

    def test_missing_output_lock_is_refused(self):
        self.fx.output.unlink()
        self.assert_clean_failure(self.fx.cli(), "output lock")

    def test_output_lock_shape_errors(self):
        good = self.fx.expected(self.commit)
        bad_locks = {
            "extra key": {**good, "extra": 1},
            "short sha": {**good, "resolvedSha": "abc"},
            "upper sha": {**good, "resolvedSha": "A" * 40},
            "files list": {**good, "files": []},
            "bad digest": {**good, "files": {**good["files"], "steps.json": "xyz"}},
            "not object": [],
        }
        for label, lock in bad_locks.items():
            with self.subTest(label):
                self.fx.seed_output(lock)
                self.assert_clean_failure(self.fx.cli(), "output lock")

    def test_output_symlink_refused(self):
        target = self.fx.tmp / "target.json"
        target.write_text(json.dumps(self.fx.expected(self.commit)))
        self.fx.output.unlink()
        os.symlink(target, self.fx.output)
        self.assert_clean_failure(self.fx.cli(), "output lock")


class PluginPinTests(RegenBase):
    def failing(self, lock_text: str, needle: str):
        self.fx.plugins.write_text(lock_text)
        before = self.fx.output.read_bytes()
        self.assert_clean_failure(self.fx.cli(), needle)
        self.assert_clean_failure(self.fx.cli(check=True), needle)
        self.assertEqual(self.fx.output.read_bytes(), before)

    def test_invalid_pins(self):
        c = self.commit
        cases = {
            "unpinned jev": ({"schema": 1, "plugins": [{"id": "jev"}]}, "unpinned"),
            "null commit": ({"schema": 1, "plugins": [{"id": "jev", "commit": None}]}, "unpinned"),
            "branch name": ({"schema": 1, "plugins": [{"id": "jev", "commit": "main"}]}, "unpinned"),
            "short hash": ({"schema": 1, "plugins": [{"id": "jev", "commit": c[:12]}]}, "unpinned"),
            "upper hash": ({"schema": 1, "plugins": [{"id": "jev", "commit": c.upper()}]}, "unpinned"),
            "other plugin unpinned": ({"schema": 1, "plugins": [
                {"id": "jev", "commit": c}, {"id": "cs2", "commit": "v1"}]}, "'cs2'"),
            "duplicate jev": ({"schema": 1, "plugins": [
                {"id": "jev", "commit": c}, {"id": "jev", "commit": c}]}, "duplicate plugin id"),
            "no jev": ({"schema": 1, "plugins": [{"id": "cs2", "commit": c}]}, "exactly one 'jev'"),
            "empty plugins": ({"schema": 1, "plugins": []}, "non-empty"),
            "plugins not list": ({"schema": 1, "plugins": {}}, "non-empty"),
            "entry not object": ({"schema": 1, "plugins": ["jev"]}, "not an object"),
            "no id": ({"schema": 1, "plugins": [{"commit": c}]}, "no id"),
            "bad schema": ({"schema": 2, "plugins": [{"id": "jev", "commit": c}]}, "schema"),
            "bool schema": ({"schema": True, "plugins": [{"id": "jev", "commit": c}]}, "schema"),
            "top-level list": ([], "schema"),
        }
        for label, (lock, needle) in cases.items():
            with self.subTest(label):
                self.failing(json.dumps(lock), needle)

    def test_malformed_json(self):
        c = self.commit
        cases = {
            "duplicate key": '{"schema": 1, "plugins": [{"id": "jev", "commit": "%s", "commit": "%s"}]}' % (c, c),
            "duplicate top key": '{"schema": 1, "schema": 1, "plugins": []}',
            "not json": "{not json",
            "empty": "",
            "nan": '{"schema": NaN, "plugins": []}',
            "invalid utf8": None,
            "deep": "[" * 100000,
        }
        for label, text in cases.items():
            with self.subTest(label):
                if text is None:
                    self.fx.plugins.write_bytes(b"\xff\xfe")
                else:
                    self.fx.plugins.write_text(text)
                self.assert_clean_failure(self.fx.cli(), "plugins lock")

    def test_duplicate_key_in_output_lock(self):
        raw = json.dumps(self.fx.expected(self.commit)).replace(
            '"contractVersion"', '"contractVersion": "x", "contractVersion"', 1)
        self.fx.output.write_text(raw)
        self.assert_clean_failure(self.fx.cli(), "duplicate JSON key")

    def test_missing_plugins_lock(self):
        self.fx.plugins.unlink()
        self.assert_clean_failure(self.fx.cli(), "plugins lock")



class ProcessTests(RegenBase):
    def test_subprocess_exit_codes_and_no_traceback(self):
        base = [sys.executable, str(SCRIPT), "--plugins-lock", str(self.fx.plugins),
                "--checkout", str(self.fx.repo), "--output", str(self.fx.output)]
        run = lambda *extra: subprocess.run([*base, *extra], capture_output=True, text=True, env={**GIT_ENV})
        stale = run("--check")
        self.assertEqual(stale.returncode, 1)
        self.assertNotIn("Traceback", stale.stderr)
        self.assertEqual(run().returncode, 0)
        self.assertEqual(run("--check").returncode, 0)
        usage = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(usage.returncode, 2)

    def test_committed_lock_is_valid_against_current_inventory(self):
        committed = json.loads((FORGE / "_shared" / "jev_release_lock.json").read_text())
        self.assertEqual(regen.validate_lock(copy.deepcopy(committed), "committed"), committed)


class HarnessPinParityTests(unittest.TestCase):
    def test_committed_lock_matches_available_harness_jev_pin(self):
        harness = Path(os.environ.get("IMG2_HARNESS_DIR") or FORGE.parent.parent / "harness")
        plugins_lock = harness / "plugins.lock.json"
        if not plugins_lock.exists():
            self.skipTest(f"harness lock not available: {plugins_lock}; set IMG2_HARNESS_DIR")
        pin = regen.jev_pin(regen._read_json(plugins_lock, "harness plugins lock"))
        committed = regen.validate_lock(
            regen._read_json(regen.DEFAULT_OUTPUT, "core release lock"), "core release lock")
        self.assertEqual(committed["resolvedSha"], pin,
                         f"{regen.DEFAULT_OUTPUT} differs from {plugins_lock}; "
                         "run scripts/regen_jev_release_lock.py for the harness pin")


if __name__ == "__main__":
    unittest.main()
