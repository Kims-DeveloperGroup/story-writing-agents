# Authoring Issues, PRs, evidence, and status comments

Explain the outcome, how the work produces it, and what evidence supports it.
[AGENTS.md](../../../../AGENTS.md) governs ownership and verification;
[GITHUB_WORKFLOW.md](../../../../GITHUB_WORKFLOW.md) governs planning, authority, native
relationships, review, and completion.

## Read the source first

Before creating or substantially updating a record, read this guide and the complete raw
template. A rendered preview hides required authoring comments and examples.

| Record | Source |
| --- | --- |
| Issue | [Work item template](../../../../.github/ISSUE_TEMPLATE/work_item.md) |
| PR | [Pull request template](../../../../.github/pull_request_template.md) |
| Evidence | [Evidence template](../../../../.github/templates/evidence.md) |
| Status comment | [Working-status template](../../../../.github/templates/working-status-comment.md) |

Preserve every visible heading and its meaning. Replace all placeholders, remove comments and
example material before publication, and omit a section only when it is genuinely optional and
empty. Never omit a material limitation, blocker, dependency, or evidence gap. Templates do not
authorize mutations or replace native Development, Project, sub-Issue, or dependency links.

## Write for the reader

Open with the useful behavior or repository outcome. Use short paragraphs, familiar words,
realistic inputs/actions and results, and descriptive links. Label constructed examples as
illustrative. Do not invent commands, checks, IDs, provider replies, timestamps, model identity,
review state, or private artifacts.

An Issue connects objective, named use cases, acceptance criteria, boundaries, related work,
and an implementation plan. Each plan step names the affected component and the criterion it
enables. Keep one delivery unit on one Issue; children require at least two independent,
necessary siblings under the workflow policy.

A PR describes final delivered behavior, the same action before and after, actual mechanisms,
verification, limits, and the correct Issue reference. Use `Refs #N` for partial/child work and
`Closes #N` only for a completing `main` PR. Record numbered rounds, selected provider/actual effort, reviewed base/head, and verified
closure in Verification when applicable.

A status comment states what works, what remains, the practical blocker or decision, and one
owned next action. A comment does not update Project state. Evidence records exact observations
and limitations; it does not authorize checks or risk acceptance.

## PR implementation descriptions

Write for a developer who has not read the Issue or earlier discussion. In What changed,
explain the problem, necessary background, and delivered outcome. Define unfamiliar terms
and component names on first use. In Before and after, compare the same concrete action
under the same relevant conditions; distinguish the expected result from what verification
actually demonstrated. Keep the Implementation section focused on how the change works.

Use one Implementation subsection per major changed component. A component is a meaningful
responsibility, not necessarily a file: it may span several modules. Include changes that
materially affect behavior, interfaces, data flow, persistence, security, performance, or
internal responsibilities. Group supporting helpers with that component. Do not give trivial
renames, formatting, import cleanup, or incidental documentation edits their own subsections.
Put supporting test results in Verification rather than listing every changed test file.

Each component subsection keeps these four explanations:

- **Purpose:** Explain its job and how it fits into the affected workflow.
- **Previous behavior:** Describe how it handled the relevant scenario and its limitation.
- **New behavior:** Describe the resulting behavior and the conditions under which it applies.
- **Major code changes:** Identify significant additions, removals, or reorganizations and
  explain how they produce that behavior. Link useful entry points or name relevant symbols;
  avoid an exhaustive file or helper inventory. Explain interactions between major components
  and material tradeoffs when they help the reader understand the result.

Scale the detail to the change; one component subsection is enough for a single-component PR.
For a substantial refactor, explain the responsibility or structure change and explicitly
state when observable behavior is unchanged. For a documentation or policy PR, use document
or contributor-workflow components and describe the significant rule, instruction, or
template edits under Major code changes without inventing runtime changes. Routine supporting
edits need no standalone component. Preserve the template's six top-level headings and its
review/evidence fields while repeating component subsections as needed.

### Illustrative component example: Git command runner

This example uses [PR #757](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/757)
at revision `7e6ab429786ba0bf03e9a7fee6ac09a3f94d58bd`. It illustrates one component subsection,
not the complete PR or its current review and verification status. Do not copy its historical
facts into an unrelated PR.

**Purpose:** Runs Git commands, collects their normal and error output, and handles timeouts
and process cleanup during repository checks.

**Previous behavior:** Each call started three helper threads: one for each output stream
and one to wait for the Git process to finish.

**New behavior:** Eligible read-only calls collect both output streams and wait for completion
in the calling thread, within one deadline. This applies to calls with no input data and real
operating-system pipes on POSIX platforms such as macOS and Linux. Calls that change Git
state, supply input, or use unsupported streams retain the existing threaded path.

**Major code changes:** In
[git/process.py at the example revision](https://github.com/Kims-DeveloperGroup/story-writing-agents/blob/7e6ab429786ba0bf03e9a7fee6ac09a3f94d58bd/src/story_writing_agents/git/process.py),
add a selector-based capture path: the selector waits until either output stream has data
available, allowing one thread to handle both streams. Both paths share result validation,
preserving output limits, repository checks, timeout handling, and child-process cleanup.
This reduces helper-thread creation for eligible calls; it does not by itself demonstrate
faster recovery or passing full-suite verification.

## Verification records

Codex review records distinguish bot-authored reservation/status comments from the personal
GitHub account that posts the trigger. Retain the request author, round/attempt marker,
requested base/head, request URL, actual provider response, and later verification separately.
Link a clean Codex response comment directly when it reports no major issues and identifies
its reviewed commit; do not invent a native review ID or a thumbs-up. Use the canonical
[personal-account handoff](../../../../GITHUB_WORKFLOW.md#managed-requester).

Runtime and test changes require complete shard CI under [TEST_POLICY.md](../../../../TEST_POLICY.md).
Runtime-code changes also require affected tests. Use the canonical runner and retain exact
IDs, JSON results, shard reports, and reconciliation. Map results to scenarios or criteria.
Distinguish failures,
accepted skips, interruption, and unexecuted work.
A timeout, missing CI, stale head SHA, or aggregate count is not a pass.

Policy, docs, templates, skills, development-agent configuration, and module-index-only
changes use static validation. Name the checked headings, links, paths, references,
frontmatter/format, policy invariants, source/head SHA, and diff review.

For review evidence, follow GITHUB_WORKFLOW.md: identify each round, its selected provider
and actual effort, reviewed base/head, finding dispositions, remaining High/Critical defects,
later diff inspection, current checks/approvals, and closure. Medium feedback is nonblocking;
from round 3 it cannot justify more repair work. Keep historical review evidence separate
from current verification. Never imply both providers or a clean extra review are required.

Evidence names its repository/worktree, base and head, relevant environment, literal commands
or static checks, outcome, failures/gaps, and assessment. Include detailed manifests, hashes,
budgets, and logs only when they improve reproduction or explain reuse; link large artifacts.
Never claim historical evidence applies to the current head without checking relevant inputs.

## Publish and verify

Check the finished body in source and rendered form when available. Confirm section order,
scenario-to-criterion coverage, links, lists, tables, and fences. Publish only under the active
workflow authority, read the resulting record back, and separately verify native links,
Project state, review closure, and current-head check status.
