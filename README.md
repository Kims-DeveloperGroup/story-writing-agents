# story-writing-agents

A Python 3.11+ runtime for building a lead-led, multi-agent novel-writing
workflow. The Python API supports ordinary conversation with the Lead
Orchestrator, strict lead outcomes, runtime-built specialist proposals, exact
Confirm/Reject/Revise decisions, confirmed specialist execution, and typed lead
synthesis. The interactive lead UI remains a later integration step.

## Current integration stage

Public direct-run paths remain absent. The selected runtime uses one
authoritative schema-v2 collaboration-history service for both provider-free
inspection and the Python lead front channel.

| You can do now | You cannot do in this stage |
| --- | --- |
| Initialize and verify one strict Git-backed schema-v2 writing workspace. | Send an ordinary shell line to an agent. |
| Create, resume, list, and pause story sessions. | Route a line to an agent or select a sticky target. |
| Create, list, focus, rename, archive, search, and read conversations. | Start a debate or publish a manuscript from the installed CLI. |
| Apply directives and edit workspace settings. | Open `$`/`$$` artifact pickers from shell input. |
| Inspect session, debate, history, transcript, status, and recorded exchanges. | Start a debate through the runtime facade. |
| Call the Python lead API with ordered artifact references; confirm a one-to-three-specialist consultation; receive typed replies and lead synthesis. | Treat consultation confirmation as artifact, consensus, or Git execution. |
| Ask the lead to form an exact create/patch/replace/rename/delete candidate; confirm its disclosed specialist review; inspect an immutable reviewed proposal. | Apply that candidate, grant write authority, stage files, or commit through this stage. |
| Propose one provider-free direct specialist advisory and confirm its execution. | Apply a core-document/manuscript change or create a Git commit. |
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
session lifecycle paths provision and verify correlated Git resources, but
artifact application and commit confirmation remain separate later integration
boundaries. No CLI or lead path invokes checkpoints merely because a provider
session is `ready`.

Python callers can call `lead_turn()` and `decide_lead_step()` without an agent
selector. A lead turn can return a direct answer, question, critique, or
recommendation, or disclose a bounded consultation proposal. Confirming that
proposal records authority, atomically records `running` plus every dispatch
before provider work, executes one to three specialists concurrently, and
returns an authoritative terminal or round-two turn. Specialist advice is
read-only. A lead artifact intent is resolved by runtime against the complete
canonical artifact state. Runtime derives its reviewers and, after exact
confirmation, sends every reviewer the same immutable candidate. A successful
lead `propose` synthesis records a reviewed proposal whose Apply/write/Git
flags are all false.

When ordinary text, routed text, or a retired execution command is submitted in
the shell, Story reports:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The rejection happens before provider binding, transcript append, RAG,
artifact-reference reads, progress reporting, or artifact publication. #466
delivered the pure schema-v2 values; #474-#479 added readable projections,
storage, archive/transfer, and the sole active storage/inspection service.
#452 added lead proposal/confirmation writes through that same service; #453
adds specialist execution, typed synthesis, results, and restart-safe local
finalization. #454 owns whole-workspace rejection of unsupported history.

See [Lead agent: story workflows and reviewed artifact proposals](docs/guides/lead-agent.md)
for responsibilities, authority limits, four concrete story workflows,
provider-call examples, visible states, and recovery behavior.

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

The interactive shell still performs its configured native provider
authentication gate and exposes `/auth` and provider settings. This prepares
configuration for the forthcoming collaboration cutover. No currently
accepted shell content invokes a story provider.

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
/conversation list
/history
/search opening
/quit
```

A newly created conversation has no collaboration events. `/history` reports
state `empty`, its user meaning, and `none` as the next safe action. `/search`
does not match the conversation subject: it searches readable event fields
only, while the subject is display metadata on a hit.

An attempted story instruction is rejected without side effects:

```text
story[novel]> Please review the opening.
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The semantic renderer adds an error heading and may add decoration depending on
terminal capabilities; the sentence and behavior are stable.

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
`lead_turn()`, `propose_specialist_advisory()`, and `decide_lead_step()`
methods. Confirmation executes the disclosed read-only consultation through
`decide_lead_step()`; there is no public unconfirmed specialist-dispatch,
debate-start, artifact-publication, consensus, or Git method. The package root
no longer exports the broad `Runtime` or `Session` aliases.

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
root. Exact decision code is in the [runtime API](docs/reference/runtime-api.md).

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
a separate mandatory read before the substantive lead call and retains only
the indicator/range/revision facts in the proposal. The shell still has no
provider-text span, so `$` and `$$` do not open a picker there. See
[artifact references](docs/guides/artifact-references.md).

## Important limits

- Python lead conversation, proposal decisions, confirmed specialist
  execution, and typed synthesis are active; story generation, debate,
  artifact modification, consensus, and Git operations are not.
- Confirming a consultation starts only the disclosed read-only provider work.
  It never confirms an artifact change or Git commit.
- Initialization accepts only an absent/empty root or one exact resumable
  initialization journal. It never adopts, overwrites, upgrades, or migrates a
  legacy or arbitrary nonempty directory.
- A dirty or drifted original `main` root is preserved and reported as requiring
  recovery. Exact correlated session branches, sibling worktrees, internal
  refs, content-free session history, exact confirmed story commits, and
  closed-path internal checkpoints are supported internally. These internal
  capabilities do not grant the lead Apply, commit, or checkpoint authority.
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

Run the complete standard-library suite with:

```bash
python -m unittest discover -s tests -v
```

Contributors must follow [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md),
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), and
[COMMIT_POLICY.md](COMMIT_POLICY.md).
