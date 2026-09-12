# Safe Git adapter and story baseline

The `story_writing_agents.git` package is a direct Python API for inspecting one
local repository, detecting an external Git operation, and creating one exact
initial story-workspace commit. It is provider-independent and uses only the
Python standard library plus the local Git executable.

This is the complete availability boundary for the #448 baseline and #530
external-operation inspection work:

- Python callers can classify repository topology, inspect or create the fixed
  baseline, inspect external-operation metadata, and read bounded status, diff,
  and log metadata.
- `story init` now composes this adapter internally after it records the exact
  schema-v2 initialization journal and installs its exact 25-file scaffold.
  The CLI does not expose manifest selection or another Git operation.
- The public adapter still has no session branch, worktree, internal-ref, or
  later-commit method. Separate package-internal #504 modules now own exact
  session resources for #455 composition. Package-internal #509 now owns exact
  story commits, and package-internal #510 owns closed-path checkpoints;
  the Lead runtime owns application/confirmation integration.

The adapter is not a general Git client. It accepts no command, argument list,
revision, ref, commit message, credential, prose, or provider-tool input.

## User impact

A Python integration can now establish and verify a deterministic history
boundary without staging unrelated files or exposing story text through its
result objects. After successful creation, users see a local `main` branch with
one root commit named `Initialize story workspace`. The commit and index
contain exactly the regular files selected by the caller's manifest.

Users running `story init` on an absent/empty safe root receive this baseline
automatically with the initializer's exact 25-file manifest. During strict
initialization or journal recovery, the workspace lifecycle rejects arbitrary
extra entries even though the direct adapter leaves unrelated untracked files
unchanged. After journal removal, workspace readiness instead omits ignored
and untracked entries and validates only required paths plus exact Git
invariants. Staged changes and tracked unknown additions still require
recovery.

Users cannot choose a manifest from the CLI, use this API to make a second
commit, apply a story proposal, manage a session worktree, contact a remote, or
repair an unexpected repository. Initialization authority covers only the one
fixed root commit. The adapter reports a closed recovery state instead of
guessing how to change an unexpected repository.

A Python caller can now detect an externally started merge, rebase,
cherry-pick, revert, or sequencer operation without running Git or exposing its
administrative payload. Users can use that fact to stop before another owner
mutates the repository. This inspection does not itself block a lifecycle
action, identify a story session, or continue, abort, reset, clean, or repair
the operation; #527 will correlate it and #528 will enforce the lifecycle
decision.

## Fixed baseline contract

Baseline creation has these exact postconditions:

- the only reference is `refs/heads/main`, and `HEAD` points to it;
- history contains exactly one commit with no parent;
- the commit message is exactly `Initialize story workspace`;
- the Git index and committed tree contain the same manifest paths, each with
  mode `100644` and the exact caller-digested bytes;
- files not named by the manifest remain untracked and unchanged; and
- repeating `create_baseline()` with the same manifest returns the same commit
  and tree instead of creating another commit.

The adapter does not modify the selected working files. Commit and tree object
IDs are intentionally not fixed: they depend on the file bytes, repository
object format, effective identity, and commit time.

### Exact manifest and digest

`GitBaselineManifest` must be nonempty, unique, and sorted by UTF-8 path bytes.
Each entry carries a lowercase 64-character SHA-256 digest of the file bytes.
Paths are relative slash-separated names whose segments use only ASCII letters,
digits, `.`, `_`, or `-`; empty, `.`/`..`, `.git` (case-insensitive), wildcard,
space, backslash, absolute, and trailing-slash paths are rejected. A leading
hyphen is safe because paths are always handled as literal data.

For these exact files:

```text
core_documents/global_outline.md     b"Opening goal.\n"
core_documents/narrative_rules.md    b"Close third person.\n"
```

the canonical ASCII JSON used only to derive manifest identity is:

```json
[{"content_digest":"76aebb71ba9459c34c0fca57afda1925643806c18c81459376ab90d78f3b8274","path":"core_documents/global_outline.md"},{"content_digest":"c6231ec8bf55b2f8f8db0481fd2add398cee4b66731760ab3c92d630c059c5de","path":"core_documents/narrative_rules.md"}]
```

Its manifest digest is:

```text
fd443338d6795340428e88ed461f6a58a77d30b9f8044073a85b3c62e6422cb0
```

The manifest supports at most 65,536 files. Each selected file is at most 8
MiB, and all selected file bytes together are at most 64 MiB. A digest mismatch,
symlink, missing path, non-regular file, or root identity change fails before
the baseline is accepted.

## Complete Python scenario

The following caller assumes the two files above already exist at an absolute,
non-symlinked workspace root and that a valid Git identity is available. It
calls every public operation and deliberately sanitizes variable object IDs.

```python
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from story_writing_agents.git import (
    GIT_BASELINE_BRANCH,
    GitBaselineManifest,
    GitManifestEntry,
    GitRepositoryAdapter,
)

root = Path("/absolute/path/to/story")
paths = (
    "core_documents/global_outline.md",
    "core_documents/narrative_rules.md",
)
manifest = GitBaselineManifest(
    tuple(
        GitManifestEntry(
            path=path,
            content_digest=hashlib.sha256((root / path).read_bytes()).hexdigest(),
        )
        for path in paths
    )
)
adapter = GitRepositoryAdapter(root)

# READ: classification and manifest-bound inspection do not create a repository.
topology_before = adapter.inspect_topology()
baseline_before = adapter.inspect_baseline(manifest)

# WRITE: this is the adapter's sole repository-creating operation.
created = adapter.create_baseline(manifest)
retried = adapter.create_baseline(manifest)

# READ: these projections contain metadata, never file or commit-message bodies.
external_operation = adapter.inspect_external_operation()
status = adapter.status()
diff = adapter.diff_metadata()
log = adapter.log_metadata()

def object_id(value: str | None) -> str | None:
    return "<object-id>" if value is not None else None

result = {
    "inspect_topology": topology_before.kind.value,
    "inspect_baseline": {
        "state": baseline_before.state.value,
        "manifest_digest": baseline_before.manifest_digest,
    },
    "create_baseline": {
        "topology": created.topology.kind.value,
        "state": created.state.value,
        "branch": GIT_BASELINE_BRANCH,
        "commit_id": object_id(created.commit_id),
        "tree_id": object_id(created.tree_id),
        "identity_verified": created.effective_identity is not None,
    },
    "retry": {
        "same_commit": retried.commit_id == created.commit_id,
        "same_tree": retried.tree_id == created.tree_id,
    },
    "external_operation": {
        "kind": external_operation.kind.value,
        "repository_identity_digest": "<repository-identity-digest>",
        "worktree_identity_digest": "<worktree-identity-digest>",
    },
    "status": {
        "branch": status.branch,
        "head_id": object_id(status.head_id),
        "clean": status.clean,
        "entries": [
            {
                "path": entry.path,
                "index": entry.index_change.value if entry.index_change else None,
                "worktree": (
                    entry.worktree_change.value if entry.worktree_change else None
                ),
            }
            for entry in status.entries
        ],
    },
    "diff_metadata": {
        "head_id": object_id(diff.head_id),
        "entries": [
            {
                "path": entry.path,
                "change": entry.change.value,
                "staged": entry.staged,
            }
            for entry in diff.entries
        ],
    },
    "log_metadata": [
        {
            "commit_id": object_id(commit.commit_id),
            "tree_id": object_id(commit.tree_id),
            "parents": [object_id(parent) for parent in commit.parent_ids],
            "message_digest": commit.message_digest,
            "message_utf8_bytes": commit.message_utf8_bytes,
        }
        for commit in log.commits
    ],
}
print(json.dumps(result, indent=2))
```

