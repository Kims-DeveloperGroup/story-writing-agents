# Workspaces

> **Availability:** `story init` creates and validates one exact Git-backed
> schema-v2 initialization root. It does not run an agent, write story content,
> create a session resource, or authorize a later story commit. A separate
> package-internal seam can now materialize exact session Git resources, but
> current `story session` commands do not call it until #455. Another internal
> seam persists content-free commit/checkpoint evidence. An internal service
> can now execute exact confirmed story commits, while #491 and #492 still own
> application production and the user-facing lead confirmation UI. A second
> internal service can now checkpoint a closed set of session runtime paths;
> neither service is wired to a command or lead action.

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
session commands. #505 coordinates later history work. #509 now supplies exact
story commits and closed-path checkpoints internally; #491 and #492 still own
application production and the lead confirmation UI. The original
`main` root remains the exact initialization baseline throughout.

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

<a id="durable-session-history-infrastructure"></a>

## Durable session-history infrastructure

This section explains infrastructure now used by internal exact story commits
and session runtime checkpoints. There is no `story commit` or checkpoint
command in this revision, and the lead cannot trigger either operation yet.

Before #508, an interrupted future Git operation would have had no dedicated,
restart-safe record showing which candidate and repository tip it concerned.
The runtime now has an internal, content-free evidence store and closed Git
process profiles. The user-facing actions remain later work:

```text
Available internally now (#508)
  ready session identity
  -> durable candidate/operation facts
  -> fixed monotonic state
  -> safe process profile and shared repository serialization

Internal exact story commit now (#509)
  typed successful application evidence + exact structured decision
  -> prepare candidate -> authorize Commit -> stage -> commit -> classify

Future lead integration (#491 + #492)
  lead: proposes exact paths, impact, and commit message
  user: chooses Commit, Reject, or Revise
  runtime: supplies the typed evidence/decision to #509

Internal checkpoint now (#510)
  owner delta -> records running -> isolated index -> internal-ref CAS
  user files and the ordinary session index are not borrowed for the checkpoint
```

Later application output must be based on the durable classification rather
than a blind retry. For example, interruption after a Git process starts can
produce `outcome_unknown`; restart must inspect the exact repository facts
instead of reusing the user's confirmation or checkpoint request. No current UI
displays these internal classifications.

### What the recorded states mean

| State | External meaning in the future workflow | Safe next action |
| --- | --- | --- |
| `prepared` | An immutable candidate describes exact affected paths and before/after digests. It is evidence, not confirmation and not a Git mutation. | Present/revalidate it through the future confirmation workflow; prepare a new candidate if its facts are stale. |
| `running` | An operation owner durably recorded that work started against one expected tip. Success is not proved. | If interrupted, inspect the operation and repository before any retry. Never infer that confirmation or a checkpoint request remains reusable. |
| `staged` | A future story-commit operation recorded that staging completed, but no commit is proved. Checkpoints do not use this state. | Preserve the worktree/index and inspect; do not restage or commit blindly. |
| `committed` | The terminal record includes the resulting story commit or checkpoint object ID. | Reverify the referenced object/tip and report success through the future owning workflow. |
| `rejected` | A story-commit proposal was declined before execution. It is terminal and authorizes nothing. Checkpoints do not use this state. | Leave it as evidence; prepare a new candidate for a materially different proposal. |
| `outcome_unknown` | Git may have mutated, but the result could not be proved. | Stop replay, preserve all evidence, and inspect. A later owner may classify the same operation as `committed` or `recovery_required`. |
| `recovery_required` | The facts are unsafe, inconsistent, or cannot be reconciled automatically. This is terminal. | Stop writers, preserve the repository and history subtree, and use explicit operator recovery or a trusted backup. |

Candidate records are immutable. Operation records advance by increasing
sequence through only the accepted state graph; terminal results cannot move
backward. The local records retain session/operation/proposal/correlation IDs,
canonical relative paths, before/after SHA-256 digests, expected Git tips or
trees, a bounded proposed commit message and impact labels, and whether author
and committer identity came from environment, repository, global, or system
configuration. They contain no core-document/manuscript bytes, provider
prompt/reply, raw Git output, credential, descriptor, writable capability, or
serialized confirmation authority. A later real Git commit will still retain
its effective author/committer identity and message in ordinary Git history.

The common-Git layout is local and per-session:

```text
.git/story-writing-agents/runtime/session-history/<session_id>/
├── candidates/<candidate_sha256>.json
├── commit-operations/<operation_id>.json
├── checkpoint-operations/<operation_id>.json
└── checkpoint-inputs/<operation_id>/index
```

