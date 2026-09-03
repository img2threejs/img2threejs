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

    def test_both_registry_sources_register_hermetically(self) -> None:
        # A seam with one consumer is a rename, so both sources are exercised: the in-repo module,
        # and an installed plugin's domain.json. Neither half may depend on what this machine
        # happens to have under ~/.img2 -- the old form of this test asserted the INSTALLED cs2
        # plugin and was green or red depending on the machine, which is what turned CI red.
        with self._temp_img2_home({}):
            self.assertEqual(sorted(registered_domains()), ["character"])
        with self._temp_img2_home({"fixture-plugin": {"id": "fixture-dom"}}):
            self.assertEqual(sorted(registered_domains()), ["character", "fixture-dom"])

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
        # Collides with the in-repo `character` module rather than an installed plugin, so the
        # refusal is provable on a machine with nothing installed. IMG2_HOME is pinned empty for
        # the same reason: a real installation must not be able to add a second collision path.
        with self._temp_img2_home({}):
            with self.assertRaises(DomainRegistryError) as ctx:
                self._with_temp_domain(
                    "dupe",
                    'DOMAIN = {"id": "character"}',
                    registered_domains,
                )
        self.assertIn("declared twice", str(ctx.exception))

    def test_a_broken_registry_degrades_the_state_cli_instead_of_killing_it(self) -> None:
        """extract-animated-character D8: state.py builds its --profile choices from
        registered_domains() at argparse-construction time, which every subcommand runs. A registry
        collision (a plugin claiming an in-repo id) must degrade init's choices to generic-only,
        not take `status`/`mark` down for every profile on the machine -- the same hazard shape
        targets.py:14-19 documents avoiding."""
        import subprocess
        import sys as _sys
        with self._temp_img2_home({"dupe": {"id": "character"}}):
            env = dict(os.environ)
            proc = subprocess.run(
                [_sys.executable, str(ROOT.parent / "forge" / "state.py"), "init", "--help"],
                capture_output=True, text=True, env=env, cwd=ROOT.parent,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--profile {generic}", proc.stdout)
        self.assertNotIn("declared twice", proc.stderr)

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
