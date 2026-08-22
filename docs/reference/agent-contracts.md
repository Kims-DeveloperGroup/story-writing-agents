# Agent contracts

> **Availability:** these models are Python APIs. Debate requests can carry an
> optional conversation snapshot; one-step and retrieval turns use separate,
> compatible request families.

Key terms: **definition**—stable context bound to one agent; **request**—dynamic
data for one provider call; **blackboard**—public debate messages;
**conversation context**—a transcript snapshot separate from debate input.

## Bound definition

```python
PromptFileReference(path: pathlib.Path, sha256: str)
PromptFileReference.from_path(path: pathlib.Path) -> PromptFileReference
PromptFileReference.read_text() -> str
AgentDefinition(agent_id: str, backend: str, fingerprint: str,
    system_prompt_file: PromptFileReference,
    persona_file: PromptFileReference, language: str,
    context_document_files: tuple[PromptFileReference, ...])
AgentDefinition.file_references() -> tuple[PromptFileReference, ...]
```

References validate strict UTF-8 and hash exact bytes. A definition stores
absolute paths and hashes, not full text; its fingerprint covers the agent,
backend, language, component identities, and hashes. Runtime bindings are
cached by `(agent_id, fingerprint)`.

| Agent | Bound core documents when current and provenance-backed |
| --- | --- |
| `lead_orchestrator` | all four |
| `plot_architect` | global and part outlines |
| `character_custodian` | character sheets |
| `execution_specialist` | narrative rules |

Absent, manually created, stale, and otherwise unprovenanced core documents are
omitted. Exact eligibility is in
[Canonical writing artifacts](writing-artifacts.md).

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
