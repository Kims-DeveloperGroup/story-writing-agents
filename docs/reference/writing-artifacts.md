# Canonical writing artifacts

> **Availability:** the `writing_artifacts` subpackage retains canonical path,
> source discovery/read, reference authorization/evidence, provenance,
> publication, and recovery components. Python and interactive Lead input
> attach `${path}` references. A separately confirmed interactive Apply can
> publish the exact reviewed change; Lead conversation alone cannot. Main-input
> `$`/`$$` activates canonical selection and ranged viewing. Existing artifacts and
> reference-operation evidence remain inspectable through direct components.
> The pure exact change-set and relationship-policy contracts documented below
> remain available independently of their installed runtime callers.

This reference uses **writing artifact path** for the canonical story-output
topology. A current source at one of those paths may be manual or
pre-provenance; only repository publication records establish agent-produced
authorship.
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

The publication component accepts the `PART/PATH.md` suffix as `output_path`.
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

## Exact in-memory artifact change sets

> **Availability:** schema-v1 artifact change sets are an implemented, pure
> Python value contract. They validate and project proposed bytes in memory.
> They do not read or write a workspace, ask a provider or lead agent for a
> decision, record confirmation, apply a proposal, or create a Git commit.

This API gives a caller one exact, reviewable description of how a complete
canonical artifact state would change. It is useful for building and checking
a proposal before any component with write authority acts.

| A caller can | A caller cannot |
| --- | --- |
| Describe creation, byte-exact patching, full replacement, rename, or deletion of canonical core documents and manuscripts. | Infer the current files from a workspace or mutate any file. The caller must supply the complete base mapping. |
| Verify the exact base state and inspect a read-only projected mapping. | Treat projection as approval, user confirmation, provider output, publication, or Git authority. |
| Serialize the complete review content to one canonical JSON byte sequence and restore it exactly. | Expect ordinary representations or errors to reveal story text. Explicit serialization is the content-bearing review surface. |
| Represent a core-document rename or deletion as a pure value. | Assume that operation is permitted by artifact policy. The implemented relationship policy rejects those operations before a proposal can be considered valid. |

