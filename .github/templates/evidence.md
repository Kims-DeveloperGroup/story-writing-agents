<!--
Use this record to show what verification demonstrates, how it was obtained, and what remains
uncertain. Read the authoring guide and this complete source first:
../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md

Runtime-code changes retain canonical-runner exact IDs, JSON evidence, outcomes, and optional
full-shard reconciliation when required. Policy/docs/templates/skills/development-agent config/
module-index-only changes report static validation instead. Runtime and test changes require
full CI. Review evidence identifies its reviewed base/head; later local verification and
current-head checks are recorded separately. Never convert missing, stale, cancelled, interrupted, or aggregate
evidence into a pass. Remove this comment and every placeholder before publication. Preserve
all visible headings and their meaning.
-->

## What this demonstrates

{{Named scenario or criterion, verification type, source/head SHA, and supported result.}}

## Scenario results

| Scenario or criterion | Expected and observed result | Outcome |
| --- | --- | --- |
| {{Name}} | {{Expected result and actual observation/source.}} | {{Pass, fail, or not established.}} |

## Reproduction and environment

**Source:** {{Repository, worktree, base/head SHA, and relevant input or dirty identity.}}

**Environment:** {{Relevant versions, configuration, and capabilities.}}

**Checks:** {{Literal runtime command/exact IDs, or named static link/path/heading/reference/diff checks.}}

## Failures and coverage gaps

{{Failures/fingerprints, accepted skips, interruptions, unexecuted work, stale results, and gaps.}}

## Technical provenance and artifacts

**Selection and identity:** {{Relevant runtime modules/exact IDs and JSON; full-suite shards only when required, or static check inventory; input identity and reuse decision.}}

**Review rounds:** {{Round number, selected provider/actual effort, reviewed base/head, reconciliation links,
remaining High/Critical findings, later final-diff verification, current checks/approvals, and closure state.}}

**Timing:** {{Actual duration and material budget limit, when relevant.}}

**Artifacts:** {{Descriptive links to manifests, logs, JSON, reports, or inaccessible/private records.}}

## Assessment

{{Supported and unsupported criteria, residual risk, acceptance owner/state, and next action.}}
