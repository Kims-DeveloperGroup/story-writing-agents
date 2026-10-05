# Bounded agent context

Bounded agent context assembles small, authoritative inputs and lets an agent
request additional evidence. Python and interactive Lead turns and confirmed
specialist consultations use the same component.

## Why the component exists

The component avoids resending every workspace document and prior exchange on
every physical provider call. It separates context by purpose:

| Category | Examples | Component behavior |
| --- | --- | --- |
| Required work | Current instruction and complete logical candidate | Always present; omission fails validation. |
| Lead session memory | Retained session history and current directives | Complete fresh-thread replay or missing turn content and new host/specialist material on a verified native thread. |
| Stable identity | Agent role, persona, language, and protocol | Reused only while its fingerprint remains compatible. |
| Working knowledge | Current constraints and focused records | Selected within explicit byte budgets. |
| Additive knowledge | Older conversation, manuscript passage, debate record, or the invoking agent's state | Remains available through bounded lexical search and exact read actions. |
| Mandatory references | `${path}` and `${path}#Lx-Ly` | A Python lead turn must read them independently and in order. |

Optimization never grants arbitrary file access, treats provider memory as
authority, or lets optional search satisfy a mandatory reference.

## Lifecycle and limits

One low-level contextual invocation follows:

```text
PREPARE -> PLAN -> INVOKE/ACTION -> VERIFY -> COMMIT
    |          |             |          |
    +----------+-------------+----------+-> FAIL
```

- `PREPARE` validates scope, authority, observer, and any required references.
- `PLAN` emits a bootstrap or compatible delta and identifies omitted but
  searchable sources.
- `INVOKE/ACTION` permits an answer or bounded `SEARCH` and `READ` actions.
- `VERIFY` checks cited handles, source revisions, and domain output.
- `COMMIT` accepts a content-free checkpoint; failure accepts no checkpoint.

The default source budget is 131,072 bytes: 73,728 required, 16,384 stable,
32,768 working, and no eager optional bytes. A contextual agent can make at
most four nonterminal actions across five provider calls, with at most two
searches and two reads. Mandatory references have a separate limit of 16
references, 128 KiB each, and 256 KiB in aggregate.

Searchable conversation history follows an additional rule: the adapter's default
`CollaborationReadableContext` bootstrap is content-free and contains zero
records. The lead runtime explicitly enables bounded searchable history across
active same-session conversations, with no eager record bodies from this adapter.
SEARCH and READ add only authorized whole records under the fixed
24-record/32,768-byte bound. This search boundary is separate from required
Lead session memory below and does not expose private typed payload fields.

Search returns invocation-bound handles rather than permanent authority. Read
accepts only live handles from the same invocation. Guessed, stale, consumed,
cross-agent, cross-scope, or drifting evidence fails closed. A final answer
must identify the handles it used.

## Lead session memory

Explicit editorial records retain user-selected directions, reasons, rejected alternatives,
protected ambiguity and setup obligations. The runtime projects their exact revision chains
from the same session history used for Lead continuation, including archived conversations.
A bounded cited view accompanies full and native-delta delivery; each included record names
its original user event and current revision. Record approval distinguishes proposed,
selected, rejected and superseded intent. Historical application facts identify the exact
task and candidate used in a matching Apply result.

