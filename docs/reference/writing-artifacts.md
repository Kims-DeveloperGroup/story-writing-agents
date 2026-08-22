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

Commands and Python calls accept only the portion after `manuscript/` as
`output_path`. For example, `part-01/scenes/opening.md` publishes
`manuscript/part-01/scenes/opening.md`.

`PART/PATH.md` is required. It is a normalized, workspace-relative POSIX path:

- `PART` begins with an ASCII letter or digit and then uses at most 63 ASCII
  letters, digits, `_`, or `-` characters;
- every later component begins with an ASCII letter or digit and then uses at
  most 127 ASCII letters, digits, `_`, `-`, or `.` characters;
- the final component ends in `.md` and is not just `.md`;
- the complete UTF-8 path is at most 1,024 bytes; and
- absolute paths, `manuscript/`-prefixed inputs, backslashes, empty, `.` or `..`
  components, non-normalized forms, and non-Markdown targets are rejected.

There is no root manuscript form, generated filename, compatibility alias, or
automatic migration. `chapter.md` and `manuscript/part-01/chapter.md` are both
invalid `output_path` values; use `part-01/chapter.md`.

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

Manuscripts are create-only. To revise one, supply the current text as draft
input and choose a distinct path such as
`part-01/revisions/opening-r2.md`. The runtime never overwrites the earlier
manuscript. Core documents, by contrast, keep their four stable identities and
can be replaced by a later unanimous publication.

## Eligibility states users observe

A canonical-looking path is not enough. The catalog exposes only current,
agent-authored bytes whose publication history still validates.

| State | Internal meaning | What users can do or see |
| --- | --- | --- |
| Absent | No file and no current catalog entry. | The file is not supplied as agent context. A later unanimous run may create it. |
| Manual or unprovenanced | Bytes exist, but no matching repository-owned catalog and commit record exists. | The bytes remain on disk but are excluded from the eligible catalog and core prompt context. A manuscript at that path also blocks create-only publication. |
| Current and eligible | The path is canonical, its catalog and commit provenance agree, and its current SHA-256 matches. | The runtime may list/read it through the artifact repository. Eligible core documents are supplied only to their allowed agents. |
| Stale and ineligible | A catalog entry exists, but the file is missing, unsafe, nonregular, or its bytes no longer match the recorded hash. | Catalog listing omits it; an exact artifact read fails. The runtime does not delete or silently repair the bytes. |
| Recovery pending | A validated transaction journal remains in `prepared` or `artifacts_published`. | The next catalog read, artifact read, or commit first attempts deterministic roll-forward under the artifact lock. No partially published set becomes eligible. |
| Recovered | Roll-forward completed, the commit record and catalog were published, and the journal was removed. This is an observed outcome, not a stored state name. | The newly current entries become eligible on that same access. |
| Recovery blocked | A pending journal, target, or recovery invariant could not be trusted, and a persistent block marker was written. | Every later artifact-repository read or commit fails closed. Preserve the workspace and investigate or restore a verified backup; there is no automatic repair command. |

A manual edit invalidates eligibility without deleting the file. Restoring the
exact recorded bytes also restores hash eligibility when the catalog and commit
record are still intact. Otherwise, let a later unanimous run republish a core
document. For a changed manuscript, use a new part-scoped revision path.

Do not edit the catalog, commit records, transaction journals, lock, or recovery
marker to force a state. They are consistency evidence, not user-authored
documents.

## Publication and recovery mechanism

`WritingArtifactRepository` owns this boundary independently of generic
`Workspace` writes. Every public catalog read, artifact read, or consensus
commit takes an in-process lock and a workspace-local advisory artifact lock,
then resolves pending recovery before continuing.

One successful commit follows this order:

1. Validate the output grammar, strict-UTF-8 agent text, four-name core
   allowlist, story-agent authorship, and session/debate/revision identities.
2. Reject any existing manuscript target before publication.
3. Record old and new hashes, complete new bytes, authors, and the catalog-after
   image in a create-only `prepared` transaction journal.
4. Publish and verify every target using descriptor-relative, no-follow,
   compare-before-replace I/O.
5. Change the journal to `artifacts_published`.
6. Publish the immutable commit record, then publish the catalog last.
7. Remove the completed journal.

If the process stops after step 3, the next access validates each target. A
target may equal either its recorded old bytes, in which case recovery publishes
the new bytes, or its recorded new bytes, in which case recovery continues. Any
other content is divergence and blocks recovery. Catalog-last ordering means an
interrupted target set is never advertised as partly eligible.

This transaction covers its manuscript, changed core documents, commit record,
and catalog. It does not form one transaction with debate messages, lifecycle
state, result JSON, conversations, configuration, or provider state. Inspect
the debate after an infrastructure interruption even when artifact recovery
succeeds.

## Provenance and work history

Each current catalog entry records the exact workspace-relative path, artifact
kind, SHA-256, commit ID and timestamp, author agent, session ID, debate ID, and
revision ID. The matching immutable commit record groups the one manuscript and
any core updates from that publication. Candidate authorship is the execution
specialist unless a conflict cycle caused the lead to synthesize the approved
candidate; core-document updates are attributed to the lead.

This repository-owned metadata is the artifact work history used for
eligibility. The JSON lives under
`agent_states/orchestrator_state/writing_artifacts/` and remains plaintext. It
is deliberately excluded from the canonical produced-output set.

## Filesystem and privacy limits

Artifact I/O requires the host's safe descriptor-relative, no-follow directory
operations, unique regular files, and advisory locking. Symlinks, hard links,
directories, other nonregular targets, workspace-root replacement, unsafe path
components, and files changed while being read fail closed. Artifact text is
strict UTF-8. The repository does not encrypt content, provenance, or journals,
provide secure erase, or enforce a retention policy.

The catalog and repository are module-supported internals used by the runtime;
they are not re-exported from the package root. Exact run signatures are in
[Runtime API](runtime-api.md), workspace topology in
[Session storage](configuration/session-storage.md), filesystem consequences in
[Filesystem safety](../operations/filesystem-safety.md), and debate sequencing
in [Consensus workflow](../design/consensus-workflow.md).
