<!--
Purpose: Let a reviewer see what the change delivers, how it works, and what the
evidence supports.
When to use: Create or update a pull request description, including a draft PR and
descriptions written through the CLI or API.

Read the authoring guide and this entire source, including the examples, before writing:
[Authoring guide](../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md).

What changed: Lead with one or two sentences about delivered behavior and the problem it
solves. Describe the final implementation rather than the conversation or abandoned
plan.

Before and after: Show the same realistic input or contributor action before and after
the change. Label constructed wording and never present it as observed output. An
internal task can show a repository or policy result without inventing a UI.

Implementation: Connect each actual mechanism or change to the named use case it
enables. Explain why it produces the requested result, using only review-relevant
components and tradeoffs. Distinguish delivered work from plans or follow-ups.

Verification: State which named scenario or acceptance criterion each result supports
and what remains unestablished. Link Evidence for exact commands, selections, source
identity, environment, artifacts, and reuse decisions. An aggregate count does not prove
every criterion. Distinguish completed checks from planned or unavailable checks; a
timeout is not a pass, and absent CI is not success.

Limitations and follow-ups: Keep material compatibility limits, unverified behavior, and
deferred work visible. Link follow-ups and state whether they block acceptance. Say when
no known limitation remains rather than hiding an unresolved check.

Related Issue: Use the Issue relationship required by workflow policy. `Refs #N` is for
partial or child work; `Closes #N` is only for a completing PR targeting main. Verify
native Development and Project links separately. Link a parent without claiming to close
it through a child PR.

Replace every double-brace placeholder. Remove comments and example material before
submission. Omit only genuinely optional empty sections. Re-read the finished draft for
the delivered behavior, concrete example, actual implementation, and unverified gaps.
-->

## What changed

{{Delivered behavior and the problem it solves in one or two sentences.}}

## Before and after

{{The same realistic input or action, its previous result, and its new result.}}

## Implementation

{{Actual mechanisms and how they enable the named scenarios, with useful tradeoffs.}}

## Verification

{{Results mapped to scenarios or criteria, linked Evidence, and unestablished cases.}}

## Limitations and follow-ups

{{Material limits, compatibility effects, and follow-ups or a clear statement of none known.}}

## Related Issue

{{Refs #N or Closes #N, as permitted by the owning Issue and PR target.}}

<!--
Historical worked example: a reader-facing reconstruction of
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622). Results
below are attributed to the
[historical verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359),
not checks run while authoring this example. The interaction wording is illustrative.

## What changed

Story conversations using Antigravity now read the assistant's final reply from the
CLI's completed response. A successfully repaired turn can return its reply even when
intermediate processing reported errors.

## Before and after

This input/output sequence is constructed to explain the result. It is not a recorded
provider transcript.

```text
Input: Remember the marker SAMPLE-27 for this conversation.
Expected reply shown: I'll remember SAMPLE-27.
Action: Reopen the same conversation.
Input: Which marker did I give you?
Expected reply shown: SAMPLE-27.
```

**Before:** A turn with a valid final success could be rejected because intermediate
stream events reported errors. The user received no Lead reply.

**After:** The same valid final success supplies the visible reply. The historical
report also records marker recall after reopening the same conversation. Final error
results remain errors.

## Implementation

The Antigravity adapter reads the completed JSON result so a valid final success can
supply the user's reply despite intermediate errors. It requests `--output-format json`
and passes the complete prompt through `--print` so the requested input reaches the CLI.
JSON-mode probes ignored text on standard input; that discovery is distinct from the
original rejected-reply failure.

Strict validation keeps final errors and invalid results from becoming successful
replies. Existing conversation handling supports reopening the same thread; Codex and
shared implementation bytes remain unchanged in the source report.

## Verification

For the “receive a valid final reply” and “reopen and recall” scenarios, the
[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
records replies, marker recall, and same-thread continuity as passed. Its exchange,
schema, accounting, and exit checks support the metadata criterion for successful turns.
These results came from three actual adapter calls and six fresh/reopened Story turns
across Pro, Flash, and Sonnet.

The report also records 51 focused tests with zero skips: 25 adapter, 18 parity, three
common, and five Codex tests. Exact 51-ID commands and the raw private manifest are
inaccessible from the comment. Separate results for a valid final success after
intermediate errors and for final-result rejection are unavailable. Reported successful
replies and the aggregate count do not establish those specific conditions or provide
an exact rerun selection.

## Limitations and follow-ups

Prompt text can be visible to someone able to inspect process arguments, and large
prompts remain subject to operating-system argument-size limits. Final JSON omits
initialization and step attestation, so it cannot independently confirm that the
requested model handled the conversation.

The historical report supports the stated local checks. It does not establish a separate
CI result or eliminate the missing-provenance limitation.

## Related Issue

Refs #621.
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) is the
source implementation. This reconstructed description does not assert new closure
authority.
-->

<!--
Historical worked example for contributor workflow: reconstructed from
[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604). It
demonstrates an internal change without inventing a product screen. The actions below
are illustrative applications of the delivered rule, not records of live GitHub
mutations.

## What changed

A plan with one complete delivery unit stays on its existing Issue. Contributors create
child Issues only when at least two independent, necessary sibling units are ready to be
planned and verified together.

## Before and after

For the same illustrative action, “Plan one complete workflow update”:

**Before:** The plan could be represented as a wrapper Issue with one child containing
the actual delivery unit.

**After:** The contributor updates the one owning Issue; no sole child or wrapper is
created.

For a plan with two independently complete, necessary releases, the rule instead
requires creating and verifying the sibling set together. These are examples of the
rule, not observed GitHub operations.

## Implementation

The direct-child gate requires at least two independently complete, necessary siblings.
A single workflow update does not meet that gate, so its owner updates or enlarges the
current Issue's approved scope. This keeps the single delivery unit scenario on one
owning
Issue while allowing a complete sibling set for the two-release scenario.

## Verification

[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604) reports
this command with 44 tests passed:

```bash
python -m unittest tests.test_repository_agent_policy -v
```

This is reported repository-policy evidence for the illustrated planning rule. It does
not establish individual scenario results or demonstrate live GitHub hierarchy creation
or Project transitions.

## Limitations and follow-ups

The check covers repository policy expectations. Live GitHub behavior was not claimed,
and the example does not replace the planning gate or authorize new Issue relationships.

## Related Issue

[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604) provides
the historical context. Its owning Issue number is unavailable in the supplied source
summary; verify that relationship before authoring a real PR description. No closure
reference is invented here.
-->
