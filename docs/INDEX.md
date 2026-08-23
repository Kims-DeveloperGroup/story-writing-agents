# Documentation

Use this index to find one canonical answer instead of searching a long manual.

> **Availability:** bare `story` and `story chat` open the installed TTY shell.
> It manages story sessions and subject conversations, routes single-line
> retrieval-enabled turns, searches and reads transcripts, and runs
> focused-conversation consensus. It also provides global help and lifecycle
> status, attached directives and safe inspection, focused history, and
> controller-boundary cancellation for active operations and local reads.
> Inline `${path}` and `${path}#Lx-Ly` references are active in provider-bound
> prompts. An independent supplied-text ranged viewer exists only as an
> unlisted internal terminal component; `$`/`$$` discovery and selection do not
> invoke it yet.

## Availability matrix

| Capability | Available through |
| --- | --- |
| Initialize a workspace; run or inspect a debate | Installed CLI and Python API |
| Create, reopen, or pause a provider session | Installed CLI and Python API |
| Open the TTY shell; list, create, resume, switch, or pause sessions | Bare `story` or `story chat` |
| Show registry help or shell status | Global in the installed shell |
| Apply directives; safely inspect sessions or debates | Current attachment; no conversation focus required |
| Create, list, resume, rename, or archive subject conversations | Installed shell after session attachment; catalog Python API |
| List or target agents; send ordinary, `@AGENT`, or `/say` turns | Installed shell; an active conversation must have current focus |
| Read durable/access facts and perform exact session transitions | Python API; used by the installed shell and runtime |
| Parse the interactive command language and route metadata | Python API; the shell dispatches a documented subset |
| Compose a prompt and render safe terminal records | Shell `/run --output PART/PATH.md`; module-supported terminal Python API |
| Append/read transcripts; derive bounded complete-turn context | Shell turns append/consume context and `/transcript` reads fresh bounded pages; Python API |
| Search one session and cite hits | Attached shell `/search` for active records; Python API can explicitly include archived records |
| Ask an agent to retrieve cited conversation history | Focused shell conversation; additive Python runtime API; active records in the current session only |
| Persist and send one explicitly addressed one-step turn | Existing `StoryRuntime.converse()` Python API |
| Run consensus with included, fresh, or draft-file input | Focused active shell conversation; noninteractive CLI and Python API remain available |
| Publish and identify canonical agent-produced writing artifacts | Every unanimous consensus run; exact paths and eligibility in the writing-artifact reference |
| Require agents to read canonical artifacts | Inline `${path}` and `${path}#Lx-Ly` in provider-bound conversation, retrieval, and consensus prompts; ordered multi-reference batches and per-invocation read enforcement are active. |
| Select an inclusive range in supplied text | Package-internal terminal component only; no supported shell command or public API. `$`, `$${file}`, `$$`, artifact discovery, viewer-driven reference construction, and composer restoration remain unavailable pending #164. |
| Cancel active advisory/consensus work or partial local rendering | Installed shell; process-level PTY verified on supported POSIX. [Delivery and platform scope](operations/recovery-and-limitations.md#posix-pty-boundary) |
| Show one focused transcript snapshot and context accounting | Attached shell `/history`; active focus required |
| Standalone artifact commands | Not implemented in the shell |

## Start and concepts

- [Project landing page](../README.md)
- [Getting started with bare `story`](getting-started.md)
- [Glossary for quick term lookup](glossary.md)
- [Core concepts and relationships](concepts.md)

## Guides

- [Canonical artifact references](guides/artifact-references.md)
- [Workspaces](guides/workspaces.md)
- [Story sessions and subject conversations](guides/story-sessions.md)
- [Glasswater interactive end-to-end journey](guides/glasswater-scenario.md)
- [Provider-free smoke test](guides/smoke-test.md)
- [Consensus runs](guides/consensus-runs.md)
- [Directives](guides/directives.md)
- [Inspection and recovery](guides/inspection-and-recovery.md)
- [Troubleshooting](guides/troubleshooting.md)

## Exact reference

- [CLI](reference/cli.md)
- [Package API and exports](reference/package-api.md)
- [Conversation catalog](reference/conversation-catalog.md)
- [Conversation transcripts](reference/conversation-transcripts.md)
- [Conversation search and cited transcript pages](reference/conversation-search.md)
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
- [Interactive CLI design system contract](design/interactive-cli-design-system.md)
- [Prompts and provider boundary](design/prompts-and-provider.md)
- [Session lifecycle](design/session-lifecycle.md)
- [Consensus workflow](design/consensus-workflow.md)
- [Persistence](design/persistence.md)

## Operations

- [Filesystem safety](operations/filesystem-safety.md)
- [Privacy and provider exposure](operations/privacy-and-provider.md)
- [Terminal display safety](operations/terminal-safety.md)
- [Recovery and limitations](operations/recovery-and-limitations.md)
