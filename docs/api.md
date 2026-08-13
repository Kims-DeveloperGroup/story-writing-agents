# API Reference

## Public Import Surface

The following names are exported from `story_writing_agents`:

- roster metadata: `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`,
  `LEAD_ORCHESTRATOR`, `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`,
  `EXECUTION_SPECIALIST`;
- backend API: `AgentBackend`, `BoundAgent`;
- definition, request, and result models: `PromptFileReference`,
  `AgentDefinition`, `AgentRequest`, `AgentReply`, `AgentStateEntry`,
  `DebateMessage`, `DebateResult`, `SessionSummary`;
- conversation catalog API: `ConversationCatalog`, `ConversationMetadata`,
  `normalize_conversation_subject`, `CONVERSATION_METADATA_SCHEMA_VERSION`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`, and
  `MAX_CONVERSATION_SUBJECT_LENGTH`;
- enums: `InputKind`, `Phase`, `Verdict`, `DebateStatus`, `DirectiveKind`,
  `ConversationStatus`, `SessionLeaseState`;
- runtime API: `StoryRuntime`, `RuntimeSession`, `Runtime`, `Session`;
- directive API: `RuntimeDirective`, `parse_directive`, `apply_directive`;
- workspace API: `FilesystemConversationCatalog`, `Workspace`,
  `initialize_workspace`;
- errors: `StoryWritingAgentsError`, `WorkspaceSecurityError`,
  `WorkspaceFormatError`, `ConfigurationError`, `DirectiveError`,
  `SessionIdError`, `ConversationNotFoundError`, `ConversationExistsError`,
  `ManuscriptExistsError`, `BackendProtocolError`, `SessionExistsError`,
  `SessionNotFoundError`, `SessionInUseError`, and `DebateExistsError`.

`Runtime` is an alias of `StoryRuntime`; `Session` is an alias of
`RuntimeSession`.

Enum values are:

| Enum | Values |
| --- | --- |
| `InputKind` | `prompt`, `draft` |
| `Phase` | `draft`, `audit`, `propose_revision`, `synthesize`, `finalize` |
| `Verdict` | `approve`, `conflict` |
| `DebateStatus` | `consensus`, `deadlock`, `failed` |
| `DirectiveKind` | `language`, `persona`, `reset` |
| `SessionLeaseState` | `available`, `owned`, `in_use`, `unverified` |
| `ConversationStatus` | `active`, `archived` |

## Conversation Catalog

The provider-neutral catalog surface is:

```python
CONVERSATION_METADATA_SCHEMA_VERSION = 1
CONVERSATION_SUBJECT_NORMALIZATION_VERSION = 1
MAX_CONVERSATION_SUBJECT_LENGTH = 256

ConversationMetadata(
    schema_version: int,
    session_id: str,
    conversation_id: str,
    subject: str,
    subject_revision: int,
    status: ConversationStatus,
    created_at: str,
    updated_at: str,
)
ConversationMetadata.to_dict() -> dict[str, Any]

normalize_conversation_subject(subject: str) -> str

