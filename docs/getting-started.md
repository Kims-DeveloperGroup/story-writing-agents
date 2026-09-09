# Getting started

The installed interactive shell is the normal front channel for the Lead
Orchestrator. After attaching a session and focusing a conversation, ordinary
input reaches the lead; exact structured decisions gate specialist review,
artifact Apply, and local Commit as separate operations.

## 1. Install

Use Python 3.11 or later in one virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story --help
```

`story` and `story-writing-agents` are exact aliases. A normal install includes
the read-only exchange viewer. Optional local RAG requires the reviewed `rag`
extra before enablement.

The shell checks configured Codex and Antigravity authentication before
constructing its controller. Ordinary focused lead turns and confirmed work
may call a provider; management and inspection commands remain provider-free.

## 2. Run the provider-free check

```bash
story smoke
```

Success is compact JSON with `"status":"passed"` and
`"provider_tested":false`. Temporary smoke uses the real initializer, verifies
one persisted directive, one conversation-catalog record, empty schema-v2
history/search/page projections, and unchanged execution artifacts, then
removes the whole temporary workspace. It does not generate a story, create a
transcript or debate, or validate a provider. A missing effective Git identity
is reported because smoke never invents one.

## 3. Initialize a workspace

```bash
story init ./first-story
cd ./first-story
```

Initialization accepts only an absent path, an empty safe directory, or one
exact interrupted-v2 journal. Success has this shape:

```json
{
  "workspace": "/absolute/path/to/first-story"
}
```

The result is one strict schema-v2 workspace with the exact packaged scaffold,
four zero-byte core documents, and one parentless local `main` commit named
`Initialize story workspace`. Git supplies the effective author and committer;
the runtime never invents an identity. The initialization journal is absent and
the worktree is clean when success is printed.

Rerunning `story init` on that unchanged root validates it without rewriting,
staging, or committing. On any journal-free ready root, validation reads only
required paths and exact Git invariants; it leaves untracked notes and unknown
provider state opaque. Those entries are permitted, not trusted, and their
owner validates them when used. A legacy, markerless arbitrary nonempty,
nested-Git, partially initialized, required-path-drifted, staged, or
disallowed-tracked root is preserved and rejected. There is no prompt-
overwrite, migration, compatibility, upgrade, or adoption mode; create a new
workspace instead.

To verify the completed root without changing it:

```bash
story smoke --workspace .
```

That explicit form reports only `workspace_initialized` and
`execution_artifacts_unchanged`, uses `null` session/conversation IDs, and
creates no directive, catalog record, diagnostic, or other residue.

## 4. Manage a session and conversation

The v2 marker records exact session names, but `story init` does not create a
session branch, sibling worktree, or internal runtime ref. Session Create and
Reopen use the package-internal resource seam to provision or verify that
isolation before attaching a destination. The original `main` root remains the pristine
initialization baseline; it is not the location or authorization for later
story/runtime writes.

The retained management commands below describe the installed management
surface. A provider-session `ready` result is distinct from Git transition
assessment and does not authorize Apply, commit, or checkpoint work. Writing
mutable state directly into the original baseline makes a fresh v2 readiness
check fail closed.

```bash
story
```

Then enter:

```text
/session create first_novel
/conversation new Opening structure
/status
/conversation list
/history
/search opening
/quit
```

Plain rendering has this shape (the workspace and conversation ID vary):

```text
story[lobby]> /session create first_novel
[session]
  Workspace: <resolved-workspace>
  Session: first_novel (created)
story[first_novel]> /conversation new Opening structure
[conversation] Created and focused c_<generated> | Subject: Opening structure
story[first_novel]> /history
[context] [empty]
  History displayed: 0/0 records
  Last sequence: 0
  Current state: empty
  User meaning: No collaboration event has been recorded yet.
  Next safe action: none
  Next action meaning: No history-derived action is pending.
