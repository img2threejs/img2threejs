#!/usr/bin/env python3
"""Cleanup-safe showcase TypeScript smoke coverage for the Stage R5 runtime emitter.

Gated exactly like test_showcase_tsc_smoke.py: skips (does not fail) when
IMG2THREEJS_SHOWCASE_ROOT is unset, so a clean CI checkout without a
img2threejs-showcase checkout sees a skip, not a failure. Set
IMG2THREEJS_REQUIRE_SHOWCASE=1 to make the missing-checkout case a hard
failure instead (see showcase_test_support.showcase_root()).

Emits a sample module covering the loop true/false/null cases and runs the
showcase's own `npx tsc --noEmit` against it -- no tsc dependency is added
here; this test only exercises whatever the showcase checkout already has
installed.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

if __package__:
    from .showcase_test_support import showcase_root
else:
    from showcase_test_support import showcase_root

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

from emit_animation_runtime import emit_animation_runtime  # noqa: E402

SAMPLE_CLIPS = [
    {"id": "walk-forward", "label": "Walk Forward", "sourceName": "NlaTrack", "duration": 1.133, "loop": True},
    {"id": "idle-still", "label": "Idle Still", "sourceName": "NlaTrack.001", "duration": 2.0, "loop": False},
    {"id": "mystery-strip", "label": "Mystery Strip", "sourceName": "NlaTrack.002", "duration": 0.75, "loop": None},
]


class EmitAnimationRuntimeTscSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.showcase_root = showcase_root()
        self.smoke_source = self.showcase_root / "src" / "__animation_runtime_smoke__.ts"

    def tearDown(self) -> None:
        # Clean up even when the assertion above failed -- never leave the
        # temp file behind in someone else's showcase checkout.
        if getattr(self, "smoke_source", None) is not None and self.smoke_source.exists():
            self.smoke_source.unlink()

    def _run_tsc(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["npx", "tsc", "--noEmit"],
            cwd=self.showcase_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_emitted_runtime_typechecks_with_no_diagnostics(self) -> None:
        self.assertFalse(self.smoke_source.exists())
        try:
            source = emit_animation_runtime(SAMPLE_CLIPS)
            self.smoke_source.write_text(source, encoding="utf-8")
            self.assertTrue(self.smoke_source.exists())

            result = self._run_tsc()
            combined_output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, combined_output)
            self.assertEqual(combined_output.strip(), "", combined_output)
        finally:
            if self.smoke_source.exists():
                self.smoke_source.unlink()

        self.assertFalse(self.smoke_source.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
