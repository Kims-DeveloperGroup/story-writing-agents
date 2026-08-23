# Canonical writing artifacts

> **Availability:** unanimous consensus can publish the canonical files on this
> page. The runtime uses their provenance catalog when supplying core context to
> story agents. The `writing_artifacts` Python subpackage can parse content-free
> reference directives and exposes the repository authorization, read-action,
> and evidence values described below. `StoryRuntime` captures inline
> `${path}` and `${path}#Lx-Ly` indicators and gives every addressed provider
> invocation a fresh private coordinator. In provider-bound shell prompt spans,
> `$` lists eligible artifacts and `$${path}` or `$$` opens the independent
> ranged viewer. No standalone artifact command or public selection API exists.

This reference uses **writing artifact** only for agent-produced story output.
Transcripts, discussion logs, provider state, agent state, configuration, role
prompts, and diagnostic files are runtime internals or inputs, not canonical
writing artifacts.

## Exact output paths

There are four possible core-document identities:

```text
core_documents/global_outline.md
core_documents/part_outlines.md
core_documents/character_sheets.md
core_documents/narrative_rules.md
```

Manuscripts have this shape:

```text
manuscript/PART/PATH.md
```

Commands and Python calls accept the `PART/PATH.md` suffix as `output_path`.
For example, `part-01/scenes/opening.md` publishes
`manuscript/part-01/scenes/opening.md`.

`PART/PATH.md` is required. It is a normalized, workspace-relative POSIX path:

- `PART` begins with an ASCII letter or digit and then uses at most 63 ASCII
  letters, digits, `_`, or `-` characters;
- every later component begins with an ASCII letter or digit and then uses at
  most 127 ASCII letters, digits, `_`, `-`, or `.` characters;
- the final component ends in `.md` and is not just `.md`;
- the complete UTF-8 path is at most 1,024 bytes; and
- absolute paths, backslashes, empty, `.` or `..` components, non-normalized
  forms, and non-Markdown targets are rejected.

`chapter.md` is invalid because it has no `PART`. `manuscript` itself satisfies
the part grammar, so `output_path=manuscript/chapter.md` is legal and publishes
`manuscript/manuscript/chapter.md`. The first `manuscript` is the canonical
directory; the second is the user-selected part ID.

## Reference directive parser

> **Availability:** the pure parser remains I/O-free, while `StoryRuntime` uses
> its result to authorize and enforce inline references for provider-bound
> operations. Parsing alone is not proof of an eligible read. Shell completion,
> picker, and ranged-viewer attachment are terminal/controller concerns and
> construct the same content-free indicators before this parser runs.

One directive is a content-free indicator containing either a canonical path
or that path followed by a one-based inclusive line range:

```text
${core_documents/global_outline.md}
${manuscript/part-01/scenes/opening.md}#L7-L12
```

The path inside `${...}` must be one of the four exact core-document paths or
a canonical `manuscript/PART/PATH.md` path under the grammar above. A range is
optional, immediately follows `}`, uses uppercase `L`, contains positive ASCII
decimal bounds without leading zeroes, and must have `start <= end`. Both
bounds are inclusive. A value larger than `sys.maxsize` is rejected.

`parse_artifact_references()` accepts one exact strict-UTF-8 `str` without NUL
and returns references in source order. One input may contain at most 16 active
directives. For repeated directives naming the same path:

- an exact duplicate is rejected;
- a whole-file directive conflicts with every ranged directive;
- inclusive ranges may not overlap, so `#L1-L3` conflicts with `#L3-L5`; and
- adjacent or disjoint ranges remain valid and retain their original order, so
  `#L1-L2` followed by `#L3-L5` is accepted.

