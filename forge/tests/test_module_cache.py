"""Tests for the per-module code cache (Plan 1.3 Phase 6, Workstream I)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage3_build"))
from module_cache import (  # noqa: E402
    get_module,
    invalidate_attached,
    module_cache_key,
    put_module,
)


class ModuleCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / ".cache" / "module_cache.json"
        self.generator = self.tmp / "generator.py"
        self.generator.write_bytes(b"# generator v1\nprint('hi')\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_key_changes_with_spec(self):
        key_a = module_cache_key({"id": "blade", "len": 10}, self.generator)
        key_b = module_cache_key({"id": "blade", "len": 20}, self.generator)
        self.assertNotEqual(key_a, key_b)

    def test_key_changes_with_generator_source(self):
        spec = {"id": "blade", "len": 10}
        gen2 = self.tmp / "generator2.py"
        gen2.write_bytes(b"# generator v2 -- different bytes\nprint('yo')\n")
        self.assertNotEqual(
            module_cache_key(spec, self.generator),
            module_cache_key(spec, gen2),
        )

    def test_key_stable_regardless_of_dict_order(self):
        self.assertEqual(
            module_cache_key({"a": 1, "b": 2}, self.generator),
            module_cache_key({"b": 2, "a": 1}, self.generator),
        )

    def test_put_get_roundtrip(self):
        spec = {"id": "blade", "len": 10}
        code = "function makeBlade(){ return mesh; }"
        put_module(self.manifest, spec, self.generator, code)
        self.assertEqual(get_module(self.manifest, spec, self.generator), code)
        # unknown spec -> miss
        self.assertIsNone(
            get_module(self.manifest, {"id": "ghost"}, self.generator)
        )

    def test_invalidate_attached_removes_dependents_only(self):
        specs = {
            "root": {"id": "root"},
            "blade": {"id": "blade", "parent": "root"},
            "handle": {"id": "handle", "parent": "root"},
            "pommel": {"id": "pommel", "attachment": {"parentId": "handle"}},
        }
        for spec in specs.values():
            put_module(self.manifest, spec, self.generator, f"code_{spec['id']}")

        components = list(specs.values())
        invalidated = invalidate_attached(self.manifest, "handle", components)

        self.assertEqual(invalidated, ["pommel"])
        # pommel gone, everyone else remains
        self.assertIsNone(
            get_module(self.manifest, specs["pommel"], self.generator)
        )
        self.assertEqual(
            get_module(self.manifest, specs["root"], self.generator), "code_root"
        )
        self.assertEqual(
            get_module(self.manifest, specs["blade"], self.generator), "code_blade"
        )
        self.assertEqual(
            get_module(self.manifest, specs["handle"], self.generator), "code_handle"
        )

    def test_invalidate_returns_empty_when_no_dependents(self):
        specs = {
            "root": {"id": "root"},
            "leaf": {"id": "leaf", "parent": "root"},
        }
        for spec in specs.values():
            put_module(self.manifest, spec, self.generator, f"code_{spec['id']}")

        # leaf has no dependents attached to it
        self.assertEqual(invalidate_attached(self.manifest, "leaf", list(specs.values())), [])
        # nothing removed
        self.assertEqual(
            get_module(self.manifest, specs["leaf"], self.generator), "code_leaf"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
