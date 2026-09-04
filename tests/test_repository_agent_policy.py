"""Structural contracts for repository-development policy and skills."""

from __future__ import annotations

import re
import subprocess
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = ROOT / "AGENTS.md"
README_PATH = ROOT / "README.md"
WORKFLOW_PATH = ROOT / "GITHUB_WORKFLOW.md"
AGENT_DIRECTORY = ROOT / ".codex" / "agents"
SKILL_DIRECTORY = ROOT / ".agents" / "skills"
SKILL_PATHS = {
    name: SKILL_DIRECTORY / name / "SKILL.md"
    for name in (
        "adaptive-verification",
        "github-task-lifecycle",
        "subagent-model-selection",
        "subagent-routing",
    )
}
POLICY_WORD_CEILINGS = {
    AGENTS_PATH: 412,
    WORKFLOW_PATH: 715,
    SKILL_PATHS["adaptive-verification"]: 399,
    SKILL_PATHS["github-task-lifecycle"]: 400,
    SKILL_PATHS["subagent-model-selection"]: 174,
    SKILL_PATHS["subagent-routing"]: 292,
}
EXPECTED_AGENTS = {
    "documentation_maintainer",
    "modularity_maintainer",
    "regression_diagnostician",
    "requirements_analyst",
    "runtime_implementer",
    "test_engineer",
}


def normalized(source: str) -> str:
    return " ".join(source.split())


