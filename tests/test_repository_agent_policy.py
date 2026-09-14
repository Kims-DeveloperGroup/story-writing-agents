"""Structural contracts for repository-development policy, skills, and templates."""

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
COMMIT_POLICY_PATH = ROOT / "COMMIT_POLICY.md"
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
    AGENTS_PATH: 440,
    WORKFLOW_PATH: 715,
    SKILL_PATHS["adaptive-verification"]: 560,
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
WORK_TEMPLATE_HEADINGS = {
    ROOT / ".github/ISSUE_TEMPLATE/work_item.md": (
        "Objective",
        "Use cases and expected results",
        "Implementation summary",
        "Acceptance criteria",
        "Boundaries and dependencies",
        "Related work and verification plan",
        "Implementation plan",
    ),
    ROOT / ".github/pull_request_template.md": (
        "What changed",
        "Before and after",
        "Implementation",
        "Verification",
        "Limitations and follow-ups",
        "Related Issue",
    ),
    ROOT / ".github/templates/evidence.md": (
        "What this demonstrates",
        "Scenario results",
        "Reproduction and environment",
        "Failures and coverage gaps",
        "Technical provenance and artifacts",
        "Assessment",
    ),
    ROOT / ".github/templates/working-status-comment.md": (
        "Current status",
        "What works now",
        "What remains",
        "Blocker or decision",
        "Next step",
        "Evidence links",
    ),
}
AUTHORING_PATH = (
    SKILL_DIRECTORY / "github-task-lifecycle/references/issue-pr-authoring.md"
)
TEMPLATE_PLACEHOLDER = re.compile(r"\{\{[^{}\n]+\}\}")
MARKDOWN_LINK = re.compile(r"\[[^\]\n]+\]\(<?([^\s)>]+)>?\)")


