"""Synthetic host-boundary tests. Mocked browser transport is NOT real quality evidence.

The fixed base capture writer and isolated, Git-pinned plugin CLI run in these
protocol tests; outage acceptance uses the real controller journal with offline
advisories. No expert corpus, authenticated classifier or native-render qualification is claimed.
"""
from dataclasses import FrozenInstanceError, replace
import hashlib
from importlib.machinery import PathFinder
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
from browser_runtime import snapshot_browser_bundle
from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from cs2_terminal_authority import (AREA, CS2TerminalAuthority, CS2TerminalPolicy, _BASE_CHECKS,
                                    _DIMENSIONS)
from route_parent import ParentError, _encode, _read, _write
from stage0_route.images import png
from stage4_review import capture_delivered_glb as capture
from test_capture_delivered_glb import config_fixture, triangle_glb

CS2_FIXTURE_COMMIT = "ea41cb608ff5428cc34fba63b3c8e8b6049e2e03"


def cs2_fixture_source():
    selected = os.environ.get("IMG2_CS2_SOURCE")
    if not selected:
        raise RuntimeError("CS2 contract fixture missing: set IMG2_CS2_SOURCE to the pinned checkout; "
                           "missing required integration coverage must not silently skip")
    source = Path(selected).resolve(strict=True)
    git = shutil.which("git", path=os.defpath)
    if git is None:
        raise RuntimeError("Git is required for the explicitly selected CS2 fixture")
    result = subprocess.run([git, "-C", str(source), "rev-parse", "--verify", "HEAD"],
                            capture_output=True, check=True, timeout=10, env={"PATH": os.defpath})
    if result.stdout.decode().strip() != CS2_FIXTURE_COMMIT:
        raise RuntimeError("CS2 fixture HEAD does not match the reviewed contract fixture pin")
    return source, git


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def picture(shade):
    pixels = bytearray()
    for y in range(40):
        for x in range(40):
            color = shade if 10 <= x < 24 and 7 <= y < 33 else 240
            pixels.extend((color, color, color))
    return png(40, 40, 3, bytes(pixels))


