"""Isolated adapter probes; fixture gates never establish real Pixi/native quality."""
from copy import deepcopy
import hashlib
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
from decision_control import DecisionController, DecisionError, canonical_digest
from route_authority import GateContract, GateSpec, PIXI_CONTRACT, PixiQualityPolicy, RouteAuthority
from route_parent import OwnerProfile, ParentError, RouteParent, VerifiedAggregate
from stage0_route.discovery import Discovery, EDGES
from stage0_route.images import png
from stage0_route.route import Router
from stage0_route.storage import RouteError, encode, snapshot
from workflow_state import recompute


AREA = ".img2/artifacts/pixi2d/"
PIN = "a" * 40
RUNTIME_PIN = "b" * 40
# This is deliberately a tiny TEST producer, not a replacement Pixi visual gate.
GATE_SOURCE = '''import hashlib, json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.path.join(os.environ["IMG2_HOME"], "harness"))
from img2_core import require_core_api
require_core_api(1)
assert sys.argv[1] == "gate" and sys.argv[2] == "--workspace" and len(sys.argv) == 4
workspace = Path(sys.argv[3])
mode_file = workspace / "gate-mode.json"
mode = json.loads(mode_file.read_text()) if mode_file.exists() else "pass"
if mode == "timeout": time.sleep(2)
path = ".img2/artifacts/pixi2d/artifact.json"
raw = (workspace / path).read_bytes()
artifact = json.loads(raw)
for collection in ("editableSources", "assets", "bindings", "evidence"):
    for item in artifact[collection]:
        assert hashlib.sha256((workspace / item["path"]).read_bytes()).hexdigest() == item["sha256"]
if mode == "mutate-source": (workspace / "scene.ts").write_text("outside mutation")
if mode == "mutate-artifact": (workspace / path).write_text("{}")
sha = hashlib.sha256(raw).hexdigest() if mode != "wrong-binding" else "0" * 64
status = "fail" if mode == "fail" else "pass"
print(json.dumps({"kind":"img2.gate-verdict","version":1,"plugin":"pixi2d",
    "gate":"pixi2d-editable-review","status":status,
    "reasons":[] if status == "pass" else ["fixture failed"],
    "evidence":{"artifact":{"path":path,"sha256":sha}}}))
sys.exit(0 if status == "pass" else 1)
'''


def transaction_process(result):
    """Exercise real Discovery under a held transaction, with a strict parent deadline."""
    case = RouteAuthorityTests()
    try:
        case.setUp()
        case.probing_consent = True
        parent = case.parent.create()
        result.put({"objects": len(parent["objects"]), "status": "created"})
    except BaseException as exc:
        result.put({"error": type(exc).__name__ + ": " + str(exc)})
    finally:
        case.doCleanups()


class RouteAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.plugin = self.workspace / "plugin"
        self.runtime = self.workspace / "runtime" / "harness"
        self.committed = {}
        self.commands = []
        self.gate_commands = []
        self.live_status = "answered"
        self.live_providers = None
        self.probing_consent = False
        self.controller = DecisionController(self.workspace, "route-run", clock=lambda: 1000)
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "c" * 64, "tokenBudget": 2_000_000}
        self.provider_row = {"plugin": "pixi2d", "version": "1.0.0", "resolvedSha": PIN,
                             "dir": str(self.plugin), "gateRunner": {"argv": ["fixture-gate-runner"]}}
        self.runtime_row = {"dir": str(self.runtime), "resolvedSha": RUNTIME_PIN}
        manifest = {"name": "pixi2d", "version": "1.0.0", "capabilities": [{"from": "image", "to": "pixi2d-scene"}],
                    "requires": {"harness": ">=0.5.0", "coreApi": 1}}
        card = {"kind": "img2.route-card", "version": 1, "outputKind": "2d-scene", "costClass": "local",
                "requires": {}, "notFor": {}, "deps": [{"probe": "python3>=3.10"}], "display": {}}
        self.pin(self.plugin, "plugin.json", encode(manifest))
        self.pin(self.plugin, "route.json", encode(card))
        self.gates = [{"id": "pixi2d-editable-review", "command": "python3 {plugin_dir}/tools/pixi2d.py gate --workspace {workspace}",
                       "blocking": True, "after": []}]
        self.pin(self.plugin, "gates.json", encode(self.gates))
        self.pin(self.plugin, "tools/pixi2d.py", GATE_SOURCE.encode())
        self.pin(self.runtime, "img2_core/__init__.py", b"def require_core_api(value):\n    assert value == 1\n")
        self.discovery = Discovery(self.controller, harness=("fixture-img2",), runner=self.command)
        self.discovery.availability = Mock(return_value={"status": "present"})
        self.provider = Mock(side_effect=self.answer)
        self.router = Router(self.workspace, "route-run", controller=self.controller,
            discovery=self.discovery, provider=self.provider, consent=self.consent)
        (self.workspace / "reference.png").write_bytes(png(4, 4, 3, bytes(range(48))))
        self.routed = self.router.route(image="reference.png", expected_output="2d")
        self.assertEqual(self.routed["decision"]["phase"], "decided")
        self.discovered = self.router.verification_snapshot()
        self.candidate = next(candidate for candidate in self.discovered["candidates"] if candidate["provider"] == "pixi2d")
        self.owners = {self.candidate["id"]: OwnerProfile("generic", "2d-scene")}
        self.authority = self.authority_for()
        self.parent = RouteParent(self.controller, verify_aggregate=self.authority, consent=self.consent)

    def pin(self, root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.committed[(str(root), relative)] = data

    def consent(self):
        if self.probing_consent:
            self.discovery.run(("fixture-probe",))
        return deepcopy(self.grant)

    @staticmethod
    def answer(context):
        ids = [candidate["id"] for candidate in context["candidates"]]
        answers = {"selection": {"type": "choice", "choice": ids[0],
            "probabilities": {ident: int(index == 0) for index, ident in enumerate(ids)}, "confidence": 1},
            "anyFits": {"type": "noul", "noul": 1}}
        if "decomposition" in context["requirements"]:
            answers["missingObjects"] = {"type": "noul", "noul": 0}
            for obj in context["requirements"]["decomposition"]["objects"]:
                left, top, right, bottom = obj["approxBox"]
                answers["object:" + obj["id"] + ":visible"] = {"type": "noul", "noul": 1}
                answers["object:" + obj["id"] + ":box"] = {"type": "box", "confidence": 1,
                    "box": {"left": left, "top": top, "right": right, "bottom": bottom}}
        return {"kind": "img2.decision-advisory", "version": 1, "group": "routing",
            "subject": deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
            "model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}

    def command(self, argv, **kwargs):
        self.commands.append((list(argv), dict(kwargs)))
        self.assertIs(kwargs.get("shell"), False)
        if argv[0] == "fixture-img2":
            if argv[1:] == ["--version", "--json"]:
                value = {"harness": "0.5.0", "commands": ["capabilities"]}
                code = 0
            else:
                edge = {"from": argv[3], "to": argv[5]}
                providers = [deepcopy(self.provider_row)] if edge == {"from": "image", "to": "pixi2d-scene"} else []
                if providers and self.live_providers is not None:
                    providers = deepcopy(self.live_providers)
                status = self.live_status if edge["to"] == "pixi2d-scene" else "answered"
                value = {"version": 1, "query": edge, "status": status, "providers": providers}
                code = {"answered": 0, "ambiguous": 3, "data-fault": 1}[status]
            return subprocess.CompletedProcess(argv, code, encode(value), b"")
        if argv[0] == "git":
            pin, relative = argv[4].split(":", 1)
            self.assertIn(pin, {PIN, RUNTIME_PIN})
            data = self.committed.get((argv[2], relative))
            return subprocess.CompletedProcess(argv, 0 if data is not None else 128, data or b"", b"")
        if argv == ["python3", "--version"]:
            return subprocess.CompletedProcess(argv, 0, b"Python 3.12.0\n", b"")
        if argv == ["fixture-probe"]:
            return subprocess.CompletedProcess(argv, 0, b"fixture read-only probe", b"")
        if str(self.plugin / "tools/pixi2d.py") in argv:
            self.gate_commands.append(list(argv))
            return subprocess.run(argv, **kwargs)
        raise AssertionError("unexpected executable: " + repr(argv))

    def authority_for(self, **overrides):
        options = {"discovered": self.discovered, "owners": self.owners, "harness": ("fixture-img2",),
                   "runtime_provider": self.runtime_row, "runner": self.command}
        options.update(overrides)
        return RouteAuthority(self.router, self.routed["decisionRef"], **options)

    def assert_reason(self, reason, function, *args, **kwargs):
        with self.assertRaises(ParentError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)

    def published_child(self, *, review_document=None):
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        child = self.workspace / descriptor["statePath"].rsplit("/", 1)[0]
        state_path = self.workspace / descriptor["statePath"]
        state = json.loads(state_path.read_bytes())
        for step in state["checklist"]:
            step.update(status="done", evidence=["fixture only; not authority"])
        state["currentPass"] = "complete"
        recompute(state)
        state_path.write_bytes(encode(state))
        def binding(path, data):
            target = child / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
        source = binding("scene.ts", b"export function makeScene() { return {}; }")
        plan = binding("plan.json", b'{"fixture":true}')
        inputs = binding(AREA + "inputs.json", encode({"kind": "img2.pixi2d-inputs", "version": 1,
            "objectId": "whole-image", "reference": state["artifacts"]["reference"], "spec": "plan.json"}))
        reference = {"path": state["artifacts"]["reference"], "sha256": descriptor["cropSha256"]}
        capture = binding(AREA + "capture.json", b'{"fixture":"not-native-capture"}')
        image = binding(AREA + "evidence/baseline.png", b"fixture evidence bytes, no visual claim")
        snapshot_doc = {"kind": "img2.pixi2d-snapshot", "version": 1, "objectId": "whole-image",
                        "bindings": [inputs, reference, source, plan]}
        binding(AREA + "snapshot.json", encode(snapshot_doc))
        evidence_doc = {"kind": "img2.pixi2d-evidence", "version": 1,
                        "snapshotSha256": hashlib.sha256(encode(snapshot_doc)).hexdigest(),
                        "capture": capture, "files": [image]}
        binding(AREA + "evidence.json", encode(evidence_doc))
        review = binding(AREA + "review.json", encode(
            review_document if review_document is not None else {"fixture": "not-visual-approval"}))
        artifact = {"kind": "img2.pixi2d-scene", "version": 1, "objectId": "whole-image", "outputKind": "2d-scene",
                    "editableSources": [source], "assets": [], "bindings": snapshot_doc["bindings"], "evidence": [image],
                    "review": review, "snapshotSha256": hashlib.sha256(encode(snapshot_doc)).hexdigest(),
                    "evidenceSha256": hashlib.sha256(encode(evidence_doc)).hexdigest()}
        raw = encode(artifact)
        binding(AREA + "artifact.json", raw)
        return descriptor, child, state, raw

    def terminal(self, fixture, *, authority=None):
        descriptor, child, state, raw = fixture
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            return (authority or self.authority).verify_2d_terminal(verify, object_id="whole-image",
                child_workspace=child, artifact_path=descriptor["artifactPath"], artifact_bytes=raw, state=state)

    def quality_fixture(self):
        """Real controller journal fixture; never a native capture attestation."""
        descriptor, child, state, _ = self.published_child()
        requirements = {
            "requiredNodes": [{"id": "shape", "type": "Graphics", "edits": ["position"]}],
            "criticalFeatures": [{"id": "mark", "bbox": [0, 0, 1, 1]}],
            "behaviorChecks": [{"id": "lifecycle", "description": "Dispose and recreate without breaking shared users.",
                                "changesPixels": False}]}
        request_raw = encode(requirements)
        (self.workspace / "user-requirements.json").write_bytes(request_raw)
        self.pixi_policies = {"whole-image": PixiQualityPolicy(1, requirements,
            {"role": "user-requirements", "path": "user-requirements.json",
             "sha256": hashlib.sha256(request_raw).hexdigest()})}
        authority = self.authority_for(pixi_quality_policies=self.pixi_policies)
        artifact = json.loads((child / (AREA + "artifact.json")).read_bytes())
        snapshot_doc = json.loads((child / (AREA + "snapshot.json")).read_bytes())
        snapshot_doc["requirements"] = deepcopy(requirements)
        snapshot_doc["checks"] = ["requirements", "layout", "identity", "ownership", "motion-coverage", "provenance",
                                  "lifecycle", "edit:shape:position", "feature:mark", "behavior:lifecycle"]
        (child / (AREA + "snapshot.json")).write_bytes(encode(snapshot_doc))
        files = []
        for index, name in enumerate(("baseline", "edit-shape-position", "edit-shape-position-reset",
                "behavior-lifecycle", "behavior-lifecycle-reset", "feature-mark-reference", "feature-mark-render")):
            width = height = 1 if name.startswith("feature-") else 4
            raw = png(width, height, 3, bytes([index, index, index]) * width * height)
            path = AREA + "evidence/" + name + ".png"
            (child / path).write_bytes(raw)
            files.append({"path": path, "sha256": hashlib.sha256(raw).hexdigest()})
        evidence = json.loads((child / (AREA + "evidence.json")).read_bytes())
        evidence["files"] = files
        evidence["snapshotSha256"] = hashlib.sha256(encode(snapshot_doc)).hexdigest()
        (child / (AREA + "evidence.json")).write_bytes(encode(evidence))
        artifact.update(evidence=files, snapshotSha256=evidence["snapshotSha256"],
                        evidenceSha256=hashlib.sha256(encode(evidence)).hexdigest())
        raw = encode(artifact)
        (child / (AREA + "artifact.json")).write_bytes(raw)
        return (descriptor, child, state, raw), authority

    @staticmethod
    def quality_answer(context, *, score=4, critical_choice="pass"):
        legend = {"0": "Absent or wrong relative to the declared requirement.",
            "1": "Major mismatch with the declared requirement.",
            "2": "Partially meets the declared requirement, with visible deficiencies.",
            "3": "Meets the declared requirement.",
            "4": "Exceeds the declared requirement without changing it."}
        answers = {"dimension:" + item["id"]: {"type": "score", "score": score, "confidence": 1,
            "legend": legend, "probabilities": {str(index): int(index == score) for index in range(5)}}
            for item in context["requirements"]["dimensions"]}
        answers.update({"critical:" + item["id"]: {"type": "choice", "choice": critical_choice,
            "probabilities": {choice: int(choice == critical_choice) for choice in ("pass", "fail", "uncertain")},
            "confidence": 1} for item in context["requirements"]["criticalFeatures"]})
        return {"kind": "img2.decision-advisory", "version": 1, "group": "quality-review",
            "subject": deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
            "model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}

    def journal_review(self, fixture, authority, *, mutate_context=None, score=4, critical_choice="pass"):
        child = fixture[1]
        context = authority.pixi_quality_context("whole-image", child)
        if mutate_context is not None:
            mutate_context(context)
        record = self.controller.consult(context,
            provider=lambda request: self.quality_answer(request, score=score, critical_choice=critical_choice),
            availability=lambda: {"status": "present"}, consent=self.consent, request_token_limit=128)
        relative = child.relative_to(self.workspace).as_posix()
        context_path = relative + "/" + AREA + "quality-review-context.json"
        context_raw = encode(context)
        (self.workspace / context_path).write_bytes(context_raw)
        record_path = ".img2threejs/decisions/quality-review/" + record["recordId"] + ".json"
        record_raw = (self.workspace / record_path).read_bytes()
        link = {"context": {"path": context_path, "sha256": hashlib.sha256(context_raw).hexdigest()},
                "record": {"path": record_path, "sha256": hashlib.sha256(record_raw).hexdigest()}}
        (child / (AREA + "quality-review-link.json")).write_bytes(encode(link))
        return context, record

    def test_genuine_journal_review_consumed_but_capture_authority_still_required(self):
        fixture, authority = self.quality_fixture()
        context, record = self.journal_review(fixture, authority)
        self.assertEqual(context["requirements"]["requiredViews"], ["view-" + str(index) for index in range(7)])
        self.assertIn("artifact", {binding["role"] for binding in context["bindings"]})
        self.assertNotIn("depth", context["observations"])
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            consumed = authority.consume_pixi_quality(verify, "whole-image", fixture[1])
        self.assertEqual(consumed, record)
        self.assertEqual(consumed["status"], "answered")
        # A real journal proves where the synthetic unit-test advisory came from,
        # not that these fixture pixels were captured in a real native host.
        self.assert_reason("terminal-native-capture-authority-unavailable", self.terminal, fixture, authority=authority)
        parent = RouteParent(self.controller, verify_aggregate=authority, consent=self.consent)
        self.assert_reason("terminal-native-capture-authority-unavailable", parent.complete_child, "whole-image")

    def test_quality_review_wrong_subject_or_crops_cannot_be_laundered(self):
        fixture, authority = self.quality_fixture()
        self.journal_review(fixture, authority,
                            mutate_context=lambda context: context["subject"].update(objectId="other-subject"))
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("pixi-quality-evidence-mismatch", authority.consume_pixi_quality,
                               verify, "whole-image", fixture[1])
        self.journal_review(fixture, authority)
        crop = fixture[1] / (AREA + "evidence/feature-mark-render.png")
        crop.write_bytes(png(1, 1, 3, b"xyz"))
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("stale-terminal-evidence", authority.consume_pixi_quality,
                               verify, "whole-image", fixture[1])
        artifact_path = fixture[1] / (AREA + "artifact.json")
        evidence_path = fixture[1] / (AREA + "evidence.json")
        artifact = json.loads(artifact_path.read_bytes())
        evidence = json.loads(evidence_path.read_bytes())
        for binding in evidence["files"]:
            if binding["path"].endswith("feature-mark-render.png"):
                binding["sha256"] = hashlib.sha256(crop.read_bytes()).hexdigest()
        evidence_path.write_bytes(encode(evidence))
        artifact["evidence"] = evidence["files"]
        artifact["evidenceSha256"] = hashlib.sha256(encode(evidence)).hexdigest()
        artifact_path.write_bytes(encode(artifact))
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("pixi-quality-evidence-mismatch", authority.consume_pixi_quality,
                               verify, "whole-image", fixture[1])

    def test_quality_review_record_tamper_revocation_and_policy_revocation_block(self):
        fixture, authority = self.quality_fixture()
        _, record = self.journal_review(fixture, authority)
        record_path = self.controller.record_root / "quality-review" / (record["recordId"] + ".json")
        raw = record_path.read_bytes()
        record_path.write_bytes(raw + b" ")
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("stale-pixi-quality-link", authority.consume_pixi_quality, verify, "whole-image", fixture[1])
        forged = deepcopy(record)
        forged["answers"]["dimension:geometry"]["score"] = 3
        forged_raw = encode(forged)
        record_path.write_bytes(forged_raw)
        link_path = fixture[1] / (AREA + "quality-review-link.json")
        link = json.loads(link_path.read_bytes())
        original_link = deepcopy(link)
        link["record"]["sha256"] = hashlib.sha256(forged_raw).hexdigest()
        link_path.write_bytes(encode(link))
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            with self.assertRaises(DecisionError) as caught:
                authority.consume_pixi_quality(verify, "whole-image", fixture[1])
            self.assertEqual(caught.exception.reason_code, "record-mismatch")
        link_path.write_bytes(encode(original_link))
        record_path.write_bytes(raw)
        self.grant["granted"] = False
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            with self.assertRaises(DecisionError):
                authority.consume_pixi_quality(verify, "whole-image", fixture[1])
        self.grant["granted"] = True
        self.pixi_policies.clear()
        self.assert_reason("pixi2d-quality-policy-changed", authority.pixi_quality_context, "whole-image", fixture[1])

    def test_quality_review_low_scores_and_failed_critical_checks_cannot_promote(self):
        fixture, authority = self.quality_fixture()
        self.journal_review(fixture, authority, score=2)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("pixi-semantic-quality-failed", authority.consume_pixi_quality, verify, "whole-image", fixture[1])
        # Different candidate bytes force a fresh question instead of reusing the
        # previous (correctly authenticated but failing) semantic answer.
        review_path = fixture[1] / (AREA + "review.json")
        review_path.write_bytes(b'{"fixture":"revised native report, still not authority"}')
        artifact = json.loads(fixture[3])
        artifact["review"]["sha256"] = hashlib.sha256(review_path.read_bytes()).hexdigest()
        raw = encode(artifact)
        (fixture[1] / (AREA + "artifact.json")).write_bytes(raw)
        revised = (fixture[0], fixture[1], fixture[2], raw)
        self.journal_review(revised, authority, critical_choice="fail")
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("pixi-semantic-quality-failed", authority.consume_pixi_quality, verify, "whole-image", fixture[1])

    def test_real_router_verification_and_parent_creation_bind_exact_inventory(self):
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            result = self.authority(verify)
            self.assertIs(type(result), VerifiedAggregate)
            self.assertEqual(result.decision, self.routed["decision"])
            self.assertEqual(result.owners, self.owners)
            self.authority.revalidate(verify, decision=result.decision, inventory=result.inventory, owners=result.owners)
            forged = deepcopy(result.inventory)
            forged["objects"].clear()
            self.assert_reason("aggregate-authority-mismatch", self.authority.revalidate, verify,
                               decision=result.decision, inventory=forged, owners=result.owners)
        parent = self.parent.create()
        self.assertEqual(parent["objects"][0]["ownerRoute"], self.candidate["id"])
        self.assertFalse(parent["routeDecision"]["executionAuthorized"])

    def test_forged_unsealed_aggregate_never_becomes_execution_authority(self):
        forged = deepcopy(self.routed["decision"])
        forged["proposalRounds"][0]["triggerDecisionIds"] = ["0" * 64]
        raw = encode(forged)
        ref = ".img2threejs/route/decision-" + hashlib.sha256(raw).hexdigest() + ".json"
        (self.workspace / ref).write_bytes(raw)
        (self.workspace / self.router.latest).write_bytes(encode({"path": ref, "sha256": hashlib.sha256(raw).hexdigest()}))
        self.authority.decision_ref = ref
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            with self.assertRaises(RouteError):
                self.authority(verify)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_changed_or_revoked_owner_approval_is_not_a_serialized_grant(self):
        self.owners.clear()
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("owner-profile-approval-changed", self.authority, verify)
        forged = {self.candidate["id"]: {"profile": "generic", "output_kind": "2d-scene"}}
        self.assert_reason("invalid-route-authority-input", self.authority_for, owners=forged)

    def test_stale_live_provider_card_dependency_and_latest_pointer_fail(self):
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.live_providers = [dict(self.provider_row, resolvedSha="d" * 40)]
            self.assert_reason("live-provider-identity-mismatch", self.authority, verify)
            self.live_providers = [self.provider_row, self.provider_row]
            self.assert_reason("live-provider-unavailable-or-ambiguous", self.authority, verify)
            self.live_providers = None
            path = self.plugin / "route.json"
            raw = path.read_bytes()
            path.write_bytes(b"{}")
            self.assert_reason("provider-pin-mismatch", self.authority, verify)
            path.write_bytes(raw)
            latest = self.workspace / self.router.latest
            latest.write_bytes(encode({"path": "old", "sha256": "0" * 64}))
            self.assert_reason("stale-route-aggregate", self.authority, verify)

    def test_real_fixture_gate_invoked_with_bounded_explicit_argv_and_byte_binding(self):
        fixture = self.published_child()
        self.assert_reason("terminal-authenticated-quality-review-unavailable", self.terminal, fixture)
        self.assertEqual(len(self.gate_commands), 1)
        argv = self.gate_commands[0]
        self.assertEqual(argv[-4:], [str(self.plugin / "tools/pixi2d.py"), "gate", "--workspace", str(fixture[1])])
        invocation = next(kwargs for command, kwargs in self.commands if command == argv)
        self.assertEqual(invocation["timeout"], 30)
        self.assertIs(invocation["shell"], False)
        self.assertEqual(invocation["env"]["IMG2_HOME"], str(self.runtime.parent))
        self.assertNotIn("PYTHONPATH", invocation["env"])
        self.assert_reason("terminal-authenticated-quality-review-unavailable", self.parent.complete_child, "whole-image")
        parent = json.loads((self.workspace / ".img2threejs/state.json").read_bytes())
        object_step = next(step for step in parent["checklist"] if step["id"] == "object:whole-image")
        self.assertEqual(object_step["status"], "pending")

    def test_forged_native_vision_scores_and_decision_reference_cannot_promote(self):
        forged = {"kind": "img2.pixi2d-review", "version": 1,
            "reviewer": {"method": "native-vision", "id": "jev:claimed-reviewer"},
            "unresolvedDefects": [], "decisionRef": "0" * 64,
            "assessments": [{"id": "identity", "judgment": "pass", "score": 4,
                             "reason": "workspace-authored approval"}]}
        fixture = self.published_child(review_document=forged)
        # Recomputed hashes and an actually executed passing fixture gate still
        # cannot authenticate these author-controlled semantic assertions.
        self.assert_reason("terminal-authenticated-quality-review-unavailable", self.terminal, fixture)
        self.assertEqual(len(self.gate_commands), 1)
        self.assert_reason("terminal-authenticated-quality-review-unavailable", self.parent.complete_child, "whole-image")
        parent = json.loads((self.workspace / ".img2threejs/state.json").read_bytes())
        self.assertNotEqual(parent["status"], "complete")

    def test_missing_or_failed_gates_are_blocked_despite_done_checklist(self):
        fixture = self.published_child()
        (fixture[1] / "gate-mode.json").write_text(json.dumps("fail"))
        self.assert_reason("blocking-terminal-gate-failed", self.terminal, fixture)
        (fixture[1] / "gate-mode.json").unlink()
        self.pin(self.plugin, "gates.json", encode(self.gates + [{"id": "new-mandatory", "blocking": True,
            "command": "python3 {plugin_dir}/tools/pixi2d.py gate --workspace {workspace}"}]))
        self.assert_reason("unsupported-blocking-terminal-gate", self.terminal, fixture)
        self.pin(self.plugin, "gates.json", encode([]))
        self.assert_reason("live-blocking-producer-mismatch", self.terminal, fixture)

    def test_gate_binding_wrong_artifact_bytes_and_stale_sources_are_rejected(self):
        fixture = self.published_child()
        (fixture[1] / "gate-mode.json").write_text(json.dumps("wrong-binding"))
        self.assert_reason("terminal-gate-artifact-binding-mismatch", self.terminal, fixture)
        (fixture[1] / "gate-mode.json").unlink()
        descriptor, child, state, raw = fixture
        self.assert_reason("terminal-artifact-bytes-mismatch", self.terminal, (descriptor, child, state, raw + b"\n"))
        (child / "scene.ts").write_bytes(b"changed")
        self.assert_reason("stale-terminal-evidence", self.terminal, fixture)

    def test_every_child_local_evidence_collection_is_independently_rehashed(self):
        fixture = self.published_child()
        child = fixture[1]
        for path in ("plan.json", AREA + "review.json", AREA + "evidence/baseline.png"):
            with self.subTest(path=path):
                source = child / path
                raw = source.read_bytes()
                source.write_bytes(b"stale")
                self.assert_reason("stale-terminal-evidence", self.terminal, fixture)
                source.write_bytes(raw)
        source = child / (AREA + "capture.json")
        source.write_bytes(b"stale capture")
        self.assert_reason("stale-terminal-capture", self.terminal, fixture)

    def test_input_reference_must_match_the_router_authenticated_crop(self):
        fixture = self.published_child()
        descriptor, child, state, raw = fixture
        artifact = json.loads(raw)
        artifact["bindings"] = [binding for binding in artifact["bindings"] if binding["path"] != state["artifacts"]["reference"]]
        snapshot_doc = json.loads((child / (AREA + "snapshot.json")).read_bytes())
        snapshot_doc["bindings"] = artifact["bindings"]
        (child / (AREA + "snapshot.json")).write_bytes(encode(snapshot_doc))
        artifact["snapshotSha256"] = hashlib.sha256(encode(snapshot_doc)).hexdigest()
        raw = encode(artifact)
        (child / (AREA + "artifact.json")).write_bytes(raw)
        self.assert_reason("terminal-reference-not-bound", self.terminal, (descriptor, child, state, raw))

    def test_gate_cannot_change_artifact_or_evidence_during_execution(self):
        fixture = self.published_child()
        (fixture[1] / "gate-mode.json").write_text(json.dumps("mutate-source"))
        self.assert_reason("stale-terminal-evidence", self.terminal, fixture)
        (fixture[1] / "scene.ts").write_bytes(b"export function makeScene() { return {}; }")
        (fixture[1] / "gate-mode.json").write_text(json.dumps("mutate-artifact"))
        self.assert_reason("terminal-artifact-changed-during-gate", self.terminal, fixture)

    def test_pinned_runtime_and_pinned_imports_are_mandatory(self):
        fixture = self.published_child()
        self.assert_reason("pinned-gate-runtime-required", self.terminal, fixture,
                           authority=self.authority_for(runtime_provider=None))
        (self.runtime / "img2_core/__init__.py").write_bytes(b"raise RuntimeError('outside edit')")
        self.assert_reason("provider-pin-mismatch", self.terminal, fixture)
        (self.runtime / "img2_core/__init__.py").write_bytes(self.committed[(str(self.runtime), "img2_core/__init__.py")])
        (self.plugin / "tools/json.py").write_bytes(b"# untracked shadow module")
        self.assert_reason("provider-pin-mismatch", self.terminal, fixture)

    def test_terminal_gate_timeout_fails_closed(self):
        fixture = self.published_child()
        (fixture[1] / "gate-mode.json").write_text(json.dumps("timeout"))
        self.assert_reason("authority-command-timeout", self.terminal, fixture,
                           authority=self.authority_for(timeout=0.1))

    def test_no_nested_controller_lock_or_probe_journaling_in_transaction(self):
        self.probing_consent = True
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            with patch.object(self.controller, "record_probe", side_effect=AssertionError("nested probe lock")), \
                 patch.object(self.controller, "consume", side_effect=AssertionError("nested consume lock")), \
                 patch.object(self.controller, "workspace_transaction", side_effect=AssertionError("nested transaction")):
                result = self.authority(verify)
                self.authority.revalidate(verify, decision=result.decision, inventory=result.inventory, owners=result.owners)
        self.assertTrue(any(argv == ["fixture-probe"] for argv, _ in self.commands))

    def test_true_process_deadline_catches_discovery_lock_regression(self):
        context = multiprocessing.get_context("spawn")
        result = context.Queue()
        process = context.Process(target=transaction_process, args=(result,))
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(2)
            self.fail("route authority deadlocked while re-probing under workspace_transaction")
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result.get(timeout=2), {"objects": 1, "status": "created"})
        result.close()

    def test_no_registered_terminal_contract_never_means_no_gates_needed(self):
        fixture = self.published_child()
        self.assert_reason("unsupported-terminal-gate-contract", self.terminal, fixture,
                           authority=self.authority_for(contracts=()))
        self.assertEqual(self.gate_commands, [])

    def test_base_route_stays_blocked_without_real_terminal_measurement_contract(self):
        routed = self.router.route(image="reference.png", expected_output="3d", objects={
            "kind": "img2.object-proposals", "version": 1, "objects": [
                {"objectId": "solid", "label": "solid", "role": "prop", "approxBox": [0, 0, 1, 1]}]})
        self.assertEqual(routed["decision"]["phase"], "decided")
        authority = RouteAuthority(self.router, routed["decisionRef"], discovered=self.router.verification_snapshot(),
            owners={"base:generic": OwnerProfile("generic", "3d-object")}, harness=("fixture-img2",), runner=self.command)
        parent = RouteParent(self.controller, verify_aggregate=authority, consent=self.consent).create()
        descriptor = parent["objects"][0]
        child = self.workspace / descriptor["statePath"].rsplit("/", 1)[0]
        state = json.loads((self.workspace / descriptor["statePath"]).read_bytes())
        artifact = {"objectId": "solid", "outputKind": "3d-object"}
        (child / "artifact.json").write_bytes(encode(artifact))
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assert_reason("base-terminal-measurement-contract-unavailable", authority.verify_terminal, verify,
                object_id="solid", child_workspace=child, state=state, artifact=artifact)
        self.assertEqual(self.gate_commands, [])

    def test_registered_contract_cannot_skip_execution_with_empty_gate_set(self):
        invalid = GateContract("pixi2d", "2d-scene", "img2.pixi2d-scene", AREA + "artifact.json", (), ("bindings",), ())
        self.assert_reason("invalid-gate-contract", self.authority_for, contracts=(invalid,))

    def test_d18_cannot_be_registered_as_a_pixi_style_envelope(self):
        native = GateContract("interior", "3d-scene", "img2.native-artifact", "artifact.json",
            (GateSpec("native-terminal", "tools/native_gate.py", ("--workspace", "{workspace}")),),
            ("files",), ())
        self.assert_reason("native-terminal-contract-amendment-required", self.authority_for, contracts=(native,))

    def test_only_pinned_cs2_augmentation_maps_to_cs2_base_3d_owner(self):
        candidate = deepcopy(self.candidate)
        candidate.update(id="cs2-fixture", provider="cs2")
        candidate["card"]["outputKind"] = "spec-augmentation"
        discovered = {"candidates": [candidate], "rejected": []}
        # This unit checks owner mapping only, not live provider verification.
        # A real aggregate call still re-queries CS2 and checks exact Git bytes.
        with patch("route_authority.new_state", return_value={"fixtureProfile": "cs2"}) as template:
            mapped = self.authority_for(discovered=discovered,
                owners={"cs2-fixture": OwnerProfile("cs2", "3d-object")})
            self.assertEqual(mapped._owners["cs2-fixture"].output_kind, "3d-object")
            template.assert_called_once_with("reference.png", profile="cs2")
            for owner in (OwnerProfile("generic", "3d-object"), OwnerProfile("cs2", "3d-scene"),
                          OwnerProfile("cs2", "spec-augmentation")):
                self.assert_reason("owner-output-mismatch", self.authority_for,
                    discovered=discovered, owners={"cs2-fixture": owner})
            for provider, pin in (("interior", PIN), ("character", PIN), ("cs2", "not-pinned")):
                altered = deepcopy(candidate)
                altered.update(provider=provider, resolvedSha=pin)
                self.assert_reason("owner-output-mismatch", self.authority_for,
                    discovered={"candidates": [altered], "rejected": []},
                    owners={"cs2-fixture": OwnerProfile("cs2", "3d-object")})

    def test_live_consent_revocation_blocks_aggregate_even_with_valid_json(self):
        self.grant["granted"] = False
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            with self.assertRaises(ValueError):
                self.authority(verify)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())


if __name__ == "__main__":
    unittest.main()