FilesystemConversationCatalog(
    workspace: Workspace | str | os.PathLike[str],
)
```

`ConversationMetadata` is frozen and slot-backed. Its eight fields are the
complete authoritative schema: version `1`, safe session and conversation
IDs, the exact display subject, a positive subject revision, an `active` or
`archived` status, and canonical ISO-8601 UTC creation and update timestamps.
The timestamps use an explicit `+00:00` offset, and `updated_at` cannot precede
`created_at`. `to_dict()` converts the status to its string value and returns
exactly those eight JSON-compatible fields.

Display subjects must be nonblank, strict-UTF-8-encodable strings of at most
256 Unicode code points and cannot contain Unicode `Cc` control characters.
Accepted leading, trailing, and repeated display whitespace is preserved.
`normalize_conversation_subject()` derives a version-1 comparison key by
applying NFKC normalization, case folding, then trimming and collapsing each
run of Unicode whitespace to one ASCII space. The derived key is not stored,
does not replace the display subject, and is not a uniqueness constraint.

`ConversationCatalog` is a storage-neutral `Protocol` exposing only logical
identifiers and immutable metadata:

```python
ConversationCatalog.create(
    session_id: str,
    subject: str,
) -> ConversationMetadata
ConversationCatalog.get(
    session_id: str,
    conversation_id: str,
) -> ConversationMetadata
ConversationCatalog.list(
    session_id: str,
    *,
    include_archived: bool = False,
) -> tuple[ConversationMetadata, ...]
ConversationCatalog.rename(
    session_id: str,
    conversation_id: str,
    subject: str,
) -> ConversationMetadata
ConversationCatalog.archive(
    session_id: str,
    conversation_id: str,
) -> ConversationMetadata
ConversationCatalog.unarchive(
    session_id: str,
    conversation_id: str,
) -> ConversationMetadata
```

`create()` allocates an immutable conversation ID scoped to the supplied
story session. It stores the display subject exactly, starts at revision `1`
and status `active`, and sets equal creation and update timestamps. Duplicate
display subjects and duplicate normalized keys create independent identities.

`get()` resolves an active or archived conversation only within its owning
session. `list()` returns active conversations by default as a tuple ordered by
`created_at` then `conversation_id`; `include_archived=True` adds archived
records. The flag must be a `bool`. `rename()` preserves identity, creation
time, and status while incrementing the subject revision on every successful
call, including a rename to the same display value. `archive()` and
`unarchive()` preserve the subject revision. They return the record unchanged,
including `updated_at`, when it already has the requested status; a real status
transition refreshes `updated_at`.

`FilesystemConversationCatalog` implements the protocol through `Workspace`.
An absent catalog lists as an empty tuple without creating directories or
rewriting legacy workspace data. A missing conversation raises
`ConversationNotFoundError`; failure to allocate a new ID without overwriting
an existing record raises `ConversationExistsError`. Unsafe logical IDs raise
`SessionIdError`, malformed or path-mismatched persisted metadata raises
`WorkspaceFormatError`, and a symlink or containment violation raises
`WorkspaceSecurityError`.

This surface manages conversation metadata only. Current-conversation
selection belongs to
[Issue #22](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/22),
and the complete conversation event store, events, and transcripts belong to
[Issue #4](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/4).
The catalog is not wired into the CLI, `StoryRuntime`, provider sessions,
debates, manuscript/core-document writes, or owner-isolated agent state.

## Concrete Backend

```python
AgentBackend(
    workspace: Workspace | str | pathlib.Path,
    *,
    executable: str = "codex",
    model: str | None = None,
    timeout_seconds: float = 300,
    backend_overrides: Mapping[str, str] | None = None,
)

await AgentBackend.bind(definition: AgentDefinition) -> BoundAgent
AgentBackend.prepare_session(session_id: str) -> None
AgentBackend.get_session_summary(
    session_id: str,
    *,
    selected_session: str | None = None,
) -> SessionSummary
AgentBackend.list_sessions(
    *,
    selected_session: str | None = None,
) -> tuple[SessionSummary, ...]
AgentBackend.create_session(session_id: str) -> SessionSummary
AgentBackend.resume_session(session_id: str) -> SessionSummary
AgentBackend.attach_session(session_id: str) -> SessionSummary
AgentBackend.detach_session(session_id: str) -> SessionSummary
AgentBackend.start_session(session_id: str) -> dict[str, Any]
AgentBackend.end_session(session_id: str) -> dict[str, Any]
AgentBackend.close() -> None

