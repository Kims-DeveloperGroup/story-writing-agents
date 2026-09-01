# Collaboration execution transition

Public prompt/draft consensus execution is intentionally unavailable. Python
lead advice and proposal confirmation are available, but stop before execution.

## Before and now

Before issue #478, the project exposed several independent schema-v1 routes to
an advisory turn or debate. They duplicated orchestration and persistence
decisions. Those public consumers have been removed rather than forwarded.

There is no installed CLI/interactive command or runtime method that starts a
consensus run. `lead_turn()` starts only a lead front-channel turn, and
`decide_lead_step()` records only a proposal decision. The underlying provider,
prompt, context, lifecycle, result, and storage
components remain internal building blocks; their presence does not make a
public run path available.

## User impact

You can prepare sessions, conversations, directives, provider settings,
storage, and canonical artifact files. You can inspect existing state and use
schema-v2 collaboration contracts directly. You cannot ask the installed
runtime to debate, modify a core document, or publish a new manuscript. A
Python caller can ask the lead for a critique or proposal.

Attempted shell execution receives:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The attempt has no provider, transcript, RAG, reference, progress, or artifact
side effect.

## Replacement path

Issues #466 and #474-#479 delivered the schema-v2 values, projections,
storage/archive/transfer, and sole active storage/inspection service. The #452
layer owns lead-facing proposal/confirmation/reconfirmation behavior. #453
owns specialist execution/result/finalization/outcome-unknown behavior,
and #454 owns whole-workspace rejection of unsupported history.

Do not restore or wrap an earlier direct path. During this transition, use
[story-session management](story-sessions.md), the
[collaboration-history contracts](../reference/collaboration-history.md), and
the [lead-agent status guide](lead-agent.md).
