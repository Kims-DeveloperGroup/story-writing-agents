"""Current documentation-link and production-isolation conformance checks."""

from __future__ import annotations

import ast
import re
import unittest
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit


_ROOT = Path(__file__).resolve().parents[1]


def _iter_markdown_links(text: str) -> Iterator[tuple[int, str]]:
    """Yield prose Markdown destinations while excluding fenced examples."""

    fenced = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in re.finditer(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", line):
            yield line_number, match.group(1).strip("<>")


def _github_heading_text(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"[^\w\- ]", "", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def _markdown_anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    anchors = set(re.findall(r"<(?:a|span)\s+(?:name|id)=[\"']([^\"']+)[\"']", text))
    occurrences: dict[str, int] = {}
    fenced = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match is None:
            continue
        base = _github_heading_text(match.group(1))
        if not base:
            continue
        occurrence = occurrences.get(base, 0)
        occurrences[base] = occurrence + 1
        anchors.add(base if occurrence == 0 else f"{base}-{occurrence}")
    return anchors


def _resolve_markdown_destination(source: Path, destination: str) -> tuple[Path, str]:
    parsed = urlsplit(destination)
    relative_path = unquote(parsed.path)
    if relative_path.startswith("/"):
        target = _ROOT / relative_path.lstrip("/")
    elif relative_path:
        target = source.parent / relative_path
    else:
        target = source
    target = target.resolve()
    try:
        target.relative_to(_ROOT)
    except ValueError as exc:
        raise AssertionError(
            f"{source.relative_to(_ROOT)} links outside the repository: {destination}"
        ) from exc
    return target, unquote(parsed.fragment)


class AgentContextConformanceTests(unittest.TestCase):
    def test_changed_document_links_and_fragments_resolve(self) -> None:
        documents = (
            _ROOT / "README.md",
            _ROOT / "PYTHON_MODULE_INDEX.md",
            *sorted((_ROOT / "docs").rglob("*.md")),
        )
        checked_links = 0
        for source in documents:
            self.assertTrue(source.is_file(), source)
            for line_number, destination in _iter_markdown_links(
                source.read_text(encoding="utf-8")
            ):
                parsed = urlsplit(destination)
                if parsed.scheme or destination.startswith("//"):
                    continue
                target, fragment = _resolve_markdown_destination(source, destination)
                self.assertTrue(
                    target.exists(),
                    f"{source.relative_to(_ROOT)}:{line_number}: missing {destination}",
                )
                if fragment and target.suffix.lower() == ".md":
                    self.assertIn(
                        fragment,
                        _markdown_anchors(target),
                        f"{source.relative_to(_ROOT)}:{line_number}: "
                        f"missing fragment {destination}",
                    )
                checked_links += 1
        self.assertGreaterEqual(checked_links, 50)

    def test_current_integration_ownership_is_explicit(self) -> None:
        overview = (_ROOT / "docs/design/overview.md").read_text(encoding="utf-8")
        self.assertIn(
            "Conversation management uses one startup-selected complete service bundle:",
            overview,
        )
        self.assertIn("The controller uses that schema-v2 service for", overview)
        self.assertIn(
            "#452 owns the later lead proposal/confirmation/reconfirmation workflow",
            overview,
        )
        self.assertIn(
            "#453 owns specialist execution/result/finalization/outcome-unknown",
            overview,
        )
        self.assertIn(
            "#454 owns whole-workspace rejection of unsupported history",
            overview,
        )

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
                    any(
                        name == "tests" or name.startswith("tests.") for name in imports
                    )
                )
                self.assertFalse(
                    any(
                        marker in literal
                        for literal in string_literals
                        for marker in forbidden_fragments
                    )
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
