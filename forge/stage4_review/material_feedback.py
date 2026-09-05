#!/usr/bin/env python3
"""Apply bounded, material-scoped feedback from crop comparison results."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "stage4_review"))
from correction_loop import decide  # noqa: E402
sys.path.insert(0, str(ROOT / "forge"))
from materials.reference import load_reference  # noqa: E402


def _prior_bounds(material: dict[str, Any], property_name: str, registry: dict[str, Any]) -> tuple[float, float]:
    profile_id = material.get("referenceMaterialId")
    profile = next((item for item in registry.get("materials", []) if item.get("id") == profile_id), None)
    prior = profile.get("renderPrior", {}).get(property_name) if isinstance(profile, dict) else None
    if isinstance(prior, dict) and isinstance(prior.get("range"), list):
        return float(prior["range"][0]), float(prior["range"][1])
    return (0.0, 1.0)


def _base(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("base"), (int, float)):
        return float(value["base"])
    return fallback


def propose_patch(material: dict[str, Any], comparison: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    mismatches = set(comparison.get("mismatches", []))
    changes: dict[str, Any] = {}
    reasons: list[str] = []
    if "wrong-roughness-or-microstructure" in mismatches or "highlight-response-too-flat" in mismatches:
        low, high = _prior_bounds(material, "roughness", registry)
        current = _base(material.get("roughness"), (low + high) / 2)
        value = min(high, current + 0.08)
        changes["roughness"] = {"base": round(value, 4), "variation": _base(material.get("roughness"), 0.0) * 0.0}
        reasons.append("increase roughness within the profile prior")
    if "highlight-response-too-sharp" in mismatches:
        low, high = _prior_bounds(material, "roughness", registry)
        current = _base(material.get("roughness"), (low + high) / 2)
        changes["roughness"] = {"base": round(max(low, current - 0.08), 4), "variation": 0.0}
        reasons.append("reduce roughness within the profile prior")
    if "missing-environment-response" in mismatches:
        changes["needsEnvironment"] = True
        reasons.append("require a stable PMREM environment for conductor/transmissive response")
    if "wrong-base-color" in mismatches:
        target = comparison.get("render", {}).get("meanRgb")
        reference = comparison.get("reference", {}).get("meanRgb")
        if isinstance(reference, list) and len(reference) == 3:
            changes["baseColor"] = "#%02X%02X%02X" % tuple(max(0, min(255, int(value))) for value in reference)
            reasons.append("seed baseColor from the admitted reference crop; remove baked lighting in the next pass")
        else:
            reasons.append(f"base-color mismatch requires image evidence (render mean={target!r})")
    if "wrong-anisotropy" in mismatches:
        low, high = _prior_bounds(material, "anisotropy", registry)
        current = _base(material.get("anisotropy"), (low + high) / 2)
        changes["anisotropy"] = {"base": round(min(high, current + 0.1), 4), "variation": 0.0}
        reasons.append("increase anisotropy and recapture a grazing view")
    return {"changes": changes, "reasons": reasons, "requiresInput": not changes and bool(mismatches)}


def apply_material_feedback(
    spec: dict[str, Any],
    material_id: str,
    comparison: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    max_iter: int = 6,
) -> dict[str, Any]:
    registry = registry or load_reference()
    material = next((item for item in spec.get("materials", []) if isinstance(item, dict) and item.get("id") == material_id), None)
    if material is None:
        raise ValueError(f"material {material_id!r} not found in spec")
    scores = comparison.get("metrics", {}).get("scores", {})
    iteration_history = list(history or spec.get("materialFeedbackHistory", []))
    entry = {
        "fidelity": float(scores.get("overall", 0.0)),
        "defectTags": list(comparison.get("mismatches", [])),
        "reverted": False,
        "materialId": material_id,
        "comparisonArtifact": comparison.get("kind", "material-comparison"),
    }
    iteration_history.append(entry)
    decision = decide(iteration_history, target_fidelity=0.85, max_iter=max_iter, min_delta=0.02)
    patch = propose_patch(material, comparison, registry) if not decision["stop"] or decision["action"] == "refine-code" else {"changes": {}, "reasons": []}
    before = {key: copy.deepcopy(material.get(key)) for key in patch["changes"]}
    for key, value in patch["changes"].items():
        material[key] = value
    after = {key: copy.deepcopy(material.get(key)) for key in patch["changes"]}
    spec.setdefault("materialFeedbackHistory", []).append({
        "materialId": material_id,
        "comparison": copy.deepcopy(comparison),
        "decision": decision,
        "patch": patch,
        "before": before,
        "after": after,
    })
    return {
        "decision": decision,
        "patch": patch,
        "materialId": material_id,
        "historyLength": len(iteration_history),
        "spec": spec,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--max-iter", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
        result = apply_material_feedback(spec, args.material_id, comparison, max_iter=args.max_iter)
        output = args.spec if args.in_place else args.out
        if output is None:
            raise ValueError("choose --in-place or --out")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result["spec"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"decision": result["decision"], "patch": result["patch"], "out": str(output.resolve())}, indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
