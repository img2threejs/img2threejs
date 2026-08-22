#!/usr/bin/env python3
"""Back-project scene landmarks to the floor plan, with honesty gates (stage 1).

Input: `scene-camera.json` from `scene_camera.py` plus a landmark file:

    {"contacts":  {"bedFootLeft": [415, 583], ...},          # pixel of a floor contact
     "heights":   {"doorwayTop": {"px": [1070, 320], "foot": "doorwayThreshold"}, ...},
     "expected":  {"bedFootLeft": [x, z], ...}}              # optional, for re-projection checks

Output per contact: the floor point (x, z) in pattern units, AND the two numbers
that say whether to believe it:

- **depthSensitivityUnitsPerPx** — floor units of depth error per pixel of
  reading error at that pixel. Near the horizon this explodes; a contact with
  sensitivity 0.2 units/px is not a measurement, and silently placing furniture
  from it puts a cabinet three times too far away. Confidence tiers come from
  this number, not from wishing.
- **reprojection decomposition** (when `expected` is given) — the mean error
  vector is split into a UNIFORM component and per-landmark RESIDUALS. This is
  the diagnostic that matters: a uniform shift means ONE cause (almost always
  the camera — a principal-point offset looks exactly like this), while local
  residuals mean individual placements. Chasing furniture positions to fix a
  uniform error wastes a whole correction cycle; today's reference frame had a
  90px uniform shift that was entirely the principal point.

Pure Python stdlib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scene_camera import SceneCamera, load_camera  # noqa: E402

# confidence tiers on depth sensitivity (units of floor depth per px of reading error)
SENSITIVITY_MEASURED = 0.03    # below this: a 2px reading error moves the point < 0.06 units
SENSITIVITY_APPROX = 0.12     # below this: usable with care
# above SENSITIVITY_APPROX: "horizon-limited" — record, do not trust

UNIFORM_SHIFT_SUSPICIOUS_PX = 15.0   # a uniform reprojection shift above this smells of camera


def confidence(sens: float) -> str:
    if sens <= SENSITIVITY_MEASURED:
        return "measured"
    if sens <= SENSITIVITY_APPROX:
        return "approximate"
    return "horizon-limited"


def backproject(cam: SceneCamera, landmarks: dict[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = {}
    for name, (u, v) in landmarks.get("contacts", {}).items():
        pt = cam.floor(float(u), float(v))
        sens = cam.depth_sensitivity(float(u), float(v))
        if pt is None:
            plan[name] = {"pixel": [u, v], "floor": None,
                          "reason": "ray does not hit the floor (above the horizon?)"}
            continue
        plan[name] = {
            "pixel": [u, v],
            "floor": [round(pt[0], 3), round(pt[1], 3)],
            "distanceUnits": round(math.hypot(*pt), 3),
            "depthSensitivityUnitsPerPx": round(sens, 4),
            "confidence": confidence(sens),
        }

    heights: dict[str, Any] = {}
    for name, spec in landmarks.get("heights", {}).items():
        u, v = map(float, spec["px"])
        foot_name = spec["foot"]
        foot = plan.get(foot_name, {}).get("floor")
        if not foot:
            heights[name] = {"reason": f"foot contact '{foot_name}' unavailable"}
            continue
        try:
            h = cam.height_at(u, v, tuple(foot))
        except ValueError as e:
            heights[name] = {"reason": str(e)}
            continue
        heights[name] = {"pixel": [u, v], "foot": foot_name,
                         "heightUnits": round(h, 3),
                         "confidence": plan[foot_name]["confidence"]}
    return {"floorPlan": plan, "heights": heights}


def reprojection_check(cam: SceneCamera, landmarks: dict[str, Any],
                       result: dict[str, Any]) -> dict[str, Any] | None:
    expected = landmarks.get("expected")
    if not expected:
        return None
    rows = []
    for name, (x, z) in expected.items():
        entry = result["floorPlan"].get(name)
        if not entry or not entry.get("pixel"):
            continue
        q = cam.project((float(x), 0.0, float(z)))
        if q is None:
            continue
        u, v = entry["pixel"]
        rows.append({"landmark": name, "dx": q[0] - float(u), "dy": q[1] - float(v)})
    if not rows:
        return None
    mx = sum(r["dx"] for r in rows) / len(rows)
    my = sum(r["dy"] for r in rows) / len(rows)
    uniform = math.hypot(mx, my)
    residuals = [{"landmark": r["landmark"],
                  "residualPx": round(math.hypot(r["dx"] - mx, r["dy"] - my), 2)}
                 for r in rows]
    worst = max(residuals, key=lambda r: r["residualPx"])
    diagnosis = []
    if uniform > UNIFORM_SHIFT_SUSPICIOUS_PX:
        diagnosis.append(
            f"uniform shift of {uniform:.1f}px across all landmarks — one cause, almost "
            f"certainly the camera (check the principal point / view offset), NOT the "
            f"individual placements")
    if worst["residualPx"] > UNIFORM_SHIFT_SUSPICIOUS_PX:
        diagnosis.append(f"local residual on '{worst['landmark']}' "
                         f"({worst['residualPx']}px) — that placement is wrong")
    return {"uniformShiftPx": [round(mx, 2), round(my, 2)],
            "uniformShiftMagnitudePx": round(uniform, 2),
            "residuals": sorted(residuals, key=lambda r: -r["residualPx"]),
            "diagnosis": diagnosis or ["reprojection is clean"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("camera", help="scene-camera.json from scene_camera.py")
    ap.add_argument("landmarks", help="JSON with contacts / heights / expected")
    ap.add_argument("--out", default="scene-plan.json")
    args = ap.parse_args(argv)
    cam = load_camera(Path(args.camera))
    landmarks = json.loads(Path(args.landmarks).read_text())
    result = backproject(cam, landmarks)
    check = reprojection_check(cam, landmarks, result)
    if check:
        result["reprojectionCheck"] = check
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
