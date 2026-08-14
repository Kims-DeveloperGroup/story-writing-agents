# Architecture

This compatibility hub preserves former architecture fragments. The focused
design pages describe responsibilities and dataflow without repeating API
signatures or storage schemas.

> **Availability:** the consensus runtime, Codex backend, session lifecycle,
> persistence, smoke workflow, and metadata-only catalog are implemented.

<a id="implemented-scope"></a><a id="agent-roster"></a><a id="conversation-catalog-boundary"></a>

- [System design overview](design/overview.md)
- [Core concepts and fixed agent roster](concepts.md)
- [Conversation catalog boundary](reference/conversation-catalog.md)

<a id="backend-and-prompt-boundary"></a>

- [Prompt and provider design](design/prompts-and-provider.md)

<a id="provider-session-lifecycle-and-attachment"></a>

- [Session lifecycle design](design/session-lifecycle.md)

<a id="built-in-smoke-workflow"></a>

- [Provider-free smoke workflow](guides/smoke-test.md)

<a id="debate-workflow"></a><a id="1-reservation-and-initiation"></a><a id="2-parallel-silent-audit"></a><a id="3-proposal-synthesis-and-re-audit"></a><a id="4-deadlock-and-failure"></a><a id="5-finalization-and-artifact-commit"></a>

- [Consensus workflow design](design/consensus-workflow.md)

<a id="lifecycle-and-recovery"></a>

- [Recovery and limitations](operations/recovery-and-limitations.md)

<a id="filesystem-topology"></a>

- [Persistence design](design/persistence.md)
- [Canonical workspace tree and schemas](reference/configuration/session-storage.md)
