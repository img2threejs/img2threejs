"""Content-based opposing view detection using silhouette IoU and COLOR histogram.

Replaces filename-dependent opposing view detection with computer vision
techniques that work regardless of how images are named.

Key algorithm decisions:
- Otsu thresholding for foreground mask extraction (CS2 items have white backgrounds)
- COLOR (BGR) histogram on object pixels (inverted mask) — grayscale fails for color diffs
- Bhattacharyya distance (0-1): 0 = identical, 1 = completely different
- IoU threshold > 0.75: same object opposing views
- Bhattacharyya threshold > 0.3: different colors/textures
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import NamedTuple

# Thresholds (tuned for CS2 item screenshots)
IOU_THRESHOLD = 0.75
BHATTACHARYYA_THRESHOLD = 0.3

# Histogram bins per channel (8x8x8 = 512 bins total)
HIST_BINS = 8


class ContentMatch(NamedTuple):
    """Result of content-based opposing view comparison."""
    iou: float
    bhattacharyya: float
    is_opposing: bool
    confidence: float


def _otsu_threshold(gray_pixels: list[int], width: int, height: int) -> int:
    """Compute Otsu's threshold for a grayscale image.

    Returns the optimal threshold that maximizes inter-class variance.
    """
    total = width * height
    if total == 0:
        return 128

    # Compute histogram
    hist = [0] * 256
    for pixel in gray_pixels:
        hist[min(255, max(0, pixel))] += 1

    # Otsu's method
    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = 0.0
    weight_bg = 0
    max_variance = 0.0
    threshold = 128

    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break

        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg

        variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if variance > max_variance:
            max_variance = variance
            threshold = t

    return threshold


def _extract_foreground_mask(gray_pixels: list[int], width: int, height: int) -> list[bool]:
    """Extract foreground mask using Otsu thresholding.

    For CS2 item screenshots: background is white (>threshold), object is darker.
    Returns True for foreground (object) pixels.
    """
    threshold = _otsu_threshold(gray_pixels, width, height)
    return [pixel <= threshold for pixel in gray_pixels]


def _compute_iou(mask_a: list[bool], mask_b: list[bool]) -> float:
    """Compute Intersection over Union between two binary masks."""
    intersection = 0
    union = 0
    for a, b in zip(mask_a, mask_b):
        if a or b:
            union += 1
            if a and b:
                intersection += 1
    return intersection / union if union else 0.0


def _flip_mask_horizontally(mask: list[bool], width: int, height: int) -> list[bool]:
    """Flip a mask horizontally (mirror along vertical axis)."""
    flipped = []
    for y in range(height):
        row_start = y * width
        row = mask[row_start:row_start + width]
        flipped.extend(reversed(row))
    return flipped


def _extract_color_histogram(
    rgb_pixels: list[tuple[int, int, int]],
    mask: list[bool],
    width: int,
    height: int,
    bins: int = HIST_BINS,
) -> list[float]:
    """Extract normalized BGR histogram from masked object pixels.

    Uses 3D histogram (bins x bins x bins) on the masked region only.
    Returns normalized histogram (sums to 1.0).
    """
    hist_size = bins ** 3
    hist = [0.0] * hist_size

    bin_width = 256.0 / bins

    for idx, is_fg in enumerate(mask):
        if not is_fg:
            continue
        r, g, b = rgb_pixels[idx]
        # Map to bin indices (BGR order for OpenCV compatibility)
        br = min(int(b / bin_width), bins - 1)
        gr = min(int(g / bin_width), bins - 1)
        rr = min(int(r / bin_width), bins - 1)
        bin_idx = br * bins * bins + gr * bins + rr
        hist[bin_idx] += 1.0

    # Normalize
    total = sum(hist)
    if total > 0:
        hist = [h / total for h in hist]

    return hist


def _bhattacharyya_distance(hist_a: list[float], hist_b: list[float]) -> float:
    """Compute Bhattacharyya distance between two histograms.

    Returns value in [0, 1]:
    - 0 = identical distributions
    - 1 = completely different distributions
    """
    bc = 0.0
    for a, b in zip(hist_a, hist_b):
        bc += math.sqrt(a * b)

    # Bhattacharyya coefficient
    bc = min(1.0, max(0.0, bc))

    # Convert to distance: D = sqrt(1 - BC)
    return math.sqrt(max(0.0, 1.0 - bc))


def _load_image_pixels(path: Path) -> tuple[int, int, list[tuple[int, int, int]], list[int]]:
    """Load image as RGB pixels and grayscale values.

    Returns (width, height, rgb_pixels, gray_pixels).
    """
    from PIL import Image

    img = Image.open(path).convert("RGB")
    width, height = img.size
    rgb_pixels = list(img.getdata())

    # Convert to grayscale using luminosity method
    gray_pixels = [int(0.299 * r + 0.587 * g + 0.114 * b) for r, g, b in rgb_pixels]

    return width, height, rgb_pixels, gray_pixels


def detect_opposing_by_content(
    path_a: Path,
    path_b: Path,
    iou_threshold: float = IOU_THRESHOLD,
    bhattacharyya_threshold: float = BHATTACHARYYA_THRESHOLD,
) -> ContentMatch:
    """Detect if two images are opposing views of the same object using content analysis.

    Uses:
    1. Otsu thresholding to extract foreground masks
    2. Silhouette IoU to measure shape similarity
    3. COLOR histogram on object pixels to measure appearance difference

    Args:
        path_a: Path to first image
        path_b: Path to second image
        iou_threshold: Minimum IoU to consider same object (default 0.75)
        bhattacharyya_threshold: Minimum Bhattacharyya distance to consider different colors (default 0.3)

    Returns:
        ContentMatch with IoU, Bhattacharyya distance, and opposing classification
    """
    width_a, height_a, rgb_a, gray_a = _load_image_pixels(path_a)
    width_b, height_b, rgb_b, gray_b = _load_image_pixels(path_b)

    # Extract foreground masks
    mask_a = _extract_foreground_mask(gray_a, width_a, height_a)
    mask_b = _extract_foreground_mask(gray_b, width_b, height_b)

    # Compute IoU on original masks
    iou = _compute_iou(mask_a, mask_b)

    # Also check flipped mask (opposing view may be mirrored)
    mask_b_flipped = _flip_mask_horizontally(mask_b, width_b, height_b)
    iou_flipped = _compute_iou(mask_a, mask_b_flipped)
    iou = max(iou, iou_flipped)

    # Extract COLOR histograms on object pixels
    hist_a = _extract_color_histogram(rgb_a, mask_a, width_a, height_a)
    hist_b = _extract_color_histogram(rgb_b, mask_b, width_b, height_b)

    # Compute Bhattacharyya distance
    bhattacharyya = _bhattacharyya_distance(hist_a, hist_b)

    # Decision logic:
    # - High IoU (>0.75) = same object silhouette
    # - High Bhattacharyya (>0.3) = different colors/textures
    # - Both conditions = opposing views (same shape, different surface)
    is_opposing = iou >= iou_threshold and bhattacharyya >= bhattacharyya_threshold

    # Confidence: weighted combination of both signals
    iou_confidence = min(1.0, iou / iou_threshold)
    color_confidence = min(1.0, bhattacharyya / bhattacharyya_threshold)
    confidence = 0.6 * iou_confidence + 0.4 * color_confidence

    return ContentMatch(
        iou=round(iou, 4),
        bhattacharyya=round(bhattacharyya, 4),
        is_opposing=is_opposing,
        confidence=round(confidence, 4),
    )


def find_opposing_pairs_in_group(
    paths: list[Path],
    iou_threshold: float = IOU_THRESHOLD,
    bhattacharyya_threshold: float = BHATTACHARYYA_THRESHOLD,
) -> list[tuple[int, int, ContentMatch]]:
    """Find all opposing view pairs in a group of images using content analysis.

    Performs pairwise comparison of all images to find opposing pairs.

    Args:
        paths: List of image paths
        iou_threshold: Minimum IoU to consider same object
        bhattacharyya_threshold: Minimum Bhattacharyya to consider different colors

    Returns:
        List of (index_a, index_b, ContentMatch) tuples for opposing pairs
    """
    pairs = []
    n = len(paths)

    for i in range(n):
        for j in range(i + 1, n):
            match = detect_opposing_by_content(
                paths[i],
                paths[j],
                iou_threshold,
                bhattacharyya_threshold,
            )
            if match.is_opposing:
                pairs.append((i, j, match))

    # Sort by confidence (highest first)
    pairs.sort(key=lambda x: x[2].confidence, reverse=True)

    return pairs
