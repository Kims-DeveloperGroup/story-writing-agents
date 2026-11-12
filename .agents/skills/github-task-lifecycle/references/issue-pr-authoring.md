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

Preserve every visible heading and its meaning. Replace all placeholders and remove
template-authoring comments and source-template instructional examples before publication.
Keep required protocol markers unchanged and retain intentional details/summary markup. Omit a section only when
it is genuinely optional and empty. Never omit a material limitation, blocker, dependency,
or evidence gap. Templates do not authorize mutations or replace native Development,
Project, sub-Issue, or dependency links.

## Write for the reader

Open with the useful behavior or repository outcome. Use short paragraphs, familiar words,
realistic inputs/actions and results, and descriptive links. Label constructed examples as
illustrative. Do not invent commands, checks, IDs, provider replies, timestamps, model identity,
review state, or private artifacts.

An Issue connects a current limitation to a useful outcome, concrete actions and results,
issue-specific acceptance criteria, and an executable implementation plan. Common quality
rules are necessary context but do not establish what this delivery changes or how its
completion will be judged. Surface important issue-specific decisions in Implementation
summary and retain their full detail in Implementation plan.

Each material plan step identifies the affected component, intended responsibility or
behavior change, relevant preserved boundary, and the scenario or criterion it enables.
Scale the structure to the work: prose or a list may be sufficient, and tables are optional.
Keep one delivery unit on one Issue; children require at least two independent, necessary
siblings under the workflow policy.

A PR describes final delivered behavior, the same action before and after, actual mechanisms,
verification, limits, and the correct Issue reference. Use `Refs #N` for partial/child work and
`Closes #N` only for a completing `main` PR. Record numbered rounds, selected provider/actual effort, reviewed base/head, and verified
closure in Verification when applicable.

A narrative status or evidence comment opens with its current conclusion and practical
effect, describes what changed, and identifies remaining criteria and the next owner/action.
Its purpose determines supporting detail, not exemption from readable reporting. A comment
does not update Project state or authorize checks, risk acceptance, or another lifecycle step.
Apply the reporting and evidence-history rules below without changing canonical review-control
messages.

## Issue content and planning readiness

### Concrete content before common procedure

The opening sections let a reader answer three questions: what is difficult or incorrect
now, what will become possible or easier, and what this Issue specifically changes.
Describe the relevant current behavior from inspected evidence. If the limitation is still
a hypothesis, label it and identify the next observation needed.

A use case contains starting conditions, an action, and an expected observable result.
A list of preserved invariants or a statement to run focused tests is not a use case by
itself. For internal refactoring, a realistic contributor action is sufficient: explain
which responsibility is being changed, which existing behavior must remain stable, and
what maintenance or verification work the separation enables. Do not invent a historical
failure or performance result to make the example concrete.

Acceptance criteria describe independent completion judgments for this Issue. Include the
relevant success, failure, recovery, compatibility, or preserved-boundary results.
Common CI, review, merge, and cleanup obligations do not replace them. Use stable criterion
labels when a substantial plan needs explicit references; do not force a numbered matrix
onto a simple change.

If the objective claims an improvement, state what observation would demonstrate it;
a non-regression check alone does not establish that improvement.

The detailed plan explains how the outcome will be produced. Name affected components,
their intended responsibilities and interactions, material interface or state-ownership
changes, ordering constraints, relevant failure cases, and verification mapped to criteria.
An inspect/move/test/merge checklist is only a workflow outline. It is not an adequate
substitute for these issue-specific decisions.

### Initial uncertainty and existing readiness

Initial planning may expose unresolved material facts or choices. Record the unresolved
question, its effect on scope or implementation, and the next owner/action instead of
inventing an answer. A future investigation step does not mean the material design choice
has already been resolved.

Before first implementation, resolve the material scope, boundary, dependency, and
verification decisions required by the existing planning gate and Ready state.
An affected component and its intended change must be clear enough to execute and review;
every private helper name or routine internal implementation choice need not be predetermined.
Revalidate the appropriate readiness when resuming work without regressing its status.

