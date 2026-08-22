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
Workspace.read_manuscript(manuscript_name: str) -> str
Workspace.write_manuscript(manuscript_name: str, content: str) -> pathlib.Path
Workspace.load_session_overrides(session_id: str) -> dict[str, Any]
Workspace.save_session_overrides(session_id: str, overrides: Mapping[str, Any]) -> pathlib.Path
```

Initialization creates missing scaffold and normally preserves existing files.
With `overwrite_prompts=True`, it replaces exactly the four canonical role
prompts and preserves all other existing content. The option must be a boolean;
invalid input raises `TypeError` before any I/O and does not consume a pending
instance's one initialization attempt.

Construction resolves the workspace root once and may make a nonmutating
filesystem identity probe; it retains no open descriptor. An existing canonical
directory with meaningful positive device and inode values binds immediately. A
specifically missing root remains pending for the first valid `initialize()`
call. That call terminalizes the pending state before checking filesystem
capabilities or mutating anything. It preflights the required safe
descriptor-relative directory and atomic-file primitives, then walks the
canonical root from an opened filesystem anchor. Each missing component is
created relative to its opened parent and opened without following its name.
The final root descriptor is identity-checked and retained for all topology and
default writes.

Those writes walk only beneath the retained descriptor and install verified
temporary regular files through held parent descriptors. Create-only entries
leave existing non-symlink targets unchanged and do not replace a competing
target; `overwrite_prompts=True` uses atomic replacement only for the four
canonical role prompts. Each installed target must identify the staged file.
After all writes, initialization rewalks the canonical root without following
component names, matches it to the retained identity, and closes the retained
descriptor. Only then does the instance record success. Replacing an ancestor
or root after the initial comparison therefore cannot redirect the scaffold or
establish a usable diagnostic identity at another location.

The original `OSError`, `KeyboardInterrupt`, or `SystemExit` from the first valid
attempt propagates unchanged, including failures during root creation, file
installation, identity verification, child/default work, or final descriptor
cleanup. Cleanup does not replace an active body failure. Safely created root
components and intended partial data on the retained root are not rolled back.
If any step before complete success fails, every later `initialize()` call
rejects with the established identity-unavailable `OSError` before root
creation, stat or bind, child creation, or default writes; it does not retry or
repeat the original error. Repeated calls after success retain their established
pathname-based preserve-existing and optional prompt-overwrite behavior.
Constructing a new `Workspace` performs a new identity probe, so callers should
inspect and repair a failed root path first.

This lifecycle changes no public signature, successful return value, or
initialized file content.

If safe creation primitives are unavailable, the first valid attempt raises
`OSError` before mutation. This is a fail-closed capability requirement, not a
promise that missing-root initialization works on every platform.

An existing directory whose construction-time probe is unavailable or
nonmeaningful is the established exception to the initialization requirement:
normal pathname-based initialization may still create its scaffold, while
diagnostic storage remains unavailable for that instance. It also never
silently rebinds.

Package-internal, non-root-exported helpers open and reverify that bound root for
diagnostic storage. Replacing the root does not make an existing `Workspace`
follow the replacement for logging; the diagnostic record is dropped without a
fallback path. These helpers add no public signature, and this identity does not
apply to normal `Workspace` methods. Those methods remain pathname-based and may
read or write a real directory installed later at the same root path; callers
cannot use one instance as an identity-pinned filesystem snapshot or a
transaction across operations. Public path operations retain their established
contract: they accept only safely contained workspace-relative paths with no
absolute path, `..`, or symlink component.
Text is strict UTF-8. JSON readers reject malformed structures where the
consuming contract performs schema validation.

`write_text` and `write_json` atomically replace one file when overwrite is
allowed. `write_manuscript` never overwrites. A direct caller of generic write
methods is a trusted workspace client and may change any safely contained
relative file; orchestration itself restricts core updates to four names.

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
Workspace.commit_approved_artifacts(manuscript_name: str, candidate: str,
    core_updates: Mapping[str, str]) -> tuple[str, tuple[str, ...]]
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

Artifact commit validates the complete update map and manuscript target before
content writes. It stages and atomically replaces allowlisted core documents in
fixed order, then exclusively creates the exact approved manuscript. On later
failure it attempts to remove the manuscript and restore earlier core files.
That rollback is best effort, not a transaction spanning files, logs, or state.

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