BoundAgent.backend: AgentBackend
BoundAgent.definition: AgentDefinition
BoundAgent.vendor: str
BoundAgent.staging_directory: pathlib.Path
await BoundAgent.invoke(request: AgentRequest) -> AgentReply
```

`AgentBackend` is a concrete coordinator. It validates per-agent vendor
routes, owns the provider-session lease and thread store, and returns a
concrete `BoundAgent` containing the vendor-specific invocation path. Codex is
implemented. `gemini` is recognized by configuration but rejected as not
implemented.

`model=None` uses the Codex CLI's built-in default while user configuration is
ignored. `timeout_seconds` is a positive per-call limit. `backend_overrides`
maps canonical agent IDs to vendor names for this backend instance.

Provider-session status has three independent dimensions. The manifest's
`active` flag is durable lifecycle state. `selected` is only a comparison with
the caller-supplied `selected_session`; `AgentBackend` never reads or writes
CLI context. `lease_state` is an observation of the process attachment
sidecar, not another active-state value.

`SessionSummary` is a frozen, slotted value with this exact shape:

```python
SessionSummary(
    session_id: str,
    active: bool,
    selected: bool,
    lease_state: SessionLeaseState,
    lease_hostname: str | None,
    lease_pid: int | None,
    created_at: str,
    updated_at: str,
)
```

Its lease states mean:

| State | Meaning at the time of the read |
| --- | --- |
| `available` | No lease exists, or on POSIX a same-host lease names a process that is demonstrably gone. A dead lease sidecar is not removed by a read. |
| `owned` | This `AgentBackend` holds the matching lease token. |
| `in_use` | On POSIX, an unowned same-host lease names a demonstrably live process. |
| `unverified` | The lease is remote-host, its POSIX liveness probe is ambiguous (including a permission failure), or it is unowned on a non-POSIX platform. Acquisition is blocked. |

When a lease sidecar exists, `lease_hostname` and `lease_pid` report its
validated values even when the state is `available`; both are `None` only when
no sidecar exists. Timestamps are the validated manifest values.

`get_session_summary()` and `list_sessions()` are strict, read-only, and
provider-free. They never run provider preflight, acquire or reclaim a lease,
create the lease-claim guard, or consult CLI context. A supplied selection is
validated and projected onto the returned values; it need not name a persisted
session. A missing manifest causes `get_session_summary()` to raise
`SessionNotFoundError`. An absent provider-session directory produces an empty
tuple. Lease sidecars, the claim guard, and unrelated files are not listed as
sessions. `list_sessions()` orders manifests by `updated_at` newest first and
then by ascending session ID for ties. A listed manifest or its corresponding
lease with invalid JSON, schema, timestamps, path identity, or thread metadata
raises its validation error instead of being skipped. On POSIX, observing a
dead same-host sidecar as `available` remains read-only and leaves it in place.
On non-POSIX platforms, including Windows, every unowned same-host lease is
`unverified`, remains in place, and blocks acquisition until a non-destructive
native liveness probe is implemented.

The strict attachment operations all preflight configured provider routes and
the Codex executable before changing a manifest or lease:

- `create_session()` is create-only. An existing active or inactive manifest
  raises `SessionExistsError`. Success creates an active schema-v1 manifest,
  retains a process-owned lease, and returns an unselected summary.
- `resume_session()` requires an existing manifest or raises
  `SessionNotFoundError`. It attaches an active session without rewriting its
  manifest, or atomically reactivates an inactive session while preserving
  `created_at` and thread metadata and advancing `updated_at`. It retains the
  lease and returns an unselected summary.
- `attach_session()` requires an existing active manifest. It neither
  reactivates nor rewrites it; an inactive session raises
  `BackendProtocolError`. After provider preflight, an already-inactive
  manifest is rejected before lease acquisition or claim-guard creation. An
  initially active manifest is rechecked after the lease claim to close races.
  Success retains the lease and returns an unselected summary.
- `detach_session()` releases this backend's matching lease without changing
  `active`, CLI selection, thread metadata, or other session data. After a
  normal owned detach, its unselected return value reports `available`. A
  missing manifest raises `SessionNotFoundError`; a changed ownership token
  raises `BackendProtocolError` without deleting the foreign lease.

`SessionInUseError` reports an attachment conflict for a POSIX-probed,
demonstrably live same-host owner or an unverified owner. It does not disclose
the lease token.
`SessionExistsError`, `SessionNotFoundError`, and `SessionInUseError` are all
subclasses of `BackendProtocolError`. Invalid persisted schema still raises
`WorkspaceFormatError`, unsafe IDs raise `SessionIdError`, and an inactive
session is rejected by `attach_session()` and `prepare_session()` with
`BackendProtocolError`. Any lease-acquiring lifecycle operation can raise
`SessionInUseError` when a live or unverified owner blocks it.

On POSIX, concurrent same-host reclamation of one demonstrably dead sidecar is
serialized. Exactly one cooperating claimant can replace it and acquire the
session lease; later claimants observe the replacement and receive the typed
`SessionInUseError`. This coordination does not change the public methods,
return values, exceptions, or lease states above.

The older lifecycle methods retain their compatibility contracts.
`prepare_session()` preflights provider routes and the executable, requires an
active manifest, and holds its lease. `start_session()` transiently creates or
reactivates a manifest and returns the schema-v1 manifest dictionary; it does
not require provider-executable preflight and does not retain the lease.
`end_session()` keeps its existing signature and manifest-dictionary return.
It may reuse an already-owned lease, atomically writes the inactive manifest
while preserving `created_at`, threads, and unrelated session data, advances
`updated_at`, and then releases the lease. An owned read or write failure keeps
the existing attachment; a transient control call releases its transient lease
on failure. `close()` releases all owned leases without deactivating sessions
and deletes temporary prompt staging; call it in `finally`. A closed backend
cannot be reused.

The installed CLI remains `story session start|end`. The additive strict
methods are Python API foundations; no attach, detach, or resume CLI command is
currently installed. A caller building session switching must persist its
selection only after the destination attachment completes successfully. The
existing lifecycle-command JSON and exit-code contracts documented below are
unchanged. `detach_session()` enables a later quit-without-ending workflow, but
no interactive TTY controller is included here.

There is no runtime-imported backend plugin loader.

This deterministic adapter demonstrates the complete binding shape:

```python
from story_writing_agents import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    Phase,
)


