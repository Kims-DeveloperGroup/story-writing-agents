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
        cls.delegation_source = _markdown_section(
            cls.agents_source,
            "### Delegation-Gain Gate",
        )
        cls.delegation_normalized = _normalized(cls.delegation_source)
        cls.routing_source = _markdown_section(
            cls.agents_source,
            "### Root-Capability Routing",
        )
        cls.routing_normalized = _normalized(cls.routing_source)
        cls.orchestration_source = _markdown_section(
            cls.agents_source,
            "### Orchestration Sequence",
        )
        cls.orchestration_normalized = _normalized(cls.orchestration_source)
        cls.adaptive_source = _markdown_section(
            cls.agents_source,
            "### Adaptive Verification",
        )
        cls.adaptive_normalized = _normalized(cls.adaptive_source)
        cls.handoff_source = _markdown_section(
            cls.agents_source,
            "### Handoff and Result Contracts",
        )
        cls.handoff_normalized = _normalized(cls.handoff_source)
        cls.telemetry_source = _markdown_section(
            cls.agents_source,
            "### Prospective Orchestration Telemetry",
        )
        cls.telemetry_normalized = _normalized(cls.telemetry_source)
        cls.runbook_source = _RUNBOOK_PATH.read_text(encoding="utf-8")
        cls.runbook_normalized = _normalized(cls.runbook_source)
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
                self.assertNotIn("model", definition)
                self.assertNotIn("model_reasoning_effort", definition)
                self.assertNotIn("reasoning_effort", definition)
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
                "Support only `mode = analysis`, remain read-only",
                "Do not design the boundary or authorize broad verification",
            ),
            "regression_diagnostician": (
                "Support only `mode = analysis`, remain read-only",
                "Diagnose only; do not fix or design remediation",
            ),
            "modularity_maintainer": (
                "`analysis`: Stay read-only.",
                "`implementation`: Require an accepted modularity brief",
                "`review`: Stay read-only.",
                "module index",
            ),
            "runtime_implementer": (
                "Require `mode = implementation`; otherwise remain read-only",
                "Never edit documentation, policies, guides, examples, tests, or project module indices",
                "honor exclusive files",
            ),
            "test_engineer": (
                "In `analysis` mode, remain read-only",
                "In `implementation` mode, edit only assigned tests.",
                "Never edit production code, documentation, policies, examples, or indices",
            ),
            "documentation_maintainer": (
                "In `analysis` mode, remain read-only",
                "edit only assigned README, documentation, policy, guide, and example files",
                "Never edit production code, tests, or project module indices",
            ),
        }
        for name, fragments in expected_fragments.items():
            instructions = _normalized(
                self.agent_data[name]["developer_instructions"]
            )
            with self.subTest(agent=name):
                for fragment in fragments:
                    self.assertIn(fragment, instructions)

    def test_role_specific_routing_and_canonical_packets_are_present(self) -> None:
        expected_fragments = {
            "requirements_analyst": (
                "run only for substantial ambiguity",
                "Do not design the boundary or authorize broad verification",
                "Recommend the smallest sufficient evidence",
            ),
            "regression_diagnostician": (
                "run only for an unknown failure",
                "stable family fingerprint",
                "never broaden or launch a broad suite",
            ),
            "modularity_maintainer": (
                "recurring failure alone does not create a modularity trigger",
                "all known responsibility holders and consumers",
                "`changed_inputs`",
                "Do not independently broaden an authorized evidence level",
            ),
            "runtime_implementer": (
                "outside the root fast path",
                "Never edit documentation, policies, guides, examples, tests",
                "never broaden or launch an unauthorized broad suite",
            ),
            "test_engineer": (
                "fuse planning, test-only edits, authorized exact execution, and final behavioral/coverage review",
                "resume this same thread after it stabilizes",
                "Never broaden selection, level, concurrency, or budget",
            ),
            "documentation_maintainer": (
                "edit only assigned README, documentation, policy, guide, and example files",
                "Keep `AGENTS.md` canonical for adaptive verification",
                "never broaden verification",
            ),
        }
        for name, fragments in expected_fragments.items():
            instructions = _normalized(
                self.agent_data[name]["developer_instructions"]
            )
            with self.subTest(agent=name):
                self.assertIn("`task_packet`", instructions)
                self.assertIn("`result_packet`", instructions)
                self.assertIn(
                    "Do not delegate to or coordinate with",
                    instructions,
                )
                for fragment in fragments:
                    self.assertIn(fragment, instructions)

    def test_delegation_gain_gate_and_root_fast_path_are_exact(self) -> None:
        self.assertContainsAll(
            self.delegation_normalized,
            (
                "`predicted_critical_path_savings_seconds = root_sequential_estimate - (parallel_critical_path + spawn_wait_handoff_synthesis_overhead)`",
                "predicted savings are at least 20 seconds **and** at least 20 percent of `root_sequential_estimate`",
                "named `material_correctness_risk_reduction` or `mandatory_ownership_rule`",
                "Mandatory ownership applies to documentation, tests, triggered modularity work, and production work outside the root fast path",
                "directly implement only a clear, localized change limited to one or two production-code or configuration files inside an established module responsibility and boundary",
                "root fast path excludes documentation, policies, guides, examples, tests, required or uncertain modularity work, substantial or unresolved ambiguity, and unknown failures",
                "Dispatch `requirements_analyst` only for substantial ambiguity",
                "`regression_diagnostician` only for an unknown failure",
                "`modularity_maintainer` only when the existing Modularity Gate applies",
                "Do not create a subagent merely to run a deterministic command",
                "batch safe, independent static reads through its tools",
            ),
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "Dispatch only when both canonical gain thresholds pass",
                "root fast path only for a clear one- or two-file production-code or configuration change within an established boundary",
                "batch safe independent static reads in the main agent's tool calls",
            ),
        )

    def test_root_capability_context_and_complexity_are_canonical(self) -> None:
        routing_with_heading = (
            "### Root-Capability Routing\n" + self.routing_source
        )
        self.assertLessEqual(len(routing_with_heading.split()), 407)
        context_rows = {
            match.group("name"): match.group("material").strip()
            for match in re.finditer(
                r"^\|\s*`(?P<name>compact|moderate|large|oversized)`\s*"
                r"\|\s*(?P<material>.*?)\s*\|$",
                self.routing_source,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            context_rows,
            {
                "compact": "<=8,192",
                "moderate": "8,193--32,768",
                "large": "32,769--131,072",
                "oversized": (
                    ">131,072, or cannot fit verified output/reasoning reserve"
                ),
            },
        )
        self.assertContainsAll(
            self.routing_normalized,
            (
                "`dispatch_context_tokens` counts only `task_packet`",
                "intentional inherited turns, attachments/preloads, and irreducible child material",
                "`observed|estimated|unavailable`, method, and confidence",
                "Unknown/unverified fit is `oversized`; narrow/split",
                "Context never relaxes root ceilings",
                "Classify complexity/risk separately",
            ),
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "[Root-Capability Routing](AGENTS.md#root-capability-routing)",
                "measure and classify only the child's dispatch context",
                "classify complexity/risk and role needs",
                "Narrow or split oversized context before dispatch",
            ),
        )

    def test_root_capability_quality_and_role_targets_are_canonical(self) -> None:
        quality_rows = {
            match.group("name"): (
                match.group("trigger").strip(),
                match.group("model").strip(),
                match.group("effort").strip(),
            )
            for match in re.finditer(
                r"^\|\s*`(?P<name>routine|standard|complex|critical)`\s*"
                r"\|\s*(?P<trigger>.*?)\s*"
                r"\|\s*(?P<model>.*?)\s*"
                r"\|\s*(?P<effort>.*?)\s*\|$",
                self.routing_source,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            quality_rows,
            {
                "routine": (
                    "literal/mechanical/reversible; no unresolved judgment/material risk",
                    "`gpt-5.6-terra`",
                    "`medium`",
                ),
                "standard": (
                    "bounded semantic work; one established responsibility",
                    "`gpt-5.6-sol`",
                    "`high`",
                ),
                "complex": (
                    "substantial ambiguity/unknown cause; multi-domain synthesis; boundary/public API; compatibility/migration; security/authorization; concurrency/process/PTY; lifecycle/persistence/resource ownership; difficult nonlocal edges",
                    "`gpt-5.6-sol`",
                    "`xhigh`",
                ),
                "critical": (
                    "two interacting complex triggers; explicitly high-consequence security/data-loss/irreversible-migration/breaking-compatibility",
                    "`gpt-5.6-sol`",
                    "`max`",
                ),
            },
        )
        role_source = self.routing_source.split(
            "Role defaults `routine` and only raises task class:",
            maxsplit=1,
        )[1].split("Context affects only model:", maxsplit=1)[0]
        role_rows = {
            match.group("role").strip(): match.group("uplift").strip()
            for match in re.finditer(
                r"^\|\s*(?P<role>`[^|]+?)\s*"
                r"\|\s*(?P<uplift>.*?)\s*\|$",
                role_source,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            role_rows,
            {
                "`requirements_analyst`/`regression_diagnostician`": (
                    "substantial ambiguity/unknown causal failure"
                ),
                "`modularity_maintainer`": (
                    "boundary work; accepted index-only keeps actual class"
                ),
                "`runtime_implementer`": (
                    "security/concurrency/migration/public API/difficult nonlocal edges"
                ),
                "`test_engineer`": (
                    "security/concurrency/PTY/process/lifecycle/complex-risk coverage"
                ),
                "`documentation_maintainer`": (
                    "normative governance/security/compatibility/migration policy"
                ),
            },
        )
        self.assertContainsAll(
            self.routing_normalized,
            (
                "Role defaults `routine` and only raises task class",
                "Context affects only model: compact/moderate floor Luna; large raises to Sol",
                "Complexity/risk controls effort",
            ),
        )

    def test_root_capability_clamps_and_orders_each_axis_independently(self) -> None:
        self.assertContainsAll(
            self.routing_source,
            (
                "desired_model = max(task_model_target, role_model_target, context_model_target)",
                "desired_effort = max(task_effort_target, role_effort_target)",
                "capped_model = min(desired_model, root_effective_model)",
                "capped_effort = min(desired_effort, root_effective_effort)",
            ),
        )
        self.assertContainsAll(
            self.routing_normalized,
            (
                "Derive/clamp independently",
                "Only `gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol` is initially comparable",
                "never assume cross-family/alias/future order",
                "Portable effort: `none < low < medium < high < xhigh < max`",
                "selected child model/effort independently stay <=root",
            ),
        )
        self.assertRegex(
            self.routing_source,
            r"`ultra` applies only when runtime-\s*exposed as comparable/selectable",
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "derive and clamp each axis independently",
                "never guess a cross-family order",
                "exceed either effective root value",
            ),
        )

    def test_root_capability_selection_and_inheritance_are_canonical(self) -> None:
        self.assertContainsAll(
            self.routing_normalized,
            (
                "Capped is minimum quality",
                "Choose exact cap, else nearest stronger compatible pair within root",
                "fewest upward steps; tie: smaller model then effort increase",
                "Never auto-select below cap for availability/cost; weaker-only returns to root",
                "Set both overrides or neither",
                "Unresolved root/order/availability/support means omit both for exact inheritance",
                "model-only may default effort",
                "`ceiling_limited` means either cap is below desired",
                "Non-high-consequence may proceed compatibly only with recorded residual risk",
                "high-consequence must narrow or require stronger root",
            ),
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "treat the capped pair as the preferred minimum-quality target within the effective root ceiling",
                "Supply both child overrides atomically or omit both for exact inheritance",
                "select a weaker pair automatically",
                "If high-consequence work is ceiling-limited, stop for a narrower task or stronger root session",
            ),
        )

    def test_root_capability_routing_evidence_is_complete(self) -> None:
        routing_without_whitespace = re.sub(r"\s+", "", self.routing_source)
        self.assertContainsAll(
            routing_without_whitespace,
            (
                "Recordroot/source",
                "contextcount/class/method/confidence",
                "complexity/triggers",
                "roletarget",
                "desired/capped/selected/compatiblepairs",
                "`ceiling_limited`",
                "fallback/inheritancereason",
                "Packetsstaynarrow",
                "exclusions",
                "accepteddecisions",
                "observablecriteria",
                "`observed|inferred|proposed|unknown`evidence",
                "uncertaintystops",
            ),
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "record all routing inputs, desired/capped/selected pairs, availability, limitation, and fallback or inheritance evidence in the canonical packet",
                "Send the canonical compact `task_packet`",
            ),
        )
        self.assertNotIn(
            "Use these dynamic model and reasoning-effort recommendations per dispatch",
            self.delegation_source,
        )
        self.assertNotIn(
            "| Agent | Default | Escalation |",
            self.delegation_source,
        )

    def test_specialist_ownership_fusion_and_thread_reuse_are_explicit(self) -> None:
        self.assertContainsAll(
            self.orchestration_normalized,
            (
                "`test_engineer` exclusively owns test work",
                "fuse test planning, test edits, exact execution, and final behavioral and coverage review into one `implementation` turn",
                "`documentation_maintainer` exclusively owns all documentation, policy, guide, and example writes",
                "Reuse the same specialist thread for follow-up phases unless its context is stale or reuse creates an isolation risk",
            ),
        )
        runtime = _normalized(
            self.agent_data["runtime_implementer"]["developer_instructions"]
        )
        self.assertIn(
            "Never edit documentation, policies, guides, examples, tests, or project module indices",
            runtime,
        )
        self.assertNotIn("reviewer", self.agent_data)

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

    def test_evidence_has_one_owner_and_an_exact_deduplication_key(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "Assign exactly one `evidence_execution_owner` to every evidence item before launch",
                "Deduplicate evidence on exactly these key fields: `normalized_command`, `exact_ids`, `relevant_input_digest`, and `environment_class`",
                "A different owner, label, purpose, venue, or wrapper does not justify an equivalent rerun",
                "reserve `affected_suite` and `full_suite` for CI",
                "Do not run equivalent broad evidence both locally and in CI unless the local environment is itself an acceptance criterion",
            ),
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "one `evidence_execution_owner` per evidence item",
                "deduplicate every item on exactly `normalized_command`, `exact_ids`, `relevant_input_digest`, and `environment_class`",
                "do not duplicate equivalent broad local and CI runs unless a local-environment acceptance criterion requires both",
            ),
        )

    def test_shards_are_disjoint_complete_and_resumable(self) -> None:
        self.assertContainsAll(
            self.adaptive_normalized,
            (
                "deterministic, disjoint shards of exact portable test IDs",
                "shard union must equal the authorized selection",
                "each ID must occur exactly once",
                "parallel execution requires separate main-agent authorization, isolated resources, nonoverlapping IDs, and runner support",
                "At most two fast-fail shards may run concurrently",
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
                "Execute only evidence assigned to this role using the root plan",
                "Never broaden selection, level, concurrency, or budget",
                "never launch an unauthorized affected or full suite",
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

    def test_packet_schemas_are_exact_and_compact(self) -> None:
        task_schema, result_and_after = self.handoff_source.split(
            "Every specialist returns a compact `result_packet`",
            maxsplit=1,
        )
        result_schema = result_and_after.split(
            "Keep each packet limited",
            maxsplit=1,
        )[0]
        self.assertEqual(
            tuple(re.findall(r"^- `([^`]+)`:", task_schema, re.MULTILINE)),
            (
                "mode",
                "objective",
                "scope",
                "acceptance",
                "ownership",
                "gates",
                "identity",
                "verification",
                "upstream_decisions",
                "stop_conditions",
            ),
        )
        self.assertEqual(
            tuple(re.findall(r"^- `([^`]+)`:", result_schema, re.MULTILINE)),
            (
                "outcome",
                "findings",
                "changed_inputs",
                "evidence",
                "risks",
                "next_action",
            ),
        )
        self.assertContainsAll(
            self.handoff_normalized,
            (
                "bounded, self-contained fork or context by default",
                "Do not include raw logs, repeat the full Issue body, or restate the complete plan",
                "Prefer specialist-thread reuse",
            ),
        )
        self.assertContainsAll(
            self.runbook_normalized,
            (
                "Use the exact canonical `task_packet` and `result_packet` schemas",
                "Nest the planning-gate evidence",
                "link stable evidence instead of copying raw logs, full Issue bodies, or the complete plan",
            ),
        )

    def test_telemetry_is_prospective_and_distinguishes_measurement_kinds(self) -> None:
        self.assertContainsAll(
            self.telemetry_source,
            tuple(
                f"`{field}`"
                for field in (
                    "root_phase_wall_time_seconds",
                    "subagent_startup_seconds",
                    "subagent_wait_seconds",
                    "turn_count",
                    "tool_call_count",
                    "duplicate_read_count",
                    "verification_time_seconds",
                    "input_tokens",
                    "output_tokens",
                )
            ),
        )
        self.assertContainsAll(
            self.telemetry_normalized,
            (
                "`measurement_kind` as `observed`, `estimated`, or `unavailable`",
                "an unavailable value requires a reason",
                "an estimate must never be presented as an observation",
                "at least 25 percent lower median wall time",
                "at least 15 percent lower P95 wall time",
                "at least 30 percent fewer root/subagent turns",
                "at least 50 percent fewer duplicate broad-suite executions",
                "25--35 percent fewer combined `task_packet` and `result_packet` tokens",
                "Static policy tests establish contract shape, not runtime performance",
            ),
        )

    def test_runbook_applies_the_canonical_adaptive_policy(self) -> None:
        self.assertContainsAll(
            self.runbook_normalized,
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