```

Capable-terminal decoration varies; the semantic fields and empty-history
meaning do not.

You can also use `/conversation resume [ID]`, `/conversation subject SUBJECT`,
`/conversation archive [ID]`, `/transcript [ID [COUNT] [--from SEQUENCE]]`,
`/inspect [DEBATE|last]`, `/exchanges`, and `/status`. `/help` shows the exact
installed registry.

## 5. Talk to the Lead

With the session attached and conversation focused, type an ordinary non-slash
line. It is passed unchanged to `RuntimeSession.lead_turn()` and always
addresses the Lead Orchestrator:

```text
story[first_novel]> What does Reject do?
[Lead]
  Outcome: answer
  Lead: Reject records the decision and starts no proposed work.
  Next action: continue_with_lead
```

Provider wording varies. The semantic `Outcome`, `Lead`, and `Next action`
fields are stable. Attach canonical evidence with `${path}` or a range such as
`${manuscript/part-01/opening.md}#L20-L32`; multiple indicators may appear in
one line. They are path/range instructions the addressed agents must read, not
file content embedded into the command.

If no session is attached or no active conversation is focused, the shell
reports that prerequisite and makes no lead call. Slash-prefixed management
commands retain their own grammar. There is no `story run`, `/run`, raw
specialist dispatch, or agent-selector syntax for ordinary input.

## 6. Decide a proposed step

The Lead may instead display an exact proposal with purpose, assignments,
evidence, user impact, limits, identity, and a confirmation request:

```text
story[first_novel]> Is the reveal too early? ${core_documents/part_outlines.md}
[Lead]
  Outcome: proposal
  Lead: A Plot specialist should check the reveal timing.
  Next action: submit_confirmation
  State: awaiting_confirmation
Lead proposal decision
  Confirm — Authorize only the exact disclosed proposal.
  Reject  — Reject the proposal without running it.
  Revise  — Record a revision request without confirming it.
```

Confirm runs only the disclosed specialist work and returns the Lead's typed
synthesis. Reject runs nothing. Revise asks for nonempty revision text and
supersedes the old proposal. Cancelling the picker grants no authority.
Free-form `Yes` is a new lead turn and supersedes a pending proposal; it never
confirms it.

Artifact review confirmation still does not write files. A reviewed proposal
gets a separate Apply/Reject/Revise picker. Apply writes only the exact
revalidated changes and does not stage or commit. A successful Apply can then
offer a separate Commit/Reject/Revise picker for one local commit; no remote
push or merge is authorized.

Progress is shown while provider work runs. Interruption preserves knowable
results; `outcome_unknown` must not be replayed, and `recovery_required` must be
repaired before normal collaboration. Reopened canonical interactive runtime
files remain valid. See [Collaborate with the Lead](guides/lead-agent-collaboration.md)
for every state and recovery action.

## 7. Use the Python lead API

With an active conversation and a configured ready backend, an application can
address the lead without choosing an agent:

```python
result = await runtime.lead_turn(
    "first_novel",
    conversation_id,
    "Critique the opening against ${core_documents/global_outline.md}.",
)
```

The result is one answer, question, critique, recommendation, or proposal. A
proposal discloses complete assignments, content-free input revisions, user
impact, side effects, and work limits. Only a matching structured
`LeadStepDecision` can Confirm, Reject, or Revise it. Free-form `Yes` is an
ordinary new lead turn and supersedes the pending proposal.

Confirming records the exact decision, executes the disclosed one-to-three
specialist consultation, and returns the authoritative terminal or round-two
turn. It cannot authorize an artifact change or Git commit. See the
[lead-agent guide](guides/lead-agent.md) for complete examples and the
[runtime API](reference/runtime-api.md) for exact signatures.

## 8. Continue

- [Story sessions and conversations](guides/story-sessions.md)
- [Lead agent and collaboration status](guides/lead-agent.md)
- [Provider-free smoke](guides/smoke-test.md)
- [CLI reference](reference/cli.md)
- [Runtime API](reference/runtime-api.md)