class BoundApprover:
    def __init__(
        self,
        definition: AgentDefinition,
        prompt_parts: tuple[str, ...],
    ) -> None:
        self.agent_id = definition.agent_id
        self.language = definition.language
        self.prompt_parts = prompt_parts

    async def invoke(self, request: AgentRequest) -> AgentReply:
        if request.agent_id != self.agent_id:
            raise RuntimeError("request crossed a bound-agent boundary")
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(
                request.revision_id,
                request.user_payload,
            )
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise RuntimeError(f"unexpected phase: {request.phase.value}")


class ApprovingBackend:
    async def bind(self, definition: AgentDefinition) -> BoundApprover:
        prompt_parts = tuple(
            reference.read_text()
            for reference in definition.file_references()
        )
        return BoundApprover(definition, prompt_parts)


backend = ApprovingBackend()
```

The deterministic classes above remain useful as a testing illustration
because `StoryRuntime` accepts compatible duck-typed objects in Python. They
are not discoverable from the CLI and are not a public plugin mechanism.

## Agent Definition and Prompt References

```python
PromptFileReference(
    path: pathlib.Path,
    sha256: str,
)

PromptFileReference.from_path(
    path: pathlib.Path,
) -> PromptFileReference

PromptFileReference.read_text() -> str

AgentDefinition(
    agent_id: str,
    backend: str,
    fingerprint: str,
    system_prompt_file: PromptFileReference,
    persona_file: PromptFileReference,
    language: str,
    context_document_files: tuple[PromptFileReference, ...],
)

