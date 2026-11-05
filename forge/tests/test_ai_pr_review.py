from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.ai_pr_review import (
    build_context,
    build_index,
    changed_symbols,
    normalize_review,
    parse_patch_lines,
    safe_json,
)


class AiPrReviewTests(unittest.TestCase):
    def test_parse_patch_lines_uses_new_file_coordinates(self) -> None:
        patch = "@@ -1,2 +4,3 @@\n old\n+added\n context\n"
        self.assertEqual(parse_patch_lines(patch), {5})

    def test_changed_symbols_returns_enclosing_function(self) -> None:
        source = "def first():\n    return 1\n\ndef second():\n    return 2\n"
        patch = "@@ -3,2 +3,2 @@\n\n-def second():\n+def second():\n"
        symbols = changed_symbols("example.py", source, patch)
        self.assertEqual([symbol.name for symbol in symbols], ["second"])

    def test_safe_json_accepts_fenced_output(self) -> None:
        self.assertEqual(safe_json("```json\n{\"findings\": []}\n```"), {"findings": []})

    def test_normalize_review_discards_malformed_findings(self) -> None:
        result = normalize_review(
            {
                "summary": "one",
                "findings": [
                    {"path": "a.py", "line": 4, "severity": "HIGH", "confidence": 1.2},
                    "not an object",
                ],
            }
        )
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["severity"], "high")
        self.assertEqual(result["findings"][0]["confidence"], 1.0)

    def test_context_contains_policy_and_pr_and_related_base_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "CLAUDE.md").write_text("trusted policy", encoding="utf-8")
            (workspace / "CONTRIBUTING.md").write_text("contribution policy", encoding="utf-8")
            (workspace / "docs").mkdir()
            (workspace / "docs" / "ARCHITECTURE.md").write_text("architecture", encoding="utf-8")
            (workspace / "module.py").write_text("def changed():\n    return 1\n", encoding="utf-8")
            (workspace / "test_module.py").write_text("from module import changed\n", encoding="utf-8")
            index = build_index(workspace)
            changed = [{"filename": "module.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@"}]
            context, metadata = build_context(
                workspace,
                index,
                changed,
                {"module.py": "def changed():\n    return 2\n"},
                "base",
                "head",
            )
        self.assertIn("TRUSTED POLICY: CLAUDE.md", context)
        self.assertIn("PR FILE (UNTRUSTED): module.py", context)
        self.assertIn("test_module.py", metadata["related_files"])


if __name__ == "__main__":
    unittest.main()
