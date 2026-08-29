"""Executable conformance checks for the #380/#387 release audit."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit


_ROOT = Path(__file__).resolve().parents[1]
_AUDIT_PATH = _ROOT / "docs/audits/issue-380-agent-context-conformance.md"
_BASELINE_PATH = (
    _ROOT / "tests/fixtures/agent_context/provider_context_baseline_v1.json"
)
_OPTIMIZED_PATH = (
    _ROOT / "tests/fixtures/agent_context/provider_context_optimized_v1.json"
)
_PRODUCTION_SHA = "d9b7721b61af68165e16b8f1ff35682a726e34f1"
_RAW_AND_CANONICAL_HASHES = {
    "baseline": (
        "c8b3737b97e33fa90d825b4ca41cfda6b3b348d62401689bdfd90dbbd7b214f4",
        "c1b5e26bc73f04cda0e004fff4fcd95bb4e02b9d0ea75a229310dcb3e6ad1392",
    ),
    "optimized": (
        "20b2a2a11028a17fb35e6be0ce8e8107f5166a40912234b367857f9fe63b1a5b",
        "59202c6c617db3c804312db4dea264fb384f1a58697ff19273deb4df4fde427f",
    ),
}
_SCENARIOS = (
    ("fresh ordinary conversation", "fresh_ordinary_conversation"),
    ("continued ordinary conversation", "continued_ordinary_conversation"),
    ("retrieval direct answer", "retrieval_direct_answer"),
    ("retrieval SEARCH/READ", "retrieval_search_read_answer"),
    ("whole-file reference", "whole_file_reference"),
    ("ranged reference", "ranged_file_reference"),
    ("multiple references", "multiple_ordered_references"),
    ("straight consensus", "straight_consensus_debate"),
    ("conflict/revision", "conflict_revision_consensus_debate"),
    ("terminal deadlock", "terminal_deadlock_debate"),
    ("outline modification", "outline_modification"),
    ("character-sheet modification", "character_sheet_modification"),
    ("narrative-rule modification", "narrative_rule_modification"),
    ("create-only manuscript revision", "create_only_manuscript_revision"),
)
_ACTION_TOTALS = {
    "provider_calls": 56,
    "reference_read_batches": 3,
    "ordinary_conversation_calls": 5,
    "retrieval_search_actions": 1,
    "retrieval_transcript_read_actions": 1,
    "retrieval_answer_actions": 2,
    "debate_draft_actions": 7,
    "debate_audit_actions": 27,
    "debate_proposal_actions": 2,
    "debate_synthesis_actions": 2,
    "debate_finalize_actions": 6,
}
_CHANGED_DOCUMENTS = (
    "PYTHON_MODULE_INDEX.md",
    "README.md",
    "docs/INDEX.md",
    "docs/audits/issue-380-agent-context-conformance.md",
    "docs/concepts.md",
    "docs/design/agent-context.md",
    "docs/design/consensus-workflow.md",
    "docs/design/overview.md",
    "docs/design/prompts-and-provider.md",
    "docs/design/session-lifecycle.md",
    "docs/glossary.md",
    "docs/guides/agent-context-progress.md",
    "docs/guides/agent-context.md",
    "docs/guides/antigravity.md",
    "docs/guides/consensus-runs.md",
    "docs/guides/inspection-and-recovery.md",
    "docs/guides/story-sessions.md",
    "docs/guides/workspaces.md",
    "docs/operations/privacy-and-provider.md",
    "docs/operations/recovery-and-limitations.md",
    "docs/reference/agent-contracts.md",
    "docs/reference/backend-and-sessions.md",
    "docs/reference/configuration/agent-configuration.md",
    "docs/reference/conversation-retrieval.md",
    "docs/reference/package-api.md",
    "docs/reference/runtime-api.md",
    "docs/reference/writing-artifacts.md",
)


def _expected_criterion_ids() -> tuple[str, ...]:
    child_counts = (
        (381, 11),
        (382, 13),
        (383, 12),
        (384, 14),
        (385, 13),
        (386, 13),
        (402, 35),
        (405, 15),
        (387, 24),
    )
    return (
        *(f"C{index:02d}" for index in range(1, 21)),
        *(f"P{index:02d}" for index in range(1, 19)),
        *(
            f"{issue}-AC{index:02d}"
            for issue, count in child_counts
            for index in range(1, count + 1)
        ),
    )


def _split_markdown_row(line: str) -> tuple[str, ...]:
    r"""Split one pipe table row without treating ``\|`` as a delimiter."""

    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValueError(f"not a complete Markdown table row: {line!r}")
    cells: list[str] = []
    current: list[str] = []
    index = 1
    end = len(stripped) - 1
    while index < end:
        character = stripped[index]
        if character == "\\" and index + 1 < end:
            current.extend((character, stripped[index + 1]))
            index += 2
            continue
        if character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    cells.append("".join(current).strip())
    return tuple(cells)


def _table(
    text: str,
    header: Sequence[str],
) -> tuple[tuple[str, ...], ...]:
    lines = text.splitlines()
    expected_header = tuple(header)
    for index, line in enumerate(lines):
        if line.startswith("|") and _split_markdown_row(line) == expected_header:
            if index + 1 >= len(lines) or not re.fullmatch(
                r"\|(?:\s*:?-+:?\s*\|)+",
                lines[index + 1],
            ):
                raise AssertionError(f"missing delimiter for table {header!r}")
            rows: list[tuple[str, ...]] = []
            for row_line in lines[index + 2 :]:
                if not row_line.startswith("|"):
                    break
                row = _split_markdown_row(row_line)
                if len(row) != len(expected_header):
                    raise AssertionError(
                        f"table {header!r} has {len(row)} cells in {row_line!r}"
                    )
                rows.append(row)
            return tuple(rows)
    raise AssertionError(f"missing table {header!r}")


def _number(cell: str) -> int:
    return int(cell.replace("*", "").replace(",", "").strip())


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _iter_markdown_links(text: str) -> Iterator[tuple[int, str]]:
    """Yield prose Markdown destinations, excluding fenced examples."""

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
    text = text.lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def _markdown_anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    anchors = set(
        re.findall(r"<(?:a|span)\s+(?:name|id)=[\"']([^\"']+)[\"']", text)
    )
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
    except ValueError as error:
        raise AssertionError(
            f"{source.relative_to(_ROOT)} links outside the repository: {destination}"
        ) from error
    return target, unquote(parsed.fragment)


class AgentContextConformanceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = _AUDIT_PATH.read_text(encoding="utf-8")
        cls.baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
        cls.optimized = json.loads(_OPTIMIZED_PATH.read_text(encoding="utf-8"))

    def test_criterion_matrix_has_exact_order_sha_and_outcomes(self) -> None:
        rows = _table(
            self.audit,
            (
                "ID",
                "exact criterion",
                "production SHA",
                "implementation/docs evidence",
                "deterministic evidence",
                "outcome",
                "remediation/owner",
            ),
        )
        expected_ids = _expected_criterion_ids()
        self.assertEqual(len(rows), 188)
        self.assertEqual(tuple(row[0] for row in rows), expected_ids)
        self.assertEqual(len({row[0] for row in rows}), len(rows))
        self.assertEqual(len({row[1] for row in rows}), len(rows))

        not_applicable: set[str] = set()
        open_rows: set[str] = set()
        for criterion_id, criterion, sha, implementation, evidence, outcome, _ in rows:
            with self.subTest(criterion_id=criterion_id):
                self.assertEqual(criterion, criterion.strip())
                self.assertTrue(criterion.endswith("."))
                self.assertNotRegex(criterion, r"\b(?:TODO|TBD|FIXME)\b")
                self.assertEqual(sha, f"`{_PRODUCTION_SHA}`")
                self.assertTrue(implementation)
                self.assertTrue(evidence)
                if outcome == "proven":
                    continue
                if outcome == "open":
                    open_rows.add(criterion_id)
                    continue
                prefix = "not applicable — "
                self.assertTrue(outcome.startswith(prefix))
                self.assertTrue(outcome.removeprefix(prefix).strip())
                not_applicable.add(criterion_id)

        self.assertEqual(not_applicable, {"P01", "402-AC34"})
        mode_match = re.search(r"> \*\*(PRE-FINAL|FINAL) audit\.\*\*", self.audit)
        self.assertIsNotNone(mode_match)
        mode = mode_match.group(1) if mode_match is not None else ""
        if mode == "PRE-FINAL":
            self.assertEqual(open_rows, {"P18", "387-AC23", "387-AC24"})
            self.assertEqual(
                (
                    sum(row[5] == "proven" for row in rows),
                    len(not_applicable),
                    len(open_rows),
                ),
                (183, 2, 3),
            )
        else:
            self.assertEqual(mode, "FINAL")
            self.assertEqual(open_rows, set())
            self.assertEqual(sum(row[5] == "proven" for row in rows), 186)

    def test_escaped_pipes_do_not_change_criterion_columns(self) -> None:
        self.assertEqual(
            _split_markdown_row(r"| ID | PREPARE → COMMIT\|FAIL | outcome |"),
            ("ID", r"PREPARE → COMMIT\|FAIL", "outcome"),
        )
        rows = _table(
            self.audit,
            (
                "ID",
                "exact criterion",
                "production SHA",
                "implementation/docs evidence",
                "deterministic evidence",
                "outcome",
                "remediation/owner",
            ),
        )
        lifecycle = next(row for row in rows if row[0] == "387-AC15")
        self.assertIn(r"COMMIT\|FAIL", lifecycle[1])

    def test_benchmark_tables_and_hashes_match_immutable_fixtures(self) -> None:
        baseline_scenarios = {
            scenario["scenario_id"]: scenario
            for scenario in self.baseline["scenarios"]
        }
        optimized_scenarios = {
            scenario["scenario_id"]: scenario
            for scenario in self.optimized["scenarios"]
        }
        scenario_ids = tuple(identifier for _, identifier in _SCENARIOS)
        self.assertEqual(tuple(baseline_scenarios), scenario_ids)
        self.assertEqual(tuple(optimized_scenarios), scenario_ids)

        audit_rows = _table(
            self.audit,
            (
                "Scenario",
                "Baseline non-reference bytes",
                "Optimized non-reference bytes",
                "Calls",
                "Raw reference bytes",
            ),
        )
        actual_rows = []
        for display_name, identifier in _SCENARIOS:
            baseline = baseline_scenarios[identifier]
            optimized = optimized_scenarios[identifier]
            self.assertEqual(
                baseline["byte_totals"]["raw_mandatory_reference_utf8_bytes"],
                optimized["byte_totals"]["raw_mandatory_reference_utf8_bytes"],
            )
            actual_rows.append(
                (
                    display_name,
                    baseline["byte_totals"]["non_reference_model_input_utf8_bytes"],
                    optimized["byte_totals"]["non_reference_model_input_utf8_bytes"],
                    optimized["physical_provider_calls"],
                    optimized["byte_totals"]["raw_mandatory_reference_utf8_bytes"],
                )
            )
        parsed_rows = tuple(
            (row[0], *(_number(cell) for cell in row[1:]))
            for row in audit_rows[:-1]
        )
        self.assertEqual(parsed_rows, tuple(actual_rows))
        self.assertEqual(
            tuple(_number(cell) for cell in audit_rows[-1][1:]),
            (180575, 84053, 56, 162),
        )

        component_rows = dict(
            (row[0], (_number(row[1]), _number(row[2])))
            for row in _table(
                self.audit,
                ("Component", "Baseline bytes", "Optimized bytes"),
            )
        )
        component_keys = {
            "binding instructions": "binding_instruction_utf8_bytes",
            "schemas": "schema_utf8_bytes",
            "payload/actual transport": "actual_transport_utf8_bytes",
            "raw mandatory-reference content": "raw_mandatory_reference_utf8_bytes",
            "gross input": "gross_model_input_utf8_bytes",
            "non-reference input": "non_reference_model_input_utf8_bytes",
        }
        self.assertEqual(set(component_rows), set(component_keys))
        for label, key in component_keys.items():
            self.assertEqual(
                component_rows[label],
                (
                    self.baseline["cumulative"]["byte_totals"][key],
                    self.optimized["cumulative"]["byte_totals"][key],
                ),
            )

        for report in (self.baseline, self.optimized):
            self.assertEqual(report["cumulative"]["scenario_count"], 14)
            self.assertEqual(report["cumulative"]["physical_provider_calls"], 56)
            self.assertEqual(report["cumulative"]["action_calls"], _ACTION_TOTALS)
            self.assertEqual(
                report["cumulative"]["byte_totals"]["payload_utf8_bytes"],
                report["cumulative"]["byte_totals"]["actual_transport_utf8_bytes"],
            )
            self.assertEqual(
                report["cumulative"]["controlled_provider_elapsed_ns"],
                257_000_000,
            )
        normalized = " ".join(self.audit.split())
        self.assertIn(
            "Action totals are 56 provider calls, 3 reference batches, 5 ordinary "
            "calls, 1 retrieval SEARCH, 1 retrieval READ, 2 retrieval ANSWER actions, "
            "7 debate drafts, 27 audits, 2 proposals, 2 syntheses, and 6 "
            "finalizations. Controlled provider elapsed time is 257 ms;",
            normalized,
        )

        evidence = dict(
            _table(self.audit, ("Evidence", "Exact value"))
        )
        self.assertEqual(evidence["Production behavior head"], f"`{_PRODUCTION_SHA}`")
        for label, path, key in (
            ("Baseline", _BASELINE_PATH, "baseline"),
            ("Optimized", _OPTIMIZED_PATH, "optimized"),
        ):
            raw_hash, canonical_hash = _RAW_AND_CANONICAL_HASHES[key]
            raw = path.read_bytes()
            report = json.loads(raw.decode("utf-8"))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), raw_hash)
            self.assertEqual(
                hashlib.sha256(_canonical_json_bytes(report)).hexdigest(),
                canonical_hash,
            )
            self.assertEqual(
                re.findall(r"[0-9a-f]{64}", evidence[f"{label} raw/canonical SHA-256"]),
                [raw_hash, canonical_hash],
            )

    def test_audit_evidence_paths_and_test_identifiers_resolve(self) -> None:
        code_spans = set(re.findall(r"`([^`\n]+)`", self.audit))
        path_tokens = {
            token
            for token in code_spans
            if token != ".md"
            and (
                token.endswith((".py", ".md", ".json"))
                or ".md#" in token
            )
        }
        resolved: dict[str, Path] = {}
        for token in path_tokens:
            path_text, _, fragment = token.partition("#")
            if path_text.startswith(("tests/", "docs/", "src/")) or path_text in {
                "README.md",
                "PYTHON_MODULE_INDEX.md",
            }:
                candidate = _ROOT / path_text
            elif path_text.startswith(
                ("design/", "guides/", "operations/", "reference/")
            ):
                candidate = _ROOT / "docs" / path_text
            elif path_text.startswith("agent_context/"):
                candidate = _ROOT / "src/story_writing_agents" / path_text
            elif path_text.startswith("test_") and path_text.endswith(".py"):
                candidate = _ROOT / "tests" / path_text
            elif "/" not in path_text and path_text.endswith(".py"):
                matches = tuple((_ROOT / "src/story_writing_agents").rglob(path_text))
                self.assertEqual(len(matches), 1, token)
                candidate = matches[0]
            else:
                self.fail(f"unclassified local evidence path: {token}")
            self.assertTrue(candidate.is_file(), token)
            if fragment:
                self.assertIn(fragment, _markdown_anchors(candidate), token)
            resolved[token] = candidate
        self.assertIn("agent_context/service.py", resolved)
        self.assertIn("test_agent_context_release_scenarios.py", resolved)
        self.assertIn("design/agent-context.md#compact-provider-wire-v5", resolved)

        declared_tests = {
            token
            for token in code_spans
            if re.fullmatch(r"test_[A-Za-z0-9_]+", token)
        }
        implemented_tests: set[str] = set()
        for test_file in (_ROOT / "tests").glob("test_*.py"):
            tree = ast.parse(
                test_file.read_text(encoding="utf-8"),
                filename=str(test_file),
            )
            implemented_tests.update(
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        self.assertTrue(declared_tests)
        self.assertEqual(declared_tests - implemented_tests, set())

    def test_changed_document_links_and_fragments_resolve(self) -> None:
        checked_links = 0
        for relative in _CHANGED_DOCUMENTS:
            source = _ROOT / relative
            self.assertTrue(source.is_file(), relative)
            for line_number, destination in _iter_markdown_links(
                source.read_text(encoding="utf-8")
            ):
                parsed = urlsplit(destination)
                if parsed.scheme or destination.startswith("//"):
                    continue
                target, fragment = _resolve_markdown_destination(source, destination)
                self.assertTrue(
                    target.exists(),
                    f"{relative}:{line_number}: missing {destination}",
                )
                if fragment and target.suffix.lower() == ".md":
                    self.assertIn(
                        fragment,
                        _markdown_anchors(target),
                        f"{relative}:{line_number}: missing fragment {destination}",
                    )
                checked_links += 1
        self.assertGreaterEqual(checked_links, 300)

    def test_production_has_no_benchmark_fixture_import_or_switch(self) -> None:
        forbidden_strings = {
            _BASELINE_PATH.name,
            _OPTIMIZED_PATH.name,
            *(identifier for _, identifier in _SCENARIOS),
        }
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
                        imported == "tests" or imported.startswith("tests.")
                        for imported in imports
                    )
                )
                self.assertFalse(
                    any("context_accounting_" in imported for imported in imports)
                )
                self.assertEqual(
                    forbidden_strings.intersection(string_literals),
                    set(),
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