AgentDefinition.file_references() -> tuple[PromptFileReference, ...]
```

`PromptFileReference.from_path()` reads a file as strict UTF-8 and
records the lowercase SHA-256 of its exact bytes. `read_text()` reads it again
only if the current bytes still match that digest; missing, unreadable,
invalid-UTF-8, or changed files raise `WorkspaceFormatError`.

An `AgentDefinition` contains its resolved backend, references, and language,
never full role prompt, persona, or core-document text. `file_references()` returns the
system-prompt file, persona file, then context-document files in binding order.
Paths are absolute workspace paths in definitions built by `StoryRuntime`.

Each canonical role uses its own static file:

| Agent | `system_prompt_file` |
| --- | --- |
| `lead_orchestrator` | `config/agents/lead_orchestrator/AGENTS.md` |
| `plot_architect` | `config/agents/plot_architect/AGENTS.md` |
| `character_custodian` | `config/agents/character_custodian/AGENTS.md` |
| `execution_specialist` | `config/agents/execution_specialist/AGENTS.md` |

The runtime builds a current definition before every phase invocation. Its
64-character fingerprint tracks the agent ID, resolved backend and language,
component identities, and component hashes. It caches bindings by
`(agent_id, fingerprint)` for the lifetime of that `StoryRuntime`; identical
concurrent binding requests share one in-flight bind. A role-prompt, selected
persona, language, or allowlisted core-document change therefore causes a new
binding on the next phase. Failed bindings are not cached. The runtime cache
is memory-only. Codex thread IDs are persisted separately by provider session
and agent.

The runtime verifies every reference before and after a new `bind` call. A
binding may be reused across sessions if its complete definition is
identical. Codex conversation state is partitioned by `(session_id, agent_id)`
and reused across debates; a changed fingerprint starts a fresh agent thread.
Provider failures return `failed`, record the error, and do not commit
manuscript or core-document content.

## Request Model

```python
AgentRequest(
    session_id: str,
    debate_id: str,
    agent_id: str,
    phase: Phase,
    round_number: int,
    revision_id: str,
    task: str,
    user_payload: str,
    candidate_payload: str | None = None,
    messages: tuple[DebateMessage, ...] = (),
    agent_state: tuple[AgentStateEntry, ...] = (),
)
```

The frozen request fields mean:

| Field | Meaning |
| --- | --- |
| `session_id`, `debate_id` | Durable workspace identifiers for the call. |
| `agent_id` | One canonical roster ID. |
| `phase` | `draft`, `audit`, `propose_revision`, `synthesize`, or `finalize`. |
| `round_number` | Current audit round or synthesis-cycle number. |
| `revision_id` | Revision the reply must target. |
| `task` | Phase-specific instruction, including the no-hidden-reasoning requirement. |
| `user_payload` | Original user prompt or draft. |
| `candidate_payload` | Current candidate when the phase operates on one. |
| `messages` | Full visible blackboard history as of the call's phase snapshot. |
| `agent_state` | Session-wide persisted outcomes belonging only to `agent_id`, including earlier debates and already-recorded current-debate outcomes. |

The request is deliberately prompt-free: it contains no role prompt, persona,
language directive, or core-document content. Those stable inputs belong to
the bound `AgentDefinition`; request fields are scoped to the current turn.
Parallel peers receive the same pre-phase `messages` snapshot. Their completed
outputs are logged afterward in canonical roster order.

`DebateMessage` and `AgentStateEntry` have explicit persisted schemas:

```python
DebateMessage(
    sender: str,
    phase: str,
    round: int,
    revision: str,
    kind: str,
    content: str,
    timestamp: str,
)

