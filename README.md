# story-writing-agents

A Python 3.11+ runtime for building a lead-led, multi-agent novel-writing
workflow. The Python API supports ordinary conversation with the Lead
Orchestrator, strict lead outcomes, runtime-built specialist proposals, exact
Confirm/Reject/Revise decisions, confirmed specialist execution, and typed lead
synthesis through both Python and the installed interactive shell.

## Current integration stage

Public direct-run paths remain absent. The selected runtime uses one
authoritative schema-v2 collaboration-history service for both provider-free
inspection and the Python lead front channel.

| You can do now | You cannot do in this stage |
| --- | --- |
| Initialize and verify one strict Git-backed schema-v2 writing workspace. | Address a specialist directly from ordinary input. |
| Create, resume, list, and pause story sessions. | Send story input before attaching a session and focusing a conversation. |
| Create, list, focus, rename, archive, search, and read conversations. | Start or advance a debate/consensus run from the installed CLI. |
| Apply directives, edit workspace settings, and attach multiple whole-file or ranged references to lead input. | Treat a file indicator as embedded content or optional reading. |
| Inspect session, debate, history, transcript, status, and recorded exchanges. | Start a debate through the runtime facade. |
| Address the Lead by ordinary focused shell input or Python; confirm a one-to-three-specialist consultation; receive typed replies and synthesis. | Treat conversation text such as `Yes` as confirmation. |
| Ask the lead to form an exact create/patch/replace/rename/delete candidate; confirm its disclosed specialist review; inspect an immutable reviewed proposal. | Treat proposal or review confirmation as write or Git authority. |
| Separately confirm Apply for an exact reviewed artifact candidate, then separately confirm Commit for one exact local session-branch commit. | Push, merge, or otherwise publish the local Git commit remotely. |
| Use the Python API to submit a prompt or complete draft, inspect and separately confirm every consensus phase, interrupt active work, inspect durable state after restart, and continue an exact synthesis deadlock in place or as a linked workflow without copying its candidate. | Start or advance consensus from the installed shell, treat finalization as artifact publication, or reuse confirmation authority after restart. |
| Use schema-v2 collaboration contracts, stores, archive/transfer, readable projection, search, and paging APIs. | Bypass the runtime with a removed direct-run method. |
| Run a disposable management/storage smoke or verify an existing workspace without changing it. | Use smoke as evidence of provider behavior or story quality. |

The runtime also has package-internal, provider-free Git infrastructure. It can
inspect, list, provision, and require one exact session branch, sibling
worktree, and internal ref. It can now durably record content-free candidates
and monotonic commit/checkpoint operation states under the common Git
directory, serialize them with provisioning, and run fixed Git operations
through closed process profiles. The package-internal story-commit service can
now prepare an exact content-free candidate and execute one literal local
session-branch commit after a fresh structured Commit decision. A separate
package-internal checkpoint service can commit exact owner-supplied runtime
state deltas to only the session internal ref through an isolated index. None
of these internal commit or checkpoint services is a user command. Installed
session lifecycle paths provision and verify correlated Git resources.
Artifact application and local commit are separately confirmed interactive
boundaries. No CLI or lead path invokes checkpoints merely because a provider
session is `ready`.

Python callers can call `lead_turn()` and `decide_lead_step()` without an agent
selector. A lead turn can answer workflow/status questions or clarify a request directly.
Substantive story work requires every affected specialist domain. The Lead can
first recommend preparing a scoped consultation or artifact-review proposal.
Confirming the resulting disclosed specialist proposal records authority,
atomically records `running` plus every dispatch
before provider work, executes one to three specialists concurrently, and
returns an authoritative terminal or round-two turn. Specialists return drafts or reviews without writing files. The Lead integrates
all required drafts for the current task/revision before final candidate review.
A fresh finished user-supplied candidate may enter review directly when the Lead
discloses that interpretation. Mixed work requires the union of its affected
Plot, Character, and Execution domains. A lead artifact intent is resolved by
runtime against the complete
canonical artifact state. Runtime derives its reviewers and, after exact
confirmation, sends every reviewer the same immutable candidate. A successful
lead `propose` synthesis records a reviewed proposal whose Apply/write/Git
flags are all false.

The Lead can assign drafting to one required specialist and constraint assessment
to others. Shared premises and sources are disclosed before Confirm and remain
fixed for that task revision. Every affected specialist still reviews the same
final candidate, and preparation plus review inherits the drafting call budget.

