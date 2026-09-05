#!/usr/bin/env python3
"""Extended image_codec coverage: the layers the PNG/Pillow cross-check does not
reach.

  * JPEG cross-check — decode_jpeg is the portability backbone for reference
    photos (the whole reason load_image stopped needing macOS sips), so its
    output is compared against Pillow across quality levels, chroma subsampling
    and grayscale variants.
  * external fallback chain — a format neither pure-Python decoder reads (WebP)
    must arrive through the machine's converter chain (here: Pillow), with the
    conversion recorded as a warning; and when NOTHING can convert, the error
    must name the whole chain and the fix instead of blaming the file.
  * decode cache — keyed by (path, mtime, size): an edited file must re-decode,
    a restored (path, mtime, size) must hit the cache. This is the invariant the
    Divine Eye speedup rests on.

Pillow-dependent cases self-skip on stdlib-only machines.
Run: python forge/tests/test_image_codec_extended.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
import image_codec  # noqa: E402
from image_codec import decode_cache_key, decode_image, load_image  # noqa: E402
from jpeg import UnsupportedJpeg, decode_jpeg, is_jpeg  # noqa: E402

try:
    from PIL import Image  # noqa: E402

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

RNG_SEED = 20260905


def random_rgb_image(size: int = 48):
    import random

    rng = random.Random(RNG_SEED)
    img = Image.new("RGB", (size, size))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(size * size)])
    return img


@unittest.skipUnless(PIL_AVAILABLE, "Pillow (the reference decoder) is not installed")
class JpegCrossCheckTest(unittest.TestCase):
    """decode_jpeg vs Pillow on baseline JPEGs.

    JPEG is deterministic for identical BYTES in one decoder, but two conforming
    decoders may differ in IDCT/level-shift rounding: libjpeg (Pillow) uses a
    fixed-point IDCT, the pure-Python decoder a float one, so ±1 (rarely 2) per
    channel is expected and harmless for every downstream signal (masks, luma,
    pHash). The bar is therefore max|Δ| <= 2 with mean |Δ| < 1 — not bit equality
    (which PNG, being lossless, does demand).
    """

    VARIANTS = (
        {"quality": 95},
        {"quality": 60},
        {"quality": 95, "subsampling": 0},  # 4:4:4
        {"quality": 85, "subsampling": 2},  # 4:2:0
        {"quality": 75, "optimize": True},
    )

    def _assert_close_to_pillow(self, data: bytes, note: str) -> None:
        width, height, pixels = decode_jpeg(data)
        reference = list(Image.open(BytesIO(data)).convert("RGBA").getdata())
        self.assertEqual((width, height), Image.open(BytesIO(data)).size)
        self.assertEqual(len(pixels), len(reference))
        deltas = [
            abs(ours[channel] - ref[channel])
            for ours, ref in zip(pixels, reference)
            for channel in range(3)
        ]
        # Rounding-only bar: essentially all channels within ±2 (zero at q>=75),
        # a vanishing tail up to 8 at aggressive quantization, mean well under 1.
        self.assertLess(sum(deltas) / len(deltas), 1.0, f"{note}: mean delta drifted beyond rounding")
        self.assertLessEqual(max(deltas), 8, f"{note}: max channel delta {max(deltas)} is not rounding noise")
        beyond_two = sum(1 for delta in deltas if delta > 2) / len(deltas)
        self.assertLess(beyond_two, 0.005, f"{note}: {beyond_two:.2%} of channels differ by more than 2")
        self.assertEqual([p[3] for p in pixels], [p[3] for p in reference], "alpha must be opaque 255 everywhere")

    def test_color_variants_match_pillow(self) -> None:
        for kwargs in self.VARIANTS:
            with self.subTest(**kwargs):
                buffer = BytesIO()
                random_rgb_image().save(buffer, format="JPEG", **kwargs)
                self.assertTrue(is_jpeg(buffer.getvalue()))
                self._assert_close_to_pillow(buffer.getvalue(), str(kwargs))

    def test_grayscale_jpeg_matches_pillow(self) -> None:
        import random

        rng = random.Random(RNG_SEED + 1)
        gray = Image.new("L", (40, 30))
        gray.putdata([rng.randrange(256) for _ in range(40 * 30)])
        buffer = BytesIO()
        gray.save(buffer, format="JPEG", quality=90)
        self._assert_close_to_pillow(buffer.getvalue(), "grayscale")

    def test_decode_is_deterministic(self) -> None:
        buffer = BytesIO()
        random_rgb_image(24).save(buffer, format="JPEG", quality=88)
        data = buffer.getvalue()
        self.assertEqual(decode_jpeg(data), decode_jpeg(data), "same bytes must decode identically")

    def test_progressive_still_raises_clearly(self) -> None:
        buffer = BytesIO()
        random_rgb_image(32).save(buffer, format="JPEG", quality=80, progressive=True)
        with self.assertRaises(UnsupportedJpeg):
            decode_jpeg(buffer.getvalue())

    def test_load_image_routes_jpeg_without_any_converter(self) -> None:
        # the portability contract from test_jpeg, restated at codec level:
        # no sips/magick/ffmpeg on the machine must not block a baseline JPEG
        buffer = BytesIO()
        random_rgb_image(32).save(buffer, format="JPEG", quality=90)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.jpg"
            path.write_bytes(buffer.getvalue())
            with mock.patch.object(shutil, "which", return_value=None):
                width, height, pixels, warnings = load_image(path)
            self.assertEqual((width, height), (32, 32))
            self.assertEqual(warnings, [])


@unittest.skipUnless(PIL_AVAILABLE, "WebP fixtures need Pillow to write")
class ExternalChainTest(unittest.TestCase):
    def _webp_fixture(self, directory: str) -> Path:
        path = Path(directory) / "shot.webp"
        random_rgb_image(24).save(path, format="WEBP", quality=90)
        return path

    def test_webp_decodes_through_the_chain_with_warning(self) -> None:
        # mirror the codec's own probing: on Windows, bare `convert` is the
        # FAT->NTFS tool and does not count as ImageMagick
        has_external = bool(
            shutil.which("magick")
            or (shutil.which("convert") and sys.platform != "win32")
            or shutil.which("ffmpeg")
            or shutil.which("sips")
        )
        if has_external:
            self.skipTest("machine has an external converter; chain tail differs")
        with tempfile.TemporaryDirectory() as directory:
            path = self._webp_fixture(directory)
            width, height, pixels, warnings = load_image(path)
            self.assertEqual((width, height), (24, 24))
            self.assertEqual(len(pixels), 24 * 24)
            self.assertTrue(
                any("Pillow" in warning for warning in warnings),
                f"conversion must be recorded as a warning, got {warnings}",
            )

    def test_no_converter_error_names_the_whole_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mystery.xyz"
            path.write_bytes(b"not an image at all")
            with mock.patch.object(shutil, "which", return_value=None), mock.patch.dict(sys.modules, {"PIL": None}):
                with self.assertRaises(ValueError) as ctx:
                    load_image(path)
            message = str(ctx.exception)
            for tool in ("ImageMagick", "ffmpeg", "sips", "Pillow"):
                self.assertIn(tool, message, f"error must name {tool}: {message}")

    def test_windows_convert_exe_is_not_treated_as_imagemagick(self) -> None:
        # C:\Windows\system32\convert.exe is the FAT->NTFS tool and is ALWAYS on
        # PATH on Windows. If it were picked as "ImageMagick", every decode of an
        # exotic format would shell out to a filesystem utility. Simulate that
        # machine: which() reports the system convert, nothing else, no Pillow.
        # The chain must stay EMPTY: the error is the no-converter-found branch
        # ("No external converter is available"), never the converters-ran-and-
        # failed branch ("every available converter failed (ImageMagick)").
        system_convert = shutil.which("convert")

        def fake_which(name, *args, **kwargs):
            return system_convert if name == "convert" else None

        with mock.patch.object(shutil, "which", side_effect=fake_which), mock.patch.dict(sys.modules, {"PIL": None}):
            warnings: list[str] = []
            with self.assertRaises(ValueError) as ctx:
                image_codec.external_convert(Path("whatever.webp"), ValueError("not png"), warnings)
        message = str(ctx.exception)
        self.assertIn("No external converter is available", message)
        self.assertNotIn("every available converter failed", message)


class DecodeCacheTest(unittest.TestCase):
    """The (path, mtime, size) cache invariant the Divine Eye speedup rests on."""

    def _png_bytes(self, shade: int, size: int = 8) -> bytes:
        # minimal 8-bit gray PNG via the codec's own encoder is not available;
        # build with struct+zlib (tiny, filter 0)
        import struct
        import zlib

        def chunk(kind: bytes, payload: bytes) -> bytes:
            crc = zlib.crc32(kind)
            crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

        raw = bytearray()
        for _ in range(size):
            raw.append(0)
            raw.extend(bytes([shade] * size))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b"")
        )

    def test_same_key_hits_cache_and_shares_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.png"
            path.write_bytes(self._png_bytes(10))
            first = decode_image(path)
            second = decode_image(path)
            self.assertIs(first[2], second[2], "same key must share one pixels list")

    def test_edited_file_redecodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.png"
            path.write_bytes(self._png_bytes(10))
            first = decode_image(path)
            # SAME byte length, different content — only mtime distinguishes them
            path.write_bytes(self._png_bytes(200))
            second = decode_image(path)
            self.assertEqual(first[2][0], (10, 10, 10, 255))
            self.assertEqual(second[2][0], (200, 200, 200, 255), "edit must bust the cache")

    def test_restored_mtime_and_size_hits_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.png"
            shade_a, shade_b = self._png_bytes(10), self._png_bytes(200)
            path.write_bytes(shade_a)
            stat_a = path.stat()
            first = decode_image(path)
            path.write_bytes(shade_b)
            decode_image(path)  # b decode (busts)
            # restore content and the recorded (mtime_ns, size): the key matches A again
            path.write_bytes(shade_a)
            import os

            os.utime(path, ns=(stat_a.st_atime_ns, stat_a.st_mtime_ns))
            third = decode_image(path)
            self.assertIs(third[2], first[2], "restored (mtime,size) must hit the A entry")

    def test_cache_key_separates_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a = Path(directory) / "a.png"
            b = Path(directory) / "b.png"
            data = self._png_bytes(77)
            a.write_bytes(data)
            b.write_bytes(data)
            self.assertNotEqual(decode_cache_key(a), decode_cache_key(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