AgentStateEntry(
    agent_id: str,
    session_id: str,
    debate_id: str,
    phase: str,
    round: int,
    revision: str,
    kind: str,
    content: str,
    timestamp: str,
)
```

Both models are frozen and provide `to_dict()`. Persisted agent-state content is
limited to 4096 characters per entry.

## Reply Model and Phase Contracts

```python
AgentReply(
    revision_id: str,
    content: str = "",
    verdict: Verdict | None = None,
    core_updates: Mapping[str, str] = {},
)
```

Factory signatures are:

```python
AgentReply.candidate(revision_id: str, content: str) -> AgentReply
AgentReply.approval(revision_id: str, content: str = "") -> AgentReply
AgentReply.conflict(revision_id: str, critique: str) -> AgentReply
AgentReply.finalization(
    revision_id: str,
    core_updates: Mapping[str, str] | None = None,
) -> AgentReply
```

Every reply must use a string `revision_id` equal to the revision expected by
the request. `content` must be a string, `verdict` must be a `Verdict` member or
`None`, and `core_updates` must map string filenames to string full-text
content. Phase validation is:

| Phase | Required reply |
| --- | --- |
| `draft` | Non-empty candidate `content`; no verdict or core updates. |
| `audit` | `Verdict.APPROVE` with optional content, or `Verdict.CONFLICT` with a non-empty critique; no core updates. |
| `propose_revision` | Non-empty proposal content; no verdict or core updates. `candidate()` is the applicable factory. |
| `synthesize` | Non-empty complete replacement candidate; no verdict or core updates. |
| `finalize` | No verdict. Content must be empty or exactly equal to the already-approved candidate. Core updates may be empty and are later restricted to the four allowlisted filenames. |

A string such as `"approve"` is not accepted in place of
`Verdict.APPROVE`. Specialists cannot return core updates, and the lead
orchestrator cannot change candidate content after unanimous approval.

## Debate Result

```python
DebateResult(
    session_id: str,
    debate_id: str,
    input_kind: InputKind,
    status: DebateStatus,
    revision_id: str | None,
    rounds_completed: int,
    final_candidate: str | None,
    latest_candidate: str | None,
    manuscript_path: str | None,
    core_updates: tuple[str, ...] = (),
    messages: tuple[DebateMessage, ...] = (),
    error: str | None = None,
    action_required: str | None = None,
)
```

`DebateStatus` has three returned-result values:

- `consensus`: all three specialists approved one revision, finalization
  succeeded, and artifacts were committed;
- `deadlock`: conflicts remained after the allowed replacement revisions;
- `failed`: a handled backend, protocol, finalization, or artifact error
  prevented consensus.

`result.consensus` is true only for `consensus`.
`result.to_dict(include_messages=True)` returns JSON-compatible enum values and
includes the blackboard by default; pass `include_messages=False` to omit it.

`rounds_completed` counts synthesized replacements. `final_candidate` and
`manuscript_path` are populated only after consensus. `latest_candidate`
retains the most recent candidate for deadlock and most handled failures.
`core_updates` lists filenames actually applied. `error` describes failure;
`action_required` describes the user ruling needed for deadlock.

`active` and `interrupted` are lifecycle inspection statuses rather than
`DebateResult` statuses. An externally cancelled coroutine re-raises
`asyncio.CancelledError` after best-effort transition to terminal
`interrupted`; it does not return a `DebateResult`.

## Runtime and Session

```python
StoryRuntime(
    workspace: Workspace | str | pathlib.Path,
    backend: AgentBackend | None = None,
)

StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(
    session_id: str,
    text: str,
) -> dict[str, object]
StoryRuntime.set_runtime_overrides(
    session_id: str,
    overrides: Mapping[str, Any],
) -> dict[str, Any]
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(
    session_id: str,
    debate_id: str,
) -> dict[str, Any]

await StoryRuntime.run_prompt(
    session_id: str,
    prompt: str,
    *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None,
    debate_id: str | None = None,
) -> DebateResult

await StoryRuntime.run_draft(
    session_id: str,
    draft: str,
    *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None,
    debate_id: str | None = None,
) -> DebateResult
```

A missing backend is valid for directives and inspection, but debate execution
raises `BackendProtocolError`. Prompt and draft payloads must be non-empty
strings. If `debate_id` is omitted, the runtime generates one. If
`manuscript_name` is omitted, it uses
`<session_id>_<debate_id>.md` beneath `manuscript/`.

Preflight errors such as invalid input, invalid round limits, unsafe IDs, a
missing backend, or an already-reserved debate raise an exception directly.
Handled execution failures normally return a `failed` result. Unexpected
exceptions that escape execution are re-raised after a best-effort failed
lifecycle transition.

The bound-session convenience API is:

```python
RuntimeSession(runtime: StoryRuntime, session_id: str)

RuntimeSession.apply_directive(text: str) -> dict[str, object]
RuntimeSession.set_overrides(
    overrides: Mapping[str, Any],
) -> dict[str, Any]
RuntimeSession.inspect() -> dict[str, Any]
RuntimeSession.inspect_debate(debate_id: str) -> dict[str, Any]
await RuntimeSession.run_prompt(prompt: str, **options: Any) -> DebateResult
await RuntimeSession.run_draft(draft: str, **options: Any) -> DebateResult
```

Example:

```python
import asyncio

