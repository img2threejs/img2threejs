#!/usr/bin/env python3
"""Attachment Anchor Gate: does every worn/held item actually relate to what it hangs from?

Two production defects share one root cause. A conical hat that should sit behind the
character's shoulders rendered at hip height, off to one side. A charm that should hang from
the butt of a staff rendered detached, four units away and below the ground plane. Measured:
the hat's bounding box spanned y 1.107-4.064 while the shoulder joint sat at y 3.829; the charm
sat 0.389 from the staff surface with no connecting geometry at all. Both were authored as
children of the model ROOT rather than of the thing they are attached to, so nothing tied
their world position to their anchor, and nothing checked it afterward. No gate in this repo
relates a worn or held item to what it is worn on or held by -- `ATTACHMENT_ROLES` in
validate_sculpt_spec.py is a DIFFERENT contract (limbs/branches/handles that must EMBED into
their parent's geometry, via `attachment.parentSocket`/`localStart`/`localEnd`); it has nothing
to say about a rigid item that merely has to sit near a bone or component it is not welded to.

Five checks. The first four run over the spec alone; the fifth only runs when the caller
supplies measured world positions (e.g. from a runtime parts manifest), because that is the
only way to catch a hat that is correctly *declared* but wrongly *placed*.

  ANCHOR_DECLARED    every worn/held/hung component must name an anchor.
  ANCHOR_RESOLVES    the named anchor must be a real component, or (with a rig) a real bone.
  ANCHOR_NOT_ROOT    the anchor must not be the model ROOT -- the literal bug both defects
                     shared: parenting to root means the item's transform carries no
                     relationship at all to the body part it belongs to.
  ANCHOR_NOT_CYCLIC  following anchors must not loop, and an item must not anchor to itself.
  ANCHOR_PROXIMITY   (measured positions only) the item's measured position must sit within
                     its anchor's declared or defaulted `maxOffset`.

A component missing from the measured positions is never silently treated as passing --
it is reported in `unmeasuredAttachments`, because "0 violations" from a check that never
ran on anything is the exact failure mode that let both defects ship.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Vocabulary for "this component IS a worn/held/hung attachment" -- deliberately small and
# disjoint from ATTACHMENT_ROLES in validate_sculpt_spec.py, which flags a component that
# needs an EMBEDDING contract (a limb welding into its parent's geometry). This gate is about
# an item's world-position relationship to what it hangs from, not about a geometry weld, so
# the vocabularies must not collide: "arm"/"leg"/"branch" belong to the other gate, not here.
ANCHOR_ATTACHMENT_TOKENS = {"attachment", "accessory", "worn", "held", "prop", "equipment"}

# ANCHOR_PROXIMITY default #1: when the anchor is a component with a `dimensions` block, the
# allowed drift is a fraction of the anchor's own largest declared extent -- a small anchor
# (a staff) tolerates only a small absolute offset, a large one (a torso) tolerates more. This
# is what makes the charm regression fire without an authored `maxOffset`: a 0.1-wide staff
# caps drift at 0.025, far under the charm's real 0.389 detachment.
ANCHOR_PROXIMITY_SIZE_FRACTION = 0.25
_EXTENT_FIELDS = ("width", "height", "depth", "length")

# ANCHOR_PROXIMITY default #2: when the anchor supplies no extents at all (a bone, or a
# component with no `dimensions`), fall back to this constant. Chosen to be small relative to
# the unit-scale specs this repo authors (compare PROPORTION_LIMIT_FRACTION in
# validate_sculpt_spec.py, which reasons the same way over a different quantity).
DEFAULT_MAX_OFFSET = 0.3


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_xyz(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(_is_number(v) for v in value)


def _distance(a: list[float], b: list[float]) -> float:
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5


def _is_attachment_component(component: dict[str, Any]) -> bool:
    """A component counts as a worn/held/hung attachment when it already declares
    `attachment.anchor`, or when role/category/kind names it with the small vocabulary above.
    The second path exists so ANCHOR_DECLARED has something to fire on: a component authored
    as `role: "worn"` with no anchor at all must still be caught, not silently ignored because
    it never reached the "has an anchor" branch."""
    attachment = component.get("attachment")
    if isinstance(attachment, dict) and isinstance(attachment.get("anchor"), str) and attachment["anchor"].strip():
        return True
    for field in ("role", "category", "kind"):
        value = component.get(field)
        if isinstance(value, str) and value.strip().lower() in ANCHOR_ATTACHMENT_TOKENS:
            return True
    return False


def _anchor_id(component: dict[str, Any]) -> str | None:
    attachment = component.get("attachment")
    if isinstance(attachment, dict):
        anchor = attachment.get("anchor")
        if isinstance(anchor, str) and anchor.strip():
            return anchor
    return None


def _find_cycle(start: str, anchor_of: dict[str, str]) -> list[str] | None:
    """Walk the anchor-of-anchor chain from `start`. Returns the cyclic segment (in order,
    NOT including the repeated closing node) the first time a node repeats, or None if the
    chain runs off the edge of the attachment graph (into a bone, a plain component, or a
    dangling id -- none of which are keys in `anchor_of`, so the `while` below simply stops)."""
    path: list[str] = []
    current = start
    while current in anchor_of:
        if current in path:
            return path[path.index(current):]
        path.append(current)
        current = anchor_of[current]
    return None


def _anchor_extent(anchor_component: dict[str, Any] | None) -> float | None:
    """Largest declared extent on the anchor's `dimensions` block, or None if it has none.
    `radius` is doubled to a diameter so it is comparable to width/height/depth/length."""
    if not isinstance(anchor_component, dict):
        return None
    dimensions = anchor_component.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    values = [float(dimensions[f]) for f in _EXTENT_FIELDS if _is_number(dimensions.get(f))]
    if _is_number(dimensions.get("radius")):
        values.append(float(dimensions["radius"]) * 2.0)
    return max(values) if values else None


def _max_offset_for(component: dict[str, Any], anchor_component: dict[str, Any] | None) -> tuple[float, str]:
    """Return (limit, rule) so a proximity failure can say WHICH rule produced the number
    instead of a bare threshold. Priority: declared `maxOffset` > anchor-size default >
    constant fallback."""
    attachment = component.get("attachment") if isinstance(component.get("attachment"), dict) else {}
    declared = attachment.get("maxOffset")
    if _is_number(declared):
        return float(declared), "declared attachment.maxOffset"
    extent = _anchor_extent(anchor_component)
    if extent is not None:
        limit = extent * ANCHOR_PROXIMITY_SIZE_FRACTION
        return limit, (
            f"default: {ANCHOR_PROXIMITY_SIZE_FRACTION:.0%} of anchor's largest declared extent "
            f"({extent:.4f})"
        )
    return DEFAULT_MAX_OFFSET, f"default: constant fallback ({DEFAULT_MAX_OFFSET}); anchor declares no extents"


def analyze_attachments(spec: dict[str, Any], *, measured: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """Run the attachment-anchor gate over `spec`'s component tree, optionally cross-checked
    against `measured` (world positions keyed by component or bone id, e.g. from a runtime
    parts manifest). See module docstring for the five checks.

    Returns a dict with `errors`, `warnings`, per-attachment `attachments` records (component,
    anchor, anchorKind, distance, maxOffset, offsetRule, withinLimit -- the last four are None
    until a measured position resolves them), `unmeasuredAttachments`, `attachmentCount`, and
    `passed` (True iff `errors` is empty).
    """
    errors: list[str] = []
    warnings: list[str] = []

    components = [
        c for c in (spec.get("componentTree") or []) if isinstance(c, dict) and isinstance(c.get("id"), str)
    ]
    by_id = {c["id"]: c for c in components}
    rig = spec.get("rig")
    bones = (
        [b for b in (rig.get("bones") or []) if isinstance(b, dict) and isinstance(b.get("id"), str)]
        if isinstance(rig, dict) else []
    )
    bones_by_id = {b["id"]: b for b in bones}

    # Root identity, both trees. A malformed multi-root component tree is
    # validate_sculpt_spec.py's problem (NAME_UNIQUENESS), not this gate's -- we only need
    # SOME root id to test ANCHOR_NOT_ROOT against, so ambiguity does not stop the gate.
    tree_roots = [c["id"] for c in components if not c.get("parent")]
    tree_root_id = tree_roots[0] if tree_roots else None
    rig_roots = [b["id"] for b in bones if not b.get("parent")]
    rig_root_id = rig_roots[0] if rig_roots else None

    attachment_components = [c for c in components if _is_attachment_component(c)]
    anchor_of: dict[str, str] = {}
    records: list[dict[str, Any]] = []

    for component in attachment_components:
        cid = component["id"]
        anchor = _anchor_id(component)
        record: dict[str, Any] = {
            "component": cid,
            "anchor": anchor,
            "anchorKind": None,
            "distance": None,
            "maxOffset": None,
            "offsetRule": None,
            "withinLimit": None,
        }
        if anchor is None:
            errors.append(
                f"ANCHOR_DECLARED: attachment {cid!r} is worn/held/hung but declares no attachment.anchor"
            )
            records.append(record)
            continue

        anchor_of[cid] = anchor
        if anchor in by_id:
            record["anchorKind"] = "component"
        elif anchor in bones_by_id:
            record["anchorKind"] = "bone"
        else:
            errors.append(
                f"ANCHOR_RESOLVES: attachment {cid!r} anchors to {anchor!r}, which matches no "
                f"existing component" + (" or bone" if isinstance(rig, dict) else "") + " in this spec"
            )
            records.append(record)
            continue

        is_root = (
            (record["anchorKind"] == "component" and anchor == tree_root_id)
            or (record["anchorKind"] == "bone" and anchor == rig_root_id)
        )
        if is_root:
            errors.append(
                f"ANCHOR_NOT_ROOT: attachment {cid!r} anchors directly to the model ROOT ({anchor!r}) "
                f"-- parenting a worn/held item to root leaves its transform unrelated to the body "
                f"part it belongs to; this is the exact defect behind both the misplaced hat and "
                f"the detached charm"
            )
        records.append(record)

    # ---- ANCHOR_NOT_CYCLIC -- self-anchor is the length-1 case of this same walk. ----
    reported_cycles: set[frozenset[str]] = set()
    for start in anchor_of:
        cycle = _find_cycle(start, anchor_of)
        if not cycle:
            continue
        key = frozenset(cycle)
        if key in reported_cycles:
            continue
        reported_cycles.add(key)
        if len(cycle) == 1:
            errors.append(f"ANCHOR_NOT_CYCLIC: attachment {cycle[0]!r} anchors to itself")
        else:
            chain = " -> ".join([*cycle, cycle[0]])
            errors.append(f"ANCHOR_NOT_CYCLIC: attachment anchor cycle detected: {chain}")

    # ---- ANCHOR_PROXIMITY -- only when the caller supplies measured world positions. ----
    unmeasured: list[str] = []
    if measured is not None:
        for record in records:
            if record["anchorKind"] is None:
                continue  # ANCHOR_DECLARED/ANCHOR_RESOLVES already failed; no anchor to measure against
            cid = record["component"]
            anchor = record["anchor"]
            item_pos = measured.get(cid)
            anchor_pos = measured.get(anchor)
            if not _is_xyz(item_pos) or not _is_xyz(anchor_pos):
                # Never silently skip: a component the caller forgot to measure is reported,
                # not swept into "0 violations" the way both real defects went unnoticed.
                unmeasured.append(cid)
                warnings.append(
                    f"ANCHOR_PROXIMITY: attachment {cid!r} has no measured world position (or its "
                    f"anchor {anchor!r} has none); proximity was not verified"
                )
                continue
            anchor_component = by_id.get(anchor) if record["anchorKind"] == "component" else None
            max_offset, rule = _max_offset_for(by_id[cid], anchor_component)
            distance = _distance(item_pos, anchor_pos)
            record["distance"] = distance
            record["maxOffset"] = max_offset
            record["offsetRule"] = rule
            record["withinLimit"] = distance <= max_offset
            if distance > max_offset:
                errors.append(
                    f"ANCHOR_PROXIMITY: attachment {cid!r} measures {distance:.4f} from anchor "
                    f"{anchor!r}, exceeding maxOffset {max_offset:.4f} ({rule})"
                )

    return {
        "errors": errors,
        "warnings": warnings,
        "attachments": records,
        "unmeasuredAttachments": unmeasured,
        "attachmentCount": len(attachment_components),
        "passed": not errors,
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_measured(raw: str) -> dict[str, list[float]]:
    """`--measured` accepts either inline JSON text or a path to a JSON file: a handful of
    positions is convenient to type inline, but a real runtime parts manifest is a file."""
    candidate = Path(raw)
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else raw
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("--measured must decode to a JSON object of id -> [x, y, z]")
    return data


def _format_summary(result: dict[str, Any]) -> str:
    lines = [f"attachments: {result['attachmentCount']}"]
    for record in result["attachments"]:
        bits = [f"component={record['component']!r}", f"anchor={record['anchor']!r}"]
        if record["distance"] is not None:
            bits.append(f"distance={record['distance']:.4f}")
            bits.append(f"maxOffset={record['maxOffset']:.4f}")
            bits.append(f"rule={record['offsetRule']}")
            bits.append("within-limit" if record["withinLimit"] else "OVER-LIMIT")
        lines.append("  " + " ".join(bits))
    if result["unmeasuredAttachments"]:
        lines.append(f"unmeasured: {', '.join(result['unmeasuredAttachments'])}")
    for message in result["errors"]:
        lines.append(f"ERROR: {message}")
    for message in result["warnings"]:
        lines.append(f"WARNING: {message}")
    lines.append("verdict: " + ("PASS" if result["passed"] else "FAIL"))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path, help="sculpt-spec JSON path")
    parser.add_argument(
        "--measured",
        help="JSON object (inline text or a file path) of id -> [x, y, z] world positions",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    try:
        spec = _load_json(args.spec)
        measured = _parse_measured(args.measured) if args.measured is not None else None
        result = analyze_attachments(spec, measured=measured)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(_format_summary(result))
        return 0 if result["passed"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


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
