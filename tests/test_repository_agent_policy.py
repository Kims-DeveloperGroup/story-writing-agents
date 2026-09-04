"""Fast structural contracts for repository-development agent policy."""

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

# Issue #553 approved-base counts and their independent half-size ceilings.
AGENTS_APPROVED_BASE_WORDS = 4_538
RUNBOOK_APPROVED_BASE_WORDS = 2_807
AGENTS_WORD_CEILING = 2_269
RUNBOOK_WORD_CEILING = 1_403


def markdown_section(source: str, heading: str) -> str:
    """Return one Markdown section through the next peer/parent heading."""

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
    """Prevent semantic drift without pinning canonical prose paragraphs."""

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
        cls.workflow_cleanup_source = markdown_section(
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
                "module index",
            ),
            "runtime_implementer": (
                "Require `mode = implementation`; otherwise remain read-only",
                "honor exclusive files",
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
        expected_terms = {
            "requirements_analyst": (
                "run only for substantial ambiguity",
                "Recommend the smallest sufficient evidence",
            ),
            "regression_diagnostician": (
                "run only for an unknown failure",
                "stable family fingerprint",
            ),
            "modularity_maintainer": (
                "recurring failure alone does not create a modularity trigger",
                "all known responsibility holders and consumers",
            ),
            "runtime_implementer": (
                "outside the root fast path",
                "never broaden or launch an unauthorized broad suite",
            ),
            "test_engineer": (
                "fuse planning, test-only edits, authorized exact execution, and final behavioral/coverage review",
                "Never broaden selection, level, concurrency, or budget",
            ),
            "documentation_maintainer": (
                "Keep `AGENTS.md` canonical for adaptive verification",
                "never broaden verification",
            ),
        }
        for name, terms in expected_terms.items():
            instructions = self.agent_data[name]["developer_instructions"]
            with self.subTest(agent=name):
                self.assertTerms(
                    instructions,
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
            "savings >=20 seconds and >=20% of `root_sequential_estimate`",
            "`material_correctness_risk_reduction`/`mandatory_ownership_rule`",
            "Mandatory ownership covers docs, tests, triggered modularity, and production outside root fast path",
            "one/two-file production/config change in an established responsibility/boundary",
            "never docs, tests, required/uncertain modularity, substantial/unresolved ambiguity, or unknown failures",
            "never for a deterministic command",
            "Batch safe root reads",
        )
        self.assertTerms(
            self.runbook_sections["## Route Work on the Critical Path"],
            "[Delegation-Gain Gate](AGENTS.md#delegation-gain-gate)",
            "Enforce exclusive docs/tests/modularity ownership",
            "one production writer",
        )

    def test_root_capability_context_and_complexity_are_canonical(self) -> None:
        section = self.sections["### Root-Capability Routing"]
        rows = {
            match.group("name"): match.group("tokens").strip()
            for match in re.finditer(
                r"^\| `(?P<name>compact|moderate|large|oversized)` "
                r"\| (?P<tokens>.*?) \|$",
                section,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            rows,
            {
                "compact": "<=8,192",
                "moderate": "8,193--32,768",
                "large": "32,769--131,072",
                "oversized": ">131,072, or no verified output/reasoning fit",
            },
        )
        self.assertTerms(
            section,
            "`dispatch_context_tokens` includes only the `task_packet`",
            "`observed|estimated|unavailable`, method, and confidence",
            "Unknown fit is `oversized`; narrow/split",
            "Context cannot relax ceilings",
            "Risk controls effort",
        )

    def test_root_capability_quality_and_role_targets_are_canonical(self) -> None:
        section = self.sections["### Root-Capability Routing"]
        rows = {
            match.group("name"): (
                match.group("trigger").strip(),
                match.group("model").strip(),
                match.group("effort").strip(),
            )
            for match in re.finditer(
                r"^\| `(?P<name>routine|standard|complex|critical)` "
                r"\| (?P<trigger>.*?) \| (?P<model>.*?) "
                r"\| (?P<effort>.*?) \|$",
                section,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            rows,
            {
                "routine": (
                    "mechanical/reversible; no unresolved judgment/material risk",
                    "`gpt-5.6-terra`",
                    "`medium`",
                ),
                "standard": (
                    "bounded semantic work; one established responsibility",
                    "`gpt-5.6-sol`",
                    "`high`",
                ),
                "complex": (
                    "ambiguity/unknown cause; multi-domain/boundary/public API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult nonlocal edges",
                    "`gpt-5.6-sol`",
                    "`xhigh`",
                ),
                "critical": (
                    "two interacting complex triggers, or explicitly high-consequence security/data-loss/irreversible-migration/breaking-compatibility",
                    "`gpt-5.6-sol`",
                    "`max`",
                ),
            },
        )
        self.assertTerms(
            section,
            "requirements/diagnosis for substantial ambiguity/unknown cause",
            "modularity for boundary work (index-only keeps actual class)",
            "runtime for security/concurrency/migration/public API/difficult edges",
            "tests for security/concurrency/PTY/process/lifecycle/complex coverage",
            "docs for normative governance/security/compatibility/migration",
            "Compact/moderate context floors model at Luna; large raises it to Sol",
            "Roles default `routine`",
        )

    def test_root_capability_clamps_and_orders_each_axis_independently(self) -> None:
        section = self.sections["### Root-Capability Routing"]
        code = re.search(r"```text\n(?P<body>.*?)\n```", section, re.DOTALL)
        self.assertEqual(
            tuple(code.group("body").splitlines()) if code else (),
            (
                "desired_model = max(task_model_target, role_model_target, context_model_target)",
                "desired_effort = max(task_effort_target, role_effort_target)",
                "capped_model = min(desired_model, root_effective_model)",
                "capped_effort = min(desired_effort, root_effective_effort)",
            ),
        )
        self.assertTerms(
            section,
            "Derive independently",
            "`gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol`",
            "Effort is `none < low < medium < high < xhigh < max`",
            "each selected axis must also be <=root",
        )

    def test_root_capability_selection_and_inheritance_are_canonical(self) -> None:
        self.assertTerms(
            self.sections["### Root-Capability Routing"],
            "The cap is minimum quality",
            "Choose exact cap, else nearest stronger compatible pair within root",
            "fewest upward steps; tie: smaller model, then effort increase",
            "Never auto-select below cap",
            "Set both overrides or neither",
            "Unresolved root/order/availability/support requires exact inheritance",
            "`ceiling_limited` means either cap is below desired",
            "high-consequence work must narrow/require stronger root",
        )

    def test_root_capability_routing_evidence_is_complete(self) -> None:
        self.assertTerms(
            self.sections["### Root-Capability Routing"],
            "Record root/source; context count/class/method/confidence; complexity/triggers; role target; desired/capped/selected/compatible pairs; limitation; fallback/inheritance reason",
            "exclusions, decisions, observable criteria",
            "`observed|inferred|proposed|unknown`",
            "uncertainty stops",
        )
        self.assertTerms(
            self.runbook_sections["## Route Work on the Critical Path"],
            "independently desired/capped/selected axes",
            "compatibility/ceiling/fallback",
            "Use bounded packets",
        )

    def test_role_mode_capability_matrix_matches_toml_declarations(self) -> None:
        section = self.sections["### Role, Mode, and Control Gate"]
        matrix: dict[str, set[str]] = {}
        capabilities: dict[tuple[str, str], str] = {}
        for match in re.finditer(
            r"^\| `(?P<role>[a-z_]+)` \| (?P<modes>.*?) "
            r"\| (?P<capability>.*?) \|$",
            section,
            flags=re.MULTILINE,
        ):
            role = match.group("role")
            modes = set(
                re.findall(
                    r"`(analysis|implementation|review)`", match.group("modes")
                )
            )
            matrix.setdefault(role, set()).update(modes)
            for mode in modes:
                capabilities[(role, mode)] = match.group("capability")
        toml_modes = {
            role: declared_supported_modes(
                self.agent_data[role]["developer_instructions"]
            )
            for role in EXPECTED_AGENTS
        }
        self.assertEqual(matrix, toml_modes)
        for (role, mode), capability in capabilities.items():
            with self.subTest(role=role, mode=mode):
                if mode in {"analysis", "review"}:
                    self.assertRegex(capability, r"read-only|same read-only")
                else:
                    self.assertEqual(capability, "assigned-worktree writes")

    def test_control_gate_preflight_is_observed_and_fail_closed(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Child first performs only minimum identity/capability preflight",
            "reports filesystem/sandbox, writes, and approval path",
            "Reject unsupported mode, unavailable evidence, or mismatch before other reads/writes",
            "observed fail-closed routing, not isolation enforcement by prompts/TOMLs/tests",
        )

    def test_full_history_exception_is_bounded_and_evidenced(self) -> None:
        section = self.sections["### Role, Mode, and Control Gate"]
        self.assertTerms(
            section,
            "Use bounded self-contained context",
            "Full history requires named reason, bounded relevant scope, stale-context risk, explicit root approval, and all four in task evidence",
        )
        self.assertTerms(
            self.runbook_sections["## Route Work on the Critical Path"],
            "Use bounded packets",
        )

    def test_join_retry_and_partial_writer_controls_are_explicit(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Root records every result ID and role/mode",
            "join only when none is running/missing and every exact `result_packet` validates",
            "Retry a transient spawn/delivery/join failure exactly once",
            "proving no write and recording its fingerprint",
            "Possible writer mutation requires state inspection, diff preservation, and same-thread resume",
            "Policy/capability/scope/schema failures are non-transient",
        )

    def test_conflicts_and_root_fallback_preserve_role_ownership(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Resolve conflicts by `observed > inferred > proposed > unknown` within role ownership, never vote",
            "escalate to owner, root, then user",
            "After failed retry root may only do authorized static reads/fast-path work",
            "never absorbs mandatory ownership or creates a reviewer role",
        )

    def test_runbook_applies_role_mode_and_control_gate(self) -> None:
        section = self.runbook_sections["## Route Work on the Critical Path"]
        self.assertTerms(
            section,
            "[Role, Mode, and Control Gate](AGENTS.md#role-mode-and-control-gate)",
            "Record estimates/formula/percentage and exception",
            "result IDs and roles/modes",
            "cross the join barrier only with all valid results",
            "Retry eligible control failure once",
            "inspect/resume possible partial writers",
            "never bypass ownership or conflict escalation",
        )

    def test_writer_preflight_commands_and_identity_are_exact(self) -> None:
        preflight = self.runbook_sections["## Preflight Every Writer"]
        command_block = re.search(
            r"```bash\n(?P<commands>.*?)\n```", preflight, re.DOTALL
        )
        self.assertIsNotNone(command_block)
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
            "physical `pwd -P`, Git root, branch, exact HEAD, approved-base SHA/ancestry, exclusive targets",
            "exact bytes, SHA-256, and decoded paths",
            "`git status --porcelain=v1 -z --untracked-files=all`",
        )

    def test_writer_preflight_is_pinned_fail_closed_and_repeatable(self) -> None:
        self.assertTerms(
            self.sections["### Role, Mode, and Control Gate"],
            "Accept dirty state only if all three were pinned",
            "Any mismatch stops without reset/checkout/clean/revert",
            "After upstream change, interruption, or invalidated identity, root sends updated identity/fingerprint and the same writer repeats full preflight",
        )
        self.assertTerms(
            self.runbook_sections["## Preflight Every Writer"],
            "Report/match observed capability/approval path, every identity, exact porcelain bytes/hash/decoded paths, and exclusive targets",
            "Dirty state is accepted only when all three were pinned",
            "Any mismatch stops without reset/checkout/clean/revert",
        )

    def test_worktree_dispositions_are_exact_and_authority_bound(self) -> None:
        section = self.sections["### Worktree Disposition"]
        disposition_sentence = section.split("Completion evidence", 1)[0]
        names = tuple(
            name
            for name in re.findall(r"`([a-z_]+)`", disposition_sentence)
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
            "Nest exactly one under task `identity`",
            "`remove_after_integration` only with cleanup authorization",
            "`retain_with_reason` plus reason/next owner",
            "`authorization_required`",
            "Change only after recorded authority/lifecycle change",
        )

    def test_completion_inventory_and_removal_gates_are_complete(self) -> None:
        section = self.sections["### Worktree Disposition"]
        self.assertTerms(
            section,
            "exact path, branch, HEAD, porcelain bytes/hash/paths, Issue/PR state, commit/PR recoverability",
            "cleanup authorization state",
            "actual removal/retention",
            "Removal requires authorization, clean status, completed integration verification, recoverability, non-forced removal, and verified absence from registry/filesystem",
            "Branch deletion is separate",
            "Retain parent until integrated into `main`",
        )
        cleanup = self.runbook_sections["## Dispose or Retain Worktrees"]
        commands = re.search(
            r"```bash\n(?P<commands>.*?)\n```", cleanup, re.DOTALL
        )
        self.assertIsNotNone(commands)
        self.assertEqual(
            tuple(commands.group("commands").splitlines()) if commands else (),
            (
                "git worktree remove <exact-worktree-path>",
                "git worktree list --porcelain",
                "test ! -e <exact-worktree-path>",
            ),
        )

    def test_lifecycle_links_and_packet_nesting_are_canonical(self) -> None:
        self.assertTerms(
            self.workflow_cleanup_source,
            "[AGENTS.md](AGENTS.md#worktree-disposition)",
            "Removal requires clean status, completed integration verification, and recoverability",
            "non-forced and verified absent afterward",
            "Branch deletion is a separate authorized action",
            "Retain the parent worktree until the parent branch is integrated into `main`",
        )
        self.assertTerms(
            self.sections["### Worktree Disposition"],
            "Keep packet schemas unchanged",
            "preflight/disposition under `identity`",
            "cleanup/recoverability under `evidence`",
            "outcome, reason, risk, next owner under `findings`, `risks`, `next_action`",
        )

    def test_specialist_ownership_fusion_and_thread_reuse_are_explicit(self) -> None:
        self.assertTerms(
            self.sections["### Orchestration Sequence"],
            "one production writer at a time",
            "After stable code, modularity owner updates indices",
            "modularity reviews code/indices before tests/docs",
            "test owner exclusively plans/edits/runs/reviews",
            "preferably one turn",
            "pause/resume same thread for dependencies",
            "Docs remain documentation owner's exclusive work",
            "Reuse specialist threads unless stale/isolation risk",
            "specialists neither delegate nor coordinate",
        )

    def test_evidence_levels_are_selectable_and_complete_is_terminal(self) -> None:
        section = self.sections["### Adaptive Verification"]
        first_part = section.split("| `verification_class`", 1)[0]
        level_set = {
            "static_inventory",
            "exact",
            "semantic_cluster",
            "affected_suite",
            "full_suite",
            "complete",
        }
        levels = tuple(
            name for name in re.findall(r"`([a-z_]+)`", first_part) if name in level_set
        )
        self.assertEqual(
            levels,
            (
                "static_inventory",
                "exact",
                "semantic_cluster",
                "affected_suite",
                "full_suite",
                "complete",
            ),
        )
        self.assertTerms(
            first_part,
            "Selectable partial order",
            "`complete` is terminal, not level six",
            "A pass never authorizes the next level",
        )

    def test_verification_class_budgets_and_shard_limits_are_exact(self) -> None:
        rows = {
            match.group("name"): (match.group("budget"), match.group("shard"))
            for match in re.finditer(
                r"^\| `(?P<name>local|bounded|cross_cutting|release)` "
                r"\| (?P<budget>[\w-]+) \| (?P<shard>[\w-]+) \|",
                self.sections["### Adaptive Verification"],
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
        section = self.sections["### Adaptive Verification"]
        self.assertTerms(
            section,
            "criteria-to-`mandatory_evidence`",
            "all changed inputs and affected inventory/exclusions",
            "exact `authorized_selection`/rationale",
            "one `evidence_execution_owner` per item",
            "estimates/source/confidence",
            "total/max/spent/remaining",
            "escalation/full-suite triggers",
            "reuse/invalidation",
            "residual risk",
            "repository, absolute worktree, baseline/HEAD, dirty hash",
            "purpose, level, selection/command, outcome/duration, fingerprint",
        )
        self.assertTerms(
            self.runbook_sections["## Plan and Record Verification"],
            "class/rationale",
            "criteria→mandatory evidence",
            "exact authorized checks/IDs/rationale",
            "estimates/source/confidence",
            "total/max/spent/remaining budget",
        )

    def test_sha_is_provenance_but_digest_controls_reuse(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "conservative `relevant_input_digest`",
            "Reuse after HEAD change only when root proves disjointness and digest/purpose remain",
            "relevant/uncertain changes invalidate it",
        )

    def test_evidence_has_one_owner_and_an_exact_deduplication_key(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "Assign one `evidence_execution_owner` per item",
            "Deduplicate exactly on `normalized_command`, `exact_ids`, `relevant_input_digest`, `environment_class`",
            "owner/label/purpose/venue/wrapper cannot justify rerun",
            "exact/cluster run locally, affected/full in CI",
            "duplicate broad local+CI only for a local-environment criterion",
        )

    def test_shards_are_disjoint_complete_and_resumable(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "deterministic disjoint portable exact-ID shards",
            "union exactly selection, each ID once",
            "Sequential is default",
            "at most two fast-fail shards",
            "manifest digest, ordinal, IDs, input digest, result, duration",
            "`completed_ids`, `failed_ids`, `accepted_skips`, `unexecuted_ids`",
            "Timeout/replacement/interruption is neither pass nor assertion failure",
            "Partial evidence is never complete",
        )

    def test_root_alone_controls_escalation_and_risk_acceptance(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "Root exclusively selects, authorizes, budgets, deduplicates, reuses, escalates, and accepts sufficiency",
            "never broaden level, selection, concurrency, or budget",
            "root accepts residual risk",
            "parallel needs root authorization",
        )
        self.assertTerms(
            self.agent_data["test_engineer"]["developer_instructions"],
            "Execute only evidence assigned to this role using the root plan",
            "Never broaden selection, level, concurrency, or budget",
            "never launch an unauthorized affected or full suite",
        )

    def test_full_suite_has_objective_triggers_and_no_default_gate(self) -> None:
        section = self.sections["### Adaptive Verification"]
        trigger_source = section.split("`full_suite` requires:", 1)[1].split(
            "Implementation/review/suite availability", 1
        )[0]
        triggers = tuple(
            item.removeprefix("or ").strip()
            for item in trigger_source.strip().rstrip(".").split(";")
        )
        self.assertEqual(len(triggers), 6)
        expected_markers = (
            "Issue/parent/release criterion",
            "unbounded static inventory",
            "package-wide/public/cross-domain invariant",
            "global discovery/bootstrap/packaging/harness",
            "unknown out-of-cluster failure",
            "uncovered concrete residual risk",
        )
        for marker in expected_markers:
            with self.subTest(marker=marker):
                self.assertTrue(any(marker in item for item in triggers))
        self.assertTerms(
            section,
            "Implementation/review/suite availability is insufficient",
            "return the decision, never omit/claim completion",
            "Docs/prompt/TOML/policy-only work may use static parsing, exact policy tests, link/format, and diff checks",
        )

    def test_failures_route_without_restarting_broad_verification(self) -> None:
        self.assertTerms(
            self.sections["### Adaptive Verification"],
            "For known failure repair then rerun invalid evidence",
            "one fingerprint occurs in >=2 responsibility holders",
            "stop broad reruns and inventory repository-wide occurrences",
            "Cross-boundary capability/ownership defects apply one modularity gate",
            "Unknown failures go to diagnosis",
            "Unknown cost/exhaustion returns completed/remaining evidence, estimates, risk, and root choices",
        )

    def test_policy_does_not_claim_capability_level_sandbox_enforcement(self) -> None:
        section = self.sections["### Role, Mode, and Control Gate"]
        self.assertTerms(
            section,
            "reports filesystem/sandbox, writes, and approval path",
            "observed fail-closed routing, not isolation enforcement by prompts/TOMLs/tests",
        )
        self.assertNotIn("prompts enforce runtime isolation", normalized(section))

    def test_packet_schemas_are_exact_and_compact(self) -> None:
        section = self.sections["### Handoff and Result Contracts"]
        task_sentence = next(
            line
            for line in section.splitlines()
            if line.startswith("Every compact `task_packet`")
        )
        result_sentence = next(
            line
            for line in section.splitlines()
            if line.startswith("Every compact `result_packet`")
        )
        task_fields = tuple(
            token
            for token in re.findall(r"`([a-z_]+)`", task_sentence)
            if token != "task_packet"
            and token not in {"analysis", "implementation", "review"}
        )
        result_fields = tuple(
            token
            for token in re.findall(r"`([a-z_]+)`", result_sentence)
            if token != "result_packet"
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
        self.assertTerms(
            section,
            "Do not copy raw logs/full Issues/plans",
            "Thread reuse does not authorize history",
        )

    def test_telemetry_is_prospective_and_distinguishes_measurement_kinds(self) -> None:
        section = self.sections["### Prospective Orchestration Telemetry"]
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
        for field in fields:
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", section)
        self.assertTerms(
            section,
            "`observed|estimated|unavailable`",
            "unavailable needs reason",
            "estimates are never observations",
            ">=25% lower median wall time",
            ">=15% lower P95",
            ">=30% fewer turns",
            ">=50% fewer duplicate broad-suite executions",
            "25--35% fewer combined packet tokens",
            "Static policy tests prove shape, not performance",
        )

    def test_runbook_applies_the_canonical_adaptive_policy(self) -> None:
        self.assertTerms(
            self.runbook_sections["## Plan and Record Verification"],
            "[Adaptive Verification](AGENTS.md#adaptive-verification)",
            "one owner/item",
            "exact deduplication key",
            "Ledger each selection/shard's level, purpose, IDs/command, provenance, manifest/input digest, duration/result",
            "Deterministically shard above maximum",
            "Interruptions are not passes/failures",
            "Run `full_suite` only for recorded objective trigger",
            "Complete only with all current mandatory green/accepted-skip evidence",
        )

    def test_compact_policy_files_meet_independent_half_baseline_limits(self) -> None:
        self.assertEqual(
            AGENTS_WORD_CEILING,
            AGENTS_APPROVED_BASE_WORDS // 2,
        )
        self.assertEqual(
            RUNBOOK_WORD_CEILING,
            RUNBOOK_APPROVED_BASE_WORDS // 2,
        )
        counts = {
            "AGENTS.md": len(self.agents_source.split()),
            "AGENT_TASK_RUNBOOK.md": len(self.runbook_source.split()),
        }
        self.assertLessEqual(counts["AGENTS.md"], AGENTS_WORD_CEILING)
        self.assertLessEqual(
            counts["AGENT_TASK_RUNBOOK.md"], RUNBOOK_WORD_CEILING
        )


if __name__ == "__main__":
    unittest.main()
