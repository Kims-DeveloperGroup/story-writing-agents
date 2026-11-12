<!--
Read the authoring guide and this complete raw source before preparing this evidence report:
../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md

This is a reporting comment with a verification purpose. Lead with the supported conclusion,
applicable scope, evidence status, and remaining uncertainty. A failing run can be valid
evidence of failure; evidence applicability is not the same as pass/fail or lifecycle status.
Use Valid, Partially valid, Superseded, Excluded, or Not established as descriptive applicability
labels for the named claims, not as code enums or pass/fail states. Excluded means the evidence
cannot support the named decision; preserve the original observation, including any failure.
Superseded identifies a replacement for a stated decision and does not make history invalid.
A baseline observation can remain Valid while applicability to the current head is Not
established. Assign only supported scope, and link the relevant earlier/current record when
an interpretation, input, or accepted criterion changes.

Runtime-code changes retain canonical-runner exact IDs, JSON evidence, outcomes, and required
full-shard reconciliation. Runtime and test changes require full CI. Policy/docs/templates/
skills/development-agent configuration/module-index-only changes report static validation.
Retain the reviewed base/head separately from later verification and current-head checks.
Never convert missing, stale, cancelled, interrupted, incomplete, or aggregate evidence into
a pass. This template does not authorize checks, risk acceptance, new Issues/PRs, or reviews.
Do not start additional work merely to fill a field; report the actual gap and its effect.

Keep the decision summary, failed/unexecuted work, material limitations, and required decisions
visible. Preserve full method ledgers, precise raw values, hashes, and reproduction metadata
in details blocks or accessible artifacts. Do not discard necessary evidence to shorten text.
Do not delete an earlier comment or silently replace its historical observations. Any authorized
correction preserves the original body and adds a clearly labeled scope/status note and link;
protocol-constrained triggers, hashes, and machine records remain untouched.
Write the report's prose in the language requested by the authoring task.

Remove this instructional comment and all placeholders before publication. Preserve all six
visible headings and their meaning. Omit an optional details block only when it is empty.
-->

## What this demonstrates

**Conclusion:** {{Lead with the scenario or criterion established, failed, or not established. Distinguish this assessment from overall verification closure or integration completion.}}

**Evidence applicability and scope:** {{Use Valid, Partially valid, Superseded, Excluded, or Not established for the specific claims to which each label applies. Identify supported and unsupported uses, the short source/head SHA, selected population, and actual execution or comparison scope. Keep applicability separate from pass/fail and lifecycle status.}}

**Changes since the previous record:** {{Describe new observations, corrections, criterion changes, exclusions, or replacement scope and link the related report. Identify a first record as a baseline or initial observation. For a criterion change, link its decision basis and applicable scope.}}

## Scenario results

| Scenario or criterion | Expected and observed result | Outcome | Evidence |
| --- | --- | --- | --- |
| {{Name}} | {{Expected result and actual observation; identify population, units, denominator where relevant, execution scope, and observation conditions.}} | {{Pass, fail, or not established.}} | {{Descriptive link supporting this claim.}} |

{{Use readable summary precision without losing meaning. Distinguish method totals from subTest, assertion, and failure-detail counts. Do not present values from different selected populations or execution conditions as the same population's improvement rate.}}

<details>
<summary>Detailed observation ledger: complete cases and repeated measurements</summary>

{{Preserve the required full method table, exact selection list, raw precision, or corresponding artifact links. Map the detail to the summary. Keep failures, outliers, and investigated items that affect the conclusion visible in the summary and coverage gaps as well.}}

</details>

## Reproduction and environment

<details>
<summary>Reproduction details: source, environment, and exact checks</summary>

**Source:** {{Repository, worktree, full base/head SHA, and relevant input or dirty identity. Do not invent unobserved values.}}

**Environment:** {{Relevant versions, configuration, execution location, capabilities, and equivalence of comparison conditions.}}

**Checks:** {{Commands and exact IDs actually executed, or static heading/link/path/reference/frontmatter/diff checks actually performed. Do not present an unexecuted command as completed verification.}}

</details>

## Failures and coverage gaps

{{Identify failures and fingerprints, accepted skips, interruptions, cancellations, unexecuted work, stale results, unresolved causes, and remaining verification. Explain the claims or acceptance criteria affected by each limitation. If none were found, state that within the observed scope.}}

{{If evidence is excluded or a previous result is superseded, identify the retained, excluded, and replaced claims and why. Preserve the historical observation: do not convert a previous failure into a pass or extend partial verification to overall success.}}

## Technical provenance and artifacts

<details>
<summary>Supporting provenance: identifiers, review history, timing, and artifacts</summary>

**Selection and identity:** {{Relevant modules, exact IDs, JSON, required full-shard reconciliation, or static check inventory. Identify the inputs and the basis for reusing earlier evidence.}}

**Review rounds:** {{Applicable existing round number, provider/actual effort, actual reviewed base/head, reconciliation links, remaining High/Critical findings, later diff verification, current checks/approvals, and closure state. If inapplicable or not yet requested, say so without creating a review.}}

**Timing:** {{Actual measurements, units, measured intervals, sample size, and relevant budgets. Separate normal measurements from diagnostic profiles and runner time from command or CI time. Do not invent unobserved intervals or add overlapping cumulative measurements.}}

**Artifacts:** {{Descriptive links to selections, logs, JSON, and reports with their access scope. Label local-only paths as local preservation rather than presenting them as reader-accessible links.}}

</details>

## Assessment

{{Support the opening conclusion against the relevant acceptance criteria. Identify unestablished criteria, residual risks, and the owner and state of any applicable approval or acceptance decision. The evidence author does not replace the root's final completion or integration assessment.}}

**Next action or decision:** {{When needed, identify the owner, action, and condition resolved, or the actual decision and recommendation. Otherwise state that no further action or decision is requested. Editing this report alone does not initiate verification, a PR, or another review round.}}
