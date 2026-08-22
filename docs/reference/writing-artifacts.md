# Canonical writing artifacts

> **Availability:** unanimous consensus can publish the canonical files on this
> page. The runtime uses their provenance catalog when supplying core context to
> story agents. The `writing_artifacts` Python subpackage can now parse and
> validate content-free reference directives, but the installed shell does not
> make them operational. Standalone artifact commands, interactive `$`/`$$`
> completion and selection, artifact attachment, and enforced agent reads are
> not implemented yet.

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

> **Availability:** #192 supplies a pure Python parser and immutable value
> contracts only. It does not read or authorize a file, attach artifact text to
> provider context, prove that an agent read anything, or add shell completion,
> a picker, or a ranged viewer. Later #163 and #164 children own those runtime
> and interactive integrations. Do not treat a successful parse as an eligible
> artifact read or use the syntax as an installed-shell feature yet.

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

Unescaped `$$` is reserved and rejected. Every unescaped `${` begins a
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
ordered canonical path/range indicators. They cannot use this API alone to
establish artifact eligibility, read content, supply it to a provider, require
an agent to read it, or activate `${...}`, `$`, or `$$` behavior in the shell.

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