For the new workspace and exact bytes above, the sanitized output is:

```json
{
  "inspect_topology": "absent",
  "inspect_baseline": {
    "state": "absent",
    "manifest_digest": "fd443338d6795340428e88ed461f6a58a77d30b9f8044073a85b3c62e6422cb0"
  },
  "create_baseline": {
    "topology": "standalone",
    "state": "baseline_committed",
    "branch": "main",
    "commit_id": "<object-id>",
    "tree_id": "<object-id>",
    "identity_verified": true
  },
  "retry": {
    "same_commit": true,
    "same_tree": true
  },
  "external_operation": {
    "kind": "none",
    "repository_identity_digest": "<repository-identity-digest>",
    "worktree_identity_digest": "<worktree-identity-digest>"
  },
  "status": {
    "branch": "main",
    "head_id": "<object-id>",
    "clean": true,
    "entries": []
  },
  "diff_metadata": {
    "head_id": "<object-id>",
    "entries": []
  },
  "log_metadata": [
    {
      "commit_id": "<object-id>",
      "tree_id": "<object-id>",
      "parents": [],
      "message_digest": "a1bef0f28a0cd18865e2ed7271c44a9b3f9e9002f361f059aadc32be17fc8318",
      "message_utf8_bytes": 27
    }
  ]
}
```

After a tracked file changes, `status()` reports its literal path and
`worktree_change="modified"`; `diff_metadata()` reports content-free object-ID
metadata for that change. An untracked path appears in `status()` but not in
`diff_metadata()`. Neither result has a content field. `log_metadata()` returns
at most 128 commits, newest first, with identities, timestamps, parent/tree
IDs, and only the digest and UTF-8 byte count of each message.

## Effective author and committer identity

Baseline creation resolves author and committer independently:

| Field | First source | Second source | Final source |
| --- | --- | --- | --- |
| Author name | `GIT_AUTHOR_NAME` | effective `user.name` | none |
| Author email | `GIT_AUTHOR_EMAIL` | `EMAIL` | effective `user.email` |
| Committer name | `GIT_COMMITTER_NAME` | effective `user.name` | none |
| Committer email | `GIT_COMMITTER_EMAIL` | `EMAIL` | effective `user.email` |

The adapter asks Git for its effective author and committer identities and
requires them to equal those expected values before mutation. Missing, invalid,
or divergent identity produces `identity_invalid`; for an absent repository,
that occurs before `.git` is created. Existing repository-local identity can
supply the effective configuration. The adapter does not change local or
global configuration. Identity values are intentionally excluded from object
representations, although callers can explicitly read the typed identity fields.

## Topology and visible state

`inspect_topology()` returns one of these closed classifications:

| Topology | External meaning |
| --- | --- |
| `absent` | The exact root is not a repository and is not inside another one. Baseline creation may proceed. |
| `standalone` | The root owns a normal `.git` directory. Further baseline validation decides whether creation or reads may proceed. |
| `parent_repository` | The root is nested inside another repository. The adapter refuses to adopt it. |
| `linked_worktree` | `.git` is an administrative-file link. Baseline and diff operations reject it; `status()` and `log_metadata()` can observe a securely validated linked worktree without establishing session ownership. |
| `submodule` | The root is a submodule or has a Git-link entry. |
| `sparse_checkout` | Sparse-checkout configuration is active. |
| `root_mismatch` | Git's worktree/top-level/bare facts do not identify the exact supplied root. |
| `symlinked_administrative_path` | `.git` or a path inside it is symlinked. |
| `malformed_administrative_path` | Administrative layout is incomplete, malformed, or uses object alternates. |

The root itself must be an existing absolute, normalized, non-symlinked
directory. Its device and inode are rebound for every operation and rechecked
around filesystem and process work.

`inspect_baseline(manifest)` then projects one user-visible state:

| State | Meaning and safe next step |
| --- | --- |
| `absent` | No repository exists. Validate identity and call `create_baseline()` when creation is intended. |
| `initialized_without_baseline` | A standalone repository has no refs or commits and `HEAD` names `main`. Retrying baseline creation can converge. |
| `baseline_committed` | The one exact manifest-bound root commit, tree, index, branch, and identities were verified. Repeating creation is read-like and idempotent. |
| `recovery_required` | Topology, refs, history, tree, index, files, or message do not match the closed baseline. Inspect or recreate the workspace outside this adapter; it will not rewrite unexpected history. |

For a linked worktree, baseline inspection reports `recovery_required`,
baseline creation refuses it, and `diff_metadata()` reports
`unsupported_topology`; guarded `status()` and `log_metadata()` are the only
exceptions. Other non-standalone topologies remain unsupported for
status/diff/log reads. A symlinked or replaced root fails as `unsafe_root`.

<a id="external-git-operation-inspection"></a>

