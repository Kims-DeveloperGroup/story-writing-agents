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
`Closes #N` only for a completing `main` PR. Record ready and final review milestone head SHAs
in Verification when applicable.

A status comment states what works, what remains, the practical blocker or decision, and one
owned next action. A comment does not update Project state. Evidence records exact observations
and limitations; it does not authorize checks or risk acceptance.

## Verification records

Runtime tests are required only for runtime-code changes. Use the canonical runner and retain
exact IDs, JSON results, groups, timing weights, shards, and reconciliation. Map results to
scenarios or criteria. Distinguish failures, accepted skips, interruption, and unexecuted work.
A timeout, missing CI, stale head SHA, or aggregate count is not a pass.

Policy, docs, templates, skills, development-agent configuration, module-index-only, and
test-only changes use static validation. Name the checked headings, links, paths, references,
frontmatter/format, policy invariants, source/head SHA, and diff review.

For review evidence, identify the current head SHA, ready/final milestone, Copilot and Codex
state, affected CI, required human approvals, and unresolved blocking findings. Only Copilot
High and Codex P0/P1 findings are AI blockers under repository policy; lower-severity findings
may be documented as nonblocking follow-ups.

Evidence names its repository/worktree, base and head, relevant environment, literal commands
or static checks, outcome, failures/gaps, and assessment. Include detailed manifests, hashes,
budgets, and logs only when they improve reproduction or explain reuse; link large artifacts.
Never claim historical evidence applies to the current head without checking relevant inputs.

## Publish and verify

Check the finished body in source and rendered form when available. Confirm section order,
scenario-to-criterion coverage, links, lists, tables, and fences. Publish only under the active
workflow authority, read the resulting record back, and separately verify native links,
Project state, and current-head review/check status.
