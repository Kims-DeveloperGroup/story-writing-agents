# Runtime management API

`StoryRuntime` is the public management, configuration, directive, and
inspection facade. `RuntimeSession` binds those operations to one session ID.
Neither class exposes a public advisory-turn or debate-start method.

## Construction

```python
StoryRuntime(
    workspace: Workspace | str | pathlib.Path,
    backend: AgentBackend | None = None,
    *,
    conversation_event_store: ConversationEventStore | None = None,
    conversation_storage_services: ConversationStorageServices | None = None,
    conversation_rag_service: ConversationRagRuntimeService | None = None,
)
```

With no conversation arguments, construction composes the filesystem storage
bundle. A complete `ConversationStorageServices` bundle and a raw
`conversation_event_store` are mutually exclusive. A RAG service requires the
complete storage-services boundary and cannot be paired with only the raw event
store. Construction does not invoke a provider.

`backend` remains available for quiescent configuration activation used by the
interactive settings workflow. Its presence does not add execution methods.

## Public methods

```python
StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(session_id: str, text: str) -> dict[str, object]
StoryRuntime.set_runtime_overrides(session_id: str,
                                   overrides: Mapping[str, Any]) -> dict[str, Any]
await StoryRuntime.prepare_runtime_configuration(
    configuration: ConfigurationSnapshot,
    provider_route_generations: Mapping[str, int],
) -> RuntimeConfigurationCandidate
await StoryRuntime.commit_runtime_configuration(
    candidate: RuntimeConfigurationCandidate,
    backend_candidate: BackendRuntimeConfigurationCandidate,
) -> None
StoryRuntime.discard_runtime_configuration(
    candidate: RuntimeConfigurationCandidate,
) -> None
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(session_id: str, debate_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate_context(
    session_id: str,
    debate_id: str,
) -> dict[str, Any] | None
StoryRuntime.inspect_reference_operation(
    operation_id: str,
) -> ReferenceOperationHistory | None
```

`RuntimeConfigurationCandidate` is defined in the orchestrator module but is
not a package-root export. Prepare requires a backend and complete quiescence;
commit rechecks candidate ownership and quiescence before swapping the backend
and prompt snapshot. Discard owns no resource.

`RuntimeSession` exposes the bound equivalents:

```python
RuntimeSession.apply_directive(text: str) -> dict[str, object]
RuntimeSession.set_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]
RuntimeSession.inspect() -> dict[str, Any]
RuntimeSession.inspect_debate(debate_id: str) -> dict[str, Any]
RuntimeSession.inspect_debate_context(debate_id: str) -> dict[str, Any] | None
RuntimeSession.inspect_reference_operation(
    operation_id: str,
) -> ReferenceOperationHistory | None
```

The bound reference inspection rejects an operation ID that does not belong to
its session. Inspection returns `None` only where the underlying strict sidecar
or reference history is absent; other validation/storage failures raise.

## Example

```python
from story_writing_agents import StoryRuntime, Workspace

workspace = Workspace("/path/to/initialized-story")
runtime = StoryRuntime(workspace)

runtime.apply_directive("novel", "[SYS: LANG=EN]")
session = runtime.session("novel")
session.set_overrides({"language": "EN", "persona": None, "agents": {}})
snapshot = session.inspect()
print(snapshot["session_id"])
```

There is no package-root `Runtime` or `Session` alias. Import the explicit
`StoryRuntime` and `RuntimeSession` names.

## Removed execution surface

No alternate or forwarding entry point is provided for public direct story
execution. Code must not expect a method that sends an advisory prompt,
performs retrieval for a turn, starts a prompt debate, or starts a draft debate
on either runtime class.

The generic backend/provider/model/prompt/context/lifecycle components and
schema-v2 collaboration contracts remain available through their own modules.
Applications can create, validate, project, search, page, archive, and transfer
structured collaboration history without treating this management facade as
an execution engine. Active lead/specialist orchestration will be connected by
the dedicated schema-v2 integration, not by restoring a removed method.

<a id="runtime-diagnostic-side-effect"></a>

## Diagnostics and side effects

Runtime construction creates no diagnostic file. Later directive,
configuration, and inspection calls best-effort append bounded operational
facts to `debug_logs/runtime.jsonl`. Diagnostic failure is swallowed and cannot
replace the method's result or exception.

Session/debate inspection does not mutate domain state, but this diagnostic
append means it is not filesystem-byte-pure. Reference-operation inspection
reads validated content-free history only. None of these methods appends a
conversation event, invokes RAG, calls a provider, or publishes an artifact.

<a id="context-lifecycle-observer"></a>

## Related direct APIs

- [Schema-v2 collaboration history](collaboration-history.md)
- [Conversation search](conversation-search.md)
- [Conversation archives and transfer](conversation-archives.md)
- [Package API](package-api.md)
- [Agent contracts](agent-contracts.md)