## External Git operation inspection

`GitRepositoryAdapter.inspect_external_operation()` directly inspects the
bound Git administration directories without starting a Git process. It returns
one frozen, content-free `GitExternalOperationInspection`:

| `kind` | Exact unambiguous administrative shape | What the user should do |
| --- | --- | --- |
| `none` | None of the supported operation markers is present. | Continue only if separate topology, status, and workflow checks also allow it. `none` does not mean clean. |
| `merge` | `MERGE_HEAD` alone. | Stop Story mutation and resolve or abort the merge with trusted Git tooling outside this API. |
| `rebase` | Exactly one of `rebase-merge/` or `rebase-apply/`, alone. | Stop and finish or abort the rebase externally. |
| `cherry_pick` | `CHERRY_PICK_HEAD`, alone or with `sequencer/`. | Stop and finish or abort the cherry-pick externally. |
| `revert` | `REVERT_HEAD`, alone or with `sequencer/`. | Stop and finish or abort the revert externally. |
| `sequencer` | `sequencer/` alone. | Stop and inspect the sequenced Git work externally before continuing. |

The other two result fields are fresh 64-lowercase-hex SHA-256 identity
digests. `repository_identity_digest` binds the common Git repository;
`worktree_identity_digest` binds the exact worktree root plus its administrative
directory. Repeated inspection of unchanged identities is stable. Linked
worktrees share the repository digest but have different worktree digests, so a
later #527 correlation can reject evidence observed from the wrong worktree.
The values are opaque: they do not reveal paths, device/inode facts, marker
contents, or story text, and they do not identify a session by themselves.

Example explanatory output for a merge is:

```json
{
  "kind": "merge",
  "repository_identity_digest": "<64-lowercase-hex>",
  "worktree_identity_digest": "<64-lowercase-hex>"
}
```

`GitStatus` remains a separate observation. A repository can report
`kind="none"` while status is dirty, staged, or unmerged; conversely an operation
marker does not replace the literal index/worktree facts in `status()`. Callers
must not infer an operation kind from status alone or infer cleanliness from
this inspection.

An absent or malformed Git administration root, dual or contradictory markers,
two rebase directories, wrong marker types, symlinks, unreadable or replaced
administration state, and metadata that races during inspection fail closed as
`GitOperationError` with:

```text
code: recovery_required
operation: inspect_external_operation
repository_state: recovery_required
recovery_action: inspect_repository
```

The fixed error contains no administrative payload. Preserve every repository,
worktree, marker, index, ref, and working byte and inspect them with trusted
local tools. This method never continues, aborts, resets, cleans, checks out, or
otherwise mutates Git. #527 will correlate this fact with one session; #528 will
later block lifecycle mutation. #530 itself owns neither behavior.

<a id="linked-worktree-status-and-log"></a>

## Linked-worktree status and log

Issue #539 extends the existing read methods, without changing their
signatures or result types:

```text
status() -> GitStatus
log_metadata() -> GitLogMetadata
```

Each now accepts either a standalone repository or a securely validated linked
worktree. For a linked worktree, the adapter binds the exact root and
administration identities and inspects its external-operation markers before
the bounded read. It repeats both checks afterward, including when the read
fails or is interrupted. The opening and closing observations must agree
exactly. A stable `merge`, `rebase`, `cherry_pick`, `revert`, or `sequencer`
marker is readable; that only permits observation and never authorizes Story
or Git mutation. The marker kind is not added to `GitStatus` or
`GitLogMetadata`; use `inspect_external_operation()` when that separate fact is
needed.

`status()` reports the linked worktree's literal branch, `HEAD`, and bounded
index/worktree path states. `log_metadata()` reports that worktree's `HEAD`
history newest first, using the same content-safe commit metadata as a
standalone repository. For example:

```text
input:
  root topology: linked_worktree
  external-operation observation before/after: merge / merge
  calls: status(), log_metadata()

output:
  status={branch: story/session/review,
          head_id: <object-id>,
          entries: [
            {path: manuscript/part-01/opening.md,
             index_change: added, worktree_change: null},
            {path: core_documents/global_outline.md,
             index_change: null, worktree_change: modified}]}
  log_metadata={commits: [<HEAD metadata>, <parent metadata>, ...]}
  ordering: newest_first
  mutation: false
```

If the body reports an ordinary typed failure and the closing guard remains
stable, that failure is preserved. A stable interruption remains `cancelled`.
If the marker, physical root, or administration identity changes, guard drift
takes precedence over either outcome and no successful status/log result is
returned:

```text
input:
  call: status()
  external-operation observation before/after: none / merge

output:
  GitOperationError(
    code="recovery_required",
    operation="status",
    recovery_action="inspect_repository"
  )
  mutation: false
```

The adapter does not correlate this worktree to a story session or continue,
abort, reset, clean, checkout, stage, commit, or repair anything. Baseline
inspection/creation and `diff_metadata()` remain standalone-only. Standalone
status/log behavior, the 24-name Git facade, and the seven-method adapter
surface are unchanged; this adds no command, dependency, migration, or
compatibility path. #527 will correlate observations later, and #528 will own
lifecycle blocking.

## Cancellation, retry, and failure recovery

If `KeyboardInterrupt` or `SystemExit` interrupts baseline creation, the public
error is `cancelled` and includes the state observed immediately afterward:

| Interruption point | Reported state | Retry behavior |
| --- | --- | --- |
| Before repository initialization | `absent` | Creates the baseline normally. |
| After initialization, before the commit is installed | `initialized_without_baseline` | Rebuilds the exact index/tree and completes. |
| After the root commit object exists, before its ref is installed | `initialized_without_baseline` | Converges to one referenced root commit. |
| After the `main` ref is installed | `baseline_committed` | Detects and returns the completed baseline. |

A successful retry followed by any further retry still leaves exactly one
commit. If recovery inspection itself cannot establish the state, the error
uses `recovery_required`.

Catch `GitOperationError` and branch only on its closed facts:

```python
from story_writing_agents.git import GitOperationError

try:
    created = adapter.create_baseline(manifest)
except GitOperationError as error:
    print(
        {
            "code": error.code.value,
            "operation": error.operation.value if error.operation else None,
            "state": (
                error.repository_state.value if error.repository_state else None
            ),
            "recovery": error.recovery_action.value,
            "diagnostic": error.diagnostic,
        }
    )
```

