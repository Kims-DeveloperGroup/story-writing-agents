# Documentation

Use this index to find one canonical answer instead of searching a long manual.

> **Availability:** bare `story` and `story chat` open the installed TTY shell.
> It manages story sessions and subject conversations, routes single-line
> retrieval-enabled turns, searches and reads transcripts, and runs
> focused-conversation consensus. It also provides global help and lifecycle
> status, attached directives and safe inspection, focused history, and
> controller-boundary cancellation for active operations and local reads.
> Desired conversation storage is resolved and activated before the native
> authentication gate and interactive runtime construction.
> Optional conversation RAG currently has direct Python contracts, a
> deterministic local provider, a disposable generation store, and explicit
> composition plus programmatic runtime/agent authorization, projection, and
> cited context. Noninteractive `story rag` status, opt-in, rebuild, cited
> search, disable, and derived-only purge are implemented. Interactive
> `/settings` persists Disabled/Local and Off/On policy for the next shell;
> eligible focused turns can use automatic preflight or one private armed
> `/rag QUERY` preflight.
> Context-capable ordinary, retrieval, prompt-debate, and draft-debate work
> automatically shows live content-free bootstrap/delta, action, answer, and
> checkpoint progress; Python callers can supply the same optional synchronous
> observer directly.
> Inline `${path}` and `${path}#Lx-Ly` references are active in provider-bound
> prompts. In those shell spans, `$` discovers current safe reference sources.
> Exact `$$` immediately shows ranged-source choices; Enter/Tab opens the
> highlighted source in the independent viewer. A complete `$${path}` opens
> that source directly on Enter/Tab. These are editing controls, not commands or
> automation UI.
> Bare `/exchanges` is a separate attached-session inspection command. It
> selects an inventoried conversation/debate and canonical agent before opening
> the base-installed isolated read-only JSONL viewer over one immutable
> snapshot. No setting, enablement step, or extra is required.
> Schema-v2 structured collaboration history is also available as a pure
> `story_writing_agents.conversations` Python contract. That facade now includes
> one-readable-record-per-event and bounded explicit-context projections; the
> direct `conversation_search` module supplies pure search, exact citations,
> snapshot paging, and derived document identity. None is active in the current
> runtime, storage adapters, transcript UI, shell, RAG, or provider calls;
> #475-#479 own integration.

## Availability matrix

