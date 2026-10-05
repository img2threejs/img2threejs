"""The domain registry is the seam that lets the base pipeline stop naming domains.

These are the refusal cases. The happy paths are covered by test_workflow_state.py's checklist
assertions, which already pin the 21 / 23 / 25 step counts per profile.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_shared"))

import domains  # noqa: E402
from domains import DomainRegistryError, domain_profile, registered_domains  # noqa: E402
from workflow_state import WorkflowStateError, new_state  # noqa: E402


class Registry(unittest.TestCase):
    def test_the_base_pipeline_names_no_domain(self) -> None:
        # The whole point of the change: grep the base state machine for domain names.
        for name in ("workflow_state.py", "state.py"):
            path = ROOT / "_shared" / name if name == "workflow_state.py" else ROOT / name
            body = path.read_text().lower()
            for domain in ("cs2", "character"):
                self.assertNotIn(domain, body, f"{name} still names the {domain!r} domain")

    def test_generic_resolves_to_no_domain(self) -> None:
        self.assertIsNone(domain_profile("generic"))

    def test_the_repo_ships_no_domain_of_its_own(self) -> None:
        """After the character extraction the in-repo source is EMPTY, by design.

        That is the property worth pinning, because it is the one that decays: a domain module
        dropped back into the package would work, silently, and the base would quietly own a domain
        again. `registered_domains()` with an empty home returning `{}` is the assertion that
        cannot be satisfied by a repo that kept one.
        """
        with self._temp_img2_home({}):
            self.assertEqual(registered_domains(), {})
        package = Path(domains.__file__).resolve().parent
        self.assertEqual([p.name for p in package.glob("*.py") if p.name != "__init__.py"], [])

    def test_two_installed_plugins_register_through_the_same_mechanism(self) -> None:
        """D9: the seam keeps two consumers, and neither is in this repo.

        This test used to exercise "the in-repo module AND an installed plugin", on the reasoning
        that a seam with one consumer is a rename rather than an abstraction. The reasoning holds;
        the in-repo half is gone. So both consumers are installed plugins now -- which is a stronger
        form of the same claim, since two providers arriving by the same path with no in-repo
        special case is exactly what "the base pipeline does not change either way" means.
        """
        with self._temp_img2_home({"plugin-a": {"id": "alpha-dom"},
                                   "plugin-b": {"id": "beta-dom"}}):
            registered = registered_domains()
            self.assertEqual(sorted(registered), ["alpha-dom", "beta-dom"])
            self.assertEqual(registered["alpha-dom"]["id"], "alpha-dom")
            self.assertEqual(domain_profile("beta-dom")["id"], "beta-dom")

    def test_an_unregistered_profile_fails_loud_and_names_what_is_available(self) -> None:
        with self.assertRaises(DomainRegistryError) as ctx:
            domain_profile("valorant")
        message = str(ctx.exception)
        self.assertIn("valorant", message)
        self.assertIn("no installed provider", message)
        self.assertIn("generic", message)

    def test_new_state_refuses_an_unregistered_profile_rather_than_downgrading(self) -> None:
        with self.assertRaises(WorkflowStateError) as ctx:
            new_state("ref.png", profile="valorant")
        self.assertIn("valorant", str(ctx.exception))

    def test_an_unknown_anchor_is_refused_not_appended(self) -> None:
        # Appending at the end would place a domain's setup step after the steps that consume it.
        with self.assertRaises(WorkflowStateError) as ctx:
            self._with_temp_domain(
                "anchortest",
                'DOMAIN = {"id": "anchortest", "setupSteps": (("x", "y"),), "setupAnchorBefore": "no-such-step"}',
                lambda: new_state("ref.png", profile="anchortest"),
            )
        self.assertIn("no-such-step", str(ctx.exception))

    def test_an_unknown_key_is_refused_not_ignored(self) -> None:
        with self.assertRaises(DomainRegistryError) as ctx:
            self._with_temp_domain(
                "keytest",
                'DOMAIN = {"id": "keytest", "setupStpes": ()}',
                registered_domains,
            )
        self.assertIn("setupStpes", str(ctx.exception))

    def test_steps_without_an_anchor_are_refused(self) -> None:
        with self.assertRaises(DomainRegistryError) as ctx:
            self._with_temp_domain(
                "anchorless",
                'DOMAIN = {"id": "anchorless", "passSteps": (("a", "b"),)}',
                registered_domains,
            )
        self.assertIn("passAnchorBefore", str(ctx.exception))

    def test_two_providers_claiming_one_id_is_ambiguous(self) -> None:
        # Collided with the in-repo `character` module until that module left. Two INSTALLED
        # plugins claiming one id is now both the realistic case and the only expressible one,
        # and it is the case that matters: two plugins is how a collision actually reaches a user.
        with self._temp_img2_home({"first": {"id": "contested"}, "second": {"id": "contested"}}):
            with self.assertRaises(DomainRegistryError) as ctx:
                registered_domains()
        self.assertIn("declared twice", str(ctx.exception))
        self.assertIn("contested", str(ctx.exception))

    def test_an_installed_plugin_colliding_with_an_in_repo_module_is_still_refused(self) -> None:
        # There is no in-repo domain to collide with today, so the path is exercised with a
        # temporary one. Deleted alongside the last in-repo domain it would have been an untested
        # branch in `claim()`, live for whoever adds the next one.
        with self._temp_img2_home({"plugin": {"id": "inrepo-clash"}}):
            with self.assertRaises(DomainRegistryError) as ctx:
                self._with_temp_domain("clash", 'DOMAIN = {"id": "inrepo-clash"}', registered_domains)
        self.assertIn("declared twice", str(ctx.exception))

    def test_a_broken_registry_does_not_kill_the_state_cli(self) -> None:
        """`state.py` used to build `--profile` choices from `registered_domains()` at
        argparse-construction time, which every subcommand runs -- so a registry collision could
        take `status` and `mark` down for every profile on the machine. It was guarded by degrading
        the choices to generic-only.

        The guard is now the absence of the hazard: there is no `choices=`, so the parser never
        touches the registry (see `state.py`, and D8's other half at 3.3a). `--help` must still
        work with a registry that cannot be read, and it must not print a choices list at all --
        that list was the thing that made a bad profile unanswerable, naming an availability set
        from which "your plugin is missing" and "this profile no longer exists" look identical.
        """
        import subprocess
        import sys as _sys
        with self._temp_img2_home({"a": {"id": "contested"}, "b": {"id": "contested"}}):
            proc = subprocess.run(
                [_sys.executable, str(ROOT.parent / "forge" / "state.py"), "init", "--help"],
                capture_output=True, text=True, env=dict(os.environ), cwd=ROOT.parent,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("declared twice", proc.stderr)
        self.assertNotIn("--profile {", proc.stdout)

    def test_a_bad_profile_is_answered_by_the_registry_not_by_argparse(self) -> None:
        """One bad profile used to give two different answers: `init` printed argparse's
        `invalid choice: 'x' (choose from ...)`, while `resume` went through
        `validate_state -> domain_profile` and printed a message naming the missing provider and
        the remedy. Same question, two answers, only one of them useful."""
        import subprocess
        import sys as _sys
        with self._temp_img2_home({}), tempfile.TemporaryDirectory() as work:
            proc = subprocess.run(
                [_sys.executable, str(ROOT.parent / "forge" / "state.py"), "init",
                 "--reference", "ref.png", "--profile", "character",
                 "--state", str(Path(work) / "state.json")],
                capture_output=True, text=True, env=dict(os.environ), cwd=ROOT.parent,
            )
        self.assertNotEqual(proc.returncode, 0)
        output = proc.stdout + proc.stderr
        self.assertNotIn("invalid choice", output)
        self.assertIn("no installed provider serves profile 'character'", output)

    def test_a_withdrawn_profile_names_its_successor_and_the_remedy(self) -> None:
        """`domain-step-contribution`: a refusal must name the withdrawn identifier, name its
        successor and state the remedy. The plain "no installed provider" message does none of
        those -- and its advice, "install the domain plugin that provides it", cannot succeed for
        a withdrawn id, since obeying it installs the current plugin and reproduces the message."""
        with self._temp_img2_home({}):
            with self.assertRaises(DomainRegistryError) as ctx:
                domain_profile("animated-character")
        message = str(ctx.exception)
        self.assertIn("animated-character", message)
        self.assertIn("withdrawn", message)
        self.assertIn("'character'", message)
        self.assertIn("--profile character", message)

    def test_a_withdrawn_profile_says_so_even_when_its_successor_is_installed(self) -> None:
        # The successor being present changes the wording ("is served by" rather than "was replaced
        # by") but must never turn the refusal into a resolution: silently routing a withdrawn id
        # to its successor would hide the withdrawal from every script that still names it.
        with self._temp_img2_home({"character": {"id": "character"}}):
            with self.assertRaises(DomainRegistryError) as ctx:
                domain_profile("animated-character")
        self.assertIn("is served by 'character'", str(ctx.exception))

    @contextlib.contextmanager
    def _temp_img2_home(self, plugins: dict[str, dict]):
        """A disposable $IMG2_HOME holding exactly `plugins` ({registry_id: domain_entry})."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rows = []
            for registry_id, domain_entry in plugins.items():
                rows.append({"id": registry_id})
                plugin_dir = home / "plugins" / registry_id
                plugin_dir.mkdir(parents=True)
                (plugin_dir / "domain.json").write_text(json.dumps(domain_entry), encoding="utf-8")
            (home / "plugins.json").write_text(
                json.dumps({"version": 1, "plugins": rows}), encoding="utf-8"
            )
            prior = os.environ.get("IMG2_HOME")
            os.environ["IMG2_HOME"] = str(home)
            try:
                yield home
            finally:
                if prior is None:
                    os.environ.pop("IMG2_HOME", None)
                else:
                    os.environ["IMG2_HOME"] = prior

    def _with_temp_domain(self, stem: str, body: str, action):
        """Drop a domain module into the package for one assertion, then remove it."""
        path = Path(domains.__file__).resolve().parent / f"zz_{stem}.py"
        path.write_text(textwrap.dedent(body) + "\n")
        try:
            return action()
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
