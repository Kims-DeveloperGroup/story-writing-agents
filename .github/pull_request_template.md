<!--
Read the authoring guide and this complete source before writing:
../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md

Describe final behavior, the same action before/after, actual mechanisms, evidence and gaps,
limits, and the correct Issue relationship. Write for a developer new to the repository:
include necessary background and explain unfamiliar terms without relying on the linked Issue.
Repeat the Before and after scenario labels for distinct actions when needed. Repeat the
Implementation component subsection for each major changed responsibility, replacing its
heading with a descriptive name. Group supporting helpers with their component and omit
trivial edits; do not turn the section into a file inventory. The authoring guide explains
how to handle refactors and non-runtime changes and includes an illustrative PR #757 example.

Record review rounds and CI runs against exact head SHAs. Runtime and test changes require
full-suite CI; runtime-code changes also require affected tests. Other changes report static
validation. Remove this comment and every placeholder before publication. Preserve the six
top-level headings and their meaning, and the labeled explanations within each component.
-->

## What changed

{{Briefly explain the problem, the background a new reader needs, and the delivered outcome.}}

## Before and after

**Action:** {{A concrete user or contributor action, with relevant input or conditions.}}

**Before:** {{The previous result of this action and the limitation it exposed.}}

**After:** {{The new result of the same action, distinguishing implemented from verified behavior.}}

## Implementation

### {{Major component name}}

**Purpose:** {{What this component does and where it fits in the affected workflow.}}

**Previous behavior:** {{How it handled the relevant scenario before this change.}}

**New behavior:** {{How it handles that scenario now, including important conditions or
unchanged behavior when the change is structural.}}

**Major code changes:** {{Significant additions, removals, or reorganizations, with useful
code references. Explain how they produce the new behavior, including interactions with
other major components and tradeoffs that matter to the reader.}}

## Verification

{{Results mapped to scenarios/criteria, with exact runtime IDs or named static checks.}}

**Review selection:** {{One of copilot-lite, copilot-balanced, codex; matching selector label.}}

**Review rationale:** {{Risk and affected behavior that justify the selected tier.}}

**Review rounds:** {{Number, provider/actual effort, reviewed base/head, reconciliation links, and state.}}

**Verified closure:** {{Blocking finding dispositions, final base/head and diff inspection,
current checks/approvals, later local verification, and closure record; or remaining work.}}

{{Link detailed Evidence and name failures, skips, unexecuted cases, or stale evidence.}}

## Limitations and follow-ups

{{Material limits, compatibility effects, and blocking/nonblocking follow-ups, or none known.}}

## Related Issue

{{Refs #N or Closes #N, as permitted by the owning Issue and PR target.}}