These authoring requirements do not add a separate approval, sign-off, Project status,
or authority to perform mutations. Use GITHUB_WORKFLOW.md for the existing stage,
readiness, dependency, and authorization rules.

### Traceable facts, proposed designs, and shared rules

Use inspected existing paths, symbols, test IDs, and relationship targets. Label new paths,
APIs, and component names as proposed rather than implying they already exist. Do not invent
selectors or test results. When exact verification coverage is not yet known during initial
planning, identify the mapping work and its owner/action; resolve any material coverage gap
before implementation readiness.

Map relevant existing cases or static checks to the scenario or criterion they verify.
For runtime and test changes, follow TEST_POLICY.md and the canonical verification rules;
this mapping does not authorize extra scenarios, relaxed assertions, broader budgets,
or replacement of a required real boundary with a helper test. For documentation and policy
work, map criteria to the affected headings, links, formats, references, and policy invariants.

Link canonical common rules instead of repeating the same full policy in each child.
Keep the issue-specific application of a rule visible. If a numeric limit or baseline
observation is necessary, include its unit, scope, source revision, and purpose.
Distinguish the accepted comparison baseline, the policy revision inspected, and later
execution evidence; a historical number or pass is not a current result.

Explain dependencies with descriptive Issue links and the reason the dependency is needed.
An internal plan identifier may accompany the link but should not be the only way to locate
the work. Native relationships and metadata remain authoritative.

### Updating an existing Issue

Preserve material detailed requirements, examples, failure cases, constraints, and their
verification mappings when improving structure or concision. The durable detailed plan stays
in the owning Issue's Implementation plan section. Do not replace it with a summary or move
its essential content behind a general parent link.

Update the summary and affected criteria when a material design decision changes, while
retaining the reason and relevant history. Make the current conclusion clear; do not leave
contradictory current instructions in successive appended sections. Preserve stable
criterion references when possible and identify a superseded decision explicitly.
Removing repetition is appropriate only when the retained text and its exact reference
preserve the full applicable requirement.

## Reporting structure and supporting detail

Narrative work reports include progress, verification evidence, questions or decisions,
and completion comments. Their purpose determines the detail needed, not whether they
must explain their current conclusion. Use the status or evidence template that fits the
record; one evidence comment can serve as the update. Do not publish a duplicate status
comment merely to satisfy both forms, and do not hard-code an account name into this rule.

Begin with the supported conclusion, practical effect, and applicable scope. Distinguish
implementation, observed verification, review closure, integration, and Issue completion.
Explain the change since the previous relevant report; the first report names its starting
state. Keep failed, unexecuted, uncertain, and materially limited results visible. State
remaining criteria and one owned next action. If a decision is needed, name the actual
decision, why it is needed, the options or recommendation, and its owner. Otherwise state
that no decision is requested; do not create a new approval for routine authorized work.

A parent report summarizes completed child outcomes, current work, remaining dependencies,
and shared risks. A child report explains its own criteria, implementation, verification,
and remaining action. Link the detailed child report instead of copying it in full into
the parent. Child completion alone does not establish parent completion.

Keep the conclusion, material failures, limitations, blockers, and requested decisions
outside collapsed details. Complete method ledgers, raw repeated measurements, long hashes,
exact ID inventories, reproduction metadata, and administrative detail belong in details
blocks or descriptive artifact links when they interrupt the main reading path. Preserve
necessary detail; never shorten text by dropping requirements or joining words.

Use normal spacing and short sentences. Define unfamiliar terms on first relevant use.
A short SHA may be used for display if unambiguous; retain the complete observed identity
in evidence and exact protocol fields. A local path is a local-only record, not a link
other readers can open. Name the artifact and access limitation when an accessible link
is unavailable; do not claim that linking a CI run proves a particular artifact is present.

Every reported number identifies its population, unit, denominator where relevant, and
observation scope. Distinguish selected or scheduled methods, started/completed outcomes,
accepted skips, unexecuted work, subTests, and failure-detail counts. Reconcile counts only
when their categories are mutually exclusive for the same population. Do not calculate an
improvement rate across different selections or conditions. Round summary values only when
the decision remains clear; preserve exact raw values and material comparison conditions.

