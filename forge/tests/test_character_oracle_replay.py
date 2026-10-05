#!/usr/bin/env python3
"""The EMISSION oracle: a frozen character spec must emit byte-identical TypeScript.

Frozen from `main` @ 6e60b5e before any part of the character extraction moved, so that "the
extraction changed no emitted byte" is a measurement rather than a claim. It must be green here,
and green again once the plugin is installed — one run is a snapshot, two are an oracle.

WHAT THIS TEST CANNOT SEE, stated because a previous extraction was misled by exactly this:
its input is an ALREADY-AUTHORED spec, and the emitter never leaves the base. So it stays green
even if the whole authoring side — the humanoid template, the derived rig — is deleted. The
authoring half is covered by `plugin-character/tests/test_character_authoring_oracle.py`, whose
input is the anatomy the authoring consumes. Neither oracle substitutes for the other.

Determinism was established before the output was frozen (OpenSpec task 0.6): no `random`, `uuid`,
`time.time()`, `datetime.now()` or `os.urandom` on the authoring or emission paths, and two full
runs produced one md5.

KEY ORDER IS PART OF THE INPUT. The emitter embeds the spec's dict key ORDER in the TypeScript it
writes, so two specs with identical content but different key order emit different bytes. The first
attempt at this fixture froze `spec.json` with `sort_keys=True` and the replay failed on 850 diff
lines of pure reordering. The fixture is therefore serialized WITHOUT `sort_keys`, and it must
round-trip exactly. This also binds the extraction: a plugin contributing `componentTree`, `rig` or
`materials` must author them in the same key order the base did, or byte-identity fails on content
that is in fact unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

_FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_FORGE), str(_FORGE / "_shared"), str(_FORGE / "stage2_spec"),
                str(_FORGE / "stage3_build")]

from generate_threejs_factory import generate  # noqa: E402

ORACLE = Path(__file__).resolve().parent / "fixtures" / "oracle-character"
SPEC_PATH = ORACLE / "spec.json"
BLOCKOUT_PATH = ORACLE / "blockout.ts"

# Recorded in the same sitting as the freeze. Re-record deliberately, never to make a red test green.
FROZEN_SPEC_MD5 = "bb1051fd35003aa8eb125fa8f77b5410"
FROZEN_BLOCKOUT_MD5 = "98ac2045dcc9d802cc369e8b4d4d20a4"

# Without these the spec degenerated to a static mesh and the byte comparison would pass on a
# no-op. A frozen output that no longer distinguishes the emission path is not an oracle.
REQUIRED_MARKERS = ("THREE.SkinnedMesh", "THREE.Skeleton", "new THREE.Bone", "skinIndex")


class CharacterEmissionOracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        cls.frozen = BLOCKOUT_PATH.read_text(encoding="utf-8")

    def test_the_fixtures_are_the_ones_that_were_frozen(self) -> None:
        self.assertEqual(hashlib.md5(SPEC_PATH.read_bytes()).hexdigest(), FROZEN_SPEC_MD5)
        self.assertEqual(hashlib.md5(BLOCKOUT_PATH.read_bytes()).hexdigest(), FROZEN_BLOCKOUT_MD5)

    def test_the_emitted_typescript_is_byte_identical(self) -> None:
        self.assertEqual(generate(self.spec, "blockout"), self.frozen)

    def test_emission_is_deterministic_across_repeated_runs(self) -> None:
        self.assertEqual(generate(self.spec, "blockout"), generate(self.spec, "blockout"))

    def test_the_frozen_output_still_takes_the_bone_track(self) -> None:
        for marker in REQUIRED_MARKERS:
            self.assertIn(marker, self.frozen, f"{marker} absent — the fixture no longer "
                                               "distinguishes the emission path it exists to pin")

    def test_the_frozen_spec_still_carries_what_the_bone_track_routes_on(self) -> None:
        # The emitter reads rig.bones presence. A fixture that lost its rig would pass the byte
        # comparison against an equally degenerate frozen output.
        self.assertEqual(len(self.spec["componentTree"]), 61)
        self.assertEqual(len(self.spec["rig"]["bones"]), 49)


if __name__ == "__main__":
    unittest.main()