The view uses the remaining existing UTF-8 allocation after exact chronological delivery.
Whole records, omission counts and provenance gaps remain explicit. Required history and
the existing capacity error are preserved. User intent and historical application facts
do not supply current artifact READ receipts, semantic fulfillment or execution permissions.
See [editorial decisions](lead-agent.md#editorial-decisions) for explicit user intake.

Ordinary Lead turns and consultation/artifact-review synthesis share one compatible
provider thread across conversations in the same workspace and story session.
Compatibility includes the agent, provider, definition, and context policy. Pause/reopen
and process restart preserve compatible continuation; the Lead thread does not rotate
after an arbitrary 16 turns. Specialists and consensus retain their separate invocation
scopes.

Before a direct Lead or Lead-synthesis invocation reads session-derived policy
or context, runtime acquires an opaque physical-generation pin for the exact
session worktree relationship. It retains that pin through the provider await
and durable result publication, rechecks the relationship, and closes the pin.
The pin carries no artifact bytes, path traversal, repository operation, or
semantic READY authority; each source read still uses its own short guarded
READY transaction, and no Git lease spans the provider wait. For each Codex or
Antigravity story call, the same session binding separately mints a call-local
capability that selects the retained session-root descriptor as OS working
directory. Codex also uses native project root `--cd .`; Antigravity uses the
exact CWD as its project root. The continuity pin itself still grants no such
process authority. See the
[Provider session execution boundary](../reference/backend-and-sessions.md#codex-session-execution-boundary).
Neither provider reads story evidence from that placement; runtime-mediated
context and SEARCH/READ supply selected content.

Each initial Lead payload includes required session context (`h`). Fresh threads receive
complete retained active and archived history as exact readable text and necessary
workflow/review facts. A verified compatible thread receives missing turn content and
newly produced host/specialist material, without repeating model-known Lead text or
current input. Storage timestamps, batches and prefix digests stay host-owned. Current
directives accompany both modes, including `workflow_meta` turns. Relevant core content
arrives through host-owned SEARCH/READ or explicit references; current core-status
evidence remains explicit. Optional P3 budgets do not remove required history or state.
See [provider disclosure
details](../operations/privacy-and-provider.md#provider-disclosure).

Archiving a conversation only changes its catalog status: it does not remove
its history from Lead memory or prevent provider disclosure. Memory grants no
current evidence receipt, confirmation, replay, artifact-write, or Git
authority. Explicit references and SEARCH/READ evidence rules still apply.

Exact delivery permits at most 4,096 conversations and an inclusive limit of
262,144 UTF-8 bytes for the canonically serialized contextual payload, including
framing and any reference observation it carries. Excess fails with
`exact session context exceeds the delivery capacity`; runtime never silently
omits or summarizes required history. Known history, current state, input, and
framing are checked before provider work. Generated observations are checked
before the subsequent substantive submission, so their excess does not imply
that no earlier provider call occurred. This runtime byte limit does not
guarantee that the selected model's token window can hold the context.

## Checkpoints and provider input

A successful READY checkpoint records content-free compatibility and cursor
facts: scope, provider/definition/policy fingerprints, source revisions,
record cursors, delta depth, accounting, and exact usage when available. It
does not store prompts, excerpts, artifact bytes, provider responses, hidden
reasoning, or free-form memory.

On a compatible later call, unchanged sources can be represented by a delta or
an exact unchanged-candidate pointer. Missing, corrupt, stale, tainted, or
incompatible state forces a fresh bootstrap or an explicit failure. Provider
input-token totals are reported only when every submitted physical call
reports an exact count; the component does not estimate missing usage.

For the Lead, bootstrap reconstructs exact retained session memory on the next
safe authorized work. An eligible failed read-only Lead attempt permits a fresh
turn in the same conversation after explicit new input. Runtime preserves its
request and failure, and retires only the matching unfinished native operation;
a successful checkpoint from another conversation is retained. Uncertain
specialist/synthesis and authoritative write outcomes remain recovery-gated.
See [continuation recovery](../reference/backend-and-sessions.md#lead-session-continuation).

## Reference enforcement

The artifact-reference parser and repository support content-free indicators
and ordered authorization evidence. A Python lead call must:

1. send only the ordered indicators first;
2. require the matching ordered read actions;
3. validate path, range, scope, and source revision;
4. deliver the captured bytes as untrusted mandatory context; and
5. revalidate receipt before accepting the answer.

Another agent's read, provider history, a checkpoint, or an optional search
cannot satisfy that obligation. Interactive Lead input uses this same
reference enforcement before accepting the substantive answer.

## What users observe

An ordinary focused shell line calls the Lead and can show content-free
context progress:

```text
[context] bootstrap: 2 selected / 1,024 bytes; 1 searchable
[context] answer accepted
```

The interactive controller and Python applications both call the Lead runtime
path. Context progress grants no specialist, artifact, or Git authority. See
[Lead agent](lead-agent.md).

## Python observability

A Python caller can render content-free progress such as:

```text
[context] bootstrap: 2 selected / 1,024 bytes; 1 searchable
[context] search action 1 started
[context] search action 1 completed: 1 hit; truncated: no
[context] answer accepted
[context] checkpoint advanced
```

This is application output, not current shell output. Counts depend on the
authorized workspace and no line exposes queries, excerpts, paths, provider
responses, credentials, or private agent state.

For the direct contracts and package owners, see
[Agent-context design](../design/agent-context.md),
[Agent-context progress](agent-context-progress.md), and
[Privacy and provider data](../operations/privacy-and-provider.md).

## Historical issue-380 section anchors

The issue-380 conformance audit retains its original section links. The
following anchors now resolve to this consolidated component/status guide; its
old runnable shell scenarios are not current instructions.

<a id="c01-context-categories"></a>
<a id="c02-current-agent-binding"></a>
<a id="c03-ordinary-conversation"></a>
<a id="c04-conversation-retrieval"></a>
<a id="c05-debate-context"></a>
<a id="c06-explicit-reference-enforcement"></a>
<a id="c07-measured-amplification"></a>
<a id="c08-stable-binding"></a>
<a id="c09-content-free-manifest"></a>
<a id="c10-priorities-and-planning"></a>
<a id="c11-search-read-answer"></a>
<a id="c12-content-free-checkpoint"></a>
<a id="c13-ordinary-follow-up"></a>
<a id="c14-missing-knowledge-outline"></a>
<a id="c15-multi-agent-debate"></a>
<a id="c16-part-scoped-modification"></a>
<a id="c17-multiple-mandatory-references"></a>
<a id="c18-user-visible-observability"></a>
<a id="c19-state-transition"></a>
<a id="c20-decisions-limits-and-user-impact"></a>
