# Agent contracts

> **Availability:** these models are Python APIs; `StoryRuntime` uses both sets,
> while `BoundAgent.converse()` uses the conversation request and reply.

Key terms: **definition**—stable context bound to one agent; **file reference**—
a path plus content digest; **request**—dynamic data for one turn; **reply**—
validated provider output; **visible context**—prior transcript events the
caller explicitly supplies.

## Bound definition

```python
PromptFileReference(path: pathlib.Path, sha256: str)
PromptFileReference.from_path(path: pathlib.Path) -> PromptFileReference
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

`from_path()` validates strict UTF-8 and stores lowercase SHA-256 of exact
bytes. `read_text()` accepts only unchanged bytes; missing, unreadable,
invalid-UTF-8, or digest-mismatched content raises `WorkspaceFormatError`.
`file_references()` returns role prompt, persona, then context documents.

Definitions contain absolute workspace paths and hashes, never full prompt or
story text. The fingerprint covers agent, backend, language, component
identities, and hashes. Runtime bindings are cached by `(agent_id,
fingerprint)` for one runtime; identical concurrent binds share one in-flight
operation and failed binds are not cached.

| Agent | Role prompt | Context |
| --- | --- | --- |
| `lead_orchestrator` | `config/agents/lead_orchestrator/AGENTS.md` | all four core documents |
| `plot_architect` | `config/agents/plot_architect/AGENTS.md` | `global_outline.md`, `part_outlines.md` |
| `character_custodian` | `config/agents/character_custodian/AGENTS.md` | `character_sheets.md` |
| `execution_specialist` | `config/agents/execution_specialist/AGENTS.md` | `narrative_rules.md` |

## Debate request

```python
AgentRequest(
    session_id: str, debate_id: str, agent_id: str, phase: Phase,
    round_number: int, revision_id: str, task: str, user_payload: str,
    candidate_payload: str | None = None,
    messages: tuple[DebateMessage, ...] = (),
    agent_state: tuple[AgentStateEntry, ...] = (),
)
```

Requests are frozen and prompt-free. `task` carries phase instructions and the
no-hidden-reasoning requirement; `user_payload` is the original input;
`candidate_payload` is the current candidate where applicable. `messages` is
the public blackboard snapshot. `agent_state` contains only the receiving
agent's session-wide persisted outcomes, including earlier debates and already
recorded current outcomes. Parallel peers receive the same pre-phase snapshot.

```python
DebateMessage(sender: str, phase: str, round: int, revision: str,
              kind: str, content: str, timestamp: str)
AgentStateEntry(agent_id: str, session_id: str, debate_id: str, phase: str,
                round: int, revision: str, kind: str, content: str,
                timestamp: str)
```

Both are frozen and provide `to_dict()`. Agent-state content is capped at 4096
characters per entry.

## One conversation turn

```python
ConversationRequest(session_id: str, conversation_id: str, turn_id: str,
                    agent_id: str, content: str, context: ConversationContextWindow)
ConversationReply(session_id: str, conversation_id: str, turn_id: str, content: str)
```

Both are frozen and slotted, with no defaults. IDs follow the common
[safe-identifier contract](configuration/identifiers-and-errors.md); the request
agent must be canonical. Content is nonempty strict UTF-8 and is not normalized.

`context` must be an exact `ConversationContextWindow` of complete,
increasing events from the same session and conversation, within fixed caps and
without the current turn. Its metadata must match its events; sequence gaps and
interleaved turns are allowed. See [Conversation transcripts](conversation-transcripts.md).

The reply validates and correlates its IDs. `StoryRuntime` persistence and
results belong to the [Runtime API](runtime-api.md#one-conversation-turn);
provider details belong to [Backend and sessions](backend-and-sessions.md).

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

Revision ID must equal the request target. Content is a string, verdict is an
enum member or `None`, and updates map filenames to complete string content.

| Phase | Valid reply |
| --- | --- |
| `draft` | Nonempty candidate; no verdict or updates. |
| `audit` | `APPROVE` with optional note, or `CONFLICT` with nonempty critique; no updates. |
| `propose_revision` | Nonempty proposal; no verdict or updates. |
| `synthesize` | Nonempty complete replacement; no verdict or updates. |
| `finalize` | No verdict; content empty or exactly the approved candidate; optional allowlisted full-text updates. |

Specialists cannot update core documents, and finalization cannot alter the
approved manuscript. Public models have no hidden-reasoning field; provider
text put in ordinary content remains visible and may be persisted.
