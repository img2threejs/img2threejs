#!/usr/bin/env python3
"""pre_validate_image: Analyse une image de référence avant la génération du spec.

Vérifications :
  1. Proportions de l'image — trop large/petite → qualité insuffisante
  2. Format, canaux, espace colorimétrique
  3. Variance des bords — sujet centré ou qui touche les bords ?
  4. Entropie des coins — fond uni ou bruité ?
  5. Occupation du cadre — le sujet est-il assez grand ?

Usage :
  python3 tools/pre_validate_image.py <image-path>
  python3 tools/pre_validate_image.py <image-path> --json   # sortie JSON structurée
"""

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path
from typing import Any


def read_png(path: str) -> tuple[int, int, int, bytes]:
    """Lit un PNG et retourne (largeur, hauteur, canaux, pixels bruts)."""
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a PNG file")
        chunk_len = struct.unpack(">I", f.read(4))[0]  # should be 13
        chunk_type = f.read(4)
        if chunk_type != b"IHDR" or chunk_len < 13:
            raise ValueError("Missing IHDR chunk")
        ihdr = f.read(13)
        # IHDR: width(4) height(4) bitDepth(1) colorType(1) compression(1) filter(1) interlace(1)
        w, h = struct.unpack(">II", ihdr[:8])
        depth = ihdr[8]
        ctype = ihdr[9]
        # Déduire le nombre de canaux
        if ctype == 0:
            channels = 1  # Greyscale
        elif ctype == 2:
            channels = 3  # RGB
        elif ctype == 6:
            channels = 4  # RGBA
        else:
            channels = 3  # fallback
        f.read(4)  # IHDR CRC (skip)
        # Trouver le début IDAT
        raw = b""
        while True:
            chunk_len = struct.unpack(">I", f.read(4))[0]
            chunk_type = f.read(4)
            data = f.read(chunk_len) if chunk_len > 0 else b""
            f.read(4)  # crc
            if chunk_type == b"IDAT":
                raw += data
            elif chunk_type == b"IEND":
                break
    dec = zlib.decompress(raw)
    return w, h, channels, dec


def edge_variance(pixels: bytes, w: int, h: int, channels: int, margin: int = 3) -> dict:
    """Calcule la variance des pixels sur les bords de l'image.
    Un fond uni → variance très faible (< 5).
    Du contenu qui touche le bord → variance élevée (> 20)."""
    stats = {"top": [], "bottom": [], "left": [], "right": []}
    for y in range(margin):
        for x in range(w):
            offset = (y * w + x) * channels
            stats["top"].append(pixels[offset])
    for y in range(h - margin, h):
        for x in range(w):
            offset = (y * w + x) * channels
            stats["bottom"].append(pixels[offset])
    for x in range(margin):
        for y in range(h):
            offset = (y * w + x) * channels
            stats["left"].append(pixels[offset])
    for x in range(w - margin, w):
        for y in range(h):
            offset = (y * w + x) * channels
            stats["right"].append(pixels[offset])
    result = {}
    for side, vals in stats.items():
        if len(vals) < 2:
            mean = float(sum(vals))
            var = 0.0
        else:
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
        result[side] = {"mean": round(mean, 1), "variance": round(var, 1)}
    return result


def corner_entropy(pixels: bytes, w: int, h: int, channels: int, size: int = 16) -> dict:
    """Entropie des 4 coins. Un fond uni → entropie proche de 0. Du bruit → > 3."""
    regions = {"top-left": (0, 0), "top-right": (w - size, 0),
               "bottom-left": (0, h - size), "bottom-right": (w - size, h - size)}
    result = {}
    for name, (ox, oy) in regions.items():
        hist = [0] * 256
        total = 0
        for y in range(oy, min(oy + size, h)):
            for x in range(ox, min(ox + size, w)):
                offset = (y * w + x) * channels
                val = pixels[offset]  # canal rouge suffit
                hist[val] += 1
                total += 1
        if total == 0:
            result[name] = 0.0
            continue
        entropy = 0.0
        for count in hist:
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)
        result[name] = round(entropy, 2)
    return result


