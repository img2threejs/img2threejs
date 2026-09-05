#!/usr/bin/env python3
"""cli_run contract: a piped consumer closing early must exit 141 SILENTLY
(this used to be a BrokenPipeError traceback plus an "Exception ignored" line,
which agents read as command failure), Ctrl-C maps to 130, a missing input file
prints one line and exits 66, and a vendored copy without the forge runtime
still runs (bare, no pipe handling).

Run: python forge/tests/test_cli_run.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SHARED = Path(__file__).resolve().parent.parent / "_shared"

CHILD_TEMPLATE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {shared!r})
    from cli_run import run_entry

    {body}
    """
)


def run_shared_child(body: str) -> subprocess.CompletedProcess[str]:
    source = CHILD_TEMPLATE.format(shared=str(SHARED), body=body)
    return subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)


class CliRunTest(unittest.TestCase):
    def test_broken_pipe_exits_141_silently(self) -> None:
        body = textwrap.dedent(
            """
            def main(argv):
                for _ in range(20000):
                    print("x" * 100)
                return 0

            raise SystemExit(run_entry(main))
            """
        )
        # Close the read end after a small read so the child's later writes hit
        # a closed pipe (POSIX raises BrokenPipeError, Windows EINVAL — both are
        # supposed to funnel to exit 141 with no stderr noise).
        process = subprocess.Popen(
            [sys.executable, "-c", CHILD_TEMPLATE.format(shared=str(SHARED), body=body)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        process.stdout.read(1024)  # type: ignore[union-attr]
        process.stdout.close()  # type: ignore[union-attr]
        process.wait(timeout=60)
        stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
        process.stderr.close()  # type: ignore[union-attr]
        self.assertEqual(process.returncode, 141, f"stderr was: {stderr!r}")
        self.assertEqual(stderr, "")

    def test_keyboard_interrupt_exits_130(self) -> None:
        body = textwrap.dedent(
            """
            def main(argv):
                raise KeyboardInterrupt

            raise SystemExit(run_entry(main))
            """
        )
        completed = run_shared_child(body)
        self.assertEqual(completed.returncode, 130, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_missing_file_prints_one_line_and_exits_66(self) -> None:
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-here.png")
        body = textwrap.dedent(
            f"""
            def main(argv):
                open(argv[0], "rb").read()
                return 0

            raise SystemExit(run_entry(main, [{missing!r}]))
            """
        )
        completed = run_shared_child(body)
        self.assertEqual(completed.returncode, 66, completed.stderr)
        self.assertIn("error:", completed.stderr)
        self.assertEqual(len(completed.stderr.strip().splitlines()), 1, "exactly one line")

    def test_real_bugs_keep_their_traceback(self) -> None:
        body = textwrap.dedent(
            """
            def main(argv):
                raise ValueError("a genuine bug")

            raise SystemExit(run_entry(main))
            """
        )
        completed = run_shared_child(body)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("ValueError: a genuine bug", completed.stderr)

    def test_vendored_copy_without_shared_runtime_runs_bare(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vendored = Path(directory) / "entry.py"
            # A copy that cannot find forge/_shared/cli_run.py must still run main.
            vendored.write_text(
                textwrap.dedent(
                    """
                    import sys

                    try:
                        sys.path.insert(0, str(next(
                            parent / "forge" / "_shared"
                            for parent in __import__("pathlib").Path(__file__).resolve().parents
                            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
                        )))
                        from cli_run import run_entry
                    except (ImportError, StopIteration):
                        def run_entry(main_fn, argv=None):
                            return main_fn(sys.argv[1:] if argv is None else argv)

                    def main(argv):
                        print("bare run ok")
                        return 0

                    raise SystemExit(run_entry(main))
                    """
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(vendored)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=directory,  # nowhere up-tree carries forge/_shared
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("bare run ok", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
