"""Perceptual image hashing (DCT-based pHash), pure Python stdlib.

Plan 1.3. Shared by:
  - reference-admission duplicate detection (stage1_intake/check_reference_admission.py, §4.5.2)
  - the Divine Eye's coarse "same-thing?" pre-gate signal (stage4_review/divine_eye.py, §3.1)

Classic pHash: grayscale -> downsample to N×N (default 32) -> 2D DCT-II -> keep the
top-left low-frequency K×K block (default 8, excluding the DC term for robustness to
overall brightness) -> hash bit = coefficient > median. Produces a 64-bit integer whose
Hamming distance approximates perceptual similarity and is invariant to scale, small
blur, and uniform brightness shifts — exactly the invariances a duplicate/near-duplicate
check wants. No PIL/numpy.
"""

from __future__ import annotations

import math

_DCT_CACHE: dict[int, list[list[float]]] = {}


def _dct_matrix(n: int) -> list[list[float]]:
    """Cached DCT-II basis matrix M where out = M @ in."""
    cached = _DCT_CACHE.get(n)
    if cached is not None:
        return cached
    matrix: list[list[float]] = []
    factor = math.pi / (2.0 * n)
    for k in range(n):
        scale = math.sqrt(1.0 / n) if k == 0 else math.sqrt(2.0 / n)
        row = [scale * math.cos((2 * i + 1) * k * factor) for i in range(n)]
        matrix.append(row)
    _DCT_CACHE[n] = matrix
    return matrix


def _dct_2d(block: list[list[float]]) -> list[list[float]]:
    """Separable 2D DCT-II of a square matrix."""
    n = len(block)
    m = _dct_matrix(n)
    # rows: temp = M @ block
    temp = [[sum(m[k][i] * block[i][j] for i in range(n)) for j in range(n)] for k in range(n)]
    # cols: out = temp @ M^T
    out = [[sum(temp[k][j] * m[l][j] for j in range(n)) for l in range(n)] for k in range(n)]
    return out


def to_grayscale_downsampled(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    size: int = 32,
) -> list[list[float]]:
    """Box-average downsample of the luminance channel to size×size (Rec.709 luma)."""
    if width <= 0 or height <= 0 or not pixels:
        return [[0.0] * size for _ in range(size)]
    out = [[0.0] * size for _ in range(size)]
    counts = [[0] * size for _ in range(size)]
    for idx, (r, g, b, _a) in enumerate(pixels):
        x = idx % width
        y = idx // width
        if y >= height:
            break
        sx = min(size - 1, x * size // width)
        sy = min(size - 1, y * size // height)
        out[sy][sx] += 0.2126 * r + 0.7152 * g + 0.0722 * b
        counts[sy][sx] += 1
    for sy in range(size):
        for sx in range(size):
            c = counts[sy][sx]
            if c:
                out[sy][sx] /= c
    return out


def phash(gray: list[list[float]], hash_size: int = 8) -> int:
    """64-bit pHash (for hash_size=8) from a square grayscale matrix (side ≥ hash_size)."""
    n = len(gray)
    if n < hash_size:
        raise ValueError(f"grayscale side {n} smaller than hash_size {hash_size}")
    coeffs = _dct_2d(gray)
    low = [coeffs[k][l] for k in range(hash_size) for l in range(hash_size)]
    # Exclude the DC term (index 0) from the median so overall brightness doesn't dominate.
    ac = low[1:]
    ordered = sorted(ac)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])
    bits = 0
    for i, value in enumerate(low):
        bits <<= 1
        if value > median:
            bits |= 1
    return bits


def phash_from_image(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    hash_size: int = 8,
    img_size: int = 32,
) -> int:
    return phash(to_grayscale_downsampled(width, height, pixels, img_size), hash_size)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def normalized_similarity(a: int, b: int, bits: int = 64) -> float:
    """1.0 = identical hash, 0.0 = maximally different. Used as a [0,1] agreement score."""
    return 1.0 - hamming(a, b) / bits
