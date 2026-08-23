# Use canonical artifact references

> **Availability:** inline `${path}` and `${path}#Lx-Ly` references are active
> in provider-bound conversation, retrieval, and consensus input. The `$`,
> `$${file}`, and `$$` interactive discovery/viewer controls are not connected
> yet; type complete indicators directly.

An artifact reference tells every addressed story agent which existing output
it must read before its reply can be accepted. It carries a canonical path and
optional inclusive line range, not a copy of the file. The runtime captures the
selected bytes once, requires a fresh exact read action from each provider
invocation, and revalidates the source before accepting the operation.

## User impact

You can now ground an advisory request or consensus modification in one or more
ordered canonical artifacts without pasting their contents. Each addressed
agent and debate phase must independently request the full batch; one call
cannot silently ignore a reference or borrow another call's evidence. This
guarantees the read sequence and current source identity, not the quality of an
agent's interpretation.

You still cannot type `$` to browse artifacts, open `$${file}` to select lines,
or use `$$` to pick and attach a file. Inline indicators must be typed exactly,
and the runtime exposes detailed evidence through Python inspection rather than
a reference-specific shell history view.

## Write a reference

```text
${core_documents/global_outline.md}
${manuscript/part-01/scenes/opening.md}#L40-L65
```

Use uppercase `L`, positive ordered line numbers, and one of these canonical
paths:

- `core_documents/global_outline.md`
- `core_documents/part_outlines.md`
- `core_documents/character_sheets.md`
- `core_documents/narrative_rules.md`
- `manuscript/PART/PATH.md`

One input may contain up to 16 references. Their order is significant. Each
selection is limited to 128 KiB of UTF-8 and the combined batch to 256 KiB.
Exact duplicates, overlapping ranges, and mixing a whole-file reference with a
ranged reference to the same path are rejected. Internal transcripts,
discussion logs, agent/provider state, and configuration are not canonical
writing artifacts and cannot be referenced.

To send the literal text `${...}` without creating a reference, escape the
dollar with one backslash: `\${...}`. The exact typed input remains in durable
conversation/debate history; the provider projection removes the escaping
backslash. `$$` is reserved for the later picker and is rejected today.

## Where references work

References are parsed once at the top of each operation in:

- ordinary, one-shot-routed (`@plot ...`), and sticky-routed (`/to character`,
  then ordinary input) advisory conversation turns, including `/say`;
- retrieval-enabled advisory turns;
- interactive `/run`, including `--fresh`, and noninteractive `story run`;
- automation using those runtime entry points; and
- `StoryRuntime` and `RuntimeSession` conversation, retrieval, `run_prompt()`,
  and `run_draft()` methods.

The existing shell grammar does not gain a separate reference command. Put
indicators inside the prompt text, for example:

```text
@plot Compare ${core_documents/global_outline.md} with ${manuscript/part-01/scenes/opening.md}#L40-L65 and propose the smallest outline correction.
```

`/say Review ${core_documents/character_sheets.md}` sends the same kind of
referenced turn to the current sticky agent focus. A fresh consensus prompt is
also direct text:

```text
/run --fresh --output part-02/revisions/fresh-review.md Review ${core_documents/part_outlines.md} before drafting the replacement.
```

```text
/run --output part-02/revisions/continuity-review.md Update the candidate using ${core_documents/character_sheets.md}, ${core_documents/narrative_rules.md}#L8-L20, and ${manuscript/part-02/scenes/arrival.md}#L12-L44.
```

For noninteractive use, quote the prompt so the shell does not interpret `$`:

```bash
story run --prompt 'Revise the part-scoped manuscript using ${core_documents/part_outlines.md} and ${manuscript/part-02/scenes/arrival.md}#L12-L44.' \
  --debate-id part_02_reference_revision \
  --output part-02/revisions/arrival-r2.md
```

## Modification scenarios

These examples describe provider-scripted collaboration; the runtime does not
write core documents itself. Advisory agents propose changes but do not
publish. In a consensus run, the execution specialist authors the initial
candidate, specialists audit it, and the lead authors approved replacements.
Only unanimous finalization can publish a new manuscript and complete core
document replacements.

| User goal | Example input | What is enforced |
| --- | --- | --- |
| Adjust the global outline | `@plot Propose an outline correction after reading ${core_documents/global_outline.md} and ${manuscript/part-01/scenes/opening.md}#L40-L65.` | The plot agent must request both indicators in that order before its advisory reply is accepted. |
| Repair a character sheet | `@character Check ${core_documents/character_sheets.md} against ${manuscript/part-01/scenes/opening.md}#L12-L24 and propose the full correction.` | The character agent independently reads both sources; an advisory reply remains a proposal. |
| Apply narrative rules | `@execution Review ${core_documents/narrative_rules.md}#L8-L20 and ${manuscript/part-01/scenes/opening.md}#L66-L90 before recommending prose changes.` | The execution agent receives only those selected ranges as transient untrusted context. |
| Publish a manuscript revision | `/run --output part-01/revisions/opening-r2.md Revise ${manuscript/part-01/scenes/opening.md} using ${core_documents/global_outline.md}, ${core_documents/character_sheets.md}, and ${core_documents/narrative_rules.md}.` | Every participating role and debate phase gets a fresh read obligation; prior evidence cannot be borrowed. The approved result is written only to the new part-scoped path. |