Failures carry a fixed diagnostic, operation, optional bounded exit code,
optional repository state, and one of `none`, `retry`, `inspect_repository`, or
`recreate_workspace`. They do not retain an exception cause, Git stdout/stderr,
file content, path to the root, identity, or provider text. Invalid requests,
invalid manifests, and manifest drift default to no automatic action. Missing
Git, invalid identity, and process-start failure allow retry. Timeout, excessive
or malformed output, command, index, baseline, cancellation, general recovery,
and I/O failures direct the caller to inspect the repository. Unsafe roots and
unsupported topologies direct the caller to recreate the workspace.

## Internal enforcement and external meaning

| Internal mechanism | What the caller or user can observe |
| --- | --- |
| Bind the exact real directory identity and classify `.git` without following administrative symlinks. | One topology enum or a content-free `unsafe_root` failure. |
| Verify every manifest file by bounded, no-follow byte reads and SHA-256. | The requested manifest digest is accepted, or creation fails without committing drifted content. |
| Resolve and cross-check Git's effective author and committer before initialization. | A verified identity on a completed inspection, or `identity_invalid` before an absent repository is changed. |
| Build an exact index/tree and install only the fixed root commit and `main` ref. | One manifest-bound baseline; unrelated files remain untracked. |
| Own each local child process through timeout, stream bounds, termination, and cleanup. | Typed bounded metadata or a fixed error; no raw process stream or retained child resource. |
| Guard a linked-worktree status/log body with matching root, administration, and external-operation observations before and after it. | Literal linked branch/HEAD/status or newest-first log metadata when stable; `recovery_required` on guard drift. A stable marker grants observation only. |
| Reinspect after creation or cancellation. | `absent`, `initialized_without_baseline`, `baseline_committed`, or `recovery_required`, enabling a deterministic retry decision. |

These implementation mechanisms are not a public command escape hatch. The
supported surface remains the seven typed methods shown below.

<a id="package-internal-session-git-resources"></a>

## Package-internal session Git resources

The session-resource substrate is deliberately absent from the project, `git`,
and `workspace` facades. It exposes four direct-module operations for installed
session lifecycle composition:

```text
inspect one session       read-only
list exhaustive sessions read-only
provision one session     create or resume an exact prefix
require one ready session read-only
```

They accept a validated session ID and the initializer's exact baseline
manifest. They do not accept raw Git arguments, arbitrary refs or paths,
provider or lead text, serialized tool instructions, commit messages, or
credentials. Session Create provisions/verifies the correlated resources;
Reopen requires them ready. Provider-session readiness remains a separate
lifecycle fact and never substitutes for Git-resource readiness.

The runtime's package-internal session-artifact binding is a fifth composition
seam above `require one ready session`. Given the initialized `main` control
root and a session ID, it freshly requires the exact READY identity, opens the
worktree without following symlinks, captures a meaningful physical
`(st_dev, st_ino)` identity, repeats complete READY inspection, and requires
the named root to retain that identity. Each later repository or session-source
action takes a short synchronous cooperating-runtime guard: the common-Git
lease remains held while the exact semantic READY resources and pinned root are
checked before and after that action, then the guard closes. A provider await
or concurrent specialist execution never holds this lease; the next
filesystem action reacquires and rechecks it. The binding supplies a writing-
artifact repository rooted at and pinned to that session worktree only through
the guard's live access object. The binding itself has no repository property,
but it owns one stable exact repository object. Every repository transaction
requires live access on the calling thread and semantic READY validation before
and after; retaining the raw object or moving it to another thread grants no
authority. Same-thread nested guards yield the current access, while long-lived
reference/source adapters reacquire access per operation. Access to the
repository, resources, or duplicated root descriptor expires when the outer
guard exits. Every nested and outer exit revalidates after success, error, and
cancellation. Binding loss found there dominates an earlier body failure;
otherwise the original body failure is preserved.
Binding itself performs no artifact I/O, provisioning, provider call, or global
runtime mutation. Known readiness and workspace-validation failures become one
content-free binding failure; malformed protocol/type defects remain visible.
There is no fallback to the control root, current directory, another session,
or a previously returned binding. Repository calls reopen and recheck the
physical identity and READY semantics, so persistent replacement or Git drift
is rejected rather than granting it authority. The lease serializes
cooperating runtime operations only; it does not promise continuous exclusion
of arbitrary external Git that mutates and restores state between checkpoints.

For session `novel`, the exact identity is:

```text
branch:   story/session/novel
ref:      refs/story-writing-agents/runtime/sessions/novel
worktree: <workspace-parent>/<workspace-name>.story-worktrees/novel
```

Provision runs under one repository-wide lock. It verifies the pristine main
baseline and exhaustive correlated inventory, rejects an unregistered sibling
collision before resource mutation, captures the effective author and
committer plus one UTC whole-second timestamp, then publishes an immutable
provisioning journal before the first Git object/ref/branch/worktree mutation.
It creates and verifies an empty-tree, parentless seed object, compare-and-swap
creates the internal ref and baseline-anchored branch, creates the exact linked
worktree, verifies the complete triple, removes and synchronizes the journal,
and verifies again before returning `ready`.

The common-Git namespace is:

```text
.git/story-writing-agents/runtime/session-provisioning/
├── .lock
└── <session_id>.json
```

The persistent lock and directories use private local modes. The temporary
journal is owner-only, canonical UTF-8 JSON with a trailing newline, at most
64 KiB, and accepts no missing, duplicate, extra, malformed, or noncanonical
field. It binds schema version, `provisioning` state, session and provisioning
IDs, baseline object ID, captured author/committer names and emails, and the
seed timestamp. It is immutable: observed Git facts determine which exact
ordered prefix can resume.

An illustrative decoded journal is below. On disk it is encoded as one compact,
sorted-key JSON line followed by `\n`:

```json
{
  "baseline_commit_id": "<object-id>",
  "effective_identity": {
    "author": {"email": "author@example.invalid", "name": "Story Author"},
    "committer": {"email": "committer@example.invalid", "name": "Story Committer"}
  },
  "provisioning_id": "0123456789abcdef0123456789abcdef",
  "schema_version": 1,
  "seed_timestamp_seconds": 1780000000,
  "session_id": "novel",
  "state": "provisioning"
}
```