A fresh recommendation discloses one preparation action: its purpose, targets
or specialists, sources, expected result, impact, and provider-call budget.
Confirm authorizes one Lead preparation. The resulting artifact-review or
consultation proposal has its own Confirm/Reject/Revise decision; Apply and
Commit remain separate. Reject starts no preparation. Revise takes nonempty
instructions and returns a new recommendation for fresh confirmation.

The lead interprets short follow-ups such as “Use that draft” from the session
context. Ordinary approval is a new authoring instruction and can supersede an
unconfirmed recommendation. With a clear target and current evidence, it can
prepare the exact candidate for separately confirmed review. Runtime retains
the authoring objective and its exact originating user request in that
conversation across discussion, consultation,
conversation switches, and restart. Only a matching successful Apply result
(`applied` or `unchanged`) satisfies it; review, prose approval, and Commit do
not. An explicit objective cancellation closes it without writing.

Pending Apply or Commit requests are shown again with their exact identities
when ordinary input arrives. After a known terminal Apply/Commit result, another
authoring cycle can begin with fresh review, Apply, and Commit requests.
Unknown outcomes and recovery states require resolution first.

After a session is attached and an active conversation is focused, every
ordinary non-slash line is sent literally to the Lead Orchestrator. The lead
can answer workflow/status questions directly or disclose a specialist drafting,
review, or artifact-review proposal.
Only the terminal's structured decision controls authorize that exact step.
Slash-prefixed management commands remain provider-free; removed execution
commands remain unavailable.

While a Lead or confirmed specialist request is active, capable terminals show
a transient shared `HH:MM:SS` working bar and per-agent context cards. SEARCH
cards may display exact query text, requested resource kinds and scope, and safe
workspace-relative locators/ranges; READ cards show selected locators and exact
boundaries. SEARCH and READ cards never display source excerpts, provider answers, absolute
paths, or hidden reasoning. During provider generation, active terminals may
display transient status indicators and a bounded rolling preview of streamed output
whose retention is mode-dependent (/progress on retains the frame for interactive inspection,
while /progress off clears it before final output). Final specialist request/answer/status panels
precede the Lead result and elapsed time. Terminal capture or scrollback may
retain the transient query, locator, or preview text.

See [Collaborate with the Lead Agent](docs/guides/lead-agent-collaboration.md)
for responsibilities, authority limits, concrete story workflows,
provider-call examples, visible states, and recovery behavior. The exact API
contract remains in [Lead agent workflows and reviewed artifact proposals](docs/guides/lead-agent.md).

