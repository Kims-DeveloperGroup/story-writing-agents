# Agent contracts

> **Availability:** these models are Python APIs. Debate requests can carry an
> optional conversation snapshot; one-step and retrieval turns use separate,
> compatible request families. Content-free explicit-reference read contracts
> are available to internal/Python backend callers, but `StoryRuntime` and the
> installed shell do not construct them yet.

Key terms: **definition**—stable context bound to one agent; **request**—dynamic
data for one provider call; **blackboard**—public debate messages;
**conversation context**—a transcript snapshot separate from debate input.

## Bound definition

```python
PromptFileReference(path: pathlib.Path, sha256: str, content: bytes)
PromptFileReference.from_path(path: pathlib.Path) -> PromptFileReference
PromptFileReference.from_bytes(path: pathlib.Path, content: bytes) -> PromptFileReference
PromptFileReference.read_text() -> str
AgentDefinition(agent_id: str, backend: str, fingerprint: str,
    system_prompt_file: PromptFileReference,
    persona_file: PromptFileReference, language: str,
    context_document_files: tuple[PromptFileReference, ...])
AgentDefinition.file_references() -> tuple[PromptFileReference, ...]
```

References are frozen content snapshots. Construction validates strict UTF-8
and requires the SHA-256 to match the captured bytes; `content` is omitted from
their representation. `path` is a lexical source identity, not continuing read
authority: `read_text()` decodes the captured bytes, and provider binding does
not reread the path. A definition's fingerprint covers the agent, backend,
language, component identities, and hashes. Runtime bindings are cached by
`(agent_id, fingerprint)`.

| Agent | Bound core documents when current and provenance-backed |
| --- | --- |
| `lead_orchestrator` | all four |
| `plot_architect` | global and part outlines |
| `character_custodian` | character sheets |
| `execution_specialist` | narrative rules |

Absent, manually created, stale, and otherwise unprovenanced core documents are
omitted. Exact eligibility is in
[Canonical writing artifacts](writing-artifacts.md).

For core context, prompt assembly asks the artifact repository to read only the
paths allowlisted for that role. Each repository-verified
`ArtifactContentSnapshot` is converted to a `PromptFileReference` backed by the
same immutable bytes. Role-prompt and persona references are captured from
their paths at definition construction; all references are content-backed by
the time the backend receives the definition.

<a id="explicit-reference-read-contracts"></a>

## Explicit-reference read contracts

Import the public values from `story_writing_agents.writing_artifacts`:

```python
ReferenceInvocationCorrelation(
    operation_id: str, invocation_id: str, invocation_ordinal: int,
    agent_id: str, phase: str, round_number: int | None, attempt: int,
)
ReferenceReadRequest(
    correlation: ReferenceInvocationCorrelation,
    references: tuple[ArtifactReference, ...],
)
ReferenceReadAction(
    kind: ReferenceReadActionKind,
    correlation: ReferenceInvocationCorrelation,
    references: tuple[ArtifactReference, ...],
)
ReferenceReadActionKind.READ_WRITING_ARTIFACTS
```

The request and action are frozen, slotted, nonempty, ordered, and
content-free. They contain only complete invocation correlation and canonical
path/range indicators. The action kind is fixed, and an acceptable action must
equal its request's complete correlation and reference tuple exactly.

The module-supported synchronous `ReferenceReadInvocation` protocol and its
closeable, content-bearing `ReferenceReadResult` are backend/repository
collaborators rather than subpackage exports. A repository-created coordinator
owns the private one-use capability and exposes only its content-free request
to the backend. References remain a separate keyword-only backend argument;
they are not fields of `AgentRequest`, `ConversationRequest`, or
`ConversationRetrievalRequest`, and are not concatenated into task, user,
candidate, messages, agent state, conversation history, or retrieval
observations. See the
[backend boundary](backend-and-sessions.md#referenced-invocation-boundary).

## Debate request

```python
AgentRequest(
    session_id: str, debate_id: str, agent_id: str, phase: Phase,
    round_number: int, revision_id: str, task: str, user_payload: str,
    candidate_payload: str | None = None,
    messages: tuple[DebateMessage, ...] = (),
    agent_state: tuple[AgentStateEntry, ...] = (),
    conversation_context: ConversationContextWindow | None = None,
)
```

Requests are frozen and prompt-free. `task` carries phase instructions;
`user_payload` is the original input; `candidate_payload` is the current
candidate where applicable. `messages` is the public blackboard snapshot.
`agent_state` contains only the receiving agent's session-wide stored outcomes;
parallel peers receive the same pre-phase state snapshot.

`conversation_context` is `None` for context-omitted and fresh runs. Included runs put
the same validated immutable window in every draft, audit, proposal, synthesis,
and finalization request. It stays a separate provider field: the runtime does
not concatenate it into the task, input, candidate, blackboard, agent state, or
bound prompt.

```python
DebateMessage(sender: str, phase: str, round: int, revision: str,
              kind: str, content: str, timestamp: str)
AgentStateEntry(agent_id: str, session_id: str, debate_id: str, phase: str,
                round: int, revision: str, kind: str, content: str,
                timestamp: str)
```

Both are frozen and provide `to_dict()`; agent-state content is capped at 4096
characters per entry.

## One advisory conversation turn

```python
ConversationRequest(session_id: str, conversation_id: str, turn_id: str,
                    agent_id: str, content: str,
                    context: ConversationContextWindow)
ConversationReply(session_id: str, conversation_id: str,
                  turn_id: str, content: str)
ConversationRetrievalRequest(*, session_id: str, conversation_id: str,
    turn_id: str, agent_id: str, content: str, context: ConversationContextWindow)
ConversationRetrievalReply(*, session_id: str, conversation_id: str,
    turn_id: str, content: str, sources: tuple[ConversationRetrievalSource, ...])
```

The one-step pair is frozen and slotted with no defaults. IDs are safe, agents are
canonical, and content is nonempty exact strict UTF-8. `context` must be a
bounded, increasing window of complete turns from the same session and
conversation and exclude the current turn. Retrieval values are keyword-only;
their reply adds ordered validated sources without changing the one-step pair.
See [Conversation transcripts](conversation-transcripts.md) for windows and
[Bounded retrieval](conversation-retrieval.md) for actions and persistence.

## Debate reply

```python
AgentReply(revision_id: str, content: str = "",
           verdict: Verdict | None = None,
           core_updates: Mapping[str, str] = {})
AgentReply.candidate(revision_id: str, content: str) -> AgentReply
AgentReply.approval(revision_id: str, content: str = "") -> AgentReply
AgentReply.conflict(revision_id: str, critique: str) -> AgentReply
AgentReply.finalization(revision_id: str,
                        core_updates: Mapping[str, str] | None = None) -> AgentReply
```

| Phase | Valid reply |
| --- | --- |
| `draft` | Nonempty candidate; no verdict or updates. |
| `audit` | Approval with optional note, or conflict with nonempty critique. |
| `propose_revision` | Nonempty proposal; no verdict or updates. |
| `synthesize` | Nonempty complete replacement; no verdict or updates. |
| `finalize` | Empty or approved candidate content; optional allowlisted full-text updates. |

Every reply targets the requested revision. Specialists cannot update core
documents, and finalization cannot alter the approved manuscript. Public models
have no hidden-reasoning field; ordinary content is visible and may persist.