The seed message is `Initialize story session runtime: <session_id>`. Its tree
is the repository-format empty tree, it has no parent, signing is disabled, and
its captured author/committer timestamp uses `+0000`. This makes retry recreate
and verify the same SHA-1 or SHA-256 object ID. A completed triple may later
have advanced branch/internal tips, but its branch root must remain the v2
baseline and its internal root must remain this seed. #505 alone will interpret
later canonical commits and checkpoints.

The result vocabulary is closed:

| State | Recovery action | Meaning |
| --- | --- | --- |
| `absent` | `create` | No journal or requested resource exists. |
| `provisioning` | `retry_create` | A valid journal and exact resumable prefix exist. |
| `ready` | `reopen` | The journal is absent and the exact complete triple verifies. |
| `outcome_unknown` | `operator_recovery` | An attempted mutation cannot be classified safely. |
| `recovery_required` | `operator_recovery` | State is partial without a journal, malformed, colliding, moved, substituted, mismatched, or topologically unsafe. |

Main-root Git readiness verifies the exact baseline tree/history/topology and
uses `git status --untracked-files=no`; ignored and untracked entries are not
enumerated. Each present session worktree must have an index equal to `HEAD`
through a targeted cached-index check, without scanning its untracked paths.
The workspace layer separately reads all 25 Git-materialized scaffold leaves
and ancestors, validates local configuration/personae, and allows unstaged
story changes. Staged changes remain recovery conditions. Generic session
readiness permits later branch tips without classifying their committed
contents; story-commit and session-recovery owners validate that history.
These omissions provide opacity, not trust: optional state is validated by its
owning consumer.

An exact complete triple is authoritative and idempotent even after its journal
has been removed. Require-ready/Reopen never creates or continues a prefix.
Any unknown or non-exact Git-resource fact is preserved; there is no automatic
branch, ref, worktree, administration-entry, object, or recovery-journal
deletion. The seam has no migration, compatibility, remote, reset, clean,
prune, garbage collection, rebase, merge, or history-rewrite operation.