Chat prose uses distinct colored message frames with Markdown presentation and
a labeled metadata footer. Frames adapt to new writes and live redraws, with a
40-column full layout, a 120-column maximum, and compact layouts for narrower
terminals. Input/output token counters come from provider response usage;
missing counters are displayed as unavailable. Messages remain complete in
ordinary terminal scrollback. See
[message frames and metadata](docs/reference/interactive-terminal.md#message-frames-markdown-and-response-metadata)
for the layout, color fallback, and usage-attribution rules.

## Install

Choose one Python environment. Do not create a standard-library virtual
environment inside an active Conda environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story --help
```

`story` and `story-writing-agents` are exact command aliases. The normal
installation includes the isolated read-only JSONL viewer used by
`/exchanges`. Optional local conversation RAG requires the reviewed `rag`
extra before it can be enabled; installation alone never enables it.

The interactive shell first performs its configured native provider
authentication gate. When the workspace has no `default_backend`, a separate
**Default provider** picker then opens before the lobby. A newly authenticated
or sole authenticated provider—or Codex when both authenticate—is highlighted
only as a recommendation; nothing is preselected. Choose either enabled
provider to save it as the workspace default. Choosing an unauthenticated
provider starts its existing native sign-in first. Cancelling, interrupting, or
ending this picker exits before saving a default or entering the lobby.

Later shells skip this first-start choice, and agents that inherit the saved
default keep an editable Provider setting. Only an explicit `--backend` mapping
is a one-run launch override: its Provider setting is read-only in that shell
and it never rewrites configuration. Existing defaults and explicit per-agent
routes are preserved. The shell also exposes `/auth` and provider settings.
Ordinary focused lead input and confirmed collaboration can invoke the
configured provider; management and inspection commands retain their
documented provider-free behavior.

## Quick start

Configure an effective Git author and committer according to your normal Git
policy, then initialize an absent path or empty directory:

```bash
story init ./my-story
cd ./my-story
story smoke --workspace .
```

Successful initialization prints the absolute workspace path:

```json
{
  "workspace": "/absolute/path/to/my-story"
}
```

In a terminal, `story init` then opens the interactive shell in that workspace,
including its normal authentication and default-provider setup. You can create
or reopen a session immediately. The parent terminal stays in its current
directory; after quitting Story, use `cd ./my-story` for subsequent commands.
When streams are redirected, initialization prints only JSON and exits without
starting the shell or authenticating providers.

It creates one strict schema-v2 workspace, four zero-byte core documents, and
one clean local `main` root commit named `Initialize story workspace`. The
initializer writes no story content. Repeating `story init` on that unchanged
workspace validates it without rewriting, staging, or committing again.
In a terminal, successful validation also opens the shell in that workspace.

The initialization root is the pristine `main` baseline. `story init` records
the session branch/ref/worktree grammar but creates none of those resources.
Session Create and Reopen use the package-internal resource seam to provision
or verify the exact correlated triple while leaving that root clean.
Do not start mutable session work in the baseline or treat initialization or
resource provisioning as authorization for a later story commit.

The installed shell retains this management grammar and attaches an eligible
session destination only after transition assessment accepts the isolated
session worktree:

```text
/session create novel
/conversation new Opening structure
/status
/history
Draft a global outline in ${core_documents/global_outline.md}: a courier loses a letter, retraces the route, and returns it.
/conversation list
/search opening
/quit
```

A newly created conversation has no collaboration events. `/history` reports
state `empty`, its user meaning, and `none` as the next safe action. `/search`
does not match the conversation subject: it searches readable event fields
only, while the subject is display metadata on a hit.

Ordinary focused input addresses the Lead without an agent selector:

```text
story[novel]> Draft a global outline in ${core_documents/global_outline.md}: a courier loses a letter, retraces the route, and returns it.
[Lead]
  Outcome: proposal
  Lead: I propose Plot drafting from your brief for the empty global outline.
  Next action: submit_confirmation
```

This synthetic example supplies a drafting brief for the initialized empty
outline; it does not assume a manuscript already exists. Exact wording comes
from the configured provider; the typed fields and
authority boundaries are stable. If the lead recommends preparation or proposes specialist work, the
shell opens a Confirm/Reject/Revise picker. Free-form `Yes` is contextual Lead
input. It can express approval of a visible draft, but it never confirms
review, Apply, or Commit.

## Provider-free smoke

```bash
story smoke
```

Without `--workspace`, smoke creates a temporary workspace through the real
initializer, exercises management/storage wiring, and removes the complete
directory. It verifies exactly these checks:

```json
{
  "checks": [
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "collaboration_history_empty",
    "collaboration_readable_context_empty",
    "collaboration_search_empty",
    "collaboration_page_empty",
    "execution_artifacts_unchanged"
  ],
  "provider_tested": false,
  "status": "passed"
}
```

The actual report is compact one-line JSON and also includes generated session
and conversation IDs plus workspace metadata. It proves management, catalog,
and empty schema-v2 projections work together. It creates no transcript,
debate, specialist state, provider exchange, or manuscript. Because it invokes
the real initializer, a missing effective Git identity is reported rather than
silently substituted.

`story smoke --workspace PATH` is different: it requires an already clean v2
workspace and performs only `workspace_initialized` and
`execution_artifacts_unchanged`. Its session and conversation IDs are `null`,
and it creates no directive, catalog record, history, debug entry, or other
residue. See the [smoke guide](docs/guides/smoke-test.md).

## Supported commands

Top-level commands are:

```text
story [chat [SESSION]]
story init [WORKSPACE]
story directive [--workspace PATH] [--session ID] TEXT
story inspect [--workspace PATH] [--session ID] [--debate ID]
story session {create|reopen|pause} ...
story storage {use|transfer|verify} ...
story rag {status|enable|rebuild|search|disable|purge} ...
story smoke [--workspace PATH]
```

There is no noninteractive story-execution command. In the shell, use `/help`
for the retained command registry. The management and inspection commands are
`/agents`, `/auth`, `/settings`, `/override`, `/session`, `/conversation`,
`/search`, `/transcript`, `/inspect`, `/exchanges`, `/history`, `/status`,
`/help`, and `/quit`.

Conversation restoration shows the latest 20 saved user, Lead, and specialist
messages. `/transcript` opens an isolated, scrollable message-only snapshot.
Without a count it shows all selected messages; a count limits messages and
`--from` keeps its stored-event sequence anchor. `/history` and `/exchanges`
retain detailed diagnostics. Closing clears the view and restores the preceding
shell screen, matching `/exchanges`.

The exact grammar is in the [CLI reference](docs/reference/cli.md) and
[interactive parser reference](docs/reference/interactive-command-parser.md).

## Python integration

`StoryRuntime` is a management, configuration, directive, inspection, and lead-
orchestration facade. It can bind a `RuntimeSession`; both expose async
`lead_turn()`, `propose_specialist_advisory()`, `decide_lead_recommendation()`,
and `decide_lead_step()` methods. Confirmation executes the disclosed read-only consultation through
`decide_lead_step()`. Separate artifact-application and local story-commit
decision methods consume only their own exact structured controls. Both
facades also expose a phase-confirmed consensus API:
submit prompt/draft input, inspect, decide or confirm the exact phase,
explicitly request its successor, interrupt, and resume inspection. There is
also an explicit synthesis-deadlock continuation call that binds the source result,
unresolved critique material, operator ruling, and added repair-round budget;
it prepares a fresh REPAIR confirmation and never copies candidate text. There
is no public unconfirmed specialist dispatch, direct artifact-publication
bypass, consensus shell command, or general Git method. The package root no longer exports the broad
`Runtime` or `Session` aliases.

Construct `StoryRuntime` from the initialized `main` workspace. That root is
the control plane for configuration, conversation history, and diagnostics;
it is not the story-artifact root for an active session. Every
artifact-sensitive session operation freshly validates the session's exact branch/ref/
sibling-worktree identity and pins the worktree's physical `(st_dev, st_ino)`
identity. Each repository or session-source step uses a short synchronous
outer guard: it holds the cooperating-runtime Git lease and verifies the exact
semantic READY resources and named physical root once before and once after the
transaction. Nested repository operations retain their own fresh READY checks.
Within one inspection, bounded Git object batches read the baseline and session
seed commits, actual trees and manifest blobs; these bytes are never reused across
inspections. A guard never spans
a provider await or specialist concurrency; the next filesystem transaction
reacquires and revalidates it. Direct Lead and Lead-synthesis paths instead
retain one opaque six-descriptor physical-generation pin through durable
result publication. That pin carries no semantic READY, traversal, or
repository authority, and no Git lease spans the provider wait. The
binding does not expose its repository directly: only live guarded access does,
and that access expires on exit. Exit revalidation runs after success, error,
or cancellation; detected binding loss takes precedence over the body result.
Artifact access and provider-visible active-debate or own-agent state then come
only from that worktree; conversation history retains its separately configured
control-plane storage. Replacing the named worktree does not transfer authority
to the replacement.
The runtime never falls back to `main`, the current directory, or a previously
used session, and it does not provision missing session resources as a side
effect. For every Codex or Antigravity Lead, specialist, synthesis, consensus,
or direct bound-agent call, the runtime derives a call-local execution
capability from that binding. The child enters the descriptor-selected exact
session worktree, so its OS working directory is the same pinned generation,
never provider staging or the control workspace. Codex also uses `--cd .`;
Antigravity treats that exact CWD as its native project root without an adapter-
control fallback. Story launches both native providers directly and does not
apply an outer macOS sandbox. Native provider processes are trusted programs
with the current user's OS permissions: Story does not prevent them from
reading unrelated files, writing session files or Git state, or starting other
programs. Codex retains its native read-only mode and disabled tools; Antigravity
retains its native sandbox, plan-mode and restricted-role settings. These
provider-owned settings are not an equivalent process-isolation guarantee.
Host-owned context, explicit-reference, SEARCH and READ services still select
story content through guarded runtime APIs. Structured Apply and Commit govern
Story's actions, rather than arbitrary native-process actions. Bound instructions,
schema and response transport remain outside story Git. Codex requires an
existing absolute owner-controlled `CODEX_HOME` that cannot overlap control,
sibling-worktree, staging or response-transport roots. Its response is accepted
only from a no-follow, identity-stable, singly linked regular file containing
strict UTF-8 and no more than 8 MiB. Descriptor-based launch requires POSIX
support; missing worktree identity or resource validation fails the call.
Codex also rejects a fully encoded `developer_instructions=` argument above
512 KiB before submission; large custom bindings should deliver story material
through host-owned context, references, or SEARCH/READ instead.
Deferred Lead publication retains its execution and provider contexts until
publication resolves. Cleanup and generation revalidation finish before the
provider-call finalizer is accepted; failure after checkpoint commit invalidates
that exact committed generation. Direct execution-workspace ownership also
revalidates currentness on exit; exit loss prevents an apparent success from
returning, while an active body failure remains primary.
Antigravity derives a content-qualified native selector from the semantic
story-agent ID and deterministic base role bytes. That exact selector is the
role's YAML name, `.agents/agents/<native-selector>/agent.md` directory, and
`--agent` value. Runtime revalidates this semantic/native/final-byte plan and
recomputes the staged file and binding byte accounting from the immutable bound
definition before provider workspace setup. Semantic agent identity still owns
routing, settings, accounting, and external provider state. Each protected
`0400` role leaf is published create-only, remains unversioned, and can coexist
with older role versions; runtime never replaces or prunes it. Definitions and
mutable provider state remain external beneath the canonical control workspace;
provider state is generation-qualified, while provider CWD stays in the session
worktree and schema/output transport also stays external. Codex continues to
inject developer instructions directly and requires no worktree configuration
file.
Create or reopen the session through the lifecycle first.

```python
from story_writing_agents import StoryRuntime, Workspace

workspace = Workspace("/path/to/initialized-story")
runtime = StoryRuntime(workspace)
runtime.apply_directive("novel", "[SYS: LANG=EN]")
session = runtime.session("novel")
print(session.inspect())
```

With a configured ready backend and active conversation, address the lead
without an `agent_id`:

```python
result = await runtime.lead_turn(
    "novel",
    conversation_id,
    "Check ${core_documents/global_outline.md} before recommending a change.",
)
print(result.kind, result.next_action)
```

Lead result, proposal-intent, structured-decision, and provider-call-accounting
values are exported from `story_writing_agents.conversations`, not the package
root. Consensus values are exported from `story_writing_agents.consensus`.
Exact calls are in the [runtime API](docs/reference/runtime-api.md), with the
complete stepwise flow in [Consensus runs](docs/guides/consensus-runs.md).

Direct schema-v2 collaboration-history values and pure projections are
available through `story_writing_agents.conversations`; direct search, paging,
and citation values are in `story_writing_agents.conversation_search`.
Filesystem/SQLite stores, archives, transfer, and activation remain separate
infrastructure boundaries. Start with the [runtime API](docs/reference/runtime-api.md),
[collaboration-history reference](docs/reference/collaboration-history.md), and
[package API](docs/reference/package-api.md).

Explicit one-turn exploration is also available through the focused shell and Python API.
It presents hypotheses, alternatives and short examples as provisional material.
Selecting an idea for adoption starts a fresh ordinary authoring workflow with
required specialist drafting, complete candidate review and separate confirmation,
Apply and Commit decisions. See [exploration and adoption](docs/guides/lead-agent.md#exploration-and-adoption).

The Python API can record editorial directions, rejected alternatives, protected ambiguity
and setup obligations through explicit user controls. Exact revisions and reasons remain
in conversation history, with a cited current view for the Lead across conversations and
restart. See [editorial decisions](docs/guides/lead-agent.md#editorial-decisions) and the
[recording and inspection API](docs/reference/runtime-api.md#editorial-memory-methods).

## Canonical writing artifacts

The canonical reference-source paths are the four exact core-document paths and
eligible lowercase-`.md` files below `manuscript/`. Python `lead_turn()` accepts
ordered `${path}` and `${path}#Lx-Ly` content-free indicators. Runtime performs
a separate mandatory read from the exact session worktree before the substantive
lead call and retains only
the indicator/range/revision facts in the proposal. In focused Lead input, `$`
opens the canonical-artifact picker and `$$` opens the same picker followed by
the independent range viewer. Picker inventory and each selected-file read use
their own short guarded access, follow the currently attached session, and are
freshly validated on entry and exit. Both insert durable identity-only
indicators. See
[artifact references](docs/guides/artifact-references.md).

## Core document completion

Build the core documents for the entire intended work in this order:

| Document | Required whole-work coverage | Initial dependencies |
| --- | --- | --- |
| `core_documents/global_outline.md` | Plot progression, conflict, ending and essential settings | Current work scope and author direction |
| `core_documents/part_outlines.md` | Every intended segment, connections and later development | Complete global outline |
| `core_documents/character_sheets.md` | Roles, relationships, motivations, settings and necessary changes | Complete global and part outlines |
| `core_documents/narrative_rules.md` | Point of view, tone, expression, narration and exceptions | Complete global and part outlines, with sufficient style evidence |

Narrative rules may be prepared while character material is unfinished. Ordinary
manuscript drafting and revision require all four documents to be complete for
the current work plan and document revisions. A file, partial draft or statement
of intent alone does not establish completion. Existing manuscripts can be read
for analysis while the core documents are being prepared.

Completion findings retain the work extent, source baseline, document revision,
criterion-level reasons, cited evidence and missing author decisions. Deliberate
reader secrecy preserves the user's decision, scope and resolved author-side
facts; an undecided essential setting remains missing. Relevant changes require
the affected findings to be reassessed. Advice, source collection, organization
and eligible core construction can continue while later documents are uncreated.
Those uncreated documents are remaining work. Completion findings do not grant
candidate approval, Apply or Commit authority.

Inspect the current findings with
`StoryRuntime.inspect_core_document_readiness(session_id)` or
`RuntimeSession.inspect_core_document_readiness()`. They return
`CoreDocumentReadiness` or actionable `CoreDocumentReadinessNeeded` information.
The runtime restores these findings across conversation changes and restarts and
checks current evidence again before writer dispatch.

For Python consensus authoring, pass the actual output through `authoring_target`,
using `AuthoringTarget` from `story_writing_agents.core_document_readiness`.
For example, `AuthoringTarget.MANUSCRIPT` requires all four core documents.
An omitted or ambiguous authoring target returns `CoreDocumentReadinessNeeded`
before a request or provider call. Review-only consensus can proceed without an
authoring target.

## Important limits

- Interactive and Python lead conversation, proposal decisions, confirmed
  specialist execution, typed synthesis, and separately confirmed stepwise
  consensus are active. Consensus has no shell command. Artifact Apply and
  local Commit use their own interactive confirmations.
- Confirming a consultation starts only the disclosed read-only provider work.
  It never confirms an artifact change or Git commit.
- Initialization accepts only an absent/empty root or one exact resumable
  initialization journal. It never adopts, overwrites, upgrades, or migrates a
  legacy or arbitrary nonempty directory.
- After that strict initialization has completed, journal-free readiness
  validates only the required scaffold paths and exact Git invariants. Unknown
  untracked or ignored files, directories, symlinks, and other entries are
  opaque and permitted: readiness does not enumerate or consume them, and
  their owning provider/runtime must still validate them before use. Staged
  changes and tracked unknown additions remain recovery conditions.
- A required-path-drifted, staged, or disallowed-tracked original `main` root
  is preserved and reported as requiring recovery. Exact correlated session
  branches, sibling worktrees, internal
  refs, content-free session history, exact confirmed story commits, and
  closed-path internal checkpoints are supported internally. These internal
  capabilities do not grant the lead Apply, commit, or checkpoint authority.
- Missing, moved, mismatched, or otherwise non-ready session Git resources
  stop artifact-sensitive work before provider or artifact I/O. There is no
  fallback or automatic provisioning. If binding or physical-root validation
  fails after Apply was durably confirmed but before the repository's
  `apply_artifact_changes()` method starts, runtime records
  `recovery_required` / `operator_recovery` and does not start artifact
  application. Once that method has started, an unclassified root, security,
  or format failure is conservatively
  `outcome_unknown` / `inspect_and_resolve_outcome`.
- Session-source reads accept only canonical relative POSIX paths and unique
  regular-file leaves. Symlinks, FIFOs, devices, hard-linked leaves, unstable
  file identity/metadata, and foreign embedded session/debate/agent ownership
  fail closed before content is accepted.
- The cooperating-runtime Git lease is advisory to this runtime. Persistent
  external Git drift is detected at the next guard checkpoint, but arbitrary
  noncooperating Git that mutates and restores state between checkpoints is
  outside the guarantee. Stop workspace processes before external recovery.
- A Commit request prepared after successful Apply remains pinned to that
  Apply worktree's physical identity through candidate preparation,
  authorization, and execution. Replacement before durable Commit execution
  requires repreparation; uncertainty after execution starts requires
  inspection. Identity-bound Commit inspection and recovery also require the
  original Apply root and never advance durable operation state after detecting
  replacement. If follow-on Commit-request publication fails after a durable
  Apply result, verified result-only history projects
  `reprepare_commit_request` across restart. Repreparation publishes a fresh
  request that still needs a structured decision; ambiguous publication
  readback requires inspection.
- Smoke is intentionally provider-free and story-free.
- Workspace configuration, histories, diagnostics, and artifacts are local
  plaintext unless the surrounding environment protects them.
- `/exchanges` can reveal bounded provider-call diagnostics from earlier or
  externally produced records; the viewer is not a redaction boundary.
- Conversation storage selection applies to the next shell and is separate
  from transfer. Transfer accepts schema-v2 collaboration history only.
- Optional RAG lifecycle and cited local search remain available, but the
  interactive one-turn provider preflight is not.

## Documentation

- [Documentation index](docs/INDEX.md)
- [Getting started](docs/getting-started.md)
- [Story sessions and conversations](docs/guides/story-sessions.md)
- [Lead story workflows and reviewed artifact proposals](docs/guides/lead-agent.md)
- [Provider-free smoke](docs/guides/smoke-test.md)
- [CLI reference](docs/reference/cli.md)
- [Runtime API](docs/reference/runtime-api.md)
- [Agent JSON contracts, schema construction, and field guidance](docs/reference/provider-wire.md)
- [Safe Git baseline, session resources, and exact internal commits](docs/reference/git-adapter.md)
- [System design](docs/design/overview.md)

## Development

The Python package is under `src/story_writing_agents/`; tests are under
`tests/`; role prompts are under `config/agents/`; and
[PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md) is the production-module map.

A Lead response-contract change must update its final offered schema, bound
validation, and source-authored repair requirements together. Check strict
field/value rules and branch relationships after the final provider projection;
a successful repair does not establish schema alignment.

For repeated JSON payload checks, `diagnose_value` and `diagnose_json_text` in
`structured_output_validation` also accept an exact `SchemaRules` owner. Declare
each final schema once with its existing byte budget and retain that owner for
the repeated calls. It reuses immutable schema preparation while each payload
gets fresh validation state. Raw mappings keep their original diagnostic order;
the JSON entry point retains strict parsing. Native projection checks still use
the original raw schema with `require_projection`.

Run tests from the repository root in the same Python 3.11+ development
environment used to install the project. The canonical runner uses only the
standard library and records the exact selected IDs, results, timings, policy,
and environment in JSON. Tests are expected to use local fakes and subprocesses,
not live provider calls; do not supply provider credentials or treat live
provider behavior as suite evidence.

For fast feedback on runtime-code changes, run the fast lane:

```bash
report_dir="$(mktemp -d "${TMPDIR:-/tmp}/story-tests.XXXXXXXXXX")"
chmod 700 "$report_dir"
PYTHONPATH=src python -m tests.runner run --tier fast --json "$report_dir/fast.json"
```

Run the relevant test modules with exact `--module` selectors (or `--id` for
individual tests), then record the selected IDs, result, and head SHA in the PR.
The implementer chooses modules from the changed behavior; CI does not infer them
from source paths:

```bash
report_dir="$(mktemp -d "${TMPDIR:-/tmp}/story-tests.XXXXXXXXXX")"
chmod 700 "$report_dir"
PYTHONPATH=src python -m tests.runner run \
  --module tests.test_artifact_proposals \
  --module tests.test_artifact_proposal_orchestration \
  --json "$report_dir/affected.json"
```

For runtime or test changes, CI requires five complementary full-suite shards.
Hosted runtime fast and full jobs use the standard four-core Ubuntu 24.04 ARM64
runner. Static validation, reconciliation and deadline control use Ubuntu 24.04
x64 runners. They retain the same locked dependencies and test inventory.
Full-suite selection includes test-only changes. The fixed timing limits still apply to every
selected full shard; a diagnostic run never replaces this evidence.

The original four-bin assignment is retained, with only
`LeadOutcomeAndProposalTests.test_front_schema_is_minimal_closed_and_provider_strict`
extracted into dedicated shard `5/5`. The five selections contain
577/554/704/691/1 tests, covering all 2,527 IDs exactly once. The other schema tests
keep their original assignments.

To measure whole-front independently, manually dispatch `pr-validation` with
`validation_scope: whole-front`. That mode runs static validation, focused policy
checks and only the dedicated whole-front job; fast, the four regular shards and
full-suite reconciliation are skipped. Retain completed reports while investigating
whole-front. An isolated report supplies partial evidence and never implies a
complete-suite pass. Normal PR/merge-group validation includes all five shards.
The dedicated canonical selector is:

```bash
PYTHONPATH=src python -m tests.runner run --shard 5/5 --json whole-front.json
```

Hosted full shards use the fixed `audited-147-v3` profile of 147 fixture-audited
methods across 17 classes to run at most four fresh test processes after their
original noneligible serial suite. The frozen launch order starts whole-front,
then seven other v40 failed or unexecuted methods in canonical order, then 139
passed methods by descending v40 observed duration with canonical tie-breaks.
Runtime history cannot change this order, and final report rows stay canonical.
Every process shares the shard's original one-hour work cutoff; shutdown has a
separate 30-second bound. Fast, affected and local runs remain serial. See
[TEST_POLICY.md](TEST_POLICY.md) for eligibility, source reconciliation and cleanup
requirements.

Independent task threads share a host queue with four executing test processes;
a campaign schedules five full shards within those four slots. Local `tests.runner run` commands
enter this queue automatically. Queue waiting is separate from test wall time.

Use the host campaign command to execute the lanes selected by the pinned
workflow, plus the affected modules you specify. It captures static checks,
fast and affected tests, all five full shards, reconciliation, and the strict budget
check. Prepare a clean checkout and its locked Python 3.11 `.venv` first:

```bash
base_sha="$(git rev-parse origin/main)"
head_sha="$(git rev-parse HEAD)"
report_dir="$(mktemp -d "${TMPDIR:-/tmp}/story-local-ci.XXXXXXXXXX")"
chmod 700 "$report_dir"
PYTHONPATH=src .venv/bin/python -m tests.local_ci run \
  --checkout "$PWD" --base-sha "$base_sha" --head-sha "$head_sha" \
  --report-dir "$report_dir" --task "my task" \
  --module tests.test_artifact_proposals \
  --module tests.test_artifact_proposal_orchestration \
  --timing-mode isolated
```

Choose `--timing-mode shared` for concurrent task feedback. Isolated mode drains
active executions and reserves the host for the campaign; use it for performance
measurements. Isolated campaigns run the five full shards sequentially on their
executor by default; shared campaigns permit four concurrently. Set
`--full-concurrency 1`, `2`, `3`, or `4` to choose explicitly. Campaign metadata records the
resolved value. The strict 18,000/3,600-second limits apply in either mode.
Run status and capability-authorized cancellation from a checkout containing
the queue utilities; campaigns can target older prepared checkouts with
`--checkout`:

```bash
PYTHONPATH=src .venv/bin/python -m tests.local_ci status
PYTHONPATH=src .venv/bin/python -m tests.local_ci cancel JOB_ID \
  --owner-file "$HOME/.cache/story-local-ci/jobs/JOB_ID/ownership.json"
```

The owner-only state lives outside checkouts under `~/.cache/story-local-ci`.
Workers create files and directories with an owner-only mask (`077`), including
in Lima, so new fixture repositories satisfy the directory safety guards.
Uncertain worker termination retains its permit in quarantine. Running-job
cancellation can retain a slot even after its original process group exits,
because separately spawned children may survive. Preserve the logs; only
complete death proof, including a verified executor restart, retires uncertain
workers, and their evidence remains invalid.
For Lima, run this same host command with `--executor lima:INSTANCE` and
`--guest-checkout /absolute/prepared/guest/checkout`. The guest needs matching
source and a locked Python 3.11 `.venv`; its workers use the same four host
permits. A guest must not start an independent local queue.

Each example creates a fresh, unpredictable, owner-only report directory. Keep
every report filename absent before its command runs: runner JSON output uses
exclusive creation, and reconciliation accepts only owner-controlled regular
files as shard inputs.

An unknown or empty selector is a visible configuration error and exits
nonzero instead of silently passing. A failing or interrupted report records
started results and `unexecuted_ids`; preserve that evidence, correct the
cause, and rerun only failed, unexecuted, or otherwise invalidated IDs with
repeated `--id` options while diagnosing. These partial reruns cannot be
combined into a complete shard report, so final reconciled evidence currently
requires a fresh complete run of the affected shard. Append-only resumable
evidence is separate Issue #659 and is not implemented here. Reconciliation
exits nonzero for failed, incomplete, overlapping, or non-exhaustive shard
reports.

The versioned timing weights in
`tests/fixtures/test_timing_weights_v1.json` record their measurement
provenance. Observed terminal class timings are used where available; unlisted
classes use the manifest's deterministic fallback, so the weights are a
partitioning input rather than proof of current performance.

The frozen suite scope and measured limits are in [TEST_POLICY.md](TEST_POLICY.md):
18,000 seconds sequential equivalent and 3,600 seconds per full shard. Hosted
full workers receive one hour of test execution. Full-shard jobs, the deadline
guardian and the shared workflow have 75-minute limits for setup and shutdown;
other jobs retain 60-minute native limits. The checked-in budget contains exact canonical IDs and per-ID
subtest caps. The gate compares it with the base commit, allowing only the
explicitly approved paired duration-policy transition, and current Python 3.11
reports. Class timing weights
balance the original four regular bins before whole-front extraction; they do not prove a shard is under budget.

<a id="contribution-guidelines"></a>

Contributors must follow [AGENTS.md](AGENTS.md),
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), and
[COMMIT_POLICY.md](COMMIT_POLICY.md). For tracked work, load
[`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md).

The original four-shard calibration is tracked in [Issue #846](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/846#implementation-plan). The dedicated fifth shard and isolated execution are tracked in [Issue #872](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872#implementation-plan).
Its plan covers partitioning, local scheduling and complete reconciliation while
preserving the existing suite cost limits.
