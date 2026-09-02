# Workspaces

> **Availability:** `story init` creates and validates one exact Git-backed
> schema-v2 initialization root. It does not run an agent, write story content,
> create a session resource, or authorize a later story commit. A separate
> package-internal seam can now materialize exact session Git resources, but
> current `story session` commands do not call it until #455.

Key terms: **initialization root**—the pristine `main` baseline; **marker**—the
strict `config/workspace.json` identity; **journal**—the temporary durable
initialization record; **ready**—the marker, scaffold, Git baseline, index,
tree, branch, and status all match exactly.

## User impact

Users can:

- initialize an absent path or empty safe directory;
- receive four empty, named core documents ready for later lead-guided work;
- receive one local `main` root commit named `Initialize story workspace`;
- validate an unchanged ready root by rerunning `story init`; and
- resume an exact interrupted initialization with a fresh command.

Users cannot:

- adopt arbitrary existing files or a markerless/legacy workspace;
- overwrite packaged prompts, migrate, upgrade, or enable compatibility;
- initialize inside another repository, submodule, linked worktree, sparse
  checkout, bare repository, or mismatched Git root;
- use initialization as authorization for a later commit; or
- treat a dirty, drifted, partial, or unsafe root as ready.

## Before and after

Earlier initialization filled missing scaffold entries, preserved arbitrary
existing bytes, optionally overwrote prompts, created no core files, and
created no Git baseline. The current contract is closed:

```text
story init PATH
  -> classify PATH before mutation
  -> write one exact initializing-v2 journal
  -> install the exact scaffold and four empty core files
  -> create one exact main baseline using the user's effective Git identity
  -> verify the marker, manifest, commit, index/tree, and status
  -> remove and synchronize the journal
  -> verify the exact clean result again
  -> print success
```

The transient journal is strict canonical UTF-8 JSON with a trailing newline:

```json
{
  "manifest_digest": "<64-lowercase-hex-sha256>",
  "schema_version": 2,
  "state": "initializing"
}
```

It is not part of the Git manifest. Do not edit or delete it manually; its
digest is the evidence a fresh process uses to distinguish exact recovery from
ambiguous partial state.

## Initialize and verify

Configure an effective Git author and committer under your normal Git policy,
then choose an absent path or empty directory:

```bash
story init ./my-novel
```

Success prints the resolved absolute path:

```json
{
  "workspace": "/absolute/path/to/my-novel"
}
```

You can inspect the externally visible Git facts with ordinary Git reads:

```bash
git -C ./my-novel branch --show-current
git -C ./my-novel rev-list --count HEAD
git -C ./my-novel log -1 --pretty=%s
git -C ./my-novel status --porcelain
```

The outputs are `main`, `1`, `Initialize story workspace`, and no status rows.
Repeating `story init ./my-novel` prints the same workspace JSON after
validation and leaves the commit count at one.

Initialization itself emits no debug-log event. A successful root has no
initialization journal or other untracked initializer residue.

## What each state means

| Observed state | User-visible behavior | Safe action |
| --- | --- | --- |
| Missing root or empty safe directory | Initialization creates the journal and converges to ready v2. | Wait for success before using the root. |
| Exact journal plus matching partial scaffold | A fresh command validates every existing byte and continues only missing exact steps. | Rerun the same `story init PATH` in a fresh process. |
| Exact baseline plus journal | The initializer verifies the baseline, removes only the journal, and verifies again. | Rerun with a fresh process. |
| Ready v2, journal absent, clean | `story init` is validation-only and creates no second commit. | Continue only through the supported session-worktree workflow. |
| Effective Git identity unavailable | Initialization reports `ConfigurationError`; the exact journal/scaffold may remain, but an absent repository is not created. | Configure a valid effective author and committer, then rerun with a fresh process. |
| Recognizable legacy/markerless root or non-v2 numeric version | The command reports that migration and compatibility are unsupported. | Preserve it and create a separate new workspace. |
| Malformed, arbitrary, dirty, drifted, or unsafe state | The command preserves all bytes and reports invalid/recovery-required state. | Stop; inspect or restore trusted state outside the runtime. Do not delete the journal or reset files to force success. |

After any failed `Workspace.initialize()` attempt, that Python object is
terminal. A fresh object or CLI process must reclassify the durable state before
recovery. This prevents an object that observed a failure from guessing that
the root is now safe.

## Marker and session naming policy

`config/workspace.json` is strict canonical UTF-8 JSON with a trailing newline:

```json
{
  "schema_version": 2,
  "session_topology": {
    "branch_prefix": "story/session/",
    "internal_ref_prefix": "refs/story-writing-agents/runtime/sessions/",
    "worktree_sibling_suffix": ".story-worktrees"
  }
}
```

For session ID `novel`, this policy names:

```text
branch:   story/session/novel
ref:      refs/story-writing-agents/runtime/sessions/novel
worktree: <workspace-parent>/<workspace-name>.story-worktrees/novel
```

`story init` records only this grammar. The package-internal #504 lifecycle can
create the correlated triple; #455 owns its connection to the installed
session commands, and #505 owns later confirmed story commits and internal
checkpoints. The original `main` root remains the exact initialization
baseline throughout.

## Session Git resources

This is internal runtime infrastructure, not a command available to a user in
this revision. It gives a later session composition one isolated place for
mutable files without changing the initialization root.

