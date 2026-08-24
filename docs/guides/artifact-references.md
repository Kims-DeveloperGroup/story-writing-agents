# Use canonical artifact references

> **Availability:** inline `${path}` and `${path}#Lx-Ly` references are active
> in provider-bound conversation, retrieval, and consensus input. In the TTY
> shell on a capable terminal, `$` opens the labeled whole-file selection
> panel. Typing the second `$` immediately changes it into ranged-source
> selection before any third key or newline; Enter/Tab opens the highlighted
> source in the independent viewer. A completed `$${path}` marker opens that
> source directly on Enter/Tab. These controls augment prompt editing; `$` is
> not a command and does not submit the prompt. Ordinary-line fallback uses the
> numbered flows described below.

An artifact reference tells every addressed story agent which existing output
it must read before its reply can be accepted. It carries an eligible
workspace-relative source path and optional inclusive line range, not a copy of
the file. The runtime captures the selected bytes once, requires a fresh exact
read action from each provider invocation, and revalidates the source before
accepting the operation.

## User impact

You can now ground an advisory request or consensus modification in one or more
ordered writing artifacts without pasting their contents. Each addressed
agent and debate phase must independently request the full batch; one call
cannot silently ignore a reference or borrow another call's evidence. This
guarantees the read sequence and current source identity, not the quality of an
agent's interpretation.

In a provider-bound shell prompt you can now discover current artifacts without
leaving the draft, insert a whole-file indicator, or select an inclusive line
range. Selection, cancellation, Ctrl-C, EOF, and handled errors return to the
same draft and cursor without dispatching it. The runtime still exposes
detailed evidence through Python inspection rather than a reference-specific
shell history view. There is no standalone artifact command, automation picker,
or public selection API.

Discovery is based on the current filesystem, not publication history. It
includes safe empty, manually created, pre-provenance, older, published, and
hash-divergent files at the four exact core paths, plus every eligible
lowercase-`.md` descendant anywhere below `manuscript/`. That includes
`manuscript/root.md`, arbitrary nesting, spaces, Unicode, `{`, `$`, and `#` in
path components. This source rule is deliberately broader than the strict
`manuscript/PART/PATH.md` grammar used when agents publish new output.

Discovery excludes `.gitkeep`, uppercase `.MD`, a filename that is only
`.md`, extra core files, `}`, line/control/format/surrogate characters,
unnormalized or unsafe paths, symlinks, multiply linked or nonregular files,
and every internal or outside path. Listing is deterministic path/type metadata
only; file bytes are opened later for viewing or runtime capture. The shell
uses its normally resolved workspace: an explicit `--workspace` wins,
otherwise the nearest initialized ancestor is selected.

Missing or corrupt publication catalog, ledger, journal, or recovery state does
not empty this source list or block safe source reads. It still blocks catalog
audit, transaction recovery, and consensus publication. An unsafe shared
repository lock or workspace root blocks both boundaries.

## Write a reference

```text
${core_documents/global_outline.md}
${manuscript/part-01/scenes/opening.md}#L40-L65
```

Use uppercase `L`, positive ordered line numbers, and one eligible source path:

- `core_documents/global_outline.md`
- `core_documents/part_outlines.md`
- `core_documents/character_sheets.md`
- `core_documents/narrative_rules.md`
- any normalized lowercase-`.md` descendant under `manuscript/`, including
  direct files such as `manuscript/root.md` and deeper paths with spaces or
  Unicode names

One input may contain up to 16 references. Their order is significant. Each
selection is limited to 128 KiB of UTF-8 and the combined batch to 256 KiB.
Exact duplicates, overlapping ranges, and mixing a whole-file reference with a
ranged reference to the same path are rejected. Internal transcripts,
discussion logs, agent/provider state, and configuration are not canonical
writing artifacts and cannot be referenced.