from story_writing_agents import AgentBackend, StoryRuntime, initialize_workspace


async def main() -> None:
    workspace = initialize_workspace("./api-workspace")
    backend = AgentBackend(workspace)
    backend.start_session("demo")
    try:
        session = StoryRuntime(workspace, backend).session("demo")
        result = await session.run_draft(
            "A neutral draft.",
            debate_id="debate_01",
            manuscript_name="demo.md",
        )
        print(result.to_dict(include_messages=False))
    finally:
        backend.close()


asyncio.run(main())
```

## Workspace and Directive Utilities

```python
Workspace(root: str | os.PathLike[str])
Workspace.initialize(*, overwrite_prompts: bool = False) -> Workspace
initialize_workspace(
    root: str | os.PathLike[str],
    *,
    overwrite_prompts: bool = False,
) -> Workspace

RuntimeDirective(
    kind: DirectiveKind,
    language: str | None = None,
    agent_id: str | None = None,
    persona: str | None = None,
)
parse_directive(text: str) -> RuntimeDirective | None
apply_directive(
    workspace: Workspace,
    session_id: str,
    text: str,
) -> dict[str, object]
```

`Workspace.initialize()` creates missing scaffold content and returns the same
workspace. By default, it preserves existing files. With
`overwrite_prompts=True`, it replaces only the four canonical agent
`AGENTS.md` files with their defaults and preserves all other workspace
content. Common direct methods include:

```python
Workspace.path(relative) -> pathlib.Path
Workspace.read_text(relative) -> str
Workspace.read_json(relative) -> Any
Workspace.write_text(relative, content, *, overwrite=True) -> pathlib.Path
Workspace.write_json(relative, value, *, overwrite=True) -> pathlib.Path
Workspace.read_persona(template_name: str) -> str
Workspace.read_core_documents(
    filenames: tuple[str, ...],
) -> dict[str, str]
Workspace.read_manuscript(manuscript_name: str) -> str
Workspace.write_manuscript(
    manuscript_name: str,
    content: str,
) -> pathlib.Path
Workspace.load_session_overrides(session_id: str) -> dict[str, Any]
Workspace.save_session_overrides(
    session_id: str,
    overrides: Mapping[str, Any],
) -> pathlib.Path
Workspace.read_agent_state(
    agent_id: str,
    session_id: str,
    debate_id: str,
) -> tuple[AgentStateEntry, ...]
Workspace.read_debate_messages(
    session_id: str,
    debate_id: str,
) -> tuple[DebateMessage, ...]
Workspace.inspect_session(session_id: str) -> dict[str, Any]
Workspace.inspect_debate(
    session_id: str,
    debate_id: str,
) -> dict[str, Any]
Workspace.commit_approved_artifacts(
    manuscript_name: str,
    candidate: str,
    core_updates: Mapping[str, str],
) -> tuple[str, tuple[str, ...]]
```

Lower-level append, reservation, transition, and result-save methods are
publicly callable on `Workspace`, but are orchestration persistence primitives;
normal integrations should use `StoryRuntime`.

## Inspection Shapes

Session inspection returns:

```json
{
  "session_id": "demo",
  "overrides": {
    "language": null,
    "agents": {}
  },
  "debates": []
}
```

`debates` is sorted by debate ID. A debate with a persisted result is represented
by the result fields plus `"terminal": true`. A reservation without a result
uses a summary containing `session_id`, `debate_id`, `input_kind`, `status`,
`terminal`, `revision_id`, `rounds_completed`, and `error`.

Debate inspection always returns:

```json
{
  "session_id": "demo",
  "debate_id": "debate_01",
  "status": "not_found",
  "terminal": false,
  "state": null,
  "result": null,
  "messages": []
}
```

For a known debate, `state` contains lifecycle schema fields but omits the
internal `owner_token`; `result` contains the persisted result fields without
messages; and `messages` contains the validated full JSONL blackboard. Possible
inspection statuses include `not_found`, `active`, `consensus`, `deadlock`,
`failed`, and `interrupted`.

## CLI

The package installs two console scripts with the identical target
`story_writing_agents.cli:main`: canonical `story-writing-agents` and short
`story`. They are exact aliases, not separate CLIs. Examples use `story`.

Installed command syntax:

```text
story init [workspace] [--overwrite-prompts]

