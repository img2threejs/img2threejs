#!/usr/bin/env python3
"""Reference-grounded gradient-stop extractor. Pure stdlib.

Samples the TRUE colour gradient of a material along a structural axis directly from the
reference crop, so procedural texture STOPS are grounded in the photo instead of hand-guessed
(the root cause of the M9 Doppler "purple guessed → rendered blue" 4-iteration failure).

Method (Context Part 2.1): foreground-masked PER-BAND MEDIAN sampling — split the object into N
bands along the axis, take the median RGB per band (median resists specular outliers far better
than mean), convert to HSV, assign a deterministic hue name, and flag any violet/purple stop that
is blue-leaning (B > R) as `hueRisk: blue-collapse` (Context Part 2.3 — such a hue collapses to
blue under ACES/Reinhard tone-mapping; fix = magenta-lean R>=B + green crosstalk).

CLI:  extract_gradient_stops.py <crop.png> [--axis u|v] [--stops N] [--json]
API:  extract_gradient_stops(path, axis="u", stops=6) -> dict
"""
from __future__ import annotations

import argparse
import colorsys
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402

# Deterministic hue-name lookup over the HSV hue circle (degrees).
_HUE_NAMES = [
    (15, "red"), (45, "orange"), (70, "yellow"), (165, "green"),
    (195, "cyan"), (255, "blue"), (290, "violet"), (345, "magenta"), (360, "red"),
]


def hue_name(h_deg: float) -> str:
    for upper, name in _HUE_NAMES:
        if h_deg < upper:
            return name
    return "red"


def _median(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[len(s) // 2]


def sample_banded_stops(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    mask: list[bool],
    axis: str,
    stops: int,
) -> list[dict[str, Any]]:
    """Median RGB per band along the axis. axis 'u' = length/x, 'v' = height/y."""
    span = width if axis == "u" else height
    band = max(1, span // stops)
    out: list[dict[str, Any]] = []
    for b in range(stops):
        lo = b * band
        hi = span if b == stops - 1 else (b + 1) * band
        rs: list[int] = []
        gs: list[int] = []
        bs: list[int] = []
        for y in range(height):
            for x in range(width):
                coord = x if axis == "u" else y
                if coord < lo or coord >= hi:
                    continue
                idx = y * width + x
                if idx >= len(mask) or not mask[idx]:
                    continue
                r, g, bl, _a = pixels[idx]
                rs.append(r)
                gs.append(g)
                bs.append(bl)
        if not rs:
            continue  # empty band (no foreground) — skip, don't fabricate a colour
        r, g, bl = _median(rs), _median(gs), _median(bs)
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, bl / 255.0)
        h_deg = h * 360.0
        name = hue_name(h_deg)
        stop: dict[str, Any] = {
            "t": round((b + 0.5) / stops, 4),
            "rgb": [r, g, bl],
            "hsv": [round(h_deg, 1), round(s, 3), round(v, 3)],
            "hueName": name,
        }
        # Hue-survival flag: a blue-leaning violet/purple (B > R) collapses to blue under
        # tone-mapping. Suggest a magenta-lean correction (raise R above B + a little green).
        if name in ("violet", "magenta", "blue") and bl > r and s > 0.15:
            stop["hueRisk"] = "blue-collapse"
            stop["suggestedRgb"] = [min(255, bl), max(g, int(bl * 0.25)), r]
        out.append(stop)
    return out


def extract_gradient_stops(path: Path, axis: str = "u", stops: int = 6) -> dict[str, Any]:
    width, height, pixels, warnings = load_image(path)
    mask, _meta, mask_warnings = build_foreground_mask(width, height, pixels)
    warnings.extend(mask_warnings)
    band_stops = sample_banded_stops(width, height, pixels, mask, axis, stops)
    # Collapse consecutive same-hue bands into named hue zones.
    zones: list[dict[str, Any]] = []
    for st in band_stops:
        if zones and zones[-1]["hueName"] == st["hueName"]:
            zones[-1]["tEnd"] = st["t"]
        else:
            zones.append({"hueName": st["hueName"], "tStart": st["t"], "tEnd": st["t"]})
    return {
        "axis": axis,
        "requestedStops": stops,
        "stops": band_stops,
        "hueZones": zones,
        "riskFlags": [s for s in band_stops if s.get("hueRisk")],
        "warnings": warnings,
        "reference": str(path.resolve()),
        "note": "band-median stops grounded in the reference; feed to the texture painter instead "
                "of hand-guessed STOPS. hueRisk=blue-collapse stops need a magenta-lean correction.",
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Extract reference-grounded gradient stops (stdlib).")
    ap.add_argument("crop", type=Path)
    ap.add_argument("--axis", choices=["u", "v"], default="u")
    ap.add_argument("--stops", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.crop.exists():
        print(f"error: {args.crop} not found", file=sys.stderr)
        return 2
    result = extract_gradient_stops(args.crop, axis=args.axis, stops=max(2, args.stops))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"axis={result['axis']} stops={len(result['stops'])}")
        for s in result["stops"]:
            risk = f"  ⚠ {s['hueRisk']} → try rgb {s.get('suggestedRgb')}" if s.get("hueRisk") else ""
            print(f"  t={s['t']:<5} rgb={s['rgb']} {s['hueName']:<8} hsv={s['hsv']}{risk}")
        print("hue zones:", " → ".join(z["hueName"] for z in result["hueZones"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