def template_parts(source: str) -> tuple[str, list[str]]:
    """Extract the reusable body, rejecting comments that can leak hidden text."""
    visible: list[str] = []
    comments: list[str] = []
    comment_start: int | None = None
    cursor = 0
    for delimiter in re.finditer(r"<!--|-->", source):
        if delimiter.group() == "<!--":
            if comment_start is not None:
                raise AssertionError("nested HTML comment")
            visible.append(source[cursor:delimiter.start()])
            comment_start = delimiter.end()
        else:
            if comment_start is None:
                raise AssertionError("unmatched HTML comment close")
            comments.append(source[comment_start:delimiter.start()])
            comment_start = None
            cursor = delimiter.end()
    if comment_start is not None:
        raise AssertionError("unclosed HTML comment")
    visible.append(source[cursor:])
    body = re.sub(r"\A---\n.*?\n---\n", "", "".join(visible), count=1, flags=re.DOTALL)
    return body.strip(), comments


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
        cls.commit_policy_source = COMMIT_POLICY_PATH.read_text(encoding="utf-8")
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

    def assertRunnerRoute(self, source: str, *parts: str) -> None:
        commands = re.findall(
            r"`([^`]*PYTHONPATH=src python -m tests\.runner[^`]*)`",
            normalized(source),
        )
        self.assertTrue(
            any(all(part in command for part in parts) for command in commands),
            f"missing canonical runner route containing {parts!r}",
        )

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

    def test_agents_points_repository_work_to_the_canonical_runner(self) -> None:
        self.assertTerms(
            self.agents_source,
            "canonical repository test runner",
            "`PYTHONPATH=src python -m tests.runner`",
        )
        self.assertRunnerRoute(self.agents_source)

    def test_adaptive_verification_names_every_canonical_runner_route_and_gate(self) -> None:
        for parts in (
            ("run", "--id"),
            ("run", "--tier fast"),
            ("run", "--group"),
            ("run", "--shard 1/2"),
            ("run", "--shard 2/2"),
        ):
            with self.subTest(route=parts):
                self.assertRunnerRoute(self.verification_source, *parts)
        self.assertTerms(
            self.verification_source,
            "#679",
            "fast lane <=300 seconds",
            "affected group <=600 seconds",
            "sequential-equivalent <=5102.566 seconds",
            "two-shard wall <=3300 seconds",
        )

    def test_runner_selection_and_execution_remain_root_owned_and_packet_exact(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Root exclusively owns selection/authorization/budget/ concurrency/dedup/reuse/escalation/sufficiency",
            "maximum two",
        )
        instructions = self.agent_data["test_engineer"]["developer_instructions"]
        self.assertRunnerRoute(instructions)
        self.assertTerms(
            instructions,
            "exact `task_packet`",
            "`--id`",
            "`--module`",
            "`--tier`",
            "`--group`",
            "`--shard`",
            "Never broaden selection, level, concurrency, or budget",
        )

    def test_runner_reports_use_fresh_owner_only_paths_and_structured_evidence(self) -> None:
        self.assertTerms(
            self.verification_source,
            "`umask 077`",
            "`mktemp -d`",
            "owner-only",
            "`0700`",
            "absent JSON leaf",
            "`0600`",
            "`canonical_manifest`",
            "`selection_manifest`",
            "`digest`",
            "`tests`",
            "`durations`",
            "`unexecuted_ids`",
        )

    def test_diagnostic_ids_cannot_replace_disjoint_exhaustive_shard_evidence(self) -> None:
        self.assertRunnerRoute(self.verification_source, "reconcile")
        self.assertTerms(
            self.verification_source,
            "Diagnostic `--id` output is not complete-shard evidence",
            "reconciliation proves shard ID sets are disjoint",
            "union exactly equals the canonical manifest",
            "both shard reports are terminal",
        )

    def test_compact_policy_files_meet_independent_half_baseline_limits(self) -> None:
        expected = {
            "AGENTS.md": 440,
            "GITHUB_WORKFLOW.md": 715,
            ".agents/skills/adaptive-verification/SKILL.md": 560,
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
                "Use for nontrivial tracked changes",
                "excludes conversation/story execution",
            ),
            "github-task-lifecycle": (
                "GitHub-tracked work",
                "supplies/guards planning,linking,branching,PR,integration,cleanup commands",
                "excludes untracked/story-runtime work",
            ),
            "subagent-model-selection": (
                "Subagent model/effort decisions",
                "derives root-capped selection/override/inheritance",
                "excludes root/story-runtime agents",
            ),
            "subagent-routing": (
                "Development-subagent routing",
                "validates/evaluates/dispatches/controls/joins/retries packets",
                "excludes story/root-only work",
            ),
        }
        for name, terms in descriptions.items():
            self.assertTerms(frontmatter(self.skill_sources[name])[1], *terms)
        self.assertTerms(
            self.verification_source,
            "Root exclusively owns selection/authorization/budget/ concurrency/dedup/reuse/escalation/sufficiency",
            "specialists propose, never broaden",
        )
        self.assertTerms(
            self.agents_source,
            "For GitHub-tracked work load `$github-task-lifecycle`",
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
        issues = markdown_section(self.workflow_source, "## Issues")
        agents_gate = markdown_section(self.agents_source, "## GitHub Planning Gate")
        requirement = markdown_section(self.workflow_source, "## Requirement Releases")
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
            issues,
            "Keep-one-implementation/delivery-unit on current-Issue",
            "Children require planned/known direct-child sets of >=2 independently-complete necessary units/slices/releases, created/verified together",
            "never sole-child/wrapper/placeholder chains",
        )
        self.assertTerms(
            agents_gate,
            "One-implementation/delivery-unit plans stay on their Issue",
            "direct-child-sets/subdivision require >=2 independently-complete necessary siblings, created/verified together",
            "otherwise enlarge/update current scope",
        )
        self.assertTerms(
            requirement,
            "New work uses organization Issue `Requirement=Must|Recommended|Optional`, replacing Priority",
            "The old field is renamed `Legacy Priority`; preserve historical values and make no new assignments",
            "Classify existing Issues only when entering a new phase/Iteration; record basis",
            "Unset blocks `Ready`",
            "Dependencies necessary for delivery are `Must`; agents never downgrade `Must` without a user/maintainer decision",
            "|Initial plan|Classify-all-known-leaves;one-implementation/delivery-unit-stays-on-current-Issue/no-sole-child-or-wrapper;children-only-as-known-at-least-2-direct-independently-complete-necessary-slice/release-siblings-created/verified-together;subdivide-only-into-at-least-2-independently-complete-children-else-enlarge/update-own-scope;no-phase/metadata/branch-PR/scope-mirror-placeholders;single-release-owned-by-current/root-Issue;numbered-release-children-only-when-at-least-2-releases-required;release-with-one-unit-implements-directly;feature-leaf-children-require-at-least-2-independent-slices;use-`-02+`-for-capacity|",
            "|Release integrity|Dependency-closed/testable/user-usable/complete-features-or-functions;never-scaffolding/half-API/dormant-config;ship-supporting-tests/docs/migrations|",
            "|Order|Later-may-depend-on-earlier;never-reverse|",
            "|Capacity|Every-implementation-leaf-needs-Size;`XS=1,S=2,M=3,L=5,XL=8`;release-load=sum-of-unfinished-leaf-points;record-capacity;planned-load-must-not-exceed-it|",
        )
        self.assertTerms(
            self.lifecycle_source,
            "Search all Issue-states; create/reuse root; descendants require general-sibling-gate",
            "Use one-Iteration-per-Requirement-release; leaves-inherit; root-tracks-current-`Must`; completed-releases-retain-theirs",
            "Create fields/native-relations; re-read complete graph/metadata and unblocked `Ready` leaf",
            "|Requirement|Set-organization-Issue-Requirement-`Must`/`Recommended`/`Optional`;record-basis;existing-only-at-new-phase/Iteration;unset-blocks-`Ready`;necessary-dependencies-`Must`;never-agent-downgrade|",
            "|Release-plan|Classify-all-known-leaves;one-implementation/delivery-unit=current-Issue/no-sole-child-or-wrapper;create/verify-only-known-at-least-2-direct-independently-complete-necessary-slice/release-siblings;subdivide-only-into-at-least-2-independent-children-else-enlarge/update-own-scope;no-phase/metadata/branch-PR/scope-mirror-placeholders;single-release=current/root-Issue;numbered-release-children-only-if-at-least-2-releases-required;one-unit-release-implements-directly;feature-children-only-if-at-least-2-independent-slices;require-each-implementation-leaf-Size;load=sum(unfinished-leaf-points)<=capacity;verify-integrity/topology|",
        )
        self.assertNotIn("Use one tree-wide logical Iteration", self.lifecycle_source)
        self.assertNotIn("|Priority|Organization-Issue-field", self.lifecycle_source)
        self.assertNotIn(
            "|Capacity|`XS=1,S=2,M=3,L=5,XL=8`;record-release-capacity|",
            requirement,
        )
        self.assertNotIn("create-only-nonempty-numbered-", requirement)
        self.assertNotIn(
            "create-only-nonempty-numbered-tier-releases", self.lifecycle_source
        )
        self.assertNotIn(
            "create/reuse root before descendants", self.lifecycle_source
        )

    def test_github_branch_status_integration_and_cleanup_are_exact(self) -> None:
        issues = markdown_section(self.workflow_source, "## Issues")
        status = markdown_section(self.workflow_source, "## Project Status")
        requirement = markdown_section(self.workflow_source, "## Requirement Releases")
        branches = markdown_section(
            self.workflow_source, "## Branches, Commits, and Pull Requests"
        )
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
            requirement,
            "|Resize|Preserve-all-`Must`;select-`Recommended`-by-accepted-user-order;defer-`Optional`-first,then-`Recommended`-that-do-not-fit,to-`Backlog`;clear-Iteration/wave;record-reason/reconsideration|",
            "|Disposition|`Not planned`-only-explicit-rejection;active-pause-needs-approved-partial-state/PR-branch-disposition/recoverability|",
            "|Oversize|Split-oversized-work-into-complete-slices/releases;indivisible-over-cap-needs-capacity-exception;preserve-all-`Must`|",
            "|Iteration|One-per-release;leaves-inherit-Iteration/waves;active-statuses-require-it;`Backlog`-may-omit;root-tracks-current-`Must`;completed-releases-retain-theirs|",
            "|Closure|Each-release-has-publishable-`main`-PR-and-closes-on-publication;root-closes-after-all-`Must`/integration;other-tiers-stay-linked/nonblocking|",
            "|Topology|Single-release=current/root-Issue;feature-leaf-PR-targets-release-branch;numbered-release-PR-targets-`main`-and-closes-release;all-PRs-use-GitHub-squash-merge;umbrella-normally-planning-only/administrative-close-after-`Must`/core-verification;distinct-diff-uses-own-Development-linked-branch/PR-targeting-`main`,starts-after-all-`Must`-release-PRs/core-integration,and-may-close-root|",
        )
        self.assertTerms(
            issues,
            "Root closes after all `Must` releases/core integration",
            "linked `Recommended`/`Optional` follow-ups are excluded from required children and never block closure",
            "Child PR never closes its parent",
        )
        self.assertTerms(
            branches,
            "Link every branch/PR through its owning Issue's Development field",
            "Use GitHub's **Squash and merge** method for every PR, including feature, release, and distinct-diff PRs; never use a merge commit or rebase merge",
            "Every resulting squash commit message must include the owning issue number (for example, `#123`)",
            "Use `Refs #N` for partial/child work",
            "Use `Closes #N` only for completing `main` PRs",
        )
        self.assertTerms(
            markdown_section(self.commit_policy_source, "## Pull Request Merges"),
            "Always merge pull requests with GitHub's **Squash and merge** method",
            "Never use a merge commit or rebase merge for a pull request",
            "Include the owning issue number (for example, `#123`) in every resulting squash commit message",
        )
        self.assertTerms(
            self.lifecycle_source,
            "|Resize|Preserve-all-`Must`;select-`Recommended`-by-accepted-user-order;defer-`Optional`-first,then-nonfitting-`Recommended`-to-`Backlog`;clear-Iteration/wave;record-reason/reconsideration;pause-only-with-approval/partial-state/branch-PR-disposition/recoverability;split-complete-slices-or-record-indivisible-exception|",
            "|Bases/order|Release/standalone=approved-main;feature-leaf=current-release;dependencies-wait;ordinary-child-parent-excludes-release-PRs|",
            "|Release-PR|After-first-feature-leaf:create-authorized-draft-targeting-`main`;after-all-leaves:sync-main/run-final-evidence/Refs-to-Closes/set-`In review`/merge/close-release|",
            "|Umbrella|Normally-planning-only/administrative-close-after-`Must`/core-verification;distinct-diff=own-Development-linked-branch/PR-target-`main`,start-after-all-`Must`-release-PRs/core-integration,may-close-root;other-tiers-linked/nonblocking|",
            "|Sync|Fresh-fetch;reconfirm-head/base-before-sync/merge|",
            "|Failure|Block-siblings;use-authorized-revert-PR/follow-up-child|",
            "|Transition|Re-read-draft/Development/Project-status/closure/linked-PRs|",
            "Release accepts only authorized leaf squashes at current tip and approved-main sync",
            "fast-forward clean parent `--ff-only`",
            "Never auto-merge/ close/clean or force-remove",
            "[completion](../../../GITHUB_WORKFLOW.md#completion-and-exceptions)",
            "[cleanup](../../../GITHUB_WORKFLOW.md#worktree-disposition-and-cleanup)",
        )
        self.assertTerms(
            markdown_section(self.workflow_source, "### Worktree Disposition and Cleanup"),
            "Non-forced removal requires authorization, clean status, integration, and recoverability",
            "Branch deletion is separate",
            "Retain parent until `main`",
        )
        self.assertTerms(
            markdown_section(self.workflow_source, "## Completion and Exceptions"),
            "Reopening clears `Done`, returns `Backlog`, and replans",
        )
        self.assertNotIn("Child PRs target parent and squash", branches)
        self.assertNotIn(
            "Ordinary child-to-parent squashes apply only to feature leaves, never release PRs",
            branches,
        )
        self.assertNotIn(
            "umbrella-root-planning-only/no-integration-branch-unless-own-distinct-diff",
            requirement,
        )
        self.assertNotIn("|Parent-PR|", self.lifecycle_source)
        self.assertNotIn("|Final-parent|", self.lifecycle_source)