A report does not update native Project state or authorize execution, publication, risk
acceptance, or a new lifecycle stage. Fill the record from actual evidence and existing
authority. Do not run checks or initiate Issues, PRs, or reviews just to fill a field.

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
or static checks, outcome, failures/gaps, and assessment. Keep the supported conclusion and
coverage visible; retain detailed manifests, hashes, budgets, logs, and reproduction metadata
in supporting details or linked artifacts. Label local-only, private, inaccessible, and
historical records honestly. For each material completion claim, make its scenario or
criterion and supporting result easy to locate. Never claim historical evidence applies
to the current head without checking the relevant source and other inputs.

### Evidence applicability and history

Describe applicability for named claims and conditions, separately from pass/fail and
lifecycle status. These are reporting terms, not new workflow states or a runtime schema.

- Valid: supports the named claims for the recorded snapshot and conditions. This alone
  does not establish applicability to a later head.
- Partially valid: identify the claims or measurements retained, those excluded, and why.
- Superseded: a later record is now used for a named decision or scope; link it and retain
  the earlier observations as history. Superseded does not mean the observations were false.
- Excluded: do not use the named evidence for the specified judgment; state the reason
  and retain the original observations. Exclusion is specific to the stated claims.
- Not established: applicability is unknown; identify the missing check or evidence.

A failing run can be valid evidence of failure. Missing, cancelled, unexecuted, incomplete,
or stale verification is not a pass. If a report supports different claims differently,
state the applicability per claim instead of forcing one misleading status on the record.

When interpretation, source, measurement conditions, or an accepted criterion changes,
record the previous and current basis, affected claims, and the authorized decision or
evidence link. Use the available source for a maintainer decision; if it is not linkable,
identify the decision and scope without inventing a URL or timestamp. Keep the current
summary and the owning Issue's affected criteria consistent with the accepted decision.

Preserve original observations. Do not delete an earlier comment or silently rewrite its
result. When an authorized earlier narrative record can be annotated, add a clearly labeled
scope/status note and a forward link while preserving the historical body; the new record
links back. Otherwise record the relationship in the current report or evidence index.

Do not annotate or reformat canonical review triggers, protocol markers, hash-bound
requests, or provenance-bound machine records. Their exact text and history remain governed
by GITHUB_WORKFLOW.md. An editorial change or applicability note alone does not start a new
verification run, review request, review round, or reopening of a closed review.

## Reusable authoring prompts

Use these prompts with the real inspected inputs and requested language. They produce
drafts under the authority already established for the task. They add no publication,
implementation, review, or integration authority. The source templates retain placeholders;
published Issue and comment bodies do not.

### Issue draft or revision

~~~text
Draft or revise the owning Issue using the complete work_item.md template and the
issue-pr-authoring guide. Use the requested language and preserve the template's visible
headings and meaning.

Inputs:
- User objective and authorized scope.
- Inspected current behavior, relevant source revision, and accepted base when established.
- Existing Issue content, material decisions, constraints, and related native records.
- Affected components and any confirmed or proposed responsibility changes.
- Known existing verification cases or required static validation.
- Unresolved material questions and their current owner/action, if any.

Write the Issue as follows:

1. Open with the current limitation and useful outcome. Make this Issue's specific purpose
   apparent without requiring the reader to reconstruct earlier discussion. Qualify
   assumptions that have not been verified.

2. Describe named, realistic scenarios with starting conditions, an action, and an expected
   observable result. Internal maintenance and verification actions are valid scenarios.
   Distinguish observed facts from constructed examples. A preservation rule or test policy
   alone is not a scenario.

3. Keep Implementation summary brief but specific. State the major mechanism or
   responsibility change and surface any important preserved boundary. Put the complete
   design and executable steps in Implementation plan.

