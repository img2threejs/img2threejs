"""Disposable browser-bundle inventory tests; no installed browser is executed."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
from browser_runtime import snapshot_browser_bundle, verify_browser_bundle
from route_parent import ParentError


class BrowserBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "Browser.app"
        self.executable = self.root / "Contents/MacOS/Chrome"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"synthetic launcher; never execute")
        self.version = self.root / "Contents/Frameworks/Chrome.framework/Versions/157"
        self.version.mkdir(parents=True)
        self.framework = self.version / "Chrome"
        self.framework.write_bytes(b"synthetic framework")
        self.current = self.version.parent / "Current"
        self.current.symlink_to("157", target_is_directory=True)

    def reject_snapshot(self, reason):
        with self.assertRaises(ParentError) as caught:
            snapshot_browser_bundle(str(self.root))
        self.assertEqual(caught.exception.reason_code, reason)

    def test_current_directory_symlink_is_bound_without_duplicate_traversal(self):
        inventory = snapshot_browser_bundle(str(self.root))
        self.assertEqual(inventory, tuple(sorted(inventory)))
        self.assertEqual(len(inventory), 3)
        records = dict(inventory)
        self.assertEqual(records[self.framework.relative_to(self.root).as_posix()],
                         hashlib.sha256(self.framework.read_bytes()).hexdigest())
        self.assertEqual(records[self.current.relative_to(self.root).as_posix()],
                         hashlib.sha256(b"symlink\0" + b"157").hexdigest())
        self.assertFalse(any("/Current/" in path for path, _ in inventory))
        verify_browser_bundle(str(self.root), inventory, str(self.executable))

    def test_file_symlink_and_its_actual_target_are_both_bound(self):
        link = self.root / "Contents/Frameworks/Chrome.framework/Chrome"
        link.symlink_to("Versions/Current/Chrome")
        inventory = snapshot_browser_bundle(str(self.root))
        self.assertIn((link.relative_to(self.root).as_posix(),
                       hashlib.sha256(b"symlink\0Versions/Current/Chrome").hexdigest()), inventory)
        self.framework.write_bytes(b"changed target, same symlink and launcher")
        with self.assertRaisesRegex(ParentError, "cs2-browser-bundle-changed"):
            verify_browser_bundle(str(self.root), inventory, str(self.executable))

    def test_addition_deletion_and_literal_target_change_revoke_approval(self):
        inventory = snapshot_browser_bundle(str(self.root))
        addition = self.version / "new.dylib"
        addition.write_bytes(b"unapproved")
        with self.assertRaisesRegex(ParentError, "cs2-browser-bundle-changed"):
            verify_browser_bundle(str(self.root), inventory, str(self.executable))
        addition.unlink()
        self.current.unlink()
        self.current.symlink_to("./157", target_is_directory=True)
        with self.assertRaisesRegex(ParentError, "cs2-browser-bundle-changed"):
            verify_browser_bundle(str(self.root), inventory, str(self.executable))
        self.current.unlink()
        self.current.symlink_to("157", target_is_directory=True)
        self.framework.unlink()
        with self.assertRaisesRegex(ParentError, "cs2-browser-bundle-changed"):
            verify_browser_bundle(str(self.root), inventory, str(self.executable))

    def test_symlink_escape_is_rejected_even_for_existing_target(self):
        outside = self.directory / "ambient-library"
        outside.write_bytes(b"not in the approved bundle")
        (self.root / "escape").symlink_to(outside)
        self.reject_snapshot("cs2-browser-symlink-escape")

    def test_dangling_symlink_is_rejected(self):
        (self.root / "dangling").symlink_to("missing")
        self.reject_snapshot("cs2-invalid-browser-bundle")

    def test_symlink_chain_cycle_is_rejected(self):
        (self.root / "first").symlink_to("second")
        (self.root / "second").symlink_to("first")
        self.reject_snapshot("cs2-invalid-browser-bundle")

    def test_directory_symlink_cycle_is_rejected_without_recursing(self):
        (self.version / "back").symlink_to(self.root, target_is_directory=True)
        self.reject_snapshot("cs2-browser-symlink-cycle")

    def test_cross_directory_symlink_cycle_is_rejected(self):
        other = self.root / "other"
        other.mkdir()
        (self.version / "other").symlink_to(other, target_is_directory=True)
        (other / "back").symlink_to(self.version, target_is_directory=True)
        self.reject_snapshot("cs2-browser-symlink-cycle")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO fixture requires POSIX")
    def test_special_file_is_rejected_without_opening_it(self):
        os.mkfifo(self.root / "pipe")
        self.reject_snapshot("cs2-unsupported-browser-entry")

    def test_root_alias_and_executable_outside_bundle_are_rejected(self):
        alias = self.directory / "alias.app"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ParentError, "cs2-invalid-browser-root"):
            snapshot_browser_bundle(str(alias))
        inventory = snapshot_browser_bundle(str(self.root))
        with self.assertRaisesRegex(ParentError, "cs2-browser-executable-outside-bundle"):
            verify_browser_bundle(str(self.root), inventory, str(self.directory / "other-launcher"))


if __name__ == "__main__":
    unittest.main()
