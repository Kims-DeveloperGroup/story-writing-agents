# Documentation

Use this index to find one canonical answer instead of searching a long manual.

> **Availability:** bare `story` and `story chat` open the installed TTY shell.
> It manages story sessions and subject conversations, routes single-line
> advisory turns, searches and reads transcripts, and runs selected-conversation
> consensus. `/history` and standalone artifact commands remain separate.

## Availability matrix

| Capability | Available through |
| --- | --- |
| Initialize a workspace; run or inspect a debate | Installed CLI and Python API |
| Start or end a provider session | Installed CLI and Python API |
| Open the TTY shell; list, create, resume, switch, or end sessions | Bare `story` or `story chat` |
| Create, list, select, rename, or archive subject conversations | Installed shell after session attachment; catalog Python API |
| List or target agents; send ordinary, `@AGENT`, or `/say` turns | Installed shell; a selected active conversation is required for turns |
| Strict create/resume/attach/detach session lifecycle | Python API; used by the installed shell |
| Parse the interactive command language and route metadata | Python API; the shell dispatches a documented subset |
| Compose a prompt and render safe terminal records | Bare shell `/run`; module-supported terminal Python API |
| Append/read transcripts; derive bounded complete-turn context | Shell turns append/consume context and `/transcript` reads fresh bounded pages; Python API |
| Search one session and cite hits | Attached shell `/search` for active records; Python API can explicitly include archived records |
| Persist and send one explicitly addressed conversation turn | Installed shell for its selected conversation; `StoryRuntime` for an explicit ID |
| Run consensus with included, fresh, or draft-file input | Selected active shell conversation; noninteractive CLI and Python API remain available |
| History and standalone artifact commands | Not implemented in the shell |

## Start and concepts

- [Project landing page](../README.md)
- [Getting started](getting-started.md)
- [Glossary for quick term lookup](glossary.md)
- [Core concepts and relationships](concepts.md)

## Guides

- [Workspaces](guides/workspaces.md)
- [Story sessions and subject conversations](guides/story-sessions.md)
- [Provider-free smoke test](guides/smoke-test.md)
- [Consensus runs](guides/consensus-runs.md)
- [Directives](guides/directives.md)
- [Inspection and recovery](guides/inspection-and-recovery.md)
- [Glasswater end-to-end scenario](guides/glasswater-scenario.md)
- [Troubleshooting](guides/troubleshooting.md)

## Exact reference

- [CLI](reference/cli.md)
- [Package API and exports](reference/package-api.md)
- [Conversation catalog](reference/conversation-catalog.md)
- [Conversation transcripts](reference/conversation-transcripts.md)
- [Conversation search and cited transcript pages](reference/conversation-search.md)
- [Interactive command parser](reference/interactive-command-parser.md)
- [Interactive terminal component](reference/interactive-terminal.md)
- [Backend and sessions](reference/backend-and-sessions.md)
- [Agent contracts](reference/agent-contracts.md)
- [Runtime API](reference/runtime-api.md)
- [Workspace API](reference/workspace-api.md)
- Configuration: [agents](reference/configuration/agent-configuration.md),
  [runtime overrides](reference/configuration/runtime-overrides.md),
  [session storage](reference/configuration/session-storage.md), and
  [identifiers and errors](reference/configuration/identifiers-and-errors.md)

## System design

- [Overview](design/overview.md)
- [Prompts and provider boundary](design/prompts-and-provider.md)
- [Session lifecycle](design/session-lifecycle.md)
- [Consensus workflow](design/consensus-workflow.md)
- [Persistence](design/persistence.md)

## Operations

- [Filesystem safety](operations/filesystem-safety.md)
- [Privacy and provider exposure](operations/privacy-and-provider.md)
- [Terminal display safety](operations/terminal-safety.md)
- [Recovery and limitations](operations/recovery-and-limitations.md)
