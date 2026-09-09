# Authoring Issues, PRs, evidence, and status comments

Write each record so a reader understands the useful result and how the work achieves
it. An Issue connects scenarios, intended changes, and completion conditions. A PR
explains the delivered mechanisms and results. A status comment explains what works,
what affects the remaining scenarios, and the next action. Evidence connects
observations to the criteria they support.

These templates govern reader-facing records. They do not change the internal task or
result packets, the development-role boundaries, or the runtime's story-agent roles.
[AGENTS.md](../../../../AGENTS.md) remains canonical for adaptive verification and
ownership. [GitHub workflow policy](../../../../GITHUB_WORKFLOW.md) governs planning,
authorization, native relationships, and completion.

## Read the source before authoring

Before creating or updating any of these records, agents must read this guide and the
relevant template's entire raw source. This applies to browser, CLI, API, and connector
writes; having seen a rendered preview does not satisfy the requirement. For a
substantial update, also read the existing record and preserve still-valid scope,
decisions, and evidence.

Open the file's **Code/source view** on GitHub or read the local file. Each template
contains a hidden authoring comment and separate hidden worked examples. The rendered
template shows only the reusable body. Comments are intentionally hidden in rendered
Markdown so readers do not receive instructions or sample history mixed into their work
item.

| Record | Read this source | Use it for |
| --- | --- | --- |
| Issue | [Work item template](../../../../.github/ISSUE_TEMPLATE/work_item.md) | A useful outcome, examples, implementation plan, and acceptance criteria. |
| PR | [Pull request template](../../../../.github/pull_request_template.md) | Delivered behavior, actual changes, and review evidence. |
| Evidence | [Evidence template](../../../../.github/templates/evidence.md) | Detailed results, reproduction, provenance, and coverage gaps. |
| Status | [Working-status comment template](../../../../.github/templates/working-status-comment.md) | Progress, blockers, decisions, and the next action. |

The Issue file has native discovery frontmatter. The PR file occupies GitHub's standard
template path. Evidence and status are reusable Markdown sources to copy into an
authorized record or comment; they do not require a new GitHub integration.

## Prepare a finished body

1. Read the current scope, relevant sources, and template instructions. Treat worked
  examples as demonstrations of writing, not facts or authority for the new task.
2. Copy the reusable body and replace every `{{double-brace placeholder}}` with
   task-specific content. Keep that template's sections in order: the Issue has seven,
   and the other templates have six. Omit only genuinely optional empty sections; never
   remove a material limitation, dependency, or verification gap because it is
   inconvenient.
3. Remove HTML comments, frontmatter, worked examples, and unused placeholder material
  from the submitted body. Do not paste a hidden example into the actual record. The
  native Issue selector can supply the body, but CLI and API authors must do this
  cleanup themselves.
4. Link a detailed Evidence record from the Issue, PR, or status comment when the
  technical record would interrupt the explanation. Give the link a useful label, such
  as “Focused adapter checks and conversation results.”
5. Re-read the finished draft, not only the template. Follow a scenario through its
   criterion, planned mechanism or actual change, and available evidence. Check that no
   scenario is orphaned, no step is unrelated, and gaps remain visible. Check links,
   lists, tables, and fenced examples in source and rendered views when available.
6. Publish only within the authorization already granted under workflow policy. Re-read
  the resulting record and verify its native relationships or status separately when the
  task requires those operations.

An update must retain valid historical records and decisions. Change stale descriptions
deliberately, and label superseded evidence or replaced scope so readers can follow the
transition. Adopting these templates does not authorize rewriting old GitHub records in
bulk.

## Make the work easy to read

Start with a functional objective in one or two sentences. Say what someone can do or
what useful result becomes reliable. Keep hashes, process vocabulary, field inventories,
and implementation identifiers in the sections where they explain a decision.

Use familiar words, short paragraphs, and blank lines between ideas. Wrap ordinary
source prose at roughly 80–100 columns because agents and contributors read the hidden
instructions there. Keep URLs, tables, and literal commands intact. Avoid crowded
punctuation and long strings of abbreviated metadata.

Give realistic inputs and outputs. For a conversation change, show a short prompt, the
expected visible reply, and any follow-up action that matters. For a workflow change,
show a contributor's action and the resulting Issue, branch, or policy state. Internal
work does not need an invented product screen.

Label constructed interaction text as illustrative or expected. Label historical results
as reported and link their source. An example answer is not an observed provider
response. Do not invent a missing command, timestamp, model identity, test ID, result
set, or private artifact to make the example look complete.

