# Documentation

Use this index to find one canonical answer instead of searching a long manual.

> **Availability:** the installed CLI currently exposes `init`, `directive`,
> `run`, `inspect`, `session start|end`, and `smoke`. The interactive command
> parser, conversation catalog, transcript event store, and one-turn bound-agent
> provider call are implemented Python APIs. Automatic interactive dispatch,
> current-conversation selection, search, `/conversation`, `/search`, and
> `/transcript` are not implemented.

## Availability matrix

| Capability | Available through |
| --- | --- |
| Initialize a workspace; run or inspect a debate | Installed CLI and Python API |
| Start or end a provider session | Installed CLI and Python API |
| Strict create/resume/attach/detach session lifecycle | Python API only |
| Parse the future interactive command language | Python API only; no dispatcher or TTY |
| Create, rename, archive, and list conversation metadata | Python API only; no selection or dispatch |
| Append/read transcripts; derive bounded complete-turn context | Python API only; no dispatcher or viewing command |
| Send one validated conversation turn to a bound agent | Python API only; caller supplies context and persistence |
| Interactive chat, transcript command, and search | Not implemented |

## Start and concepts

- [Project landing page](../README.md)
- [Getting started](getting-started.md)
- [Glossary for quick term lookup](glossary.md)
- [Core concepts and relationships](concepts.md)

## Guides

- [Workspaces](guides/workspaces.md)
- [Story sessions](guides/story-sessions.md)
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
- [Interactive command parser](reference/interactive-command-parser.md)
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
- [Recovery and limitations](operations/recovery-and-limitations.md)
