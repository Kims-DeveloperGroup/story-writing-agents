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
EXPECTED_AGENTS = {
    "documentation_maintainer",
    "modularity_maintainer",
    "regression_diagnostician",
    "requirements_analyst",
    "runtime_implementer",
    "test_engineer",
}
AGENTS_APPROVED_BASE_WORDS = 4_538
AGENTS_WORD_CEILING = 999
SKILL_WORD_CEILING = 800


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


def frontmatter(source: str) -> tuple[str, str]:
    match = re.match(r"\A---\n(?P<body>.*?)\n---\n", source, re.DOTALL)
    if match is None:
        raise AssertionError("missing YAML frontmatter")
    body = match.group("body")
    name = re.search(r"^name: (?P<value>[a-z0-9-]+)$", body, re.MULTILINE)
    description = re.search(
        r"^description: >-\n(?P<value>(?:  .+(?:\n|$))+)",
        body,
        re.MULTILINE,
    )
    if name is None or description is None:
        raise AssertionError("invalid skill name or description frontmatter")
    return name.group("value"), normalized(description.group("value"))


class RepositoryAgentPolicyTests(unittest.TestCase):
    """Prevent policy drift without coupling to full prose paragraphs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.agents_source = AGENTS_PATH.read_text(encoding="utf-8")
        cls.readme_source = README_PATH.read_text(encoding="utf-8")
        cls.workflow_source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.skill_sources = {
            name: path.read_text(encoding="utf-8")
            for name, path in SKILL_PATHS.items()
        }
        cls.routing_source = cls.skill_sources["subagent-routing"]
        cls.selection_source = cls.skill_sources["subagent-model-selection"]
        cls.verification_source = cls.skill_sources["adaptive-verification"]
        cls.lifecycle_source = cls.skill_sources["github-task-lifecycle"]
        cls.routing_sections = {
            heading: markdown_section(cls.routing_source, heading)
            for heading in (
                "## Delegation",
                "## Model Selection",
                "## Control",
                "## Modularity",
                "## Packets",
            )
        }
        cls.selection_sections = {
            heading: markdown_section(cls.selection_source, heading)
            for heading in (
                "## Resolve Inputs",
                "## Classify Work",
                "## Derive and Select",
                "## Record the Decision",
            )
        }
        cls.verification_sections = {
            heading: markdown_section(cls.verification_source, heading)
            for heading in (
                "## Evidence Levels",
                "## Plan and Provenance",
                "## Shards and Interruptions",
                "## Full Suite and Failures",
            )
        }
        cls.lifecycle_sections = {
            heading: markdown_section(cls.lifecycle_source, heading)
            for heading in (
                "## Authorization",
                "## Planning",
                "## Branches",
                "## Integration",
                "## Cleanup",
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
        section = self.routing_sections["## Delegation"]
        code = re.search(r"```text\n(?P<body>.*?)\n```", section, re.DOTALL)
        self.assertEqual(
            code.group("body") if code else None,
            "predicted_critical_path_savings_seconds=root_sequential_estimate-"
            "(parallel_critical_path+spawn_wait_handoff_synthesis_overhead)",
        )
        self.assertTerms(
            section,
            "savings >=20 seconds and >=20% of `root_sequential_estimate`",
            "`material_correctness_risk_reduction`",
            "`mandatory_ownership_rule`",
            "Exceptions authorize, not claim savings",
            "Mandatory owners cover documentation, tests, triggered modularity, and production outside fast path",
            "one- or two-file production/configuration changes only within established responsibility/boundary",
            "Exclude documentation, tests, modularity, substantial ambiguity, and unknown failures",
            "Never dispatch for a deterministic command",
        )

    def test_root_capability_context_and_complexity_are_canonical(self) -> None:
        section = self.selection_sections["## Resolve Inputs"]
        expected_rows = (
            "|`compact`|<=8,192|",
            "|`moderate`|8,193--32,768|",
            "|`large`|32,769--131,072|",
            "|`oversized`|>131,072 or no verified output/reasoning fit|",
        )
        for row in expected_rows:
            with self.subTest(row=row):
                self.assertIn(row, section)
        self.assertTerms(
            section,
            "`dispatch_context_tokens` counts only the task packet, intentionally inherited turns, attachments, preloads, and irreducible child material",
            "`observed|estimated|unavailable`, method, and confidence",
            "Unknown fit is oversized. Narrow or split it without relaxing root ceilings",
        )

    def test_root_capability_quality_and_role_targets_are_canonical(self) -> None:
        section = self.selection_sections["## Classify Work"]
        expected_rows = (
            "|`routine`|Mechanical/reversible; no unresolved judgment/material risk|`gpt-5.6-terra`|`medium`|",
            "|`standard`|Bounded semantics; one established responsibility|`gpt-5.6-sol`|`high`|",
            "|`complex`|Ambiguity/unknown cause; multi-domain/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult nonlocal edges|`gpt-5.6-sol`|`xhigh`|",
            "|`critical`|Two interacting complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility|`gpt-5.6-sol`|`max`|",
            "|Requirements/diagnosis|Substantial ambiguity/unknown cause|",
            "|Modularity|Boundaries|",
            "|Runtime|Security/concurrency/migration/API/difficult nonlocal edges|",
            "|Tests|Security/concurrency/PTY/process/lifecycle/complex coverage|",
            "|Documentation|Normative governance/security/compatibility/migration|",
        )
        for row in expected_rows:
            with self.subTest(row=row):
                self.assertIn(row, section)
        self.assertTerms(
            section,
            "Roles default routine; index-only work keeps its actual class",
            "Context affects only model: compact/moderate floor Luna; large floors Sol",
            "Complexity and risk alone control effort",
        )

    def test_root_capability_clamps_and_orders_each_axis_independently(self) -> None:
        section = self.selection_sections["## Derive and Select"]
        blocks = re.findall(r"```text\n(?P<body>.*?)\n```", section, re.DOTALL)
        self.assertEqual(
            blocks[0].splitlines(),
            [
                "desired_model=max(task_model_target,role_model_target,context_model_target)",
                "desired_effort=max(task_effort_target,role_effort_target)",
                "capped_model=min(desired_model,root_effective_model)",
                "capped_effort=min(desired_effort,root_effective_effort)",
            ],
        )
        self.assertTerms(
            section,
            "`gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol`",
            "assume no other model order",
            "Order effort `none < low < medium < high < xhigh < max`",
            "every selected axis must stay <=root",
        )

    def test_root_capability_selection_and_inheritance_are_canonical(self) -> None:
        self.assertTerms(
            self.selection_sections["## Derive and Select"],
            "Select the exact cap, otherwise the nearest stronger compatible pair within root",
            "Minimize upward steps; ties prefer smaller model, then effort increase",
            "Never select below cap. Weaker-only availability returns to root",
            "Treat caps as minimum quality",
            "Set both overrides or neither",
            "Unresolved root/order/availability/support requires exact inheritance",
            "model-only selection may default effort",
            "Define `ceiling_limited` when either cap is below desired",
            "High-consequence work must narrow or require a stronger root",
        )

    def test_root_capability_routing_evidence_is_complete(self) -> None:
        self.assertTerms(
            self.selection_sections["## Record the Decision"],
            "Record root/source; context count/class/method/confidence; complexity/triggers; role target; desired/capped/selected/compatible pairs; limitation; and fallback/inheritance reason",
            "exclusions, accepted decisions, observable criteria",
            "`observed|inferred|proposed|unknown` evidence",
            "uncertainty stops",
        )

    def test_model_selection_precedence_and_enforcement_limits_are_exact(self) -> None:
        section = self.selection_sections["## Derive and Select"]
        self.assertTerms(
            section,
            "Explicit spawn model/effort values implement the selection",
            "file-level `model` or `model_reasoning_effort` takes precedence over explicit spawn selection",
            "Keep role TOMLs unpinned",
            "OpenAI does not natively enforce this task classification or child<=root policy",
            "A full-history fork cannot take model/effort overrides",
            "Dynamic selection must use no or bounded inherited history",
            "otherwise inherit both axes and record the reason",
        )
        for definition in self.agent_data.values():
            self.assertNotIn("model", definition)
            self.assertNotIn("model_reasoning_effort", definition)

    def test_model_selection_contract_is_exclusive_to_selector(self) -> None:
        selector_only_terms = (
            "dispatch_context_tokens",
            "|`compact`|<=8,192|",
            "|`moderate`|8,193--32,768|",
            "|`large`|32,769--131,072|",
            "|`oversized`|>131,072 or no verified output/reasoning fit|",
            "|`routine`|Mechanical/reversible; no unresolved judgment/material risk|`gpt-5.6-terra`|`medium`|",
            "|`standard`|Bounded semantics; one established responsibility|`gpt-5.6-sol`|`high`|",
            "|`complex`|Ambiguity/unknown cause; multi-domain/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult nonlocal edges|`gpt-5.6-sol`|`xhigh`|",
            "|`critical`|Two interacting complex triggers",
            "desired_model=max(task_model_target,role_model_target,context_model_target)",
            "desired_effort=max(task_effort_target,role_effort_target)",
            "capped_model=min(desired_model,root_effective_model)",
            "capped_effort=min(desired_effort,root_effective_effort)",
            "gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol",
            "none < low < medium < high < xhigh < max",
            "ceiling_limited",
            "nearest stronger compatible pair within root",
            "Weaker-only availability returns to root",
            "Set both overrides or neither",
            "Unresolved root/order/availability/support requires exact inheritance",
            "file-level `model` or `model_reasoning_effort` takes precedence",
            "OpenAI does not natively enforce this task classification or child<=root policy",
            "A full-history fork cannot take model/effort overrides",
            "Dynamic selection must use no or bounded inherited history",
            "desired/capped/selected/compatible pairs",
        )
        self.assertTerms(self.selection_source, *selector_only_terms)
        for term in selector_only_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, self.routing_source)
        self.assertTerms(
            self.routing_sections["## Model Selection"],
            "Load `$subagent-model-selection` before any dispatch model, effort, or context decision",
            "That skill exclusively defines the algorithm",
        )

    def test_role_mode_capability_matrix_matches_toml_declarations(self) -> None:
        section = self.routing_sections["## Control"]
        expected_rows = (
            "|Requirements/diagnosis|`analysis`|RO|",
            "|`modularity_maintainer`|`analysis`,`review`|RO|",
            "|Modularity/runtime/tests/docs|`implementation`|RW|",
            "|Tests/docs|`analysis`|RO|",
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
            self.routing_sections["## Control"],
            "RO means read-only with no approval-enabled writes",
            "RW means assigned-worktree writes",
            "Child first reports filesystem/sandbox, write access, approval path, and identity",
            "Reject unsupported mode, unavailable evidence, or mismatch before other work",
            "Observations route fail-closed",
            "prompts, TOMLs, and tests cannot enforce isolation",
        )

    def test_full_history_exception_is_bounded_and_evidenced(self) -> None:
        self.assertTerms(
            self.routing_sections["## Control"],
            "Full history requires named reason, bounded scope, stale-risk assessment, root approval",
            "record all four in task evidence",
        )

    def test_join_retry_and_partial_writer_controls_are_explicit(self) -> None:
        self.assertTerms(
            self.routing_sections["## Control"],
            "Pre-record result IDs and role/mode",
            "Join only when none runs/is missing and every exact result validates",
            "Retry transient spawn/delivery/join failure once after no-write proof/fingerprint",
            "Possible writes require state inspection, preserved diff, and same-thread resume",
            "Never retry policy/capability/scope/schema failures",
        )

    def test_conflicts_and_root_fallback_preserve_role_ownership(self) -> None:
        self.assertTerms(
            self.routing_sections["## Control"],
            "After failed retry, root may perform only authorized static reads or fast-path work",
            "never absorb mandatory ownership",
            "Resolve conflicts by `observed > inferred > proposed > unknown` within role ownership",
            "never vote",
            "Escalate owner, root, then user",
        )

    def test_routing_skill_applies_role_mode_and_control_gate(self) -> None:
        self.assertTerms(
            self.agents_source,
            "Load `$subagent-routing` before evaluating or dispatching any subagent",
            "Load `$subagent-routing` before preflight, handoff, join, retry, interruption, or conflict handling",
            "Load `$subagent-routing` before creating or validating any `task_packet` or `result_packet`",
        )

    def test_model_selector_load_boundaries_are_exact(self) -> None:
        self.assertTerms(
            self.agents_source,
            "Load `$subagent-model-selection` before choosing, requesting, overriding, or inheriting a child context, model, or reasoning effort",
        )
        self.assertTerms(
            self.routing_sections["## Model Selection"],
            "Load `$subagent-model-selection` before any dispatch model, effort, or context decision",
            "That skill exclusively defines the algorithm",
        )

    def test_writer_preflight_commands_and_identity_are_exact(self) -> None:
        blocks = re.findall(
            r"```bash\n(?P<body>.*?)\n```",
            self.lifecycle_sections["## Branches"],
            re.DOTALL,
        )
        preflight = next(block for block in blocks if block.startswith("pwd -P"))
        self.assertEqual(
            tuple(preflight.splitlines()),
            (
                "pwd -P",
                "git rev-parse --show-toplevel",
                "git branch --show-current",
                "git rev-parse HEAD",
                'git merge-base HEAD "$approved_base_oid"',
                'git merge-base --is-ancestor "$approved_base_oid" HEAD',
                "git status --porcelain=v1 -z --untracked-files=all | od -An -tx1",
                "git status --porcelain=v1 -z --untracked-files=all | shasum -a 256",
            ),
        )
        self.assertTerms(
            self.lifecycle_sections["## Branches"],
            "Match capability, identities, ancestry, targets, and exact porcelain bytes, SHA-256, and decoded paths",
        )

    def test_writer_preflight_is_pinned_fail_closed_and_repeatable(self) -> None:
        self.assertTerms(
            self.routing_sections["## Control"],
            "Writers match physical path, Git root, branch, HEAD, base/ancestry, targets, and porcelain bytes/SHA-256/paths",
            "Pin dirty state",
            "never reset, checkout, clean, or revert",
            "Re-preflight after upstream changes, interruption, or invalidation",
        )
        self.assertTerms(
            self.lifecycle_sections["## Branches"],
            "Match capability, identities, ancestry, targets, and exact porcelain bytes, SHA-256, and decoded paths",
            "Never reset, checkout, clean, or revert to match",
        )

    def test_worktree_dispositions_are_exact_and_authority_bound(self) -> None:
        control = self.routing_sections["## Control"]
        self.assertTerms(
            control,
            "Task `identity` contains one disposition",
            "`remove_after_integration` requires cleanup authority",
            "`retain_with_reason` includes reason/next owner",
            "`authorization_required`",
            "Change only after recorded authority/lifecycle change",
            "follow `$github-task-lifecycle`",
        )
        self.assertTerms(
            self.agents_source,
            "Record exactly one disposition under task `identity`",
            "`remove_after_integration`",
            "`retain_with_reason`",
            "`authorization_required` when removal authority is absent or unresolved",
        )

    def test_completion_inventory_and_removal_gates_are_complete(self) -> None:
        cleanup = self.lifecycle_sections["## Cleanup"]
        self.assertTerms(
            cleanup,
            "Record worktree, branch, HEAD, porcelain bytes/hash/paths, Issue/PR state, SHAs, recoverability, cleanup authority, outcome, reason, and next owner",
            "Never clean automatically",
            "authorized, clean, integrated, recoverable worktree whose disposition permits removal",
            "Never force removal",
            "Branch deletion needs separate authorization",
            "Retain the parent worktree until its branch reaches `main`",
        )
        commands = re.search(r"```bash\n(?P<body>.*?)\n```", cleanup, re.DOTALL)
        self.assertEqual(
            tuple(commands.group("body").splitlines()) if commands else (),
            (
                'git worktree remove "$worktree"',
                "git worktree list --porcelain",
                'test ! -e "$worktree"',
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
            self.routing_sections["## Packets"],
            "Nest preflight/disposition under `identity`",
            "cleanup/recoverability under `evidence`",
            "disposition results in their fields",
        )

    def test_specialist_ownership_fusion_and_thread_reuse_are_explicit(self) -> None:
        self.assertTerms(
            markdown_section(self.agents_source, "### Orchestration Sequence"),
            "exactly one production owner at a time",
            "reconcile indices index-only before boundary review",
            "Run tests preferably in one turn; pause/resume that thread",
            "Run documentation after tests",
            "index-only implementation, then read-only review",
        )
        self.assertTerms(
            self.routing_sections["## Modularity"],
            "Run necessary nonoverlapping owners sequentially",
            "After runtime implements a brief, modularity reviews code/indices before tests/docs",
            "Return deviations/drift to their owners until resolved or accepted",
            "Reuse threads unless stale/unsafe",
        )

    def test_evidence_levels_are_selectable_and_complete_is_terminal(self) -> None:
        section = self.verification_sections["## Evidence Levels"]
        expected = (
            "static_inventory",
            "exact",
            "semantic_cluster",
            "affected_suite",
            "full_suite",
            "complete",
        )
        observed = tuple(
            name for name in re.findall(r"`([a-z_]+)`", section) if name in expected
        )
        self.assertEqual(observed[:6], expected)
        self.assertTerms(
            section,
            "`complete` is terminal, not level six",
            "current green or accepted-skip mandatory evidence",
            "resolved failures, sufficient inventory, and root-accepted risk",
            "A pass never requires or authorizes escalation",
        )

    def test_verification_class_budgets_and_shard_limits_are_exact(self) -> None:
        section = self.verification_sections["## Evidence Levels"]
        expected_rows = (
            "|`local`|300|180|Inventory plus exact or static|",
            "|`bounded`|900|300|Inventory, exact, required semantic|",
            "|`cross_cutting`|1800|600|Inventory plus selected cluster or affected|",
            "|`release`|Issue-defined|Issue-defined|Named integration or release gates|",
        )
        for row in expected_rows:
            with self.subTest(row=row):
                self.assertIn(row, section)

    def test_root_plan_and_result_fields_are_identifiable(self) -> None:
        plan = self.verification_sections["## Plan and Provenance"]
        self.assertTerms(
            plan,
            "class and risk rationale; each criterion mapped to `mandatory_evidence`",
            "changed source, tests, fixtures, configuration, policy, documentation, and environment",
            "direct tests, transitive consumers, helpers, fixtures, invariants, cross-boundary consumers, and justified exclusions",
            "flags for API, packaging, persistence, migration, authorization, security, concurrency, process, PTY, shared configuration, and uncertain dynamic coupling",
            "exact `authorized_selection`, rationale, owner, estimates/source/confidence, total, maximum, spent, remaining, triggers, stops, reuse/invalidation, and residual risk",
            "repository, absolute worktree, baseline, HEAD, dirty fingerprint, environment capabilities, purpose, level, exact selection and command, outcome, duration, and stable failure fingerprint",
        )

    def test_sha_is_provenance_but_digest_controls_reuse(self) -> None:
        self.assertTerms(
            self.verification_sections["## Plan and Provenance"],
            "conservative `relevant_input_digest` covers selected tests, shared helpers and fixtures, runner configuration, transitive production closure, and relevant environment",
            "Reuse after HEAD changes only when root proves disjointness and unchanged digest and purpose",
            "Relevant or uncertain changes invalidate evidence",
            "renaming purpose does not",
        )

    def test_evidence_has_one_owner_and_an_exact_deduplication_key(self) -> None:
        self.assertTerms(
            self.verification_sections["## Plan and Provenance"],
            "Assign one `evidence_execution_owner` per item",
            "Deduplicate exactly on `normalized_command`, `exact_ids`, `relevant_input_digest`, and `environment_class`",
            "Different owner, label, purpose, venue, or wrapper never justifies rerunning equivalents",
            "Normally run exact and cluster locally, affected and full in CI",
            "Duplicate broad local and CI evidence only for an environment criterion",
        )

    def test_shards_are_disjoint_complete_and_resumable(self) -> None:
        self.assertTerms(
            self.verification_sections["## Shards and Interruptions"],
            "deterministic, disjoint, portable exact IDs",
            "union equals selection and each ID appears once",
            "Default sequentially",
            "root authorization, isolation, nonoverlap, and runner support",
            "At most two fast-fail shards",
            "manifest digest, ordinal, exact IDs, input digest, result, duration",
            "`completed_ids`, `failed_ids`, `accepted_skips`, and `unexecuted_ids`",
            "interruption is neither pass nor assertion failure",
            "Resume only valid unexecuted or invalidated IDs",
            "Partial evidence never completes work",
        )

    def test_root_alone_controls_escalation_and_risk_acceptance(self) -> None:
        self.assertTerms(
            self.verification_source,
            "Root owns selection, authorization, budgets, deduplication, reuse, escalation, and sufficiency",
            "Specialists only propose; they never broaden level, selection, concurrency, or budget",
            "root-accepted risk",
        )
        self.assertTerms(
            self.agent_data["test_engineer"]["developer_instructions"],
            "Execute only evidence assigned to this role using the root plan",
            "Never broaden selection, level, concurrency, or budget",
            "never launch an unauthorized affected or full suite",
        )

    def test_full_suite_has_objective_triggers_and_no_default_gate(self) -> None:
        section = self.verification_sections["## Full Suite and Failures"]
        triggers = (
            "explicit Issue, parent-integration, or release criterion",
            "static inventory that cannot conservatively bound the affected set",
            "changed package-wide, public, or cross-domain invariant",
            "global discovery, bootstrap, packaging, or harness behavior making narrower evidence unreliable",
            "unknown out-of-cluster failure family",
            "concrete residual risk not covered by affected selections",
        )
        for trigger in triggers:
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, normalized(section))
        self.assertTerms(
            section,
            "Implementation, final review, or suite availability is insufficient",
            "never omit it or claim completion",
            "Prompt, TOML, policy, or documentation-only work may use static parsing, exact policy tests, link and format checks, and diff inspection",
        )

    def test_failures_route_without_restarting_broad_verification(self) -> None:
        self.assertTerms(
            self.verification_sections["## Full Suite and Failures"],
            "Repair a known family and rerun exact invalidated evidence",
            "one stable fingerprint appears in >=2 responsibility holders, stop broad reruns",
            "Inventory repository-wide occurrences with roots, exclusions, patterns, classifications, and limitations",
            "modularity gate once for cross-boundary ownership or capability defects",
            "unknown failures to `regression_diagnostician`",
            "On unknown cost or exhaustion, return completed and mandatory remaining evidence, revised estimates, residual risk, and root choices",
        )

    def test_policy_does_not_claim_capability_level_sandbox_enforcement(self) -> None:
        section = self.routing_sections["## Control"]
        self.assertTerms(
            section,
            "Child first reports filesystem/sandbox, write access, approval path, and identity",
            "Observations route fail-closed",
            "prompts, TOMLs, and tests cannot enforce isolation",
        )

    def test_packet_schemas_are_exact_and_compact(self) -> None:
        section = self.routing_sections["## Packets"]
        task_fields = tuple(
            re.findall(r"^\|`([a-z_]+)`\|", section.split("|`result_packet` field|", 1)[0], re.MULTILINE)
        )
        result_fields = tuple(
            re.findall(r"^\|`([a-z_]+)`\|", section.split("|`result_packet` field|", 1)[1], re.MULTILINE)
        )
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
            (
                "outcome",
                "findings",
                "changed_inputs",
                "evidence",
                "risks",
                "next_action",
            ),
        )
        self.assertTerms(section, "Exclude raw logs and full Issues/plans")

    def test_telemetry_is_prospective_and_distinguishes_measurement_kinds(self) -> None:
        section = self.routing_sections["## Packets"]
        expected_fields = (
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
        self.assertIn(",".join(expected_fields), section)
        self.assertTerms(
            section,
            "`observed|estimated|unavailable`",
            "explain unavailable",
            "estimates never count as observations",
            "median >=25% lower",
            "P95 >=15% lower",
            "turns >=30% fewer",
            "duplicate broad suites >=50% fewer",
            "packet tokens 25--35% fewer",
            "Static tests prove no performance",
        )

    def test_verification_skill_applies_canonical_adaptive_policy(self) -> None:
        self.assertTerms(
            self.agents_source,
            "Load `$adaptive-verification` before planning, executing, reusing, escalating, or accepting evidence for any nontrivial tracked change",
            "Root alone authorizes selection, budget, concurrency, reuse, escalation, and final sufficiency",
        )
        self.assertTerms(
            self.verification_source,
            "A pass never requires or authorizes escalation",
            "Deduplicate exactly on",
            "Partial evidence never completes work",
            "Authorize `full_suite` only for",
        )

    def test_compact_policy_files_meet_independent_half_baseline_limits(self) -> None:
        count = len(self.agents_source.split())
        self.assertEqual(AGENTS_WORD_CEILING, 999)
        self.assertLessEqual(count, AGENTS_WORD_CEILING)
        self.assertLessEqual(AGENTS_WORD_CEILING, AGENTS_APPROVED_BASE_WORDS // 2)

    def test_policy_skill_roster_frontmatter_and_word_limits_are_exact(self) -> None:
        self.assertEqual(set(SKILL_PATHS), set(self.skill_sources))
        headings = {
            "adaptive-verification": "# Adaptive Verification",
            "github-task-lifecycle": "# GitHub Task Lifecycle",
            "subagent-model-selection": "# Subagent Model Selection",
            "subagent-routing": "# Subagent Routing",
        }
        for name, source in self.skill_sources.items():
            with self.subTest(skill=name):
                declared_name, description = frontmatter(source)
                self.assertEqual(declared_name, name)
                self.assertTrue(description)
                self.assertLessEqual(len(source.split()), SKILL_WORD_CEILING)
                self.assertIn(headings[name], source)

    def test_agents_loads_skills_at_positive_and_negative_boundaries(self) -> None:
        expected_descriptions = {
            "subagent-model-selection": (
                "Use before selecting, requesting, overriding, or inheriting a subagent model, reasoning effort, or dispatch context",
                "Excludes root-session configuration and story-runtime agents",
            ),
            "subagent-routing": (
                "Use for subagent evaluation, dispatch, join, retry, or acceptance",
                "Excludes story agents and root-only commands",
            ),
            "adaptive-verification": (
                "Use before planning, running, reusing, escalating, or accepting evidence for nontrivial tracked changes",
                "Excludes untracked conversation and story-runtime execution",
            ),
            "github-task-lifecycle": (
                "Use for GitHub-tracked planning, inspection, Issues, Projects, branches, worktrees, PRs, integration/cleanup",
                "Excludes untracked and story-runtime work",
            ),
        }
        for name, terms in expected_descriptions.items():
            _, description = frontmatter(self.skill_sources[name])
            with self.subTest(skill=name):
                self.assertTerms(description, *terms)
        self.assertTerms(
            self.agents_source,
            "Load `$github-task-lifecycle` for every GitHub-tracked task",
            "Load `$subagent-routing` before evaluating or dispatching any subagent",
            "Load `$subagent-model-selection` before choosing, requesting, overriding, or inheriting a child context, model, or reasoning effort",
            "Load `$adaptive-verification` before planning, executing, reusing, escalating, or accepting evidence for any nontrivial tracked change",
        )
        self.assertTerms(
            self.readme_source,
            "[`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md)",
        )

    def test_removed_runbook_is_absent_and_unreferenced(self) -> None:
        removed_name = "AGENT_TASK_" + "RUNBOOK.md"
        self.assertFalse((ROOT / removed_name).exists())
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.split(b"\0")
        needle = removed_name.encode()
        references = []
        for raw_path in tracked:
            if not raw_path:
                continue
            path = ROOT / raw_path.decode()
            if path.is_file() and needle in path.read_bytes():
                references.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(references, [])

    def test_modularity_triggers_missing_policy_index_and_review_loop(self) -> None:
        root_section = markdown_section(self.agents_source, "### Modularity Gate")
        self.assertTerms(
            root_section,
            "importable structure changes",
            "redistributed feature, state, resource, or lifecycle ownership",
            "dependency-direction or cycle changes",
            "private cross-boundary access",
            "material shared abstractions, adapters, facades, protocols, exports, public imports, multi-consumer extraction, structural migration, or architectural coupling defects",
            "Exclude localized unchanged-contract refactors, tests, documentation, formatting, comments, typing, ordinary configuration or data, dependency metadata, and story runs",
            "Use `analysis` before writers",
            "exclusive `implementation` for architecture-dominant work",
            "`review` after another owner changes a boundary",
        )
        self.assertTerms(
            self.routing_sections["## Modularity"],
            "Missing policy: report exact language/path",
            "root asks about packaged creation, resumes this thread",
            "records any declined-decision general-judgment exception",
            "Missing index needs no separate permission",
            "Derive full inventory/path/content, then create it index-only in this thread",
            "Structural work never completes with drift",
            "Return deviations/drift to their owners until resolved or accepted",
        )

    def test_github_authorization_planning_and_relationships_are_exact(self) -> None:
        authority = markdown_section(self.workflow_source, "## Authority and Authorization")
        gate = markdown_section(self.workflow_source, "## Initial Planning Gate")
        planning = self.lifecycle_sections["## Planning"]
        self.assertTerms(
            authority,
            "explicit implementation request also authorizes only the initial-planning mutations",
            "create the in-scope task branch through the Issue's Development section after the gate passes",
            "staging, commits, other pushes, PR actions, review, closure, merge, deletion, and cleanup still require explicit authorization",
            "Worktree removal and branch deletion are distinct actions",
        )
        self.assertTerms(
            gate,
            "`github_planning_gate = not_required | pending | passed | blocked`",
            "Only read-only analysis may continue while it is pending",
            "Large work needs one root and all known descendants",
            "native hierarchy and dependencies",
            "Project Status and Iteration on all items",
            "preserve IDs, repair only missing state, and set `blocked`",
        )
        self.assertTerms(
            planning,
            "Search all Issue states; draft root/descendants; create/reuse root first",
            "one logical Iteration tree-wide",
            "Priority through the organization Issue field",
            "Create/re-read native relationships",
            "complete hierarchy/dependencies, canonical Project membership/fields",
            "unblocked `Ready` leaf",
        )

    def test_github_branch_status_integration_and_cleanup_are_exact(self) -> None:
        branches = self.lifecycle_sections["## Branches"]
        integration = self.lifecycle_sections["## Integration"]
        status = markdown_section(self.workflow_source, "## Project Status")
        self.assertTerms(
            branches,
            "Create branch only through Issue Development",
            "Stop on an unlinked existing branch",
            "Never recreate, rename, replace, rebase, or force-update",
            "one worktree per branch",
            "Base children only on clean, current, GitHub-present parent commits",
            "Verify Development and Project links",
        )
        self.assertTerms(
            status,
            "`In progress`",
            "assignee and a Development-linked branch or PR exist",
            "Draft PRs remain here",
            "`In review`",
            "Project `Linked pull requests`",
            "Blocked is an overlay, not a status",
            "Blocked work never becomes `Done`",
        )
        self.assertTerms(
            integration,
            "Parent accepts only authorized child squashes at its current tip and approved-main sync",
            "Merge parent into child without rebase",
            "Ready/`In review` requires complete evidence plus verified Development and Project PR links",
            "fast-forward clean parent with `--ff-only`",
            "manually close and verify `Done`",
            "Never rebase, force-push, auto-merge, or auto-close",
            "Separately authorize these mutations",
            "drafts remain there",
            "Reopen failed closed work to `Backlog` and replan",
        )


if __name__ == "__main__":
    unittest.main()