Unescaped `$$` is reserved and rejected if it reaches this runtime parser. The
interactive terminal consumes an active `$$` picker marker before submission;
it never becomes a command. Every unescaped `${` begins a
directive and must have a closing brace, a nonempty whitespace-free canonical
path, and any range that begins with `#L` must be complete. Lowercase `#l`,
zero or leading-zero bounds, reversed ranges, extra range markers, and a
letter, number, underscore, or other identifier-continuation character
immediately following a range are malformed. A single `$` that does not begin
`${` or `$$` remains ordinary text.

Backslashes immediately before `$` determine whether the dollar is active.
The parser always preserves the exact caller input as `source_text`:

| Caller source fragment | `provider_text` fragment | Parsed reference |
| --- | --- | --- |
| `${core_documents/global_outline.md}` | unchanged | Yes |
| `\${core_documents/global_outline.md}` | `${core_documents/global_outline.md}` | No; the odd backslash escapes `$` and one escaping backslash is removed. |
| `\\${core_documents/global_outline.md}` | unchanged | Yes; an even run leaves `$` active and preserves both backslashes. |
| `\\\${core_documents/global_outline.md}` | `\\${core_documents/global_outline.md}` | No; the odd run loses one escaping backslash. |

This parity rule applies to bare dollars and reserved syntax too. With no active
directive and no escaped dollar, parsing is compatible with ordinary text:
`source_text` and `provider_text` are identical and `references` is empty.

Import the API from the subpackage, not the project-root facade:

```python
from story_writing_agents.writing_artifacts import (
    ArtifactReferenceBatch,
    ArtifactReferenceDirectiveError,
    parse_artifact_references,
)

batch = parse_artifact_references(
    "Use ${core_documents/narrative_rules.md} and "
    "${manuscript/part-01/scenes/opening.md}#L7-L12."
)
assert isinstance(batch, ArtifactReferenceBatch)
assert tuple(reference.token for reference in batch.references) == (
    "${core_documents/narrative_rules.md}",
    "${manuscript/part-01/scenes/opening.md}#L7-L12",
)
```

`ArtifactReferenceBatch` is immutable and contains the exact `source_text`,
the escaped-dollar `provider_text` projection, and an ordered tuple of
`ArtifactReference` path/range values. Each reference exposes its canonical
`path`, optional `line_range`, and reconstructed `token`; a range exposes its
inclusive `start` and `end`. Neither the batch nor a reference contains
artifact bytes. Invalid directive input raises
`ArtifactReferenceDirectiveError`, a `ValueError` subtype whose deterministic
messages do not echo a rejected path or artifact content. Parsing performs no
filesystem, repository, provider, controller, terminal, or network I/O.

At this stage, Python callers can validate directive grammar, preserve the
original input, compute the provider-text escape projection, and inspect
ordered canonical path/range indicators. This parser API alone does not
establish artifact eligibility, read content, supply it to a provider, or
require an agent read. Runtime entry points add those authorization and
provider steps for `${...}`. Interactive `$`/`$$` activation occurs before
parser submission and adds no parser I/O or state.

## Authorized reads and lifecycle evidence

> **Availability:** `StoryRuntime` integrates repository authorization,
> transient delivery, durable content-free evidence, and exact provider read
> enforcement for inline references. The shell can construct those indicators
> through `$` catalog completion and `$${path}`/`$$` ranged viewing; evidence
> and provider enforcement remain runtime-owned.

The `${canonical/path.md}` and `${canonical/path.md}#Lx-Ly` values remain
content-free indicators. A caller holding `WritingArtifactRepository` can use
an already parsed nonempty batch to:

1. capture one immutable authorization for an operation;
2. issue a repository-minted, one-use capability for the exact ordered batch;
3. deliver the captured selected bytes through a closeable transient value;
4. record a result, accept or terminate the invocation, and revalidate or
   finish the overall operation; and
5. inspect validated operation history later.

