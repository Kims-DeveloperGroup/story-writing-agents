# Workspace API

> **Availability:** workspace initialization, safe persistence primitives,
> directives, inspection, and the supported filesystem catalog facade are
> implemented Python APIs.

Key terms: **relative path**—a location beneath the workspace root; **atomic
replacement**—one complete file replacing another; **reservation**—exclusive
ownership of a debate ID; **allowlist**—the fixed permitted core filenames.

## Construction and common methods

```python
Workspace(root: str | os.PathLike[str])
Workspace.initialize() -> Workspace
Workspace.require_ready() -> Workspace
initialize_workspace(root: str | os.PathLike[str]) -> Workspace

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
Workspace.load_session_overrides(session_id: str) -> dict[str, Any]
Workspace.save_session_overrides(session_id: str, overrides: Mapping[str, Any]) -> pathlib.Path
```

There is no prompt-overwrite parameter, alias, or compatibility shim.
Initialization accepts only an absent root, an existing empty safe directory,
or one exact journal-backed interrupted-v2 root. `initialize_workspace(root)`
is the convenience wrapper for `Workspace(root).initialize()`.

Construction performs a nonmutating classification. A ready root is fully
verified at construction; a missing, empty, or exactly initializing root is
captured as not ready. Unsupported, malformed, markerless arbitrary nonempty,
required-path-drifted, staged/disallowed-tracked, or unsafe state raises
immediately. Unknown ignored and untracked entries in an otherwise ready root
are opaque to this classification. Construction retains no open descriptor and
performs no Git or filesystem mutation.

A ready root may be the pristine initialization root or one exact #504 session
linked worktree. Session-root construction verifies its schema-derived path,
session ID, common-Git indirection, registration, branch, and immutable
baseline/seed anchors. It does not make arbitrary linked worktrees valid and
does not interpret later #505 history. Nearest discovery preserves whether the
result is the main or session root internally; the public `Workspace.root`
remains the exact root that was discovered or supplied.

`initialize()` owns one attempt on that object:

```text
missing/empty
  -> exact initialization journal
  -> exact 25-file scaffold
  -> one #448 main baseline
  -> ready verification with journal
  -> journal removal and directory synchronization
  -> ready verification without journal
  -> return self
```

The scaffold includes strict `config/workspace.json`, current packaged
configuration/persona/role-prompt bytes, ten empty `.gitkeep` files, and the
four exact zero-byte core documents. It includes no story content. The Git
baseline uses the effective author and committer, contains exactly the
initializer manifest, has one parentless commit named
`Initialize story workspace`, and leaves `main` clean.

Marker and journal decoding accepts only canonical UTF-8 JSON with a trailing
newline and their closed fields. Duplicate, missing, extra, malformed, and
noncanonical values fail; a numeric version other than `2` receives the fixed
unsupported-workspace guidance.

A ready `initialize()` call is validation-only. It does not rewrite, restage,
or recommit. If an initialization attempt fails, the object becomes terminal;
later calls on that same object raise `OSError` before another attempt. A fresh
object or process may inspect and resume only an exact durable journal and
matching partial scaffold. That initializing state remains recursively closed:
unknown or divergent bytes are preserved and rejected rather than adopted,
reset, deleted, or overwritten.

Journal-free readiness uses a narrower required-path projection. For a main
root it checks root identity and journal absence; required directories and all
25 scaffold files through descriptor-relative no-follow reads; canonical
marker and immutable bytes; valid mutable configuration and the safe regular
presence of referenced personae; and the exact baseline, index, `main`
topology/history, and correlated session-resource inventory. It does not list,
stat, open, decode, or descend into unknown filesystem entries. Git readiness
likewise does not enumerate ignored or untracked paths. Those entries are
permitted but not trusted: the provider or runtime owner must validate optional
state when consuming it. Staged changes and tracked unknown additions remain
recovery conditions.

A ready session root validates the same 25 scaffold leaves and every ancestor
that Git materializes, plus its local marker, configuration, referenced
personae, clean index, and exact correlated session topology. It does not
require schema-only empty runtime directories that are absent from a linked
worktree. Unstaged story edits and untracked entries are allowed; staged
changes are not.

