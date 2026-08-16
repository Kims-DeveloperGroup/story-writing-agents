# Persistence design

> **Availability:** filesystem persistence covers configuration, sessions,
> conversations, debates, optional debate-context sidecars, and manuscripts.

Key terms: **blackboard**—public debate JSONL; **sidecar**—context stored apart
from established debate records; **create-only**—never replaces an existing
path; **legacy run**—a debate created without a context request.

The workspace uses separate inspectable files rather than a database:

| Channel | Owner |
| --- | --- |
| Debate log, lifecycle, and result | Runtime and normal inspection |
| Debate context sidecar | Runtime; separate context inspection |
| Per-agent history | Its owning agent |
| Provider manifest and lease | `AgentBackend` |
| Session/conversation selections | CLI context helpers |
| Conversation metadata and transcript | Catalog and event-store adapters |
| Retrieval actions, observations, progress, sources | Process/terminal only |
| Sticky interactive target | Controller memory only |
| Approved manuscript | Workspace artifact commit |

<a id="debate-context-sidecar"></a>

## Debate-context sidecar

The exact lazy path is
`discussion_logs/<session_id>/<debate_id>.context.json`. Its version-1 included
shape is:

```json
{
  "schema_version": 1,
  "session_id": "novel",
  "debate_id": "opening_01",
  "conversation_id": "c_opening",
  "mode": "included",
  "snapshot": {
    "events": [{
      "schema_version": 1,
      "session_id": "novel",
      "conversation_id": "c_opening",
      "turn_id": "t_01",
      "sequence": 1,
      "timestamp": "2026-01-02T00:00:00+00:00",
      "sender": "user",
      "recipient": "plot_architect",
      "kind": "user_message",
      "content": "Question"
    }, {
      "schema_version": 1,
      "session_id": "novel",
      "conversation_id": "c_opening",
      "turn_id": "t_01",
      "sequence": 2,
      "timestamp": "2026-01-02T00:00:01+00:00",
      "sender": "plot_architect",
      "recipient": "user",
      "kind": "agent_reply",
      "content": "Answer"
    }],
    "total_complete_turns": 1,
    "retained_turns": 1,
    "omitted_turns": 0,
    "retained_utf8_bytes": 14,
    "truncated": false
  }
}
```

The paired events are one complete turn. Top-level objects have exactly the six
shown keys, event objects exactly the ten shown keys, and snapshot objects
exactly the six shown keys. Snapshot metadata must match ordered,
identity-bound events and fixed context caps.

Included mode always stores an exact snapshot, including the empty form
`events: []` with zero counts and `truncated: false`. Fresh mode keeps the same
six top-level fields, sets `mode` to `fresh`, and sets `snapshot` to `null`.
A legacy run has no sidecar; absence is read as `None`.

Creation occurs after debate reservation and before initiation or provider
work. The file is strict UTF-8 JSON, identity-bound to its path, rejects
missing, extra, or duplicate keys, and is never overwritten. A collision raises
`DebateExistsError`. `Workspace.inspect_debate_context()` and the matching
runtime methods are read-only; existing debate/session inspection, logs, result
JSON, and `DebateResult` remain unchanged.

## Durability boundaries

The blackboard retains input, candidates, audits, proposals, visible errors,
finalization metadata, and commit record. Result JSON is a projection and omits
its duplicated message list. Agent history remains owner-isolated and bounded,
though provider output in visible fields may still be sensitive.

Terminal result creation and lifecycle transition finish before terminal
progress. Approved artifact commit validates the whole set, replaces core files
in fixed order, then exclusively creates the manuscript; cross-file rollback is
best effort. No sidecar, log, selection, transcript, result, state, and artifact
set forms one database transaction.

One-step and retrieval turns append exactly one user event before provider work,
then one reply or sanitized runtime error. Retrieval adds no control, source,
sidecar, catalog, selection, debate, state, or artifact record; compatible
provider-thread metadata may update. Hard interruption can leave an unmatched
user event, which later context excludes. Consensus snapshots never append the
transcript.

The canonical path tree is in [Session storage](../reference/configuration/session-storage.md).
Accessor signatures are in [Workspace API](../reference/workspace-api.md),
containment guarantees in [Filesystem safety](../operations/filesystem-safety.md),
and consequences in [Recovery](../operations/recovery-and-limitations.md).
