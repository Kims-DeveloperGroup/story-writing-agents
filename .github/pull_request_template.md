<!--
Read the authoring guide and this complete source before writing:
../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md

Describe final behavior, the same action before/after, actual mechanisms, evidence and gaps,
limits, and the correct Issue relationship. Record review rounds and CI runs against exact head
SHAs. Runtime tests are required only for runtime-code changes; other changes report static
validation. Remove this comment and every placeholder before publication. Preserve all visible
headings and their meaning.
-->

## What changed

{{Delivered behavior and the problem it solves in one or two sentences.}}

## Before and after

{{The same realistic input or action, its previous result, and its new result.}}

## Implementation

{{Actual mechanisms and how they enable the named scenarios, including useful tradeoffs.}}

## Verification

{{Results mapped to scenarios/criteria, with exact runtime IDs or named static checks.}}

**Initial review-round head:** {{SHA and Copilot, Codex, affected CI, and required-human state.}}

**Final review-round head:** {{SHA and gate results after material fixes, or not required.}}

{{Link detailed Evidence and name failures, skips, unexecuted cases, or stale evidence.}}

## Limitations and follow-ups

{{Material limits, compatibility effects, and blocking/nonblocking follow-ups, or none known.}}

## Related Issue

{{Refs #N or Closes #N, as permitted by the owning Issue and PR target.}}