To send a literal dollar without opening artifact controls, type `\$`. To send
the literal text `${...}` without creating a runtime reference, use
`\${...}`. The exact typed input remains in durable conversation/debate
history; the provider projection removes the escaping backslash.

## Discover, view, and attach in the shell

Artifact controls activate only when all three prerequisites hold:

1. This shell has an attached session.
2. That session has a focused active conversation.
3. The cursor is in provider-bound prompt text.

Eligible text includes ordinary advisory input, the text after an `@agent`
route or `/say`, and `/run` prompt text. The controls also work in the multiline
`/run` composer and the package-internal chat composer after the first two
prerequisites hold. They are inert in the lobby, while attached without
conversation focus, and in command names, management arguments, identities,
options, output paths, subjects, settings, confirmations, and displayed output.
The shell orientation and full `/help` state the attachment/focus prerequisite
and distinguish `$` whole-file selection from `$$` ranged selection.

| Surface | `$` activation |
| --- | --- |
| Attached + focused ordinary advisory line | Eligible across the line. |
| Valid `@agent TEXT` or `/say TEXT` | Eligible only in `TEXT`. |
| `/run [--fresh] [--output PATH] PROMPT` | Eligible only in `PROMPT`, never flags or `PATH`. |
| `/run` multiline composer; internal chat composer | Eligible in composed prompt text. |
| Lobby or attached without conversation focus; management, ID, subject, option, output-path, settings, confirmation, and output fields | Inert. |
| Noninteractive CLI, automation, or Python | No activation UI; type literal `${...}` indicators. |

| Typed control | Capable terminal | Ordinary-line fallback | Result in the draft |
| --- | --- | --- | --- |
| `$` | Shows `Writing artifact selection [open]`, then `[controls] Up/Down selects; Enter/Tab inserts a whole-file reference.` Up/Down chooses and Enter or Tab completes. | Shows a numbered source list. | `${path}` replaces only the active marker; the prompt is not submitted. |
| `$${path}` | After the closing `}`, Enter or Tab opens that artifact in the independent viewer. | Submitting the marker line opens its complete numbered text viewer. | Enter on a range inserts `${path}#Lx-Ly`. |
| `$$` | The second `$` immediately shows ranged-source choices. Up/Down changes the highlight; Enter or Tab opens it in the viewer. | Submitting the marker line uses a numbered picker, then the complete numbered viewer. | The selected path and inclusive range replace only the marker. |
| `\$` | No activation. | No activation. | A literal dollar remains in the prompt. |

In the capable viewer, Up/Down moves the active line. Shift+Up/Down creates,
extends, contracts, or reverses a normalized inclusive range; Enter selects it
and Escape cancels. The independent viewer stays active while the range is
being adjusted, then closes and restores the same composer when the selection
or cancellation completes. In fallback, enter exact ASCII `START-END` or
`/cancel`. The full artifact remains visible in fallback scrollback. The
terminal viewer receives verified text from the caller and retains no document
or selection after return.

One prompt can contain several completed indicators. Each new activation uses
a fresh filesystem-list/read/view operation; completed indicators stay in their typed
order. A selection replaces only its active `$` marker and leaves the rest of
the buffer unchanged, with the cursor immediately after the inserted token.

### Whole-file selection example

At an attached, focused prompt, type:

```text
Compare $core_documents/char
```

The panel title is `Writing artifact selection [open]`. The path fragment after
`$` filters the rows case-sensitively. If
`${core_documents/character_sheets.md}` is highlighted, Enter or Tab replaces
only `$core_documents/char` with that whole-file indicator. It does not send
the line. You can continue editing and type `$$`; the ranged-source choices
appear as soon as the second `$` is entered. Use Up/Down, then Enter or Tab to
open the highlighted source in the independent viewer. Only a later ordinary
prompt submission dispatches the resulting references.