class RepositoryWorkTemplateTests(unittest.TestCase):
    """Check reusable structure; prose quality and source accuracy need review."""

    def readRequired(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"missing authoring file: {path.relative_to(ROOT)}")
        return path.read_text(encoding="utf-8")

    def localTargets(self, path: Path, source: str) -> set[Path]:
        targets = set()
        for destination in MARKDOWN_LINK.findall(source):
            if TEMPLATE_PLACEHOLDER.search(destination) or re.match(
                r"[a-zA-Z][a-zA-Z0-9+.-]*:", destination
            ):
                continue
            relative_path = destination.split("#", 1)[0]
            target = (path.parent / relative_path).resolve() if relative_path else path
            self.assertTrue(target.is_relative_to(ROOT), f"link leaves repository: {destination}")
            self.assertTrue(target.exists(), f"broken link in {path.relative_to(ROOT)}: {destination}")
            targets.add(target)
        return targets

    def test_comment_extraction_rejects_leaks_and_preserves_body(self) -> None:
        source = "---\nname: Work\n---\n<!-- notes -->\n## Objective\n{{goal}}\n<!-- example -->"
        self.assertEqual(template_parts(source), ("## Objective\n{{goal}}", [" notes ", " example "]))
        for source in (
            "<!-- example <!-- notes --> leaked example -->",
            "<!-- unclosed example",
            "unexpected --> example",
        ):
            with self.subTest(source=source), self.assertRaises(AssertionError):
                template_parts(source)

    def test_native_issue_template_has_discovery_frontmatter(self) -> None:
        source = self.readRequired(ROOT / ".github/ISSUE_TEMPLATE/work_item.md")
        metadata = re.match(r"\A---\n(.*?)\n---\n", source, re.DOTALL)
        self.assertIsNotNone(metadata, "native Issue template needs YAML frontmatter")
        assert metadata is not None
        for field in ("name", "about"):
            with self.subTest(field=field):
                value = re.search(rf"^{field}:\s*([^\n]+)$", metadata[1], re.MULTILINE)
                self.assertIsNotNone(value, f"missing native Issue template {field}")
                assert value is not None
                self.assertTrue(value[1].strip(" \"'"))

    def test_reusable_bodies_contain_only_expected_sections_and_placeholders(self) -> None:
        for path, headings in WORK_TEMPLATE_HEADINGS.items():
            with self.subTest(template=path.relative_to(ROOT)):
                body, _ = template_parts(self.readRequired(path))
                self.assertTrue(body.startswith(f"## {headings[0]}\n"))
                self.assertEqual(re.findall(r"^## (.+)$", body, re.MULTILINE), list(headings))
                self.assertNotRegex(body, r"(?i)\b(?:historical|illustrative|worked) example\b")
                self.assertNotRegex(body, r"https?://", "concrete example URLs belong in hidden comments")
                for heading in headings:
                    section = markdown_section(body, f"## {heading}")
                    self.assertRegex(section, TEMPLATE_PLACEHOLDER, f"no placeholder in {heading}")

    def test_templates_have_separate_hidden_guidance_for_every_section(self) -> None:
        for path, headings in WORK_TEMPLATE_HEADINGS.items():
            with self.subTest(template=path.relative_to(ROOT)):
                _, comments = template_parts(self.readRequired(path))
                guidance = "\n".join(
                    comment for comment in comments
                    if not re.search(r"^## ", comment, re.MULTILINE)
                )
                self.assertRegex(guidance, r"(?i)\bpurpose\b")
                self.assertRegex(guidance, r"(?i)\bwhen to use\b")
                for heading in headings:
                    self.assertIn(heading.casefold(), guidance.casefold())

    def test_templates_have_complete_qualified_examples_with_sources(self) -> None:
        for path, headings in WORK_TEMPLATE_HEADINGS.items():
            with self.subTest(template=path.relative_to(ROOT)):
                _, comments = template_parts(self.readRequired(path))
                examples = [
                    comment for comment in comments
                    if re.search(r"^## ", comment, re.MULTILINE)
                ]
                self.assertTrue(examples, "missing separate hidden complete example")
                complete_examples = 0
                for example in examples:
                    self.assertRegex(example, r"(?i)\bexample\b")
                    self.assertRegex(example, r"(?i)\b(?:historical|illustrative)\b")
                    self.assertNotRegex(example, TEMPLATE_PLACEHOLDER)
                    self.assertTrue(MARKDOWN_LINK.findall(example), "example needs a source link")
                    starts = [
                        match.start() for match in re.finditer(
                            rf"^## {re.escape(headings[0])}$", example, re.MULTILINE
                        )
                    ]
                    for start, end in zip(starts, [*starts[1:], len(example)]):
                        variant = example[start:end]
                        if re.findall(r"^## (.+)$", variant, re.MULTILINE) == list(headings):
                            complete_examples += 1
                            for heading in headings:
                                self.assertTrue(markdown_section(variant, f"## {heading}").strip())
                self.assertGreaterEqual(complete_examples, 1, "no example covers every body section")

    def test_issue_example_use_cases_link_acceptance_and_plan(self) -> None:
        """The illustrative example must connect its cases, criteria, and work."""
        _, comments = template_parts(
            self.readRequired(ROOT / ".github/ISSUE_TEMPLATE/work_item.md")
        )
        examples = [
            comment for comment in comments
            if re.search(r"^## Objective$", comment, re.MULTILINE)
        ]
        self.assertTrue(examples, "missing illustrative Issue example")
        for number, example in enumerate(examples, start=1):
            with self.subTest(example=number):
                cases = markdown_section(example, "## Use cases and expected results")
                definitions = re.findall(
                    r"^(?:- )?(?:\*\*)?(UC\d+):[ \t]*(.+)$", cases, re.MULTILINE
                )
                self.assertTrue(definitions, "example needs labeled use-case definitions")
                use_cases = {identifier for identifier, _ in definitions}
                self.assertEqual(len(use_cases), len(definitions), "duplicate use-case ID")
                for identifier, description in definitions:
                    self.assertTrue(description.strip(" *"), f"empty definition for {identifier}")

                criteria = markdown_section(example, "## Acceptance criteria")
                entries = re.findall(
                    r"^- \[[ xX]\] (?:\*\*)?(AC\d+):[ \t]*(.*?)"
                    r"(?=^- \[[ xX]\] |\Z)",
                    criteria,
                    re.MULTILINE | re.DOTALL,
                )
                self.assertTrue(entries, "example needs labeled acceptance checkboxes")
                self.assertEqual(
                    len(entries), len(re.findall(r"^- \[[ xX]\] ", criteria, re.MULTILINE)),
                    "each acceptance checkbox needs an AC label",
                )
                acceptance_cases = {}
                for identifier, criterion in entries:
                    self.assertNotIn(identifier, acceptance_cases, "duplicate acceptance ID")
                    references = set(re.findall(r"\bUC\d+\b", criterion))
                    self.assertTrue(references, f"{identifier} needs a related use case")
                    self.assertLessEqual(references, use_cases, f"unknown use case in {identifier}")
                    acceptance_cases[identifier] = references
                self.assertEqual(
                    set().union(*acceptance_cases.values()), use_cases,
                    "every use case needs acceptance coverage",
                )

                plan = markdown_section(example, "## Implementation plan")
                steps = re.findall(r"^\d+\. (.*?)(?=^\d+\. |\Z)", plan, re.MULTILINE | re.DOTALL)
                self.assertTrue(steps, "example needs ordered implementation steps")
                planned_cases = set()
                for step_number, step in enumerate(steps, start=1):
                    case_refs = set(re.findall(r"\bUC\d+\b", step))
                    criterion_refs = set(re.findall(r"\bAC\d+\b", step))
                    self.assertTrue(case_refs, f"plan step {step_number} needs a use case")
                    self.assertTrue(criterion_refs, f"plan step {step_number} needs acceptance criteria")
                    self.assertLessEqual(case_refs, use_cases, "unknown plan use case")
                    self.assertLessEqual(criterion_refs, acceptance_cases.keys(), "unknown plan criterion")
                    related_cases = set().union(*(acceptance_cases[ref] for ref in criterion_refs))
                    self.assertLessEqual(
                        case_refs, related_cases,
                        "plan use cases must relate to its cited acceptance criteria",
                    )
                    planned_cases.update(case_refs)
                self.assertEqual(planned_cases, use_cases, "every use case needs plan coverage")

    def test_authoring_index_links_all_four_source_templates(self) -> None:
        source = self.readRequired(AUTHORING_PATH)
        self.assertTrue(set(WORK_TEMPLATE_HEADINGS) <= self.localTargets(AUTHORING_PATH, source))

    def test_workflow_and_lifecycle_link_authoring_index(self) -> None:
        for path in (WORKFLOW_PATH, SKILL_PATHS["github-task-lifecycle"]):
            with self.subTest(entrypoint=path.relative_to(ROOT)):
                self.assertIn(AUTHORING_PATH, self.localTargets(path, self.readRequired(path)))

    def test_adaptive_verification_links_evidence_template(self) -> None:
        path = SKILL_PATHS["adaptive-verification"]
        self.assertIn(
            ROOT / ".github/templates/evidence.md",
            self.localTargets(path, self.readRequired(path)),
        )

    def test_template_and_authoring_local_links_resolve(self) -> None:
        for path in (*WORK_TEMPLATE_HEADINGS, AUTHORING_PATH):
            with self.subTest(source=path.relative_to(ROOT)):
                self.localTargets(path, self.readRequired(path))


if __name__ == "__main__":
    unittest.main()
