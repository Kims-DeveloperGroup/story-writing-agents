# Collaboration execution transition

Public prompt/draft consensus execution is intentionally unavailable in this
integration stage.

## Before and now

Before issue #478, the project exposed several independent schema-v1 routes to
an advisory turn or debate. They duplicated orchestration and persistence
decisions. Those public consumers have been removed rather than forwarded.

Now there is no installed CLI command, interactive command, or
`StoryRuntime`/`RuntimeSession` method that starts a story turn or consensus
run. The underlying provider, prompt, context, lifecycle, result, and storage
components remain internal building blocks; their presence does not make a
public run path available.

## User impact

You can prepare sessions, conversations, directives, provider settings,
storage, and canonical artifact files. You can inspect existing state and use
schema-v2 collaboration contracts directly. You cannot ask the installed
runtime to draft, critique, debate, modify a core document, or publish a new
manuscript in this revision.

Attempted shell execution receives:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The attempt has no provider, transcript, RAG, reference, progress, or artifact
side effect.

## Replacement path

Issue #466 delivered pure schema-v2 values, and #474-#477 added
projection/storage/archive/transfer pieces on the integration branch. Issue
#478 removes the old public consumers. Issue #479 activates the sole schema-v2
storage/inspection service and removes the last active-v1 service path. After
parent #467 completes, open issue #452 wires lead-facing
proposal/confirmation/reconfirmation behavior to that store and #453 wires
specialist execution/result/finalization/outcome-unknown behavior to it.

Do not restore or wrap an earlier direct path. During this transition, use
[story-session management](story-sessions.md), the
[collaboration-history contracts](../reference/collaboration-history.md), and
the [lead-agent status guide](lead-agent.md).