Relationship validation is also outside this change-set layer. For example,
renaming a manuscript does not find or repair Markdown links. The caller must
describe each inbound-reference patch separately, then pass the complete
change set to the implemented
[relationship validator](#canonical-artifact-relationship-validation). Later
lead-review and apply workflows may consume these values, but this API itself
grants none of their authority.

<a id="artifact-application-contracts"></a>

## Exact artifact-application contracts

> **Availability:** schema-v1 artifact-application requests and results are an
> implemented, pure Python value/codec contract. The module-supported internal
> `WritingArtifactRepository.apply_artifact_changes()` seam now accepts one
> exact request and may persist it through the private #519 store. Neither the
> value contract nor repository seam asks a user or agent for confirmation,
> calls a provider, changes prompt context, or stages or commits Git. They are
> not standalone installed commands or provider/lead capabilities. The runtime
> invokes this seam only after its separate exact Apply decision boundary.

The installed Lead workflow creates an `ArtifactApplicationRequest` for one
reviewed candidate and invokes the repository only after the user separately
confirms **Apply**. The request
then carries the exact content-bearing `ArtifactChangeSet` that the repository
may apply. Possessing, constructing, or deserializing this value is not proof of
confirmation and grants no provider, agent, or Git authority.

### Exact correlation

The immutable provenance keeps the later application tied to the review facts:

| Field | Meaning |
| --- | --- |
| `application_id` | Identity of this application attempt. |
| `candidate_id` and `candidate_revision` | Exact reviewed candidate and its positive revision. |
| `candidate_digest` | Tagged `sha256:<64-lowercase-hex>` identity of that candidate. |
| `change_set_digest` | Untagged 64-lowercase-hex digest that must exactly equal the embedded change set's digest. |
| `relationship_validation_digest` | Tagged `sha256:<64-lowercase-hex>` identity of the separate relationship-validation result. |
| `attached_core_paths` | Exact UTF-8-sorted, duplicate-free tuple of canonical core paths whose evidence informed review; it participates in relationship validation and its digest. |

The contract validates shapes and the embedded change-set correlation. It does
not look up a candidate or determine whether Apply was confirmed. The repository
does reread the complete canonical state and recompute the relationship result
under its lease before writing; runtime remains responsible for creating
authority only after confirmation.

### Public Python surface

The 14 additive names are exported from
`story_writing_agents.writing_artifacts`, not the project-root facade:

```python
from story_writing_agents.writing_artifacts import (
    ARTIFACT_APPLICATION_SCHEMA_VERSION,
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationFinalArtifact,
    ArtifactApplicationProvenance,
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    ArtifactDeletionProvenance,
    ArtifactRenameLineage,
    deserialize_artifact_application_request,
    deserialize_artifact_application_result,
    serialize_artifact_application_request,
    serialize_artifact_application_result,
)
```

All values are frozen and slotted. The schema version is exactly `1`.

The application method is available only from the repository module; the
repository class is not re-exported by either package facade:

```python
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

result = WritingArtifactRepository(workspace_root).apply_artifact_changes(request)
```

The method accepts exactly `ArtifactApplicationRequest`, not prose, a JSON
dictionary, a mutable wrapper, or a subclass. It revalidates the frozen value
before taking one repository lock and workspace lease, then delegates once to
the private store. There is no alternate write method for Reject or Revise.

### Sample input: all five operation representations

This readable rendering shows one correlated request. The neutral text is
fixture data. The actual serializer emits the same object with sorted keys,
compact separators, strict UTF-8, and no trailing newline.

```json
{
  "attached_core_paths": ["core_documents/narrative_rules.md"],
  "schema_version": 1,
  "provenance": {
    "application_id": "application-1",
    "candidate_id": "candidate-1",
    "candidate_revision": 2,
    "candidate_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "change_set_digest": "caf20a51e1a6616d71cc26f985d7cf52b7c121f04bf41c34289753bfd20ae48e",
    "relationship_validation_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "change_set": {
    "schema_version": 1,
    "base_state_digest": "2296df72bad3d5e982a685cbbb0888a8340b4f38f8836bb923fb986dbb54cac9",
    "projected_state_digest": "da05330a32cc957c6888f121f8cfdf7995c396d664cd8cd6d6340ae80f263d21",
    "change_set_digest": "caf20a51e1a6616d71cc26f985d7cf52b7c121f04bf41c34289753bfd20ae48e",
    "operations": [
      {
        "operation": "create",
        "kind": "core_document",
        "path": "core_documents/character_sheets.md",
        "content": "created\n",
        "projected_digest": "59134a4054b27a3fc30e1ac81d9b9168dc0561f65982151324a021fe8ce88d06"
      },
      {
        "operation": "patch",
        "kind": "core_document",
        "path": "core_documents/global_outline.md",
        "base_digest": "01d09d19c2139a46aebfb577780d123d7396e97201bc7ead210a2ebff8239dee",
        "hunks": [{
          "base_digest": "01d09d19c2139a46aebfb577780d123d7396e97201bc7ead210a2ebff8239dee",
          "start_utf8_byte": 0,
          "end_utf8_byte": 4,
          "old_text": "old\n",
          "new_text": "new\n"
        }],
        "projected_digest": "7aa7a5359173d05b63cfd682e3c38487f3cb4f7f1d60659fe59fab1505977d4c"
      },
      {
        "operation": "rename",
        "kind": "manuscript",
        "source_path": "manuscript/part-01/move.md",
        "destination_path": "manuscript/part-01/moved.md",
        "base_digest": "c366d780a7cee327edc8444ea0b2ccecbf7f52422c3715061a440bff95914679",
        "projected_digest": "c366d780a7cee327edc8444ea0b2ccecbf7f52422c3715061a440bff95914679"
      },
      {
        "operation": "replace",
        "kind": "manuscript",
        "path": "manuscript/part-01/opening.md",
        "base_digest": "9160d4be34c8695bd172a76c7c7966587ea5a4d991ad22c87b2b91af54aa9ebb",
        "content": "after\n",
        "projected_digest": "7b9a72466d3960eb2aacccfc848939453490db0678bd4725def3f789b891c919"
      },
      {
        "operation": "delete",
        "kind": "manuscript",
        "path": "manuscript/part-01/retired.md",
        "base_digest": "2ab99e684919afa1245556d366a2018d1dd0bd63b1b66fb92cde34ec5c4d60c6"
      }
    ]
  }
}
```

The order is the exact UTF-8 ordering of each operation's primary path. The
serialized request intentionally contains create/replace bodies and patch
old/new text. Treat those bytes as story content: do not log or expose the wire
value merely because the provenance fields are content-free.

### Sample output: applied repository facts

When `WritingArtifactRepository.apply_artifact_changes(request)` applies that
request, its immutable terminal result has no story bodies. An
`applied` result means this invocation changed the affected paths. It identifies
every final surviving artifact, represents both sides of a manuscript rename,
and retains provenance for a separately deleted manuscript. This is a readable
rendering; the result encoder emits the same facts as compact canonical JSON:

```json
{
  "schema_version": 1,
  "state": "applied",
  "affected_paths": [
    "core_documents/character_sheets.md",
    "core_documents/global_outline.md",
    "manuscript/part-01/move.md",
    "manuscript/part-01/moved.md",
    "manuscript/part-01/opening.md",
    "manuscript/part-01/retired.md"
  ],
  "final_artifacts": [
    {"artifact_kind": "core_document", "path": "core_documents/character_sheets.md", "sha256": "59134a4054b27a3fc30e1ac81d9b9168dc0561f65982151324a021fe8ce88d06"},
    {"artifact_kind": "core_document", "path": "core_documents/global_outline.md", "sha256": "7aa7a5359173d05b63cfd682e3c38487f3cb4f7f1d60659fe59fab1505977d4c"},
    {"artifact_kind": "manuscript", "path": "manuscript/part-01/moved.md", "sha256": "c366d780a7cee327edc8444ea0b2ccecbf7f52422c3715061a440bff95914679"},
    {"artifact_kind": "manuscript", "path": "manuscript/part-01/opening.md", "sha256": "7b9a72466d3960eb2aacccfc848939453490db0678bd4725def3f789b891c919"}
  ],
  "rename_lineage": [{
    "source_path": "manuscript/part-01/move.md",
    "destination_path": "manuscript/part-01/moved.md",
    "sha256": "c366d780a7cee327edc8444ea0b2ccecbf7f52422c3715061a440bff95914679"
  }],
  "deletion_provenance": [{
    "path": "manuscript/part-01/retired.md",
    "prior_sha256": "2ab99e684919afa1245556d366a2018d1dd0bd63b1b66fb92cde34ec5c4d60c6"
  }],
  "provenance": {
    "application_id": "application-1",
    "candidate_id": "candidate-1",
    "candidate_revision": 2,
    "candidate_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "change_set_digest": "caf20a51e1a6616d71cc26f985d7cf52b7c121f04bf41c34289753bfd20ae48e",
    "relationship_validation_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}
```

`final_artifacts` excludes the retired rename source and deleted path.
`rename_lineage` proves that the manuscript identity moved without changing
bytes. `deletion_provenance` records the deleted manuscript's prior digest. The
same result topology is required for both terminal states.

`unchanged` does not mean that the reviewed change set was empty: change sets
cannot be no-ops. It means this invocation was an idempotent retry and the exact
application was already complete. Such a result returns the original affected
paths, final artifacts, rename lineage, deletion provenance, and provenance;
`applied_paths` is empty while `unchanged_paths` contains every affected path.
This preserves historical truth without falsely claiming the retry rewrote a
file.

### Strict decoding, rejection, and privacy

Both codecs accept only exact `bytes`, strict UTF-8, one canonical sorted-key
JSON object, exact fields, unique keys, and no trailing newline. Unknown,
missing, duplicate, pretty-printed, reordered, noncanonical-escape, `NaN`, or
`Infinity` input is rejected. Requests also revalidate their complete embedded
change set; results require sorted unique canonical paths and a complete,
nonoverlapping final/rename/deletion topology.

The closed error codes are `invalid_schema`, `invalid_serialization`,
`invalid_utf8`, `invalid_identifier`, `invalid_digest`, `invalid_path`,
`correlation_mismatch`, `invalid_result`, `drift`, `corruption`, and
`recovery_required`. They distinguish outer shape or version, wire encoding,
identifier/digest/path facts, request correlation, result topology, stale
authority, invalid durable state, and operator recovery without echoing story
bytes or retaining an underlying exception graph.

For example, changing only the provenance value above to
`change_set_digest="0000000000000000000000000000000000000000000000000000000000000000"`
while leaving the embedded digest unchanged raises:

```text
error.code: correlation_mismatch
message: artifact application correlation does not match
filesystem writes: 0
```

The failure does not retain the rejected payload in its exception cause or
context. Request representations, result serialization, result values, and all
fixed errors are content-free; only explicit request serialization contains
story text. The pure codec functions still log and persist nothing. The
repository seam performs local artifact persistence only: it has no Git,
network, provider, agent, confirmation, prompt-context, clock, or random
responsibility. There is no migration or compatibility reader for the earlier
request encoding without `attached_core_paths`; schema `1` with the current
exact canonical fields is the only accepted form.

<a id="story-commit-application-evidence"></a>

### Story-commit application evidence

The package-internal #509 story-commit service consumes a typed
`StoryCommitApplicationEvidence` only after some owning workflow has applied the
exact change set successfully. The installed Lead runtime produces that
evidence internally; it is not a public writing-artifacts export. Applying
artifact files and committing them remain separate operations: Apply never
stages or commits, and a change-set value or application receipt is not user
confirmation or Git authority.

The evidence binds the session/application/proposal/correlation IDs, complete
`ArtifactChangeSet`, and its exact projected-state digest. Candidate preparation
then rereads the complete canonical artifact state and requires it to equal that
projection before publishing content-free candidate metadata. An extra
canonical edit, missing applied edit, digest mismatch, unsafe path, pre-staged
index entry, or moved session branch fails closed. Noncanonical untracked files
are excluded and preserved.

The commit candidate maps application operations to Git path effects without
carrying their content:

| Change-set operation | Candidate path effect |
| --- | --- |
| `create` | One `create` with `after_sha256` |
| `patch` or `replace` | One `update` with distinct `before_sha256` and `after_sha256` |
| `delete` | One `delete` with `before_sha256` |
| `rename` | Source `delete` plus destination `create`, sorted with all paths by UTF-8 bytes |

Only the four canonical core documents and valid manuscript paths can enter
this mapping. During a later exact Commit decision, #509 rereads the current
after-bytes through the artifact repository, checks those digests again, and
passes them only to literal filter-free blob/index operations. Reject preserves
the applied files and candidate. Revise or drift requires newly prepared
evidence/candidate and fresh confirmation. See the
[user flow](../guides/workspaces.md#exact-confirmed-story-commits) and
[internal Git contract](git-adapter.md#package-internal-exact-story-commits).

### Public Python surface

Import the 16 change-set symbols from the writing-artifacts facade, not the
project-root facade:

```python
from story_writing_agents.writing_artifacts import (
    ARTIFACT_CHANGE_SET_SCHEMA_VERSION,
    ArtifactChangeOperation,
    ArtifactChangeOperationKind,
    ArtifactChangeSet,
    ArtifactChangeSetError,
    ArtifactChangeSetErrorCode,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    deserialize_artifact_change_set,
    project_artifact_change_set,
    serialize_artifact_change_set,
)
```

The schema version is exactly `1`. The value types are frozen, slotted
dataclasses with these exact fields:

```text
ArtifactTextHunk(base_digest, start_utf8_byte, end_utf8_byte, old_text, new_text)
ArtifactCreateOperation(path, artifact_kind, content, projected_digest)
ArtifactPatchOperation(path, artifact_kind, base_digest, hunks, projected_digest)
ArtifactReplaceOperation(path, artifact_kind, base_digest, content, projected_digest)
ArtifactRenameOperation(source_path, destination_path, artifact_kind, base_digest, projected_digest)
ArtifactDeleteOperation(path, artifact_kind, base_digest)
ArtifactChangeSet(schema_version, base_state_digest, operations, projected_state_digest, change_set_digest)
```

`ArtifactChangeOperation` is the union of the five operation values. Each
operation's `operation_kind` is the corresponding `create`, `patch`, `replace`,
`rename`, or `delete` enum member. The four functions have these signatures:

```python
build_artifact_change_set(
    base_state: Mapping[str, bytes],
    operations: tuple[ArtifactChangeOperation, ...],
) -> ArtifactChangeSet

project_artifact_change_set(
    base_state: Mapping[str, bytes],
    change_set: ArtifactChangeSet,
) -> Mapping[str, bytes]

serialize_artifact_change_set(change_set: ArtifactChangeSet) -> bytes
deserialize_artifact_change_set(data: bytes) -> ArtifactChangeSet
```

The returned projection is a `MappingProxyType` ordered by path UTF-8 bytes.
The functions do not mutate either the caller's base mapping or the contained
byte values.

### Exact matching and ordering rules

A hunk uses zero-based, half-open `[start_utf8_byte, end_utf8_byte)` offsets
over the **original base bytes**. Both offsets must be UTF-8 code-point
boundaries, `old_text` must equal that exact byte slice, and every hunk must
carry the operation's exact lowercase SHA-256 `base_digest`. All text values
are strict UTF-8 bytes. There is no fuzzy matching or newline, Unicode, BOM, or
NUL normalization; CRLF versus LF and composed versus decomposed characters
remain different bytes.

Hunks are caller-canonical in ascending `(start_utf8_byte, end_utf8_byte)`
order and are all evaluated against the same original base. Adjacent hunks are
valid. One zero-width insertion immediately before a consuming hunk at the
same offset is valid. Overlap, a second insertion at the same anchor, an empty
hunk list, or any hunk whose old and new bytes are equal fails closed.

| Operation | Exact base rule | Projected result |
| --- | --- | --- |
| `create` | The path is absent from the original base. | Adds the supplied content whose digest is `projected_digest`. |
| `patch` | The path is present and its content digest equals `base_digest`. | Applies the ordered original-base hunks; the result must equal `projected_digest`. |
| `replace` | The path is present and its content digest equals `base_digest`. | Replaces it with different supplied bytes whose digest is `projected_digest`. |
| `rename` | The source is present; the distinct destination is absent; both paths have the same `ArtifactKind`; and `base_digest == projected_digest`. | Moves the source bytes unchanged. |
| `delete` | The path is present and its content digest equals `base_digest`. | Removes the path. |

Operations must be a nonempty tuple ordered by each operation's primary path
(the rename source) as UTF-8 bytes. A path may participate in only one
operation, including either side of a rename. Rename chains, swaps, and cycles
are invalid because a rename source may not also be a rename destination.
Every operation is evaluated against the original base rather than against a
preceding operation's projection. No individual operation or complete change
set may be a no-op.

### Scenario: patch a character sheet exactly

This minimal fixture changes only `scout` to `captain`. The byte offsets are
17 through 22 in the original UTF-8 bytes:

```python
import hashlib

from story_writing_agents.writing_artifacts import (
    ArtifactKind,
    ArtifactCreateOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    deserialize_artifact_change_set,
    project_artifact_change_set,
    serialize_artifact_change_set,
)

sha256 = lambda value: hashlib.sha256(value).hexdigest()
path = "core_documents/character_sheets.md"
before = b"Name: Mara\nRole: scout\n"
after = b"Name: Mara\nRole: captain\n"
base = {path: before}
base_digest = sha256(before)

hunk = ArtifactTextHunk(
    base_digest=base_digest,
    start_utf8_byte=17,
    end_utf8_byte=22,
    old_text=b"scout",
    new_text=b"captain",
)
operation = ArtifactPatchOperation(
    path=path,
    artifact_kind=ArtifactKind.CORE_DOCUMENT,
    base_digest=base_digest,
    hunks=(hunk,),
    projected_digest=sha256(after),
)
change_set = build_artifact_change_set(base, (operation,))
wire = serialize_artifact_change_set(change_set)
restored = deserialize_artifact_change_set(wire)
projected = project_artifact_change_set(base, restored)

print(base_digest)
print(change_set.base_state_digest)
print(change_set.projected_state_digest)
print(change_set.change_set_digest)
print(dict(projected))
print(restored == change_set, wire.endswith(b"\n"))
```

Output:

```text
99570638f3e420feb6978de30a17c2cacd90072ee55277f3a6becebe68482f7d
76d6735233da0c88f361e50fd6fe27e240ecb3405657c0ec67474d57e6273cfa
a25050ae167f12dad6f3c620542586a10490fb7502e5a87efb1154df8f936c8b
de78ee07ff0f501ebba7c86cf453d0cffef468c752a2bab5a41da715ba6d26f1
{'core_documents/character_sheets.md': b'Name: Mara\nRole: captain\n'}
True False
```

The exact canonical `wire` value is one UTF-8 line with sorted object keys,
compact separators, and no trailing newline:

```json
{"base_state_digest":"76d6735233da0c88f361e50fd6fe27e240ecb3405657c0ec67474d57e6273cfa","change_set_digest":"de78ee07ff0f501ebba7c86cf453d0cffef468c752a2bab5a41da715ba6d26f1","operations":[{"base_digest":"99570638f3e420feb6978de30a17c2cacd90072ee55277f3a6becebe68482f7d","hunks":[{"base_digest":"99570638f3e420feb6978de30a17c2cacd90072ee55277f3a6becebe68482f7d","end_utf8_byte":22,"new_text":"captain","old_text":"scout","start_utf8_byte":17}],"kind":"core_document","operation":"patch","path":"core_documents/character_sheets.md","projected_digest":"6bc9739b048a6b54c61a23642f1736cdf13fdee1a8475ecceb590eae459f6022"}],"projected_state_digest":"a25050ae167f12dad6f3c620542586a10490fb7502e5a87efb1154df8f936c8b","schema_version":1}
```

This output is only an in-memory proposal. The character-sheet file is still
unchanged on disk, and no user confirmation has been requested or granted.

### Scenario: create a manuscript

Creation binds both the required absence and the new bytes. This and the next
scenario reuse the imports and `sha256` helper above:

```python
content = b"Scene placeholder.\n"
path = "manuscript/part-02/opening.md"
create = ArtifactCreateOperation(
    path=path,
    artifact_kind=ArtifactKind.MANUSCRIPT,
    content=content,
    projected_digest=sha256(content),
)
creation = build_artifact_change_set({}, (create,))
print(dict(project_artifact_change_set({}, creation)))
```

```text
{'manuscript/part-02/opening.md': b'Scene placeholder.\n'}
```

If that path is present in the supplied base, building fails with
`base_artifact_mismatch`. Projection still does not create the file.

### Scenario: rename a manuscript and patch an inbound link

A rename preserves bytes but does not discover relationships. Here the caller
supplies a separate patch to the index that names the manuscript:

```python
index_path = "manuscript/part-01/index.md"
source_path = "manuscript/part-01/opening.md"
destination_path = "manuscript/part-01/arrival.md"
index_before = b"[Opening](opening.md)\n"
index_after = b"[Opening](arrival.md)\n"
scene = b"# Opening\n"
index_digest = sha256(index_before)
scene_digest = sha256(scene)

link_patch = ArtifactPatchOperation(
    path=index_path,
    artifact_kind=ArtifactKind.MANUSCRIPT,
    base_digest=index_digest,
    hunks=(ArtifactTextHunk(
        base_digest=index_digest,
        start_utf8_byte=10,
        end_utf8_byte=20,
        old_text=b"opening.md",
        new_text=b"arrival.md",
    ),),
    projected_digest=sha256(index_after),
)
rename = ArtifactRenameOperation(
    source_path=source_path,
    destination_path=destination_path,
    artifact_kind=ArtifactKind.MANUSCRIPT,
    base_digest=scene_digest,
    projected_digest=scene_digest,
)
base = {index_path: index_before, source_path: scene}
proposal = build_artifact_change_set(base, (link_patch, rename))
print(dict(project_artifact_change_set(base, proposal)))
```

```text
{'manuscript/part-01/arrival.md': b'# Opening\n', 'manuscript/part-01/index.md': b'[Opening](arrival.md)\n'}
```

The operation order is `index.md` then `opening.md`, the UTF-8 ordering of the
primary paths. The pure change-set layer also accepts the rename without the
link patch. The relationship validator below distinguishes the dangling
rename from this repaired proposal. Nothing in this scenario changes either
file on disk.

### Complete-state digests, serialization, and failures

The base and projected state digests cover the complete supplied mapping, not
only changed paths. Their canonical manifest has this shape, with artifacts
sorted by path UTF-8 bytes:

```json
{"artifacts":[{"content_digest":"<lowercase SHA-256>","kind":"core_document","path":"core_documents/character_sheets.md","utf8_bytes":23}],"schema_version":1}
```

The digest is the SHA-256 of those canonical JSON bytes. An unrelated path
addition, removal, or byte change therefore causes `base_state_mismatch` at
projection. `change_set_digest` is the SHA-256 of the canonical change-set
object without its own `change_set_digest` field. It is content identity only:
it carries no user, agent, session, confirmation, authority, timestamp, or Git
metadata.

`serialize_artifact_change_set()` is intentionally the explicit review
surface: it includes create/replace content and patch old/new text. Ordinary
dataclass representations omit those direct content fields, and every
`ArtifactChangeSetError` has a fixed content-free message. Do not place
serialized bytes in an ordinary diagnostic channel. Deserialization requires
exact `bytes`, strict UTF-8, unique keys at every nesting level, exact fields,
and byte-identical canonical JSON. Pretty printing, a trailing newline,
duplicate or unknown fields, noncanonical key/spacing/escape forms, and
`NaN`/`Infinity` are rejected.

Typical closed failures are:

| Input | Observable result |
| --- | --- |
| Project the character-sheet change set over a mapping with changed or extra bytes. | `error.code is ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH` |
| Build a patch after its target bytes drift, or give a hunk the wrong exact `old_text`. | `BASE_ARTIFACT_MISMATCH`, with the operation and, when applicable, hunk index |
| Supply consuming ranges `[0, 5)` and `[4, 7)`. | `HUNK_OVERLAP`, `hunk_index == 1` |
| Patch and replace the same path in one operation tuple. | `PATH_COLLISION`, `operation_index == 1` |
| Rename onto an existing base path, create an existing path, or use a missing source. | `RENAME_CONFLICT` or `BASE_ARTIFACT_MISMATCH`, according to the violated rule |

The stable error-code values are:

```text
invalid_schema, invalid_serialization, invalid_utf8, invalid_path,
invalid_kind, invalid_digest, invalid_hunk, invalid_byte_boundary, no_op,
hunk_order, hunk_overlap, duplicate_insertion, operation_order,
base_state_mismatch, base_artifact_mismatch, projected_digest_mismatch,
path_collision, rename_conflict, change_set_digest_mismatch
```

`ArtifactChangeSetError` is a `RuntimeError` with the exact constructor
`ArtifactChangeSetError(code, *, operation_index=None, hunk_index=None)`. It
exposes the enum `code`, optional zero-based `operation_index`, and optional
zero-based `hunk_index`. A failed build or projection has no partial result and
performs no recovery or retry. The caller must acquire current bytes and build
a new exact proposal; this pure API does not read those bytes itself.

## Canonical artifact relationship validation

> **Availability:** schema-v1 relationship validation is an implemented, pure
> in-memory Python contract over a complete base mapping and one exact change
> set. It does not read or write files, invoke an agent or provider, select a
> specialist, request or record confirmation, apply or stage a proposal, or
> create a Git commit.

The relationship policy answers two artifact-only questions: whether the
projected canonical graph has any links to absent canonical artifacts, and
which story-review domains the proposed operations and supplied core-evidence
facts affect. It returns immutable facts for a later workflow to interpret;
it does not produce a user-facing answer itself.

| Internal mechanism | User-side meaning |
| --- | --- |
| Project the exact change set over the caller-supplied complete base state. | The result describes what the artifacts would look like; no workspace changed. |
| Parse the base and projected canonical link graphs and compare them. | A caller can show every added, removed, retained, or still-dangling link instead of silently accepting a broken rename or delete. |
| Reject rename/delete operations on the four permanent core identities. | A missing core document may be created and a present one may be patched or replaced, but its canonical identity cannot be moved or removed. |
| Derive content-free domain/reason facts. | The reviewed-candidate workflow can explain which kinds of review are relevant, but this layer names no agent and dispatches nobody. |

### Public Python surface and limits

The relationship module exports exactly 15 symbols, all re-exported from the
writing-artifacts facade and absent from the project-root facade:

```python
from story_writing_agents.writing_artifacts import (
    ARTIFACT_RELATIONSHIP_COMPANION_RULES,
    ARTIFACT_RELATIONSHIP_SCHEMA_VERSION,
    MAX_RELATIONSHIP_ARTIFACTS,
    MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
    MAX_RELATIONSHIP_EDGES,
    MAX_RELATIONSHIP_STATE_UTF8_BYTES,
    ArtifactRelationshipEdge,
    ArtifactRelationshipError,
    ArtifactRelationshipErrorCode,
    ArtifactRelationshipKind,
    ArtifactRelationshipValidation,
    ArtifactReviewDomain,
    ArtifactReviewFact,
    ArtifactReviewReason,
    validate_artifact_relationships,
)
```

The schema version is `1`, and the companion-rule set is the exact empty tuple
`()`. The fixed inclusive limits apply independently to both the base and
projected state:

| Limit | Exact value |
| --- | ---: |
| Canonical artifacts | 4,096 |
| Strict-UTF-8 bytes in one artifact | 8 MiB |
| Strict-UTF-8 bytes across one complete state | 64 MiB |
| Parsed canonical relationship edges in one state | 65,536 |

The three result values are frozen, slotted dataclasses with these exact
fields:

```text
ArtifactRelationshipEdge(
    source_path, target_path,
    destination_start_utf8_byte, destination_end_utf8_byte,
    fragment, relationship_kind,
)
ArtifactReviewFact(domain, reason, artifact_path, operation_index)
ArtifactRelationshipValidation(
    schema_version, base_state_digest, projected_state_digest,
    change_set_digest, added_edges, removed_edges, retained_edges,
    projected_dangling_edges, review_facts,
)
```

`ArtifactRelationshipValidation.is_valid` is `True` exactly when
`projected_dangling_edges` is empty. The validation entry point is:

```python
validate_artifact_relationships(
    base_state: Mapping[str, bytes],
    change_set: ArtifactChangeSet,
    *,
    attached_core_paths: tuple[str, ...],
) -> ArtifactRelationshipValidation
```

`attached_core_paths` must be an exact, duplicate-free tuple containing zero or
more of the four core identities in ascending UTF-8 path order. It is a
caller-supplied evidence fact, not proof that this function read, authorized,
or understood a file.

The returned values contain paths, digests, relationship kinds, byte spans,
review facts, and an optional fragment. They contain no artifact bytes, link
labels, surrounding prose, agent IDs, provider result, confirmation, or Git
metadata. `fragment` is available by explicit field access but omitted from
ordinary representations. Fixed errors and their exception graphs do not
include paths, fragments, labels, or prose; validation emits no log records.
The input mappings and change set are not mutated or retained by the result.

### Exact link subset

The one recognized relationship kind is `inline_markdown_link`. Its source
syntax is the exact inline subset:

```text
[nonempty label](destination)
[nonempty label](destination#optional-fragment)
```

The first `]` closes the nonempty label; before it, the label may not contain a
line break, `[`, backslash, or an ASCII control. The opening `[` may not be
escaped or immediately form an unescaped image marker `![`. The destination
must be nonempty strict UTF-8 with no ASCII space
or control, backslash, parentheses, `?`, or `%`. It may contain at most one
`#`; when present, the fragment must be nonempty and additionally cannot
contain `#`. Scheme-like targets such as `https:`, network-path targets that
start with `//`, titled links, reference links/definitions, autolinks, HTML,
images, nested-parenthesis destinations, empty targets, and noncanonical forms
are not relationships.

Inline links inside matching backtick code spans are ignored. Fenced regions
opened by at least three backticks or tildes after zero to three spaces are
also ignored through a matching same-marker close of at least the opening
length; an unclosed fence is ignored through end of content.

A relative destination is resolved from the source artifact's directory. A
single leading `/` resolves from the workspace root. Empty and `.` path
segments are ignored, while `..` removes one component. An attempt to move
above the workspace root raises `PATH_ESCAPE`; this is a hard failure even if
the eventual spelling would not be a canonical artifact. A resolved target
enters the graph only if it is one of the four exact core identities or a
canonical `manuscript/PART/PATH.md` identity. Safe noncanonical and internal
targets such as `transcripts/`, `discussion_logs/`, `agent_states/`, or a
Unicode manuscript filename are treated as ordinary prose, not as edges or
dangling blockers.

The edge records the zero-based half-open UTF-8 byte span of the destination's
**path only** in the source content. The optional fragment is recorded
separately and does not participate in target existence or anchor validation.
The parser does not normalize case, percent escapes, Unicode, or path spelling.

### Graph delta and review facts

Validation first checks the bounded complete base, projects the exact change
set, rejects protected core operations, then checks the bounded projection.
It parses both complete graphs and returns deterministic UTF-8-ordered tuples:

- `added_edges` exist only after projection;
- `removed_edges` exist only in the base;
- `retained_edges` are byte-span-, source-, target-, fragment-, and
  kind-identical in both states; and
- `projected_dangling_edges` contains **every** added or retained edge whose
  canonical target is absent from the projected state.

A dangling graph returns a validation value with `is_valid == False`; it is
not reduced to the first blocker and is not raised as an exception. A change
may repair a pre-existing dangling edge by creating its missing target. When a
linked manuscript is renamed, every inbound source must be changed separately.
When a source manuscript moves, its unchanged relative outbound links are
resolved again from the destination directory, so the old edge is removed and
the newly resolved edge is added. Finite cycles and self-links are valid when
all targets exist; validation does not recurse through the graph.

Schema v1 has no companion inference:
`ARTIFACT_RELATIONSHIP_COMPANION_RULES == ()`. Similar filenames, part
membership, ordinary prose, search words, `${...}` references, and `$$`
markers never create edges.

Review facts are exact artifact facts, not routing decisions:

| Reason | Domain and artifact fact |
| --- | --- |
| `touched_core` | A core create, patch, or replace maps `global_outline.md` and `part_outlines.md` to `plot_structure`, `character_sheets.md` to `character_world`, and `narrative_rules.md` to `tone_execution`; it carries the operation index. |
| `manuscript_operation` | Every manuscript create, patch, replace, rename, or delete adds `tone_execution` for the primary path and operation index. |
| `attached_core_evidence` | Each exact caller-attached core path adds its mapped domain with `operation_index=None`. |
| `relationship_core_evidence` | A directly incident base or projected edge between a participating operation path and a core endpoint adds that core's domain and operation index. |

Facts are deduplicated and deterministically sorted. Relevance is direct only:
the policy performs no transitive graph walk and no natural-language inference.
The implemented `story_writing_agents.artifact_proposals` layer maps the
candidate's canonical domains to specialists. This relationship API itself
still exposes no agent ID and cannot silently choose, trigger, or attribute a
specialist.

### Provider-free scenario harness

Run this helper once before the seven workflows below. It builds exact change
sets and validates them without a provider, repository, or workspace:

```python
import hashlib

from story_writing_agents.writing_artifacts import (
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRelationshipError,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    validate_artifact_relationships,
)

sha256 = lambda content: hashlib.sha256(content).hexdigest()

def create(path, content, kind=ArtifactKind.MANUSCRIPT):
    return ArtifactCreateOperation(path, kind, content, sha256(content))

def patch(path, before, after, kind=ArtifactKind.MANUSCRIPT):
    digest = sha256(before)
    hunk = ArtifactTextHunk(digest, 0, len(before), before, after)
    return ArtifactPatchOperation(path, kind, digest, (hunk,), sha256(after))

def replace(path, before, after, kind=ArtifactKind.MANUSCRIPT):
    return ArtifactReplaceOperation(
        path, kind, sha256(before), after, sha256(after)
    )

def rename(source, destination, content):
    digest = sha256(content)
    return ArtifactRenameOperation(
        source, destination, ArtifactKind.MANUSCRIPT, digest, digest
    )

def validate(base, operations, attached=()):
    change_set = build_artifact_change_set(base, operations)
    return validate_artifact_relationships(
        base, change_set, attached_core_paths=attached
    )
```

These are library examples. The quoted lead replies are illustrative outputs
for the current lead workflow, not strings returned by this validator or
guaranteed provider wording.

#### 1. Create a missing core document

User input: “The global outline is empty because its file is missing. Propose
the minimum initial structure.”

```python
core = "core_documents/global_outline.md"
content = b"Outline placeholder.\n"
result = validate(
    {},
    (create(core, content, ArtifactKind.CORE_DOCUMENT),),
)
print(result.is_valid)
print([
    (fact.domain.value, fact.reason.value, fact.artifact_path,
     fact.operation_index)
    for fact in result.review_facts
])
```

```text
True
[('plot_structure', 'touched_core', 'core_documents/global_outline.md', 0)]
```

The same absent/create rule applies to each of the four core identities;
patch and replace are valid only when that identity is present and exact. The
lead can say, “The missing global outline can be created, and the
proposal requires plot/structure review. Shall I proceed to that review?” The
file is still absent: no write or confirmation occurred.

#### 2. Reject a protected-core rename or delete

User input: “Delete the global outline; we no longer need it.”

```python
base = {core: content}
delete = ArtifactDeleteOperation(
    core, ArtifactKind.CORE_DOCUMENT, sha256(content)
)
change_set = build_artifact_change_set(base, (delete,))
try:
    validate_artifact_relationships(base, change_set, attached_core_paths=())
except ArtifactRelationshipError as error:
    print(error.code.value, error.operation_index)
```

```text
protected_core_operation 0
```

All four core identities have the same permanent rule, including rename. The
lead can reject the destructive form and offer an exact patch or
replacement instead. The current core bytes remain unchanged.

#### 3. Detect every blocker in a broken manuscript rename

User input: “Rename `opening.md` to `arrival.md`.”

```python
opening = "manuscript/part-01/opening.md"
arrival = "manuscript/part-01/arrival.md"
chapter_list = "manuscript/part-01/chapter-list.md"
index = "manuscript/part-01/index.md"
before = b"[Opening](opening.md)"
after = b"[Arrival](arrival.md)"
scene = b"chapter"
base = {chapter_list: before, index: before, opening: scene}

broken = validate(base, (rename(opening, arrival, scene),))
print(broken.is_valid, len(broken.projected_dangling_edges))
print([
    (edge.source_path, edge.target_path)
    for edge in broken.projected_dangling_edges
])
```

```text
False 2
[('manuscript/part-01/chapter-list.md', 'manuscript/part-01/opening.md'), ('manuscript/part-01/index.md', 'manuscript/part-01/opening.md')]
```

The lead can say, “I cannot advance this rename as proposed: both
`chapter-list.md` and `index.md` would still point to the removed identity.”
That is a proposal critique only; the source file has not moved.

#### 4. Validate the rename with all inbound repairs

User input: “Include both inbound-link repairs in the same rename proposal.”

```python
repaired = validate(
    base,
    (
        patch(chapter_list, before, after),
        patch(index, before, after),
        rename(opening, arrival, scene),
    ),
)
print(
    repaired.is_valid,
    len(repaired.removed_edges),
    len(repaired.added_edges),
    len(repaired.projected_dangling_edges),
)
```

```text
True 2 2 0
```

This is the relationship-checked form of the exact change-set workflow above.
The lead can say, “The atomic proposal removes both old links, adds both
new links, and has no dangling target. It is ready for the later review step.”
Validation still did not apply or confirm any operation.

#### 5. Edit a manuscript with attached and linked core evidence

User input: “Revise the review scene using the character sheet I attached and
add its narrative-rule link.”

```python
manuscript = "manuscript/part-01/review.md"
characters = "core_documents/character_sheets.md"
rules = "core_documents/narrative_rules.md"
before = b"[Characters](/core_documents/character_sheets.md)"
after = before + b"\n[Rules](/core_documents/narrative_rules.md)"
base = {
    characters: b"Characters.\n",
    rules: b"Rules.\n",
    manuscript: before,
}
reviewed = validate(
    base,
    (replace(manuscript, before, after),),
    attached=(characters,),
)
print(reviewed.is_valid)
for fact in reviewed.review_facts:
    print(
        fact.domain.value, fact.reason.value,
        fact.artifact_path, fact.operation_index,
    )
```

```text
True
character_world attached_core_evidence core_documents/character_sheets.md None
character_world relationship_core_evidence core_documents/character_sheets.md 0
tone_execution manuscript_operation manuscript/part-01/review.md 0
tone_execution relationship_core_evidence core_documents/narrative_rules.md 0
```

The lead can say, “The artifact facts indicate character/world and
tone/execution review. May I dispatch those consultations?” The fact list does
not choose agents, prove that the attachment was read, or authorize a write.

#### 6. Allow a finite manuscript cycle

User input: “Create the three cross-linked manuscript notes, including the
self-link.”

```python
cycle = validate(
    {},
    (
        create("manuscript/part-01/a.md", b"[B](b.md)"),
        create("manuscript/part-01/b.md", b"[C](c.md)"),
        create("manuscript/part-01/c.md", b"[A](a.md) [Self](c.md)"),
    ),
)
print(cycle.is_valid, len(cycle.added_edges), cycle.projected_dangling_edges)
```

```text
True 4 ()
```

The lead can report that every canonical target exists and the cycle is
structurally valid. That is not a judgment about story quality and does not
create the three files.

#### 7. Keep ordinary discussion outside change validation

User input: “Would a quieter opening better fit this story?”

There is no proposed artifact operation, so the application should not invent
a no-op change set or call relationship validation. There is no validation
value and no review fact to display. Plain words such as “opening,” a filename,
or a core-document name do not create relationships or select a specialist.
The lead can clarify the question directly. A substantive recommendation
requires a scoped specialist proposal through the orchestration workflow, even
though no change set is needed. Nothing is written, staged, or committed.

### Relationship failures and retry

The stable relationship error-code values are:

```text
invalid_schema, invalid_attached_core_paths, protected_core_operation,
path_escape, artifact_count_limit, artifact_size_limit, state_size_limit,
edge_count_limit
```

`ArtifactRelationshipError` has the exact constructor
`ArtifactRelationshipError(code, *, operation_index=None,
artifact_index=None, link_index=None)`. Its optional indices are zero-based:
operations follow change-set order, artifacts follow canonical UTF-8 path
order, and links follow source scan order. A malformed or noncanonical input,
invalid attachment tuple, protected-core operation, escaping target, or limit
failure raises without a partial validation value. Exact change-set failures,
such as base-state drift, propagate as `ArtifactChangeSetError` rather than
being wrapped.

A retry must use current complete base bytes and a newly rebuilt exact change
set when those bytes changed. For `is_valid == False`, the caller can use every
returned dangling edge to construct one revised proposal. Neither path grants
confirmation or write authority.

## Lead-reviewed artifact proposals

`StoryRuntime.lead_turn()` now connects evidence-grounded lead intent to the
pure change-set and relationship contracts without connecting them to a writer.
For `kind=proposal`, the lead provider may return exactly one provider-neutral
artifact intent instead of an ordinary consultation intent:

| Operation | Provider names | Runtime requirement |
| --- | --- | --- |
| create | canonical `suggested_path`, `new_text` | Path must be canonical and complete-state projection must permit creation. |
| patch | current artifact `evidence_id`, `replacement_text` | Evidence must identify one exact whole or ranged boundary. |
| replace | current artifact `evidence_id`, `new_text` | Evidence must identify a whole artifact. |
| rename | current artifact `evidence_id`, canonical `suggested_destination` | Evidence must identify a whole artifact; projected links/policies must remain valid. |
| delete | current artifact `evidence_id` | Evidence must identify a whole artifact; protected core and relationship rules still apply. |

The Lead first integrates all required current-task specialist drafts. A fresh
user-supplied complete candidate may enter review when the Lead discloses that
interpretation. Every required specialist then reviews the exact immutable
final candidate; earlier drafts or supporting reviews do not authorize Apply.
An earlier Lead source message retains its whole-message identity and digest,
but the candidate is prepared independently and may omit conversational framing.
The runtime notice about unfinished authoring is not source or story content.
Selected earlier drafts cannot waive current task/revision drafting obligations.
Only the bound candidate's exact successful Apply satisfies an open authoring
objective; reviewed status and a completed consultation do not.

The lead also may name additional affected review domains. Runtime, not the
provider, performs `capture_complete_state()` under the source lease, resolves
the evidence-bound operations, calls `build_artifact_change_set()`, calls
`validate_artifact_relationships()`, derives mandatory domains from the
returned review facts, unions current authoring-scope and requested domains
in canonical order, maps them to specialists, and computes the candidate digest. Invalid or stale
intent produces no partial candidate.

The candidate is immutable and content-bearing. It contains the exact change
set, projected relationship validation, grounding evidence IDs,
mandatory/disclosed review domains, identity/revision, and digest. Exact user
confirmation authorizes only consultation about that candidate. Every
disclosed reviewer and the final lead synthesis receive the same serialized
candidate. Reviewers return attributed assessments; the lead preserves all
non-supporting inputs and chooses `propose`, `revise`, or `reject`. These views
are evidence, not a vote.

Only `propose` produces an immutable `ArtifactReviewedProposal`. That value
contains visible rationale, user impact, limitations, every review, unresolved
inputs, and exact candidate/history identity, while requiring all of these to
remain false:

```text
artifact_write_occurred = false
apply_authority_granted = false
git_action_occurred = false
```

Before review and again before reviewed-proposal finalization, runtime checks
the candidate against current complete state. Drift yields `stale`; revision
yields a fresh candidate and permanently supersedes the old one. No migration
or legacy proposal form is used. See the
[lead-agent scenarios](../guides/lead-agent.md#explicit-story-writing-scenarios)
and [runtime API](runtime-api.md#leadturnresult).

## Reference directive parser

> **Availability:** the pure parser remains I/O-free. Parsing alone is not proof
> of an eligible read. `StoryRuntime.lead_turn()` consumes the result and adds
> repository authorization/provider steps; terminal picker/viewer components
> are not active from main input.

One directive is a content-free indicator containing either an eligible source
path or that path followed by a one-based inclusive line range:

```text
${core_documents/global_outline.md}
${manuscript/part-01/scenes/opening.md}#L7-L12
```

The path inside `${...}` must be one of the four exact core-document paths or
any safely representable lowercase-`.md` descendant under `manuscript/`. The
latter includes `manuscript/root.md`, arbitrary nesting, spaces, Unicode, `{`,
`$`, and `#`; it is intentionally broader than the publication grammar above.
It excludes `}`, line separators, control/format/surrogate characters, unsafe
or non-normalized paths, uppercase `.MD`, and a filename that is only `.md`.
A range is optional, immediately follows `}`, uses uppercase `L`, contains
positive ASCII decimal bounds without leading zeroes, and must have
`start <= end`. Both bounds are inclusive. A value larger than `sys.maxsize`
is rejected.

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
directive and must have a closing brace and a nonempty eligible source path;
any range that begins with `#L` must be complete. Lowercase `#l`,
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

The parser API lets callers validate directive grammar, preserve the original
input, compute the provider-text escape projection, and inspect ordered
canonical path/range indicators. This parser alone does not
establish artifact eligibility, read content, supply it to a provider, or
require an agent read. `lead_turn()` adds those steps for `${...}`. Interactive
Lead input accepts the same indicators directly. `$` opens canonical selection;
`$$` uses that same picker and then opens the independent range viewer.

## Authorized reads and lifecycle evidence

> **Availability:** repository authorization, transient delivery, durable
> content-free evidence, and exact provider-read enforcement are used by
> Python and interactive Lead turns. Main input activates `$` whole-file
> selection and `$$` ranged viewing; the resulting indicator remains
> identity-only.

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
| `requested` | The create-only operation manifest and first evidence event exist before source reads. | A reference attempt was recorded. No bytes have been authorized. |
| `captured` | Every ordered reference was selected, hashed, and held in one immutable in-memory capture with a filesystem source identity. | The current sources and selected-byte proofs were valid at capture time. This says nothing about publication provenance. |
| `revalidated` | Each reopened source still matches the captured canonical path, kind, physical identity, size, timestamps, and full-file hash at a caller-named checkpoint. | The captured identity was still current at that checkpoint; the event does not refresh the captured bytes. |
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

The durable schema-v2 history contains ordered path and optional range,
content-free filesystem source identity, selected SHA-256 and byte count,
source line count, agent/invocation
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

Initial capture takes the source/evidence lock, writes `requested` evidence,
and securely reads each unique referenced path once even
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
operation acceptance, the repository compares the complete current filesystem
source identity and full-file hash with the original capture. The selected
bytes are never refreshed. Deletion, replacement—including a same-byte
replacement with a new physical identity—unsafe topology, size-limit failure,
or byte drift terminates the affected path through the lifecycle.

An empty reference batch returns without taking the artifact lock, reading
storage, or creating history. For a nonempty batch,
`inspect_reference_operation()` returns `None` only when the operation
directory is absent. A present but partial, malformed, noncanonical, or
inconsistently sequenced history raises the fixed `evidence_corrupt` error
instead of being repaired or treated as absent.

Reference evidence schema v1 cannot authorize or resume an operation and is
not migrated. Its corruption remains local to that operation identity; a new
schema-v2 operation with a fresh ID can still proceed when its sources and
evidence namespace are safe.

## What a user can change

Users can ask the Python or installed interactive Lead to critique or
recommend a change, construct an exact artifact candidate, confirm its
specialist review, and inspect an immutable reviewed proposal. A separate
structured Apply decision can publish that exact reviewed change in the
correlated session worktree. Review confirmation alone does not apply the
candidate, and providers never receive filesystem or Git authority. Users can
also prepare or inspect safe canonical files directly while no workspace
process is active. A manual file has no agent-produced provenance.

The implemented execution/publication workflow supports:

- an outline request can lead the lead orchestrator to publish full replacements
  for `global_outline.md` or `part_outlines.md`;
- a character, relationship, or world-continuity request can lead to a full
  `character_sheets.md` replacement;
- a prose, viewpoint, tone, or formatting request can lead to a full
  `narrative_rules.md` replacement; and
- a manuscript request always publishes one new file at the required part path.

### Explicit-reference scenarios

These content-free indicators describe target evidence, but they are not
currently executable shell input:

- Outline review can attach `${core_documents/global_outline.md}` followed by
  `${manuscript/part-01/scenes/opening.md}#L40-L65`.
- Character continuity can attach `${core_documents/character_sheets.md}` and
  multiple nonoverlapping manuscript ranges in one ordered batch.
- Narrative-rule review can attach a range of
  `${core_documents/narrative_rules.md}` plus the manuscript range being
  reviewed.
- A manuscript revision can read the earlier manuscript and relevant Part
  outline, then propose an exact create, patch, replace, rename, or delete.

The current lead invocation completes its independently correlated ordered
read. Each later addressed specialist must do the same; the lead's receipt
cannot be reused. The shell rejects the surrounding story instruction before
parsing references.

See the [artifact-reference guide](../guides/artifact-references.md) for exact
syntax, limits, work-history inspection, and complete user scenarios.

The retained internal consensus algorithm expects the execution specialist to
author an initial candidate and all three specialists to audit the same
revision. That algorithm has no current public entry point. Its existence does
not authorize a repository commit.

The retained `commit_consensus` method is now only a temporary native translator
for its existing caller: it converts one new manuscript plus changed core
documents into the new application request/store and writes only the new
schemas. It does not preserve the former commit-ledger format and is not the
exact request-only `apply_artifact_changes()` seam.

The native store supports all five operations for manuscripts. Core documents
keep four permanent identities: create, patch, and replace are allowed, while
core rename and delete fail relationship validation before a journal is
published. A deleted or renamed manuscript disappears from current catalog
membership; its immutable application record retains the historical fact.

## Filesystem-source and publication states users observe

Source availability and application provenance remain distinct, but they share
one observation boundary. Every repository-owned catalog, source-list/read, and
explicit-reference operation takes the process lock and workspace lease, then
recovers pending application journals before reading authoritative files.

Source discovery checks exactly the four core paths and recursively scans
regular files below `manuscript/`. Every safely representable lowercase-`.md`
descendant is eligible, including direct root files, arbitrary nesting, spaces,
Unicode, `{`, `$`, and `#`. Results are lexical and contain path and type
metadata only; listing does not read, decode, hash, or line-split file content.
Safe empty, manual, initialized by another tool, pre-provenance, older,
published, and hash-divergent files are included. `.gitkeep`, extra core paths,
unsupported extensions, `}`, invalid/oversize paths, case or normalization
aliases, symlinks, hard-linked files, special files, internal state, and outside
paths are excluded. These source rules do not relax the stricter publication
identity in [Exact output paths](#exact-output-paths).

| State | Source behavior | Publication behavior |
| --- | --- | --- |
| Absent | The path is not listed, viewable, referenceable, or available to contextual source capture. | `apply_artifact_changes()` may create it when an exact request and policy permit; the installed Lead path reaches that seam only after separate review and Apply decisions. |
| Present and safe | The path is listed. Opening captures current bytes with strict UTF-8/NUL and 8 MiB checks; role-allowlisted core files can enter contextual P2 selection. Empty whole-file references are valid. | If catalog/application-ledger metadata matches, it may also be a current applied artifact. Source use alone does not establish that fact. |
| Manual, pre-provenance, or hash-divergent | It has the same source behavior as any other present safe file. A changed core file changes its context revision and can force delta/bootstrap. | No provenance is fabricated or adopted. Its current bytes can make an exact create/update/delete request stale before journaling. |
| Unsafe or unsupported | It is excluded during metadata discovery when the failure is entry-local; an exact open fails closed. A listed file whose bytes later prove oversized, invalid UTF-8, or NUL-containing fails when opened. | It cannot be published through the unsafe identity. |
| Source drift after capture | Deletion, replacement, physical-identity change, unsafe topology, oversize, or hash change terminates the invocation or operation before acceptance. | The captured bytes are not refreshed and no publication is accepted from that referenced operation. |
| Recovery pending | The operation first completes or verifies the exact journal, then observes the recovered old/new authority; it never returns an intermediate target set. | Catalog and application reads use that same recovered authority. |
| Recovery blocked or a pending journal cannot be reconciled | Source, reference, catalog, and application operations fail closed before returning authoritative data. | Stop workspace writers and restore a complete trusted state; do not edit the marker, journal, catalog, or record to force progress. |
| Catalog/application ledger invalid with no pending journal | Safe source/reference operations may still observe current filesystem sources after recovery; catalog/application operations fail their stricter audit. | Stop writers and restore the complete catalog/record set before applying or trusting provenance. |
| Lock unsafe | Source and publication operations both fail before protected work because they share the repository lease. | Stop workspace processes and investigate the three-name lock identity. |
| Retained staging residue | Internal staging never appears as a source. | Staging is non-authoritative and ignored by recovery/catalog projection. |

Every source open is bounded to at most 8 MiB. A caller-supplied larger limit is
clamped to 8 MiB; a stricter positive limit is honored. Explicit-reference
selection then applies the separate 128 KiB per-reference and 256 KiB aggregate
limits and validates requested line ranges. Listing may therefore show a path
that later fails to open or attach.

Stop all workspace processes before any external/manual filesystem edit; live
manual namespace mutation is outside the supported writer boundary. Do not
edit the catalog, application records, transaction journals, lock, evidence, or
recovery files to force a state. They are internal consistency records, not
canonical story sources.

## Application storage and recovery mechanism

The #519 private persistence owner now sits beneath the #520
`WritingArtifactRepository.apply_artifact_changes()` seam. The repository is a
module-supported internal component, not a package-facade export or installed
CLI command. It accepts application authority but does not obtain it: the
runtime supplies the separate lead/user Apply-confirmation workflow. It has no
provider, agent, context, or Git entry point. The retained `commit_consensus`
method is only a compatibility translator for its existing native caller.

Repository construction remains I/O-free. Every authoritative catalog,
canonical-source, explicit-reference, and application operation then uses one
fixed resource boundary:

```text
in-process repository lock
-> permanent workspace .artifacts.lock lease
-> recover every exact pending application journal
-> perform the requested read or application
-> release the lease
```

The runtime-composed session variant adds an outer READY gate without changing
that repository API. One exact repository object belongs to the binding, but
each transaction requires a live same-thread binding access and semantic READY
validation before and after. A retained raw repository therefore fails outside
access. Long-lived runtime reference/source adapters retain an access factory,
not authority, and reacquire the guard for each operation; same-thread nested
binding access is reentrant.

The private store, not `repository.py` or `_storage.py`, is the sole interpreter
of application journals, immutable records, catalog transitions, completed
lookup, audit, and recovery. The existing secure-storage primitive still owns
descriptor-relative no-follow I/O; #519 does not change that lower boundary.

### Operation effects

| Operation | Required old state | Authoritative new state |
| --- | --- | --- |
| `create` | Canonical path absent. | New exact strict-UTF-8 bytes and digest are present. |
| `patch` | Path and every exact base byte/digest/hunk still match. | Patched bytes and projected digest are present. |
| `replace` | Path and base digest still match. | Different exact replacement bytes are present. |
| `rename` | Manuscript source exists with the expected digest and distinct destination is absent. | Source is absent; destination has the same bytes/digest. This creates two ordered transaction targets. |
| `delete` | Manuscript path and prior digest still match. | Path is absent; deletion provenance retains its prior digest. |

Manuscripts support all five operations. Core documents support create, patch,
and replace only. The relationship policy rejects a core rename or delete
before any transaction journal or application authority is published. Every
request also revalidates the complete current canonical mapping, relationship
result, provenance digests, path ordering, and target topology under the lease.
Drift or a stale base therefore fails with no journal, record, catalog change,
or target write.

### Durable layout and successful flow

The local state is under:

```text
agent_states/orchestrator_state/writing_artifacts/
├── .artifacts.lock[.anchor|.ready]
├── applications/<application_id>.json
├── transactions/<transaction_id>.json
├── catalog.json
├── recovery_blocked.json
└── staging/
```

`catalog.json` is schema 2 and describes only current membership. Each
`applications/<application_id>.json` record is immutable, content-free, and
preserves exact historical result/correlation facts. A transaction journal is
the one content-bearing recovery authority: it includes the complete request,
catalog-before and catalog-after images, immutable application record, and
ordered target old/new facts and new bytes.

For the five-operation `application-1` request shown above, the internal and
external sequence is:

```text
input: exact request + attached core paths
  -> recover and audit existing authority
  -> project and revalidate current bytes and relationships
  -> create and fsync one immutable content-bearing journal
  -> publish six ordered targets (rename source + destination are separate)
  -> publish immutable content-free application record
  -> publish schema-2 current catalog last
  -> verify every target, record, and catalog fact
  -> retire the unchanged journal to non-authoritative staging
output: APPLIED with exact affected/final/lineage/deletion facts
```

The eight named test/fault boundaries are `after_revalidation`,
`after_journal`, `after_target` (once for each target—six times for the sample),
`after_targets`, `after_record`, `after_catalog`,
`before_journal_retirement`, and `after_journal_retirement`. They describe
observation points for verification; callers cannot inject them through the
public facade.

Repository-visible atomicity means **recovery before authoritative read**.
Individual target publications are filesystem-atomic, but six files, one
record, and one catalog are not replaced in one simultaneous filesystem step.
A raw external filesystem reader during a crash may see a partial target set.
A repository reader cannot: it first takes the same lease and either rolls the
exact journal forward to new authority, verifies already-complete new
authority, or fails closed without returning data. The catalog is published
last, so it never intentionally advertises a partial new set.

### User and runtime scenarios

The exact request identifies the intended operations; the result reports only
terminal repository facts. Read the pair together. A result does not contain
story text, a complete catalog, confirmation evidence, or automatic context/Git
effects. Call `catalog()` separately when current membership is needed.

| User intent and exact request | Repository-visible output |
| --- | --- |
| Repair one incomplete core document with a patch to `core_documents/character_sheets.md`, with that core path attached to the reviewed evidence. | `applied`; one affected path and one final core path/kind/SHA-256 fact. The repository does not claim that a lead obtained confirmation. |
| Replace `manuscript/part-01/opening.md` after review against the sorted attached paths `core_documents/character_sheets.md` and `core_documents/narrative_rules.md`. | `applied`; the manuscript path and final digest. Attached core paths are relationship-review facts, not implicit write targets. |
| Repair multi-file consistency in one request by patching `global_outline.md`, replacing `opening.md`, and patching every affected inbound manuscript link. | `applied`; every operation path appears in the affected/final/rename/delete facts. Omitting a path from the request is not deletion. |
| Reject the proposal or request a revision before Apply. | Runtime must not call this method. There is no result, journal, catalog change, or artifact write. A revised candidate needs a fresh exact request and confirmation. |

For example, after runtime records the separate confirmation it may pass an
already-built request to the repository:

```text
input: ArtifactApplicationRequest(
  application_id="application-1",
  operations=(patch core character sheet, replace manuscript),
  attached_core_paths=("core_documents/character_sheets.md",)
)
call: repository.apply_artifact_changes(request)
output: ArtifactApplicationResult(
  state="applied",
  affected_paths=("core_documents/character_sheets.md",
                  "manuscript/part-01/opening.md"),
  final_artifacts=(path/kind/SHA-256 facts for those two paths),
  rename_lineage=(), deletion_provenance=()
)
```

This is readable shorthand for the typed values; callers do not pass that text
or a dictionary to the method.

### Explicit outcomes

**Success.** The sample request returns the exact `applied` result shown above.
Current catalog membership contains the create, patch, replace, and rename
destination. It omits the rename source and deleted manuscript; their immutable
lineage/deletion facts remain in the application record. This operation does not
stage a Git index, create a Git commit, move `HEAD`, or update a ref.

**Immediate drift.** Suppose
`manuscript/part-01/opening.md` changes after the request was built. Base-state
projection fails before the journal and the seam raises a content-free
`ArtifactApplicationError` with `code == drift`:

```text
result: error.code = drift
canonical target writes: 0
application records: 0
catalog changes: 0
safe next action: reread current state and prepare/reconfirm a new request
```

Reusing an existing `application_id` with different canonical request bytes is
`correlation_mismatch` and never republishes the historical application.

**Crash after the journal or a target.** The exact journal remains
authoritative. The next catalog, source, reference, or application call recovers
under the lease. At catalog-before, each target must be exactly its recorded old
or new state; recovery idempotently publishes the remaining new targets, record,
and catalog, verifies them, and retires the journal. The caller then observes
the complete new state, never a repository-returned partial state.

```text
input after interruption: apply the same exact request again
internal first step: recover and finalize the existing journal
output: unchanged with the original affected/final/rename/delete facts
new application publication: none
```

**Crash after catalog publication.** Recovery accepts only the exact catalog-
after image with every new target and immutable record already matching. It
verifies and retires the same journal; it does not replay or invent another
application.

**Lost response or crash after journal retirement.** Completed lookup finds the
immutable record. Retrying the byte-identical request returns the original
affected paths, final artifacts, rename lineage, deletion provenance, and
provenance with state `unchanged`. It writes no target, record, catalog, or
journal again. These are the original historical result facts even if later
applications have since changed current membership.

**Corruption.** Malformed/noncanonical catalog or application records, unsafe
repository metadata, or another persistent format/security failure maps to
`error.code = corruption`. The error contains no story text or raw internal
exception. Preserve the workspace and restore verified exact metadata; do not
retry the request as though it were merely stale.

**Recovery blocked.** A changed journal, target matching neither old nor new,
catalog matching neither exact before nor after image, missing/divergent
application record, alias/link substitution, or invalid ledger sequence during
journal recovery creates or preserves `recovery_blocked.json` and fails
authoritative reads and writes. Stop all workspace processes and restore one
complete trusted application state. Do not hand-edit or delete the marker or
partial evidence to force a retry. An independent catalog/ledger audit failure
without a pending journal also fails closed but need not create this marker.

At the repository seam the durable marker maps to
`error.code = recovery_required`. Preserve every target, journal, application
record, catalog, and marker; stop writers and perform operator recovery. An
automatic retry, reset, cleanup, or replay is unsafe.

### Reference and later-Git consequences

Application does not mutate captured reference bytes or silently refresh an
agent's context. If a captured source is patched, replaced, renamed, or deleted,
later `revalidate_reference_operation(..., checkpoint="after_application")`
fails with reference `drift`. Existing evidence and invocation events are
retained, and one terminal operation event is appended with that checkpoint and
outcome. The old authorization cannot continue; capture a new reference after
the new artifact state is accepted. Here “checkpoint” is a caller-named
reference revalidation point, not a Git/session checkpoint.

Application also does not stage or commit Git. A later confirmed story-commit
workflow must separately prepare exact Git evidence from the applied state and
obtain its own fresh Commit decision. The application result alone is neither
Git authority nor proof that the user confirmed Commit.

### Inspecting one historical application

The module-supported repository also provides one read-only lookup:

```python
result = repository.inspect_artifact_application("application-1")
```

The ID must be an exact built-in `str` matching the safe application-ID grammar;
an empty value, unsafe value, non-string, or string subclass raises
`ArtifactApplicationErrorCode.INVALID_IDENTIFIER` before locking. A valid call
takes the repository lock and workspace lease once, recovers pending journals,
audits the complete application-record history against the current catalog,
delegates one lookup, and then returns:

```text
input: "application-1" (existing completed identity)
output: ArtifactApplicationResult(state="applied", original exact facts...)

input: "missing" (safe but absent identity)
output: None
```

An existing lookup always returns the immutable historical `applied` result
stored by the first application. It does **not** return `unchanged`; that state
belongs only to calling `apply_artifact_changes()` again with the exact completed
request. An absent lookup returns `None` only after recovery and the full
ledger/catalog audit succeeds. On a brand-new workspace, taking the repository
lease may initialize its standard lock/state-directory topology, but inspection
does not initiate a new artifact/application write. Recovery may finish an
already-journaled application before returning; otherwise inspection writes no
canonical artifact, application record, journal, or catalog entry.

Inspection does not reconstruct or expose the content-bearing request, reapply
the operation, infer a session, or initiate changes to files, context, provider
state, agents, or Git. The returned result remains content-free but does expose
its intended paths, digests, candidate/application identifiers, rename lineage,
and deletion provenance. Malformed catalog/record authority raises content-safe
`CORRUPTION`; a durable block marker or unrecoverable journal raises
`RECOVERY_REQUIRED`. Preserve the workspace and follow the recovery action
below rather than treating either failure as absence.

Later #527 session recovery may look up the application ID already recorded in
its own correlated session evidence. This seam supplies only application truth;
it cannot discover or prove which session, confirmation, or conversation owns
that application. The lookup is additive over existing schema-1 application
records and introduces no persisted format or migration.

<a id="provenance-and-work-history"></a>

## Provenance, membership, and immutable history

The schema-2 catalog is the current projection reconstructed by replaying
immutable application records in contiguous positive `sequence` order. Every
current entry stores path, artifact kind, SHA-256, application ID/sequence,
candidate ID/revision, tagged candidate digest, change-set digest, and tagged
relationship-validation digest. A later application replaces the current entry
for an updated path; rename and delete remove their sources from membership.

Application records remain after membership changes. Each binds the application
ID/sequence, request SHA-256, base/projected state digests, content-free
operation facts, and exact `applied` result. The result preserves final
artifacts, manuscript rename lineage, and manuscript deletion provenance.
Records and catalog entries contain paths, digests, identifiers, and operation
history, but no story bodies. They are audit/recovery evidence, not user
confirmation, write authority, Git history, or a backup of deleted text.

Transaction journals are different: the embedded request and target facts
contain the exact create/replace bodies and patch text needed to finish after a
crash. They remain local plaintext until exact retirement; ambiguous journals
are preserved. Staging residue is non-authoritative and excluded from catalog
membership and source discovery, but it may also contain prior or new story
bytes. Protect the entire workspace state directory as sensitive data.

There is deliberately no migration or compatibility path. A nonempty legacy
`commits/` directory, catalog schema 1, or transaction schema 2 is preserved and
rejected. Unknown, missing, duplicate, noncanonical, malformed, aliased,
symlinked, hard-linked, or inconsistent application state likewise fails closed
instead of being adopted, rewritten, reconstructed, or normalized.

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
components, and detected file changes fail closed. Metadata discovery omits an
entry-local failure. Root, ancestry, lease, and recovery failures propagate to
all repository-owned source/reference/catalog/application operations. Catalog-
schema and application-ledger audit failures block catalog/application work;
without pending recovery they do not erase otherwise safe filesystem sources.
Artifact text is strict UTF-8. The repository does not encrypt content,
provenance, journals, or staging residue, provide secure erase, or enforce a
retention policy.

The lease serializes repository writers, and generic `Workspace` canonical
writes are rejected. It is not a security boundary against an actively hostile
same-user process mutating live names during a native filesystem call. Make
manual or tool-driven filesystem changes only after every workspace process has
stopped, then restart and let source and publication validation report the result.

The catalog, private application store, and repository are module-supported
internals used by the runtime; neither repository nor store is re-exported.
`apply_artifact_changes()` is the one exact request-only repository application
seam. Exact run signatures are in
[Runtime API](runtime-api.md), workspace topology in
[Session storage](configuration/session-storage.md), filesystem consequences in
[Filesystem safety](../operations/filesystem-safety.md), and debate sequencing
in [Consensus workflow](../design/consensus-workflow.md).
