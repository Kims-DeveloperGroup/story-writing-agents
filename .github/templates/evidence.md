<!--
Purpose: Show what a verification result demonstrates, how it was obtained, and what
remains uncertain.
When to use: Prepare or update a detailed verification record linked from an Issue, PR,
or status comment. This record supports the root's assessment; it does not authorize
checks or replace internal handoff/result packets.

Read the authoring guide and this entire source, including the examples, before writing:
[Authoring guide](../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md).
Follow [adaptive verification](../../.agents/skills/adaptive-verification/SKILL.md);
AGENTS remains canonical for ownership and authorization.

What this demonstrates: Name the use case and acceptance condition under examination,
the purpose and evidence level, and the supported result. Use scenario names or existing
optional IDs. Distinguish current observations, historical reports, and inferences;
never describe a planned check as executed.

Scenario results: For each named scenario or criterion, show the input or action,
expected result, observed result, and outcome. Mark a missing case result unestablished;
an aggregate count does not prove it passed. Quote actual outputs only when available
and safe to share. Label constructed wording as illustrative, not an observed
transcript. Link detailed case results instead of adding columns for technical fields.

Reproduction and environment: Record the repository, absolute worktree, baseline and
HEAD, dirty fingerprint, environment class and relevant capabilities or versions.
Include literal commands, exact test IDs or a linked exact case manifest, prerequisites,
and configuration needed for reproduction. If a command, private artifact, or
environment fact is unavailable, say so and describe the resulting reproduction limit.
Do not invent missing provenance or publish private story content.

Failures and coverage gaps: State failed, skipped, interrupted, and unexecuted work
separately. Include stable failure fingerprints and remaining acceptance conditions. A
timeout, interruption, or replacement is neither a pass nor an assertion failure.
Missing CI is not a successful CI result. Report partial coverage without claiming
completion.

Technical provenance and artifacts: Retain applicable adaptive-verification details
below. Use short labeled paragraphs and descriptive links; attach a long manifest or log
instead of pasting a metadata wall.
- Inventory and selection: class and rationale, mandatory_evidence, acceptance mapping,
  changed inputs, tests, consumers, helpers, fixtures, invariants, exclusions, risks,
  exact authorized_selection, evidence_execution_owner, estimate, its source and
  confidence, and stop or escalation triggers.
- Identity and reuse: source identity, relevant_input_digest and covered inputs,
  environment_class, normalized_command, exact_ids, reused evidence and its original
  purpose, proven disjointness after a HEAD change, and invalidation decisions. A
  matching label or wrapper does not justify a rerun.
- Execution: manifest digest, shard ordinal and IDs, input digest, outcome, duration,
  stable failure fingerprint, and completed_ids, failed_ids, accepted_skips,
  unexecuted_ids. Explain accepted skips and link the owner's decision. Preserve these
  sets after interruption.
- Timing and budget: estimated and actual duration, total and maximum shard budget,
  spent and remaining budget, and any approved adjustment. Missing historical timing or
  budget stays unavailable.
- Artifacts: descriptive links to exact commands, manifests, logs, source snapshots, and
  reports. Mark private or inaccessible artifacts without exposing them. Retain enough
  identity to explain whether evidence can be reused.

Assessment: Explain which criteria the record supports, which it does not, residual
risk, and the next evidence or decision needed. Attribute acceptance to the authorized
root or reviewer; a template is not acceptance authority. A historical report with
missing selection or input identity cannot by itself establish current reusable
evidence.

Replace every double-brace placeholder. Remove comments and examples before publication.
Omit only genuinely optional empty sections; never omit a material failure or
missing-provenance gap. Re-read the result for a clear claim, concrete scenarios,
reproducibility, and an honest assessment.
-->

## What this demonstrates

{{Named scenario and criterion, verification purpose and level, and supported result.}}

## Scenario results

| Scenario or input | Expected and observed result | Outcome |
| --- | --- | --- |
| {{Named scenario or criterion.}} | {{Expected result; actual observation or its source.}} | {{Outcome or not established.}} |

{{Result counts and a descriptive link to detailed case results when needed.}}

## Reproduction and environment

**Source:** {{Repository, absolute worktree, baseline and HEAD, and dirty fingerprint.}}

**Environment:**
{{Relevant versions, configuration, capabilities, and environment class.}}

**Reproduction:**
{{Literal commands, exact IDs or linked case manifest, and prerequisites; name any unavailable detail.}}

## Failures and coverage gaps

{{Failures and their fingerprints, accepted skips, interruptions, unexecuted work, and remaining acceptance conditions.}}

## Technical provenance and artifacts

**Inventory and selection:**
{{Authorized selection, owner, acceptance mapping, inventory, and risks.}}

**Identity and reuse:**
{{Input and manifest digests, their coverage, deduplication identity, reused results, and invalidation decisions.}}

**Execution record:**
{{Shard results and exact outcome sets, with their supporting artifact.}}

**Timing and budget:**
{{Estimated and actual duration, total and shard limits, spent and remaining budget, and approved adjustments.}}