These actions are exposed by the repository module, not by the project-root
facade. The `writing_artifacts` subpackage exports the content-free value and
error contracts: `AuthorizedArtifactReference`, `ReferenceOperationManifest`,
`ReferenceInvocationCorrelation`, `ReferenceEvidence`,
`ReferenceOperationHistory`, `ReferenceOperationState`,
`ReferenceInvocationState`, `ReferenceAuthorizationError`, and
`ReferenceAuthorizationErrorCode`; plus the provider-neutral, content-free
`ReferenceReadActionKind`, `ReferenceReadRequest`, and `ReferenceReadAction`.
`WritingArtifactRepository.begin_reference_invocation()` creates the private
synchronous coordinator that owns one repository-minted capability. The
coordinator, capability, delivery, and content-bearing `ReferenceReadResult`
remain package-internal. Content carriers hide content from `repr`, reject
serialization, and are closeable; the capability is one-use. Closing releases
the runtime's references to byte tuples but does not guarantee memory zeroing.

### What the states mean

Operation history follows `REQUESTED` → `CAPTURED` → zero or more
`REVALIDATED` checkpoints → `ACCEPTED` or `TERMINATED`. The enum members are
serialized with the lowercase values shown in the table.

| Operation state | Internal event | What an inspecting caller can conclude |
| --- | --- | --- |
| `requested` | The create-only operation manifest and first evidence event exist before catalog or artifact reads. | A reference attempt was recorded. No bytes have been authorized. |
| `captured` | Every ordered reference was eligible, selected, hashed, and held in one immutable in-memory capture. | The recorded provenance and selected-byte proof were valid at capture time. |
| `revalidated` | Full catalog provenance and current file hash still match the capture at a caller-named checkpoint. | The captured identity was still current at that checkpoint; the event does not refresh the captured bytes. |
| `accepted` | Final revalidation succeeded and all invocations are terminal, with at least one accepted invocation. Routine runtime history may move directly from `captured` to this event because final revalidation occurs inside acceptance. | The complete runtime reference operation succeeded and its result may return or publish. |
| `terminated` | A fixed coded cancellation, provider, workflow, authorization, drift, or evidence failure ended the operation. | The operation was not accepted; the content-free outcome identifies the failure category. |

Each invocation follows `AUTHORIZED` → `DELIVERED` → `RESULT_RECEIVED` →
`ACCEPTED`, with `TERMINATED` available from any nonterminal state. It has its
own ordered history:

| Invocation state | Internal event | Current external meaning |
| --- | --- | --- |
| `authorized` | The repository persisted correlation and issued one private capability for the captured batch. | One exact attempt is authorized. No provider content or user-visible output exists, and the capability never enters a provider payload. |
| `delivered` | The capability was consumed, the source was revalidated, and evidence was persisted before transient bytes were returned. | On the backend path, the provider first returned the complete exact action and the bytes are ready for its immediately following call. This state alone does not prove remote receipt or use. |
| `result_received` | The caller recorded that a substantive invocation result was received. | The backend records this only after structurally decoding the substantive result. Direct repository callers can make the same claim, so the event is not proof of comprehension or obedience. |
| `accepted` | Another full revalidation succeeded after the recorded result. | The exact invocation evidence chain was accepted before the backend returned its reply. The result's optional operation ID lets Python callers inspect the content-free history. |
| `terminated` | A fixed coded failure ended the invocation. | No accepted reply exists for the attempt. A retry needs a fresh correlation, coordinator, and one-use capability; it cannot reuse this evidence. |

The durable history contains ordered path and optional range, full catalog
provenance, selected SHA-256 and byte count, source line count, agent/invocation
correlation, lifecycle state, checkpoint, and terminal outcome. It contains no
artifact text, captured bytes, parser source/provider text, provider prompt, or
provider result. Baseline core-context reads do not create this history and
cannot satisfy an explicit-reference evidence requirement.

### Provider-enforced invocation sequence

The backend seam is separate from parsing and from the ordinary request
models. An internal caller first captures a nonempty batch and calls
`begin_reference_invocation()` with one complete correlation. Then:

