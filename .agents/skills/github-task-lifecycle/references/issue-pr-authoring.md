# Authoring Issues, PRs, evidence, and status comments

Explain what the work enables, how it achieves that result, and what evidence supports it.
These templates govern reader-facing records; internal task and result packets,
development role boundaries, and runtime story roles stay unchanged.
[AGENTS.md](../../../../AGENTS.md) remains canonical for adaptive verification and
ownership. [GitHub workflow policy](../../../../GITHUB_WORKFLOW.md) governs planning,
authorization, native relationships, and completion.

## Read the source before authoring

Before creating or updating records through a browser, CLI, API, or connector, agents
must read this guide and the relevant template's entire raw source. Open GitHub's
**Code/source view** or read locally; a rendered preview is insufficient. The source
contains a hidden authoring comment and separate hidden worked examples; rendering shows
only the reusable body.

| Record | Read this source | Use it for |
| --- | --- | --- |
| Issue | [Work item template](../../../../.github/ISSUE_TEMPLATE/work_item.md) | Objective, cases, plan, and acceptance criteria. |
| PR | [Pull request template](../../../../.github/pull_request_template.md) | Delivered behavior, changes, and verification. |
| Evidence | [Evidence template](../../../../.github/templates/evidence.md) | Results, reproduction, provenance, and gaps. |
| Status | [Working-status comment template](../../../../.github/templates/working-status-comment.md) | Progress, blockers, decisions, and next action. |

GitHub discovers the Issue template through frontmatter and the PR through its standard
path. Copy Evidence and status into authorized records or comments; no new integration
is needed.

## Prepare a finished body

1. Read current scope and sources, plus the existing record for substantial updates.
   Examples demonstrate writing; they supply no task facts or authority.
2. Fill every `{{double-brace placeholder}}` with task content. Keep sections in order:
   seven for Issues, six for the others. Omit only genuinely optional empty sections,
   preserving material limitations, dependencies, and verification gaps.
3. Remove HTML comments, frontmatter, worked examples, and unused placeholders. The native
   Issue selector can supply the body; CLI and API authors must perform cleanup themselves.
4. Link detailed Evidence when technical records would interrupt an Issue, PR, or status
   explanation. Use descriptive labels such as “Focused adapter checks and conversation
   results.”
5. Re-read the finished draft. Trace scenarios through criteria, planned mechanisms or
   actual changes, and evidence. Check scenario coverage, step relevance, visible gaps,
   links, lists, tables, and fenced examples in source and rendered views when available.
6. Publish only as authorized under workflow policy. Read back the result and separately
   verify required native relationships or status.

Preserve valid historical records, scope, decisions, and evidence. When revising stale
descriptions, label superseded evidence or replaced scope. These templates do not
authorize bulk rewriting of old GitHub records.

## Make the work easy to read

Open with a functional objective in one or two sentences about what someone can do or
what becomes reliable. Put hashes, process terms, field inventories, and implementation
identifiers where they explain a decision.

Use familiar words, short paragraphs, and blank lines. Wrap ordinary source prose at
roughly 80–100 columns; keep URLs, tables, and literal commands intact. Avoid crowded
punctuation and abbreviated metadata chains.

Show realistic inputs and outputs: a prompt, expected visible reply, and relevant
follow-up, or a contributor action and resulting Issue, branch, or policy state.
Internal work needs no invented screen.

Label constructed interactions as illustrative or expected, and cite historical results
as reported. Never present examples as observed provider replies or invent commands,
timestamps, model identities, test IDs, result sets, or private artifacts.

Use narrow tables when comparisons help: scenario, expected and observed result, outcome.
Put long commands, provenance, and artifacts in labeled paragraphs or linked records.
Link labels should identify the evidence or decision readers will find.

## Describe scope and delivery

After use cases, give an Implementation summary: one short paragraph or a few bullets
about planned mechanisms and changes, without workflow, test, or log detail. Keep the
detailed Implementation plan last, after scope and verification context.

Each ordered step names affected logic or components, necessary details, and the scenario
and criterion it enables. Include needed tests and documentation. Each acceptance
checkbox names its scenario and an observable success or failure condition. Cover every
use case in both criteria and plan. Scenario names suffice; IDs are optional. Define IDs
once, reference only defined IDs, and match each step's use cases to its cited criteria.
Update the plan for approved scope changes; the planning gate still applies.

Initiatives and parents require the objective, non-goals, integration criteria, ordered
children, risks, evidence plan, and approved base. Leaves identify their parent or
standalone status and canonical Project, then describe context, included and excluded
work, testable completion, verification, documentation, affected components,
dependencies, constraints, and risks. Place facts in relevant sections and link existing
records; do not copy every Project field into the opening.

Keep one implementation or delivery unit on its Issue. Child sets require at least two
independently complete, necessary siblings created and verified together. Follow the
[planning and topology rules](../../../../GITHUB_WORKFLOW.md#issues); templates neither
create native relationships nor authorize subdivision.

For the same input or action, PRs use explicit Before and After results and explain the
mechanism behind the change. Map verification to scenarios or criteria and flag
unestablished cases. Update titles and descriptions to final scope, retaining only
review-relevant history and tradeoffs.

Use `Refs #N` for partial or child work; `Closes #N` only for completing PRs targeting
`main`. Neither replaces native Development or Project links. Child PRs must not close
parents, and status comments do not update Projects. After failed closure, reopen the
Issue, return its Project status to `Backlog`, and replan.

## Keep the evidence honest and useful

Follow the [adaptive-verification skill](../../adaptive-verification/SKILL.md) for
selection, execution ownership, budgets, deduplication, reuse, and sufficiency. Evidence
records decisions and results; it authorizes neither checks nor risk acceptance.

Summaries name the scenario or criterion, expected and observed results, and material
gaps. Status states what works, how remaining work and blockers affect the scenario, and
the next action that resolves them. Evidence retains applicable commands, case manifests,
source and environment identity, digests, outcome sets, timing, budgets, artifacts, and
reuse decisions. Link lengthy details.

Name missing provenance and its effect on reproduction or reuse. Aggregate counts supply
neither exact IDs nor separate passes for every criterion or variant. An adapter hash
does not automatically cover tests, fixtures, runner, transitive inputs, and environment.

Distinguish assertion failures, accepted skips, interruptions, and unexecuted work.
Timeouts and absent CI are not passes. Partial or historical evidence cannot silently
complete current criteria. Keep material limitations in PR and status summaries even
when details are linked.

## What the examples demonstrate

[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) and the
[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
ground the Antigravity examples. A valid final reply rejected after intermediate errors
is distinct from the later JSON-mode probes that ignored standard-input text. Marker
interactions and Issue UC and AC labels are illustrative, not retrieved transcripts or
original identifiers; related examples use scenario names. Counts and durations are
reported. Missing commands, manifest contents, and distinct intermediate-error and
rejection-case results remain unestablished.

[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604)
illustrates one delivery unit on one Issue. Its reported policy-test command supports
the wording without proving live GitHub behavior.

Status examples cover progress, blocked, and completion updates. Their sequence and
blocker are hypothetical. Completion separates implemented work and reported checks from
unresolved acceptance evidence and publication gates.
