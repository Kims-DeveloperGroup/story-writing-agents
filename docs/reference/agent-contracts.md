# Agent contracts

> **Availability:** these models are Python APIs. Debate requests can carry an
> optional conversation snapshot; one-step and retrieval turns use separate,
> compatible request families. Content-free explicit-reference read contracts
> remain available at the direct repository/backend boundary. The current
> `StoryRuntime` does not construct them for a public provider call.

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
    context_document_files: tuple[PromptFileReference, ...],
    provider_managed_context: bool = False)
AgentDefinition.file_references() -> tuple[PromptFileReference, ...]
```

References are frozen content snapshots. Construction validates strict UTF-8
and requires the SHA-256 to match the captured bytes; `content` is omitted from
their representation. `path` is a lexical source identity, not continuing read
authority: `read_text()` decodes the captured bytes, and provider binding does
not reread the path. A definition's fingerprint covers the agent, backend,
language, component identities, and hashes. Runtime bindings are cached by
`(agent_id, fingerprint)`.

| Agent | Contextual P2 core sources in the built-in runtime |
| --- | --- |
| `lead_orchestrator` | all four |
| `plot_architect` | global and part outlines |
| `character_custodian` | character sheets |
| `execution_specialist` | narrative rules |

Built-in definitions set `provider_managed_context=True` and
`context_document_files=()`. The table describes contextual source eligibility,
not permanent binding bytes. A direct custom definition may leave the flag
false and eagerly bind explicit content-backed references; that compatibility
path preserves its prior fingerprint and adapter behavior. Exact source and
publication rules are in
[Canonical writing artifacts](writing-artifacts.md).

For current built-in core context, source capture and the manifest/planner—not
prompt assembly—read the role-authorized current snapshot. A manual core change
therefore changes the source revision/delta and can force bootstrap; it does not
change the stable definition fingerprint. Role-prompt and persona references
remain content-backed at definition construction.

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
    story_session_context: StorySessionContext | None = None,
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

`story_session_context` is a trailing package-internal capture and direct-call
compatibility value. It must match the request's session and agent. The current
built-in runtime translates authoritative capture into compact contextual wire
v5; it does not send this object as the provider envelope. Direct noncontextual
callers may still use the established request field. The context types are not
package-root exports; see [Agent-context design](../design/agent-context.md#compact-provider-wire-v5).

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
                    context: ConversationContextWindow,
                    story_session_context: StorySessionContext | None = None)
ConversationReply(session_id: str, conversation_id: str,
                  turn_id: str, content: str)
ConversationRetrievalRequest(*, session_id: str, conversation_id: str,
    turn_id: str, agent_id: str, content: str, context: ConversationContextWindow,
    story_session_context: StorySessionContext | None = None,
    rag_context: ConversationRagContext | None = None)
ConversationRetrievalReply(*, session_id: str, conversation_id: str,
    turn_id: str, content: str, sources: tuple[ConversationRetrievalSource, ...])
```

The one-step pair is frozen and slotted; its request's trailing continuity
envelope defaults to `None`. IDs are safe, agents are canonical, and content is
nonempty exact strict UTF-8. `context` must be a
bounded, increasing window of complete turns from the same session and
conversation and exclude the current turn. Retrieval values are keyword-only;
their two trailing context fields default to `None`, and their reply adds
ordered validated sources without changing the one-step pair.
When present, story-session capture must match the request session, agent, and
any focused conversation identity. Current built-in execution converts it to
contextual wire v5; omitting it retains direct-request compatibility. An
omitted `rag_context` means there was no attempted RAG envelope.
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