1. `BoundAgent` validates a synchronous coordinator and the addressed agent,
   phase, and round before provider work.
2. Under the same provider-thread lock as the substantive work, the backend
   sends one content-free prelude containing only the operation/invocation
   correlation, ordered canonical path/range indicators, and fixed rules.
3. A request-specific schema and exact equality check require the provider's
   first action to echo the complete ordered batch. Result-first, partial,
   reordered, duplicate, broadened, foreign, and malformed actions fail.
4. Only after that action does the coordinator consume its capability. The
   backend decodes the selected strict-UTF-8 bytes and adds them once, in the
   same order, under `untrusted_writing_artifact_context` on the immediately
   following substantive payload.
5. A structurally valid result advances through `result_received`, final
   repository revalidation, and `accepted` before the backend returns it.

The selected content is not copied into the task, user text, candidate,
blackboard messages, conversation history, retrieval observations, durable
evidence, or diagnostics. An accepted invocation proves the exact read request,
delivery sequence, substantive result, and final revalidation. It does not
prove that a model understood, followed, or correctly applied the content.
All four canonical roles use the same structural `BoundAgent` path; role-prompt
prose is not the enforcement mechanism. See the exact
[backend seam](backend-and-sessions.md#referenced-invocation-boundary),
[provider data flow](../design/prompts-and-provider.md#explicit-reference-provider-flow),
and [privacy boundary](../operations/privacy-and-provider.md#explicit-reference-data-boundary).

### Capture, limits, and revalidation

Initial capture takes the repository lock, writes `requested` evidence, loads
the trusted catalog, and securely reads each unique referenced path once even
when several ranges use it. It validates the complete source as strict UTF-8
without NUL before selecting bytes. Line ranges use universal newline records:
CRLF, LF, and lone CR each end a line and remain in the selected byte slice;
Unicode U+2028 remains ordinary content. Empty input has zero lines, and a
final terminator creates no extra empty line.

The inclusive limit is 128 KiB of selected UTF-8 bytes per reference and
256 KiB across the ordered batch. Exact-limit selections are accepted. An
out-of-bounds range or a value above either limit fails with a fixed,
content-free code. These authorization limits are separate from the ranged
viewer's 8 MiB defensive source limit.

Delivery consumes the capability before checking the caller's requested tuple,
so a mismatched order, path, or range cannot be retried with that capability.
Before delivery, invocation acceptance, operation checkpoints, and final
operation acceptance, the repository compares the complete current provenance
and full-file hash with the original capture. The selected bytes are never
refreshed. Even identical file bytes with newly published provenance are drift
and terminate the affected path through the lifecycle.

An empty reference batch returns without taking the artifact lock, reading
storage, or creating history. For a nonempty batch,
`inspect_reference_operation()` returns `None` only when the operation
directory is absent. A present but partial, malformed, noncanonical, or
inconsistently sequenced history raises the fixed `evidence_corrupt` error
instead of being repaired or treated as absent.

## What a user can change

Ask the story agents for changes through a consensus prompt or supplied draft:

- an outline request can lead the lead orchestrator to publish full replacements
  for `global_outline.md` or `part_outlines.md`;
- a character, relationship, or world-continuity request can lead to a full
  `character_sheets.md` replacement;
- a prose, viewpoint, tone, or formatting request can lead to a full
  `narrative_rules.md` replacement; and
- a manuscript request always publishes one new file at the required part path.

For example:

```text
/run --output part-01/planning/baseline.md Establish the agreed outline, character facts, and narrative rules, then produce the approved baseline.
```

### Inline explicit-reference scenarios

These indicators are executable prompt text. The interactive interface can
construct the same path/range values through discovery and the ranged viewer;
automation and Python callers supply them directly.

- Outline review: `@plot Compare ${core_documents/global_outline.md} and ${manuscript/part-01/scenes/opening.md}#L40-L65, then propose the smallest outline adjustment.`
  The plot architect must request both references in that order before its
  advisory reply is accepted.
- Character-sheet review: `@character Check ${core_documents/character_sheets.md}, ${manuscript/part-01/scenes/opening.md}#L12-L24, and ${manuscript/part-01/scenes/aftermath.md}#L3-L18 for continuity.`
  One exact three-reference batch is required; partial or reordered reads fail.
- Narrative-rule review: `@execution Apply ${core_documents/narrative_rules.md}#L8-L20 when reviewing ${manuscript/part-01/scenes/opening.md}#L66-L90.`
  Both selected ranges must reach the one substantive call as untrusted
  reference context.
- Manuscript revision: `/run --output part-01/revisions/opening-r2.md Revise ${manuscript/part-01/scenes/opening.md} using ${core_documents/part_outlines.md}#L30-L52.`
  Every addressed agent, phase, round, attempt, or replacement receives an
  independent correlation and read lifecycle over the captured top-level batch.

The first three advisory requests can propose changes but cannot publish them.
Only the existing unanimous consensus workflow can replace a core document or
publish a new part-scoped manuscript path.

See the [artifact-reference guide](../guides/artifact-references.md) for exact
syntax, limits, work-history inspection, and complete user scenarios.

An ordinary advisory message can help refine the request but cannot write a
core document or manuscript. During a consensus run, the execution specialist
authors `r0` from either the prompt or the supplied draft. A supplied draft is
therefore input, not bytes copied directly into a canonical artifact. All three
specialists audit the same candidate. If repair is needed, conflicting
specialists propose changes and the lead authors the synthesized replacement.
Only unanimous approval permits finalization.

At finalization, the lead may return complete text for genuinely changed core
documents. The runtime accepts only the four exact filenames and persists the
agent-returned bytes without inventing or editing story content. A successful
commit always contains exactly one manuscript, even when the user's primary
goal was a core-document change.

Manuscripts are create-only, and an identity remains reserved after it has been
cataloged even if its file is later deleted. To revise or replace one, supply
the current text as draft input and choose a distinct path such as
`part-01/revisions/opening-r2.md`. The runtime never overwrites the earlier
manuscript. Core documents, by contrast, keep their four stable identities and
can be replaced by a later unanimous publication.

## Eligibility states users observe

A canonical-looking path is not enough. The catalog exposes only current,
agent-authored bytes whose publication history still validates.

| State | Internal meaning | What users can do or see |
| --- | --- | --- |
| Absent | No file and no current catalog entry. | The file is not supplied as agent context. A later unanimous run may create it. |
| Manual or unprovenanced | Bytes exist, but no matching repository-owned catalog and commit record exists. Generic `Workspace` writes cannot create this state because they reject canonical paths; it can result from an external filesystem edit. | The bytes remain on disk but are excluded from the eligible catalog and core prompt context. A manuscript at that path also blocks create-only publication. |
| Current and eligible | The path is canonical, its catalog entry and immutable commit record agree exactly, its current SHA-256 matches, and the complete catalog/commit ledger passes global audit. | The runtime may list/read it through the artifact repository. Eligible core documents are supplied only to their allowed agents. |
| Stale and ineligible | A catalog entry exists, but the file is missing, unsafe, nonregular, or its bytes no longer match the recorded hash. | Catalog listing and selective context reads omit that entry; an exact artifact read fails. The runtime does not delete or silently repair the bytes. The permanent commit ledger continues to reserve a deleted manuscript identity. |
| Recovery pending | One immutable schema-v2 JSON transaction journal remains in authoritative `transactions/`. It records the exact catalog-before position, catalog-after image, commit, ordered targets, old/new hashes, and complete new bytes. | The next catalog read, artifact read, or commit first attempts deterministic recovery under the artifact lock. No partially published set becomes eligible. |
| Recovered | Roll-forward completed, the commit record and catalog were published, and the journal was exclusively retired into non-authoritative staging with a `.completed` suffix. This is an observed outcome, not a stored state name. | The newly current entries become eligible on that same access. The retained journal bytes are internal evidence, not a listed artifact or pending transaction. |
| Recovery blocked | A pending journal, target, or recovery invariant could not be trusted, and a persistent block marker was written. | Every later artifact-repository read or commit fails closed. Preserve the workspace and investigate or restore a verified backup; there is no automatic repair command. |
| Ledger or catalog invalid | A permanent commit is missing, malformed, noncanonical at the byte level, duplicated, unexpected, or inconsistent with the catalog; the catalog is missing/incomplete; or repeated core history has no unique newest instant or projects an older commit. | Artifact access fails closed without changing story bytes. Preserve the workspace and restore exact verified repository metadata from backup; the runtime does not rewrite, reconstruct, or migrate it. |
| Lock unsafe | The permanent `.artifacts.lock`, `.artifacts.lock.anchor`, and `.artifacts.lock.ready` names are aliased, partial, divergent, have an extra hard link, or cannot be created and flushed safely. | The current artifact operation fails before entering the protected repository body. Stop workspace processes and investigate the triple; the runtime does not clean up, reconstruct, or migrate it. |
| Retained staging residue | A `.tmp`, `.held`, `.retired`, or `.completed` file remains under the repository's non-authoritative `staging/` directory. | This is normal internal state and never appears in the canonical catalog or agent context. It may grow over time; do not treat it as a writing artifact or move it into `transactions/`. |

A manual edit invalidates eligibility without deleting the file. Restoring the
exact recorded bytes also restores hash eligibility when the complete catalog
and permanent commit ledger still audit successfully. Otherwise, let a later
unanimous run republish a core document. For a changed manuscript, use a new
part-scoped revision path. Stop all workspace processes before any
external/manual filesystem edit; live manual namespace mutation is outside the
supported writer boundary.

Do not edit the catalog, commit records, transaction journals, lock, or recovery
marker to force a state. They are consistency evidence, not user-authored
documents.

## Publication and recovery mechanism

`WritingArtifactRepository` is the only application write path for canonical
artifacts. Generic `Workspace.write_text()` and `write_json()` reject all four
core paths, every canonical manuscript path, and case-folded or
Unicode-normalized aliases of those reserved namespaces before mutating the
filesystem. Repository construction captures its root immediately as a lexical
absolute path. A relative root is bound to the construction-time current
directory, so a later directory change cannot retarget the repository. This
capture does not call `resolve()`, dereference symlinks, open or inspect the
filesystem, or probe security primitives; symlink spelling is preserved. The
first catalog read, artifact read, or commit securely opens and validates that
captured root, checks for the
required descriptor-relative, no-follow, listing, and advisory-lock primitives,
takes an in-process lock and a workspace-local lease, then resolves pending
recovery before continuing. Other workspace and runtime APIs remain usable on a
host that cannot perform artifact I/O. An unsafe captured root therefore fails
on that first artifact operation without being silently resolved elsewhere.

The lease is a permanent three-name hard-link identity for the repository lock,
not an artifact. The exact names are `.artifacts.lock`,
`.artifacts.lock.anchor`, and `.artifacts.lock.ready`; all three must name one
regular inode with link count three. Their aliases are rejected by
exact-spelling checks before final inspection, creation, or locking.

Initialization first takes an exclusive bootstrap `flock` on the already-open
writing-artifact state directory and flushes that directory's retained ancestry
to the workspace root. When all three names are absent, it creates the primary
lock with `O_EXCL`, flushes the file and parent, links the anchor and flushes the
parent, verifies the two-link intermediate state with `ready` still absent, then
links `ready` and flushes the parent again. It verifies the complete triple,
acquires `flock` on the primary lock, reverifies the triple, and only then
releases the bootstrap lock and enters the protected body. Later use requires
the already complete triple and opens the primary without create flags.

Partial one- or two-link state, an old two-name development lock, divergent
inodes, an extra hard link, or a failed creation is rejected unchanged on retry;
there is no cleanup, reconstruction, or migration. The three names, open
descriptor, retained directory chain, and workspace root are revalidated around
mutation. Canonical artifact files themselves must still be unique regular
files with one link.

One successful commit follows this order:

1. Validate the output grammar, strict-UTF-8 agent text, four-name core
   allowlist, story-agent authorship, session/debate/revision identities, and
   exact repository JSON schemas. Duplicate object keys are rejected at every
   nesting level.
2. Globally audit the complete immutable commit ledger against the current
   catalog, then reject a manuscript path already reserved by any historical
   commit or occupied by existing bytes.
3. Create one immutable schema-v2 transaction journal containing the exact
   catalog-before existence/hash/image, catalog-after image, commit, and ordered
   targets with old/new hashes, complete new bytes, and authors. The journal is
   never replaced with another phase image.
4. Stage each target outside the authoritative `transactions/` directory, then
   publish it with descriptor-relative, no-follow, compare-before-replace I/O.
   Publication creates a unique `.tmp` with `O_EXCL`, flushes it, exclusively
   moves verified prior bytes to a `.held` name when replacing, and uses the
   host's native atomic no-replace rename to move staged bytes into the absent
   target name. It then verifies the published bytes and retained identities.
   Newly created directories are made durable by flushing each parent; target
   publication and retirement flush the directories whose entries changed.
5. Publish the immutable commit record, then publish the catalog last.
6. Verify the unchanged journal and exclusively move it from `transactions/`
   to a `.completed` name in staging.

The repository never unlinks or uses a replacing rename for this publication
flow. A detected post-publication failure exclusively retires the new target as
`.retired` and conditionally restores the exact `.held` prior bytes when the
original path relationship remains safe. Changed or replaced journal bytes are
preserved and recovery blocks instead of retiring uncertain state. Every
`.tmp`, `.held`, `.retired`, and `.completed` residue remains in staging. That
directory is not transaction authority, is ignored by catalog/recovery reads,
and can grow across successful and failed operations.

If the process stops after step 3, recovery first compares the current catalog
bytes with the journal's two exact positions. At catalog-before, every target
must equal its recorded old or new bytes; recovery rolls the transaction
forward and publishes the catalog last. If that pending commit already exists,
only its byte-canonical record exactly matching the journal is temporarily
exempt from the catalog-before path and newest-core audit. Older established
commits still determine the complete before authority; an unrelated record
absent from that exact view or a divergent record blocks. The projected after
view includes the pending commit and must pass the full audit. At catalog-after,
every target, canonical commit, catalog projection, and unique-newest core
selection must already equal the recorded new state; recovery only verifies
them and retires the journal. A
catalog matching neither position, an untrusted target, a missing/replaced
journal, or a missing/divergent commit in catalog-after state blocks recovery
durably rather than guessing. Catalog-last ordering means an
interrupted target set is never advertised as partly eligible.

This transaction covers its manuscript, changed core documents, commit record,
and catalog. It does not form one transaction with debate messages, lifecycle
state, result JSON, conversations, configuration, or provider state. Inspect
the debate after an infrastructure interruption even when artifact recovery
succeeds.

## Provenance and work history

Each current catalog entry records the exact workspace-relative path, artifact
kind, SHA-256, commit ID and timestamp, author agent, session ID, debate ID, and
revision ID. Every immutable commit record permanently remains in the ledger and
groups exactly one manuscript with any core updates from that publication. At
publication, ordered transaction targets, commit artifacts, and the
catalog-after entries introduced by that commit contain the same exact
path/kind/author/hash set and correlation metadata.

Every access globally audits the ledger and catalog. Commit filenames and
schemas must be exact, and each immutable commit file must byte-for-byte equal
the repository's canonical sorted, indented JSON encoding with its final
newline. Semantically equivalent reformatting is invalid immutable state; it is
preserved and fails closed rather than being rewritten or migrated. Duplicate
historical manuscript paths are also invalid. The
catalog path set must equal the union of every manuscript and core-artifact path
named by every non-exempt immutable commit, and every catalog entry must exactly
project an artifact from its named commit.

Commit timestamps must be offset-aware and are compared as normalized instants.
For every repeated core path, authority belongs to the one commit at the unique
newest instant. The older commits remain history and keep that shared path in
the ledger union, but a catalog entry projecting an older commit is stale and
fails closed. Two commits tied at the newest instant are ambiguous and also
fail; file order or commit ID never breaks the tie. Repository-generated commit
timestamps are strictly later than every ledger instant: wall-clock time is used
when already later, otherwise the latest ledger instant advances by one
microsecond. A backward clock therefore cannot reverse core authority. If that
instant cannot advance because the timestamp range is exhausted, the commit
fails before a journal or artifact is mutated.

At catalog-before recovery, an already-published pending commit is exempt only
when its canonical bytes exactly match the journal. That pending commit
contributes no paths and does not participate in newest-core selection for the
expected before catalog; every path and unique newest core authority contributed
by older established commits remains required. The projected catalog-after and
exact catalog-after recovery include the pending commit in the full ledger and
rerun path, entry, and unique-newest audits without an exemption. Catalog loss
therefore cannot erase manuscript reservations or other established artifact
identities, and the runtime neither reconstructs nor migrates repository
metadata. Schema fields and scalar types are exact rather than extensible.

The execution specialist authors manuscript revision `r0`. Any later approved
revision is a lead-authored synthesis, and every core-document update is
lead-authored. Mismatched recovered authorship or correlation is invalid
repository state rather than eligible work history.

This repository-owned metadata is the artifact work history used for
eligibility. The JSON lives under
`agent_states/orchestrator_state/writing_artifacts/` and remains plaintext. It
is deliberately excluded from the canonical produced-output set.

## Filesystem and privacy limits

Artifact I/O requires the host's safe descriptor-relative, no-follow directory
operations, unique regular files, advisory locking, and a native atomic
no-replace rename: `renameatx_np(RENAME_EXCL)` on Darwin or
`renameat2(RENAME_NOREPLACE)` on Linux. The check occurs on the first artifact
operation, not repository or runtime construction. A host without that native
operation fails the artifact operation while other non-artifact APIs remain
usable. Every descriptor-relative component must have exact stored spelling;
case-folded or NFKC-normalized aliases fail closed rather than resolving to the
canonical identity. Symlinks, hard-linked artifacts, directories, other
nonregular targets, workspace-root or ancestor replacement, unsafe path
components, and detected file changes fail closed. Catalog and selective reads omit an entry-local failure;
root, lease, recovery, catalog-schema, and global provenance-integrity failures
propagate because the repository boundary is no longer trustworthy. Artifact
text is strict UTF-8. The repository does not encrypt content, provenance, or
journals, provide secure erase, or enforce a retention policy.

The lease serializes repository writers, and generic `Workspace` canonical
writes are rejected. It is not a security boundary against an actively hostile
same-user process mutating live names during a native filesystem call. Make
manual or tool-driven filesystem changes only after every workspace process has
stopped, then restart and let eligibility/recovery validation report the result.

The catalog and repository are module-supported internals used by the runtime;
they are not re-exported from the package root. Exact run signatures are in
[Runtime API](runtime-api.md), workspace topology in
[Session storage](configuration/session-storage.md), filesystem consequences in
[Filesystem safety](../operations/filesystem-safety.md), and debate sequencing
in [Consensus workflow](../design/consensus-workflow.md).