Escape closes an immediate `$$` panel but preserves the exact `$$` text.
Pressing Enter or Tab later reopens the ranged-source choices in place. To name
a source directly, type a complete marker such as
`$${manuscript/older drafts/{世界$ 章#1.md}`; typing `{` after `$$` converts
the panel back into exact text entry, and Enter or Tab after the closing `}`
opens that file. Character-by-character input, a single terminal write, and
bracketed paste preserve the marker literally.

If the fragment matches no current safe path, the open panel instead shows
`[empty] No matching writing artifacts. Change the path fragment.` Edit or
remove the fragment to recover. If no current safe reference source exists,
typing `$` immediately shows `[empty] No writing artifacts. Add a supported
canonical core document or Markdown file with a lowercase .md suffix under
manuscript/. Consensus can also publish one.` No provider call starts. Enter or
Tab then emits the established `command_invalid` `empty_sources` error below
and restores the `$` draft.

### Capable and fallback completion boundary

Capable editing performs completion or range attachment inside the same live
input read and returns to the exact buffer without submission. A fallback
activation first completes the picker/viewer interaction, then prints the
amended draft and asks for a second confirmation. Press Enter to submit that
draft, type a replacement draft to reevaluate it, or resolve another active
marker. This extra confirmation prevents a fallback selection from dispatching
implicitly. Fallback input remains visible in scrollback.

Cancellation, interrupt, EOF, and handled activation failures restore the
exact modeled draft, cursor, focus, and input mode. They add no main history and
start no provider work. The fixed content-free failures are:

| Condition / category | Exact summary | Exact recovery hint |
| --- | --- | --- |
| Empty sources / `command_invalid` | `No current writing artifacts are available.` | `Add a supported canonical core document or Markdown file with a lowercase .md suffix under manuscript/. Consensus can also publish one.` |
| Invalid insertion / `command_invalid` | `The artifact reference cannot be inserted.` | `Remove the active marker or choose a nonconflicting current artifact.` |
| Stale selection / `workspace_invalid` | `The selected writing artifact is no longer current.` | `Type $ again to refresh the canonical artifact list.` |
| Unsafe view / `workspace_invalid` | `The selected writing artifact cannot be viewed safely.` | `Inspect its size and UTF-8 repository state, then refresh the list.` |
| Repository unavailable / `workspace_invalid` | `The writing-artifact repository is unavailable.` | `Inspect workspace recovery guidance, then retry discovery.` |

These messages disclose no path or selected content. Listing is a fresh sorted
metadata-only filesystem snapshot; opening performs a bounded descriptor-safe
read of current bytes; normal submission later performs an independent runtime capture and
revalidation instead of reusing viewer bytes. Unsafe listing-to-view or
post-capture drift fails closed. Any replacement between viewing and submission
may therefore be the version agents read; reopen the viewer when exact
displayed lines matter. Every source open is limited to 8 MiB: larger explicit
limits clamp to 8 MiB, while stricter positive limits are honored. Content must
be strict UTF-8 without NUL. A path can appear in the metadata-only list and
still fail when opened because listing does not decode or read it. The viewer's
8 MiB display limit is independent from the
runtime's 128 KiB per-reference and 256 KiB per-batch limits.

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

The source files in these examples may be manual or pre-provenance bytes; the
read contract is the same. Advisory work still cannot write them. Consensus
can publish approved full replacements for the four stable core paths, but a
manuscript path is create-only even when its current file was created manually
or is absent while its publication ledger still reserves the identity. Choose
a new revision path instead. Reading a manual source never creates or repairs
catalog, commit, or authorship records.

## What happens during one operation

There are two related lifecycles:

