# Workspaces

> **Availability:** `story init` creates and validates one exact Git-backed
> schema-v2 initialization root. It does not run an agent, write story content,
> create a session resource, or authorize a later story commit.

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

For a future session ID `novel`, this policy names:

```text
branch:   story/session/novel
ref:      refs/story-writing-agents/runtime/sessions/novel
worktree: <workspace-parent>/<workspace-name>.story-worktrees/novel
```

#454 records only this grammar. It creates no session ID, branch, internal ref,
or sibling worktree. #449 owns those resources and the separate confirmation
required for later user-authored commits. Until that composition is available,
the original `main` root remains the exact initialization baseline.

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
edits belong in #449's session worktree.

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