4. Write issue-specific, independently assessable acceptance criteria. Connect them to
   the scenarios and relevant success, failure, recovery, compatibility, or boundary
   outcomes. Common CI/review/merge rules do not substitute for these criteria.

5. Explain scope and dependencies. Link an actual related Issue when its identity is known,
   and say why the dependency matters. Keep native fields and relations authoritative.
   Do not copy the complete parent policy into this Issue.

6. Make each material implementation step identify the affected component, intended change,
   resulting responsibility or interface, material preserved boundary, and the scenario
   or criterion it enables. Include the necessary order and failure handling. Do not
   stop at a generic inspect/move/test/merge sequence.

7. Map planned verification to the criteria using inspected existing test IDs or selectors,
   or specific static checks for non-runtime work. State material selection or expansion
   conditions. Distinguish planned checks, observed results, historical evidence, and gaps.
   Do not invent commands, existing paths, IDs, measurements, or successful checks.
   Label proposed new paths, APIs, and components as proposals.

8. If a material fact or choice is unresolved, record the question, its impact, and the
   next owner/action. Do not fabricate a completed plan or imply Ready status. Resolve
   material planning gaps under the existing workflow before first implementation;
   routine implementation choices do not require a separate approval.

9. Preserve existing material detailed requirements, examples, edge cases, constraints,
   and verification mappings when revising. Update the summary when a material decision
   changes, identify superseded decisions, and keep the complete current plan in the
   owning Issue's Implementation plan section.

10. Use the simplest structure that makes the work clear. A small Issue may use short
    paragraphs and a short list. Use a table only when a comparison or mapping benefits
    from it. Do not add filler scenarios, arbitrary length limits, or mandatory matrices.

Before returning the draft, check that a reader can identify what changes, what remains
stable, what proves completion, how the components produce the result, and which material
questions remain. Remove placeholders and source-template examples. Keep all visible
headings and preserve the full applicable requirements.

Return the complete proposed Issue body. Do not infer publication, Project-state changes,
implementation, or another lifecycle action from this authoring request.
~~~

### Narrative work update

~~~text
Draft one narrative work update using the complete working-status-comment.md template
and issue-pr-authoring guide. Use the requested language. Preserve the template's visible
headings and meaning, and follow the task's existing publication authority.

Inputs:
- Owning Issue and current acceptance criteria; parent/child context when relevant.
- Previous relevant reports by the designated reporting author or automation, regardless
  of whether their content is progress, evidence, decisions, or completion.
- Changes since the last report and observed implementation, verification, review,
  integration, and closure states, with their source snapshots and evidence links.
- Failed, skipped, unexecuted, excluded, or uncertain results and material limitations.
- Remaining work, actual blockers, next owner/action, and any genuinely needed decision.

Write the current conclusion and practical effect first. State the scope and distinguish
implementation from observed verification, review closure, integration, and completion.
Explain the new information since the previous report; do not repeat the entire plan.
On the first report, state the starting point. Name the criteria that remain unresolved.

Show important failures and limitations before supporting detail. Keep numbers tied to
their population, unit, and scope. Separate method counts from subTest or failure-detail
counts and distinguish accepted skips from unexecuted work. Link evidence for each material
claim. Do not turn a partial pass, review response, old head, or future plan into completion.

When a real decision is needed, state the question, reason, options or recommendation,
and decision owner. Otherwise write that no decision is requested. State one concrete
next action and owner. Do not invent an approval checkpoint or an additional task.

For a parent Issue, summarize child outcomes, remaining dependencies, and shared risk;
link child reports. For a child, explain that child's results and remaining criteria.
Do not paste the same full report at both levels or infer parent completion from a child.

Put long identifiers and administrative detail in supporting details; keep conclusions,
blockers, failed/unexecuted work, and decision requests visible. Preserve necessary evidence
and normal spacing. If earlier evidence changed applicability, identify the retained and
excluded claims and connect the earlier and current records under the history rules.

Return the complete proposed comment. Do not run tests, change native fields, create an
Issue/PR, or start a review to fill the template. Canonical review-control messages are
outside this narrative rewrite and must keep their exact required text.
~~~