Use a table when a few parallel cases become easier to compare. Keep it narrow: a
scenario, its expected and observed result, and its outcome are usually enough. Put long
commands, provenance, and artifacts in short labeled paragraphs or linked records.
Descriptive link labels should tell the reader what evidence or decision they will find.

## Describe scope and delivery

An Issue places a brief Implementation summary after its use cases. Use one short
paragraph or a few bullets for planned mechanisms and changes; keep workflow, test, and
log detail out of that summary. Put the detailed Implementation plan last, after scope
and verification context.

Each ordered plan step names the affected logic or component, necessary details, and the
scenario and criterion it enables. Include necessary tests and documentation. Each
acceptance checkbox names its scenario and an observable success or failure condition.
Cover every use case in both criteria and the plan. Scenario names work; short IDs are
optional. If used, define IDs once, reference only defined IDs, and keep each step's use
cases consistent with its cited criteria. Update the plan when approved scope changes;
the planning gate still applies.

For an initiative or parent Issue, include the objective, non-goals, integration
criteria, children and their order, risks, evidence plan, and approved base. For a leaf,
identify its parent or standalone status and canonical Project, then describe context,
included and excluded work, testable completion, verification, documentation, affected
components, dependencies, constraints, and risks. Put each fact in the relevant section
and use descriptive links for existing records. Do not copy every Project field into the
opening paragraph.

Keep one implementation or delivery unit on its current Issue. Only create a direct
child set when it contains at least two independently complete, necessary siblings that
can be created and verified together. Follow the
[planning and topology rules](../../../../GITHUB_WORKFLOW.md#issues); the template does
not create native relationships or authorize subdivision.

A PR describes actual changes. Use explicit Before and After wording for the same input
or action, then connect the implementation mechanism to that difference. Map
verification results to the relevant scenario or criterion and name any unestablished
case. Rewrite the title and description around the final scope when it changes,
retaining only review-relevant history and tradeoffs.

Use `Refs #N` for partial or child work and `Closes #N` only for a completing PR
targeting `main`, as workflow policy requires. These references do not replace native
Development or Project links, and a child PR must not close its parent. A status comment
likewise does not update the Project by itself.

After a failed closure, reopen the Issue, return its Project status to `Backlog`, and
replan.

## Keep the evidence honest and useful

Read the [Evidence source](../../../../.github/templates/evidence.md) before creating or
updating an evidence record. Follow the
[adaptive-verification skill](../../adaptive-verification/SKILL.md) for selection,
execution ownership, budgets, deduplication, reuse, and sufficiency. The template
documents those decisions and results; it grants no new authority to run checks or
accept risk.

Summaries name the checked scenario or criterion, its expected and observed result, and
material gaps. Status updates connect remaining work and blockers to that scenario's
practical impact and identify the next action that resolves it. The detailed Evidence
record retains applicable commands, case manifests, source and environment identity,
digests, outcome sets, timing, budgets, artifacts, and reuse decisions. Use descriptive
links when those details are too large for the body.

If provenance is unavailable, name the missing fact and its effect on reproduction or
reuse. An aggregate test count supplies neither missing exact IDs nor a separate pass
for every criterion or variant. A final adapter hash is not automatically a digest of
its tests, fixtures, runner, transitive inputs, and environment.

Distinguish failed assertions, accepted skips, interruptions, and unexecuted work. A
timeout is not a pass. An absent CI run does not establish success. Partial or
historical evidence does not silently complete current acceptance conditions. Preserve
material limitations in the PR and status summary even when the detailed explanation is
linked.

## What the examples demonstrate

The Antigravity examples draw on
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) and the
[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359).
They distinguish a valid final reply rejected after intermediate errors from the later
JSON-mode probe finding that standard-input text was ignored. The marker interaction and
Issue UC/AC labels are illustrative, not retrieved transcripts or original Issue
identifiers. Related examples use scenario names. Counts and durations come from the
historical report; missing commands, manifest contents, and distinct results for the
intermediate-error and rejection cases remain unestablished.

The contributor-workflow examples draw on
[PR #604](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/604). They
show how one delivery unit stays on one Issue and how the reported policy-test command
supports the wording without proving live GitHub behavior.

The status template includes progress, blocked, and completion variants. Its sequence
and blocker are hypothetical. The completion variant separates implemented work and
reported checks from unresolved acceptance evidence and publication gates.
