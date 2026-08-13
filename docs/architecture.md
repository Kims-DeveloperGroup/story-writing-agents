# Architecture

## Implemented Scope

`story-writing-agents` is an asynchronous orchestration runtime with a
concrete, vendor-aware backend. It coordinates four fixed agents around a
shared, append-only public blackboard, persists runtime state in a workspace,
and invokes Codex through the installed Codex CLI.

This document describes the current implementation of
[Issue #1](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1)
and
[Issue #2](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/2),
plus the provider-session lifecycle foundation in
[Issue #5](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/5)
and the provider-neutral conversation catalog from
[Issue #19](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/19).
In this repository, “commit” means committing approved text to filesystem
artifacts. The runtime does not invoke Git.

## Agent Roster

The roster and voting membership are fixed:

| Canonical ID | Role | Context documents | Vote |
| --- | --- | --- | --- |
| `lead_orchestrator` | Moderates, synthesizes, finalizes | All four core documents | No |
| `plot_architect` | Audits plot, structure, flow, and pacing | `global_outline.md`, `part_outlines.md` | Yes |
| `character_custodian` | Audits character, relationship, world, and continuity behavior | `character_sheets.md` | Yes |
| `execution_specialist` | Drafts prompt input and audits prose, POV, tone, grammar, and formatting | `narrative_rules.md` | Yes |

Only the three specialists vote. The lead orchestrator never supplies a
consensus vote and cannot make a conflict disappear by filtering it.

The initialized configuration resolves every agent to
`default_kim_eun_hee`. That template is implemented as strict, logical, candid,
and skeptical writing-room behavior that prioritizes domain consistency over
polite agreement; it is not biographical impersonation. The normal
configuration cascade can replace the persona session-wide or per agent.

## Conversation Catalog Boundary

The conversation catalog introduces a metadata-only hierarchy alongside the
existing debate runtime:

```text
story session
├── selected-conversation reference (Issue #22; not implemented here)
└── conversation ID and metadata (Issue #19)
    ├── events and transcript (Issue #4; not implemented here)
    └── search and index (Issue #20; not implemented here)
```

`ConversationCatalog` is the storage-neutral contract. It accepts a story
session ID and logical conversation ID and returns immutable
`ConversationMetadata`; it never exposes filesystem paths. The public
`FilesystemConversationCatalog` adapter owns the concrete metadata layout
through the existing `Workspace` containment, encoding, and atomic-write
boundary.

Conversation IDs are immutable and scoped by story session. The same logical
ID may exist independently in two sessions, while metadata whose embedded
session or conversation ID disagrees with its owning path is rejected. Subject
display values are not identities: duplicate subjects and duplicate normalized
comparison keys remain separate conversations. Rename changes only the display
subject, revision, and update timestamp; archive and unarchive change discovery
status without deleting the identity.

The catalog has no dependency on a provider and is not invoked by the CLI,
`StoryRuntime`, or debate workflow. Catalog operations do not select the
current conversation, create events or transcripts, build a search index,
invoke a model, or mutate provider sessions, debate logs, manuscripts, core
documents, or owner-isolated agent state.
[Issue #22](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/22)
owns selection, and
[Issue #4](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/4)
owns the complete `ConversationEventStore`, events, and transcripts.
[Issue #20](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/20)
owns the search/index boundary. No database or RAG boundary is implemented by
this catalog.

## Backend and Prompt Boundary

Provider behavior is embedded in two concrete levels:

```python
AgentBackend(workspace, *, executable, model, timeout_seconds, backend_overrides)
await AgentBackend.bind(definition) -> BoundAgent
await BoundAgent.invoke(request) -> AgentReply
```

The runtime owns orchestration, persistence, definition assembly, binding
reuse, concurrency, and protocol validation. `AgentBackend` owns per-agent
vendor routing, provider-session leases, prompt staging, and persistent thread
metadata. `BoundAgent` owns one immutable definition and its vendor-specific
invocation path. Codex is implemented; Gemini is recognized but unsupported.

Each role's static instructions are an editable workspace file:

```text
config/agents/<agent-id>/AGENTS.md
```

Before each phase invocation, the runtime resolves current configuration and
builds an `AgentDefinition` containing:

- the canonical agent ID;
- the resolved backend name;
- a `PromptFileReference` for that role's `AGENTS.md`;
- a reference for the resolved persona;
- the resolved language token;
- references for only that role's allowlisted core documents; and
- a fingerprint covering the agent ID, backend, language, reference
  identities, and exact file hashes.

Definitions contain file paths and SHA-256 digests, never full prompt,
persona, or core-document text. Each reference provides a verified strict
UTF-8 read that fails when its current bytes no longer match its digest.
The Codex binding combines verified role, persona, language, and allowlisted
context in an isolated temporary top-level `AGENTS.md`.

Bindings are cached by `(agent_id, fingerprint)` for one `StoryRuntime`.
Matching concurrent binds share a single in-flight operation, cancellation of
one waiter does not cancel a bind still needed by another, and failed bindings
are not cached. Editing an `AGENTS.md`, selected persona, resolved language,
or allowlisted core document changes the next definition and causes a new
binding on the next phase. A changed fingerprint starts a fresh Codex thread
for that agent.

Context references remain least-privilege by role: the plot architect receives
the two outline files, the character custodian receives the character sheet,
the execution specialist receives the narrative rules, and the lead
orchestrator receives all four allowlisted files.

`AgentRequest` is prompt-free and keeps request-scoped data in separate
fields:

- `task`: the phase instruction plus the no-hidden-reasoning instruction;
- `user_payload`: the original prompt or draft;
- `candidate_payload`: the current candidate, or `None` before prompt drafting;
- `messages`: the public blackboard snapshot visible at invocation time;
- `agent_state`: only the receiving agent's validated history for this session.

The public blackboard and private-by-owner outcome history are separate
channels. Agent state never grants an agent another agent's persisted
outcomes. An identical immutable binding can serve multiple sessions, while
the concrete `BoundAgent` partitions Codex conversation state by
`(session_id, agent_id)`. Persisted `agent_state` remains the authoritative
runtime memory channel, and a team session resumes each agent's thread across
debates.

### Provider Session Lifecycle and Attachment

A provider session has three deliberately separate kinds of state:

- its schema-v1 manifest durably records `active`, timestamps, and resumable
  provider thread references;
- its lease sidecar represents a process attachment and excludes conflicting
  local use; and
- `cli_context.json` records only the CLI-selected session.

An active session need not be selected or attached, a selected session may be
inactive, and an observed lease does not change either durable value. Backend
summary reads accept an optional selected ID from the caller and compare it;
the backend does not load or persist selection. Reads also do not acquire or
reclaim a lease.

Strict `create_session()`, `resume_session()`, and `attach_session()` calls
preflight all configured provider routes and the executable before manifest or
lease mutation. They check the requested manifest condition, acquire the lease
atomically, and then recheck that condition before writing or returning. A
failed recheck releases a lease newly acquired by that call. In particular,
after preflight `attach_session()` rejects an already-inactive manifest before
lease acquisition or claim-guard creation, then rechecks an initially active
manifest after claiming to close races. On POSIX, a demonstrably dead same-host
sidecar can be reclaimed during acquisition; live, remote-host, ambiguous, and
all non-POSIX unowned sidecars block acquisition. Successful strict attachment
retains the lease until `detach_session()`, `end_session()`, or `close()`.

All cooperating processes serialize each short lease-claim transaction through
the persistent workspace-global opaque file
`provider_sessions/.lease-claims.lock`. While holding its OS advisory lock, a
claimant attempts exclusive sidecar creation or, on POSIX, reads and revalidates
an existing dead same-host sidecar, unlinks it, and completes replacement. The
replacement sidecar is durable before the guard is released, so concurrent
POSIX dead-sidecar reclamation has exactly one owner; the next claimant observes
an in-use lease.
The guard serializes claims across sessions, never represents session
ownership or liveness, and is never unlinked by the runtime. Closing its file
descriptor releases the advisory lock; process exit also releases it after a
crash.

The API permits one backend to hold more than one session lease so a caller can
switch without dropping its working source first. The safe boundary ordering
is:

1. completely create, resume, or attach the destination;
2. persist the destination as selected only after that succeeds; and
3. detach the source last.

If destination attachment fails, the caller must leave both its source
attachment and existing selection unchanged. The backend operations satisfy
their half of this invariant by never changing CLI context and by not
implicitly detaching another session. This is an integration contract for the
Python API; an interactive switch controller is not implemented in this
release.

`detach_session()` removes only this backend's matching lease. It does not end
the durable session, remove provider threads or other data, or change
selection. `end_session()` uses an already-owned lease when available;
otherwise it claims a transient one. It atomically persists `active: false`
while retaining `created_at`, thread references, and unrelated data, advances
`updated_at`, and on success releases after the write. If an owned read or write
fails, the existing attachment remains available for recovery. A failed
transient control call instead releases its transient lease.

The compatibility path remains intact: `story session start` uses transient
create-or-reactivate behavior without provider-executable preflight, and
`story run` uses `prepare_session()` to preflight and hold an active session
lease. Within one backend, calls are also serialized by
`(session_id, backend, agent_id)` so separate bindings cannot race one provider
thread. `story session end` marks the session inactive but retains thread IDs.
The installed lifecycle CLI still exposes only `start` and `end`.

The Codex subprocess runs without shell evaluation, uses read-only sandboxing
and no approvals, and receives dynamic request JSON over stdin. Phase-specific
output schemas are validated before conversion to `AgentReply`; failed calls
are not retried automatically.

The CLI adds a workspace-local navigation layer without changing the runtime
API. When `--workspace` is absent, it walks from the caller's current
directory toward the filesystem root and selects the nearest directory with
the complete initialized-workspace sentinels. An explicit path bypasses
discovery. A successful `session start` atomically writes the selected session
to `agent_states/orchestrator_state/cli_context.json`; other commands read but
never change it, and a failed start leaves it intact. `AgentBackend` itself
never reads or writes this file. Explicit `--session` values take precedence
over this selection.

For `run`, input resolution happens before provider construction and accepts
exactly one of a positional prompt, `--prompt`, `--draft`, or `--draft-file`.
Draft files are decoded as strict UTF-8 relative to the caller's current
directory. Debate IDs, manuscript names, and runtime defaults continue to be
resolved by the orchestration layer when omitted.

## Built-In Smoke Workflow

The CLI `smoke` command supplies a private deterministic backend to the same
`StoryRuntime` path; it does not construct `AgentBackend` or make a network call.
It initializes a workspace, persists and rehydrates an `EN` session directive,
and snapshots the four core documents before running one complete synthetic
revision cycle:

```text
prompt → execution draft r0
       → three parallel audits (one plot conflict)
       → plot proposal
       → lead synthesis r1
       → three parallel unanimous audits
       → lead finalization
       → manuscript commit and persisted inspection
```

The command verifies workspace initialization, directive persistence, exact
phase counts and owner-isolated state, persisted consensus, the committed
synthetic `r1` manuscript, and byte-for-byte unchanged core documents. With no
workspace argument, all of this occurs under a temporary directory removed
when the command completes. An explicit workspace initializes or reuses that
directory and retains uniquely identified synthetic sessions, debates, logs,
states, results, and manuscripts for diagnosis.

This checks orchestration and persistence invariants only. It does not test an
LLM, Codex provider, model, network, credentials, or writing
quality.

## Debate Workflow

### 1. Reservation and initiation

Before a provider is invoked, the runtime validates the input, chooses or
validates a debate ID, and atomically creates
`<debate_id>.state.json`. A second runtime attempting the same
session/debate pair receives `DebateExistsError` before its backend runs.

The original input is appended to the debate JSONL log as revision `r0`. For
prompt input, `execution_specialist` first creates a complete `r0` candidate.
For draft input, the supplied draft is already the `r0` candidate and there is
no drafting call.

### 2. Parallel silent audit

`plot_architect`, `character_custodian`, and `execution_specialist` audit the
same candidate concurrently. Each receives the blackboard snapshot from before
that audit, so peers do not see or react to another peer's in-flight verdict.
An audit reply must target the current revision and contain:

- `APPROVE`, with an optional concise note; or
- `CONFLICT`, with a non-empty actionable critique.

Although provider calls can finish in any order, audit outcomes are appended to
the blackboard in the stable canonical specialist order shown above. On the
first backend or validation failure, unfinished peer calls are cancelled and
all outcomes are recorded in stable order.

### 3. Proposal, synthesis, and re-audit

If any specialist reports a conflict, only the specialists that own conflicts
are asked for revision proposals. Those proposals are also requested in
parallel from one shared pre-proposal history snapshot and are logged in
canonical specialist order.

The lead orchestrator then receives the prior candidate and the full visible
blackboard—including every critique and proposal—and must return one complete
replacement candidate. The revision advances from `r0` to `r1`, and so on.
All three specialists re-audit that complete new candidate with the full
blackboard history.

`max_debate_rounds` counts synthesized replacement revisions, not audit
passes. The global default is `1`: the runtime may synthesize one replacement
after conflicts on `r0`, then audits `r1`. A value of `0` permits the initial
audit but no synthesized revision.

Consensus requires valid `APPROVE` verdicts from all three specialists for the
same current revision. Approvals from an older revision never count.

### 4. Deadlock and failure

If conflicts remain when the revision limit is reached, the result is
`deadlock` and includes an `action_required` user-ruling message. A backend
exception, peer cancellation caused by a failure, or invalid reply produces a
`failed` result.

Deadlock and pre-finalization handled failure still persist lifecycle state,
blackboard logs, agent outcomes, and a result record, but do not write a
manuscript or change core-document content. A failure during artifact commit
returns `failed` after the best-effort rollback described below; infrastructure
failure during rollback has the stated partial-durability caveat.

### 5. Finalization and artifact commit

After unanimous approval, the lead orchestrator receives a `finalize` request.
It cannot replace the approved candidate. It may return an empty map or
full-text replacements for these files only:

- `global_outline.md`
- `part_outlines.md`
- `character_sheets.md`
- `narrative_rules.md`

The runtime validates all update names and values and verifies that the target
manuscript does not exist before content mutation. It then replaces requested
core documents in allowlist order and creates the exact approved candidate
under `manuscript/`. Existing manuscripts are never silently overwritten.

Each file replacement is staged and atomic. If a later write fails, the runtime
attempts to remove the new manuscript and restore earlier core files before
returning a failed result. This is a best-effort cross-file rollback, not a
single power-loss-safe transaction.

## Lifecycle and Recovery

The reservation state can report `active`, `consensus`, `deadlock`, `failed`,
or `interrupted`. Normal returned terminal outcomes also have a
`.result.json`; externally cancelled work is marked terminal `interrupted` and
may have only state plus the blackboard written before cancellation.

An active marker records ownership but does not prove that its creating process
is still alive. Debate IDs are immutable reservations: completed, failed,
deadlocked, interrupted, or abandoned IDs are not resumed or reused. Recovery
means inspecting the durable state and starting a new debate ID.

## Filesystem Topology

`story init .` creates missing scaffold entries without modifying existing
content. Existing workspaces can run the same command to add missing agent
prompts. `story init . --overwrite-prompts` replaces only the four canonical
`AGENTS.md` files with defaults, preserves every other existing workspace
file, and still creates any missing scaffold entries. The initialized
scaffold is:

```text
workspace/
├── config/
│   ├── system_defaults.json
│   ├── agent_configs.json
│   ├── agents/
│   │   ├── lead_orchestrator/AGENTS.md
│   │   ├── plot_architect/AGENTS.md
│   │   ├── character_custodian/AGENTS.md
│   │   └── execution_specialist/AGENTS.md
│   └── persona_templates/
│       ├── default_kim_eun_hee.md
│       └── alternative_templates/
│           └── .gitkeep
├── core_documents/
│   ├── global_outline.md
│   ├── part_outlines.md
│   ├── character_sheets.md
│   └── narrative_rules.md
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── .gitkeep
│   │   ├── sessions/.gitkeep
│   │   └── provider_sessions/
│   ├── plot_architect_state/.gitkeep
│   ├── character_custodian_state/.gitkeep
│   └── execution_specialist_state/.gitkeep
├── discussion_logs/.gitkeep
└── manuscript/.gitkeep
```

Persona templates added by the user may appear in either persona directory.
CLI context at `agent_states/orchestrator_state/cli_context.json`, session
overrides, per-agent debate histories, discussion lifecycle/log/result files,
and manuscript output are created lazily in the paths shown in the
[User Guide](../UserGuide.md#generated-files).

Conversation catalog metadata is also lazy and uses this fixed path:

```text
discussion_logs/<session_id>/interactive/conversations/<conversation_id>/metadata.json
```

`create()` exclusively creates one versioned metadata file for a new identity;
rename and status transitions atomically replace that file. Listing a session
with no catalog returns empty, and missing-record reads do not create the
catalog or rewrite existing discussion artifacts. The adapter performs no
legacy migration. This path currently contains metadata only, not a
conversation transcript or index.

Session override JSON survives new `StoryRuntime` instances. Each agent JSONL
contains only concise outcomes owned by that agent. The discussion JSONL is the
full public blackboard and preserves the original user content, candidates,
verdict notes, critiques, proposals, errors, finalization metadata, and commit
record. Result JSON intentionally omits the duplicated message list; inspection
combines the result, lifecycle state, and JSONL messages.

See [Configuration](configuration.md), [API](api.md), and
[Security, Privacy, and Limitations](security.md) for the corresponding
schemas and operational constraints.
