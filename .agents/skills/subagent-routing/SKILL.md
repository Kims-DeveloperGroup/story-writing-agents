---
name: subagent-routing
description: >-
  Use for subagent evaluation, dispatch, join, retry, or acceptance. Excludes story agents
  and root-only commands.
---

# Subagent Routing

[AGENTS.md](../../../AGENTS.md) is authoritative; never override it.

## Delegation

Calculate recorded estimates:

```text
predicted_critical_path_savings_seconds=root_sequential_estimate-(parallel_critical_path+spawn_wait_handoff_synthesis_overhead)
```

- Require savings >=20 seconds and >=20% of `root_sequential_estimate`, or name
  `material_correctness_risk_reduction` or `mandatory_ownership_rule`.
- Exceptions authorize, not claim savings. Mandatory owners cover documentation, tests,
  triggered modularity, and production outside fast path.
- Root fast path permits one- or two-file production/configuration changes only within
  established responsibility/boundary.
- Exclude documentation, tests, modularity, substantial ambiguity, and unknown failures.
  Never dispatch for a deterministic command.
- Use requirements only for substantial ambiguity, diagnosis only for unknown failure, and
  modularity only when triggered. Batch safe reads.

## Routing

Resolve root model/effort after aliases, defaults, overrides, and child settings/pairs;
TOMLs remain unpinned.

`dispatch_context_tokens` counts only packet, intentional history, attachments, preloads,
and irreducible child material. Record `observed|estimated|unavailable`, method, confidence.

|Context|Tokens|
|---|---:|
|`compact`|<=8,192|
|`moderate`|8,193--32,768|
|`large`|32,769--131,072|
|`oversized`|>131,072 or unverified fit|

Unknown fit is oversized; narrow/split within root ceilings.

|Class|Trigger|Model|Effort|
|---|---|---|---|
|`routine`|Mechanical/reversible; no unresolved judgment/material risk|`gpt-5.6-terra`|`medium`|
|`standard`|Bounded semantics; one responsibility|`gpt-5.6-sol`|`high`|
|`complex`|Ambiguity/unknown cause; multi-domain/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult nonlocal edges|`gpt-5.6-sol`|`xhigh`|
|`critical`|Two interacting complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility|`gpt-5.6-sol`|`max`|

Roles default routine; index-only keeps actual class.

|Role|Complex uplift|
|---|---|
|Requirements/diagnosis|Substantial ambiguity/unknown cause|
|Modularity|Boundaries|
|Runtime|Security/concurrency/migration/API/nonlocal edges|
|Tests|Security/concurrency/PTY/process/lifecycle/complex coverage|
|Documentation|Governance/security/compatibility/migration|

Compact/moderate floor Luna; large floors Sol. Risk controls effort.

```text
desired_model=max(task_model_target,role_model_target,context_model_target)
desired_effort=max(task_effort_target,role_effort_target)
capped_model=min(desired_model,root_effective_model)
capped_effort=min(desired_effort,root_effective_effort)
```

- Compare only `gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol`; assume no other order. Effort
  is `none < low < medium < high < xhigh < max`; use runtime-comparable/selectable `ultra`.
- Select exact cap, else nearest stronger compatible pair within root. Minimize upward
  steps; ties prefer smaller model, then effort increase. Weaker-only returns to root.
- Caps are minimum quality; selected axes stay <=root.
- Set both overrides or neither. Unresolved root/order/availability/support requires exact
  inheritance; model-only may default effort.
- `ceiling_limited` means either cap is below desired. High-consequence work must narrow or
  require stronger root; otherwise proceed compatibly only with recorded residual risk.
- Record root/source; context count/class/method/confidence; complexity/triggers; role target;
  desired/capped/selected/compatible pairs; limitation; fallback/inheritance reason.
- Packets include exclusions, decisions, observable criteria,
  `observed|inferred|proposed|unknown` evidence, and uncertainty stops.

## Control

|Role|Mode|Capability|
|---|---|---|
|Requirements/diagnosis|`analysis`|RO|
|`modularity_maintainer`|`analysis`,`review`|RO|
|Modularity/runtime/tests/docs|`implementation`|RW|
|Tests/docs|`analysis`|RO|

