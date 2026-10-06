# Documentation

The runtime has one active schema-v2 collaboration-history path. Management,
structured history/search/transcript reads, provider-free smoke, and the Python
and interactive lead/confirmed-specialist collaboration APIs are available.
Interactive artifact Apply and local Commit are available only through their
own exact structured confirmations.
The strict Git-backed workspace boundary also provides exact session Git
resources and durable content-free commit/checkpoint operation evidence as
package-internal infrastructure. An internal service can create an exact
confirmed local story commit, and a separate internal service can checkpoint
only allowlisted session runtime state. They are not user commands or lead
actions. Ordinary focused shell input is the Lead front channel; there is no
`story run`, `/run`, or raw specialist command. Start with the page
that matches your task.

## Start here

- [Project status and quick start](../README.md)
- [Getting started](getting-started.md)
- [Story sessions and conversations](guides/story-sessions.md)
- [Collaborate with the Lead Agent](guides/lead-agent-collaboration.md)
- [Authoring call limits and measured support](guides/lead-authoring-call-support.md)
- [Lead story workflows and reviewed artifact proposals](guides/lead-agent.md)
- [Glossary](glossary.md)
- [Core concepts](concepts.md)

## What is available

| Task | Canonical page |
| --- | --- |
| Initialize, validate, and understand a strict v2 workspace | [Workspaces](guides/workspaces.md) |
| Recover an interrupted or rejected initialization | [Workspace recovery](operations/recovery-and-limitations.md#workspace-initialization-recovery) |
| Understand exact session branches, worktrees, refs, and state | [Session Git resources](guides/workspaces.md#session-git-resources) |
| Recover interrupted or inconsistent session Git resources | [Session Git-resource recovery](operations/recovery-and-limitations.md#session-git-resource-recovery) |
| Understand exact confirmed story-commit scenarios and decisions | [Exact confirmed story commits](guides/workspaces.md#exact-confirmed-story-commits) |
| Understand internal-ref checkpoint paths, owners, and outcomes | [Exact session runtime checkpoints](guides/workspaces.md#exact-session-runtime-checkpoints) |
| Understand commit/checkpoint evidence and interruption states | [Durable session-history infrastructure](guides/workspaces.md#durable-session-history-infrastructure) |
| Recover an interrupted internal story commit or checkpoint | [Session-history recovery](operations/recovery-and-limitations.md#session-history-recovery) |
| Manage sessions and conversations | [Story sessions](guides/story-sessions.md) |
| Converse with the interactive Lead, attach references, consult specialists, and separately decide review, Apply, and Commit | [Collaborate with the Lead Agent](guides/lead-agent-collaboration.md) |
| Run disposable smoke or read-only workspace verification | [Provider-free smoke](guides/smoke-test.md) |
| Apply session directives | [Directives](guides/directives.md) |
| Inspect and recover local state | [Inspection and recovery](guides/inspection-and-recovery.md) |
| Configure or query optional local RAG | [Conversation RAG](guides/conversation-rag.md) |
| Select or transfer conversation storage | [SQLite conversation storage](guides/sqlite-conversation-storage.md) |
| Inspect recorded provider exchanges | [Agent exchanges](guides/agent-exchanges.md) |
| Understand artifact-reference syntax and current availability | [Artifact references](guides/artifact-references.md) |
| Understand automatic initialization Git and package-internal Git infrastructure | [Safe Git adapter](reference/git-adapter.md) |

## Exact reference

- [CLI](reference/cli.md)
- [Interactive command parser](reference/interactive-command-parser.md)
- [Interactive terminal](reference/interactive-terminal.md)
- [Runtime and lead-orchestration API](reference/runtime-api.md)
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
- [Editorial evaluation cases and comparison protocol](evaluations/lead-editorial-coordination.md)
- [Production-module inventory](../PYTHON_MODULE_INDEX.md)

Long design and audit documents can describe earlier execution surfaces as
historical evidence or future target behavior. Their availability notices name
that status. For current callable behavior, use the README, CLI reference,
runtime API, parser reference, and story-session guide.