The alternate index is not a general-purpose file path. Only the internal
checkpoint process profile accepts the exact index derived from its ready
repository, session ID, and operation ID. It is retained for
`outcome_unknown` or `recovery_required`, and can be removed only after that
exact checkpoint is durably `committed`. Candidate and operation evidence is
not automatically deleted. See the
[exact internal contract](../reference/git-adapter.md#package-internal-session-history),
[recovery table](../operations/recovery-and-limitations.md#session-history-recovery),
and [privacy implications](../operations/privacy-and-provider.md#session-history-exposure).

<a id="exact-confirmed-story-commits"></a>

## Exact confirmed story commits

The capability in this section is package-internal. It proves the runtime can
perform the safe Git part of a confirmed story commit, but there is no installed
command and no current lead conversation calls it. #491 must first supply typed
evidence that an exact artifact change set was successfully applied, and #492
must turn an interactive user choice into the structured decision shown below.

### User impact and complete flow

With that integration, users can review every affected canonical path,
before/after digest, proposed message, impact boundary, branch tip/tree, opaque
correlations, and author/committer identity source before choosing Commit,
Reject, or Revise. Applying files never stages or commits them. A stale,
revised, mismatched, or already-used decision never persists as replayable Git
authority: the same service rejects a reused confirmation ID, and a process
restart loses every authority. #492 must obtain a fresh user decision after
restart rather than resubmitting an old decision value.

```text
successful artifact application evidence (#491)
  -> Prepare: verify exact files/session/index and persist immutable candidate
  -> lead shows content-free candidate projection (#492)
  -> user chooses Commit | Reject | Revise (#492)
  -> Commit only: one-use authority -> durable running -> exact staging
                  -> durable staged -> commit-tree -> branch CAS
                  -> durable committed result
```

Prepare is read-only for working files, the real index, Git objects, refs, and
commits. Its only durable write is the immutable content-free candidate record.
It fails before that publication for pre-staged entries, unreported canonical
changes, mismatched application bytes/digests, an unsafe path, or a moved
session branch/tip. Unrelated noncanonical files remain unchanged and are not
included in the candidate.

### Successful Commit example

The following is an illustrative projection of the exact typed values, not a
current CLI transcript or JSON wire protocol:

```json
{
  "request": "Apply the approved opening revision",
  "candidate": {
    "candidate_id": "<sha256>",
    "session_id": "novel",
    "branch": "story/session/novel",
    "branch_tip": "<object-id>",
    "expected_tree": "<object-id>",
    "paths": [
      {
        "path": "core_documents/global_outline.md",
        "operation": "update",
        "before_sha256": "<sha256>",
        "after_sha256": "<sha256>"
      }
    ],
    "proposed_message": "Revise the opening structure",
    "impact_boundary": ["Readers encounter the inciting event earlier"],
    "author_identity_source": "repository",
    "committer_identity_source": "repository",
    "proposal_id": "proposal:opening-2",
    "correlation_id": "correlation:opening-2"
  }
}
```

```text
Lead: Commit this exact candidate, reject it, or revise the proposal?
User: Commit.
```

```json
{
  "decision": "commit",
  "confirmation_id": "confirmation:opening-2",
  "candidate_id": "<same-sha256>",
  "proposal_id": "proposal:opening-2",
  "correlation_id": "correlation:opening-2"
}
```

```json
{
  "state": "committed",
  "recovery_action": "none",
  "branch": "story/session/novel",
  "branch_tip": "<new-object-id>",
  "commit_id": "<same-new-object-id>",
  "candidate_id": "<same-sha256>",
  "operation_id": "<32-lowercase-hex>"
}
```

The runtime revalidates the complete candidate twice around authorization and
execution. After durable `running`, it hashes each create/update body literally
with filters disabled and stages only the exact path; deletion stages only an
exact literal removal. After durable `staged`, it verifies the index tree,
creates one commit whose sole parent is the candidate branch tip and whose
message/identity are the prepared values, then compare-and-swap updates only
that session branch. SHA-1 and SHA-256 repositories follow the same contract;
only object-ID length differs.

Create maps to `create`; patch and replace map to `update`; delete maps to
`delete`. Rename maps to two UTF-8-byte-sorted entries: source `delete` plus
destination `create`. Only canonical core documents and manuscripts may appear.
The internal ref, pristine main root/head/index/status, noncandidate index
state, and unrelated noncanonical working bytes remain invariant.

### Reject, Revise, drift, and interruption

- **Reject:** the future user selects Reject. The runtime records terminal
  `rejected`, changes no Git object/index/ref or artifact byte, and preserves
  the candidate. A different later decision needs a fresh confirmation.
- **Revise:** the future user asks the lead to change paths, message, or impact.
  The runtime records `rejected`, invalidates that candidate's process-local
  binding, and requires newly applied evidence, a newly prepared candidate, and
  a new confirmation. The old authority cannot be recovered.
- **Drift before staging:** if a canonical byte, session tip, index, identity
  source, internal ref, or main-root fact no longer matches,
  authorization/execution returns a content-free failure with `reprepare`.
  Even if confirmation was already supplied, it cannot be reused. A mismatched
  candidate/proposal/correlation ID is instead rejected as invalid authority and
  is not acceptance of anything.
- **Interrupted after staging:** restart can prove the branch stayed at the old
  tip and the worktree/index exactly matches the expected tree. It reports
  `staged` with `operator_recovery`; it never resets, commits, cleans, or replays
  the consumed confirmation automatically.
- **Commit exists after a lost result:** if the session branch already points
  to the exact one-parent candidate commit, recovery records `committed` and
  returns that object ID without creating another commit.
- **Outcome unknown:** a foreign branch tip, unclassifiable process result, or
  uncertain post-mutation history leaves `outcome_unknown` with `inspect`.
  Preserve files, index, refs, objects, and records; do not auto-retry.

The [recovery matrix](../operations/recovery-and-limitations.md#session-history-recovery)
defines the exact safe next action for all seven states. The
[internal API and Git sequence](../reference/git-adapter.md#package-internal-exact-story-commits),
[artifact-operation mapping](../reference/writing-artifacts.md#story-commit-application-evidence),
and [privacy boundary](../operations/privacy-and-provider.md#session-history-exposure)
provide the lower-level details.

<a id="exact-session-runtime-checkpoints"></a>

## Exact session runtime checkpoints

This capability is package-internal infrastructure, not an installed command,
lead trigger, provider action, or public facade. It gives a later application a
local restart trail for a closed set of session runtime state while keeping
that state out of story commits. A checkpoint advances only
`refs/story-writing-agents/runtime/sessions/<session_id>`; it does not advance
the story branch.

### Included paths and owners

The calling runtime component supplies an ordered create/update/delete delta.
The Git layer does not scan the workspace. Every path must contain the same
safe session ID as the request and belong to its declared owner:

| Owner | Exact accepted path family (`S` session, `D` debate, `C` conversation, `H` 64 lowercase hex) |
| --- | --- |
| `workspace` | `agent_states/orchestrator_state/sessions/{S}.json` |
| `workspace` | `agent_states/{OWNER}/debates/{S}/{D}.jsonl`, where `OWNER` is exactly `orchestrator_state`, `plot_architect_state`, `character_custodian_state`, or `execution_specialist_state` |
| `workspace` | `discussion_logs/{S}/{D}.jsonl`, `discussion_logs/{S}/{D}.state.json`, `discussion_logs/{S}/{D}.result.json`, or `discussion_logs/{S}/{D}.context.json` |
| `cli_context` | `agent_states/orchestrator_state/conversation_defaults/{S}.json` |
| `backend` | `agent_states/orchestrator_state/provider_sessions/{S}.json` |
| `backend` | `agent_states/orchestrator_state/provider_sessions/.contextual/v1/{S}/{H}.json` |
| `conversation_catalog` | `discussion_logs/{S}/interactive/conversations/{C}/metadata.json` |
| `collaboration_history` | `discussion_logs/{S}/interactive/conversations/{C}/collaboration-history.jsonl` |

For example, `workspace` may submit an update for
`discussion_logs/novel/debate_1.state.json`, but `backend` may not submit that
same path. Create proves absence and matching after bytes/digest; update proves
the existing before digest and distinct matching after bytes/digest; delete
proves the existing before digest and supplies no after bytes. A path omitted
from the request stays in the internal-ref tree; omission never means deletion.

Examples rejected before checkpoint mutation include:

```text
core_documents/global_outline.md                         # canonical story file
manuscript/part-01/opening.md                            # canonical story file
config/settings.json                                    # configuration
debug_logs/novel.jsonl                                  # debug data
discussion_logs/conversation_storage.sqlite3            # SQLite/global store
agent_states/orchestrator_state/cli_context.json         # global state
agent_states/orchestrator_state/provider_sessions/.antigravity/novel.json
agent_states/orchestrator_state/sessions/novel.lock      # temporary/lock leaf
discussion_logs/novel/unknown.future                    # unknown family
agent_states/orchestrator_state/sessions/other.json      # another session
```

### Successful checkpoint: user and runtime views

A future application may describe the action to the user as “save supported
runtime progress for session `novel`.” It must not imply that story content was
committed or that all workspace state can be restored. The following values are
illustrative direct-module facts, not a CLI wire format:

```json
{
  "session_id": "novel",
  "correlation_id": "turn:opening-review:7",
  "snapshots": [
    {
      "owner": "workspace",
      "path": "discussion_logs/novel/debate_1.state.json",
      "operation": "update",
      "before_sha256": "<64-lowercase-hex>",
      "after_sha256": "<64-lowercase-hex>",
      "content": "<exact bytes supplied in memory; omitted from display>"
    }
  ]
}
```

```text
runtime: validate ready session, complete internal tree, path owner, and digest
runtime: record running -> allocate isolated index -> apply exact delta
runtime: write one-parent commit -> CAS only the internal ref -> verify
runtime: record committed -> remove only that completed alternate index
```

```json
{
  "session_id": "novel",
  "correlation_id": "turn:opening-review:7",
  "state": "committed",
  "recovery_action": "none",
  "internal_tip": "<new-object-id>",
  "operation_id": "<32-lowercase-hex>",
  "checkpoint_id": "<same-new-object-id>"
}
```

The one-parent commit uses the exact effective local Git author and committer
names/emails and one captured UTC second for both timestamps. Its fixed message
contains the session, correlation, and operation IDs. These identity, message,
timestamp, path, and runtime-state bytes persist in local Git history even
though the durable operation JSON remains content-free.

### Outcomes and safe next actions

| Result | Meaning | Safe action |
| --- | --- | --- |
| `unchanged` / `none` | An empty delta or a fully validated same-tree result needs no commit. No operation record, alternate index, object, or ref update is created. | Continue; do not invent a checkpoint ID. |
| `running` / `inspect` | Durable intent exists, but completion is not proved. | Stop repository writers and inspect/recover the exact operation; do not resubmit it blindly. |
| `committed` / `none` | The internal ref names the exact correlated checkpoint. | Use the returned existing checkpoint ID; do not create a duplicate. |
| `outcome_unknown` / `inspect` | Git may have changed, or a foreign/stale-CAS tip cannot be correlated. | Preserve the ref, objects, index, and records. Inspect; never reset or retry the CAS. |
| `recovery_required` / `operator_recovery` | The expected tip stayed put after uncertain pre-CAS work, or repository evidence is unsafe/inconsistent. | Preserve the operation-owned index and perform explicit operator recovery or restore a consistent trusted backup. |

Concrete interruption cases:

- A failure after the alternate index/commit work begins but before the ref CAS
  returns uncertainty. Recovery that still sees the expected old ref records
  terminal `recovery_required`; it does not replay `commit-tree` or CAS.
- If CAS succeeded but result verification or publication was interrupted,
  recovery matches the ref tip to the salted commit intent and finalizes that
  same object as `committed`. It creates no duplicate commit.
- If another writer installed a foreign internal-ref tip, the compare-and-swap
  preserves it and recovery reports `outcome_unknown`; it never guesses which
  result was intended.

The checkpoint uses an operation-owned alternate index under the common Git
directory. The real main and session indexes, both symbolic `HEAD` values, the
story branch, canonical files, owner source files, unrelated bytes, and the
pristine main root remain unchanged on success and conservative failure.
Checkpoint and story-commit services take the same repository/session-history
lease, so they cannot mutate Git concurrently. A checkpoint that moves the
internal ref first makes an already prepared story candidate stale; the story
operation fails before mutation and must be prepared again.

A checkpoint restores none of the excluded paths above, canonical artifacts,
remote history, or working-file bytes. Recovery never resets, cleans, checks
out, replays a commit/CAS, or contacts a remote. Back up the common Git
directory, session-history subtree, and related working trees consistently;
the operation JSON alone is not a runtime-data backup. See the
[internal checkpoint contract](../reference/git-adapter.md#package-internal-session-checkpoints),
[recovery matrix](../operations/recovery-and-limitations.md#session-history-recovery),
and [privacy boundary](../operations/privacy-and-provider.md#session-history-exposure).

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
