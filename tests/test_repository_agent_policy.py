"""Structural contracts for the compact repository-development policy."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = ROOT / "AGENTS.md"
RUNBOOK_PATH = ROOT / "AGENT_TASK_RUNBOOK.md"
WORKFLOW_PATH = ROOT / "GITHUB_WORKFLOW.md"
AGENT_DIRECTORY = ROOT / ".codex" / "agents"
EXPECTED_AGENTS = {
    "documentation_maintainer",
    "modularity_maintainer",
    "regression_diagnostician",
    "requirements_analyst",
    "runtime_implementer",
    "test_engineer",
}

AGENTS_APPROVED_BASE_WORDS = 4_538
RUNBOOK_APPROVED_BASE_WORDS = 2_807
AGENTS_WORD_CEILING = 999
RUNBOOK_WORD_CEILING = 999


def markdown_section(source: str, heading: str) -> str:
    """Return one Markdown section through the next peer/parent heading."""

    match = re.fullmatch(r"(#{1,6})\s+(.+)", heading)
    if match is None:
        raise ValueError(f"invalid Markdown heading: {heading!r}")
    level = len(match.group(1))
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


def normalized(source: str) -> str:
    return " ".join(source.split())


def declared_supported_modes(instructions: str) -> set[str]:
    source = normalized(instructions)
    modes = set(
        re.findall(
            r"(?:Support only|Require) `mode = "
            r"(analysis|implementation|review)`",
            source,
        )
    )
    handoff = re.search(
        r"parent handoff must set `mode` to (?P<modes>[^.]+)\.", source
    )
    if handoff is not None:
        modes.update(
            re.findall(
                r"`(analysis|implementation|review)`", handoff.group("modes")
            )
        )
    modes.update(
        re.findall(r"In `(analysis|implementation|review)` mode,", source)
    )
    if not modes:
        raise AssertionError("missing supported-mode declaration")
    return modes


class RepositoryAgentPolicyTests(unittest.TestCase):
    """Prevent semantic drift without coupling to full prose paragraphs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.agents_source = AGENTS_PATH.read_text(encoding="utf-8")
        cls.runbook_source = RUNBOOK_PATH.read_text(encoding="utf-8")
        cls.workflow_source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.sections = {
            heading: markdown_section(cls.agents_source, heading)
            for heading in (
                "### Delegation-Gain Gate",
                "### Root-Capability Routing",
                "### Role, Mode, and Control Gate",
                "### Worktree Disposition",
                "### Modularity Gate",
                "### Orchestration Sequence",
                "### Adaptive Verification",
                "### Handoff and Result Contracts",
                "### Prospective Orchestration Telemetry",
            )
        }
        cls.runbook_sections = {
            heading: markdown_section(cls.runbook_source, heading)
            for heading in (
                "## Preflight Every Writer",
                "## Route Work on the Critical Path",
                "## Dispose or Retain Worktrees",
                "## Plan and Record Verification",
            )
        }
        cls.workflow_cleanup = markdown_section(
            cls.workflow_source, "### Worktree Disposition and Cleanup"
        )
        cls.agent_paths = sorted(AGENT_DIRECTORY.glob("*.toml"))
        cls.agent_data = {
            path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
            for path in cls.agent_paths
        }

    def assertTerms(self, source: str, *terms: str) -> None:
        source = normalized(source)
        for term in terms:
            with self.subTest(term=term):
                self.assertIn(term, source)

    def test_agent_roster_parses_and_names_match(self) -> None:
        self.assertEqual(set(self.agent_data), EXPECTED_AGENTS)
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
        for name in ("requirements_analyst", "regression_diagnostician"):
            self.assertEqual(self.agent_data[name].get("sandbox_mode"), "read-only")
        expected_terms = {
            "requirements_analyst": (
                "Support only `mode = analysis`, remain read-only",
                "Do not design the boundary or authorize broad verification",
            ),
            "regression_diagnostician": (
                "Support only `mode = analysis`, remain read-only",
                "Diagnose only; do not fix or design remediation",
            ),
            "modularity_maintainer": (
                "`analysis`: Stay read-only",
                "`implementation`: Require an accepted modularity brief",
                "`review`: Stay read-only",
            ),
            "runtime_implementer": (
                "Require `mode = implementation`; otherwise remain read-only",
                "Never edit documentation, policies, guides, examples, tests",
            ),
            "test_engineer": (
                "In `analysis` mode, remain read-only",
                "In `implementation` mode, edit only assigned tests",
                "Never edit production code, documentation, policies, examples, or indices",
            ),
            "documentation_maintainer": (
                "In `analysis` mode, remain read-only",
                "edit only assigned README, documentation, policy, guide, and example files",
                "Never edit production code, tests, or project module indices",
            ),
        }
        for name, terms in expected_terms.items():
            with self.subTest(agent=name):
                self.assertTerms(self.agent_data[name]["developer_instructions"], *terms)

    def test_role_specific_routing_and_canonical_packets_are_present(self) -> None:
        role_terms = {
            "requirements_analyst": ("substantial ambiguity", "smallest sufficient evidence"),
            "regression_diagnostician": ("unknown failure", "stable family fingerprint"),
            "modularity_maintainer": ("module index", "canonical deduplication"),
            "runtime_implementer": ("outside the root fast path", "authorized evidence"),
            "test_engineer": ("behavioral/coverage review", "Never broaden selection"),
            "documentation_maintainer": ("documentation impact", "never broaden verification"),
        }
        for name, terms in role_terms.items():
            with self.subTest(agent=name):
                self.assertTerms(
                    self.agent_data[name]["developer_instructions"],
                    "`task_packet`",
                    "`result_packet`",
                    "Do not delegate to or coordinate with",
                    *terms,
                )

    def test_delegation_gain_gate_and_root_fast_path_are_exact(self) -> None:
        section = self.sections["### Delegation-Gain Gate"]
        formula = re.search(
            r"`predicted_critical_path_savings_seconds = [^`]+`", section
        )
        self.assertEqual(
            formula.group(0) if formula else None,
            "`predicted_critical_path_savings_seconds = root_sequential_estimate - "
            "(parallel_critical_path + spawn_wait_handoff_synthesis_overhead)`",
        )
        self.assertTerms(
            section,
            "savings >=20 seconds and >=20%",
            "`material_correctness_risk_reduction|mandatory_ownership_rule`",
            "docs/tests/triggered-modularity/production outside fast path",
            "<=2-file production/config work",
            "excludes mandatory ownership, ambiguity, unknown failure",
            "Never dispatch for deterministic commands",
            "batch safe root reads",
        )

    def test_root_capability_context_and_complexity_are_canonical(self) -> None:
        section = self.sections["### Root-Capability Routing"]
        self.assertTerms(
            section,
            "packet+intentional-history+attachments/preloads+irreducible child",
            "`observed|estimated|unavailable`, method/confidence",
            "`compact <=8,192`",
            "`moderate 8,193--32,768`",
            "`large 32,769--131,072`",
            "`oversized >131,072|unverified-fit`",
            "narrow/split",
            "never root ceilings",
        )

    def test_root_capability_quality_and_role_targets_are_canonical(self) -> None:
        section = self.sections["### Root-Capability Routing"]
        expected_rows = (
            "| `routine` | `gpt-5.6-terra`/`medium` | mechanical/reversible/no-material-risk |",
            "| `standard` | `gpt-5.6-sol`/`high` | bounded semantic/one responsibility |",
            "| `complex` | `gpt-5.6-sol`/`xhigh` | ambiguity/cause/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource/nonlocal |",
            "| `critical` | `gpt-5.6-sol`/`max` | two complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility |",
        )
        for row in expected_rows:
            with self.subTest(row=row):
                self.assertIn(row, section)
        self.assertTerms(
            section,
            "Roles default routine",
            "requirements/diagnosis ambiguity/unknown-cause",
            "modularity boundary",
            "runtime security/concurrency/migration/API/nonlocal",
            "tests security/concurrency/PTY/process/lifecycle/complex-risk",
            "docs governance/security/compatibility/migration",
            "Index-only stays actual class",
        )

    def test_root_capability_clamps_and_orders_each_axis_independently(self) -> None:
        section = self.sections["### Root-Capability Routing"]
        code = re.search(r"```text\n(?P<body>.*?)\n```", section, re.DOTALL)
        self.assertEqual(
            tuple(code.group("body").splitlines()) if code else (),
            (
                "desired_model=max(task_model_target,role_model_target,context_model_target)",
                "desired_effort=max(task_effort_target,role_effort_target)",
                "capped_model=min(desired_model,root_effective_model)",
                "capped_effort=min(desired_effort,root_effective_effort)",
            ),
        )
        self.assertTerms(
            section,
            "`gpt-5.6-luna<gpt-5.6-terra<gpt-5.6-sol`",
            "`none<low<medium<high<xhigh<max`",
            "within each root ceiling",
        )

    def test_root_capability_selection_and_inheritance_are_canonical(self) -> None:
        self.assertTerms(
            self.sections["### Root-Capability Routing"],
            "Select exact cap, else nearest stronger within each root ceiling",
            "fewest upward steps; tie smaller model then effort",
            "Never select weaker",
            "weaker-only returns root",
            "Override both axes or neither",
            "model-only may default effort",
            "unresolved root/order/availability/support inherits",
            "`ceiling_limited` high-consequence work narrows/requires stronger root",
        )

    def test_root_capability_routing_evidence_is_complete(self) -> None:
        self.assertTerms(
            self.sections["### Root-Capability Routing"],
            "Record root/source, context measurement/class, triggers/role target, desired/capped/selected/compatible pairs, limitation/fallback",
            "Packets bound decisions/exclusions/criteria",
            "`observed>inferred>proposed>unknown` evidence",
        )
        self.assertTerms(
            self.runbook_sections["## Route Work on the Critical Path"],
            "root/context/risk/role targets",
            "independently desired/capped/selected axes",
            "compatibility/ceiling/fallback",
            "Use bounded packets",
        )

    def test_role_mode_capability_matrix_matches_toml_declarations(self) -> None:
        section = self.sections["### Role, Mode, and Control Gate"]
        expected_rows = (
            "| requirements/diagnosis | `analysis` | read-only filesystem; no approval-enabled writes |",
            "| modularity | `analysis|review` / `implementation` | read-only / assigned-worktree writes |",
            "| runtime | `implementation` | assigned-worktree writes |",
            "| tests/docs | `analysis` / `implementation` | read-only / assigned-worktree writes |",
        )
        for row in expected_rows:
            with self.subTest(row=row):
                self.assertIn(row, section)
        expected_modes = {
            "requirements_analyst": {"analysis"},
            "regression_diagnostician": {"analysis"},
            "modularity_maintainer": {"analysis", "implementation", "review"},
            "runtime_implementer": {"implementation"},
            "test_engineer": {"analysis", "implementation"},
            "documentation_maintainer": {"analysis", "implementation"},
        }
        self.assertEqual(
            {
                role: declared_supported_modes(
                    self.agent_data[role]["developer_instructions"]
                )
                for role in EXPECTED_AGENTS
            },
            expected_modes,
        )

    def test_control_gate_preflight_is_observed_and_fail_closed(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Child first reports observed filesystem/sandbox/write/approval capability and identity",
            "unavailable/mismatched role-mode-capability stops before other reads/writes",
            "exact bytes/SHA-256/decoded paths",
        )

    def test_full_history_exception_is_bounded_and_evidenced(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Use bounded context",
            "full history requires reason/bound/stale-risk/root-approval evidence",
        )
        self.assertTerms(
            self.runbook_sections["## Route Work on the Critical Path"],
            "Use bounded packets",
        )

    def test_join_retry_and_partial_writer_controls_are_explicit(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Pre-record result IDs+role/mode",
            "join only when all valid/finished",
            "Retry transient spawn/delivery/join once after no-write proof/fingerprint",
            "possible writer mutation requires inspect/preserve/same-thread resume",
            "Never retry policy/capability/scope/schema failures",
        )

    def test_conflicts_and_root_fallback_preserve_role_ownership(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Resolve conflicts by evidence/role ownership",
            "escalating owner→root→user",
            "Root never absorbs mandatory ownership/adds reviewer roles",
            "specialists never delegate/coordinate",
        )

    def test_runbook_applies_role_mode_and_control_gate(self) -> None:
        section = self.runbook_sections["## Route Work on the Critical Path"]
        self.assertTerms(
            section,
            "[Delegation-Gain Gate](AGENTS.md#delegation-gain-gate)",
            "[Root-Capability Routing](AGENTS.md#root-capability-routing)",
            "[Role, Mode, and Control Gate](AGENTS.md#role-mode-and-control-gate)",
            "[Orchestration Sequence](AGENTS.md#orchestration-sequence)",
            "result IDs+role/mode",
            "validated join barrier",
            "authorized retry/resume/conflict escalation",
        )

    def test_writer_preflight_commands_and_identity_are_exact(self) -> None:
        preflight = self.runbook_sections["## Preflight Every Writer"]
        command_block = re.search(
            r"```bash\n(?P<commands>.*?)\n```", preflight, re.DOTALL
        )
        self.assertEqual(
            tuple(command_block.group("commands").splitlines())
            if command_block
            else (),
            (
                "pwd -P",
                "git rev-parse --show-toplevel",
                "git branch --show-current",
                "git rev-parse HEAD",
                "git merge-base HEAD <approved-base-sha>",
                "git merge-base --is-ancestor <approved-base-sha> HEAD",
                "git status --porcelain=v1 -z --untracked-files=all | od -An -tx1",
                "git status --porcelain=v1 -z --untracked-files=all | shasum -a 256",
            ),
        )
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "`pwd -P`, Git root/branch/exact HEAD/base/ancestry, exclusive targets",
            "exact bytes/SHA-256/decoded paths",
            "`git status --porcelain=v1 -z --untracked-files=all`",
        )

    def test_writer_preflight_is_pinned_fail_closed_and_repeatable(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "dirty state requires all three pinned",
            "Never reset/checkout/clean/revert",
            "Identity invalidation requires root update and same-writer re-preflight",
        )
        self.assertTerms(
            self.runbook_sections["## Preflight Every Writer"],
            "Report/match capability/approval path, identity, exclusive targets, exact porcelain bytes/hash/decoded paths",
            "Dirty state requires all three pinned",
            "Mismatch stops without reset/checkout/clean/revert",
            "root updates identity/hash and same writer repeats",
        )

    def test_worktree_dispositions_are_exact_and_authority_bound(self) -> None:
        section = self.sections["### Worktree Disposition"]
        first_sentence = section.split("; change only", 1)[0]
        names = tuple(
            name
            for name in re.findall(r"`([a-z_]+)`", first_sentence)
            if name != "identity"
        )
        self.assertEqual(
            names,
            (
                "remove_after_integration",
                "retain_with_reason",
                "authorization_required",
            ),
        )
        self.assertTerms(
            section,
            "`remove_after_integration` (pre-authorized)",
            "`retain_with_reason` (reason/next owner)",
            "`authorization_required`",
            "change only after recorded authority/state change",
        )

    def test_completion_inventory_and_removal_gates_are_complete(self) -> None:
        self.assertTerms(
            self.sections["### Worktree Disposition"],
            "Completion inventories path/branch/HEAD/porcelain/Issue-PR/recoverability/authority/outcome",
            "Removal requires authorization+clean+integrated+recoverable",
            "non-force",
            "verified registry/filesystem absence",
            "branch deletion is separate",
            "retain parent until `main`",
        )
        cleanup = self.runbook_sections["## Dispose or Retain Worktrees"]
        commands = re.search(
            r"```bash\n(?P<commands>.*?)\n```", cleanup, re.DOTALL
        )
        self.assertEqual(
            tuple(commands.group("commands").splitlines()) if commands else (),
            (
                "git worktree remove <exact-path>",
                "git worktree list --porcelain",
                "test ! -e <exact-path>",
            ),
        )

    def test_lifecycle_links_and_packet_nesting_are_canonical(self) -> None:
        self.assertTerms(
            self.workflow_cleanup,
            "[AGENTS.md](AGENTS.md#worktree-disposition)",
            "Removal requires clean status, completed integration verification, and recoverability",
            "non-forced and verified absent afterward",
            "Branch deletion is a separate authorized action",
        )
        self.assertTerms(
            self.sections["### Handoff and Result Contracts"],
            "Nest preflight/disposition under `identity`",
            "cleanup/recoverability under `evidence`",
            "disposition outcome/reason/risk/owner under findings/risks/next action",
        )

    def test_specialist_ownership_fusion_and_thread_reuse_are_explicit(self) -> None:
        self.assertTerms(
            self.sections["### Orchestration Sequence"],
            "one production owner",
            "nonoverlapping sequential ownership",
            "stabilize code then modularity reconciles indices",
            "stabilize then exclusive test and docs owners work",
            "resuming threads after pauses",
            "Reuse threads unless stale/isolation-risk",
        )

    def test_evidence_levels_are_selectable_and_complete_is_terminal(self) -> None:
        section = self.sections["### Adaptive Verification"]
        prefix = section.split("| Class |", 1)[0]
        expected = (
            "static_inventory",
            "exact",
            "semantic_cluster",
            "affected_suite",
            "full_suite",
            "complete",
        )
        observed = tuple(
            name for name in re.findall(r"`([a-z_]+)`", prefix) if name in expected
        )
        self.assertEqual(observed, expected)
        self.assertTerms(
            prefix,
            "Partial-order levels",
            "`complete` is terminal only with current mandatory green/accepted-skips",
            "resolved failures",
            "sufficient inventory",
            "accepted residual risk",
        )

    def test_verification_class_budgets_and_shard_limits_are_exact(self) -> None:
        section = self.sections["### Adaptive Verification"]
        expected_rows = (
            "| `local` | 300/180 | inventory+exact/static |",
            "| `bounded` | 900/300 | +required semantic |",
            "| `cross_cutting` | 1800/600 | selected cluster/affected |",
            "| `release` | Issue-defined | named gates |",
        )
        for row in expected_rows:
            with self.subTest(row=row):
                self.assertIn(row, section)

    def test_root_plan_and_result_fields_are_identifiable(self) -> None:
        section = self.sections["### Adaptive Verification"]
        self.assertTerms(
            section,
            "Plan criteria→evidence",
            "changed/affected inputs/exclusions/risks",
            "exact selection+owner",
            "estimates/confidence",
            "total/max/spent/remaining",
            "stops/triggers/reuse/risk",
            "Ledger provenance/digests/duration",
            "`completed_ids|failed_ids|accepted_skips|unexecuted_ids`",
        )
        self.assertTerms(
            self.runbook_sections["## Plan and Record Verification"],
            "class/rationale",
            "criteria→mandatory evidence",
            "changed/affected inventory/exclusions/risks",
            "exact selection/rationale/one owner",
            "estimates/confidence/budget",
            "stops/triggers/reuse/risk",
        )

    def test_sha_is_provenance_but_digest_controls_reuse(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "`normalized_command,exact_ids,relevant_input_digest,environment_class`",
            "reuse only root-proven disjoint changes with stable purpose/digest",
        )
        self.assertTerms(
            self.runbook_sections["## Plan and Record Verification"],
            "provenance/manifest/input-digest",
            "Reuse only root-proven disjoint, purpose/digest-stable evidence",
        )

    def test_evidence_has_one_owner_and_an_exact_deduplication_key(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "exact selection+owner",
            "Deduplicate exactly by `normalized_command,exact_ids,relevant_input_digest,environment_class`",
            "specialists only propose expansion",
        )
        self.assertTerms(
            self.runbook_sections["## Plan and Record Verification"],
            "exact selection/rationale/one owner",
            "exact dedup key",
            "No specialist broadens selection/level/concurrency/budget",
        )

    def test_shards_are_disjoint_complete_and_resumable(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "Shards are deterministic/disjoint/exhaustive exact IDs",
            "sequential unless root authorizes isolated nonoverlap",
            "maximum two fast-fail",
            "`completed_ids|failed_ids|accepted_skips|unexecuted_ids`",
            "interruption is no verdict",
            "resume valid remainder",
        )

    def test_root_alone_controls_escalation_and_risk_acceptance(self) -> None:
        section = self.sections["### Adaptive Verification"]
        self.assertTerms(
            section,
            "Root owns selection/authorization/budget/reuse/escalation/sufficiency",
            "specialists only propose expansion",
            "accepted residual risk",
            "root authorizes isolated nonoverlap",
        )
        self.assertTerms(
            self.agent_data["test_engineer"]["developer_instructions"],
            "Execute only evidence assigned to this role using the root plan",
            "Never broaden selection, level, concurrency, or budget",
            "never launch an unauthorized affected or full suite",
        )

    def test_full_suite_has_objective_triggers_and_no_default_gate(self) -> None:
        section = self.sections["### Adaptive Verification"]
        expected_triggers = (
            "explicit Issue/release criterion",
            "unbounded inventory",
            "changed package/public/cross-domain invariant",
            "unreliable global harness",
            "unknown out-of-cluster failure",
            "uncovered concrete risk",
        )
        for trigger in expected_triggers:
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, normalized(section))
        self.assertTerms(section, "Run `full_suite` only for")

    def test_failures_route_without_restarting_broad_verification(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "Unknown failure→diagnosis",
            "cross-boundary defect→modularity",
            "budget/cost exhaustion returns remaining evidence/options, never completion",
        )

    def test_policy_does_not_claim_capability_level_sandbox_enforcement(self) -> None:
        section = self.sections["### Role, Mode, and Control Gate"]
        self.assertTerms(
            section,
            "observed filesystem/sandbox/write/approval capability",
            "unavailable/mismatched role-mode-capability stops",
        )
        self.assertNotIn("prompts enforce runtime isolation", normalized(section))

    def test_packet_schemas_are_exact_and_compact(self) -> None:
        section = self.sections["### Handoff and Result Contracts"]
        task = re.search(r"Exact `task_packet`: `(?P<fields>[^`]+)`", section)
        result = re.search(r"Exact `result_packet`: `(?P<fields>[^`]+)`", section)
        self.assertEqual(
            tuple(task.group("fields").split(",")) if task else (),
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
            tuple(result.group("fields").split(",")) if result else (),
            (
                "outcome",
                "findings",
                "changed_inputs",
                "evidence",
                "risks",
                "next_action",
            ),
        )
        self.assertTerms(section, "Keep packets bounded", "no logs/full Issues/plans")

    def test_telemetry_is_prospective_and_distinguishes_measurement_kinds(self) -> None:
        section = self.sections["### Prospective Orchestration Telemetry"]
        self.assertTerms(
            section,
            "wall/startup/wait/turn/tool/duplicate-read/verification/token metrics",
            "`observed|estimated|unavailable`",
            "median -25%",
            "P95 -15%",
            "turns -30%",
            "duplicate suites -50%",
            "packet tokens -25--35%",
            "without correctness/safety/ownership/gate regression",
        )

    def test_runbook_applies_the_canonical_adaptive_policy(self) -> None:
        self.assertTerms(
            self.runbook_sections["## Plan and Record Verification"],
            "[Adaptive Verification](AGENTS.md#adaptive-verification)",
            "exact dedup key",
            "Ledger level/purpose/IDs/command/provenance/manifest/input-digest/duration/result/completed/failed/skipped/unexecuted/budget",
            "Shard deterministically above maximum",
            "Interruptions preserve state, never pass/fail",
            "`full_suite` needs objective trigger",
            "Complete only with current mandatory green/accepted-skip evidence",
        )

    def test_compact_policy_files_meet_independent_half_baseline_limits(self) -> None:
        counts = {
            "AGENTS.md": len(self.agents_source.split()),
            "AGENT_TASK_RUNBOOK.md": len(self.runbook_source.split()),
        }
        self.assertEqual(AGENTS_WORD_CEILING, 999)
        self.assertEqual(RUNBOOK_WORD_CEILING, 999)
        self.assertLessEqual(counts["AGENTS.md"], AGENTS_WORD_CEILING)
        self.assertLessEqual(
            counts["AGENT_TASK_RUNBOOK.md"], RUNBOOK_WORD_CEILING
        )
        self.assertLessEqual(
            AGENTS_WORD_CEILING, AGENTS_APPROVED_BASE_WORDS // 2
        )
        self.assertLessEqual(
            RUNBOOK_WORD_CEILING, RUNBOOK_APPROVED_BASE_WORDS // 2
        )


if __name__ == "__main__":
    unittest.main()