story directive
  [--workspace WORKSPACE]
  [--session SESSION]
  TEXT

story run
  [--workspace WORKSPACE]
  [--session SESSION]
  [--backend AGENT=BACKEND]...
  [--codex-model MODEL]
  [--codex-timeout SECONDS]
  [POSITIONAL_PROMPT | --prompt PROMPT | --draft DRAFT | --draft-file PATH]
  [--output MANUSCRIPT_PATH]
  [--debate-id DEBATE_ID]
  [--max-debate-rounds MAX_DEBATE_ROUNDS]

story inspect
  [--workspace WORKSPACE]
  [--session SESSION]
  [--debate DEBATE]

story session (start | end)
  [SESSION]
  [--workspace WORKSPACE]
  [--session SESSION]

story smoke
  [--workspace WORKSPACE]
```

For `directive`, `run`, `inspect`, and `session`, omitted `--workspace`
discovers the nearest initialized workspace from the current directory or its
ancestors. An explicit workspace wins. Omitted sessions use the selection
written by the last successful `session start`; lifecycle commands also accept
a positional session. Normal
initialization creates missing agent prompts and preserves existing ones.
`--overwrite-prompts` replaces only the four canonical
`config/agents/<agent-id>/AGENTS.md` files. `run` prints the complete
`DebateResult.to_dict()` JSON, including messages. `--backend` is a repeatable
per-agent override such as `plot_architect=codex`. `--codex-model` overrides
the Codex default, and `--codex-timeout` defaults to 300 seconds.
`run` requires exactly one input source. A positional value is a prompt;
`--draft-file` reads a strict-UTF-8 file relative to the caller's current
directory.
Initialization, session lifecycle, directives, and inspection print formatted
JSON.

`smoke` is different: without `--workspace`, it creates and removes a
temporary workspace; with an explicit path, it initializes or reuses that
workspace and retains uniquely named synthetic session, debate, state, log,
result, and manuscript artifacts. It never constructs the Codex backend or makes
a network call. Its ordinary runtime path deterministically drafts `r0`, runs
three audits with one plot conflict, records a plot proposal, synthesizes
`r1`, runs three unanimous audits, finalizes and commits the manuscript,
re-inspects persisted consensus, and verifies that all four core documents are
unchanged.

A successful smoke report is one line of compact JSON with:

- `"command": "smoke"`, `"status": "passed"`, and
  `"provider_tested": false`;
- `workspace.mode`, resolved `workspace.path`, and `workspace.retained`;
- unique `session_id` and `debate_id`;
- `debate_status: "consensus"`, `revision_id: "r1"`, and
  `rounds_completed: 1`;
- the six checks `workspace_initialized`, `directive_persisted`,
  `revision_workflow`, `consensus_persisted`, `manuscript_committed`, and
  `core_documents_unchanged`;
- a disclaimer that no LLM or external provider was called.

Errors handled by the normal CLI contract are written to standard error as
`ExceptionType: message`.

Exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | Initialization, directive, or inspection succeeded; a debate reached consensus; or all smoke invariants passed. |
| `1` | Smoke completed but an invariant failed; compact failed JSON includes `failed_check`. |
| `2` | CLI usage or a caught exception occurred, including provider/session preflight errors and smoke workspace setup, configuration, or unexpected errors. |
| `3` | `run` returned `deadlock` (unchanged by the smoke command). |
| `4` | `run` returned `failed` (unchanged by the smoke command). |

See [Configuration](configuration.md) for persisted schemas and
[Security, Privacy, and Limitations](security.md) before exposing a workspace
to sensitive content.
