#!/usr/bin/env python3
"""A reference map backs a texture only through a servable `url`, never a `path`.

The extractor records where it wrote each map on the authoring machine
(`path`) and, optionally, where the showcase serves it (`url`). The emitted
runtime used to fall back to `path` when `url` was absent, so a spec whose
maps were kept out of the shipped tree (the code-only contract) produced
textures that never loaded and every material rendered white — colour forced
to 0xffffff with roughness 1, waiting on maps that could not arrive. A map
with a path and no url is a deliberately unshipped map: the runtime must take
the procedural route instead.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def import_generator():
    module_names = ("generate_threejs_factory", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        import generate_threejs_factory
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return generate_threejs_factory


GEN = import_generator()

SPEC = {
    "targetName": "Map Url Fixture",
    "schemaVersion": "2.1",
    "suitability": "pass",
    "coordinateFrame": {},
    "silhouette": {},
    "proceduralStrategy": [],
    "materials": [{"id": "clay"}],
    "componentTree": [
        {
            "id": "body",
            "name": "Body",
            "level": "macro",
            "role": "body",
            "primitive": "box",
            "parent": None,
            "material": "clay",
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        },
    ],
}


class ReferenceMapUrlTest(unittest.TestCase):
    def test_emitted_resolver_never_falls_back_to_path(self) -> None:
        generated = GEN.generate(SPEC, "material-pass")
        start = generated.index("function referenceMapUrl(")
        body = generated[start:generated.index("\n}\n", start)]
        self.assertIn("record.url", body)
        self.assertNotIn(
            "record.path",
            body,
            "referenceMapUrl must not treat an authoring-machine path as a loadable URL",
        )


if __name__ == "__main__":
    unittest.main()
