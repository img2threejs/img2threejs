"""The profile string the core writes for a routed owner must equal the plugin's declared domain id.

The sibling plugin checkouts are read from disk (their real `domain.json`); the tests skip with a
reason when a sibling is absent. Nothing here weakens a verifier: the native terminal authority's
expected profile is exercised unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE.parent), str(FORGE), str(FORGE / "_shared"), str(FORGE / "tests")]
SPEC = importlib.util.spec_from_file_location("img2_route_cli_owner_ids", FORGE / "route.py")
route_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route_cli)

from native_terminal_authority import NativeTerminalAuthority  # noqa: E402
from route_parent import _encode, _write  # noqa: E402

REPO = FORGE.parent
CARD_KIND = {"character": "rigged-3d", "cs2": "spec-augmentation", "interior": "3d-scene", "pixi2d": "2d-scene"}


def sibling(name: str) -> Path | None:
    """`<name>/` checkout next to the repo (searched up the ancestors, for nested worktrees)."""
    override = os.environ.get("IMG2_PLUGIN_SIBLINGS")
    roots = [Path(override)] if override else list(REPO.parents[:5])
    for root in roots:
        if (root / name / "domain.json").is_file():
            return root / name
    return None


def core_owner(provider: str, domain_bytes: bytes):
    """Run the real `_owner_profiles` for one provider against a pinned `domain.json`."""
    declared = json.loads(domain_bytes)
    candidate = {"id": "selected", "provider": provider, "resolvedSha": "a" * 40,
                 "providerStatus": "answered", "claimants": [provider], "blockingGateProduced": True,
                 "dependencies": {}, "card": {"costClass": "heavy" if provider == "character" else "local",
                                              "outputKind": CARD_KIND[provider]}}
    routed = {"decision": {"phase": "decided",
                           "request": {"expectedOutput": "2d" if provider == "pixi2d" else "3d"},
                           "objects": [{"status": "resolved", "selected": ["selected"]}]}}
    router = Mock()
    router.discovery.query.return_value = {"status": "answered", "providers": [
        {"plugin": provider, "resolvedSha": "a" * 40, "dir": "/plugin"}]}
    router.discovery._pinned_file.return_value = domain_bytes
    registered = dict(declared)  # the registry's own form: {plugin_dir} resolved, steps as tuples
    for key in ("setupSteps", "passSteps", "rigSteps"):
        if key in registered:
            registered[key] = tuple((ident, command.replace("{plugin_dir}", "/plugin"))
                                    for ident, command in registered[key])
    with patch.object(route_cli, "registered_domains", return_value={declared["id"]: registered}):
        return route_cli._owner_profiles(router, routed, {"candidates": [candidate]})["selected"]


class CoreProfileMatchesPluginDomainId(unittest.TestCase):
    def test_core_profile_equals_declared_domain_id_for_every_plugin(self):
        for provider in ("character", "cs2", "interior", "pixi2d"):
            with self.subTest(provider=provider):
                directory = sibling(provider)
                if directory is None:
                    self.skipTest(f"sibling plugin checkout '{provider}' not found next to the repo")
                raw = (directory / "domain.json").read_bytes()
                self.assertEqual(core_owner(provider, raw).profile, json.loads(raw)["id"])

    def test_character_owner_is_animated_character_rigged_3d(self):
        owner = core_owner("character", json.dumps({"id": "animated-character"}).encode())
        self.assertEqual((owner.profile, owner.output_kind), ("animated-character", "rigged-3d"))

    def test_plugin_declaring_the_legacy_character_id_fails_closed(self):
        with self.assertRaises(Exception) as caught:
            core_owner("character", json.dumps({"id": "character"}).encode())
        self.assertEqual(getattr(caught.exception, "reason_code", None), "owner-profile-unavailable")


class RoutedCharacterChildPassesNativeProfileCheck(unittest.TestCase):
    def build(self):
        from test_native_character_route import NativeRouteTests
        from test_native_terminal_authority import NativeTerminalAuthorityTests

        route_case = NativeRouteTests("test_native_child_gets_native_steps_and_plan_not_sculpt_spec")
        route_case.setUp()
        self.addCleanup(route_case.doCleanups)
        terminal_case = NativeTerminalAuthorityTests("test_no_dictionary_policy_or_mutable_nested_approval")
        terminal_case.setUp()
        self.addCleanup(terminal_case.doCleanups)
        return route_case, terminal_case

    @staticmethod
    def character_policy(case):
        exe = (str(Path(sys.executable).resolve()), "c" * 64)
        return replace(case.policy, provider="character", node=exe, browser=exe,
                       three_root=str(case.harness), three_package_sha="d" * 64)

    def test_core_created_character_child_passes_native_terminal_profile_check(self):
        route_case, terminal_case = self.build()
        owner = core_owner("character", json.dumps({"id": "animated-character"}).encode())
        # The child is created by RouteParent from the owner profile the core itself produced.
        route_case.configure(workflow=None, profile=owner.profile, kind=owner.output_kind)
        route_case.parent.create()
        routed = route_case.read(route_case.state_path)
        self.assertEqual((routed["profile"], routed["routeChild"]["outputKind"]), (owner.profile, owner.output_kind))
        # Carry the core-produced profile/outputKind into the authority fixture's subject; only the
        # run/object identity fields (fixture-owned) are re-pointed at the fixture policy.
        state = json.loads(json.dumps(terminal_case.state))
        state["profile"] = routed["profile"]
        state["routeChild"]["outputKind"] = routed["routeChild"]["outputKind"]
        (terminal_case.child / "state.json").write_bytes(_encode(state))
        policy = self.character_policy(terminal_case)
        authority = NativeTerminalAuthority(terminal_case.parent, {policy.object_id: policy})
        accepted = authority._policy(terminal_case.child, "character", state, policy.artifact_binding[0])
        self.assertEqual(accepted, (policy, terminal_case.child))

    def test_state_written_with_the_old_character_profile_fails_closed(self):
        _, terminal_case = self.build()
        state = json.loads(json.dumps(terminal_case.state))
        state["profile"] = "character"
        state["routeChild"]["outputKind"] = "rigged-3d"
        (terminal_case.child / "state.json").write_bytes(_encode(state))
        policy = self.character_policy(terminal_case)
        authority = NativeTerminalAuthority(terminal_case.parent, {policy.object_id: policy})
        with self.assertRaisesRegex(Exception, "subject-mismatch"):
            authority._policy(terminal_case.child, "character", state, policy.artifact_binding[0])


if __name__ == "__main__":
    unittest.main()
