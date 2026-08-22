# Workspace API

> **Availability:** workspace initialization, safe persistence primitives,
> directives, inspection, and the filesystem conversation adapters are
> implemented Python APIs.

Key terms: **relative path**—a location beneath the workspace root; **atomic
replacement**—one complete file replacing another; **reservation**—exclusive
ownership of a debate ID; **allowlist**—the fixed permitted core filenames.

## Construction and common methods

```python
Workspace(root: str | os.PathLike[str])
Workspace.initialize(*, overwrite_prompts: bool = False) -> Workspace
initialize_workspace(root: str | os.PathLike[str], *, overwrite_prompts: bool = False) -> Workspace

Workspace.validate_identifier(value: str, *, label: str = "session ID") -> str
Workspace.validate_template_name(value: str) -> str
Workspace.path(relative) -> pathlib.Path
Workspace.read_text(relative) -> str
Workspace.read_json(relative) -> Any
Workspace.write_text(relative, content, *, overwrite: bool = True) -> pathlib.Path
Workspace.write_json(relative, value, *, overwrite: bool = True) -> pathlib.Path
Workspace.read_persona(template_name: str) -> str
Workspace.persona_path(template_name: str) -> pathlib.Path
Workspace.persona_exists(template_name: str) -> bool
Workspace.read_core_documents(filenames: tuple[str, ...]) -> dict[str, str]
Workspace.load_session_overrides(session_id: str) -> dict[str, Any]
Workspace.save_session_overrides(session_id: str, overrides: Mapping[str, Any]) -> pathlib.Path
```

Initialization creates missing scaffold and normally preserves existing files.
With `overwrite_prompts=True`, it replaces exactly the four canonical role
prompts and preserves all other existing content. Initialization creates the
`core_documents/` directory but no core-document templates or manuscript.

The workspace root is resolved once. Public path operations accept only safely
contained workspace-relative paths with no absolute path, `..`, or symlink
component. Text is strict UTF-8. JSON readers reject malformed structures where
the consuming contract performs schema validation.

`write_text` and `write_json` atomically replace one file when overwrite is
allowed, except that both reject the four canonical core-document paths and
every canonical `manuscript/PART/PATH.md`. Canonical application writes must go
through the artifact repository so bytes, authorship, commit history, and the
current catalog cannot diverge. Generic writes remain available for other
safely contained workspace files. An external filesystem edit can still make
an artifact manual or stale; those bytes remain on disk but are ineligible
unless a matching catalog and commit record exist and the current hash agrees.
Stop all workspace processes before making such an external edit. Orchestration
restricts core updates to four names and delegates publication to the separate
artifact repository.

## Orchestration persistence primitives

```python
Workspace.append_agent_state(entry: AgentStateEntry) -> None
Workspace.read_agent_state(agent_id: str, session_id: str,
                           debate_id: str) -> tuple[AgentStateEntry, ...]
Workspace.append_debate_message(session_id: str, debate_id: str,
                                message: DebateMessage) -> None
Workspace.reserve_debate(session_id: str, debate_id: str, *,
                         input_kind: str, owner_token: str) -> dict[str, Any]
Workspace.transition_debate(session_id: str, debate_id: str, *,
    owner_token: str, status: str, terminal: bool,
    revision_id: str | None, rounds_completed: int,
    error: str | None = None) -> dict[str, Any]
Workspace.read_debate_messages(session_id: str,
                               debate_id: str) -> tuple[DebateMessage, ...]
Workspace.save_debate_result(result: DebateResult, *,
                             overwrite: bool = False) -> pathlib.Path
Workspace.save_debate_context(session_id: str, debate_id: str, *,
    conversation_id: str, mode: DebateContextMode,
    snapshot: ConversationContextWindow | None) -> pathlib.Path
Workspace.inspect_debate_context(session_id: str, debate_id: str) -> dict[str, Any] | None
Workspace.inspect_debate(session_id: str, debate_id: str) -> dict[str, Any]
Workspace.inspect_session(session_id: str) -> dict[str, Any]
```

These methods are public but are low-level orchestration primitives; normal
integrations should use `StoryRuntime`. Reservation uses exclusive creation, so
one session/debate ID has one owner. The owner token coordinates transitions
but is not authentication and is omitted by normal inspection.

`save_debate_context()` exclusively creates an identity-bound sidecar: included
mode requires a canonical snapshot, while fresh mode requires `None`.
`inspect_debate_context()` is a separate strict read and returns `None` for an
absent context sidecar. It does not change `inspect_debate()` or
`inspect_session()`. The schema is owned by
[Persistence](../design/persistence.md#debate-context-sidecar).

`Workspace` no longer exposes manuscript read/write or approved-artifact commit
helpers. `StoryRuntime` owns the supported orchestration entry and uses the
domain-specific, module-internal `WritingArtifactRepository` for provenance,
create-only part manuscripts, catalog-last publication, and recovery. See
[Canonical writing artifacts](writing-artifacts.md).

Constructing `Workspace`, `StoryRuntime`, or the artifact repository performs
no artifact I/O. Platforms without the repository's secure filesystem
primitives—including Darwin `renameatx_np(RENAME_EXCL)` or Linux
`renameat2(RENAME_NOREPLACE)`—can continue to use non-artifact workspace and
runtime APIs; the first catalog read, artifact read, or artifact commit raises
`WorkspaceSecurityError`.

## Directive utility

```python
RuntimeDirective(kind: DirectiveKind, language: str | None = None,
                 agent_id: str | None = None, persona: str | None = None)
parse_directive(text: str) -> RuntimeDirective | None
apply_directive(workspace: Workspace, session_id: str,
                text: str) -> dict[str, object]
```

Parsing recognizes only a complete exact directive and returns `None`
otherwise. Applying validates and atomically saves the resulting override or
raises `DirectiveError`. Exact forms and precedence are in
[Runtime overrides](configuration/runtime-overrides.md).

## Conversation adapters

Key terms: **catalog**—conversation metadata and discovery; **event store**—the
visible transcript and its bounded context view.

`FilesystemConversationCatalog(workspace)` and
`FilesystemConversationEventStore(workspace, *, catalog=None)` are backed by
`Workspace`. Their contracts live in [Conversation catalog](conversation-catalog.md)
and [Conversation transcripts](conversation-transcripts.md). See
[Filesystem safety](../operations/filesystem-safety.md) for the security
boundary and [Session storage](configuration/session-storage.md) for paths.