def markdown_section(source: str, heading: str) -> str:
    match = re.fullmatch(r"(#{1,6})\s+(.+)", heading)
    if match is None:
        raise ValueError(f"invalid heading: {heading!r}")
    level = len(match.group(1))
    lines = source.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError as error:
        raise AssertionError(f"missing heading: {heading}") from error
    end = len(lines)
    for index in range(start, len(lines)):
        candidate = re.match(r"^(#{1,6})\s+", lines[index])
        if candidate is not None and len(candidate.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def frontmatter(source: str) -> tuple[str, str]:
    match = re.match(r"\A---\n(?P<body>.*?)\n---\n", source, re.DOTALL)
    if match is None:
        raise AssertionError("missing YAML frontmatter")
    body = match.group("body")
    name = re.search(r"^name: (?P<value>[a-z0-9-]+)$", body, re.MULTILINE)
    description = re.search(
        r"^description: >-\n(?P<value>(?:  .+(?:\n|$))+)", body, re.MULTILINE
    )
    if name is None or description is None:
        raise AssertionError("invalid skill frontmatter")
    return name.group("value"), normalized(description.group("value"))


def declared_supported_modes(instructions: str) -> set[str]:
    source = normalized(instructions)
    modes = set(
        re.findall(
            r"(?:Support only|Require) `mode = (analysis|implementation|review)`",
            source,
        )
    )
    handoff = re.search(r"parent handoff must set `mode` to (?P<modes>[^.]+)\.", source)
    if handoff:
        modes.update(re.findall(r"`(analysis|implementation|review)`", handoff["modes"]))
    modes.update(re.findall(r"In `(analysis|implementation|review)` mode,", source))
    if not modes:
        raise AssertionError("missing supported-mode declaration")
    return modes


class RepositoryAgentPolicyTests(unittest.TestCase):
    """Prevent semantic drift without requiring verbose prose."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.agents_source = AGENTS_PATH.read_text(encoding="utf-8")
        cls.readme_source = README_PATH.read_text(encoding="utf-8")
        cls.workflow_source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.skill_sources = {
            name: path.read_text(encoding="utf-8") for name, path in SKILL_PATHS.items()
        }
        cls.verification_source = cls.skill_sources["adaptive-verification"]
        cls.lifecycle_source = cls.skill_sources["github-task-lifecycle"]
        cls.selection_source = cls.skill_sources["subagent-model-selection"]
        cls.routing_source = cls.skill_sources["subagent-routing"]
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
        terms = {
            "requirements_analyst": ("Support only `mode = analysis`, remain read-only",),
            "regression_diagnostician": ("Support only `mode = analysis`, remain read-only",),
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
            ),
            "documentation_maintainer": (
                "In `analysis` mode, remain read-only",
                "edit only assigned README, documentation, policy, guide, and example files",
            ),
        }
        for role, expected in terms.items():
            self.assertTerms(self.agent_data[role]["developer_instructions"], *expected)

    def test_role_specific_routing_and_canonical_packets_are_present(self) -> None:
        role_terms = {
            "requirements_analyst": ("substantial ambiguity", "smallest sufficient evidence"),
            "regression_diagnostician": ("unknown failure", "stable family fingerprint"),
            "modularity_maintainer": ("module index", "canonical deduplication"),
            "runtime_implementer": ("outside the root fast path", "authorized evidence"),
            "test_engineer": ("behavioral/coverage review", "Never broaden selection"),
            "documentation_maintainer": ("documentation impact", "never broaden verification"),
        }
        for role, expected in role_terms.items():
            self.assertTerms(
                self.agent_data[role]["developer_instructions"],
                "`task_packet`",
                "`result_packet`",
                "Do not delegate to or coordinate with",
                *expected,
            )

    def test_delegation_gain_gate_and_root_fast_path_are_exact(self) -> None:
        formula = (
            "predicted_critical_path_savings_seconds=root_sequential_estimate-"
            "(parallel_critical_path+spawn_wait_handoff_synthesis_overhead)"
        )
        self.assertIn(f"`{formula}`", self.routing_source)
        self.assertTerms(
            self.routing_source,
            "Require >=20s and >=20% of root estimate",
            "`material_correctness_risk_reduction|mandatory_ownership_rule`",
            "exceptions authorize, not savings",
            "Mandatory owners: docs/tests/triggered-modularity/production outside fast path",
            "Fast path permits clear one/two-file production/configuration within established responsibility/boundary",
            "excludes mandatory owners/ambiguity/unknown failures",
            "Never dispatch deterministic commands",
        )

    def test_root_effective_model_and_effort_resolution_is_canonical(self) -> None:
        self.assertIn(
            "Resolve root model/effort after aliases/defaults/session overrides/runtime child pairs",
            normalized(self.selection_source),
        )
        forbidden = (
            "dispatch_context_tokens",
            "context_model_target",
            "|Context|Tokens|",
            "8,193--32,768",
            "32,769--131,072",
            "Unknown fit is oversized",
            "context count/class/method/confidence",
            "dispatch context",
            "child context",
        )
        for source in (self.selection_source, self.routing_source, self.agents_source):
            for term in forbidden:
                self.assertNotIn(term, source)

    def test_root_capability_quality_and_role_targets_are_canonical(self) -> None:
        rows = (
            "|`routine`|Mechanical/reversible; no unresolved judgment/material risk|`gpt-5.6-terra`|`medium`|",
            "|`standard`|Bounded semantics; one-established-responsibility|`gpt-5.6-sol`|`high`|",
            "|`complex`|Ambiguity/unknown cause; multi-domain/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult-nonlocal-edges|`gpt-5.6-sol`|`xhigh`|",
            "|`critical`|Two interacting complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility|`gpt-5.6-sol`|`max`|",
            "|Requirements/diagnosis|Substantial-ambiguity/unknown-cause|",
            "|Modularity|Boundaries|",
            "|Runtime|Security/concurrency/migration/API/difficult-nonlocal-edges|",
            "|Tests|Security/concurrency/PTY/process/lifecycle/complex coverage|",
            "|Docs|Normative-governance/security/compatibility/migration|",
        )
        for row in rows:
            self.assertIn(row, self.selection_source)
        self.assertIn("Roles default routine; index-only keeps actual class", self.selection_source)

    def test_root_capability_clamps_and_orders_each_axis_independently(self) -> None:
        block = re.search(r"```text\n(?P<body>.*?)\n```", self.selection_source, re.DOTALL)
        self.assertEqual(
            block.group("body").splitlines() if block else [],
            [
                "desired_model=max(task_model_target,role_model_target)",
                "desired_effort=max(task_effort_target,role_effort_target)",
                "capped_model=min(desired_model,root_effective_model)",
                "capped_effort=min(desired_effort,root_effective_effort)",
            ],
        )
        self.assertTerms(
            self.selection_source,
            "`gpt-5.6-luna<gpt-5.6-terra<gpt-5.6-sol`",
            "`none<low<medium<high<xhigh<max`",
            "add `ultra` only when runtime-comparable",
            "compatible pair <=root",
        )

    def test_root_capability_selection_and_inheritance_are_canonical(self) -> None:
        self.assertTerms(
            self.selection_source,
            "Select exact cap or nearest stronger compatible pair <=root by fewest steps",
            "tie: smaller model, then higher effort",
            "Never weaken; weaker-only returns",
            "Override both or neither",
            "unresolved root/order/support/availability inherits exactly",
            "model-only may default effort",
            "`ceiling_limited` means either cap is below desired",
            "high-consequence work narrows or needs stronger root; otherwise record risk",
        )

    def test_root_capability_routing_evidence_is_complete(self) -> None:
        self.assertTerms(
            self.selection_source,
            "Record root/source, complexity/triggers, role target, desired/capped/selected/compatible pairs",
            "limitation, fallback/inheritance, exclusions, accepted decisions, criteria",
            "evidence `observed|inferred|proposed|unknown`, uncertainty stops",
        )

    def test_model_selection_precedence_and_enforcement_limits_are_exact(self) -> None:
        self.assertTerms(
            self.selection_source,
            "Implement selection through explicit spawn values",
            "Full-history forbids overrides: use bounded/no history, else inherit both with reason",
            "File-level `model|model_reasoning_effort` beats spawn",
            "keep role TOMLs unpinned",
            "[OpenAI](https://developers.openai.com/codex/subagents/) enforces neither classification nor child<=root",
        )

    def test_model_selection_contract_is_exclusive_to_selector(self) -> None:
        selection = normalized(self.selection_source)
        routing = normalized(self.routing_source)
        terms = (
            "desired_model=max(task_model_target,role_model_target)",
            "desired_effort=max(task_effort_target,role_effort_target)",
            "capped_model=min(desired_model,root_effective_model)",
            "capped_effort=min(desired_effort,root_effective_effort)",
            "ceiling_limited",
            "desired/capped/selected/compatible pairs",
            "Full-history forbids overrides",
        )
        for term in terms:
            self.assertIn(term, selection)
            self.assertNotIn(term, routing)
        self.assertIn("Load `$subagent-model-selection` for model/effort", self.routing_source)

    def test_role_mode_capability_matrix_matches_toml_declarations(self) -> None:
        self.assertTerms(
            self.routing_source,
            "RO: requirements/diagnosis `analysis`; modularity `analysis|review`; tests/docs `analysis`",
            "RW: modularity/runtime/tests/docs `implementation`",
            "|`requirements_analyst`|Substantial-ambiguity-only|",
            "|`regression_diagnostician`|Unknown-failure-only|",
        )
        expected = {
            "requirements_analyst": {"analysis"},
            "regression_diagnostician": {"analysis"},
            "modularity_maintainer": {"analysis", "implementation", "review"},
            "runtime_implementer": {"implementation"},
            "test_engineer": {"analysis", "implementation"},
            "documentation_maintainer": {"analysis", "implementation"},
        }
        self.assertEqual(
            {
                role: declared_supported_modes(data["developer_instructions"])
                for role, data in self.agent_data.items()
            },
            expected,
        )

    def test_control_gate_preflight_is_observed_and_fail_closed(self) -> None:
        self.assertTerms(
            self.routing_source,
            "RO means read-only without approval-enabled writes; RW means assigned-worktree writes",
            "Child first reports filesystem/sandbox/write availability/approval path/identity",
            "reject unsupported mode, missing evidence, or mismatch before other work",
            "Route observed capability fail-closed",
            "prompts/TOMLs/tests cannot enforce isolation",
        )

    def test_full_history_exception_is_bounded_and_evidenced(self) -> None:
        self.assertIn(
            "Full-history requires recorded reason/bounded scope/stale-risk assessment/root approval",
            normalized(self.routing_source),
        )

    def test_join_retry_and_partial_writer_controls_are_explicit(self) -> None:
        self.assertTerms(
            self.routing_source,
            "Pre-record result IDs/role/mode; join after none runs/missing and all packets validate",
            "Retry transient spawn/delivery/join once with fingerprint/no-write proof",
            "Possible writes require inspection/diff preservation/same-thread resume",
            "policy/capability/scope/schema never retry",
        )

    def test_conflicts_and_root_fallback_preserve_role_ownership(self) -> None:
        self.assertTerms(
            self.routing_source,
            "After failed retry, root does only authorized static/fast-path work",
            "`observed>inferred>proposed>unknown` within ownership",
            "never vote; escalate owner/root/user",
            "Mandatory owners: docs/tests/triggered-modularity/production outside fast path",
        )

    def test_routing_skill_applies_role_mode_and_control_gate(self) -> None:
        self.assertTerms(
            self.agents_source,
            "Load `$subagent-routing` before evaluating/dispatching subagents",
            "Load `$subagent-routing` before capability/identity preflight, handoff, join, retry, interruption, or conflict handling",
            "Load `$subagent-routing` for exact packets",
        )

    def test_model_selector_load_boundaries_are_exact(self) -> None:
        self.assertIn(
            "Load `$subagent-model-selection` before selecting, requesting, overriding, or inheriting a child model/reasoning effort",
            normalized(self.agents_source),
        )
        self.assertIn("Load `$subagent-model-selection` for model/effort", self.routing_source)

    def test_writer_preflight_commands_and_identity_are_exact(self) -> None:
        block = next(
            body
            for body in re.findall(r"```bash\n(.*?)\n```", self.lifecycle_source, re.DOTALL)
            if "pwd -P" in body
        )
        self.assertEqual(
            block[block.index("pwd -P") :].splitlines(),
            [
                "pwd -P",
                "git rev-parse --show-toplevel",
                "git branch --show-current",
                "git rev-parse HEAD",
                'git merge-base HEAD "$base_oid"',
                'git merge-base --is-ancestor "$base_oid" HEAD',
                "git status --porcelain=v1 -z --untracked-files=all | od -An -tx1",
                "git status --porcelain=v1 -z --untracked-files=all | shasum -a 256",
            ],
        )

    def test_writer_preflight_is_pinned_fail_closed_and_repeatable(self) -> None:
        self.assertTerms(
            self.routing_source,
            "Writers match physical path/Git root/branch/HEAD/base ancestry/targets and exact porcelain bytes/SHA-256/decoded paths",
            "pin all three",
            "Never reset/checkout/clean/revert",
            "Re-preflight after change/interruption/invalidation",
        )
        self.assertTerms(
            self.lifecycle_source,
            "Match handoff capability/identity/ancestry/targets and porcelain bytes/hash/paths",
            "Omit-`--track`/`-b`",
            "Legacy traceability requires authorized linked PR; stop if unlinked",
        )

    def test_worktree_dispositions_are_exact_and_authority_bound(self) -> None:
        for row in (
            "|`remove_after_integration`|Cleanup-authority-granted|",
            "|`retain_with_reason`|Reason/next-owner|",
            "|`authorization_required`|Removal-authority-absent/unresolved|",
        ):
            self.assertIn(row, self.routing_source)
        self.assertIn(
            "Nest under task `identity`; change only after recorded authority/state",
            normalized(self.routing_source),
        )

    def test_completion_inventory_and_removal_gates_are_complete(self) -> None:
        cleanup = markdown_section(self.workflow_source, "### Worktree Disposition and Cleanup")
        self.assertTerms(
            cleanup,
            "Completion inventories worktree/branch/HEAD/porcelain, Issue/PR, recoverability, cleanup authority, outcome/reason/ next owner",
            "Non-forced removal requires authorization, clean status, integration, and recoverability",
            "verify absence",
            "Branch deletion is separate",
            "Retain parent until `main`",
        )
        self.assertTerms(
            self.lifecycle_source,
            'git worktree remove "$worktree"',
            "git worktree list --porcelain",
            'test ! -e "$worktree"',
            "Never auto-merge/ close/clean or force-remove",
        )

    def test_lifecycle_links_and_packet_nesting_are_canonical(self) -> None:
        self.assertTerms(
            self.routing_source,
            "|`identity`|Repository/Issue/Project/Iteration/Development-linked-branch-or-PR/worktree/base/head/preflight/disposition|",
            "cleanup/recoverability",
            "|`risks`|Blockers/ambiguity/residual/proposed-expansions-requiring-root-authorization|",
        )
        self.assertTerms(
            self.lifecycle_source,
            "[GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md): authorization/metadata",
            "[AGENTS.md](../../../AGENTS.md): routing/verification",
        )

    def test_specialist_ownership_fusion_and_thread_reuse_are_explicit(self) -> None:
        sequence = markdown_section(self.agents_source, "### Orchestration Sequence")
        self.assertTerms(
            sequence,
            "Root resolves scope, gates, inventory, delegation, requirements/diagnosis, then any boundary brief/index delta",
            "use one production owner at a time",
            "reconcile indices and boundary review",
            "Run exclusive tests before exclusive docs",
            "Root validates packets, integrates, verifies, and summarizes",
        )
        self.assertTerms(
            self.routing_source,
            "Runtime boundary work needs index reconciliation/review before tests/docs",
            "return deviations until resolved/accepted",
            "Tests precede docs; reconcile drift",
            "reuse unless stale/unsafe",
        )

    def test_evidence_levels_are_selectable_and_complete_is_terminal(self) -> None:
        expected = (
            "static_inventory",
            "exact",
            "semantic_cluster",
            "affected_suite",
            "full_suite",
            "complete",
        )
        observed = tuple(
            name
            for name in re.findall(r"`([a-z_]+)`", self.verification_source)
            if name in expected
        )
        self.assertEqual(observed[:6], expected)
        self.assertTerms(
            self.verification_source,
            "`complete` is terminal, not level six",
            "mandatory/exact evidence is current green/accepted-skip",
            "failures resolved, inventory sufficient, and root accepts risk",
            "Passing never authorizes escalation",
        )

    def test_verification_class_budgets_and_shard_limits_are_exact(self) -> None:
        for row in (
            "|`local`|300|180|Inventory+exact/static|",
            "|`bounded`|900|300|Inventory+exact+semantic|",
            "|`cross_cutting`|1800|600|Inventory+cluster/affected|",
            "|`release`|Issue-defined|Issue-defined|Named gates|",
        ):
            self.assertIn(row, self.verification_source)

    def test_root_plan_and_result_fields_are_identifiable(self) -> None:
        self.assertTerms(
            self.verification_source,
            "each-criterion/`mandatory_evidence`",
            "exact-`authorized_selection`/rationale/owner",
            "API/packaging/persistence/migration/auth/security/concurrency/process/ PTY/config/dynamic-coupling risks",
            "estimate/source/confidence; total/max/spent/remaining; triggers/stops/reuse/invalidation/risk",
            "repository/absolute-worktree, baseline/HEAD, dirty fingerprint, environment- capabilities, purpose, level, exact-selection/IDs/command, outcome/duration/stable-failure-fingerprint",
        )

    def test_sha_is_provenance_but_digest_controls_reuse(self) -> None:
        self.assertTerms(
            self.verification_source,
            "`relevant_input_digest` covers selected tests, shared helpers/fixtures, runner configuration, transitive production closure, and environment",
            "Reuse after HEAD change requires proven disjointness plus unchanged digest/purpose",
            "uncertainty invalidates",
        )

    def test_evidence_has_one_owner_and_an_exact_deduplication_key(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Assign one `evidence_execution_owner`",
            "deduplicate exactly by `normalized_command,exact_ids,relevant_input_digest,environment_class`",
            "owner/label/purpose/ venue/wrapper never justify reruns",
            "Run exact/cluster locally, affected/full in CI",
            "duplicate broad venues only for an environment criterion",
        )

    def test_shards_are_disjoint_complete_and_resumable(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Above `max_shard_seconds`, partition deterministic disjoint portable IDs",
            "union equals selection; each occurs once",
            "Sequential is default",
            "Parallel requires root authorization, isolation, nonoverlap, runner support; maximum two fast-fail shards",
            "manifest digest/ordinal/IDs/input digest/result/duration",
            "`completed_ids,failed_ids,accepted_skips,unexecuted_ids`",
            "Timeout/replacement/interruption is neither pass nor assertion-failure: classify",
            "resume valid unexecuted/invalidated IDs",
            "Partial evidence never completes",
        )

    def test_root_alone_controls_escalation_and_risk_acceptance(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Root exclusively owns selection/authorization/budget/ concurrency/dedup/reuse/escalation/sufficiency",
            "specialists propose, never broaden",
            "root accepts risk",
        )
        self.assertTerms(
            self.agent_data["test_engineer"]["developer_instructions"],
            "Execute only evidence assigned to this role using the root plan",
            "Never broaden selection, level, concurrency, or budget",
            "never launch an unauthorized affected or full suite",
        )

    def test_full_suite_has_objective_triggers_and_no_default_gate(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Full-suite triggers: explicit Issue/parent/release criterion",
            "unbounded inventory",
            "changed package-wide/public/cross-domain invariant",
            "unreliable global discovery/bootstrap/packaging/ harness",
            "unknown out-of-cluster family",
            "uncovered concrete risk",
            "Implementation/review/suite availability is insufficient",
            "Over-budget required breadth returns the decision, never a completion claim",
            "Prompt/TOML/policy/docs-only work may use static/exact-policy/link/format/ diff evidence absent runtime/full-suite triggers",
        )

    def test_failures_route_without_restarting_broad_verification(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Unknown-cost/exhaustion returns completed/mandatory-remaining evidence,revised estimates, residual risk,and root-decision alternatives",
            "Repair known families and rerun invalidated evidence",
            "One fingerprint in >=2 responsibility holders stops broad reruns",
            "repository occurrence inventory with roots/exclusions/ patterns/classifications/limits",
            "Route cross-boundary defects once through modularity and unknown failures to diagnosis",
        )

    def test_policy_does_not_claim_capability_level_sandbox_enforcement(self) -> None:
        self.assertTerms(
            self.routing_source,
            "Child first reports filesystem/sandbox/write availability/approval path/identity",
            "Route observed capability fail-closed",
            "prompts/TOMLs/tests cannot enforce isolation",
        )

    def test_packet_schemas_are_exact_and_compact(self) -> None:
        packet_source = self.routing_source.split("|`task_packet` field|", 1)[1]
        task_source, result_source = packet_source.split("|`result_packet` field|", 1)
        task_fields = tuple(re.findall(r"^\|`([a-z_]+)`\|", task_source, re.MULTILINE))
        result_fields = tuple(re.findall(r"^\|`([a-z_]+)`\|", result_source, re.MULTILINE))
        self.assertEqual(
            task_fields,
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
            result_fields,
            ("outcome", "findings", "changed_inputs", "evidence", "risks", "next_action"),
        )
        self.assertTerms(
            self.routing_source,
            "|`gates`|GitHub/modularity-decisions/rationale/evidence/accepted-boundary/index-status/delta|",
            "|`upstream_decisions`|Distilled-findings/accepted-decisions|",
            "|`stop_conditions`|Policy-conflict/breaking-decision/scope-deviation/unexpected-structure/other-explicit-returns|",
            "Exclude logs/full Issues/plans",
        )

    def test_telemetry_is_prospective_and_distinguishes_measurement_kinds(self) -> None:
        fields = (
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
        self.assertIn(",".join(fields), self.routing_source)
        self.assertTerms(
            self.routing_source,
            "as `observed|estimated|unavailable`",
            "explain unavailable; estimates aren't observations",
            "`median>=25%,P95>=15%,turns>=30%,duplicate-broad-suites>=50%,packet-tokens=25--35%`",
            "Static tests don't prove performance",
        )

    def test_verification_skill_applies_canonical_adaptive_policy(self) -> None:
        self.assertTerms(
            self.agents_source,
            "Load `$adaptive-verification` before planning, running, reusing, escalating, or accepting evidence for nontrivial tracked changes",
            "Root owns authorization and sufficiency",
        )
        self.assertTerms(
            self.verification_source,
            "Passing never authorizes escalation",
            "deduplicate exactly by",
            "Partial evidence never completes",
            "Full-suite triggers",
        )

    def test_compact_policy_files_meet_independent_half_baseline_limits(self) -> None:
        expected = {
            "AGENTS.md": 412,
            "GITHUB_WORKFLOW.md": 715,
            ".agents/skills/adaptive-verification/SKILL.md": 399,
            ".agents/skills/github-task-lifecycle/SKILL.md": 400,
            ".agents/skills/subagent-model-selection/SKILL.md": 174,
            ".agents/skills/subagent-routing/SKILL.md": 292,
        }
        self.assertEqual(
            {path.relative_to(ROOT).as_posix(): limit for path, limit in POLICY_WORD_CEILINGS.items()},
            expected,
        )
        for path, limit in POLICY_WORD_CEILINGS.items():
            self.assertLessEqual(len(path.read_text(encoding="utf-8").split()), limit)

    def test_policy_skill_roster_frontmatter_and_word_limits_are_exact(self) -> None:
        self.assertEqual(set(SKILL_PATHS), set(self.skill_sources))
        for name, source in self.skill_sources.items():
            declared_name, description = frontmatter(source)
            self.assertEqual(declared_name, name)
            self.assertTrue(description)
            self.assertTrue(source.split("---\n", 2)[-1].strip())

    def test_agents_loads_skills_at_positive_and_negative_boundaries(self) -> None:
        descriptions = {
            "adaptive-verification": (
                "Use for nontrivial tracked-change evidence planning/running/reuse/escalation/acceptance",
                "excludes conversation/story execution",
            ),
            "github-task-lifecycle": (
                "Use for GitHub-tracked planning/inspection/mutation/integration/cleanup",
                "excludes untracked/story-runtime work",
            ),
            "subagent-model-selection": (
                "Use for subagent model/effort selection/override/inheritance",
                "excludes root/story agents",
            ),
            "subagent-routing": (
                "Use for development-subagent evaluation/dispatch/control/handoff/join/retry",
                "excludes story/root-only work",
            ),
        }
        for name, terms in descriptions.items():
            self.assertTerms(frontmatter(self.skill_sources[name])[1], *terms)
        self.assertTerms(
            self.agents_source,
            "Load `$github-task-lifecycle` for GitHub-tracked work",
            "Load `$subagent-routing` before evaluating/dispatching subagents",
            "Load `$subagent-model-selection` before selecting, requesting, overriding, or inheriting a child model/reasoning effort",
            "Load `$adaptive-verification` before planning, running, reusing, escalating, or accepting evidence for nontrivial tracked changes",
        )
        self.assertIn(
            "[`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md)",
            self.readme_source,
        )

    def test_removed_runbook_is_absent_and_unreferenced(self) -> None:
        removed_name = "AGENT_TASK_" + "RUNBOOK.md"
        self.assertFalse((ROOT / removed_name).exists())
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, check=True, stdout=subprocess.PIPE
        ).stdout.split(b"\0")
        references = []
        for raw_path in tracked:
            if not raw_path:
                continue
            path = ROOT / raw_path.decode()
            if path.is_file() and removed_name.encode() in path.read_bytes():
                references.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(references, [])

    def test_modularity_triggers_missing_policy_index_and_review_loop(self) -> None:
        gate = markdown_section(self.agents_source, "### Modularity Gate")
        self.assertTerms(
            gate,
            "importable structure",
            "responsibility/state/resource/lifecycle ownership",
            "dependency direction/cycles/private access",
            "shared abstractions/adapters/ facades/protocols/exports/public imports",
            "multi-consumer extraction/migration",
            "architectural coupling",
            "Exclude unchanged-contract local refactors, tests/docs, formatting, typing, ordinary configuration/data, dependency metadata, and story runs",
            "pre-writer `analysis`, architecture-dominant `implementation`, or post-writer `review`",
            "Unexpected triggers stop writers",
        )
        self.assertTerms(
            self.routing_source,
            "Missing policy: report language/path; root asks about packaged creation, resumes thread",
            "records declined general-judgment exceptions",
            "Missing index needs no permission: derive inventory/path/content; same-thread index-only creation",
            "Select/verify root `<LANGUAGE>_MODULE_INDEX.md`",
            "return deviations until resolved/accepted",
        )

    def test_github_authorization_planning_and_relationships_are_exact(self) -> None:
        authority = markdown_section(self.workflow_source, "## Authority and Authorization")
        gate = markdown_section(self.workflow_source, "## Initial Planning Gate")
        self.assertTerms(
            authority,
            "Implementation also authorizes initial in-scope Issue/ milestone creation or reuse, native relations, canonical Project membership/metadata, and Development-linked task-branch creation after gate passage",
            "Project-schema changes, unrelated edits, stages, commits, other pushes, PR/review/closure/merge/deletion/cleanup need explicit authorization",
            "Worktree removal and branch deletion are separate",
        )
        self.assertTerms(
            gate,
            "`github_planning_gate=not_required|pending|passed|blocked`",
            "Pending permits read-only only",
            "Large work requires root and all known descendants before implementation",
            "Verify native hierarchy/dependencies",
            "required fields/labels/milestones, approved base, and an executable `Ready` leaf",
            "Partial failure preserves IDs, repairs gaps, blocks writing, and never duplicates",
        )
        self.assertTerms(
            self.lifecycle_source,
            "Search every Issue-state; create/reuse root before descendants",
            "Use one tree-wide logical Iteration",
            "Create fields/native relations; re-read complete graph/metadata and unblocked `Ready` leaf",
            "|Priority|Organization-Issue-field;never-same-named-Project-field|",
        )

    def test_github_branch_status_integration_and_cleanup_are_exact(self) -> None:
        status = markdown_section(self.workflow_source, "## Project Status")
        self.assertTerms(
            status,
            "|`Backlog`|New/incomplete/reprioritized/pre-start blocked|",
            "|`Ready`|Accepted, unblocked, metadata complete|",
            "|`In progress`|Authorized work; assignee plus Development branch/PR; drafts stay|",
            "|`In review`|Non-draft Development/Project-linked PR plus evidence|",
            "|`Done`|Gates passed, approved merge/exception, Issue closed|",
            "Blockage is an overlay",
            "blocked never becomes `Done`",
        )
        self.assertTerms(
            self.lifecycle_source,
            "|Bases/order|Parent/standalone=approved-main;child=current-parent;dependencies-wait|",
            "|Parent-PR|After-first-child:create-authorized-draft|",
            "|Final-parent|After-all-children:sync-approved-main;run-final-evidence;Refs-to-Closes;set-`In review`;merge|",
            "|Sync|Fresh-fetch;reconfirm-head/base-before-sync/merge|",
            "|Failure|Block-siblings;use-authorized-revert-PR/follow-up-child|",
            "|Transition|Re-read-draft/Development/Project-status/closure/linked-PRs|",
            "Parent accepts only authorized child squashes at current tip and approved-main sync",
            "fast-forward clean parent `--ff-only`",
            "Never auto-merge/ close/clean or force-remove",
            "Retain parent until main integration",
        )


if __name__ == "__main__":
    unittest.main()
