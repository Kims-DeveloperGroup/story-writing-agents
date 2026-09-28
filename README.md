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

It creates one strict schema-v2 workspace, four zero-byte core documents, and
one clean local `main` root commit named `Initialize story workspace`. The
initializer writes no story content. Repeating `story init` on that unchanged
workspace validates it without rewriting, staging, or committing again.

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
transaction. Nested repository work reuses that transaction with liveness and
named-root checks instead of repeating the full READY scan. A guard never spans
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
effect. For every Codex Lead, specialist, synthesis, consensus, or direct bound-
agent call, the runtime derives a call-local execution capability from that
binding. The child enters the descriptor-selected exact session worktree and
uses `--cd .`, so both its OS working directory and native project root are the
same pinned generation, never prompt staging or the control workspace. On
Darwin, a second default-deny sandbox grants the provider no session-tree read
or write access and explicitly denies `.git` and prompt staging. Host-owned
context, explicit-reference, SEARCH, and READ services select and deliver story
content through the provider payload instead. Closed exceptions admit only
the literal canonical Codex executable, required system runtime files,
transport, outbound network access, and an existing absolute owner-controlled
`CODEX_HOME` that cannot overlap the control root, the schema-derived sibling-
worktree container, staging, or output transport. Bound instructions, schema,
and response transport remain outside story Git. The response is accepted only
from a no-follow, identity-stable, singly linked regular file containing strict
UTF-8 and no more than 8 MiB. The
deprecated Darwin sandbox facility is fail-closed when unavailable, and an
unsupported host does not fall back to an unconfined Codex story call.
Codex also rejects a fully encoded `developer_instructions=` argument above
512 KiB before submission; large custom bindings should deliver story material
through host-owned context, references, or SEARCH/READ instead.
Deferred Lead publication retains its execution and provider contexts until
publication resolves. Cleanup and generation revalidation finish before the
provider-call finalizer is accepted; failure after checkpoint commit invalidates
that exact committed generation. Direct execution-workspace ownership also
revalidates currentness on exit; exit loss prevents an apparent success from
returning, while an active body failure remains primary.
Antigravity adopts the corresponding worktree boundary separately in
[Issue #706](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/706).
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
  a replacement. Failure to publish the follow-on Commit request after the
  Apply result is durable does not yet have its own durable failure-state record;
  that separate work remains in
  [Issue #662](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/662).
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
- [Safe Git baseline, session resources, and exact internal commits](docs/reference/git-adapter.md)
- [System design](docs/design/overview.md)

## Development

The Python package is under `src/story_writing_agents/`; tests are under
`tests/`; role prompts are under `config/agents/`; and
[PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md) is the production-module map.

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

For runtime or test changes, CI requires two complementary full-suite shards.
For a local full-suite run, use the same commands. Start no more than these
two test processes concurrently, preserve both reports, and then reconcile
them:

```bash
report_dir="$(mktemp -d "${TMPDIR:-/tmp}/story-tests.XXXXXXXXXX")"
chmod 700 "$report_dir"
PYTHONPATH=src python -m tests.runner run --shard 1/2 --json "$report_dir/shard-1.json" &
shard_one_pid=$!
PYTHONPATH=src python -m tests.runner run --shard 2/2 --json "$report_dir/shard-2.json" &
shard_two_pid=$!
shard_status=0
wait "$shard_one_pid" || shard_status=1
wait "$shard_two_pid" || shard_status=1
test "$shard_status" -eq 0
PYTHONPATH=src python -m tests.runner reconcile \
  "$report_dir/shard-1.json" \
  "$report_dir/shard-2.json" \
  --json "$report_dir/reconciliation.json"
PYTHONPATH=src python -m tests.suite_budget --base-ref main \
  --report "$report_dir/shard-1.json" \
  --report "$report_dir/shard-2.json"
```

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
1,080 seconds sequential equivalent and 540 seconds per shard. The checked-in
budget contains exact canonical IDs and per-ID subtest caps; the CI gate compares
it with the base commit and current Python 3.11 reports. Class timing weights
only balance the two shards; they do not prove the run is under budget.

<a id="contribution-guidelines"></a>

Contributors must follow [AGENTS.md](AGENTS.md),
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), and
[COMMIT_POLICY.md](COMMIT_POLICY.md). For tracked work, load
[`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md).