| Scope and state | Internal meaning | What you can conclude externally |
| --- | --- | --- |
| Operation `requested` | A content-free manifest exists before artifact reads. | The attempt was recorded; no provider reply is accepted yet. |
| Operation `captured` | Every ordered selection was captured from current safe filesystem sources with full-source identity and selected-byte proofs. | The requested batch existed and passed capture checks. No publication provenance is implied. |
| Final revalidation | The runtime reopens each source and checks its path, kind, physical identity, size, and full hash immediately before completion. Explicit repository checkpoints may be recorded as `revalidated`; routine history can proceed directly from `captured` to `accepted`. | Files were not deleted, replaced, made unsafe, enlarged beyond the source bound, or otherwise changed during the accepted operation. |
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
session. History uses schema v2 and contains paths/ranges, filesystem source
identities, hashes, byte counts, correlations,
states, and outcomes—not selected text, user/provider prompts, replies, or
diagnostics. Schema-v1 evidence is neither migrated nor reusable for a new
authorization or resumed operation. A handled `ReferenceOperationFailure` carries the operation ID and
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
missing, changed, or corrupt source/evidence state is `workspace_invalid`. It does
not display paths, selected text, the operation ID, or internal exception prose.
Use the Python inspection API when an integration needs the durable operation
history.

Selected content is local and transient until the substantive provider call.
It is excluded from task/user/candidate fields, conversation and retrieval
history, durable evidence, and diagnostics. Local release is not secure-memory
erasure, and a provider or remote thread may retain content after receipt under
its own policy. Acceptance proves the read/delivery/result sequence, not that a
model understood or obeyed the material.

## From editing to an accepted agent result

The UI and runtime keep separate state so browsing never masquerades as agent
work:

```text
editing a provider-bound span
  $                    -> source completion -> ${path}      -> same editing span
  second $             -> ranged choices shown immediately
  $$ + Up/Down + Enter -> viewer -> ${path}#Lx-Ly           -> same editing span
  $${path} + Enter     -> viewer -> ${path}#Lx-Ly           -> same editing span
  cancel/error         -------------------------------------> exact original span

later ordinary submit -> parse once -> capture/revalidate -> per-invocation
read prelude -> transient provider context -> accepted result or atomic failure
```

Internally, the controller supplies only the provider-bound buffer span and
filesystem-source callbacks. A pure activation layer classifies the active
marker. Exact `$$` projects both the source choices and a correlated picker
request; the editor controls own the open panel and highlight. Enter or Tab
converts the highlighted path into a viewer request, and the terminal driver
owns that viewer's input and screen lifecycle. For an available but empty
source list, `$` projects an empty artifact panel together with a deferred
`empty_sources` failure: rendering stays side-effect-free, while Enter or Tab
surfaces the fixed error. A no-match panel is instead the filtered view of a
nonempty completion projection, so changing the path fragment can reveal rows
again.

The driver restores the immutable origin on every cancel or failed activation
and clears call-local callbacks. The controller safely reads one current
source, supplies its text and display name to the independent, storage-free
viewer, and attaches only the returned inclusive line numbers to the path.
Only the later normal submission enters history, reaches the parser, and
dispatches an agent. The runtime then captures and revalidates the complete
ordered reference batch and requires every addressed agent invocation to read
each indicator through its one-use authorization; viewer bytes are never
reused as proof of that read.

Source-list display, highlighting, viewing, and cancellation create no durable
work-history row. A completed token enters main history only if the user later
submits the prompt; composer navigation remains history-free, while its final
sent prompt is recorded by the debate workflow. Conversation and debate
history retain the exact indicator text, not selected bytes. Runtime inspection
then records content-free operation and per-invocation evidence as described
above, and writing-artifact provenance shows only agent-authored publications.
Displaying or referencing a manual file does not adopt it into that history.

Automation and noninteractive commands do not receive this UI. Supply literal
`${...}` indicators in their quoted prompt text. The independent viewer is not
a supported public selection API, and there is no installed `/compose` or
standalone artifact command. No legacy reference spelling or selection model is
accepted or migrated.

See [Canonical writing artifacts](../reference/writing-artifacts.md) for path,
source, publication, and evidence contracts; [Runtime API](../reference/runtime-api.md)
for Python values; and [Privacy and provider boundaries](../operations/privacy-and-provider.md)
for the trust model.