class CS2AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source, git = cs2_fixture_source()
        cls.shared = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.shared.cleanup)
        cls.shared_root = Path(cls.shared.name).resolve()
        cls.plugin = cls.shared_root / "plugin"
        cls.plugin.mkdir()
        # Read only immutable committed bytes, never another worktree's dirty files.
        listing = subprocess.run([git, "-C", str(source), "ls-tree", "-rz", CS2_FIXTURE_COMMIT,
                                  "--", "tools", "plugin.json", "gates.json"],
                                 capture_output=True, check=True, timeout=10, env={"PATH": os.defpath}).stdout
        for item in listing.split(b"\0"):
            if not item:
                continue
            metadata, raw_path = item.split(b"\t", 1)
            mode, kind, object_id = metadata.decode().split()
            relative = Path(raw_path.decode())
            if kind != "blob" or mode not in {"100644", "100755"} or relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("unsafe CS2 fixture tree entry")
            data = subprocess.run([git, "-C", str(source), "cat-file", "blob", object_id],
                                  capture_output=True, check=True, timeout=10, env={"PATH": os.defpath}).stdout
            target = cls.plugin / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if mode == "100755" else 0o644)
        # These commits exist ONLY in this disposable test fixture repository.
        for args in (("init", "--quiet"), ("add", "--", "tools", "plugin.json", "gates.json"),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                      "commit", "--quiet", "-m", "Pinned synthetic CS2 boundary fixture")):
            subprocess.run([git, "-C", str(cls.plugin), *args], check=True, capture_output=True, timeout=10)
        cls.commit = subprocess.run([git, "-C", str(cls.plugin), "rev-parse", "HEAD"],
                                    check=True, capture_output=True, timeout=10).stdout.decode().strip()
        cls.browser_root = cls.shared_root / "Browser.app"
        cls.chrome_path = cls.browser_root / "Contents/MacOS/Google Chrome"
        cls.chrome_path.parent.mkdir(parents=True)
        cls.chrome_path.write_bytes(b"\xcf\xfa\xed\xfe" + b"unit transport only; never execute")
        cls.chrome_path.chmod(0o755)
        cls.chrome = (str(cls.chrome_path), sha(cls.chrome_path.read_bytes()))
        framework = cls.browser_root / "Contents/Frameworks/Chrome.framework/Versions/157"
        framework.mkdir(parents=True)
        (framework / "Chrome").write_bytes(b"synthetic framework, never execute")
        (framework.parent / "Current").symlink_to("157", target_is_directory=True)
        cls.browser_files = snapshot_browser_bundle(str(cls.browser_root))
        cls.modules = []
        for index, url in enumerate(sorted(capture.REQUIRED_MODULES)):
            path = cls.shared_root / f"module-{index}.js"
            path.write_bytes(b"export const syntheticTransportOnly = true;")
            cls.modules.append((url, str(path), sha(path.read_bytes())))
        cls.modules = tuple(cls.modules)
        python = Path(sys.executable).resolve(strict=True)
        cls.python = (str(python), sha(python.read_bytes()))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.child = self.parent / ".img2threejs/objects/subject/rev-1"
        self.child.mkdir(parents=True)
        self.reference = self.put("reference.png", picture(32))
        self.classification = self.put("classification.json", {
            "itemFamily": "knife", "subtype": "karambit", "confidence": .98,
            "evidenceRefs": ["synthetic-unit-source"], "provider": "synthetic-unit-only", "version": "1", "timedOut": False})
        self.intake = self.put("intake.json", {"state": "proceed", "itemFamily": "knife", "subtype": "karambit",
            "classification": json.loads((self.child / "classification.json").read_bytes()),
            "sourceViews": [{"role": "reference", **self.reference}]})
        self.delivery = self.put("model.glb", triangle_glb())
        self.artifact = self.put("artifact.json", {"objectId": "subject", "outputKind": "3d-object",
            "files": [self.delivery], "entrypoint": "model.glb", "frame": {"units": "m", "up": "+Y", "forward": "+Z"},
            "pivot": "bottom-centre", "scaleEstimate": 1, "rig": None})
        self.configs, evidence = [], []
        for index in range(3):
            config = config_fixture()
            config["viewId"] = f"view-{index}"
            config["viewport"].update(width=40, height=40)
            config["camera"]["position"][0] = index
            ref = self.put(f"config-{index}.json", config)
            self.configs.append(ref)
            evidence.append((f"camera-{index}", ref["path"], ref["sha256"]))
        material_views = []
        for index, shade in enumerate((32, 80)):
            source_ref = self.put(f"material-source-{index}.png", picture(shade))
            config = json.loads((self.child / self.configs[0]["path"]).read_bytes())
            config["viewId"] = f"material-{index}"
            config["lighting"]["ambient"]["intensity"] = index + 1
            config_ref = self.put(f"material-config-{index}.json", config)
            material_views.append({"source": source_ref, "config": config_ref})
            evidence.extend(((f"material-source-{index}", source_ref["path"], source_ref["sha256"]),
                             (f"material-config-{index}", config_ref["path"], config_ref["sha256"])))
        self.sources_value = {"reference": self.reference, "regions": [{"id": "paint", "bounds": [.3, .25, .5, .7]}],
                              "criticalFeatures": [{"id": "visible-detail", "bounds": [.3, .25, .5, .7]}],
                              "materialViews": material_views}
        self.sources = self.put("sources.json", self.sources_value)
        self.inputs_value = {"intake": self.intake, "classification": self.classification, "artifact": self.artifact,
            "delivery": self.delivery, "sources": self.sources, "configs": self.configs, "passId": "cs2-terminal-review"}
        self.inputs = self.put("production-inputs.json", self.inputs_value)
        request = self.put_parent("requirements.json", {"request": "synthetic boundary fixture; not quality approval"})
        self.policy = CS2TerminalPolicy(object_id="subject", revision=1, run_id="unit-run", parent_workspace=str(self.parent),
            reference_binding=self.pair(self.reference), classification_binding=self.pair(self.classification),
            intake_binding=self.pair(self.intake), sources_binding=self.pair(self.sources), inputs_binding=self.pair(self.inputs),
            user_requirements_binding=self.pair(request), approved_evidence=tuple(evidence),
            plugin_root=str(self.plugin), plugin_commit=self.commit, python=self.python, chrome=self.chrome, modules=self.modules,
            browser_root=str(self.browser_root), browser_files=self.browser_files,
            required_checks=tuple(sorted(_BASE_CHECKS | {"region:paint", "feature:visible-detail"})),
            review_id="synthetic-boundary-only", review_notes="Unit-only fixture admission, never real source/classifier approval.")
        self.policies = {"subject": self.policy}
        self.authority = CS2TerminalAuthority(self.policies)
        self.browser_calls = 0
        self.state = {"profile": "cs2", "routeChild": {"runId": "unit-run", "objectId": "subject", "revision": 1,
                                                       "outputKind": "3d-object", "cropSha256": self.reference["sha256"]}}

    @staticmethod
    def pair(ref):
        return ref["path"], ref["sha256"]

    def put(self, path, value):
        raw = value if type(value) is bytes else _encode(value)
        _write(self.child, path, raw, replace=True)
        return {"path": path, "sha256": sha(raw)}

    def put_parent(self, path, value):
        raw = _encode(value)
        _write(self.parent, path, raw, replace=True)
        return {"path": path, "sha256": sha(raw)}

    def prepare(self):
        return self.authority.prepare(self.child, object_id="subject", artifact=self.artifact, inputs=self.inputs)

    def browser(self, *args):
        self.browser_calls += 1
        config = args[2]
        viewport = config["viewport"]
        shade = 80 if config["lighting"]["ambient"]["intensity"] == 2 else 32
        report = {"deliverySha256": self.delivery["sha256"], "threeRevision": "unit-transport-only",
                  "viewport": viewport, "canvas": {"width": 40, "height": 40}, "frame": config["frame"],
                  "camera": dict(config["camera"], aspect=1), "renderer": {"name": "WebGLRenderer"}}
        # Incidental runtime paths deliberately differ each invocation.
        return picture(shade), report, {"product": "unit-transport-only"}, [f"--user-data-dir=/unit/{self.browser_calls}"], ["/", "/delivery.glb"]

    def reject(self, reason, callback, *args, **kwargs):
        with self.assertRaises(ParentError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)

    def attempt_area(self, context):
        scene = next(binding for binding in context["bindings"] if binding["role"] == "produced-scene")
        child_relative = self.child.relative_to(self.parent)
        return Path(scene["path"]).relative_to(child_relative).parent.as_posix() + "/"

    def publish_link(self, context, record_ref):
        relative = (self.child.relative_to(self.parent).as_posix() + "/" + self.attempt_area(context)
                    + "quality-review-context-" + canonical_digest(context) + ".json")
        context_ref = {"path": relative, "sha256": sha(_read(self.parent, relative)[0])}
        return self.put(AREA + "quality-review-link.json", {"context": context_ref, "record": record_ref})

    @staticmethod
    def model_advisory(context):
        legend = {
            "0": "Absent or wrong relative to the declared requirement.",
            "1": "Major mismatch with the declared requirement.",
            "2": "Partially meets the declared requirement, with visible deficiencies.",
            "3": "Meets the declared requirement.",
            "4": "Exceeds the declared requirement without changing it.",
        }
        answers = {}
        for dimension in context["requirements"]["dimensions"]:
            answers["dimension:" + dimension["id"]] = {
                "type": "score", "score": 3, "confidence": .9, "legend": legend,
                "probabilities": {str(score): int(score == 3) for score in range(5)}}
        for check in context["requirements"]["criticalFeatures"]:
            answers["critical:" + check["id"]] = {
                "type": "choice", "choice": "pass", "confidence": .9,
                "probabilities": {"pass": 1, "fail": 0, "uncertain": 0}}
        return {"kind": "img2.decision-advisory", "version": 1, "group": "quality-review",
                "subject": context["subject"], "contextDigest": canonical_digest(context),
                "model": "offline-host-model", "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 5}}

    def synthetic_journal(self, context, *, failing=False):
        record = {"recordId": canonical_digest(context), "group": "quality-review", "subject": context["subject"],
                  "status": "answered", "decider": "jev:unit-transport-not-real-review", "answers": {}}
        for dimension in _DIMENSIONS:
            record["answers"]["dimension:" + dimension] = {"type": "score", "score": 3}
        for check in self.policy.required_checks:
            record["answers"]["critical:" + check] = {"type": "choice", "choice": "pass"}
        if failing:
            record["answers"]["critical:semantic-asset-identity"]["choice"] = "fail"
        record_ref = self.put_parent(".img2threejs/decisions/quality-review/" + record["recordId"] + ".json", record)
        self.publish_link(context, record_ref)
        seen = []
        def verify(actual, expected):
            self.assertEqual(actual, record)
            self.assertEqual(expected, context)
            seen.append(actual)
            return actual
        verify.route_rounds = lambda: []
        verify.route_round_links = lambda: []
        return verify, seen

    def test_missing_policy_refuses_before_capture_or_plugin_execution(self):
        authority = CS2TerminalAuthority({})
        with patch("cs2_terminal_authority.capture_delivered_glb") as renderer:
            self.reject("cs2-terminal-policy-unavailable", authority.prepare, self.child,
                        object_id="subject", artifact=self.artifact, inputs=self.inputs)
            renderer.assert_not_called()

    def test_workspace_dictionary_cannot_supply_policy(self):
        self.reject("invalid-cs2-terminal-policies", CS2TerminalAuthority, {"subject": {"approved": True}})
        with self.assertRaises(FrozenInstanceError):
            self.policy.revision = 2
        self.reject("cs2-source-approval-required", replace, self.policy, approved_evidence=[])

    def test_live_revocation_refuses_before_renderer(self):
        self.policies.clear()
        with patch("cs2_terminal_authority.capture_delivered_glb") as renderer:
            self.reject("cs2-terminal-policy-changed", self.prepare)
            renderer.assert_not_called()

    def test_policy_cannot_omit_independent_identity_check(self):
        self.reject("cs2-independent-critical-checks-required", replace, self.policy,
                    required_checks=tuple(c for c in self.policy.required_checks if c != "semantic-asset-identity"))

    def test_self_hashed_new_camera_is_not_source_approval(self):
        other = self.put("other-camera.json", json.loads((self.child / "config-0.json").read_bytes()))
        self.inputs_value["configs"][0] = other
        self.inputs = self.put("production-inputs.json", self.inputs_value)
        policy = replace(self.policy, inputs_binding=self.pair(self.inputs))
        self.reject("cs2-source-or-camera-not-approved", self.authority._admit, policy, self.child, self.artifact, self.inputs)

    def test_missing_material_source_observations_are_named_before_capture(self):
        del self.sources_value["materialViews"]
        self.sources = self.put("sources.json", self.sources_value)
        self.inputs_value["sources"] = self.sources
        self.inputs = self.put("production-inputs.json", self.inputs_value)
        policy = replace(self.policy, sources_binding=self.pair(self.sources), inputs_binding=self.pair(self.inputs))
        self.reject("cs2-calibrated-material-source-observations-missing", self.authority._admit,
                    policy, self.child, self.artifact, self.inputs)

    def test_empty_regions_are_not_passing_source_evidence(self):
        self.sources_value["regions"] = []
        self.sources = self.put("sources.json", self.sources_value)
        self.inputs_value["sources"] = self.sources
        self.inputs = self.put("production-inputs.json", self.inputs_value)
        policy = replace(self.policy, sources_binding=self.pair(self.sources), inputs_binding=self.pair(self.inputs))
        self.reject("cs2-approved-source-regions-missing", self.authority._admit,
                    policy, self.child, self.artifact, self.inputs)

    def test_changed_approved_source_bytes_are_refused(self):
        self.put("material-source-1.png", picture(60))
        self.reject("cs2-stale-evidence:material-source-1.png", self.authority._admit,
                    self.policy, self.child, self.artifact, self.inputs)

    def test_dirty_provider_file_is_not_a_git_pin(self):
        path = self.plugin / "tools/cs2_terminal.py"
        raw = path.read_bytes()
        try:
            path.write_bytes(raw + b"\n# unit dirty pin\n")
            self.reject("cs2-provider-pin-mismatch:tools/cs2_terminal.py", self.authority._provider, self.policy)
        finally:
            path.write_bytes(raw)

    def isolated_browser_policy(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / "Browser.app"
        shutil.copytree(self.browser_root, root, symlinks=True)
        chrome = root / self.chrome_path.relative_to(self.browser_root)
        policy = replace(self.policy, browser_root=str(root),
                         browser_files=snapshot_browser_bundle(str(root)),
                         chrome=(str(chrome), sha(chrome.read_bytes())))
        self.policies["subject"] = policy
        self.authority = CS2TerminalAuthority(self.policies)
        return root

    def test_provider_root_cache_ancestor_cannot_admit_shadow_package(self):
        root = self.parent / "__pycache__/provider"
        shutil.copytree(self.plugin, root)
        policy = replace(self.policy, plugin_root=str(root))
        self.policies["subject"] = policy
        self.authority = CS2TerminalAuthority(self.policies)
        self.authority._provider(policy)
        original = PathFinder.find_spec("cs2_pixels", [str(root / "tools")])
        self.assertEqual(original.origin, str(root / "tools/cs2_pixels.py"))
        package = root / "tools/cs2_pixels"
        package.mkdir()
        # Inspect resolution only: neither this benign module nor a payload executes.
        (package / "__init__.py").write_text('\"\"\"Unapproved shadow package.\"\"\"\\n')
        shadow = PathFinder.find_spec("cs2_pixels", [str(root / "tools")])
        self.assertEqual(shadow.origin, str(package / "__init__.py"))
        with patch("cs2_terminal_authority.capture_delivered_glb") as renderer:
            self.reject("cs2-unpinned-provider-import", self.prepare)
            renderer.assert_not_called()

    def test_provider_cache_exemption_only_accepts_generated_pinned_source_bytecode(self):
        root = self.parent / "__pycache__/provider"
        shutil.copytree(self.plugin, root)
        policy = replace(self.policy, plugin_root=str(root))
        cache_dir = root / "tools/__pycache__"
        cache_dir.mkdir()
        (cache_dir / "cs2_pixels.cpython-313.pyc").write_bytes(b"ignored by isolated pycache prefix")
        self.authority._provider(policy)
        (cache_dir / "unapproved.py").write_text('\"\"\"Not generated bytecode.\"\"\"\\n')
        self.reject("cs2-unpinned-provider-import", self.authority._provider, policy)

    def test_untracked_provider_symlink_under_cache_ancestor_is_rejected(self):
        root = self.parent / "__pycache__/provider"
        shutil.copytree(self.plugin, root)
        policy = replace(self.policy, plugin_root=str(root))
        (root / "tools/unapproved.py").symlink_to("cs2_pixels.py")
        self.reject("cs2-unsafe-provider-import", self.authority._provider, policy)

    def test_changed_framework_or_added_bundle_file_revokes_before_capture(self):
        root = self.isolated_browser_policy()
        framework = root / "Contents/Frameworks/Chrome.framework/Versions/157/Chrome"
        original = framework.read_bytes()
        framework.write_bytes(b"changed framework; launcher unchanged")
        with patch("cs2_terminal_authority.capture_delivered_glb") as renderer:
            self.reject("cs2-browser-bundle-changed", self.prepare)
            renderer.assert_not_called()
        framework.write_bytes(original)
        (root / "Contents/added.dylib").write_bytes(b"unapproved code")
        with patch("cs2_terminal_authority.capture_delivered_glb") as renderer:
            self.reject("cs2-browser-bundle-changed", self.prepare)
            renderer.assert_not_called()

    def test_changed_literal_current_target_revokes_even_with_same_resolved_directory(self):
        root = self.isolated_browser_policy()
        current = root / "Contents/Frameworks/Chrome.framework/Versions/Current"
        resolved = current.resolve(strict=True)
        current.unlink()
        current.symlink_to("./157", target_is_directory=True)
        self.assertEqual(current.resolve(strict=True), resolved)
        with patch("cs2_terminal_authority.capture_delivered_glb") as renderer:
            self.reject("cs2-browser-bundle-changed", self.prepare)
            renderer.assert_not_called()

    def test_framework_change_during_capture_batch_is_refused(self):
        root = self.isolated_browser_policy()
        framework = root / "Contents/Frameworks/Chrome.framework/Versions/157/Chrome"
        def browser(*args):
            result = self.browser(*args)
            if self.browser_calls == 1:
                framework.write_bytes(b"changed during capture")
            return result
        with patch.object(capture, "_browser", side_effect=browser):
            self.reject("cs2-browser-bundle-changed", self.prepare)
        self.assertEqual(self.browser_calls, 5)

    def test_terminal_verification_rechecks_bundle_before_replay_and_after_review(self):
        root = self.isolated_browser_policy()
        framework = root / "Contents/Frameworks/Chrome.framework/Versions/157/Chrome"
        original = framework.read_bytes()
        with patch.object(capture, "_browser", side_effect=self.browser):
            context = self.prepare()
            verify, _ = self.synthetic_journal(context)
            framework.write_bytes(b"changed between prepare and terminal")
            with patch.object(self.authority, "_replay") as replay:
                self.reject("cs2-browser-bundle-changed", self.authority.verify_terminal, verify,
                            object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)
                replay.assert_not_called()
            framework.write_bytes(original)
            def changed_review(actual, expected):
                record = verify(actual, expected)
                framework.write_bytes(b"changed after capture in terminal review")
                return record
            changed_review.route_rounds = verify.route_rounds
            changed_review.route_round_links = verify.route_round_links
            self.reject("cs2-browser-bundle-changed", self.authority.verify_terminal, changed_review,
                        object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)

    def test_browser_inventory_is_required_immutable_and_external(self):
        self.reject("cs2-browser-approval-required", replace, self.policy, browser_files=())
        self.reject("cs2-browser-approval-required", replace, self.policy, browser_files=list(self.browser_files))
        self.reject("cs2-browser-executable-outside-bundle", replace, self.policy, browser_root=str(self.plugin))
        root = self.parent / "Browser.app"
        shutil.copytree(self.browser_root, root, symlinks=True)
        self.reject("cs2-browser-root-must-be-external", replace, self.policy, browser_root=str(root))

    def test_protocol_pipeline_uses_base_capture_and_real_isolated_plugin_replay(self):
        with patch.object(capture, "_browser", side_effect=self.browser):
            context = self.prepare()
            self.assertEqual(self.browser_calls, 5)
            self.assertEqual(context["subject"], {"runId": "unit-run", "objectId": "subject", "revision": 1,
                                                   "passId": "cs2-terminal-review"})
            image_roles = {row["role"] for row in context["observations"]["images"]}
            self.assertTrue({"reference", "render", "orbit-left", "orbit-right", "source-material-0", "render-material-1"} <= image_roles)
            verify, seen = self.synthetic_journal(context)
            record = self.authority.verify_terminal(verify, object_id="subject", child_workspace=self.child,
                                                     state=self.state, artifact=self.artifact)
        self.assertEqual(self.browser_calls, 10)
        self.assertEqual(len(seen), 1)
        self.assertEqual(record["recordId"], canonical_digest(context))
        # Exact equality to the reference was legitimate in this authored protocol fixture.
        self.assertEqual(_read(self.child, self.attempt_area(context) + "view-0/capture.png")[0],
                         _read(self.child, "reference.png")[0])

    def test_prepare_twice_preserves_old_attempt_and_only_link_selects_current(self):
        prior_report = self.put("cs2-review.json", {"prior": "unrelated per-pass gate report"})
        with patch.object(capture, "_browser", side_effect=self.browser):
            first = self.prepare()
            first_area = self.attempt_area(first)
            self.assertRegex(first_area, r"^\.img2/cs2-terminal/run-[a-f0-9]{32}/$")
            verify_first, _ = self.synthetic_journal(first)
            self.authority.verify_terminal(verify_first, object_id="subject", child_workspace=self.child,
                                           state=self.state, artifact=self.artifact)
            previous_link = _read(self.child, AREA + "quality-review-link.json")[0]
            old_files = {path.relative_to(self.child).as_posix(): path.read_bytes()
                         for path in (self.child / first_area).rglob("*") if path.is_file()}
            old_link = json.loads(previous_link)
            old_record = _read(self.parent, old_link["record"]["path"])[0]
            second = self.prepare()
            second_area = self.attempt_area(second)
            self.assertNotEqual(second_area, first_area)
            self.assertNotEqual(canonical_digest(second), canonical_digest(first))
            self.assertEqual(_read(self.child, AREA + "quality-review-link.json")[0], previous_link)
            verify_second, seen = self.synthetic_journal(second)
            result = self.authority.verify_terminal(verify_second, object_id="subject", child_workspace=self.child,
                                                    state=self.state, artifact=self.artifact)
        self.assertEqual(result["recordId"], canonical_digest(second))
        self.assertEqual(len(seen), 1)
        link = json.loads(_read(self.child, AREA + "quality-review-link.json")[0])
        self.assertNotEqual(link, old_link)
        self.assertIn(second_area, link["context"]["path"])
        self.assertEqual(_read(self.parent, old_link["record"]["path"])[0], old_record)
        self.assertEqual({path.relative_to(self.child).as_posix(): path.read_bytes()
                          for path in (self.child / first_area).rglob("*") if path.is_file()}, old_files)
        self.assertEqual(sha(_read(self.child, prior_report["path"])[0]), prior_report["sha256"])
        self.assertTrue((self.child / first_area / "gate-review.json").is_file())
        self.assertTrue((self.child / second_area / "gate-review.json").is_file())
        self.assertFalse((self.child / AREA / "inputs.json").exists())
        self.assertEqual(self.browser_calls, 20)

    def test_real_journal_model_outage_authorizes_terminal_review(self):
        controller = DecisionController(self.parent, "unit-run")
        consent = lambda: {"granted": True, "purposes": list(GROUPS), "sha256": "a" * 64,
                           "tokenBudget": 10000}
        provider = Mock(side_effect=AssertionError("Jev must not run during proven outage"))
        fallback = Mock(side_effect=self.model_advisory)
        with patch.object(capture, "_browser", side_effect=self.browser):
            context = self.prepare()
            record = controller.consult(context, provider=provider, fallback=fallback,
                availability=lambda: {"status": "capability-unavailable"}, consent=consent, request_token_limit=128)
            self.assertEqual(record["status"], "answered")
            self.assertEqual(record["decider"], "model:offline-host-model")
            self.assertEqual(record["unavailableReason"], "capability-unavailable")
            record_path = ".img2threejs/decisions/quality-review/" + record["recordId"] + ".json"
            self.publish_link(context, {"path": record_path, "sha256": sha(_read(self.parent, record_path)[0])})
            with controller.workspace_transaction(consent=consent) as verify:
                actual = self.authority.verify_terminal(verify, object_id="subject", child_workspace=self.child,
                                                        state=self.state, artifact=self.artifact)
        self.assertEqual(actual, record)
        provider.assert_not_called()
        fallback.assert_called_once()
        entries = [json.loads(line) for line in controller.journal_path.read_text().splitlines()]
        self.assertTrue(any(entry["event"] == "probe" and entry["data"].get("fallbackAuthorized") is True
                            and entry["data"].get("reasonCode") == "capability-unavailable" for entry in entries))
        self.assertEqual(self.browser_calls, 10)

    def test_real_journal_rejects_self_asserted_model_outage(self):
        controller = DecisionController(self.parent, "unit-run")
        consent = lambda: {"granted": True, "purposes": list(GROUPS), "sha256": "a" * 64,
                           "tokenBudget": 10000}
        with patch.object(capture, "_browser", side_effect=self.browser):
            context = self.prepare()
            self.synthetic_journal(context)
            link = json.loads(_read(self.child, AREA + "quality-review-link.json")[0])
            forged = json.loads(_read(self.parent, link["record"]["path"])[0])
            forged.update(decider="model:offline-host-model", unavailableReason="capability-unavailable")
            record_ref = self.put_parent(link["record"]["path"], forged)
            self.publish_link(context, record_ref)
            with controller.workspace_transaction(consent=consent) as verify:
                with self.assertRaisesRegex(DecisionError, "record-mismatch"):
                    self.authority.verify_terminal(verify, object_id="subject", child_workspace=self.child,
                                                   state=self.state, artifact=self.artifact)

    def test_link_requires_attempt_and_digest_named_context_before_replay(self):
        with patch.object(capture, "_browser", side_effect=self.browser):
            context = self.prepare()
        verify, _ = self.synthetic_journal(context)
        link = json.loads(_read(self.child, AREA + "quality-review-link.json")[0])
        prefix = self.child.relative_to(self.parent).as_posix() + "/"
        for path in (AREA + "quality-review-context.json",
                     self.attempt_area(context) + "quality-review-context.json",
                     AREA + "run-not-a-generated-uuid/quality-review-context-" + canonical_digest(context) + ".json"):
            with self.subTest(path=path):
                self.put(AREA + "quality-review-link.json",
                         {"context": {"path": prefix + path, "sha256": link["context"]["sha256"]},
                          "record": link["record"]})
                with patch.object(self.authority, "_replay") as replay:
                    self.reject("cs2-quality-context-path-mismatch", self.authority.verify_terminal, verify,
                                object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)
                    replay.assert_not_called()
        wrong_path = self.attempt_area(context) + "quality-review-context-" + "0" * 64 + ".json"
        ref = self.put(wrong_path, context)
        self.put(AREA + "quality-review-link.json",
                 {"context": {"path": prefix + wrong_path, "sha256": ref["sha256"]}, "record": link["record"]})
        self.reject("cs2-quality-review-context-mismatch", self.authority.verify_terminal, verify,
                    object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)

    def test_hash_named_context_cannot_change_subject_or_select_another_attempt(self):
        with patch.object(capture, "_browser", side_effect=self.browser):
            first = self.prepare()
            second = self.prepare()
        verify, _ = self.synthetic_journal(first)
        link = json.loads(_read(self.child, AREA + "quality-review-link.json")[0])
        prefix = self.child.relative_to(self.parent).as_posix() + "/"
        changed = json.loads(json.dumps(first))
        changed["subject"]["revision"] = 2
        for context, area in ((changed, self.attempt_area(first)), (first, self.attempt_area(second))):
            with self.subTest(area=area, subject=context["subject"]):
                path = area + "quality-review-context-" + canonical_digest(context) + ".json"
                ref = self.put(path, context)
                self.put(AREA + "quality-review-link.json",
                         {"context": {"path": prefix + path, "sha256": ref["sha256"]}, "record": link["record"]})
                self.reject("cs2-quality-review-context-mismatch", self.authority.verify_terminal, verify,
                            object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)

    def test_semantic_identity_failure_remains_blocking_after_real_measurement_replay(self):
        with patch.object(capture, "_browser", side_effect=self.browser):
            context = self.prepare()
            verify, _ = self.synthetic_journal(context, failing=True)
            self.reject("cs2-semantic-quality-failed", self.authority.verify_terminal, verify,
                        object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)

    def test_plain_callback_is_not_a_held_controller_verifier(self):
        self.reject("held-route-transaction-required", self.authority.verify_terminal, lambda a, b: a,
                    object_id="subject", child_workspace=self.child, state=self.state, artifact=self.artifact)

    def test_parent_relative_context_cannot_escape_or_follow_symlink(self):
        link = self.child / "source-link.png"
        link.symlink_to(self.child / "reference.png")
        policy = replace(self.policy, reference_binding=("source-link.png", self.reference["sha256"]))
        with self.assertRaises((OSError, ParentError)):
            self.authority._admit(policy, self.child, self.artifact, self.inputs)


if __name__ == "__main__":
    unittest.main()
