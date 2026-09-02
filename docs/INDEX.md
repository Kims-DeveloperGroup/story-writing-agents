# Documentation

The runtime has one strict Git-backed schema-v2 workspace boundary and one
active schema-v2 storage/inspection path. Initialization, management,
structured history/search/transcript reads, and provider-free smoke are
available. Exact session Git-resource provisioning exists as package-internal
infrastructure for #455 but is not yet a user command; public story execution
is not available. Start with the page that matches your task.

## Start here

- [Project status and quick start](../README.md)
- [Getting started](getting-started.md)
- [Story sessions and conversations](guides/story-sessions.md)
- [Lead agent and collaboration status](guides/lead-agent.md)
- [Glossary](glossary.md)
- [Core concepts](concepts.md)

## What is available

| Task | Canonical page |
| --- | --- |
| Initialize, validate, and understand a strict v2 workspace | [Workspaces](guides/workspaces.md) |
| Recover an interrupted or rejected initialization | [Workspace recovery](operations/recovery-and-limitations.md#workspace-initialization-recovery) |
| Understand exact session branches, worktrees, refs, and state | [Session Git resources](guides/workspaces.md#session-git-resources) |
| Recover interrupted or inconsistent session Git resources | [Session Git-resource recovery](operations/recovery-and-limitations.md#session-git-resource-recovery) |
| Manage sessions and conversations | [Story sessions](guides/story-sessions.md) |
| Understand why story instructions are unavailable | [Collaboration transition](guides/consensus-runs.md) |
| Understand the lead's responsibilities and confirmation boundary | [Lead agent](guides/lead-agent.md) |
| Run disposable smoke or read-only workspace verification | [Provider-free smoke](guides/smoke-test.md) |
| Apply session directives | [Directives](guides/directives.md) |
| Inspect and recover local state | [Inspection and recovery](guides/inspection-and-recovery.md) |
| Configure or query optional local RAG | [Conversation RAG](guides/conversation-rag.md) |
| Select or transfer conversation storage | [SQLite conversation storage](guides/sqlite-conversation-storage.md) |
| Inspect recorded provider exchanges | [Agent exchanges](guides/agent-exchanges.md) |
| Understand artifact-reference syntax and current availability | [Artifact references](guides/artifact-references.md) |
| Understand automatic initialization Git or use its closed Python adapter | [Safe Git adapter](reference/git-adapter.md) |

## Exact reference

- [CLI](reference/cli.md)
- [Interactive command parser](reference/interactive-command-parser.md)
- [Interactive terminal](reference/interactive-terminal.md)
- [Runtime management API](reference/runtime-api.md)
- [Workspace API](reference/workspace-api.md)
- [Package API and exports](reference/package-api.md)
- [Schema-v2 collaboration history](reference/collaboration-history.md)
- [Conversation catalog](reference/conversation-catalog.md)
- [Conversation transcripts](reference/conversation-transcripts.md)
- [Conversation search](reference/conversation-search.md)
- [Conversation archives and transfer](reference/conversation-archives.md)
- [Conversation retrieval contracts](reference/conversation-retrieval.md)
- [Backend and sessions](reference/backend-and-sessions.md)
- [Canonical writing artifacts](reference/writing-artifacts.md)
- [Safe Git adapter and story baseline](reference/git-adapter.md)
- Configuration: [agents](reference/configuration/agent-configuration.md),
  [runtime overrides](reference/configuration/runtime-overrides.md),
  [session storage](reference/configuration/session-storage.md), and
  [identifiers and errors](reference/configuration/identifiers-and-errors.md)

## Design and operations

- [Current system design](design/overview.md)
- [Session lifecycle](design/session-lifecycle.md)
- [Agent-context internals](design/agent-context.md)
- [Consensus algorithm record](design/consensus-workflow.md)
- [Interactive CLI design record](design/interactive-cli-design-system.md)
- [Persistence](design/persistence.md)
- [Filesystem safety](operations/filesystem-safety.md)
- [Privacy and provider exposure](operations/privacy-and-provider.md)
- [Terminal safety](operations/terminal-safety.md)
- [Recovery and limitations](operations/recovery-and-limitations.md)
- [Production-module inventory](../PYTHON_MODULE_INDEX.md)

Long design and audit documents can describe earlier execution surfaces as
historical evidence or future target behavior. Their availability notices name
that status. For current callable behavior, use the README, CLI reference,
runtime API, parser reference, and story-session guide.
