from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATE_CLI = ROOT / "forge" / "state.py"
ITEM_ID = "2026-07-29-test-dragon"


class CrossAgentStateCliTests(unittest.TestCase):
    def run_state(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(STATE_CLI), "--root", str(root), "--json", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_resume_acknowledgement_authorizes_one_intake_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            reference = repository / "reference.txt"
            reference.write_text("reference bytes", encoding="utf-8")

            initialized = self.run_state(
                repository,
                "init",
                "--item-id",
                ITEM_ID,
                "--primary-reference",
                str(reference),
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertEqual(json.loads(initialized.stdout)["revision"], 0)

            resumed = self.run_state(repository, "resume", ITEM_ID)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(json.loads(resumed.stdout)["nextAction"], "intake")

            blocked = self.run_state(repository, "preflight", ITEM_ID, "intake")
            self.assertEqual(blocked.returncode, 11, blocked.stderr)
            self.assertEqual(json.loads(blocked.stdout)["code"], "RESUME_REQUIRED")

            claimed = self.run_state(repository, "claim", ITEM_ID, "--actor", "codex")
            self.assertEqual(claimed.returncode, 0, claimed.stderr)

            acknowledged = self.run_state(repository, "acknowledge", ITEM_ID, "0", "--actor", "codex")
            self.assertEqual(acknowledged.returncode, 0, acknowledged.stderr)

            authorized = self.run_state(repository, "preflight", ITEM_ID, "intake", "--actor", "codex")
            self.assertEqual(authorized.returncode, 0, authorized.stderr)
            receipt = json.loads(authorized.stdout)
            self.assertEqual(receipt["action"], "intake")
            self.assertTrue(receipt["operationId"])

            replay = self.run_state(repository, "preflight", ITEM_ID, "intake", "--actor", "codex")
            self.assertEqual(replay.returncode, 17, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)["code"], "OPERATION_CONFLICT")

    def test_artifact_drift_blocks_preflight_without_mutating_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            reference = repository / "reference.txt"
            reference.write_text("before", encoding="utf-8")
            self.assertEqual(
                self.run_state(repository, "init", "--item-id", ITEM_ID, "--primary-reference", str(reference)).returncode,
                0,
            )
            self.assertEqual(self.run_state(repository, "claim", ITEM_ID, "--actor", "claude").returncode, 0)
            self.assertEqual(self.run_state(repository, "acknowledge", ITEM_ID, "0", "--actor", "claude").returncode, 0)
            reference.write_text("after", encoding="utf-8")

            drift = self.run_state(repository, "preflight", ITEM_ID, "intake", "--actor", "claude")
            self.assertEqual(drift.returncode, 12, drift.stderr)
            self.assertEqual(json.loads(drift.stdout)["code"], "ARTIFACT_DRIFT")

    def test_commit_invalidates_acknowledgement_and_preserves_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            reference = repository / "reference.txt"
            reference.write_text("reference", encoding="utf-8")
            self.assertEqual(
                self.run_state(repository, "init", "--item-id", ITEM_ID, "--primary-reference", str(reference)).returncode,
                0,
            )
            self.assertEqual(self.run_state(repository, "claim", ITEM_ID, "--actor", "claude").returncode, 0)
            self.assertEqual(self.run_state(repository, "acknowledge", ITEM_ID, "0", "--actor", "claude").returncode, 0)
            authorized = json.loads(self.run_state(repository, "preflight", ITEM_ID, "intake", "--actor", "claude").stdout)
            committed = self.run_state(
                repository,
                "commit",
                ITEM_ID,
                "--operation-id",
                authorized["operationId"],
                "--actor",
                "claude",
                "--next-action",
                "assessment",
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)
            self.assertEqual(json.loads(committed.stdout)["nextAction"], "assessment")

            stale = self.run_state(repository, "preflight", ITEM_ID, "assessment", "--actor", "claude")
            self.assertEqual(stale.returncode, 11, stale.stderr)
            current_revision = json.loads(self.run_state(repository, "resume", ITEM_ID).stdout)["revision"]
            self.assertEqual(self.run_state(repository, "acknowledge", ITEM_ID, str(current_revision), "--actor", "claude").returncode, 0)
            handoff = self.run_state(
                repository,
                "handoff",
                ITEM_ID,
                "--actor",
                "claude",
                "--recipient",
                "codex",
                "--summary",
                "intake accepted",
            )
            self.assertEqual(handoff.returncode, 0, handoff.stderr)
            self.assertEqual(json.loads(handoff.stdout)["handoff"]["recipient"], "codex")

    def test_recover_marks_an_interrupted_operation_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            reference = repository / "reference.txt"
            reference.write_text("reference", encoding="utf-8")
            self.assertEqual(
                self.run_state(repository, "init", "--item-id", ITEM_ID, "--primary-reference", str(reference)).returncode,
                0,
            )
            self.assertEqual(self.run_state(repository, "claim", ITEM_ID, "--actor", "codex").returncode, 0)
            self.assertEqual(self.run_state(repository, "acknowledge", ITEM_ID, "0", "--actor", "codex").returncode, 0)
            authorized = json.loads(self.run_state(repository, "preflight", ITEM_ID, "intake", "--actor", "codex").stdout)

            recovered = self.run_state(
                repository,
                "recover",
                ITEM_ID,
                "--operation-id",
                authorized["operationId"],
                "--actor",
                "codex",
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            snapshot = json.loads(self.run_state(repository, "read", ITEM_ID).stdout)["snapshot"]
            self.assertEqual(snapshot["operations"][0]["status"], "unknown")

    def test_second_agent_cannot_claim_an_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            reference = repository / "reference.txt"
            reference.write_text("reference", encoding="utf-8")
            self.assertEqual(
                self.run_state(repository, "init", "--item-id", ITEM_ID, "--primary-reference", str(reference)).returncode,
                0,
            )
            first = self.run_state(repository, "claim", ITEM_ID, "--actor", "claude")
            self.assertEqual(first.returncode, 0, first.stderr)
            lease = json.loads(first.stdout)["lease"]
            self.assertEqual(
                self.run_state(
                    repository,
                    "heartbeat",
                    ITEM_ID,
                    "--actor",
                    "claude",
                    "--lease-token",
                    lease["token"],
                ).returncode,
                0,
            )

            second = self.run_state(repository, "claim", ITEM_ID, "--actor", "codex")
            self.assertEqual(second.returncode, 13, second.stderr)
            self.assertEqual(json.loads(second.stdout)["code"], "LEASE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
