# Documentation

Use this index to find one canonical answer instead of searching a long manual.

> **Availability:** the installed CLI exposes the existing commands plus a bare
> or `chat` TTY session lobby. The lobby manages session attachment only. Chat
> turns, conversation selection/history, transcript, search, and artifact work
> remain follow-up work in Issues #22 and #11. Their lower-level parser,
> terminal, catalog, transcript, search, and one-turn runtime APIs are available.

## Availability matrix

| Capability | Available through |
| --- | --- |
| Initialize a workspace; run or inspect a debate | Installed CLI and Python API |
| Start or end a provider session | Installed CLI and Python API |
| Open the TTY lobby; list, create, resume, switch, or end sessions | Bare `story` or `story chat` |
| Strict create/resume/attach/detach session lifecycle | Python API; used by the installed lobby |
| Parse the interactive command language | Python API; the lobby dispatches only help, sessions, and quit |
| Prompt, confirm, compose, and render safe terminal records | Module-supported Python API; the lobby uses the non-chat subset |
| Create, rename, archive, and list conversation metadata | Python API only; no selection or dispatch |
| Append/read transcripts; derive bounded complete-turn context | Python API only; no dispatcher or viewing command |
| Search one session; cite hits; read bounded transcript pages | Python API only; no dispatcher or command |
| Persist and send one explicitly addressed conversation turn | `StoryRuntime` Python API only; caller supplies the existing conversation ID |
| Chat turns and conversation/history/search/transcript commands | Not implemented |

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