For user-oriented scenarios and illustrative typed values, see
[Workspaces](../guides/workspaces.md#session-git-resources). For failures and
safe next actions, see
[Session Git-resource recovery](../operations/recovery-and-limitations.md#session-git-resource-recovery).

<a id="package-internal-session-history"></a>

## Package-internal session history and process profiles

Issue #508 supplies the common infrastructure now used by #509 story commits
and #510 internal checkpoints. The history store itself does not stage a file,
create a commit/checkpoint, update a ref, consume confirmation, or expose a
CLI/runtime/facade method. Runtime and the confirmation UI consume it only
through their separate orchestration boundaries.

`git.session_history_contracts` owns the pure schema-v1 values and codecs;
`git.session_history` owns descriptor-relative durable storage;
`git.repository_lock` owns the identity-bound common-Git lease; and
`git.process` owns the fixed process profiles. These modules are package-
internal and add no name to `story_writing_agents`, `story_writing_agents.git`,
or a workspace facade.

Opening a history first requires the exact #504 session triple to be `ready`,
then takes the same repository-wide lease used by provisioning and revalidates
the session under that lease. The per-session namespace is:

```text
.git/story-writing-agents/runtime/session-history/<session_id>/
├── candidates/<64-lowercase-hex-candidate-id>.json
├── commit-operations/<32-lowercase-hex-operation-id>.json
├── checkpoint-operations/<32-lowercase-hex-operation-id>.json
└── checkpoint-inputs/<32-lowercase-hex-operation-id>/index
```

Directories request mode `0700`; JSON leaves request `0600`. JSON is bounded to
1 MiB and must be one canonical sorted-key UTF-8 object plus a trailing newline.
Reads and writes reject aliases, links, nonregular or multiply linked leaves,
case-colliding names, oversized data, noncanonical encodings, unexpected
fields, and identity substitution. Candidate publication is create-only and
idempotent only for the identical canonical value. Operation replacement is
atomic, synchronized, and accepted only for the exact next sequence and legal
successor state.

### Records and transitions

A candidate ID is the SHA-256 of its canonical content excluding the ID itself.
The record binds the ready session and branch, expected branch tip/tree,
worktree identity digest, application/proposal/correlation IDs, sorted unique
path operations with before/after SHA-256 digests, proposed commit message,
sorted impact labels, and author/committer identity-source categories. It does
not store the affected files' bytes or the effective identity values.

A story-commit operation binds its candidate, correlation, expected branch tip,
sequence, state, and optional terminal commit ID. A checkpoint operation binds
its correlation, expected internal-ref tip, sequence, state, optional terminal
checkpoint ID, and whether its alternate index is preserved. Neither operation
record contains prompt/reply text, story bytes, raw Git output, a descriptor,
credentials, or executable/confirmation authority.

| Record/current state | Allowed next state |
| --- | --- |
| Candidate `prepared` | None; the candidate is immutable. |
| New story-commit operation | `rejected` as a terminal pre-execution decision, or `running` to begin later execution. |
| Story commit `running` | `staged`, `outcome_unknown`, or `recovery_required` |
| Story commit `staged` | `committed`, `outcome_unknown`, or `recovery_required` |
| Story commit `outcome_unknown` | `committed` or `recovery_required` |
| New checkpoint operation | `running` |
| Checkpoint `running` | `committed`, `outcome_unknown`, or `recovery_required` |
| Checkpoint `outcome_unknown` | `committed` or `recovery_required` |
| Any `committed`, `rejected`, or `recovery_required` operation | None; terminal. |

`prepared` belongs only to the immutable candidate. `staged` and `rejected`
belong only to story commits. `committed` requires the same-format Git object
ID recorded in the operation. `outcome_unknown` can advance only to
`committed` or terminal `recovery_required`; no terminal record reopens or
moves backward. The store returns content-free failure/recovery facts and a
bounded inventory of IDs, states, and preserved checkpoint indices.

<a id="package-internal-session-history-snapshot"></a>

### Complete read-only snapshots

Issue #536 adds `GitSessionHistorySnapshot` and
`GitSessionHistory.snapshot() -> GitSessionHistorySnapshot` to the internal
direct module. A snapshot pairs the existing content-free
`GitSessionHistoryInspection` with sorted, complete tuples of the candidate,
story-commit operation, and checkpoint-operation records named by that
inspection. The complete tuples are excluded from `repr(snapshot)`. Internal
callers can still read the records' existing bounded fields—such as candidate
paths and digests, proposed message, impact labels, correlations, states, and
identity sources—but the snapshot adds no story-file bytes, absolute or
physical paths, raw Git output, live descriptor, capability, or executable
authority.

`snapshot()` captures and decodes the first complete inventory while holding
the existing repository lease, constructs an exact immutable snapshot, then
independently re-inspects the inventory under that lease. If a record
disappears or changes between those observations, it raises the existing typed
`recovery_required` failure and returns no partial snapshot. Malformed,
missing, unsafe, substituted, or lock-unavailable state uses the existing
content-safe session-history failure semantics. Opening with `create=False`
and finding no history returns an empty snapshot without creating the history
directory.

For example, an internal capture can report candidate `C4`, story-commit
operation `O4`, checkpoint operation `K4`, and `K4`'s preserved alternate
index while returning the exact three matching records:

```text
input:  snapshot() under the active session-history lease
output: inspection={candidate_ids:[C4],
                    commit_operations:[(O4, staged)],
                    checkpoint_operations:[(K4, outcome_unknown)],
                    preserved_checkpoint_indices:[K4]}
        candidates=[complete C4 record]
        commit_operations=[complete O4 record]
        checkpoint_operations=[complete K4 record]
        mutation=false, replay=false
```

If `O4` disappears or changes before the independent second inspection, the
result is `GitSessionHistoryError(recovery_required)`, with no partial result
and no new operation record. The method never writes, replays an operation,
or changes an index, object, ref, branch, or worktree. It has no provider,
session-reconciliation, UI, or lifecycle behavior; #527 consumes this evidence
later. No persisted format, migration, or compatibility reader is added.

### Closed Git process profiles

The four internal profiles are `read_only`, `mutation`, `identity_mutation`,
and `internal_checkpoint`. Each process uses caller-fixed arguments,
`shell=False`, `--no-pager`, bounded input/output and time, disabled hooks and
signing, disabled external diff/fsmonitor/pagers/editors/prompts, and a freshly
sanitized environment. Inherited Git directory/worktree/configuration/SSH/
index controls and other arbitrary `GIT_*` variables do not pass through;
the four standard author/committer identity variables are the only inherited
Git-environment exceptions.

`identity_mutation` accepts only a typed effective author/committer value. Only
`internal_checkpoint` accepts a `GitAlternateIndex` and typed UTC commit
timestamps, and that index value derives the
single exact operation-owned path shown above. The runner revalidates the main
root and every alternate-index ancestor before and after the child process. It
does not admit an arbitrary `GIT_INDEX_FILE`. Git commits created by the #509
and #510 internal services retain the effective author/committer and message in
Git history even though session-history JSON omits effective identity values.

### Cleanup and interruption

The checkpoint index is allocated only after a durable `running` checkpoint
record. It is preserved when the result is `outcome_unknown` or
`recovery_required`; removal is allowed only for that exact operation after a
durable `committed` record and synchronization. No cleanup routine removes
candidate/operation records, Git objects/refs, provisioning journals, user
files, an ambiguous index, or unrelated repository data.

If process exit, cancellation, I/O failure, or lost process output leaves the
result uncertain, the operation owner must inspect the durable operation and
repository facts. It must not replay a consumed confirmation or rerun Git
from `running`/`staged` merely because no success was returned. See
[session-history recovery](../operations/recovery-and-limitations.md#session-history-recovery)
and [session-history privacy](../operations/privacy-and-provider.md#session-history-exposure).

<a id="package-internal-exact-story-commits"></a>

## Package-internal exact story commits

Issue #509 implements the local Git operation behind the separately confirmed
Lead story-commit workflow. The service remains a direct-module capability:
there is no standalone CLI command, provider path, or export from `story_writing_agents`,
`story_writing_agents.git`, or a workspace facade. Successful typed application
evidence, Lead projection, and confirmation reach it only through runtime
orchestration. The separate #510 checkpoint service is described below.

The three internal owners are:

| Module | Responsibility |
| --- | --- |
| `git.story_commit_contracts` | Frozen typed application evidence, candidate paths/candidate, lead projection, structured confirmation, decision/state/failure/recovery enums, content-free result, and projection. |
| `git.story_commit_candidates` | Prepare/load/revalidate one exact candidate, bind content-bearing staging inputs only in process memory, and publish immutable candidate history. |
| `git.story_commits` | Process-local authority, Commit/Reject/Revise decision handling, exact execution, durable inspection, and conservative restart recovery. |

The direct-module operations are:

```text
prepare_story_commit_candidate(root, manifest, evidence, message, impact)
load_story_commit_candidate(root, manifest, session_id, candidate_id)
revalidate_story_commit_candidate(binding, history=None)
project_story_commit_candidate(candidate)

StoryCommitService(root, manifest, session_id)
  .prepare(evidence, message, impact)
  .authorize(candidate, commit_confirmation)
  .execute(authority)
  .reject(candidate, reject_confirmation)
  .revise(candidate, revise_confirmation)
  .inspect(operation_id)
  .recover(operation_id)
```

These are internal contracts, not a stable application API. The serializable
values contain no method or hidden capability. `StoryCommitAuthority` can be
created only by the service after one exact fresh Commit decision; it is bound
to that service owner, process, and thread, cannot be copied/pickled, and is
consumed before the first filesystem or Git action. Reject/Revise/Apply/
consultation values cannot authorize execution, a confirmation ID cannot be
reserved twice by the same service, and candidate/proposal/correlation IDs must
all match. No authority survives restart; the runtime must obtain a fresh user
decision instead of presenting an old serialized decision again.

### Prepare and lead projection

`StoryCommitApplicationEvidence` binds the session, application/proposal/
correlation IDs, exact `ArtifactChangeSet`, and its projected-state digest.
Prepare requires that the change set is already applied exactly in the ready
session worktree. Runtime composition additionally pins preparation to the
physical worktree identity that successfully completed Apply; the same pin is
retained through authorization and execution. It proves:

- symbolic `HEAD` names the exact session branch and equals the ready tip;
- the real index has no pre-staged entry;
- the complete current canonical artifact mapping equals the projected change
  set, with no unreported canonical change;
- candidate paths are exact unique canonical artifact paths;
- the expected full tree can be derived without writing a Git object;
- effective author/committer identity and its source are stable;
- the internal ref, main `HEAD`, main status, session/main directory identities,
  and worktree identity match; and
- unrelated noncanonical files are excluded and left byte-identical.

Its only durable mutation is create-only publication of the immutable candidate
under #508 session history. It does not alter a working file, index, object,
ref, branch, or commit. Repeating the exact preparation returns the same
content-addressed candidate.

The lead projection exposes candidate/session/branch/tip/tree IDs, exact path
operations and before/after SHA-256 digests, bounded proposed message and
sorted impact boundary, application/proposal/correlation IDs, and identity-
source categories. It deliberately omits story bytes, effective identity
values, worktree identity, runtime binding, authority, raw Git output, and
provider text.

Artifact application operations normalize as follows:

| Applied operation | Candidate path entries |
| --- | --- |
| Create | destination `create` with only `after_sha256` |
| Patch or replace | path `update` with distinct `before_sha256` and `after_sha256` |
| Delete | source `delete` with only `before_sha256` |
| Rename | source `delete` plus destination `create`, then all entries sorted by UTF-8 path bytes |

### Exact Commit sequence

Authorize revalidates the process-local binding before minting one-use
authority. Execute consumes that authority, takes the common-Git/session-history
lease, independently requires the exact READY session resources, and places
semantic READY and Apply-root checks around durable observations and
transitions. Before intentional staging, READY requires the clean index. After
staging, phase-aware checks instead require the exact candidate index/worktree,
branch and internal-ref tips, registered session worktree, main HEAD/status,
and physical roots. READY/history resource loss detected before the `RUNNING`
record is durable maps to `recovery_required`; ambiguity afterward is
`outcome_unknown`. Candidate/root replacement found before execution remains
candidate drift and requires
repreparation. Execution then follows this fixed order:

```text
publish and fsync RUNNING operation evidence
-> for each sorted candidate path only:
     create/update: hash-object -w --no-filters --stdin
                    update-index --add --cacheinfo 100644,<blob>,<literal-path>
     delete:        update-index --force-remove -- <literal-path>
-> write-tree and require candidate.expected_tree
-> publish and fsync STAGED operation evidence
-> commit-tree <expected-tree> -p <prepared-tip>
   using the prepared literal message and effective identity
-> update-ref refs/heads/<session-branch> <commit> <prepared-tip>
-> verify exact new session tip/tree plus unchanged main head/status and
   internal ref; literal targeting leaves main index and unrelated bytes alone
-> publish and fsync COMMITTED evidence with the commit ID
```

If final COMMITTED publication fails after the branch update, the service
records `outcome_unknown` for the same operation when it can still prove the
phase-specific topology. It never reports an unproved commit as completed.

The branch update is compare-and-swap; it cannot silently replace a moved tip.
The commit has exactly one parent, exact expected tree/message, no hook/signing/
filter/textconv/alias/editor/pager/credential widening, and the prepared
effective author and committer. The same algorithm works in SHA-1 and SHA-256
repositories. It does not update the internal checkpoint ref, main root, remote,
or any noncandidate path. It provides no reset, clean, checkout, merge, rebase,
push, deletion, migration, or compatibility operation.

### Decisions, results, and restart

Reject records one terminal `rejected` decision without changing files, index,
objects, or refs; the candidate remains available. Revise records `rejected`
under the deterministic revision identity, removes the live binding, and makes
that candidate ineligible for later authorization. A changed proposal therefore
requires newly applied evidence, a new candidate, and fresh confirmation.

`StoryCommitResult` is content-free: session/candidate/operation/correlation,
state/recovery action, branch/tip, and optional committed object ID. Inspection
reads durable evidence only. Recovery performs read-only Git classification
plus monotonic history updates; it never resets the real index, stages again,
reuses authority/confirmation, or creates a commit.

Execution, inspection, and recovery keep their semantic checkpoints inside the
live session-history lease. Inspection and recovery check READY around owner
reads; recovery uses the same intentional-staged topology while classifying or
advancing a staged operation, so ordinary READY's clean-index rule does not
misclassify the service's own exact staging.

Terminal story-commit inspection and recovery are read-only with respect to
session history and require the current session to remain READY. A historical
`committed` record does not require its commit to remain current `HEAD`: the
recorded commit must exactly match its candidate's tree, sole parent, and
message, and it must equal or be an ancestor of the current READY branch tip.
A reset away from that commit, divergent history, a missing commit, or any
candidate mismatch fails `recovery_required`. Historical `rejected` and
`recovery_required` records remain immutable inspectable terminal evidence
under current READY. Neither `inspect()` nor `recover()` writes or advances a
terminal record.

For a runtime-composed identity-bound service, inspection and recovery require
the original Apply worktree identity before and between their owner reads and
before any monotonic history transition. If that root has been replaced, they
return `recovery_required` and make no transition from the replacement's
operation, status, index, tree, or commit facts.

- Exact expected staging with the old branch tip is returned as `staged` /
  `operator_recovery`.
- A branch tip that is exactly the candidate tree, sole parent, and message is
  finalized as `committed` without another commit.
- A conflicting index/worktree is terminal `recovery_required`.
- An unproved or foreign branch result remains `outcome_unknown` / `inspect`.

See the [user scenarios](../guides/workspaces.md#exact-confirmed-story-commits),
[recovery matrix](../operations/recovery-and-limitations.md#session-history-recovery),
and [privacy boundary](../operations/privacy-and-provider.md#session-history-exposure).

<a id="package-internal-session-checkpoints"></a>

## Package-internal session checkpoints

Issue #510 implements exact local commits of selected session runtime state to
the session internal ref. It remains a direct-module capability: there is no
CLI command, provider call, lead binding, workspace method, or export from
`story_writing_agents` or `story_writing_agents.git`.

| Module | Responsibility |
| --- | --- |
| `git.session_checkpoint_contracts` | Closed path classifier; frozen owner, path-operation, delta request/result, state, failure, and recovery values. |
| `git.session_checkpoints` | Exact no-op proof, alternate-index tree construction, commit/internal-ref CAS, durable inspection, and conservative recovery. |

The supported internal direct-module surface is
`classify_session_checkpoint_path(path, session_id)`, the frozen contract
values listed by `git.session_checkpoint_contracts.__all__`, and
`SessionCheckpointService(root, manifest, session_id)` with `checkpoint()`,
`inspect(operation_id)`, and `recover(operation_id)`. These are not a stable
application API. The complete allowlist and five-owner mapping are in the
[workspace guide](../guides/workspaces.md#included-paths-and-owners).

`SessionCheckpointRequest` contains unique snapshots sorted by UTF-8 path
bytes. Each snapshot is exactly `create`, `update`, or `delete` with the
required before/after SHA-256 proof and, for create/update, exact bytes held in
memory. The declared owner must match the path family. Missing snapshots do not
delete paths, and the service never recursively discovers files. Requests are
bounded to 4,096 paths, 8 MiB per supplied body, and 64 MiB of supplied or
existing checkpoint-tree content.

Under the shared common-Git lease, `checkpoint()` validates the ready session,
main/session symbolic heads and real indexes, pristine main status, current
internal-ref tree, effective local identity, and the complete resulting path
classification. If the request is empty or produces the same proven tree, it
returns `unchanged` without an operation record, alternate index, object, or ref
mutation. Otherwise it performs exactly:

```text
fsync RUNNING operation with expected tip/tree and salted commit-intent digest
-> allocate checkpoint-inputs/<operation-id>/index
-> read-tree <expected-internal-tip> into that isolated index
-> hash exact create/update bytes with filters disabled; update/remove exact paths
-> verify index entries and write-tree == expected tree
-> commit-tree <expected-tree> -p <expected-internal-tip>
   using captured effective identity, fixed message, and captured UTC timestamps
-> CAS refs/story-writing-agents/runtime/sessions/<session-id>
-> verify exact commit intent/tip and unchanged repository invariants
-> fsync COMMITTED operation -> remove only the completed alternate index
```

The commit has one parent and works identically for SHA-1 and SHA-256 object
formats apart from object-ID length. The fixed message records session,
correlation, and operation IDs. Author and committer use Git's exact effective
local names/emails; both timestamps use the one captured second at `+0000`.

The operation never uses a real index and never targets a working file, story
branch, symbolic `HEAD`, canonical artifact, unrelated path, pristine main ref,
or remote. It exposes no arbitrary `GIT_*`, command, reset, clean, checkout,
merge, rebase, push, migration, or compatibility mechanism. The shared lease
serializes it with #509. If a checkpoint moves the internal ref before a
prepared story commit authorizes, that candidate fails revalidation before any
story-commit mutation.

Recovery never repeats `commit-tree` or CAS. An exact post-CAS tip matching the
salted intent is finalized as `committed`; an unchanged expected tip after
uncertain pre-CAS work becomes terminal `recovery_required`; and a foreign tip
remains `outcome_unknown`. It never overwrites the foreign tip. Only an exact
durably committed operation permits removal of its alternate index. See the
[checkpoint scenarios](../guides/workspaces.md#exact-session-runtime-checkpoints),
[recovery matrix](../operations/recovery-and-limitations.md#session-history-recovery),
and [privacy boundary](../operations/privacy-and-provider.md#session-history-exposure).

## Safety and privacy boundary

Every operation is local and synchronous. Internally the adapter uses fixed
argument sequences with `shell=False`, a 30-second process timeout, bounded
stdin/stdout/stderr, disabled terminal prompting, and no pager. Read-only
operations disable optional Git locks. All processes and streams are closed;
on timeout or interruption, the adapter terminates and, if necessary, kills
the child before returning.

Repository- or user-controlled execution extensions are blocked or bypassed:

- hooks and inherited initialization templates;
- commit and tag signing programs;
- filesystem monitors and pagers;
- external diff and text-conversion drivers;
- clean/smudge filters, including required filters;
- configurable aliases and credential helpers; and
- inherited Git directory/worktree, configuration-path, SSH, index, and similar
  `GIT_*` environment overrides, except the four standard author/committer
  identity fields. Explicit identity overrides are typed; the sole alternate
  index is admitted only by the validated internal-checkpoint profile described
  above.

Selected bytes are hashed and stored without filters. The API defines no
remote or credential-bearing operation; terminal prompts and askpass are also
disabled. Effective identity configuration may be read, but neither repository
nor global configuration is mutated.

Metadata is bounded and content-safe: process stdout is capped at 8 MiB,
stderr at 64 KiB, and stdin at 64 MiB; status is capped at 65,536 entries, diff
at 131,072 entries, and log at 128 commits. Raw process output and file or
commit-message bodies never cross the public boundary.

## Supported imports and methods

Import the 24-name direct facade; none of these names is re-exported by
`story_writing_agents`:

```python
from story_writing_agents.git import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitBaselineInspection,
    GitBaselineManifest,
    GitChangeKind,
    GitCommitMetadata,
    GitDiffEntry,
    GitDiffMetadata,
    GitEffectiveIdentity,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitFailureCode,
    GitIdentity,
    GitLogMetadata,
    GitManifestEntry,
    GitOperationError,
    GitOperationKind,
    GitRecoveryAction,
    GitRepositoryAdapter,
    GitRepositoryState,
    GitRepositoryTopology,
    GitStatus,
    GitStatusEntry,
    GitTopologyKind,
)
```

`GitRepositoryAdapter(root)` performs no I/O during construction. Its complete
public method set is:

```text
inspect_topology() -> GitRepositoryTopology
inspect_baseline(manifest: GitBaselineManifest) -> GitBaselineInspection
create_baseline(manifest: GitBaselineManifest) -> GitBaselineInspection
status() -> GitStatus
diff_metadata() -> GitDiffMetadata
log_metadata() -> GitLogMetadata
inspect_external_operation() -> GitExternalOperationInspection
```

There is no migration or compatibility mode and no arbitrary process runner.
Use [Package API and exports](package-api.md) for the facade inventory and
[Workspaces](../guides/workspaces.md) for the stricter automatic `story init`
composition, journal, and readiness behavior.