`require_ready()` checks the readiness already authorized for that object. It
does not perform a live filesystem rescan. Supported CLI entry points and
explicit smoke construct a fresh object before activation, so they receive a
strict current inspection. A long-lived direct Python caller must not treat a
`Workspace` as a filesystem watcher, immutable snapshot, or transaction across
operations.

Exact expected failure categories include:

| Condition | Public result |
| --- | --- |
| Effective Git author or committer is missing, invalid, or divergent | `ConfigurationError("effective Git identity is unavailable or invalid")`; a fresh process may resume the exact journal after identity is configured. |
| Recognizable markerless workspace or non-v2 numeric marker/journal | `WorkspaceVersionUnsupportedError` with guidance that migration and compatibility are unsupported and a new workspace is required. |
| Markerless arbitrary nonempty root, malformed record, partial mismatch, required-path drift, staged/disallowed tracked state, tracked unknown addition, or baseline drift | `WorkspaceFormatError`; bytes are preserved and recovery is required. Unknown ignored/untracked entries alone are not this condition. |
| Parent repository, arbitrary or uncorrelated linked worktree, submodule, sparse/bare/mismatched repository, symlinked administration, or unsafe root | `WorkspaceSecurityError` or a content-free workspace topology error before unsafe mutation. An exact correlated #504 session worktree is the only supported linked-worktree form. |

Initialization rejects parent/nested repositories and creates no session
branch, sibling worktree, internal runtime ref, provider state, artifact
publication, remote, or later commit. The marker records only the exact naming
grammar. The separate package-internal #504 seam owns those resources for #455;
initialization itself authorizes only its one fixed root commit.

Public path operations accept only safely contained workspace-relative paths
with no absolute path, `..`, or symlink component. They first require the
object's captured ready state. Text is strict UTF-8. JSON readers reject
malformed structures where the consuming contract performs schema validation.
Package-internal root helpers recheck the bound identity for diagnostic and
storage owners, but they are not public imports.

This explicit path contract is unchanged by opaque readiness. For example,
readiness does not inspect an unknown symlink beneath `.antigravity/`, but a
later `Workspace.path()` or read directed through that symlink still rejects
it as uncontained/unsafe rather than following it.

`Workspace.read_text()` is a raw, safely contained UTF-8 read. It does not
classify canonical paths or perform the artifact repository's descriptor-safe
source capture. Although it can physically read a canonical file, callers must
not treat that result as the immutable snapshot used for story-agent context.
`Workspace.read_core_documents()` has been removed with no forwarding alias.
Low-level contextual execution selects trusted story context through the
artifact repository. Python lead turns start that read-only provider path;
shell management does not. Generic raw reads remain appropriate for
noncanonical inputs and internal workspace files.

`write_text` and `write_json` atomically replace one file when overwrite is
allowed, except that both reject the four canonical core-document paths, every
canonical `manuscript/PART/PATH.md`, and case-folded or NFKC-normalized aliases
of either reserved top-level namespace. They also reject the lifecycle marker
and journal. This check occurs before parent or
target mutation. Canonical application writes must go through the artifact
repository so bytes, authorship, commit history, and the current catalog cannot
diverge. Generic writes remain low-level operations, but using them against the
original initialization root makes a later fresh readiness check reject that
root when they alter a required or tracked path or stage an unknown path. An
untracked unknown file is outside readiness, but remains untrusted and creates
no catalog, authorship, ownership, or commit record. Later legitimate mutable
state belongs in the exact session/internal worktrees after #455 composition.
Stop all workspace processes before any external inspection or recovery
action.

Cataloged manuscript paths are permanent create-only publication identities.
Deleting one externally removes it from source discovery but does not free the
path for reuse; a later revision must use a distinct `PART/PATH.md`. A manual
file at an otherwise unused path is discoverable but also occupies that target,
so publication must choose another path or fail without overwrite.

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

These methods are public but are low-level orchestration primitives. Use
`StoryRuntime` for current management and inspection, not to start the retained
debate engine. Reservation uses exclusive creation, so
one session/debate ID has one owner. The owner token coordinates transitions
but is not authentication and is omitted by normal inspection.

