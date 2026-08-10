"""The browser capture must preserve the canvas alpha.

divine_eye.py's photometric signals (ssim, blowoutParity, tonalParity, hueZoneParity) compare
full frames. If the capture is composited onto the page background while the reference carries
alpha, those four signals measure the BACKDROP, not the model -- and the mask-based signals
(silhouette IoU, scale) are unaffected, so the result looks like a real material failure rather
than a measurement fault.

Observed on the reconstruction that produced this test, same render both ways:

    composited onto white   fidelity 0.5075   ssim 0.000  blowout 0.000  tonal 0.217
    like-for-like           fidelity 0.8349   ssim 0.916  blowout 0.987  tonal 0.876

Three correction rounds were spent tuning geometry against the first number.
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "capture_threejs_playwright.py"


class CaptureAlphaContract(unittest.TestCase):
    def setUp(self) -> None:
        self.text = ADAPTER.read_text(encoding="utf-8")

    def test_every_screenshot_call_omits_the_background(self) -> None:
        calls = [line for line in self.text.splitlines() if ".screenshot(path=" in line]
        self.assertTrue(calls, "expected at least one screenshot call in the adapter")
        for line in calls:
            self.assertIn("omit_background=True", line, f"screenshot call drops alpha: {line.strip()}")

    def test_the_reason_is_recorded_next_to_the_call(self) -> None:
        self.assertIn("measures the BACKDROP rather than the model", self.text)


if __name__ == "__main__":
    unittest.main()
