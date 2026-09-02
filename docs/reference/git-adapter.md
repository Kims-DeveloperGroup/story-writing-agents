# Safe Git adapter and story baseline

The `story_writing_agents.git` package is a direct Python API for inspecting one
local repository and creating one exact initial story-workspace commit. It is
provider-independent and uses only the Python standard library plus the local
Git executable.

This is the complete availability boundary for issue
[#448](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/448):

- Python callers can classify repository topology, inspect or create the fixed
  baseline, and read bounded status, diff, and log metadata.
- `story init` now composes this adapter internally after it records the exact
  schema-v2 initialization journal and installs its exact 22-file scaffold.
  The CLI does not expose manifest selection or another Git operation.
- The public adapter still has no session branch, worktree, internal-ref, or
  later-commit method. Separate package-internal #504 modules now own exact
  session resources for #455 composition. Confirmed story commits and internal
  checkpoints remain #505.

The adapter is not a general Git client. It accepts no command, argument list,
revision, ref, commit message, credential, prose, or provider-tool input.

## User impact

A Python integration can now establish and verify a deterministic history
boundary without staging unrelated files or exposing story text through its
result objects. After successful creation, users see a local `main` branch with
one root commit named `Initialize story workspace`. The commit and index
contain exactly the regular files selected by the caller's manifest.

Users running `story init` on an absent/empty safe root receive this baseline
automatically with the initializer's exact 22-file manifest. That composition
is stricter than the adapter alone: the workspace lifecycle rejects arbitrary
extra files even though the direct adapter leaves unrelated untracked files
unchanged.

Users cannot choose a manifest from the CLI, use this API to make a second
commit, apply a story proposal, manage a session worktree, contact a remote, or
repair an unexpected repository. Initialization authority covers only the one
fixed root commit. The adapter reports a closed recovery state instead of
guessing how to change an unexpected repository.

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
| `linked_worktree` | `.git` is an administrative-file link. This baseline adapter rejects it; the separate #504 owner accepts only an exact correlated session worktree. |
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

For a non-standalone topology, baseline inspection reports
`recovery_required`, baseline creation refuses it, and status/diff/log reads
report `unsupported_topology`. A symlinked or replaced root fails as
`unsafe_root`.

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
| Reinspect after creation or cancellation. | `absent`, `initialized_without_baseline`, `baseline_committed`, or `recovery_required`, enabling a deterministic retry decision. |

These implementation mechanisms are not a public command escape hatch. The
supported surface remains the six typed methods shown below.

<a id="package-internal-session-git-resources"></a>

## Package-internal session Git resources

The #504 substrate is deliberately absent from the project, `git`, and
`workspace` facades. It exposes four direct-module operations solely for the
later #455 composition:

```text
inspect one session       read-only
list exhaustive sessions read-only
provision one session     create or resume an exact prefix
require one ready session read-only
```

They accept a validated session ID and the initializer's exact baseline
manifest. They do not accept raw Git arguments, arbitrary refs or paths,
provider or lead text, serialized tool instructions, commit messages, or
credentials. Current `story session create` and `story session reopen` do not
call them. A command reporting provider-session `ready` therefore does not yet
report Git-resource readiness.

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

An exact complete triple is authoritative and idempotent even after its journal
has been removed. Require-ready/Reopen never creates or continues a prefix.
Any unknown or non-exact fact is preserved; there is no automatic branch, ref,
worktree, administration-entry, object, or recovery-journal deletion. The seam
has no migration, compatibility, remote, reset, clean, prune, garbage
collection, rebase, merge, or history-rewrite operation.

For user-oriented scenarios and illustrative typed values, see
[Workspaces](../guides/workspaces.md#session-git-resources). For failures and
safe next actions, see
[Session Git-resource recovery](../operations/recovery-and-limitations.md#session-git-resource-recovery).

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
- inherited Git directory/worktree, configuration-path, SSH, and similar
  `GIT_*` environment overrides, except the four validated identity fields.

Selected bytes are hashed and stored without filters. The API defines no
remote or credential-bearing operation; terminal prompts and askpass are also
disabled. Effective identity configuration may be read, but neither repository
nor global configuration is mutated.

Metadata is bounded and content-safe: process stdout is capped at 8 MiB,
stderr at 64 KiB, and stdin at 64 MiB; status is capped at 65,536 entries, diff
at 131,072 entries, and log at 128 commits. Raw process output and file or
commit-message bodies never cross the public boundary.

## Supported imports and methods

Import the 22-name direct facade; none of these names is re-exported by
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
```

There is no migration or compatibility mode and no arbitrary process runner.
Use [Package API and exports](package-api.md) for the facade inventory and
[Workspaces](../guides/workspaces.md) for the stricter automatic `story init`
composition, journal, and readiness behavior.
