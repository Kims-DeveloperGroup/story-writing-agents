# Canonical writing artifacts

> **Availability:** unanimous consensus can publish the canonical files on this
> page. The runtime uses their provenance catalog when supplying core context to
> story agents. Standalone artifact commands and file-reference syntax/UI are
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
| Current and eligible | The path is canonical, its catalog entry and immutable commit record agree exactly, and its current SHA-256 matches. | The runtime may list/read it through the artifact repository. Eligible core documents are supplied only to their allowed agents. |
| Stale and ineligible | A catalog entry exists, but the file is missing, unsafe, nonregular, or its bytes no longer match the recorded hash. | Catalog listing and selective context reads omit that entry; an exact artifact read fails. The runtime does not delete or silently repair the bytes. A deleted cataloged manuscript identity remains reserved and cannot be reused. |
| Recovery pending | One immutable schema-v2 JSON transaction journal remains in authoritative `transactions/`. It records the exact catalog-before position, catalog-after image, commit, ordered targets, old/new hashes, and complete new bytes. | The next catalog read, artifact read, or commit first attempts deterministic recovery under the artifact lock. No partially published set becomes eligible. |
| Recovered | Roll-forward completed, the commit record and catalog were published, and the journal was exclusively retired into non-authoritative staging with a `.completed` suffix. This is an observed outcome, not a stored state name. | The newly current entries become eligible on that same access. The retained journal bytes are internal evidence, not a listed artifact or pending transaction. |
| Recovery blocked | A pending journal, target, or recovery invariant could not be trusted, and a persistent block marker was written. | Every later artifact-repository read or commit fails closed. Preserve the workspace and investigate or restore a verified backup; there is no automatic repair command. |
| Retained staging residue | A `.tmp`, `.held`, `.retired`, or `.completed` file remains under the repository's non-authoritative `staging/` directory. | This is normal internal state and never appears in the canonical catalog or agent context. It may grow over time; do not treat it as a writing artifact or move it into `transactions/`. |

A manual edit invalidates eligibility without deleting the file. Restoring the
exact recorded bytes also restores hash eligibility when the catalog and commit
record are still intact. Otherwise, let a later unanimous run republish a core
document. For a changed manuscript, use a new part-scoped revision path. Stop
all workspace processes before any external/manual filesystem edit; live manual
namespace mutation is outside the supported writer boundary.

Do not edit the catalog, commit records, transaction journals, lock, or recovery
marker to force a state. They are consistency evidence, not user-authored
documents.

## Publication and recovery mechanism

`WritingArtifactRepository` is the only application write path for canonical
artifacts. Generic `Workspace.write_text()` and `write_json()` reject all four
core paths, every canonical manuscript path, and case-folded or
Unicode-normalized aliases of those reserved namespaces before mutating the
filesystem. Repository construction does no artifact I/O. The first catalog
read, artifact read, or commit checks for the
required descriptor-relative, no-follow, listing, and advisory-lock primitives,
takes an in-process lock and a workspace-local lease, then resolves pending
recovery before continuing. Other workspace and runtime APIs remain usable on a
host that cannot perform artifact I/O.

The lease is a permanent paired hard-link identity for the repository lock, not
an artifact. Both names, the open descriptor, the retained directory chain, and
the workspace root are revalidated around mutation. Canonical artifact files
themselves must still be unique regular files with one link.

One successful commit follows this order:

1. Validate the output grammar, strict-UTF-8 agent text, four-name core
   allowlist, story-agent authorship, session/debate/revision identities, and
   exact repository JSON schemas. Duplicate object keys are rejected at every
   nesting level.
2. Reject any existing manuscript target before publication.
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
forward and publishes the catalog last. At catalog-after, every target and the
immutable commit must already equal the recorded new state; recovery only
verifies them and retires the journal. A catalog matching neither position, an
untrusted target, a missing/replaced journal, or a missing/divergent commit in
catalog-after state blocks recovery durably rather than guessing. Catalog-last
ordering means an
interrupted target set is never advertised as partly eligible.

This transaction covers its manuscript, changed core documents, commit record,
and catalog. It does not form one transaction with debate messages, lifecycle
state, result JSON, conversations, configuration, or provider state. Inspect
the debate after an infrastructure interruption even when artifact recovery
succeeds.

## Provenance and work history

Each current catalog entry records the exact workspace-relative path, artifact
kind, SHA-256, commit ID and timestamp, author agent, session ID, debate ID, and
revision ID. The matching immutable commit record groups exactly one manuscript
and any core updates from that publication. Transaction targets, commit
artifacts, and all catalog entries for that commit must contain the same exact
path/kind/author/hash set; their commit and correlation metadata must agree.
Schema fields and scalar types are exact rather than extensible.

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
