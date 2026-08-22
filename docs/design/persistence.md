# Persistence design

> **Availability:** filesystem persistence covers configuration, sessions,
> conversations, debates, optional debate-context sidecars, canonical writing
> artifacts, and content-free explicit-reference authorization evidence. The
> #189 backend can drive that evidence for an internal/Python referenced
> invocation. `StoryRuntime` and shell reference commands do not create or
> expose the operation yet.

Key terms: **blackboard**—public debate JSONL; **sidecar**—context stored apart
from established debate records; **create-only**—never replaces an existing
path; **context-omitted run**—a debate created without a context request.

The workspace uses separate inspectable files rather than a database:

| Channel | Owner |
| --- | --- |
| Debate log, lifecycle, and result | Runtime and normal inspection |
| Debate context sidecar | Runtime; separate context inspection |
| Per-agent history | Its owning agent |
| Provider manifest and access claim | `AgentBackend` |
| Session/conversation defaults | CLI context helpers |
| Conversation metadata and transcript | Catalog and event-store adapters |
| Retrieval actions, observations, progress, sources | Process/terminal only |
| Current conversation and agent focus | Controller memory only |
| Canonical writing artifacts and provenance | `WritingArtifactRepository` |
| Explicit-reference operation and invocation evidence | `WritingArtifactRepository`; content-free create-only records |
| Explicit-reference selected content | Transient repository/backend carriers and one immediate provider payload; no workspace evidence field |

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
A context-omitted run has no sidecar; absence is read as `None`.

Creation occurs after debate reservation and before initiation or provider
work. The file is strict UTF-8 JSON, identity-bound to its path, rejects
missing, extra, or duplicate keys, and is never overwritten. A collision raises
`DebateExistsError`. `Workspace.inspect_debate_context()` and the matching
runtime methods are read-only; existing debate/session inspection, logs, result
JSON, and `DebateResult` remain unchanged.

## Durability boundaries

Explicit-reference authorization has a separate durability boundary under
`writing_artifacts/input_snapshots/`. For each nonempty operation, the
repository publishes a schema-v1 manifest and `requested` event before reading
the catalog or artifact content. Later operation and invocation events are
create-only, canonically encoded records with contiguous sequences and
contiguous invocation ordinals. A delivery event is durable before selected
bytes leave the repository boundary. The same repository lease serializes
these evidence writes with artifact reads and provenance revalidation.

Durable evidence contains schema and operation/invocation identity,
path/range, provenance, hashes, counts, correlation, states, checkpoints, and
fixed outcomes—not selected bytes, source/provider text, prompts, or results.
The private invocation manifest also binds a capability identifier; the
validated history returned to callers does not expose it. Captured and
delivered byte tuples are process-local, closeable values. The capability
object is never serialized or sent to a provider; its identifier remains
repository-private and is absent from provider payloads and validated history.
A backend-created read coordinator advances the existing invocation records:
the exact full provider action precedes `delivered`, a structurally valid
substantive result precedes `result_received`, and final provenance
revalidation precedes `accepted`. Selected text, provider payloads, and provider
results never become evidence fields. A restart can validate history but cannot
reconstruct or resume transient capabilities. An empty reference batch creates
neither evidence nor artifact I/O. These records are not part of the canonical
writing-artifact catalog, completion, or picker namespace.

The blackboard retains input, candidates, audits, proposals, visible errors,
finalization metadata, and commit record. Result JSON is a projection and omits
its duplicated message list. Agent history remains owner-isolated and bounded,
though provider output in visible fields may still be sensitive.

Terminal result creation and lifecycle transition finish before terminal
progress. Approved artifact publication is a separate recoverable transaction:
one immutable schema-v2 journal records the exact catalog-before state,
catalog-after image, commit, and ordered manuscript/core targets. It is not
rewritten as publication advances. The repository publishes and verifies target
bytes, writes immutable commit history, publishes the catalog last, then
exclusively retires the unchanged completed journal to non-authoritative
staging. The next artifact access rolls forward only from exact catalog-before
or verifies an exact catalog-after state; any other catalog position or missing,
replaced, or divergent authority writes a persistent block marker and fails all
later artifact access closed. Retained `.tmp`, `.held`, `.retired`, and
`.completed` staging files are ignored by recovery and catalog listing and may
accumulate.

Immutable commits are also the permanent manuscript-reservation ledger, not
just history for current catalog rows. Each access globally audits exact commit
records against the catalog. Each immutable commit file must equal the
repository's canonical JSON bytes, catalog paths equal the union of every
artifact path in all non-exempt commits, and each entry exactly matches its
referenced commit. Reformatting is divergence, not a migration input.

Commit times are offset-aware instants. For a repeated core path, the catalog
must select the unique newest normalized instant; an equal-newest tie or older
projection fails closed. Generated timestamps are strictly later than every
ledger instant even across wall-clock rollback, and an unrepresentable next
instant fails before mutation. During exact catalog-before recovery, only the
pending journal's already published byte-identical canonical commit is exempt:
it contributes no paths or newest-core candidate, while all older established
authority remains required. Projected and exact catalog-after audit the full
commit set without an exemption. Catalog loss, subsets, malformed/noncanonical
or extra commits, mismatches, and duplicate manuscript reservations fail closed
instead of being rewritten, reconstructed, or migrated.

The permanent repository readiness triple has its own durability boundary:
primary lock, anchor, and `ready` are exact hard-link names for one regular inode
with link count three. Initialization first takes a transient bootstrap `flock`
on the open writing-artifact state directory and flushes its retained ancestry.
It exclusively creates and flushes the primary, flushes the parent, links and
flushes the anchor, verifies that `ready` is absent, then links and flushes
`ready`. It verifies the triple, acquires the primary `flock`, reverifies, and
releases bootstrap before work. Partial or old two-link state, aliases,
divergent or extra links, and any failure prevent protected work and remain
unchanged; no cleanup, reconstruction, or migration is attempted.

That artifact transaction does not combine sidecars, blackboards, defaults,
transcripts, debate lifecycle, results, provider state, and agent state into one
database transaction. Infrastructure interruption can therefore leave a
recoverable artifact journal alongside an incomplete non-artifact operation.
Exact artifact paths, eligibility, recovery positions, and user responses are in
[Canonical writing artifacts](../reference/writing-artifacts.md).

One-step and retrieval turns append exactly one user event before provider work,
then one reply or sanitized runtime error. Retrieval adds no control, source,
sidecar, catalog, default, debate, state, or artifact record; compatible
provider-thread metadata may update. Hard interruption can leave an unmatched
user event, which later context excludes. Consensus snapshots never append the
transcript.

The canonical path tree is in [Session storage](../reference/configuration/session-storage.md).
Accessor signatures are in [Workspace API](../reference/workspace-api.md),
containment guarantees in [Filesystem safety](../operations/filesystem-safety.md),
and consequences in [Recovery](../operations/recovery-and-limitations.md).
