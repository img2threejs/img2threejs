"""Live capture authority for a program-reviewed, exact Pixi ESM closure.

Only trusted host code may construct ReviewedPixiPolicy after inspecting the
actual sources AND dependencies. Hashes identify that review; they do not perform
it. Keep the policy mapping live: removing/replacing approval revokes authority.
Reviewed code shares the browser/Pixi realm and is trusted code. This does NOT
establish confinement/security for arbitrary JavaScript, source/build equivalence,
or artwork quality. RouteAuthority must separately enforce gate + semantic review.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from route_parent import ParentError, _decode, _encode, _read
from stage0_route.images import decode

AREA = ".img2/artifacts/pixi2d/"
PRODUCER = "pixi2d-external-native-capture-v1"
PRODUCER_FILES = ("tools/replay-pixi2d.mjs", "skills/img2pixi2d/runtime/replay-host.mjs",
                  "skills/img2pixi2d/runtime/review-capture.mjs")
OPTIONS = {"pixi-package", "playwright-package", "chromium", "chromium-root"}
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition, reason):
    if not condition:
        raise ParentError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(path):
    _require(type(path) is str and path and not path.startswith("/") and ":" not in path
             and "\\" not in path and all(part not in {"", ".", ".."} for part in path.split("/")),
             "invalid-pixi-capture-binding")
    return path


def _binding(value):
    _require(type(value) is tuple and len(value) == 2 and type(value[1]) is str
             and SHA.fullmatch(value[1]), "invalid-pixi-capture-binding")
    _relative(value[0])
    return {"path": value[0], "sha256": value[1]}


def _absolute(value):
    _require(type(value) is str and Path(value).is_absolute()
             and str(Path(value).resolve(strict=True)) == value, "noncanonical-pixi-runtime-path")
    return Path(value)


@dataclass(frozen=True)
class ReviewedPixiPolicy:
    """Host-only approval, never deserialize from candidate/model/workspace JSON.

    All nested fields are immutable. *_json are canonical UTF-8 JSON bytes.
    snapshot_binding/reference_binding and sources/assets use (relative_path, sha).
    runtime_options is a tuple of (driver option, absolute canonical path).
    runtime_lock_json is the independently inspected complete `pins` result.
    review_id/notes identify the actual source/dependency review, not a flag that
    a candidate can submit. There is deliberately no auto-approve/policy loader.
    """
    object_id: str
    revision: int
    snapshot_binding: tuple[str, str]
    reference_binding: tuple[str, str]
    requirements_json: bytes
    sources: tuple[tuple[str, str], ...]
    assets: tuple[tuple[str, str], ...]
    plugin_root: str
    node: str
    runtime_options: tuple[tuple[str, str], ...]
    runtime_lock_json: bytes
    review_id: str
    review_notes: str

    def __post_init__(self):
        _require(type(self.object_id) is str and bool(self.object_id)
                 and type(self.revision) is int and self.revision > 0
                 and type(self.review_id) is str and bool(self.review_id.strip())
                 and type(self.review_notes) is str and bool(self.review_notes.strip()),
                 "invalid-reviewed-pixi-policy")
        for value in (self.snapshot_binding, self.reference_binding):
            _binding(value)
        _require(self.snapshot_binding[0] == AREA + "snapshot.json", "invalid-pixi-snapshot-binding")
        for values in (self.sources, self.assets):
            _require(type(values) is tuple, "immutable-pixi-closure-required")
            for value in values:
                _binding(value)
            _require(len({value[0] for value in values}) == len(values), "duplicate-pixi-closure-binding")
        _require(bool(self.sources) and not ({v[0] for v in self.sources} & {v[0] for v in self.assets}),
                 "invalid-reviewed-pixi-closure")
        for raw in (self.requirements_json, self.runtime_lock_json):
            _require(type(raw) is bytes and _encode(_decode(raw)) == raw, "canonical-pixi-policy-json-required")
        requirements = _decode(self.requirements_json)
        _require(type(requirements) is dict and set(requirements) == {"requiredNodes", "criticalFeatures", "behaviorChecks"}
                 and all(type(value) is list for value in requirements.values()), "invalid-pixi-capture-requirements")
        _absolute(self.plugin_root)
        _absolute(self.node)
        _require(type(self.runtime_options) is tuple and all(type(pair) is tuple and len(pair) == 2 for pair in self.runtime_options)
                 and len(self.runtime_options) == 4 and {pair[0] for pair in self.runtime_options} == OPTIONS,
                 "explicit-pixi-runtime-selection-required")
        for _, path in self.runtime_options:
            _absolute(path)


def _file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree(root):
    result = []
    def visit(directory):
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = path.resolve(strict=True)
                _require(target.is_relative_to(root), "pixi-runtime-symlink-escapes")
                result.append({"path": name, "symlink": target.relative_to(root).as_posix()})
            elif path.is_dir():
                visit(path)
            else:
                _require(path.is_file(), "unsupported-pixi-runtime-file")
                result.append({"path": name, "sha256": _file_sha(path)})
    visit(root)
    return result


def _runtime(policy):
    lock = _decode(policy.runtime_lock_json)
    options = dict(policy.runtime_options)
    _require(lock.get("kind") == "img2.pixi2d-replay-runtime-lock" and lock.get("version") == 1,
             "invalid-reviewed-pixi-runtime")
    _require(lock["node"]["path"] == policy.node and _file_sha(_absolute(policy.node)) == lock["node"]["sha256"],
             "pixi-node-pin-mismatch")
    _require(lock["pixi"]["version"] == "8.22.0" and lock["playwright"]["version"] == "1.64.0"
             and lock["playwrightCore"]["version"] == "1.64.0", "pixi-runtime-version-mismatch")
    _require(options == {"pixi-package": lock["pixi"]["path"], "playwright-package": lock["playwright"]["path"],
                        "chromium": lock["chromium"]["path"], "chromium-root": lock["chromium"]["root"]},
             "pixi-runtime-selection-mismatch")
    for key in ("pixi", "playwright", "playwrightCore", "chromium"):
        root = _absolute(lock[key]["root" if key == "chromium" else "path"])
        _require(sorted(_tree(root), key=lambda row: row["path"]) == sorted(lock[key]["files"], key=lambda row: row["path"]),
                 "pixi-runtime-pin-mismatch:" + key)
    _require(_absolute(options["chromium"]).is_relative_to(Path(options["chromium-root"])), "pixi-browser-outside-bundle")
    expected = [{"path": name, "sha256": _sha(_read(policy.plugin_root, name)[0])} for name in PRODUCER_FILES]
    _require(lock["producer"] == expected, "pixi-producer-pin-mismatch")
    return lock


def _pixels(url, artboard):
    _require(type(url) is str and url.startswith("data:image/png;base64,"), "pixi-real-png-required")
    raw = base64.b64decode(url.split(",", 1)[1], validate=True)
    _require(raw.startswith(b"\x89PNG\r\n\x1a\n"), "pixi-real-png-required")
    width, height, channels, pixels = decode(raw)
    _require([width, height] == artboard and channels in (3, 4), "pixi-capture-artboard-mismatch")
    if channels == 3:
        pixels = b"".join(pixels[index:index + 3] + b"\xff" for index in range(0, len(pixels), 3))
    return pixels


def _measure(capture, snapshot):
    """Independently check native IDs/bounds against the observed CDP pixel diffs."""
    requirements = snapshot["requirements"]
    nodes = requirements["requiredNodes"]
    _require(capture.get("kind") == "img2.pixi2d-capture" and capture.get("version") == 1
             and capture.get("producer") == PRODUCER and capture.get("renderer") == "pixi.js"
             and capture.get("pixiVersion") == "8.22.0" and capture.get("nodes") == nodes,
             "pixi-reviewed-native-capture-required")
    expected, changing = {"baseline"}, set()
    for node in nodes:
        for edit in node["edits"]:
            key = "edit-" + node["id"] + "-" + edit
            expected.update((key, key + "-reset"))
            if "noPixelFootprint" not in node:
                changing.add(key)
    for behavior in requirements["behaviorChecks"]:
        key = "behavior-" + behavior["id"]
        expected.update((key, key + "-reset"))
        if behavior["changesPixels"]:
            changing.add(key)
    _require(set(capture["images"]) == expected, "pixi-incomplete-native-capture")
    images = {key: _pixels(value, snapshot["artboard"]) for key, value in capture["images"].items()}
    baseline = images["baseline"]
    for key, pixels in images.items():
        _require(not key.endswith("-reset") or pixels == baseline, "pixi-capture-reset-mismatch:" + key)
        _require(key not in changing or pixels != baseline, "pixi-capture-no-visible-effect:" + key)
    provenance = capture["provenance"]
    _require(set(provenance) == {"contract", "resolution", "nodes"}
             and provenance["contract"] == "img2.pixi2d-node-provenance/v1" and provenance["resolution"] == 1,
             "pixi-native-provenance-contract-mismatch")
    records = provenance["nodes"]
    _require(len(records) == len(nodes) and [r["id"] for r in records] == [n["id"] for n in nodes]
             and len({n["id"] for n in nodes}) == len(nodes), "pixi-native-node-ids-mismatch")
    width, height = snapshot["artboard"]
    parents, masks, results = {}, {}, []
    for node, record in zip(nodes, records):
        ident, bounds, isolation = node["id"], record["bounds"], record["isolation"]
        _require(record["type"] == node["type"] and type(record["visibleAtBaseline"]) is bool
                 and isolation["method"] == "visibility-toggle" and isolation["restoredVisible"] is record["visibleAtBaseline"],
                 "pixi-native-node-identity-mismatch")
        _require(set(bounds) == {"x", "y", "width", "height"}
                 and all(type(v) in (int, float) and math.isfinite(v) for v in bounds.values())
                 and bounds["width"] >= 0 and bounds["height"] >= 0, "pixi-invalid-native-bounds")
        parents[ident] = record["parent"]
        _require(record["parent"] is None or record["parent"] in {n["id"] for n in nodes}, "pixi-invalid-native-parent")
        isolated = _pixels(isolation["isolated"], snapshot["artboard"])
        _require(_pixels(isolation["restored"], snapshot["artboard"]) == baseline, "pixi-isolation-reset-mismatch")
        if "visibility" in node["edits"]:
            _require(isolated == images["edit-" + ident + "-visibility"], "pixi-isolation-visibility-mismatch")
        flags = bytes(baseline[i:i + 4] != isolated[i:i + 4] for i in range(0, len(baseline), 4))
        count = flags.count(1)
        region = None
        if "noPixelFootprint" in node:
            _require(count == 0, "pixi-declared-empty-footprint-contradicted")
        else:
            _require(count > 0 and bounds["width"] > 0 and bounds["height"] > 0, "pixi-native-node-footprint-empty")
            left, top, right, bottom = width, height, 0, 0
            for index, changed in enumerate(flags):
                if changed:
                    x, y = index % width, index // width
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x + 1), max(bottom, y + 1)
            region = [left, top, right - left, bottom - top]
            _require(bounds["x"] + bounds["width"] > 0 and bounds["y"] + bounds["height"] > 0
                     and bounds["x"] < width and bounds["y"] < height, "pixi-native-bounds-outside-artboard")
            _require(left >= math.floor(bounds["x"]) - 2 and top >= math.floor(bounds["y"]) - 2
                     and right <= math.ceil(bounds["x"] + bounds["width"]) + 2
                     and bottom <= math.ceil(bounds["y"] + bounds["height"]) + 2,
                     "pixi-native-pixels-outside-bounds")
            masks[ident] = int.from_bytes(flags, "little")
        results.append({"id": ident, "type": record["type"], "bounds": bounds, "pixels": count, "region": region})
    ancestors = {}
    for ident in parents:
        chain, current = set(), parents[ident]
        while current is not None:
            _require(current != ident and current not in chain, "pixi-native-parent-cycle")
            chain.add(current)
            current = parents[current]
        ancestors[ident] = chain
    for ident, mask in masks.items():
        rivals = 0
        for other, other_mask in masks.items():
            if other != ident and other not in ancestors[ident] and ident not in ancestors[other]:
                rivals |= other_mask
        _require(bool(mask & ~rivals), "pixi-ambiguous-native-ownership:" + ident)
    return results


class PixiCaptureAuthority:
    """A live host approval map; receipts are returned only after actual replay."""
    def __init__(self, policies=None):
        if policies is None:
            policies = {}
        _require(type(policies) is dict and all(type(p) is ReviewedPixiPolicy and key == p.object_id
                                              for key, p in policies.items()), "invalid-reviewed-pixi-policies")
        self._live = policies
        self._approved = dict(policies)

    def _policy(self, object_id):
        _require(self._live == self._approved, "pixi-capture-policy-revoked")
        policy = self._approved.get(object_id)
        _require(policy is not None, "terminal-native-capture-authority-unavailable")
        return policy

    def verify_capture(self, child_workspace, *, object_id, revision, artifact_binding,
                       snapshot_binding, reference_binding, requirements):
        """Return observed receipt or ParentError. All bindings are {path,sha256}.

        The caller must supply the current independently checked artifact/snapshot,
        admitted reference and host-approved full requirements, not candidate claims.
        Nothing is written to the child. A successful receipt is capture evidence,
        not the semantic/gate/terminal decision owned by RouteAuthority.
        """
        policy = self._policy(object_id)
        try:
            child = _absolute(str(child_workspace))
            _require(revision == policy.revision and type(revision) is int
                     and snapshot_binding == _binding(policy.snapshot_binding)
                     and reference_binding == _binding(policy.reference_binding)
                     and _encode(requirements) == policy.requirements_json, "pixi-capture-approval-subject-mismatch")
            _binding((artifact_binding["path"], artifact_binding["sha256"]))
            _require(artifact_binding["path"] == AREA + "artifact.json", "pixi-capture-artifact-path-mismatch")
            def current():
                watched = {}
                def read(binding):
                    _require(type(binding) is dict and set(binding) == {"path", "sha256"}, "invalid-pixi-capture-binding")
                    _binding((binding["path"], binding["sha256"]))
                    raw = _read(child, binding["path"])[0]
                    _require(_sha(raw) == binding["sha256"], "pixi-capture-stale-binding:" + binding["path"])
                    watched[binding["path"]] = binding["sha256"]
                    return raw
                snapshot = _decode(read(snapshot_binding))
                artifact = _decode(read(artifact_binding))
                _require(snapshot["objectId"] == object_id and artifact["objectId"] == object_id
                         and _encode(snapshot["requirements"]) == policy.requirements_json
                         and artifact["snapshotSha256"] == _sha(_encode(snapshot)), "pixi-capture-subject-mismatch")
                _require(artifact["editableSources"] == [_binding(value) for value in policy.sources]
                         and artifact["assets"] == [_binding(value) for value in policy.assets], "pixi-unreviewed-source-or-asset")
                _require(reference_binding in snapshot["bindings"] and artifact["bindings"] == snapshot["bindings"],
                         "pixi-capture-reference-not-bound")
                for binding in snapshot["bindings"] + artifact["evidence"] + [artifact["review"]]:
                    read(binding)
                inputs = _decode(_read(child, AREA + "inputs.json")[0])
                _require(inputs["sources"] == [value[0] for value in policy.sources]
                         and inputs["assets"] == [value[0] for value in policy.assets]
                         and inputs["reference"] == reference_binding["path"], "pixi-unreviewed-source-or-asset")
                for binding in artifact["editableSources"] + artifact["assets"]:
                    read(binding)
                capture_raw = _read(child, AREA + "capture.json")[0]
                evidence_raw = _read(child, AREA + "evidence.json")[0]
                evidence = _decode(evidence_raw)
                _require(evidence["capture"] == {"path": AREA + "capture.json", "sha256": _sha(capture_raw)}
                         and evidence["files"] == artifact["evidence"]
                         and evidence["snapshotSha256"] == artifact["snapshotSha256"]
                         and _sha(_encode(evidence)) == artifact["evidenceSha256"],
                         "pixi-capture-evidence-binding-mismatch")
                watched[AREA + "capture.json"] = _sha(capture_raw)
                watched[AREA + "evidence.json"] = _sha(evidence_raw)
                return snapshot, _decode(capture_raw), watched
            snapshot, saved_capture, before = current()
            lock = _runtime(policy)  # Re-read every executable/dependency BEFORE execution.
            with tempfile.TemporaryDirectory(prefix="img2-reviewed-pixi-") as temporary:
                held = Path(temporary) / "approved-runtime.json"
                held.write_bytes(policy.runtime_lock_json)
                argv = [policy.node, str(Path(policy.plugin_root) / PRODUCER_FILES[0]), "reviewed-replay",
                        "--workspace", str(child), "--runtime-lock", str(held)]
                for key, value in policy.runtime_options:
                    argv.extend(("--" + key, value))
                result = subprocess.run(argv, cwd=temporary, env={"PATH": os.defpath, "HOME": temporary,
                    "TMPDIR": temporary, "LANG": "en_US.UTF-8"}, shell=False, capture_output=True, timeout=240)
                _require(result.returncode == 0, "pixi-reviewed-live-replay-failed")
                observed = _decode(result.stdout)
            _require(observed.get("kind") == "img2.pixi2d-live-replay" and observed.get("version") == 1
                     and observed.get("objectId") == object_id and observed.get("snapshotBinding") == snapshot_binding
                     and observed.get("artifactBinding") == artifact_binding and observed.get("runtime") == lock
                     and observed.get("runtimeLockSha256") == _sha(policy.runtime_lock_json)
                     and observed.get("sourceBindings") == [_binding(v) for v in policy.sources]
                     and observed.get("assetBindings") == [_binding(v) for v in policy.assets], "pixi-live-replay-binding-mismatch")
            capture = observed["replayCapture"]
            _require(capture == saved_capture and capture["snapshotSha256"] == _sha(_encode(snapshot))
                     and observed["replayCaptureSha256"] == _sha(_encode(capture)), "pixi-current-native-capture-mismatch")
            measured = _measure(capture, snapshot)
            _require(current() == (snapshot, saved_capture, before), "pixi-capture-inputs-changed")
            _runtime(policy)
            self._policy(object_id)  # Detect revocation while the owned browser ran.
            return {"kind": "img2.pixi2d-reviewed-capture-receipt", "version": 1, "objectId": object_id,
                "revision": revision, "reviewId": policy.review_id, "artifact": artifact_binding,
                "snapshot": snapshot_binding, "reference": reference_binding,
                "capture": {"path": AREA + "capture.json", "sha256": before[AREA + "capture.json"]},
                "runtimeLockSha256": _sha(policy.runtime_lock_json), "nodes": measured,
                "browserVersion": observed["browserVersion"], "browserCommandLine": observed["browserCommandLine"],
                "renderedHostSha256": observed["renderedHostSha256"], "loadedResources": observed["loadedResources"],
                "trustBoundary": "Reviewed exact source closure and approved dependencies are trusted code; NOT confinement/security for arbitrary same-realm JavaScript.",
                "artworkQuality": "not-evaluated-by-capture-authority"}
        except ParentError:
            raise
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raise ParentError("pixi-reviewed-capture-invalid-or-unavailable") from exc