### Evidence report

~~~text
Draft one evidence report using the complete evidence.md template and issue-pr-authoring
guide. Use the requested language and preserve the visible headings and their meaning.
An evidence report is also a reader-facing update; do not add a duplicate status comment.

Inputs:
- Scenario or acceptance criterion and the decision this evidence can inform.
- Actual source/base/head, selected scope, environment, commands or static checks, observed
  outcomes, failures, skips, unexecuted work, and available artifacts.
- Comparable baseline and candidate conditions when making a comparison.
- Earlier evidence, changed interpretations or conditions, and accepted criteria changes.
- Actual review records if relevant; remaining uncertainty and decision/next-action owner.

Open with the supported conclusion, applicable scope, and remaining completion conditions.
Describe evidence applicability separately from test outcome and lifecycle state: valid,
partially valid, superseded, excluded, or not established for the explicitly named claims.
A valid failed run remains failure evidence. A superseded record remains historical evidence.
Do not force one status onto claims with different applicability.

Give a short result summary that connects each material claim to its criterion and source.
Identify populations, units, denominators, measurement intervals, and comparable conditions.
Do not compare different selections as a speedup, subtract diagnostic overhead to manufacture
a pass, or combine overlapping cumulative timings. Keep raw precision in the detailed ledger.
Make material failures, exceptions, unexplained variation, and missing coverage visible.

Preserve exact commands/IDs, complete source identity, environment, review provenance, and
required JSON/shard records in supporting details or verified artifact links. Keep reviewed
base/head distinct from later verification. State local/private access limits honestly.
Do not invent artifact availability, commands that ran, model identity, or timestamps.

When replacing a prior judgment, show what changed, why, which claims remain usable, and
the actual replacement link. Preserve original observations. Annotate an earlier narrative
record only under existing authority and the guide's history rules; never edit canonical
review triggers or provenance-bound machine records.

End with supported and unsupported criteria, residual uncertainty, and the responsible
next action or genuine decision. Do not claim overall completion when another gate remains.
Return the complete proposed comment only. Preparing the report does not authorize extra
execution, acceptance, publication, a new Issue/PR, or another review round.
~~~

## Publish and verify

Check the finished body in source and rendered form when available. Confirm visible heading
order, links, lists, tables, fences, details/summary pairs, and removal of placeholders and
template-authoring comments from the published body. Keep required protocol markers intact.

For an Issue, verify that:
- The opening identifies the current limitation, useful outcome, and specific delivery.
- Use cases contain concrete actions and expected results, not only preservation rules.
- Issue-specific criteria cover the material outcomes and can be assessed independently.
- The detailed plan names affected components and intended changes, and connects them
  to scenarios or criteria. Workflow procedure alone is insufficient.
- Verification maps to appropriate existing cases/selectors or specific static checks;
  planned, observed, historical, proposed, and unresolved information are distinguished.
- Important decisions are visible in the summary and agree with the detailed plan and
  applicable accepted common rules.
- Restructuring has preserved material requirements, examples, edge cases, constraints,
  and the owning Issue's complete detailed plan.

For a report, verify that:
- The opening conclusion agrees with its result summary, applicable evidence, and actual
  lifecycle state. Failed, unexecuted, uncertain, and materially limited results stay visible.
- Changes since the previous record, remaining criteria, and next owner/action are clear.
- Counts, populations, units, comparison conditions, and rounding are not misleading.
- Evidence applicability and replacement links are traceable, including partial exclusions.
- Parent and child reports serve their respective scope, and detailed ledgers do not hide
  the conclusion, blocker, or requested decision.
- Normal spacing, understandable terms, descriptive links, and appropriate detail placement
  make the record readable without reconstructing earlier discussion.

This is an authoring self-check, not a new approval or execution gate. Publish only under
active workflow authority. Read the resulting record back and separately verify native
links, Project state, review closure, and current-head check status when applicable to the
active stage. A clear body does not prove those external states or gates changed.
An editorial correction alone does not require runtime tests or reopening a closed review.
