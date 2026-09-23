"""Production isolation from benchmark-only test fixtures."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


class AgentContextConformanceTests(unittest.TestCase):
    def test_production_has_no_benchmark_fixture_import_or_switch(self) -> None:
        forbidden_fragments = (
            "context_accounting_baseline_fixture",
            "context_accounting_optimized_fixture",
            "provider_context_baseline_v1",
            "provider_context_optimized_v1",
        )
        for source in (_ROOT / "src/story_writing_agents").rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imports: list[str] = []
            string_literals: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    string_literals.append(node.value)
            with self.subTest(source=source.relative_to(_ROOT)):
                self.assertFalse(
                    any(name == "tests" or name.startswith("tests.") for name in imports)
                )
                self.assertFalse(
                    any(marker in literal for literal in string_literals for marker in forbidden_fragments)
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
