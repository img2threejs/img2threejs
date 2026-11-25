"""Explicit host admission for the CS2 delivered-GLB terminal path.

Only trusted program code constructs CS2TerminalPolicy after reviewing upstream
classification, source calibration/annotations, user requirements and native host
inputs. There is no policy loader or auto-approval path. Digests identify that
approval; they never perform it. Removing/changing a live policy revokes admission.

prepare() performs actual fixed base GLB captures and pinned plugin measurement,
then emits the shared quality-review context. Consult the controller OUTSIDE the
parent transaction and save quality-review-link.json with parent-relative context
and record {path,sha256} refs. verify_terminal() requires the held controller
verifier, re-renders all views, reruns measurements and the real blocking gate,
then consumes the exact journal-backed semantic review. Measurement surrogates do
not prove asset identity, source truth, physical BRDF recovery or general quality.
The approved browser bundle is pinned, including frameworks and symlink identity;
OS libraries, the interpreter and other host dependencies remain trusted ambient
code, not sandboxed. No arbitrary scene script, external host command or plugin
Python import executes in the authority process.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import stat
import tempfile
from uuid import uuid4

from decision_control import canonical_digest
from browser_runtime import verify_browser_bundle
from route_artifact import validate_artifact
from route_parent import ParentError, _decode, _encode, _read, _write
from stage0_route.images import decode
from stage4_review.capture_delivered_glb import capture_delivered_glb, validate_config, REQUIRED_MODULES, MODULE_URLS

AREA = ".img2/cs2-terminal/"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PIN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_BASE_CHECKS = frozenset({"classification-source-authority", "finish-material-response", "identity-detail",
                          "semantic-asset-identity", "source-calibration", "delivery-frame-scale"})
_DIMENSIONS = ("silhouette-proportions", "part-relationships", "geometry", "material-appearance", "identity-features")
_GATE = "python3 {plugin_dir}/tools/cs2_review.py --terminal-request .img2/cs2-terminal/gate-request.json"


def _require(condition, reason):
    if not condition:
        raise ParentError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(value):
    _require(type(value) is str and value and not value.startswith("/") and ":" not in value and "\\" not in value
             and all(part not in {"", ".", ".."} for part in value.split("/"))
             and not any(ord(char) < 32 or ord(char) == 127 for char in value), "cs2-unsafe-path")
    return value


def _binding(value):
    _require(type(value) is tuple and len(value) == 2 and type(value[1]) is str
             and _SHA.fullmatch(value[1]), "invalid-cs2-approved-binding")
    return {"path": _relative(value[0]), "sha256": value[1]}


def _ref(value):
    _require(type(value) is dict and set(value) == {"path", "sha256"}, "invalid-cs2-binding")
    return _binding((value["path"], value["sha256"]))


def _bound(root, ref):
    ref = _ref(ref)
    raw = _read(root, ref["path"])[0]
    _require(_sha(raw) == ref["sha256"], "cs2-stale-evidence:" + ref["path"])
    return raw


def _pretty(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _host(binding):
    _require(type(binding) is tuple and len(binding) == 2 and type(binding[0]) is str
             and type(binding[1]) is str and _SHA.fullmatch(binding[1]), "invalid-cs2-host-binding")
    path = Path(binding[0])
    _require(path.is_absolute() and str(path.resolve(strict=True)) == str(path), "cs2-noncanonical-host-path")
    # _read pins every component, including the leaf, without following symlinks.
    raw = _read("/", str(path)[1:])[0]
    _require(_sha(raw) == binding[1], "cs2-host-pin-mismatch")
    return {"path": str(path), "sha256": binding[1]}


@dataclass(frozen=True)
class CS2TerminalPolicy:
    """Host-reviewed immutable admission, NOT a candidate/workspace JSON schema.

    Child bindings are (relative_path, sha256). User requirements are relative to
    parent_workspace. approved_evidence contains (role, relative_path, sha256)
    for ALL actual source images/configs and any retained upstream review evidence.
    review_id/notes identify the real host review; prose alone is not validation.
    browser_root is an explicitly host-reviewed canonical external bundle directory;
    browser_files is its complete immutable (relative_path, sha256) inventory from
    browser_runtime.snapshot_browser_bundle. Computing hashes is not approval.
    """
    object_id: str
    revision: int
    run_id: str
    parent_workspace: str
    reference_binding: tuple[str, str]
    classification_binding: tuple[str, str]
    intake_binding: tuple[str, str]
    sources_binding: tuple[str, str]
    inputs_binding: tuple[str, str]
    user_requirements_binding: tuple[str, str]
    approved_evidence: tuple[tuple[str, str, str], ...]
    plugin_root: str
    plugin_commit: str
    python: tuple[str, str]
    chrome: tuple[str, str]
    browser_root: str
    browser_files: tuple[tuple[str, str], ...]
    modules: tuple[tuple[str, str, str], ...]
    required_checks: tuple[str, ...]
    review_id: str
    review_notes: str
    requested_model: str = "jev-1.14.0"

    def __post_init__(self):
        _require(type(self.object_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", self.object_id)
                 and type(self.revision) is int and self.revision > 0
                 and all(type(v) is str and v.strip() for v in (self.run_id, self.review_id, self.review_notes, self.requested_model)),
                 "invalid-cs2-terminal-policy")
        for value in (self.reference_binding, self.classification_binding, self.intake_binding, self.sources_binding,
                      self.inputs_binding, self.user_requirements_binding):
            _binding(value)
        for value in (self.parent_workspace, self.plugin_root, self.browser_root):
            _require(type(value) is str and Path(value).is_absolute() and str(Path(value).resolve(strict=True)) == value,
                     "cs2-noncanonical-policy-root")
        _require(not Path(self.browser_root).is_relative_to(Path(self.parent_workspace))
                 and not Path(self.parent_workspace).is_relative_to(Path(self.browser_root)),
                 "cs2-browser-root-must-be-external")
        _require(type(self.plugin_commit) is str and _PIN.fullmatch(self.plugin_commit), "cs2-provider-unpinned")
        _require(type(self.approved_evidence) is tuple and self.approved_evidence, "cs2-source-approval-required")
        for item in self.approved_evidence:
            _require(type(item) is tuple and len(item) == 3 and type(item[0]) is str and item[0], "invalid-cs2-source-approval")
            _binding((item[1], item[2]))
        _require(len({item[0] for item in self.approved_evidence}) == len(self.approved_evidence), "cs2-duplicate-approved-role")
        _require(type(self.required_checks) is tuple and len(set(self.required_checks)) == len(self.required_checks)
                 and all(type(v) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", v) for v in self.required_checks)
                 and _BASE_CHECKS <= set(self.required_checks), "cs2-independent-critical-checks-required")
        _require(type(self.modules) is tuple and all(type(row) is tuple and len(row) == 3 for row in self.modules)
                 and len({row[0] for row in self.modules}) == len(self.modules)
                 and REQUIRED_MODULES <= {row[0] for row in self.modules} <= MODULE_URLS,
                 "invalid-cs2-approved-modules")
        for binding in (self.python, self.chrome, *((row[1], row[2]) for row in self.modules)):
            _host(binding)
        verify_browser_bundle(self.browser_root, self.browser_files, self.chrome[0])


class CS2TerminalAuthority:
    def __init__(self, live_policies):
        _require(type(live_policies) is dict and all(type(p) is CS2TerminalPolicy and key == p.object_id
                 for key, p in live_policies.items()), "invalid-cs2-terminal-policies")
        self._live = live_policies
        self._policies = deepcopy(live_policies)
        self._git = shutil.which("git", path=os.defpath)
        _require(self._git is not None, "cs2-git-unavailable")
        self._git = str(Path(self._git).resolve(strict=True))
        self._git_sha = _sha(Path(self._git).read_bytes())
        # The fixed base producer/runtime are trusted running program code, not
        # provider-selected imports; detect changes throughout this authority lifetime.
        capture_dir = Path(__file__).resolve().parents[1] / "stage4_review"
        self._base_files = {path: _sha(path.read_bytes()) for path in (
            Path(__file__).resolve(), Path(__file__).with_name("browser_runtime.py").resolve(),
            capture_dir / "capture_delivered_glb.py", capture_dir / "delivered_glb_runtime.mjs")}

    def _policy(self, object_id, child_workspace):
        _require(self._live == self._policies, "cs2-terminal-policy-changed")
        policy = self._policies.get(object_id)
        _require(policy is not None, "cs2-terminal-policy-unavailable")
        child = Path(child_workspace)
        expected = Path(policy.parent_workspace) / f".img2threejs/objects/{object_id}/rev-{policy.revision}"
        _require(child == expected and child.resolve(strict=True) == child, "cs2-child-workspace-mismatch")
        self._check_host(policy)
        return policy, child

    def _check_host(self, policy):
        for path, digest in self._base_files.items():
            _require(_sha(path.read_bytes()) == digest, "cs2-base-producer-changed")
        _require(_sha(Path(self._git).read_bytes()) == self._git_sha, "cs2-git-changed")
        for binding in (policy.python, policy.chrome, *((row[1], row[2]) for row in policy.modules)):
            _host(binding)
        verify_browser_bundle(policy.browser_root, policy.browser_files, policy.chrome[0])

    @staticmethod
    def _env():
        return {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"}

    def _run(self, argv, cwd):
        try:
            result = subprocess.run(argv, cwd=str(cwd), env=self._env(), shell=False,
                                    capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ParentError("cs2-fixed-command-failed") from exc
        _require(type(result.stdout) is bytes and len(result.stdout) <= 32 * 1024 * 1024,
                 "cs2-command-output-invalid")
        return result

    def _provider(self, policy):
        root = Path(policy.plugin_root)
        _require(root.resolve(strict=True) == root, "cs2-provider-root-changed")
        commit = self._run([self._git, "-C", str(root), "cat-file", "-t", policy.plugin_commit], root)
        _require(commit.returncode == 0 and commit.stdout == b"commit\n", "cs2-provider-commit-required")
        result = self._run([self._git, "-C", str(root), "ls-tree", "-r", "-z", policy.plugin_commit,
                            "--", "tools", "plugin.json", "gates.json"], root)
        _require(result.returncode == 0, "cs2-provider-pin-unavailable")
        committed = {}
        for entry in result.stdout.split(b"\0"):
            if not entry:
                continue
            metadata, path_raw = entry.split(b"\t", 1)
            mode, kind, _ = metadata.split(b" ")
            path = path_raw.decode("utf-8")
            _require(mode in (b"100644", b"100755") and kind == b"blob", "cs2-unsafe-provider-tree")
            raw = _read(root, _relative(path))[0]
            saved = self._run([self._git, "-C", str(root), "show", policy.plugin_commit + ":" + path], root)
            _require(saved.returncode == 0 and saved.stdout == raw, "cs2-provider-pin-mismatch:" + path)
            committed[path] = _sha(raw)
        approved_dirs = {parent.as_posix() for name in committed for parent in Path(name).parents}
        for directory, dirs, files in os.walk(root / "tools", followlinks=False):
            directory = Path(directory)
            relative_dir = directory.relative_to(root)
            _require(not any((directory / name).is_symlink() for name in dirs), "cs2-unsafe-provider-import")
            for name in dirs:
                relative = (relative_dir / name).as_posix()
                _require(relative in approved_dirs or (name == "__pycache__"
                         and relative_dir.as_posix() in approved_dirs), "cs2-unpinned-provider-import")
            for name in files:
                path = directory / name
                relative = path.relative_to(root)
                _require(not path.is_symlink() and path.is_file(), "cs2-unsafe-provider-import")
                # Only generated bytecode for a committed source is exempt. Never
                # let an ancestor of the provider root grant cache exemptions.
                cached = re.fullmatch(r"(.+)\.cpython-[0-9]+(?:\.opt-[0-9]+)?\.pyc", name)
                if relative.parent.name == "__pycache__" and cached is not None:
                    source = relative.parent.parent / (cached.group(1) + ".py")
                    _require(source.as_posix() in committed and not path.stat().st_mode & (
                        stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), "cs2-unsafe-provider-import")
                    continue
                _require(path.suffix not in {".so", ".pyd", ".pyc", ".pth"}, "cs2-unsafe-provider-import")
                _require(relative.as_posix() in committed, "cs2-unpinned-provider-import")
        _require({"tools/cs2_terminal.py", "tools/cs2_review.py", "plugin.json", "gates.json"} <= committed.keys(),
                 "cs2-terminal-provider-unavailable")
        manifest = _decode(_read(root, "plugin.json")[0])
        _require(manifest.get("name") == "cs2" and manifest.get("version") == "0.3.0",
                 "cs2-provider-identity-mismatch")
        gates = _decode(_read(root, "gates.json")[0])
        _require(type(gates) is list and len(gates) == 1 and gates[0] == {
            "id": "cs2-review", "command": _GATE, "blocking": True, "after": []}, "cs2-unreviewed-blocking-gate")
        return committed

    def _admit(self, policy, child, artifact, inputs):
        artifact_raw = _bound(child, artifact)
        validated = validate_artifact(child, _decode(artifact_raw))
        _require(validated["objectId"] == policy.object_id and validated["outputKind"] == "3d-object", "cs2-artifact-subject-mismatch")
        for binding in (policy.reference_binding, policy.classification_binding, policy.intake_binding,
                        policy.sources_binding, policy.inputs_binding):
            _bound(child, _binding(binding))
        _bound(policy.parent_workspace, _binding(policy.user_requirements_binding))
        values = _decode(_bound(child, inputs))
        _require(inputs == _binding(policy.inputs_binding), "cs2-unapproved-inputs")
        _require(type(values) is dict and set(values) == {"intake", "classification", "artifact", "delivery", "sources", "configs", "passId"},
                 "cs2-invalid-production-inputs")
        _require(type(values["configs"]) is list and type(values["passId"]) is str and values["passId"],
                 "cs2-invalid-production-inputs")
        for key, binding in (("intake", policy.intake_binding), ("classification", policy.classification_binding), ("sources", policy.sources_binding)):
            _require(values[key] == _binding(binding), "cs2-unapproved-" + key)
        _require(values["artifact"] == artifact and values["delivery"] in [{"path": f["path"], "sha256": f["sha256"]} for f in validated["files"]]
                 and values["delivery"]["path"].lower().endswith(".glb"), "cs2-delivery-selection-mismatch")
        sources = _decode(_bound(child, values["sources"]))
        _require(type(sources) is dict, "cs2-invalid-approved-sources")
        _require(sources.get("reference") == _binding(policy.reference_binding), "cs2-reference-approval-mismatch")
        approved = {(path, digest) for _, path, digest in policy.approved_evidence} | {policy.reference_binding}
        for _, path, digest in policy.approved_evidence:
            _bound(child, {"path": path, "sha256": digest})
        refs = list(values["configs"])
        _require(len(refs) == 3, "cs2-three-reference-orbit-configs-required")
        views = sources.get("materialViews")
        _require(type(views) is list and len(views) >= 2, "cs2-calibrated-material-source-observations-missing")
        for view in views:
            _require(type(view) is dict and set(view) == {"source", "config"}, "cs2-invalid-material-source")
            refs.extend((view["source"], view["config"]))
        checks = set(_BASE_CHECKS)
        for key, prefix in (("regions", "region:"), ("criticalFeatures", "feature:")):
            annotations = sources.get(key)
            _require(type(annotations) is list and annotations, "cs2-approved-source-" + key + "-missing")
            for annotation in annotations:
                _require(type(annotation) is dict and set(annotation) == {"id", "bounds"}
                         and type(annotation["id"]) is str and annotation["id"], "cs2-invalid-source-annotation")
                bounds = annotation["bounds"]
                _require(type(bounds) is list and len(bounds) == 4 and all(
                    type(v) in (int, float) and math.isfinite(v) for v in bounds)
                    and 0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1,
                    "cs2-invalid-source-region-bounds")
                checks.add(prefix + annotation["id"])
        _require(checks <= set(policy.required_checks), "cs2-unapproved-source-checks")
        for ref in refs:
            _ref(ref)
            _require((ref["path"], ref["sha256"]) in approved, "cs2-source-or-camera-not-approved")
            _bound(child, ref)
        main_configs = [validate_config(_decode(_bound(child, ref))) for ref in values["configs"]]
        material_configs = [validate_config(_decode(_bound(child, view["config"]))) for view in views]
        fixed = lambda config: {k: v for k, v in config.items() if k not in {"lighting", "viewId"}}
        _require(all(fixed(config) == fixed(main_configs[0]) for config in material_configs),
                 "cs2-material-source-camera-calibration-mismatch")
        _require(len({_encode(config["lighting"]) for config in material_configs}) == len(material_configs),
                 "cs2-distinct-material-source-lighting-required")
        for ref in [sources["reference"], *(view["source"] for view in views)]:
            decode(_bound(child, ref))
        return values, sources

    def _capture(self, policy, child, values, sources, output_dir):
        verify_browser_bundle(policy.browser_root, policy.browser_files, policy.chrome[0])
        result = deepcopy(values)
        for key, configs, prefix in (("captures", values["configs"], "view"),
                                      ("materialCaptures", [v["config"] for v in sources["materialViews"]], "material")):
            result[key] = []
            for index, ref in enumerate(configs):
                directory = output_dir + f"/{prefix}-{index}"
                capture_delivered_glb(child, artifact=values["artifact"], delivery=values["delivery"],
                    config=_decode(_bound(child, ref)), chrome=_host(policy.chrome),
                    modules={url: _host((path, digest)) for url, path, digest in policy.modules}, output_dir=directory)
                path = directory + "/capture.json"
                result[key].append({"path": path, "sha256": _sha(_read(child, path)[0])})
        verify_browser_bundle(policy.browser_root, policy.browser_files, policy.chrome[0])
        return result

    def _replay(self, policy, child, inputs_ref):
        before = self._provider(policy)
        with tempfile.TemporaryDirectory(prefix="cs2-pycache-") as cache:
            result = self._run([policy.python[0], "-E", "-s", "-S", "-B", "-X", "pycache_prefix=" + cache,
                str(Path(policy.plugin_root) / "tools/cs2_terminal.py"), "--workspace", str(child),
                "--inputs", inputs_ref["path"], "--inputs-sha256", inputs_ref["sha256"]], child)
        replay = _decode(result.stdout)
        if result.returncode not in (0, 1):
            detail = replay.get("reasonCode", "unknown") if type(replay) is dict else "invalid-output"
            field = replay.get("detail", "") if type(replay) is dict else ""
            raise ParentError("cs2-measurement-refused:" + str(detail) + ":" + str(field))
        _require(type(replay) is dict and set(replay) == {"inputs", "manifest", "metrics", "scene", "report", "context"},
                 "cs2-invalid-measurement-output")
        _require(self._provider(policy) == before, "cs2-provider-changed-during-measurement")
        return replay

    def _gate_request(self, child, area, *, create=False):
        _require(re.fullmatch(re.escape(AREA) + r"run-[a-f0-9]{32}/", area) is not None,
                 "cs2-gate-request-run-mismatch")
        descriptor = {"kind": "img2.cs2-terminal-request", "version": 1}
        for name in ("inputs", "scene"):
            path = area + name + ".json"
            descriptor[name] = {"path": path, "sha256": _sha(_read(child, path)[0])}
        path = area + "gate-request.json"
        raw = _encode(descriptor)
        if create:
            _write(child, path, raw)
        _require(_read(child, path)[0] == raw, "cs2-gate-request-binding-mismatch")
        return {"path": path, "sha256": _sha(raw)}

    def _gate(self, policy, child, area, replay, artifact):
        request = self._gate_request(child, area)
        descriptor = _decode(_bound(child, request))
        with tempfile.TemporaryDirectory(prefix="cs2-gate-pycache-") as cache:
            result = self._run([policy.python[0], "-E", "-s", "-S", "-B", "-X", "pycache_prefix=" + cache,
                str(Path(policy.plugin_root) / "tools/cs2_review.py"), "--workspace", str(child),
                "--terminal-request", request["path"]], child)
        verdict = _decode(result.stdout)
        _require(result.returncode in (0, 1), "cs2-blocking-terminal-gate-failed")
        report_path = area + "gate-review.json"
        report_raw = _read(child, report_path)[0]
        report = replay["report"]
        passed = report["verdict"] == "pass"
        expected = {
            "artifact": artifact, "request": request, "inputs": descriptor["inputs"],
            "scene": descriptor["scene"], "report": {"path": report_path, "sha256": _sha(report_raw)},
            "scope": "mechanical-replay-only", "terminalAuthority": False,
            "verdict": report["verdict"], "action": report["action"], "mode": "strict", "deferredGates": [],
        }
        _require(type(verdict) is dict and set(verdict) == {
                    "kind", "version", "plugin", "gate", "status", "reasons", "evidence"}
                 and verdict["kind"] == "img2.gate-verdict" and type(verdict["version"]) is int
                 and verdict["version"] == 1 and verdict["plugin"] == "cs2" and verdict["gate"] == "cs2-review"
                 and result.returncode == (0 if passed else 1)
                 and verdict["status"] == ("pass" if passed else "fail")
                 and verdict["reasons"] == report["failedGates"]
                 and type(verdict["evidence"]) is dict and verdict["evidence"] == expected
                 and verdict["evidence"].get("terminalAuthority") is False,
                 "cs2-blocking-terminal-gate-failed")
        _require(_decode(report_raw) == report and self._gate_request(child, area) == request,
                 "cs2-gate-report-mismatch")
        for name in ("inputs", "scene"):
            _bound(child, descriptor[name])
        return expected

    def _context(self, policy, child, replay, area, *, policy_version="cs2-terminal-policy-v2"):
        relative = child.relative_to(Path(policy.parent_workspace)).as_posix()
        prefix = "" if relative == "." else relative + "/"
        bindings, images, roles = [], [], set()
        def add(role, ref, image=False, parent=False):
            _require(role not in roles, "cs2-duplicate-context-role")
            roles.add(role)
            raw = _bound(policy.parent_workspace if parent else child, ref)
            bindings.append({"role": role, "path": ref["path"] if parent else prefix + ref["path"], "sha256": ref["sha256"]})
            if image:
                decode(raw)
                images.append({"role": role, "asset": role})
        add("user-requirements", _binding(policy.user_requirements_binding), parent=True)
        for role, binding in (("reference", policy.reference_binding), ("classification", policy.classification_binding),
                              ("intake", policy.intake_binding), ("sources", policy.sources_binding), ("approved-inputs", policy.inputs_binding)):
            add(role, _binding(binding), image=role == "reference")
        for index, (role, path, digest) in enumerate(policy.approved_evidence):
            add("approved-" + str(index), {"path": path, "sha256": digest}, image=path.lower().endswith(".png"))
        for binding in replay["context"]["bindings"]:
            original = binding["role"]
            role = {"view-0-screenshot": "render", "view-1-screenshot": "orbit-left",
                    "view-2-screenshot": "orbit-right"}.get(original, "measurement-" + original)
            if original.startswith("material-") and original.endswith("-screenshot"):
                role = "render-" + original.removesuffix("-screenshot")
            elif original.startswith("material-source-"):
                role = "source-material-" + original.removeprefix("material-source-")
            add(role, {k: binding[k] for k in ("path", "sha256")},
                image="screenshot" in original or original.startswith("material-source-"))
        for name in ("inputs", "metrics", "scene", "report", "context"):
            raw = _read(child, area + name + ".json")[0]
            _require(raw == _pretty(replay[name]), "cs2-stale-produced-" + name)
            add("produced-" + name, {"path": area + name + ".json", "sha256": _sha(raw)})
        add("produced-gate-request", self._gate_request(child, area))
        _require(set(replay["context"]["requiredChecks"]) <= set(policy.required_checks), "cs2-unapproved-measured-checks")
        image_roles = [image["role"] for image in images]
        views = []
        for ref in replay["inputs"]["configs"]:
            camera = validate_config(_decode(_bound(child, ref)))["camera"]
            x, z = (camera["position"][i] - camera["target"][i] for i in (0, 2))
            _require(math.hypot(x, z) > 0, "cs2-view-direction-unavailable")
            yaw = math.degrees(math.atan2(x, z)) % 360
            if yaw not in views:
                views.append(yaw)
        return {"kind": "img2.quality-review-context", "version": 1, "group": "quality-review",
            "subject": {"runId": policy.run_id, "objectId": policy.object_id, "revision": policy.revision,
                        "passId": replay["inputs"]["passId"]}, "bindings": bindings, "candidates": [],
            "observations": {"images": images, "availableViews": views,
                "approvedChecks": list(policy.required_checks), "hostReviewId": policy.review_id,
                "reviewScope": "Delivered GLB and calibrated source comparisons. Pixel surrogates do not prove asset identity; independently judge semantic identity, finish and every approved region/feature."},
            "requirements": {"rubricVersion": "quality-review-rubric-v1",
                "dimensions": [{"id": ident, "threshold": 3, "evidenceRoles": image_roles} for ident in _DIMENSIONS],
                "criticalFeatures": [{"id": ident, "threshold": 3, "evidenceRoles": image_roles} for ident in policy.required_checks],
                "requiredViews": views, "evidenceRoles": [b["role"] for b in bindings]},
            "policyVersion": policy_version, "questionSetVersion": "quality-review-questions-v1",
            "requestedModel": policy.requested_model}

    def prepare(self, child_workspace, *, object_id, artifact, inputs):
        """Capture and measure; return actual shared review context, not acceptance."""
        policy, child = self._policy(object_id, child_workspace)
        return self._prepare_production(policy, child, artifact, inputs,
            revalidate=lambda: self._policy(object_id, child))

    def _prepare_production(self, policy, child, artifact, inputs, *, revalidate,
                            policy_version="cs2-terminal-policy-v2"):
        revalidate()
        provider = self._provider(policy)
        values, sources = self._admit(policy, child, artifact, inputs)
        area = AREA + "run-" + uuid4().hex + "/"
        _require(not (child / area).exists(), "cs2-terminal-output-must-be-fresh")
        captured = self._capture(policy, child, values, sources, area.rstrip("/"))
        raw = _pretty(captured)
        _write(child, area + "inputs.json", raw)
        replay = self._replay(policy, child, {"path": area + "inputs.json", "sha256": _sha(raw)})
        for name in ("metrics", "scene", "report", "context"):
            _write(child, area + name + ".json", _pretty(replay[name]))
        self._gate_request(child, area, create=True)
        context = self._context(policy, child, replay, area, policy_version=policy_version)
        _write(child, area + "quality-review-context-" + canonical_digest(context) + ".json", _encode(context))
        self._admit(policy, child, artifact, inputs)
        _require(self._provider(policy) == provider, "cs2-provider-changed-during-preparation")
        revalidate()
        verify_browser_bundle(policy.browser_root, policy.browser_files, policy.chrome[0])
        # Mutable locator is discovery only; authenticated context binds the per-run descriptor.
        request = self._gate_request(child, area)
        _write(child, AREA + "gate-request.json", _bound(child, request), replace=True)
        return context

    @staticmethod
    def _quality_record(verify, record, context, policy):
        record = verify(record, context)
        _require(type(record) is dict and record.get("group") == "quality-review"
                 and record.get("subject") == context["subject"] and record.get("status") == "answered"
                 and type(record.get("decider")) is str and record["decider"].startswith(("jev:", "model:")),
                 "cs2-quality-review-not-authenticated")
        answers = record.get("answers", {})
        for dimension in _DIMENSIONS:
            answer = answers.get("dimension:" + dimension, {})
            score = answer.get("score")
            _require(answer.get("type") == "score" and type(score) in (int, float) and math.isfinite(score)
                     and score >= 3, "cs2-semantic-quality-failed")
        for check in policy.required_checks:
            answer = answers.get("critical:" + check, {})
            _require(answer.get("type") == "choice" and answer.get("choice") == "pass",
                     "cs2-semantic-quality-failed")
        return record

    def _compare_capture(self, child, saved_ref, fresh_ref):
        saved, fresh = (_decode(_bound(child, ref)) for ref in (saved_ref, fresh_ref))
        for key in ("artifact", "delivery", "viewId", "frame", "viewport", "camera", "effectiveScale"):
            _require(saved.get(key) == fresh.get(key), "cs2-rerender-view-mismatch")
        _require(_bound(child, saved["config"]) == _bound(child, fresh["config"]), "cs2-rerender-config-mismatch")
        for record in (saved, fresh):
            screenshot = {k: record["screenshot"][k] for k in ("path", "sha256")}
            _bound(child, screenshot)
        _require(saved["screenshot"]["sha256"] == fresh["screenshot"]["sha256"], "cs2-rerender-pixels-mismatch")
        renderers = [_decode(_bound(child, record["renderer"])) for record in (saved, fresh)]
        for key in ("chrome", "modules", "programRuntimeSha256", "htmlSha256", "runtimeReport"):
            _require(renderers[0].get(key) == renderers[1].get(key), "cs2-rerender-runtime-mismatch")

    def verify_terminal(self, verify, *, object_id, child_workspace, state, artifact):
        """Verify under the caller's held controller transaction; never consult here."""
        _require(callable(verify) and callable(getattr(verify, "route_rounds", None))
                 and callable(getattr(verify, "route_round_links", None)), "held-route-transaction-required")
        policy, child = self._policy(object_id, child_workspace)
        verify_browser_bundle(policy.browser_root, policy.browser_files, policy.chrome[0])
        route = state.get("routeChild", {})
        _require(state.get("profile") == "cs2" and route.get("runId") == policy.run_id
                 and route.get("objectId") == object_id and route.get("revision") == policy.revision
                 and route.get("outputKind") == "3d-object" and route.get("cropSha256") == policy.reference_binding[1],
                 "cs2-child-state-mismatch")
        link_raw = _read(child, AREA + "quality-review-link.json")[0]
        link = _decode(link_raw)
        _require(type(link) is dict and set(link) == {"context", "record"}, "cs2-invalid-quality-link")
        context_ref, record_ref = _ref(link["context"]), _ref(link["record"])
        child_prefix = child.relative_to(Path(policy.parent_workspace)).as_posix() + "/"
        context_path = re.fullmatch(re.escape(child_prefix + AREA)
            + r"(run-[a-f0-9]{32}/)quality-review-context-([a-f0-9]{64})\.json", context_ref["path"])
        _require(context_path is not None, "cs2-quality-context-path-mismatch")
        area = AREA + context_path.group(1)
        actual_context = _decode(_bound(policy.parent_workspace, context_ref))
        record = _decode(_bound(policy.parent_workspace, record_ref))
        _require(type(actual_context) is dict and canonical_digest(actual_context) == context_path.group(2)
                 and type(record) is dict
                 and record_ref["path"] == ".img2threejs/decisions/quality-review/" + str(record.get("recordId")) + ".json",
                 "cs2-quality-review-context-mismatch")
        before_provider = self._provider(policy)
        values, sources = self._admit(policy, child, artifact, _binding(policy.inputs_binding))
        _require(actual_context.get("subject") == {"runId": policy.run_id, "objectId": object_id,
                 "revision": policy.revision, "passId": values["passId"]}, "cs2-quality-review-context-mismatch")
        raw = _read(child, area + "inputs.json")[0]
        saved_ref = {"path": area + "inputs.json", "sha256": _sha(raw)}
        replay = self._replay(policy, child, saved_ref)
        _require({k: v for k, v in replay["inputs"].items() if k not in {"captures", "materialCaptures"}} == values,
                 "cs2-produced-inputs-not-approved")
        context = self._context(policy, child, replay, area)
        _require(actual_context == context, "cs2-quality-review-context-mismatch")
        _require(replay["report"]["verdict"] == "pass", "cs2-measured-terminal-gates-failed")
        with tempfile.TemporaryDirectory(prefix=".cs2-independent-", dir=child / area) as directory:
            relative = Path(directory).relative_to(child).as_posix()
            rerendered = self._capture(policy, child, values, sources, relative)
            for key in ("captures", "materialCaptures"):
                _require(len(replay["inputs"][key]) == len(rerendered[key]), "cs2-rerender-coverage-mismatch")
                for saved, fresh in zip(replay["inputs"][key], rerendered[key]):
                    self._compare_capture(child, saved, fresh)
            path = relative + "/inputs.json"
            raw_fresh = _pretty(rerendered)
            _write(child, path, raw_fresh)
            fresh = self._replay(policy, child, {"path": path, "sha256": _sha(raw_fresh)})
            _require(fresh["metrics"] == replay["metrics"], "cs2-rerender-derived-metrics-mismatch")
            _require({k: v for k, v in fresh["scene"].items() if k != "rendererVersion"} == {
                k: v for k, v in replay["scene"].items() if k != "rendererVersion"}, "cs2-rerender-scene-mismatch")
        # The plugin alone publishes the immutable derived report. No caller-selected output.
        self._gate(policy, child, area, replay, artifact)
        record = self._quality_record(verify, record, context, policy)
        self._admit(policy, child, artifact, _binding(policy.inputs_binding))
        _require(_bound(child, saved_ref) == raw and self._context(policy, child, self._replay(policy, child, saved_ref), area) == context,
                 "cs2-terminal-evidence-changed")
        _require(_read(child, AREA + "quality-review-link.json")[0] == link_raw,
                 "cs2-quality-review-link-changed")
        _bound(policy.parent_workspace, link["context"])
        _bound(policy.parent_workspace, link["record"])
        _require(self._provider(policy) == before_provider, "cs2-provider-changed-during-terminal-check")
        self._policy(object_id, child)
        verify_browser_bundle(policy.browser_root, policy.browser_files, policy.chrome[0])
        return record