| Capability | Available through |
| --- | --- |
| Initialize a workspace; run or inspect a debate | Installed CLI and Python API |
| Configure Codex or Antigravity per agent | Persistent JSON or shell `/settings`; one-process CLI overrides for `story chat` and `story run` |
| Select filesystem or SQLite conversation storage | Shell `/settings` or provider-free `story storage use`; selection saves desired storage for the next shell and never transfers or activates data. [Newcomer workflow](guides/sqlite-conversation-storage.md) and [exact report](reference/cli.md#conversation-storage-selection) |
| Check or complete native provider authentication | Interactive startup gate or global shell `/auth`; [exact commands and routing invariant](reference/cli.md#interactive-provider-authentication) |
| Create, reopen, or pause a provider session | Installed CLI and Python API |
| Open the TTY shell; list, create, resume, switch, or pause sessions | Bare `story` or `story chat` |
| Show registry help or shell status | Global in the installed shell |
| Apply directives; safely inspect sessions or debates | Current attachment; no conversation focus required |
| Inspect recorded agent/provider request, response, and outcome exchanges | Every normal installation includes exact-pinned `jsonl-viewer` 0.1.1; attached shell `/exchanges`; no conversation focus, identifier argument, setting, or extra. The command opens transient scope and agent pickers, then the read-only viewer. [Availability, upgrade, workflow, preview, and privacy](guides/agent-exchanges.md) |
| Create, list, resume, rename, or archive subject conversations | Installed shell after session attachment; catalog Python API |
| Serialize or transfer canonical conversation-only archives | Direct `story_writing_agents.conversation_storage` Python API; serialization is pure, while filesystem export/import require a caller-held mutation lease. There is no raw archive shell command or complete-workspace backup. |
| Use the optional SQLite conversation catalog, transcript store, and exact literal search | Installed shell after an explicit next-shell selection, or direct Python API after readiness preflight and explicit provisioning; derived FTS5 hints are optional and non-authoritative. [Setup, data lifecycle, and recovery](guides/sqlite-conversation-storage.md) |
| Plan, copy, or verify canonical v2 collaboration history between filesystem and SQLite | Provider-free `story storage transfer` / `story storage verify`, or the transfer-named direct API retained in the internal `conversation_storage.migration` module. Both stores are in one workspace, SQLite is already ready, `--from` is active, and the workflow never activates or deletes a backend. [Scenario-driven transfer](guides/sqlite-conversation-storage.md#sqlite-storage-transfer) |
| List or target agents; send ordinary, `@AGENT`, or `/say` turns | Installed shell; an active conversation must have current focus |
| Read durable/access facts and perform exact session transitions | Python API; used by the installed shell and runtime |
| Parse the interactive command language and route metadata | Python API; the shell dispatches a documented subset |
| Compose a prompt and render safe terminal records | Shell `/run --output PART/PATH.md`; module-supported terminal Python API |
| Append/read transcripts; derive bounded complete-turn context | Shell turns append/consume context and `/transcript` reads fresh bounded pages; Python API |
| Model, validate, inspect, search, cite, page, or explicitly select a future lead-led collaboration history | Pure `story_writing_agents.conversations` facade and direct `story_writing_agents.conversation_search` schema-v2 Python APIs only; no current runtime, store, UI, RAG, provider, or command integration. [User impact, readable records, state versus context, and examples](reference/collaboration-history.md) |
| Search one session and cite hits | Attached shell `/search` for active records; Python API can explicitly include archived records |
| Ask an agent to retrieve cited conversation history | Focused shell conversation; additive Python runtime API; active records in the current session only |
| Observe bounded agent-context execution | Automatic live shell progress for contextual ordinary/retrieval/debate work; optional synchronous `context_event_sink` on all `StoryRuntime` and `RuntimeSession` provider-bound entry forms. [States, output, privacy, and recovery](guides/agent-context-progress.md) |
| Inspect or operate optional local conversation RAG | Provider-free `story rag status`; explicit `enable`, `rebuild`, cited `search`, `disable`, and derived-only `purge`; shell `/settings` provider/automatic policy; private one-turn `/rag QUERY`; or supported direct Python modules. Local use requires the exact-pinned `rag` extra before opt-in. [Commands, interactive use, and boundary](guides/conversation-rag.md) |
| Persist and send one explicitly addressed one-step turn | Existing `StoryRuntime.converse()` Python API |
| Run consensus with included, fresh, or draft-file input | Focused active shell conversation; noninteractive CLI and Python API remain available |
| Publish and identify canonical agent-produced writing artifacts | Every unanimous consensus run; exact source paths and separate publication provenance in the writing-artifact reference |
| Require agents to read canonical artifacts | Inline `${path}` and `${path}#Lx-Ly` in provider-bound conversation, retrieval, and consensus prompts; ordered multi-reference batches and per-invocation read enforcement are active. |
| Discover a writing artifact or select an inclusive range | Provider-bound shell prompt spans use `$`, `$${path}`, or `$$`; `$` scans the four supported core paths and every eligible lowercase-`.md` descendant under `manuscript/`, while exact `$$` shows ranged-source choices on the second dollar. Completion/viewer return to the exact draft without submission. The viewer remains package-internal, with no standalone command, public selection API, or automation UI. |
| Cancel active advisory/consensus work or partial local rendering | Installed shell; process-level PTY verified on supported POSIX. [Delivery and platform scope](operations/recovery-and-limitations.md#posix-pty-boundary) |
| Show one focused transcript snapshot and context accounting | Attached shell `/history`; active focus required |
| Locate safe runtime diagnostics or inspect an eligible rejected provider response | Safe exception metadata is in workspace `debug_logs/runtime.jsonl`; completed rejected bodies may be in `debug_logs/provider_error_responses/`. Start with the [inspection and privacy boundary](operations/privacy-and-provider.md#rejected-provider-response-artifacts); CLI hints name only the [runtime debug log](reference/cli.md#runtime-debug-logs). |
| Standalone artifact commands | Not implemented in the shell |

## Start and concepts

- [Project landing page](../README.md)
- [Getting started with bare `story`](getting-started.md)
- [Glossary for quick term lookup](glossary.md)
- [Core concepts and relationships](concepts.md)

## Guides

- [Bounded agent context: canonical scenarios, selection, search/read, references, progress, and limits](guides/agent-context.md)
- [Agent exchanges: standard-install availability, picker-first inspection, preview, nested JSON, colors, privacy, and recovery](guides/agent-exchanges.md)
- [Canonical artifact references](guides/artifact-references.md)
- [Workspaces](guides/workspaces.md)
- [Story sessions and subject conversations](guides/story-sessions.md)
- [Live agent-context progress, states, privacy, and observer recovery](guides/agent-context-progress.md)
- [Glasswater interactive end-to-end journey](guides/glasswater-scenario.md)
- [Provider-free smoke test](guides/smoke-test.md)
- [SQLite conversation storage: readiness, selection, transfer, activation, search, data lifecycle, and recovery](guides/sqlite-conversation-storage.md)
- [Optional conversation RAG setup, commands, runtime boundary, and interactive use](guides/conversation-rag.md)
- [Antigravity setup, routing, and safety](guides/antigravity.md)
- [Consensus runs](guides/consensus-runs.md)
- [Directives](guides/directives.md)
- [Inspection and recovery](guides/inspection-and-recovery.md)
- [Troubleshooting](guides/troubleshooting.md)

## Exact reference

- [CLI](reference/cli.md)
- [Package API and exports](reference/package-api.md)
- [Optional conversation RAG direct-module API](reference/package-api.md#optional-conversation-rag-boundary)
- [Conversation catalog](reference/conversation-catalog.md)
- [Conversation transcripts](reference/conversation-transcripts.md)
- [Structured collaboration history: user impact, exact decisions, readable records, state/context separation, recovery, wire format, and Python examples](reference/collaboration-history.md)
- [Canonical v2 conversation archives and backend transfer](reference/conversation-archives.md)
- [Conversation search, cited transcript pages, and direct-only readable-v2 search/citation/page/document-ID contracts](reference/conversation-search.md)
- [Bounded agent-directed conversation retrieval](reference/conversation-retrieval.md)
- [Interactive command parser](reference/interactive-command-parser.md)
- [Interactive terminal component](reference/interactive-terminal.md)
- [Backend and sessions](reference/backend-and-sessions.md)
- [Agent contracts](reference/agent-contracts.md)
- [Runtime API](reference/runtime-api.md)
- [Workspace API](reference/workspace-api.md)
- [Canonical writing artifacts](reference/writing-artifacts.md)
- Configuration: [agents](reference/configuration/agent-configuration.md),
  [runtime overrides](reference/configuration/runtime-overrides.md),
  [session storage](reference/configuration/session-storage.md), and
  [identifiers and errors](reference/configuration/identifiers-and-errors.md)

## System design

- [Overview](design/overview.md)
- [Agent-context ownership, wire v5, checkpoints, candidate deltas, and recovery](design/agent-context.md)
- [Interactive CLI design system contract](design/interactive-cli-design-system.md)
- [Canonical Python module inventory (97 production units)](../PYTHON_MODULE_INDEX.md)
- [Prompts, provider boundary, and exact context accounting](design/prompts-and-provider.md)
- [Session lifecycle](design/session-lifecycle.md)
- [Consensus workflow](design/consensus-workflow.md)
- [Persistence](design/persistence.md)
- [Pinned JSONL viewer design system and hosted scenarios](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/1d4f5f86ccb4ebd2f458f046df7a0b99b9fbeaec/docs/design-system.md)
- [Nine pinned deterministic JSONL viewer sample frames](https://github.com/Kims-DeveloperGroup/jsonl-viewer/tree/1d4f5f86ccb4ebd2f458f046df7a0b99b9fbeaec/docs/samples)

## Operations

- [Filesystem safety](operations/filesystem-safety.md)
- [Privacy, provider exposure, runtime debug logs, and rejected-response
  inspection](operations/privacy-and-provider.md)
- [Terminal display safety](operations/terminal-safety.md)
- [Recovery and limitations](operations/recovery-and-limitations.md)
- [Provider authentication recovery](operations/recovery-and-limitations.md#authentication-recovery)

## Audits

- [Final issue #380 agent-context conformance matrix (188 criteria)](audits/issue-380-agent-context-conformance.md)