Users will be able to create or resume one exact triple through #455. They can
already rely on the implemented substrate to preserve exact resources across
process exit and to reject ambiguous state. They cannot make current
`story session create` provision the triple, use Reopen to create or repair
resources, delete a durable session, migrate an older layout, contact a remote,
or request reset, clean, prune, garbage collection, rebase, merge, or history
rewrite through this seam.

The five Git-resource states are separate from the provider-session `ready`
and `paused` states shown by current commands:

| Git-resource state | Meaning | Safe next action |
| --- | --- | --- |
| `absent` | No journal or requested branch/ref/worktree exists. | The future explicit Create composition may provision it. |
| `provisioning` | A valid immutable journal and one exact ordered prefix exist. | Retry the same Create operation; it resumes only the next missing step. |
| `ready` | The journal is absent and the complete correlated triple has passed final verification. | Reopen may validate it without mutation; repeated Create returns the same identity. |
| `outcome_unknown` | A mutation was attempted but the runtime could not prove its result. | Stop automatic replay, preserve all state, and inspect with trusted local tools. |
| `recovery_required` | A journal, branch, ref, worktree, path, anchor, or topology is partial, malformed, moved, substituted, colliding, or inconsistent. | Preserve the evidence and perform explicit operator recovery; neither Create nor Reopen guesses a repair. |

The following is an illustrative projection of the package-internal seam. It
is not current terminal input or output:

```json
{
  "input": {
    "operation": "provision",
    "main_root": "/stories/my-novel",
    "session_id": "novel"
  },
  "output": {
    "state": "ready",
    "recovery_action": "reopen",
    "branch": "story/session/novel",
    "internal_ref": "refs/story-writing-agents/runtime/sessions/novel",
    "worktree": "/stories/my-novel.story-worktrees/novel",
    "baseline_commit_id": "<object-id>",
    "seed_commit_id": "<object-id>",
    "current_branch_tip": "<object-id>",
    "current_internal_tip": "<object-id>"
  }
}
```

Here are the other concrete seam outcomes. These are explanatory projections,
not serialized terminal records:

- With an exact ready triple, input `provision(novel)` outputs
  `ready/reopen` with the same baseline, seed, branch, ref, and worktree.
- With that same triple, input `require_ready(novel)` outputs `ready/reopen`
  without mutation.
- With a journal, exact seed ref, and exact baseline branch but no worktree,
  input `inspect(novel)` outputs `provisioning/retry_create`; subsequent input
  `provision(novel)` creates only the missing exact worktree and outputs
  `ready/reopen`.
- With an unregistered sibling directory, input `provision(novel)` outputs
  `recovery_required/operator_recovery` without mutation.
- If a mutation was attempted but neither its result nor recovery inspection
  can be proved, input `provision(novel)` outputs
  `outcome_unknown/operator_recovery`.

Repeated provision therefore creates no second branch, ref, worktree, or seed.
Reopen uses the read-only require-ready seam. An exact interrupted prefix
resumes only its next missing step. A pre-existing unregistered sibling path is
detected before the journal, object, ref, or branch is created and is never
removed.

Provisioning leaves these initialization-root observations unchanged:

```text
branch: main
HEAD:   <same-baseline-object-id>
tree:   <same-baseline-tree-id>
index:  <same-baseline-index>
status: clean
```

Inside `/stories/my-novel.story-worktrees/novel`, discovery returns that exact
session root, session ID `novel`, and main root `/stories/my-novel`. Discovery
inside the initialization root returns the main root. A differently located or
unrelated marker-bearing linked worktree is rejected rather than adopted.

Pause, release, close, and process exit do not remove a branch, ref, worktree,
administration entry, seed object, or recovery journal. See
[session Git recovery](../operations/recovery-and-limitations.md#session-git-resource-recovery)
and the [internal Git-resource contract](../reference/git-adapter.md#package-internal-session-git-resources).

## Core documents and role files

Initialization creates these exact zero-byte Markdown files:

- `core_documents/global_outline.md`
- `core_documents/part_outlines.md`
- `core_documents/character_sheets.md`
- `core_documents/narrative_rules.md`

Empty means that no story decision exists yet; it is not generated content.
Later lead workflows must identify incomplete or conflicting evidence and
propose user-confirmed changes. Initialization never fills these files.

The four packaged role prompts remain at
`config/agents/<agent-id>/AGENTS.md`, for `lead_orchestrator`,
`plot_architect`, `character_custodian`, and `execution_specialist`. They are
part of the exact baseline. There is no prompt-overwrite option. Editing a
prompt, configuration file, core document, or adding any other file to the
original root makes a fresh readiness check reject that root; later supported
edits belong in the exact session worktree after #455 composes it.

Persona templates are plaintext UTF-8 Markdown under
`config/persona_templates/`. Do not put secrets in them.

## Discovery and explicit paths

Without `--workspace`, supported commands search upward for the nearest
*recognizable* workspace boundary. A marker, initialization journal, or known
legacy sentinel makes a directory recognizable. Discovery validates that
nearest boundary and never skips a malformed or unsupported inner workspace to
select an outer one.

An explicit path wins but does not initialize it. Nested repositories and
nested workspace roots are not supported. For automation, pass the exact
initialized root and treat any readiness error as fail-closed.

For the exact generated tree, use
[Session storage](../reference/configuration/session-storage.md). For public
Python behavior, use [Workspace API](../reference/workspace-api.md). For path,
Git-topology, and symlink guarantees, use
[Filesystem safety](../operations/filesystem-safety.md). For recovery, use
[Recovery and limitations](../operations/recovery-and-limitations.md#workspace-initialization-recovery).