**Artifacts:**
{{Descriptive links to detailed records, with unavailable or private artifacts identified.}}

## Assessment

{{Supported and unsupported criteria, residual risk, acceptance decision or pending owner, and next action.}}

<!--
Historical worked example: reconstructed from the
[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
and [PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622).
Outcomes below are reported historical results; no checks were rerun for this example.

## What this demonstrates

The reported checks support that the repaired Antigravity adapter returned replies and
retained context when a Story conversation was reopened. Final JSON does not
independently attest the resolved model.

## Scenario results

| Scenario | Expected and reported result | Outcome |
| --- | --- | --- |
| Receive a valid final reply. | Story displays the completed assistant reply; the report records successful replies. | Reported passed. |
| Receive a valid final success after intermediate errors. | The final reply should still appear; a distinct result for this variant is unavailable. | Not established. |
| Reopen the conversation and ask for its earlier marker. | The same thread retains context; the report records marker recall and continuity. | Reported passed. |
| Reject a final error or invalid result. | The turn should remain unsuccessful; a distinct result for this case is unavailable from the report. | Not established. |

For successful fresh and reopened turns, the report also records exchange, schema,
accounting, and exit checks as passed, supporting the metadata criterion. It reports
51 focused tests with zero skips: 25 adapter, 18 parity, three common, and five Codex
tests. The actual-call selection comprised three adapter calls and six fresh/reopened
Story turns. Neither successful replies nor the aggregate count separately establish
the intermediate-error variant or rejection condition.

## Reproduction and environment

The source is
[PR #622 in story-writing-agents](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622).
The observed Antigravity CLI version was 1.1.27.

The repair reads the final result with `--output-format json`. JSON-mode discovery
probes ignored text on standard input, so the adapter supplies the full prompt through
`--print`. These flags describe the implementation, not a recovered exact invocation or
the original failure's transport.

## Failures and coverage gaps

Exact commands, test-ID and shard records, raw manifest contents, full worktree and
environment identity, the complete input digest, execution-authorization records,
budgets, and suite durations are unavailable from the supplied report. Exact
prompt/reply text and machine-readable outcome sets are also unavailable. These gaps
limit independent reproduction and current reuse; the published manifest digest below
does not reveal its contents.

The report claims zero focused-test skips; no separate CI outcome is established.
Prompt text remains visible to process-argument inspection, and large prompts are
constrained by operating-system argument-size limits. Final JSON omits initialization
and step attestation, so the report cannot independently confirm that the requested
model handled each conversation.

## Technical provenance and artifacts

**Source identity:** Reported PR head `0fc430205d690ac6380cfd841e0e92edc4a7d8ff`; final
adapter SHA-256 `c8d91d2e12ce873f21396789a73d4d4a72e99fb90a4db856a236c9431091c927`.

**Artifact identity:** The
[verification comment](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
publishes sanitized private-manifest SHA-256
`4ac7bc0c869c1caf0c728feb9ef2406472c483e347f4c07a92993f7dde92f7bc`. Its raw contents are
inaccessible from that comment. No private story content is reproduced here.

**Reported timing:**

| Model label | Adapter call | Story turns: fresh / reopened |
| --- | --- | --- |
| Pro | 14.03 s | 11.97 s / 11.54 s |
| Flash | 8.37 s | 5.81 s / 9.17 s |
| Sonnet | 35.16 s | 23.11 s / 11.18 s |

These per-call durations do not establish total verification time. The remaining
provenance gaps are listed above; current reuse has not been established from this
summary.

## Assessment

The report supports the historical successful-reply, reopening, and metadata outcomes.
The intermediate-error variant and rejection criterion remain unestablished. A current
assessment needs the missing
case results and reproducible selection, or newly authorized evidence, while retaining
the argument and model-attestation limits.
-->

<!--
Historical worked example for contributor workflow: based on
[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604). The
scenario illustrates the delivered rule; it is not a live GitHub operation.

## What this demonstrates

Repository policy checks support keeping one complete delivery unit on its existing
Issue. Children require at least two independent, necessary siblings planned and
verified together.

## Scenario results

The “one owning Issue” scenario uses the illustrative action “Plan one complete workflow
update.” Expected: one owning Issue without a sole child or wrapper. Reported: 44
repository-policy tests passed. Individual scenario outcomes and a live GitHub operation
are not established by that aggregate result.

## Reproduction and environment

The reported command is:

```bash
python -m unittest tests.test_repository_agent_policy -v
```

Run identity matters: the same command at another revision may discover different tests.

## Failures and coverage gaps

The supplied summary does not expose exact outcome sets, complete worktree and
environment identity, input or manifest digests, execution-authorization records,
duration, or budgets. No live GitHub behavior or separate CI outcome is claimed.

## Technical provenance and artifacts

[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604) records
head `0683280776d50fe6758d95c96e0144ab65ab4c0a` and the reported command. Remaining
provenance gaps are listed above; no current reuse is established.

## Assessment

This is historical evidence for policy wording and repository checks. It does not
demonstrate live hierarchy creation or satisfy verification for a later change by
itself.
-->
