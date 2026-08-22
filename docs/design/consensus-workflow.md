# Consensus workflow

> **Availability:** `StoryRuntime` implements prompt and supplied-draft debates,
> optional focused-conversation context, trusted progress, terminal results,
> and approved artifact commits.

Key terms: **reservation**—the immutable claim on one debate ID; **snapshot**—
one bounded conversation window; **revision round**—one complete replacement;
**terminal durability**—result and lifecycle state saved before terminal
progress; **observer**—the optional synchronous progress sink.

```mermaid
flowchart TD
    P[Validate input, observer, session, and options] --> C{Context mode}
    C -->|included| R[Read and canonicalize focused context once]
    C -->|fresh or omitted| V[Reserve debate ID]
    R --> V
    V --> S[Save requested sidecar and initiation log]
    S --> ST[STARTED]
    ST --> D[Execution specialist authors r0]
    D --> DR[DRAFTED]
    DR --> AS[AUDIT_STARTED]
    AS --> AC[AUDIT_COMPLETED]
    AC -->|conflicts and rounds remain| Y[Proposals and lead synthesis]
    Y --> SY[SYNTHESIZED]
    SY --> AS
    AC -->|conflicts at limit| TD[Save deadlock result and terminal state]
    TD --> DE[DEADLOCK]
    AC -->|all approve| F[Lead finalizes and artifacts commit]
    F --> TC[Save consensus result and terminal state]
    TC --> AR[ARTIFACTS_COMMITTED]
    AR --> CO[CONSENSUS]
    ST -. handled failure .-> TF[Save failed result and terminal state]
    DR -. handled failure .-> TF
    AS -. handled failure .-> TF
    AC -. handled failure .-> TF
    Y -. handled failure .-> TF
    F -. handled failure .-> TF
    TF --> FA[FAILED]
```

## Before provider work

The runtime validates the backend, nonempty input, required
`PART/PATH.md` output, IDs, round limit, optional context request, and observer,
then prepares the story session. Output validation happens before session
access, debate reservation, or provider work. Included mode
performs exactly one synchronous context read and canonical validation before
reservation. Fresh and context-omitted modes perform no context read. Any failure here
emits nothing and leaves the debate ID unreserved.

Reservation exclusively creates lifecycle state. A requested included or fresh
sidecar is then created before the initiation blackboard entry, provider work,
or `STARTED`. Sidecar failure is raised and best-effort makes the reserved state
terminal `failed`; it produces no result or progress.

## Trusted progress sequence

`STARTED` follows the durable initiation entry and is the only event carrying
context mode and counts. Both prompt and supplied-draft input ask the execution
specialist to author `r0`; for a supplied draft it preserves sound material and
applies requested changes before any audit. `DRAFTED` follows the durable
agent-authored candidate.

Each revision emits `AUDIT_STARTED`, then the three specialists audit the same
candidate and the same immutable context snapshot in parallel. Successful
audit persistence precedes `AUDIT_COMPLETED`, whose typed agent list reports
conflicts in roster order. Peer prose never enters progress.

If conflicts remain and another replacement is allowed, only conflicting
specialists propose repairs. The lead synthesizes a complete next revision;
after it is durable the runtime emits `SYNTHESIZED` and starts the next audit.
There is no separate proposal event. Older approvals never count.

At the round limit, conflicts produce a durable deadlock result and state before
`DEADLOCK`. Unanimous approval allows lead finalization and artifact commit;
finalization may return complete updates for only the four core documents. The
artifact repository journal-publishes one create-only part manuscript and any
core updates, then makes the provenance catalog visible last. The consensus
result and state are then durable before
`ARTIFACTS_COMMITTED`, followed by `CONSENSUS`. A handled provider, protocol,
context-propagation, workspace, finalization, or artifact failure similarly
saves its result and state before a typed `FAILED` event.

## Observer and cancellation boundaries

Declared coroutine or async-generator observers fail validation before
mutation. Every failure raised synchronously by an accepted observer, plus an
awaitable or non-`None` result, becomes `ProgressObserverError` through
`__cause__`.
Before terminal durability this best-effort marks the debate `interrupted` and
raises. After terminal durability it raises without rewriting the saved result,
state, or artifacts.

Genuine external cancellation remains `asyncio.CancelledError`. The runtime
joins its execution task, best-effort persists `interrupted`, and emits
`INTERRUPTED` only after that transition is confirmed. If that observer call
fails, cancellation remains primary and the observer error is its cause.

The interactive controller adds proof boundaries around that runtime contract.
Ctrl-C before coroutine entry, with the shared Runner quiescent, is provider-free:
no reservation, run row, or `last` update occurs. Once entered, friendly recovery
requires a joined cancellation and trusted terminal `INTERRUPTED`; that event may
be first after reservation and proves only the durable terminal state. Only
`STARTED` updates `/inspect last`. An entered cancellation without trusted proof
is fatal `operation_interrupted`; known attachment state is abandoned before
ordered cleanup. An interrupted debate cannot resume; `/run` creates a new ID.
Exact shell records are in the [terminal reference](../reference/interactive-terminal.md#exact-cancellation-records).

Exact types are in [Runtime API](../reference/runtime-api.md); artifact and
sidecar storage are in [Persistence](persistence.md), with the exact artifact
contract in [Canonical writing artifacts](../reference/writing-artifacts.md);
operator response is in
[Recovery and limitations](../operations/recovery-and-limitations.md).