- RO means read-only with no approval-enabled writes; RW means assigned-worktree writes.
  Child first reports filesystem/sandbox, write access, approval path, and identity.
- Reject unsupported mode, unavailable evidence, or mismatch before other work. Observations
  route fail-closed; prompts, TOMLs, and tests cannot enforce isolation.
- Writers match physical path, Git root, branch, HEAD, base/ancestry, targets, and porcelain
  bytes/SHA-256/paths. Pin dirty state; never reset, checkout, clean, or revert. Re-preflight
  after upstream changes, interruption, or invalidation.
- Full history requires named reason, bounded scope, stale-risk assessment, root approval;
  record all four in task evidence.
- Pre-record result IDs and role/mode. Join only when none runs/is missing and every exact
  result validates.
- Retry transient spawn/delivery/join failure once after no-write proof/fingerprint.
  Possible writes require state inspection, preserved diff, and same-thread resume.
- Never retry policy/capability/scope/schema failures. After failed retry, root may perform
  only authorized static reads or fast-path work; never absorb mandatory ownership.
- Resolve conflicts by `observed > inferred > proposed > unknown` within role ownership;
  never vote. Escalate owner, root, then user for required external decisions.

Task `identity` contains one disposition: `remove_after_integration` requires cleanup
authority; `retain_with_reason` includes reason/next owner; or `authorization_required`.
Change only after recorded authority/lifecycle change. For tracked work,
follow `$github-task-lifecycle`.

## Modularity

- Stop unexpected modularity triggers before writing. Modes are pre-writer `analysis`,
  architecture-dominant `implementation`, and post-writer `review`.
- Missing policy: report exact language/path; root asks about packaged creation, resumes this
  thread, and records any declined-decision general-judgment exception.
- Missing index needs no separate permission. Derive full inventory/path/content, then create it
  index-only in this thread. Structural work never completes with drift.
- Root accepts analysis and any brief/index delta, passes planning, then assigns one
  production owner. Run necessary nonoverlapping owners sequentially; avoid invalid state.
- After runtime implements a brief, modularity reviews code/indices before tests/docs.
  Return deviations/drift to their owners until resolved or accepted.
- Modularity implementation needs no separate self-review beyond its report/root review. Tests precede
  documentation; reconcile drift. Root integrates/verifies.
- Specialists never delegate or coordinate. Reuse threads unless stale/unsafe; reuse never
  authorizes history.

## Packets

|`task_packet` field|Contents|
|---|---|
|`mode`|Analysis/implementation/review|
|`objective`|Outcome/evidence|
|`scope`|In/out targets|
|`acceptance`|Criteria/compatibility|
|`ownership`|Read-only/exclusive targets|
|`gates`|Decisions/rationale/evidence/brief/index|
|`identity`|Repository/GitHub/worktree/base/head|
|`verification`|Plan/ledger/checks/budget/output|
|`upstream_decisions`|Distilled findings/accepted decisions|
|`stop_conditions`|Policy/breaking/scope/structural stops|

|`result_packet` field|Contents|
|---|---|
|`outcome`|Completed/blocked state|
|`findings`|Files/index consistency|
|`changed_inputs`|Relevant input changes|
|`evidence`|Mandatory/selected checks; commands; completion sets; invalidation; duration/budget; identities/digests/fingerprints; next checks|
|`risks`|Ambiguity/residual risk; expansion needing root authorization|
|`next_action`|Next owner/action|

Exclude raw logs and full Issues/plans. Nest preflight/disposition under `identity`,
cleanup/recoverability under `evidence`, and disposition results in their fields.

Record `root_phase_wall_time_seconds,subagent_startup_seconds,subagent_wait_seconds,turn_count,tool_call_count,duplicate_read_count,verification_time_seconds,input_tokens,output_tokens`.
Mark each `observed|estimated|unavailable`; explain unavailable; estimates never count as
observations. Targets: median >=25% lower; P95 >=15% lower; turns >=30% fewer; duplicate
broad suites >=50% fewer; packet tokens 25--35% fewer. Static tests prove no performance.
