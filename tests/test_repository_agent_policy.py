"""Fast static contracts for repository-development agent policy."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_PATH = _REPOSITORY_ROOT / "AGENTS.md"
_RUNBOOK_PATH = _REPOSITORY_ROOT / "AGENT_TASK_RUNBOOK.md"
_AGENT_DIRECTORY = _REPOSITORY_ROOT / ".codex" / "agents"
_EXPECTED_AGENTS = {
    "documentation_maintainer",
    "modularity_maintainer",
    "regression_diagnostician",
    "requirements_analyst",
    "runtime_implementer",
    "test_engineer",
}


def _markdown_section(source: str, heading: str) -> str:
    """Return one Markdown section through the next peer or parent heading."""

    heading_match = re.fullmatch(r"(#{1,6})\s+(.+)", heading)
    if heading_match is None:
        raise ValueError(f"invalid Markdown heading: {heading!r}")
    level = len(heading_match.group(1))
    lines = source.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError as error:
        raise AssertionError(f"missing Markdown heading: {heading}") from error
    end = len(lines)
    for index in range(start, len(lines)):
        candidate = re.match(r"^(#{1,6})\s+", lines[index])
        if candidate is not None and len(candidate.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def _normalized(source: str) -> str:
    """Collapse insignificant formatting whitespace for semantic assertions."""

    return " ".join(source.split())


class RepositoryAgentPolicyTests(unittest.TestCase):
    """Prevent drift in the prompted root/subagent verification contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.agents_source = _AGENTS_PATH.read_text(encoding="utf-8")
        cls.adaptive_source = _markdown_section(
            cls.agents_source,
            "### Adaptive Verification",
        )
        cls.adaptive_normalized = _normalized(cls.adaptive_source)
        cls.handoff_source = _markdown_section(
            cls.agents_source,
            "### Handoff and Result Contracts",
        )
        cls.runbook_source = _RUNBOOK_PATH.read_text(encoding="utf-8")
        cls.agent_paths = sorted(_AGENT_DIRECTORY.glob("*.toml"))
        cls.agent_data = {
            path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
            for path in cls.agent_paths
        }

    def assertContainsAll(self, source: str, fragments: tuple[str, ...]) -> None:
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                if fragment not in source:
                    self.fail(f"missing semantic contract: {fragment}")

    def test_agent_roster_parses_and_names_match(self) -> None:
        self.assertEqual(set(self.agent_data), _EXPECTED_AGENTS)
        for path in self.agent_paths:
            with self.subTest(path=path.name):
                definition = self.agent_data[path.stem]
                self.assertEqual(definition["name"], path.stem)
                self.assertTrue(definition["description"].strip())
                self.assertTrue(definition["developer_instructions"].strip())
        self.assertNotIn("reviewer", self.agent_data)

    def test_existing_role_and_mode_boundaries_are_preserved(self) -> None:
        self.assertEqual(
            self.agent_data["requirements_analyst"].get("sandbox_mode"),
            "read-only",
        )
        self.assertEqual(
            self.agent_data["regression_diagnostician"].get("sandbox_mode"),
            "read-only",
        )
        expected_fragments = {
            "requirements_analyst": (
                "Stay in analysis mode and do not modify files.",
                "Do not design the final module boundary",
            ),
            "regression_diagnostician": (
                "Stay read-only.",
                "Do not apply a fix.",
            ),
            "modularity_maintainer": (
                "`analysis`: Stay read-only.",
                "`implementation`: Require an accepted modularity brief",
                "`review`: Stay read-only.",
                "module index",
            ),
            "runtime_implementer": (
                "Require `mode = implementation`.",
                "Do not edit project module indices",
                "Do not edit files assigned to another writer.",
            ),
            "test_engineer": (
                "In `analysis` mode, stay read-only",
                "In `implementation` mode, edit only assigned test files.",
                "Do not modify production code to make a test pass",
                "Do not edit module indices",
            ),
            "documentation_maintainer": (
                "In `analysis` mode, stay read-only",
                "In `implementation` mode, edit only assigned",
                "do not modify production code or tests",
                "Do not edit project module indices",
            ),
        }
        for name, fragments in expected_fragments.items():
            instructions = _normalized(
                self.agent_data[name]["developer_instructions"]
            )
            with self.subTest(agent=name):
                for fragment in fragments:
                    self.assertIn(fragment, instructions)

    def test_role_specific_adaptive_contracts_are_present(self) -> None:
        expected_fragments = {
            "requirements_analyst": (
                "`changed_inputs`",
                "`local`, `bounded`, `cross_cutting`, or `release`",
                "`mandatory_evidence`",
                "`estimated_seconds`",
                "do not authorize or launch broad validation",
            ),
            "regression_diagnostician": (
                "stable failure-family fingerprint",
                "two or more distinct responsibility holders",
                "inventory completeness",
                "`estimated_seconds`",
                "Do not launch a broad suite",
            ),
            "modularity_maintainer": (
                "recurring failure alone does not create a modularity trigger",
                "all known responsibility holders and consumers",
                "`changed_inputs`",
                "Do not independently broaden an authorized evidence level",
            ),
            "runtime_implementer": (
                "repository-wide occurrence checklist",
                "`changed_inputs`",
                "`authorized_selection`",
                "do not launch an unauthorized broad suite",
            ),
            "test_engineer": (
                "root-supplied adaptive verification plan",
                "`authorized_selection`",
                "deterministic, disjoint exact-ID shards",
                "`relevant_input_digest`",
                "Reject autonomous evidence-level, selection, concurrency, or budget expansion.",
            ),
            "documentation_maintainer": (
                "keep `AGENTS.md` as the canonical contract",
                "evidence catalog, budgets, objective full-suite triggers, reuse rules",
                "Do not invent or independently broaden an evidence level",
            ),
        }
        for name, fragments in expected_fragments.items():
            instructions = _normalized(
                self.agent_data[name]["developer_instructions"]
            )
            with self.subTest(agent=name):
                for fragment in fragments:
                    self.assertIn(fragment, instructions)

    def test_evidence_levels_are_selectable_and_complete_is_terminal(self) -> None:
        self.assertContainsAll(
            self.adaptive_source,
            tuple(
                f"`{name}`"
                for name in (
                    "static_inventory",
                    "exact",
                    "semantic_cluster",
                    "affected_suite",
                    "full_suite",
                    "complete",
                )
            ),
        )
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "partial order of selectable evidence, not a mandatory ladder",
                "`complete` is a terminal state, not a sixth required evidence step",
                "It may follow any selected level",
                "Passing one level neither requires nor authorizes the next",
            ),
        )

    def test_verification_class_budgets_and_shard_limits_are_exact(self) -> None:
        rows = {
            match.group("name"): (match.group("budget"), match.group("shard"))
            for match in re.finditer(
                r"^\|\s*`(?P<name>[a-z_]+)`\s*"
                r"\|\s*(?P<budget>[\w-]+)\s*"
                r"\|\s*(?P<shard>[\w-]+)\s*\|",
                self.adaptive_source,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            rows,
            {
                "local": ("300", "180"),
                "bounded": ("900", "300"),
                "cross_cutting": ("1800", "600"),
                "release": ("Issue-defined", "Issue-defined"),
            },
        )

    def test_root_plan_and_result_fields_are_identifiable(self) -> None:
        plan_source = self.adaptive_source.split(
            "Incomplete dependency knowledge",
            maxsplit=1,
        )[0]
        self.assertContainsAll(
            plan_source,
            tuple(
                f"`{field}`"
                for field in (
                    "verification_class",
                    "mandatory_evidence",
                    "changed_inputs",
                    "authorized_selection",
                    "estimated_seconds",
                    "estimate_source",
                    "estimate_confidence",
                    "total_stage_budget_seconds",
                    "max_shard_seconds",
                    "spent_seconds",
                    "remaining_seconds",
                )
            ),
        )
        self.assertContainsAll(
            _normalized(plan_source),
            (
                "risk rationale",
                "selection rationale",
                "escalation and full-suite triggers",
                "stop conditions",
                "invalidation analysis",
                "residual risk",
            ),
        )

        result_source = "\n".join(
            (
                self.adaptive_source.split(
                    "For provenance, every result records",
                    maxsplit=1,
                )[1],
                self.handoff_source,
            )
        )
        self.assertContainsAll(
            _normalized(result_source),
            (
                "repository, absolute worktree, baseline and HEAD SHAs",
                "dirty-state fingerprint",
                "relevant environment capabilities",
                "purpose, evidence level, exact selection and command",
                "outcome, actual duration",
                "stable failure fingerprint",
                "spent and remaining budget",
                "residual risk",
                "recommended next evidence",
            ),
        )
        self.assertContainsAll(
            result_source,
            tuple(
                f"`{field}`"
                for field in (
                    "relevant_input_digest",
                    "mandatory_evidence",
                    "completed_ids",
                    "failed_ids",
                    "accepted_skips",
                    "unexecuted_ids",
                    "authorized_selection",
                )
            ),
        )

    def test_sha_is_provenance_but_digest_controls_reuse(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "For provenance, every result records repository",
                "Whole-repository identity does not by itself decide reuse",
                "`relevant_input_digest` covering its selected tests",
                "only when the main agent proves the later change is disjoint",
                "digest and purpose remain unchanged",
                "Any relevant or uncertain input or dependency change invalidates it",
                "renaming the purpose does not create distinct evidence",
            ),
        )

    def test_shards_are_disjoint_complete_and_resumable(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "deterministic, disjoint shards of exact portable test IDs",
                "shard union must equal the authorized selection",
                "each ID must occur exactly once",
                "manifest digest, ordinal, exact IDs",
                "Aggregate success requires valid green or accepted-skip evidence for every authorized ID",
                "timeout, user replacement, or interruption is neither a pass nor an assertion failure",
                "preserve `completed_ids`, `failed_ids`, `accepted_skips`, and `unexecuted_ids`",
                "resume only unexecuted or invalidated IDs whose inputs remain valid",
                "Partial evidence never establishes `complete`",
            ),
        )

    def test_root_alone_controls_escalation_and_risk_acceptance(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "main agent owns verification selection, authorization, budgeting, evidence reuse, escalation, and the final sufficiency decision",
                "must not independently broaden an authorized evidence level, selection, concurrency, or budget",
                "main agent explicitly accepts the recorded residual risk",
                "parallel execution requires separate main-agent authorization",
            ),
        )
        test_instructions = _normalized(
            self.agent_data["test_engineer"]["developer_instructions"]
        )
        self.assertContainsAll(
            test_instructions,
            (
                "Reject autonomous evidence-level, selection, concurrency, or budget expansion",
                "parallel shards require explicit root authorization",
                "Do not launch `affected_suite` or `full_suite` without the root's exact authorization",
                "final risk acceptance by the main agent",
            ),
        )

    def test_full_suite_has_objective_triggers_and_no_default_gate(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "Issue, parent integration criterion, or release gate explicitly requires it",
                "static inventory cannot conservatively bound the affected set",
                "package-wide, public, or cross-domain invariant changed",
                "global discovery, bootstrap, packaging, or test-harness behavior",
                "unknown out-of-cluster failure family",
                "concrete residual risk cannot be covered by affected selections",
                "Tracked implementation, final review, or the existence of `full_suite` is not a trigger",
                "do not silently omit it or claim completion",
                "Prompt, TOML, policy, or documentation-only work may complete with static parsing, exact policy tests, link or formatting checks, and diff inspection",
            ),
        )

    def test_failures_route_without_restarting_broad_verification(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "For a known failure family, repair it and rerun the exact invalidated evidence",
                "same stable fingerprint appears in two or more distinct responsibility holders",
                "stop broad reruns and perform one repository-wide occurrence inventory",
                "cross-boundary ownership or capability defect, apply the existing modularity gate once across all holders and consumers",
                "unknown failure, run `regression_diagnostician` instead of launching broad validation",
                "cost is unknown or budget is exhausted, return completed and mandatory remaining evidence",
                "revised estimates, residual risk, and root decision alternatives",
            ),
        )

    def test_policy_does_not_claim_capability_level_sandbox_enforcement(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "Modes and read-only requirements declared in prompts or TOML are behavioral policy",
                "do not prove capability-level sandbox enforcement",
                "actual effective sandbox and approval capabilities as separate runtime evidence",
            ),
        )

    def test_runbook_applies_the_canonical_adaptive_policy(self) -> None:
        normalized = _normalized(self.runbook_source)
        self.assertContainsAll(
            normalized,
            (
                "AGENTS.md#adaptive-verification",
                "`authorized_selection`",
                "`estimated_seconds`",
                "`max_shard_seconds`",
                "`remaining_seconds`",
                "deterministic, disjoint exact-ID shards",
                "`relevant_input_digest`",
                "`completed_ids`",
                "`unexecuted_ids`",
                "interruption",
                "objective full-suite trigger",
                "mandatory evidence",
                "residual risk",
                "`complete`",
            ),
        )


if __name__ == "__main__":
    unittest.main()