## What happens during one operation

There are two related lifecycles:

| Scope and state | Internal meaning | What you can conclude externally |
| --- | --- | --- |
| Operation `requested` | A content-free manifest exists before artifact reads. | The attempt was recorded; no provider reply is accepted yet. |
| Operation `captured` | Every ordered selection was eligible and captured from current provenance. | The requested batch existed and passed capture checks. |
| Final revalidation | The runtime checks current provenance and hashes immediately before completion. Explicit repository checkpoints may be recorded as `revalidated`; routine history can proceed directly from `captured` to `accepted`. | Files did not drift during the accepted operation. |
| Operation `accepted` | All invocations are terminal and final revalidation passed. | The complete operation succeeded. |
| Operation `terminated` | Parsing, authorization, drift, cancellation, provider, protocol, or workflow failure ended it. | No referenced result from that operation was accepted. |
| Invocation `authorized` | One fresh ordinal, correlation, and private one-use capability were created. | This agent/phase/round has its own obligation. |
| Invocation `delivered` | The provider echoed the exact ordered batch and the capability delivered selected text. | Exact delivery occurred; this is not proof of comprehension. |
| Invocation `result_received` | A structurally valid substantive result arrived. | A result exists but is not accepted yet. |
| Invocation `accepted` | The invocation passed final source revalidation. | That one reply/phase may return to the operation. |
| Invocation `terminated` | The invocation failed or was cancelled. | Its evidence cannot be reused for a retry or another agent. |

For each logical invocation the provider first receives a content-free prelude
and must return one exact `read_writing_artifacts` action. Only then does the
immediately following substantive call receive the selected text under
`untrusted_writing_artifact_context`. The private capability is never
serialized. All four roles use this same structural backend path; role prompt
wording is not the enforcement mechanism.

| Operation | Without references | With references |
| --- | ---: | ---: |
| Direct conversation | 1 provider call | 2: prelude + reply |
| One agent in one debate phase | 1 provider call | 2: prelude + phase result |
| Retrieval turn | 1–5 provider calls | 2–6: prelude + unchanged 1–5 action loop |

The physical sixth retrieval call can only be the separate prelude plus five
retrieval calls; there is no sixth retrieval action. A conflict/revision debate
may require many independent reads. For example, the covered one-revision flow
uses 11: draft, three round-0 audits, two revision proposals, lead synthesis,
three round-1 audits, and lead finalization.

## Inspect evidence and recover from failure

Successful referenced results contain an optional `reference_operation_id`.
It is `None` (and omitted from serialized debate results) when the input has no
references. Python callers can inspect validated, content-free history:

```python
result = await session.converse(conversation_id, "plot_architect", prompt)
operation_id = result.reference_operation_id
if operation_id is not None:
    history = session.inspect_reference_operation(operation_id)
```

`StoryRuntime.inspect_reference_operation(operation_id)` provides the
unbound equivalent. A `RuntimeSession` rejects an ID belonging to another
session. History contains paths/ranges, hashes, byte counts, correlations,
states, and outcomes—not selected text, user/provider prompts, replies, or
diagnostics. A handled `ReferenceOperationFailure` carries the operation ID and
a content-free failure code for inspection; a handled terminal debate failure
may instead retain the ID on its failed `DebateResult`.

Failures are atomic at the operation boundary: malformed or missing references
fail before provider work; later drift, read-action, provider, or acceptance
failure produces no accepted reply or partial artifact publication. There is
no automatic retry and a terminated operation cannot resume. Correct the cause
and start a new operation, which receives a new ID, capture, ordinal sequence,
coordinator, and capability.

The shell deliberately uses its existing content-free categories: malformed
syntax is `command_invalid`; provider/read-action failures are
`provider_protocol_error`; cancellation is `operation_cancelled`; and unsafe,
ineligible, changed, or corrupt artifact state is `workspace_invalid`. It does
not display paths, selected text, the operation ID, or internal exception prose.
Use the Python inspection API when an integration needs the durable operation
history.

Selected content is local and transient until the substantive provider call.
It is excluded from task/user/candidate fields, conversation and retrieval
history, durable evidence, and diagnostics. Local release is not secure-memory
erasure, and a provider or remote thread may retain content after receipt under
its own policy. Acceptance proves the read/delivery/result sequence, not that a
model understood or obeyed the material.

## Interactive boundary still pending

Issue #164 still owns typing `$` to list canonical artifacts, `$${file}` to
open the independent ranged viewer, `$$` to pick a file, restoring the exact
composer buffer/cursor, attaching the selected path/range, and
reference-specific shell diagnostics. The supplied-text viewer exists and
closes cleanly after selection or cancellation, but it does not discover files
or construct references. Until that integration lands, type complete inline
indicators and inspect evidence through the Python runtime API.

See [Canonical writing artifacts](../reference/writing-artifacts.md) for path,
eligibility, and evidence contracts; [Runtime API](../reference/runtime-api.md)
for Python values; and [Privacy and provider boundaries](../operations/privacy-and-provider.md)
for the trust model.
