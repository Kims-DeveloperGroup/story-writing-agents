# Bounded agent context

Bounded agent context assembles small, authoritative inputs and lets an agent
request additional evidence. `StoryRuntime.lead_turn()` uses it for the Python
lead workflow. The public shell, debate commands, reference picker, and
specialist execution do not invoke it in this integration stage.

## Why the component exists

The component avoids resending every workspace document and prior exchange on
every physical provider call. It separates context by purpose:

| Category | Examples | Component behavior |
| --- | --- | --- |
| Required work | Current instruction and complete logical candidate | Always present; omission fails validation. |
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

Focused conversation history follows an additional rule: the adapter's default
`CollaborationReadableContext` bootstrap is content-free and contains zero
records. The lead runtime explicitly enables bounded searchable history across
active same-session conversations, but still sends no eager record bodies.
SEARCH and READ add only authorized whole records under the fixed
24-record/32,768-byte bound. The adapter never loads the full transcript or
exposes private typed payload fields.

Search returns invocation-bound handles rather than permanent authority. Read
accepts only live handles from the same invocation. Guessed, stale, consumed,
cross-agent, cross-scope, or drifting evidence fails closed. A final answer
must identify the handles it used.

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

## Reference enforcement

The artifact-reference parser and repository support content-free indicators
and ordered authorization evidence. A Python lead call must:

1. send only the ordered indicators first;
2. require the matching ordered read actions;
3. validate path, range, scope, and source revision;
4. deliver the captured bytes as untrusted mandatory context; and
5. revalidate receipt before accepting the answer.

Another agent's read, provider history, a checkpoint, or an optional search
cannot satisfy that obligation. In the current shell, the surrounding story
instruction is rejected before parsing or reading references.

## What users observe

An ordinary shell story line still receives:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

That shell path makes no provider call. A Python application can call
`lead_turn(..., context_event_sink=callback)` and receive content-free lifecycle
events while the lead searches/reads authorized context. It may append
conversation/proposal records, but never writes an artifact or runs a
specialist. See [Lead agent](lead-agent.md).

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