def subject_frame_coverage(pixels: bytes, w: int, h: int, channels: int,
                           bg_threshold: int = 30) -> float:
    """Estime la proportion du cadre occupée par le sujet (pixels non-fond).
    Utilise la variance locale pour détecter le sujet vs fond uni."""
    # Échantillonnage: grille 32×32
    step_x = max(1, w // 32)
    step_y = max(1, h // 32)
    subject_pixels = 0
    total_samples = 0
    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            total_samples += 1
            offset = (y * w + x) * channels
            r, g, b = pixels[offset], pixels[offset + 1], pixels[offset + 2]
            # Détection simple: si le pixel n'est pas gris uniforme (variance RGB > threshold)
            var_rgb = abs(r - g) + abs(g - b) + abs(r - b)
            if var_rgb > bg_threshold:
                subject_pixels += 1
    return round(subject_pixels / max(1, total_samples), 3)


def validate_image(path: str) -> dict[str, Any]:
    """Analyse complète d'une image de référence."""
    img = Path(path)
    if not img.exists():
        return {"valid": False, "error": f"File not found: {path}"}

    size_kb = img.stat().st_size / 1024
    result: dict[str, Any] = {
        "file": str(img.resolve()),
        "size_kb": round(size_kb, 1),
        "valid": True,
    }

    try:
        w, h, channels, pixels = read_png(str(img))
    except (ValueError, struct.error, zlib.error) as e:
        # Fallback: essayer de lire comme JPEG via le système
        result["valid"] = False
        result["error"] = f"Not a valid PNG: {e}"
        result["warning"] = "Only PNG supported for pixel analysis. JPEG files will be accepted without edge analysis."
        return result

    result["width"] = w
    result["height"] = h
    result["channels"] = channels
    result["megapixels"] = round(w * h / 1_000_000, 2)

    # --- Checks ---
    issues = []
    warnings = []

    # Résolution minimale
    if w < 512 or h < 512:
        issues.append(f"Resolution too low ({w}×{h}), minimum 512×512")
    elif w < 1024 or h < 1024:
        warnings.append(f"Resolution marginal ({w}×{h}), 1024×1024+ recommended")

    # Occupation du cadre
    coverage = subject_frame_coverage(pixels, w, h, channels)
    result["subject_coverage"] = coverage
    if coverage < 0.2:
        issues.append(f"Subject occupies only {coverage:.0%} of frame — too small for reconstruction")
    elif coverage < 0.4:
        warnings.append(f"Subject occupies {coverage:.0%} of frame — marginal, expect shallow component tree")

    # Variance des bords (sujet touche-t-il les bords?)
    edges = edge_variance(pixels, w, h, channels)
    result["edge_variance"] = edges
    high_var_sides = [side for side, v in edges.items() if v["variance"] > 30]
    if high_var_sides:
        warnings.append(f"Subject touches frame edges ({', '.join(high_var_sides)}) — may include surrounding context")

    # Entropie des coins (fond uni?)
    corners = corner_entropy(pixels, w, h, channels)
    result["corner_entropy"] = corners
    noisy_corners = [name for name, e in corners.items() if e > 2.5]
    if noisy_corners:
        warnings.append(f"Background corners are not uniform ({', '.join(noisy_corners)}) — subject may blend into background")

    # Ratio d'aspect
    aspect = w / h
    result["aspect_ratio"] = round(aspect, 2)
    if aspect > 2.5 or aspect < 0.4:
        warnings.append(f"Aspect ratio {aspect:.2f} is extreme — consider cropping to square")

    result["issues"] = issues
    result["warnings"] = warnings
    result["score"] = "pass" if len(issues) == 0 else ("conditional" if len(issues) <= 2 else "reject")

    return result


def print_report(r: dict[str, Any]) -> None:
    """Affiche un rapport lisible."""
    print(f"\n{'='*50}")
    print(f"  Pre-Validation Report: {Path(r['file']).name}")
    print(f"{'='*50}")
    if not r.get("valid", True):
        print(f"  ❌ {r.get('error', 'Invalid')}")
        if r.get("warning"):
            print(f"  ⚠️  {r['warning']}")
        return
    print(f"  Size:   {r['width']}×{r['height']} ({r['megapixels']}MP, {r['size_kb']}KB)")
    print(f"  Aspect: {r['aspect_ratio']}")
    print(f"  Subject: {r['subject_coverage']:.0%} of frame")
    print(f"  Verdict: {'✅ pass' if r['score'] == 'pass' else '⚠️  ' + r['score']}")
    if r.get("issues"):
        print(f"\n  ❌ Issues:")
        for i in r["issues"]:
            print(f"     • {i}")
    if r.get("warnings"):
        print(f"\n  ⚠️  Warnings:")
        for w in r["warnings"]:
            print(f"     • {w}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-validate a reference image for 3D reconstruction")
    parser.add_argument("image", help="Path to reference PNG image")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()
    report = validate_image(args.image)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    sys.exit(0 if report.get("score") in ("pass", "conditional") else 1)