`save_debate_context()` exclusively creates an identity-bound sidecar: included
mode requires a canonical snapshot, while fresh mode requires `None`.
`inspect_debate_context()` is a separate strict read and returns `None` for an
absent context sidecar. It does not change `inspect_debate()` or
`inspect_session()`. The schema is owned by
[Persistence](../design/persistence.md#debate-context-sidecar).

`Workspace` no longer exposes manuscript read/write or approved-artifact commit
helpers. The domain-specific, module-internal `WritingArtifactRepository` owns
provenance, create-only part manuscripts, catalog-last publication, and
recovery. `StoryRuntime` has no current public publication entry. See
[Canonical writing artifacts](writing-artifacts.md).

Constructing `Workspace`, `StoryRuntime`, or the artifact repository performs
no artifact I/O. Platforms without the repository's secure filesystem
primitives—including Darwin `renameatx_np(RENAME_EXCL)` or Linux
`renameat2(RENAME_NOREPLACE)`—can continue to use non-artifact workspace and
runtime APIs; the first source list/read, catalog read, or artifact commit raises
`WorkspaceSecurityError`.

## Internal session-resource composition

`workspace.initialization` defines five package-internal typed operations for
#455: inspect one session, list the exhaustive correlated inventory, provision
or resume one exact triple, require one exact ready triple, and capture one
complete session-history snapshot. They are absent
from `story_writing_agents`, `story_writing_agents.workspace`, and
`story_writing_agents.git`; direct application code must not treat them as a
stable public API.

Inspect, list, and require-ready are read-only. Provision alone may create or
resume, and only from the exact main root. Results contain closed identities,
states, recovery actions, and object IDs—never descriptors, raw Git output,
argv/environment, story content, or a reusable Git capability. Provider or
lead text and serialized values cannot invoke the mutation owner. See the
[internal Git-resource contract](git-adapter.md#package-internal-session-git-resources).

<a id="workspace-session-history-snapshot"></a>

### Session-history snapshot composition

The package-internal composition seam is exact and read-only:

```python
from story_writing_agents.workspace.initialization import inspect_session_history

# Exact signature: (root: Path, session_id: str) -> GitSessionHistorySnapshot
snapshot = inspect_session_history(root, session_id)
```

It derives the baseline manifest from the canonical initializer scaffold,
accepts either the exact main workspace root or an exact session worktree root,
resolves the main repository, and opens the requested session's history with
`create=False`.
Consequently an empty history returns an immutable empty snapshot without
creating `.git/story-writing-agents/runtime/session-history/<session_id>`.
Existing history is captured under its repository lease and must pass the
snapshot's independent stability reinspection.

The helper returns no live history handle and performs no write, replay,
provider call, reconciliation, index/ref/branch/worktree change, or UI action.
The helper and snapshot type are absent from the root, workspace, and Git
facades. #527 may consume the snapshot later; #536 itself does not decide or
enforce session recovery. See the [complete snapshot contract](git-adapter.md#package-internal-session-history-snapshot).

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
authoritative schema-v2 collaboration events and bounded readable context.

`story_writing_agents.workspace` exports `Workspace`, `initialize_workspace`,
and `FilesystemConversationCatalog`. It does not export a concrete event-store
adapter. Advanced composition code imports the filesystem or SQLite
`CollaborationEventStore` adapter directly from its defining module; the
package root, workspace facade, and `conversation_storage` facade deliberately
do not re-export those concrete adapters.

The filesystem owner exclusively owns conversation metadata and schema-v2
event paths, parsing, serialization, allocation cleanup, and locking.
`workspace.core` continues to own generic secure workspace primitives plus
debate/session persistence. Private conversation implementation globals are
not available from the workspace facade. The contracts live in
[Conversation catalog](conversation-catalog.md) and
[Conversation transcripts](conversation-transcripts.md). See
[Filesystem safety](../operations/filesystem-safety.md) for the security
boundary and [Session storage](configuration/session-storage.md) for paths.
