# Recovery and limitations

> **Available:** guarded access recovery, shell workflows, and process-level
> POSIX PTY verification, including the package-internal ranged viewer.
> Ordinary focused shell input uses the Lead workflow. `$` selects a whole
> canonical artifact and `$$` opens ranged selection through the independent
> viewer; direct canonical indicators remain accepted.
> Python and interactive lead-turn, confirmed
> consultation, and decision recovery use the same `StoryRuntime` authority.
> Direct Python conversation archive operations
> have content-free failures and recognized additive-import recovery. Optional
> SQLite conversation storage also fails closed. Explicit `/settings` or
> provider-free `story storage use` can prepare and save desired storage for the
> next shell; host installation always requires explicit consent. Transfer and
> parity verification remain separate workflows between two already-ready
> stores. Optional local conversation RAG has no-write status, explicit
> opt-in/rebuild/search/disable, confirmed derived-only purge recovery, and
> next-shell settings. The former one-turn preflight is unavailable.
> Automatic retry, silent installation, general destructive cleanup, event
> repair, live switching, automation picker UI, and Windows console emulation
> are not available.
> Package-internal session Git-resource inspection, provisioning, exact-prefix
> resume, and require-ready validation are wired into explicit session Create;
> Reopen and Pause assess existing correlated resources without provisioning.
> Explicit SQLite provisioning recognizes only one exact
> interrupted-publication alias shape.

Key terms: **fail closed** means preserve uncertain state; **taint** means stop
continuing incomplete provider history; **external backup** is outside the workspace.

<a id="workspace-initialization-recovery"></a>

## Workspace initialization recovery

`story init PATH` supports only an absent root, an empty safe directory, or one
exact interrupted schema-v2 initialization. It never adopts, overwrites,
migrates, upgrades, or resets other state.

| Observation or failure | Meaning | Safe response |
| --- | --- | --- |
| Success JSON and no journal | The marker, exact 25-file scaffold, one parentless `main` commit, index/tree, and clean status were verified after journal removal. | Keep the original root pristine. Mutable session and runtime state belongs in the exact sibling/internal resources provisioned by session Create. |
| `ConfigurationError: effective Git identity is unavailable or invalid` | The runtime did not invent an author/committer. Exact scaffold/journal bytes may exist, but an absent Git repository was not created. | Configure a valid effective Git author and committer under normal host policy, then run the same init command in a fresh process. |
| Exact `.story-writing-agents-initialize-v2.json` remains | Initialization stopped after durable progress. The failed `Workspace` object is terminal. | Preserve all bytes and rerun with a fresh object/process. It will validate every present byte and continue only missing exact steps. |
| `WorkspaceVersionUnsupportedError` | A recognizable markerless/pre-v2 root or unsupported numeric marker/journal was found. | Preserve it and create a separate new workspace. Migration and compatibility are unsupported. |
| `WorkspaceFormatError` naming scaffold, journal, Git state, status, or recovery | State is malformed, ambiguous, dirty, drifted, or differs from the exact initialization contract. | Stop and preserve the complete root. Restore a trusted exact snapshot or create a new workspace; do not edit/delete the journal, reset Git, or remove unknown files to force readiness. |
| `WorkspaceSecurityError` or topology failure | The root or Git layout is nested, linked, symlinked, replaced, sparse, bare, mismatched, or otherwise unsafe. | Do not follow or repair suspicious paths in place. Preserve evidence and choose a new standalone safe root. |
| `OSError`, interruption, or process exit | Safe primitives or an operation failed. Exact completed partial steps are not rolled back. | Use a fresh process only after the environment is stable; exact journal recovery will converge or fail closed. |

A ready root can be passed to `story init` again. That call validates only and
does not rewrite, restage, or create a second commit. It reads required paths
and omits ignored and untracked entries from Git readiness; an untracked user
note or opaque provider `.antigravity/` tree is preserved and does not by
itself require recovery. This does not validate or trust that optional state:
its owner must validate it when consumed. Required-path drift, staged changes,
disallowed tracked changes, and tracked unknown additions are preserved and
rejected. Initialization itself creates no debug record, so its stderr and the
protected root are the recovery evidence.

`story smoke --workspace PATH` is read-only validation, not repair. It requires
an already ready root, returns null session/conversation IDs, and creates no
directive, catalog record, history, or diagnostic. Opaque ignored/untracked
entries are neither inspected nor repaired. `story smoke` without a path uses
the real initializer inside a disposable directory, reports identity failures,
and removes the directory.

Discovery stops at the nearest recognizable boundary. It does not skip an
unsupported or malformed inner marker/journal/legacy sentinel to select an
outer workspace. An explicit path also validates strictly and never
initializes.

<a id="external-git-operation-recovery"></a>

## External Git operation recovery

The direct Git facade can inspect an externally started operation without
running Git or mutating the repository. Session transition assessment now
correlates that observation with active or idle owner evidence and blocks
enter, leave, and pause when a stable external operation is present.

| Observed kind | User meaning and one safe response |
| --- | --- |
| `none` | No supported external-operation marker is present. Continue only after separate `GitStatus`, topology, and workflow checks also pass; this does not prove a clean index/worktree. |
| `merge` | One unambiguous merge is active. Stop Story mutation and resolve the merge externally before reinspection. |
| `rebase` | Exactly one rebase mode is active. Stop Story mutation and resolve the rebase externally before reinspection. |
| `cherry_pick` | One cherry-pick, optionally using the sequencer, is active. Stop Story mutation and resolve it externally before reinspection. |
| `revert` | One revert, optionally using the sequencer, is active. Stop Story mutation and resolve it externally before reinspection. |
| `sequencer` | Sequenced Git work exists without the supported cherry-pick/revert head. Stop Story mutation and inspect/resolve that operation externally. |
| `RECOVERY_REQUIRED` | Markers are mixed, contradictory, wrong-type, symlinked, unreadable, replaced, or changed during inspection. Preserve the repository, common Git administration, worktree, markers, index, refs, and files; inspect them offline. |

The failure is content-safe and recommends `inspect_repository`. The adapter
never chooses `git merge --continue`, `rebase --abort`, reset, clean, checkout,
or another repair. It also cannot infer which story session owns the work. A
dirty, staged, or unmerged `GitStatus` remains separate evidence: neither
inspection result substitutes for the other.

```text
input: direct inspection of MERGE_HEAD plus rebase-merge/
output: GitOperationError(
  code="recovery_required",
  operation="inspect_external_operation",
  recovery_action="inspect_repository"
)
repository mutation: none
```

See [External Git operation inspection](../reference/git-adapter.md#external-git-operation-inspection)
for the exact accepted marker shapes and opaque identity binding.

<a id="linked-worktree-read-recovery"></a>

### Linked-worktree read recovery

Direct `status()` and `log_metadata()` calls can safely observe a securely
validated linked worktree. They inspect the external-operation markers and
bind the root/administration identities before and after the bounded read,
including after an ordinary failure or interruption. An unchanged merge,
rebase, cherry-pick, revert, or sequencer marker may therefore coexist with a
successful metadata read. That success is observation only: users must still
resolve the external operation with trusted Git tooling before any later Story
mutation.

When the closing marker or identity observation differs, no successful result
is returned. The public error is `recovery_required`, names `status` or
`log_metadata` as the operation, and recommends `inspect_repository`; this
guard failure takes precedence over a simultaneous body failure or
interruption. Preserve the linked worktree, common Git administration,
markers, index, refs, and working bytes, stop automated writers, and inspect
offline. Do not retry merely because partial metadata may have been read.

These reads never continue, abort, reset, clean, checkout, stage, commit, or
repair Git. They also do not establish which story session owns the worktree.
The recovery reconciler and transition assessor correlate those observations
internally, while the CLI/controller consume the resulting operation
permissions. The linked-read boundary itself provides neither orchestration nor
a user command. See the
[linked-worktree read contract](../reference/git-adapter.md#linked-worktree-status-and-log).

<a id="session-git-resource-recovery"></a>

## Session Git-resource recovery

This recovery contract belongs to the package-internal session-resource seam.
`story session create` and interactive Create now provision and verify the
exact resource triple before lifecycle creation. Reopen never provisions a
missing triple; after acquiring lifecycle access it relies on transition
assessment to prove the existing resources and history.

| Observation or result | Meaning | Safe response |
| --- | --- | --- |
| `absent` / `create` | No journal, requested branch/ref, or registered worktree exists. Provision still performs a separate sibling-path collision preflight. | Explicit Create may provision it. Reopen remains read-only and fails rather than creating. |
| `provisioning` / `retry_create` | The immutable journal and an exact ordered prefix exist. This includes a complete triple whose journal removal or post-removal verification did not finish. | Preserve all facts and retry the same Create operation. It reinspects before attempting only the next missing exact step. |
| `ready` / `reopen` | The journal is absent and the exact correlated branch, ref, worktree, baseline root, and seed root passed final verification. | Reopen or repeat Create may return the same identity. Neither creates a duplicate. |
| `outcome_unknown` / `operator_recovery` | A mutation may have happened, but subsequent inspection could not prove whether it did. | Stop automatic replay and all workspace writers. Inspect the complete repository and sibling path with trusted local tools before deciding a recovery. |
| `recovery_required` / `operator_recovery` | Durable facts are malformed, partial without a journal, non-prefix, moved, substituted, differently anchored, colliding, or otherwise unsafe. | Preserve every path, ref, object, journal, and administration record. Restore a trusted exact state or create a separate workspace; do not delete or rewrite evidence to force readiness. |

Concrete cases:

- If interruption leaves only the journal, or the journal plus the exact seed
  ref, or the journal plus exact seed ref and baseline branch, the same Create
  resumes in order. Reopen and require-ready only report `provisioning` and
  never continue it.
- If `git worktree add` completed but its process result was lost, a fresh
  Create first inspects the worktree. An exact completed triple proceeds to
  journal removal and final verification instead of creating another one.
- If journal removal fails, the complete triple remains `provisioning` with
  `retry_create`. A later Create verifies it, removes only that journal, and
  verifies again.
- A pre-existing file, directory, symlink, or filesystem alias at the sibling
  path is preserved and rejected before journal, object, ref, or branch
  mutation. A foreign registered worktree, unknown ref, packed-ref collision,
  moved worktree, malformed `.git` indirection, or changed anchor likewise
  fails closed.
- Ready session inspection requires the index to match `HEAD`, so any staged
  change is recovery-required. Unstaged story work and untracked entries are
  allowed; the workspace layer still checks all 25 Git-materialized scaffold
  leaves, their ancestors, local configuration, and referenced personae.
- Ordinary READY remains strict. A separate recovery-only Story Commit reader
  may defer only index cleanliness, and only long enough to prove one exact
  correlated staged operation under the narrower contract below. It does not
  make arbitrary non-READY dirty or staged state ready.
- Concurrent Create calls for the same or different session IDs serialize
  across the repository. Exact same-session calls converge; case-distinct IDs
  follow filesystem identity truth and a real alias fails closed.

Artifact-sensitive runtime and picker operations add no recovery shortcut.
Each call freshly requires the exact READY identity and then uses only that
session worktree for artifact source reads, references, proposals,
application-ledger access, Apply, and recovery. The binding pins the physical
`(st_dev, st_ino)` identity and the exact semantic branch/ref/worktree READY
inspection. Each outer repository or session-state transaction holds the
cooperating-runtime Git lease only for its short synchronous body, performing
one complete READY and named-root check before and after it. Nested binding and
repository actions reuse the transaction with liveness/root checks rather than
repeating the complete READY scan. No such guard spans a provider await or
concurrent specialist execution; a later transaction reacquires it. The binding
keeps one stable exact repository private and exposes it only through live
same-thread access, so a retained raw repository or cross-thread use fails.
Access, Git leases, and Commit authority use opaque process-aware thread-
state tokens rather than numeric identifiers, so identifier reuse grants no
authority. Same-thread nested access is reentrant; long-lived reference/source
adapters reacquire an outer guard for each operation. Borrowed Commit access
retains complete history-owned validation before and after its body. Nested
liveness and outer/borrowed postchecks run on success, `RuntimeError`, and
cancellation. When both the body and exit fail, binding loss takes precedence;
a clean postcheck preserves the original exception, and cleanup remains
guaranteed.

Direct Lead and Lead-synthesis owners acquire a separate opaque six-descriptor
physical-generation pin before session-derived sources, definitions, or
provider work and retain it through durable persistence. Its short-lock
acquisition/recheck grants no semantic READY or artifact authority and retains
no Git lease across the provider await.
Conversation history remains in its separately
configured control-plane store. Absent
resources are not provisioned; moved, mismatched, staged, or otherwise
non-ready resources do not fall back to
`main`, the current directory, a sibling session, or an older binding. The
binding failure is content-free and occurs before provider or artifact I/O.

Session-state source paths must be canonical relative POSIX text, and each leaf
must remain one singly linked regular file throughout the read. Symlinks,
FIFOs, devices, hard links, replacement or metadata drift, and embedded
session/debate/agent ownership that disagrees with the requested path all fail
closed without exposing the replacement as evidence. Compound agent-state and
debate reads retain the complete root-to-leaf-parent descriptor chain and
compare the exact whole inventory—names plus device, inode, type, link count,
size, nanosecond modification time, and nanosecond change time—before returning.
Every parent-child edge is reopened and revalidated after the final leaf,
inventory, or absence observation. Agent-state JSONL accepts at most 8 MiB and
10,000 records across all selected files; debate JSONL accepts 10,000 records.
Both reject record 10,001 before line slicing, parsing, or retention. An absent
agent-state parent or leaf is accepted only after the retained existing
ancestor prefix remains exact; late creation fails closed.

The common-Git lease coordinates this runtime's cooperating operations only.
Persistent external Git drift is rejected by the before/after or next-action
READY check. A noncooperating process that changes and restores Git state
between checkpoints is outside the supported guarantee; stop all workspace
processes before external inspection, repair, or recovery.

If exact binding or physical-root validation fails after an Apply decision is
durably recorded but before the repository's `apply_artifact_changes()` method
starts, runtime consumes that decision once and appends an
`artifact_application_result` with `recovery_required` and
`operator_recovery`, does not start artifact application, and emits no Commit
request for that result. Once that method starts, an unclassified root,
security, or format failure remains `outcome_unknown` with
`inspect_and_resolve_outcome`, including when the method returned before its
outer binding postcheck rejected completion. Runtime publishes no optimistic
application result or Commit request in that state. A repository-classified
terminal recovery condition remains `recovery_required`. After deriving the
complete current artifact state and relationship result, Apply explicitly
repeats complete READY validation immediately before marking repository
execution started and calling the mutation method; its outer transaction still
postvalidates before a result escapes. Preserve the session resources and
authoritative history and inspect any uncertain application before another
write. Commit remains a separate decision. Its runtime-composed service carries
the Apply worktree's physical identity through request preparation,
authorization, and execution while independently revalidating semantic READY.
Runtime also reopens the Apply binding around the `story_commit_request` append
and in-memory service registration. Detected binding loss suppresses the
returned request and removes the in-memory service, but cannot roll back an
already-durable append. Commit execution/inspection/recovery keeps the session-
history lease around its semantic READY checkpoints and durable transitions.
Before intentional staging it requires ordinary READY; afterward phase-aware
checks require the exact candidate index/worktree and the complete registered
session/main-root topology. READY/history resource loss detected before the
`running` record is durable maps to `recovery_required`; any ambiguity after
that record is durable maps to `outcome_unknown`. Candidate/root replacement
before execution still requires
repreparation. Failure to publish the final `committed` transition durably
preserves `outcome_unknown` when exact phase authority can be proved.

Identity-bound Commit `inspect()` and `recover()` check the Apply root before
and between durable-owner observations and immediately before any recovery
transition. Both ordinary READY and staged-recovery admission require the
operation and candidate to share the exact session, candidate identity,
correlation, and expected branch tip. Replacement or cross-linked records
become `recovery_required`; neither path advances an operation from those
facts.

If Apply-result publication succeeds but the later Commit-request preparation,
publication, or service registration fails, the Apply result remains
authoritative and no Commit is authorized. The current history has no dedicated
durable state, schema field, or restart projection for that post-Apply Commit-
request failure; operators must inspect before preparing again.
[Issue #662](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/662)
tracks that separate state-machine work and remains outside this change.
This change covers only the #661 Git/session resource path. Conversation-side
occurrence remains follow-up work in
[Issue #668](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/668),
and is not shipped behavior here. Direct recovery-topology validation now
normalizes a delegated session-resource failure to the history-owned
`recovery_required` / `operator_recovery` contract, without mutation or an
exposed lower-layer exception chain.
Issue #661 does not confine the provider working directory or project root;
that separate boundary remains tracked by
[Issue #670](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/670).

Resource loss during local closure does not replay known provider work. A
completed artifact-review result that cannot be finalized against its bound
session closes durably as artifact/collaboration `recovery_required`, without
Apply authority. Recommendation or reference closure that cannot prove its
correlated session evidence returns a typed blocking recovery result and keeps
the original attempt closed. Guarded reference binding loss retains its
resource classification through preparation, read action, failure,
terminalization, and cleanup, then maps to `recovery_required` without a
provider or fallback call. Public unguarded references retain their fixed
`ReferenceOperationFailure` conversion. Repair the exact resources and
authoritative history instead of retrying that provider call.

The runtime never automatically removes a named branch, internal ref,
worktree, worktree-administration entry, seed object, journal-backed recovery
fact, or user byte. The lock file persists as coordination infrastructure.
Pause, release, close, and process exit preserve durable session resources.
There is no migration, compatibility, remote, reset, clean, prune, garbage
collection, rebase, merge, or history-rewrite recovery route. #505, not #504,
coordinates that later work; #509 now supplies internal story commits and #510
supplies closed-path internal checkpoints.

<a id="session-history-recovery"></a>

## Session-history recovery

Issue #508 supplies package-internal evidence and classification. #509 now
supplies internal exact story-commit execution, and #510 now supplies exact
internal-ref checkpoint execution and conservative recovery. The separately
confirmed Lead workflow can start a story commit through the runtime; no
standalone Git command or provider capability does so. Checkpoints remain
package-internal and are not started merely by ordinary Lead work.

For any internal interruption or later public-workflow interruption, first stop
repository writers and preserve the main repository, sibling session worktree,
common `.git` directory, and the complete session-history subtree together.
Restoring only one JSON record, one index, one ref, or one worktree can destroy
the correlation needed for safe classification.

<a id="session-history-snapshot-recovery"></a>

### Stable snapshot evidence

Issue #536 can capture the complete candidate, commit-operation, and
checkpoint-operation records behind one session-history inspection without
creating or changing history. The first inventory is decoded under the
existing repository lease, then independently re-inspected. The immutable
result is returned only when both observations agree exactly; its complete
record tuples are hidden from `repr`, while their bounded fields remain
available to the later internal consumer.

If operation `O4` disappears or changes before the second inspection, the
snapshot attempt returns the existing typed `recovery_required` failure—never
a partial result—and creates no replacement record. Stop writers and preserve
the history, Git state, and any alternate checkpoint index. The internal #527
reconciler can use a stable snapshot to surface the preserved recovery state;
it does not duplicate the commit or checkpoint. #536 itself has no UI,
lifecycle decision, replay, recovery mutation, provider call, or cleanup
authority. An empty `create=False` inspection creates nothing, and this
additive read path adds no persisted format, migration, or compatibility
reader. See the [snapshot contract](../reference/git-adapter.md#package-internal-session-history-snapshot)
and [workspace composition](../reference/workspace-api.md#workspace-session-history-snapshot).

| State, result, or failure | Meaning | Safe response |
| --- | --- | --- |
| Candidate `prepared` | Exact path/digest/expected-tree evidence exists; no confirmation or Git index/object/ref mutation is implied. | Revalidate it and request a fresh exact decision through the Lead workflow; if any candidate fact changed, `reprepare` from new successful application evidence. |
| Checkpoint `unchanged` / `none` | An empty delta or fully validated same-tree result needed no operation. No operation ID, index, object, or ref update exists to recover. | Continue from the unchanged internal tip; do not fabricate an operation or checkpoint ID. |
| Commit `rejected` / `none` | One Reject or Revise decision was recorded before execution. It is terminal and authorizes nothing. Reject preserves the candidate; Revise invalidates it. | After Reject, keep the candidate and require a fresh confirmation for another decision. After Revise, apply/prepare a new candidate and request new confirmation. |
| Story commit `running` / `inspect` | Commit authority was consumed and intent was durable before staging, but the resulting index/branch state is not yet proved. | Call the internal recovery classifier once writers are stopped. Do not replay confirmation or execute again. It may classify exact staging, an exact committed tip, ambiguity, or recovery-required state. |
| Checkpoint `running` / `inspect` | Intent is durable against one expected internal tip, but CAS completion is not proved. | Stop repository writers and call the internal checkpoint recovery classifier. Do not resubmit the delta, rerun `commit-tree`, or retry CAS. |
| Story commit `staged` / `operator_recovery` | The branch remains at the old tip and index/worktree exactly match the candidate tree, but no commit is proved. | Preserve worktree, index, objects, and records. Inspect and recover explicitly; the service never automatically commits, resets, cleans, or restages. |
| Story commit or checkpoint `committed` / `none` | The terminal record names the exact commit. Story Commit permits that commit to equal or be an ancestor of the current READY branch tip after later valid commits; checkpoint rules retain their internal-ref correlation. Recovery can reach this state when a correlated commit was already CAS-installed but result persistence/reporting failed. | Treat the returned existing object as the result after verification. Do not create a duplicate commit. |
| Operation `outcome_unknown` / `inspect` | Git may have mutated, or the branch/index/result cannot be classified exactly. | Preserve all records, index, refs, and objects. Inspect; only an exact correlated tip may advance the same record to `committed`. Otherwise retain ambiguity or require operator recovery. |
| Operation `recovery_required` / `operator_recovery` | State is inconsistent, unsafe, substituted, or no longer automatically reconcilable. It is terminal. | Perform explicit operator recovery or restore a complete trusted backup. Never edit the record or delete/reset evidence to force another transition. |
| Missing/noncanonical/oversized record, unexpected entry, alias, symlink, hard link, or failed fsync | The store cannot trust the addressed history boundary. | Stop all writers, retain exact bytes and metadata, and investigate offline. Do not normalize, replace, or follow the rejected path. |
| Repository lock unavailable or root/common-Git identity changed | Safe serialization or repository identity cannot be proved. | End the operation and retry only after the original exact repository is trustworthy and no competing writer remains. |

An `outcome_unknown` checkpoint can retain
`checkpoint-inputs/<operation_id>/index`; `recovery_required` may retain it as
well. The runtime removes that leaf only after the exact checkpoint record is
durably `committed`. Candidate and operation records have no automatic expiry
or cleanup. Process exit, session pause/close, and a later successful operation
do not erase earlier evidence.

For story commits specifically:

- Commit, Reject, and Revise operation IDs are the first 32 lowercase
  hexadecimal characters of SHA-256 over ASCII
  `<commit|reject|revise>\0<candidate_id>`. Inspection and recovery recompute
  this ownership: `rejected` accepts only the Reject or Revise identity, while
  every other state requires Commit. Pre-#661 random Commit or
  confirmation-salted Reject evidence is `recovery_required`; no migration or
  compatibility reader rewrites it.
- Execution, inspection, and recovery place semantic READY checks before and
  after durable history observations/transitions while the session-history
  lease is live. Once the candidate is intentionally staged, exact phase
  topology replaces the ordinary clean-index READY assertion; unrelated
  staging, worktree/ref drift, or registration/main-root drift fails closed.
- A READY/history resource failure before a `running` operation is durable is
  `recovery_required`. Once `running` is durable, an unproved outcome is
  `outcome_unknown`; this includes ambiguity while publishing the final
  `committed` record, which is durably downgraded to `outcome_unknown` when the
  phase can still be proved.
- Constructing the initial `running` record in memory is not durable. Failures
  keep their pre-durable mapping until its create-only history append returns
  successfully; only a successfully appended `running` record crosses the
  uncertain-outcome boundary.
- Recovery-only staged admission holds one lease and requires an addressed,
  exactly correlated `running`, `staged`, or `outcome_unknown` operation and
  candidate. An unaddressed reconciliation snapshot requires exactly one
  eligible pair. The guard revalidates the exact branch and internal ref,
  candidate tree, staged index and matching worktree, absence of untracked
  files and external Git activity, bound session root and registered worktree,
  main HEAD, and main-worktree status plus allowed-path content fingerprint
  before and after. Only the Story Commit recovery owner may perform a
  monotonic transition after admission.
- Session recovery attributes an exact staged snapshot to Story Commit only
  when reviewed/application provenance, relationship and change-set coherence,
  normalized Create/Patch/Replace/Delete/Rename effects, candidate
  before/after digests, affected paths, final artifacts, rename lineage, and
  deletion provenance all agree. The expected-tree proof remains the separate
  exact staged-index/worktree topology from the recovery snapshot. A mismatch
  fails closed instead of borrowing Story Commit ownership from path names.
- Terminal `inspect()` and `recover()` require current READY and never write or
  advance history. A historical `committed` record remains valid when its
  commit exactly matches its candidate and equals or is an ancestor of the
  current READY branch tip. Resetting away, divergent ancestry, a missing or
  mismatched commit, or candidate mismatch is `recovery_required`. Historical
  `rejected` and `recovery_required` records remain immutable inspectable
  evidence under current READY.

- If interruption leaves `running` but the old branch tip and exact candidate
  index/worktree are present, `recover()` records/returns `staged` with
  `operator_recovery`; it does not continue to `commit-tree`.
- If the branch points at a commit with the candidate's exact tree, sole parent,
  and literal message, `recover()` advances the existing operation to
  `committed` and returns that same object ID. It creates no second object or
  branch update.
- If the branch remains old but index/worktree match neither the parent nor
  candidate tree, recovery records terminal `recovery_required`. A staged
  record whose expected index was lost is likewise recovery-required.
- A foreign or otherwise unproved new branch tip remains `outcome_unknown`.
  Recovery does not reset it or inspect provider history to guess intent.

For checkpoints specifically:

- If uncertain pre-CAS work leaves the internal ref at its exact expected tip,
  `recover()` records terminal `recovery_required`. It preserves an allocated
  alternate index and never repeats `commit-tree` or CAS.
- If the internal ref already names the exact one-parent commit whose salted
  intent matches the durable record, recovery records `committed`, returns that
  same object ID, and removes only its completed alternate index.
- A stale CAS never overwrites the newer tip. If that tip is foreign or cannot
  be correlated, recovery reports or retains `outcome_unknown`; it does not
  reset the ref or guess.
- Checkpoint recovery restores no excluded runtime store, canonical artifact,
  working-file byte, or remote history. The internal-ref Git objects and
  content-free JSON are evidence, not a complete workspace backup.

These records are content-free, so they are not a story-data backup. Conversely,
a story-files-only backup is not recovery evidence. A useful recovery backup
must preserve the repository, common Git administration/history namespace, and
sibling worktree consistently. Git commits produced by the internal services
also contain effective identity and commit-message metadata. See the
[story-commit scenarios](../guides/workspaces.md#exact-confirmed-story-commits),
[checkpoint scenarios](../guides/workspaces.md#exact-session-runtime-checkpoints),
[story-commit contract](../reference/git-adapter.md#package-internal-exact-story-commits),
and [checkpoint contract](../reference/git-adapter.md#package-internal-session-checkpoints).

<a id="correlated-session-recovery-projection"></a>

## Correlated session-recovery projection

The pure package API converts already gathered, immutable owner facts for one
active story workflow into one content-free state and one safe next-action
label. The internal reconciler gathers stable owner facts and delegates to it;
the transition assessor preserves that exact result for lifecycle commands.
Neither component performs the projected action.

<a id="session-recovery-owner-reconciliation"></a>

### Stable owner reconciliation

`SessionRecoveryReconciler.inspect()` accepts owner observation only when the
caller-supplied lifecycle is exactly `ready + held_here`. It does not acquire
the claim. Ready state held by another live process or with unknown ownership
returns `outcome_unknown`; absent, paused, or claimable rather than held state
returns `recovery_required`. When no authoritative collaboration read was
accepted, `collaboration_tail_sequence=0` means “tail unavailable,” not
“durable history is empty.”

If the initial session-resource observation is recovery-required because of a
staged index, reconciliation first attempts the recovery-only Story Commit
snapshot. An exact staged topology is assigned to `story_commit` only when one
candidate/operation also matches the reviewed change-set and relationship
digests, application provenance and result digests, and normalized operation
effects with exact before/after, affected-path, final-artifact, rename, and
deletion facts. Expected-tree authority still comes from that staged snapshot,
not from caller-supplied review evidence alone. Missing or mismatched
application correlation is reported with the Story Commit owner; if the narrow
snapshot itself cannot prove its exact topology, the original arbitrary
non-READY dirty/resource condition remains owned by `session_resources`.

For an accepted lifecycle, the reconciler reads collaboration, the exact
session-resource triple, complete session history, external Git operation, and
linked session-worktree status as generation A. It performs an exact
application-ID owner lookup when application evidence exists, selects at most
one correlated candidate/commit/checkpoint chain, and reads the current-`HEAD`
log only when that chain claims a committed story operation. It then rereads
collaboration, resources, history, external Git, and status as generation B.
The exact artifact binding is established against generation A; application
inspection runs through it. Each generation's external-operation and linked-
status observation, plus the conditional committed-`HEAD` log read, has its own
short guarded access through that same binding. A final guarded checkpoint then
requires the same physical root and semantic READY resources as generation B.
Session-resource drift, a second resource-read failure, or binding loss at any
of these guarded observations is `recovery_required` for session resources
rather than permission to combine generations. Drift or second-read failure
for the other owners remains `outcome_unknown` for that owner. Stable facts are
selected again, any commit is checked against the nonempty newest-first log and
stable status, and only then are they passed to the pure classifier.

The visible result fields answer different questions:

- `state` says the exact workflow position that was proved.
- `next_action` names one safe instruction but grants no authority.
- `owner` identifies the durable boundary to inspect or maintain.
- `failure_code` says why progress is accepted, uncertain, or blocked.
- Optional proposal, application, candidate, operation, commit, checkpoint,
  and correlation IDs appear only when the evidence proved them. Direct
  blockers do not fabricate them.

A first-read owner failure normally returns `recovery_required`; a second-read
failure or changed A/B observation normally returns `outcome_unknown`. Session
resources are stricter: any A/B change, second-read failure, or exact-binding
loss returns `recovery_required`. Resource provisioning remains outcome-
unknown, while absent resources are a missing predecessor. Artifact inspection
distinguishes a recovered exact result from blocked outcomes: absence maps to
`missing_predecessor`; drift, corruption, or a mismatched/foreign result maps
to `correlation_mismatch`; artifact `recovery_required` maps to
`owner_recovery_required`; and loss of the binding during that inspection also
requires owner recovery. Its documented recovery-before-read may finish or
verify an already-journaled application and create only its standard
maintenance state. This is owner maintenance, not a new workflow mutation:
reconciliation never calls Apply, invokes a provider, replays work, changes Git or lifecycle,
cleans evidence, or performs `next_action`.

| Projected state | What it means to a user | Exact next action |
| --- | --- | --- |
| `ready` | Stable correlated owner checks prove safe empty history or exact ordinary, proposal-free Lead history, with no pending workflow, unresolved outcome, or recovery blocker. This grants no execution or session-transition authority. | `continue_with_lead` |
| `proposed` | The collaboration history has an active proposal, but no exact reviewed artifact candidate is supplied. | `continue_with_lead` |
| `reviewed` | The reviewed proposal/candidate facts agree, but no application-confirmation request exists. | `request_application_confirmation` |
| `awaiting_application` | The exact application request and its visible Confirm/Reject/Revise choices are durable and still correlated. No executable capability is retained. | `submit_application_decision` |
| `applied_not_committed` | An exact session-bound terminal application result is correlated, but no story commit is closed. `applied` requires exactly its affected paths as unstaged changes; idempotent `unchanged` requires a clean status. | `request_commit_confirmation` |
| `staged` | The exact candidate and commit operation are correlated and Git status proves only those candidate paths are staged at the expected branch tip. A commit is not yet proved. | `inspect_story_commit` |
| `committed` | Commit metadata, branch tip, tree, parent, candidate, application, proposal, and correlation facts agree. | `continue_with_lead` |
| `rejected` | Collaboration history or the exact story-commit operation records a terminal rejection. It authorizes no apply or commit. | `continue_with_lead` |
| `outcome_unknown` | One authoritative owner is still running, provisioning, ownership-unknown, or otherwise cannot prove its outcome. A session-history operation summary is sufficient even when its optional detailed record is absent. | `inspect_owner_outcome` |
| `recovery_required` | An owner or session-history summary already requires recovery, external Git is active, a predecessor is missing, or cross-owner/repository facts contradict one another. | `operator_recovery` |

The action names are instructions consumed by the lifecycle controller, not
callable authority. Users can use the projection to understand which exact decision or
inspection should happen next. They cannot use it to call a provider, apply an
artifact, stage or commit Git data, reconstruct a capability, retry an
operation, or continue/abort/reset/clean a repository. For
`outcome_unknown`, preserve evidence and inspect the named owner. For
`recovery_required`, stop automated work and have an operator restore or prove
one exact consistent state; do not reexecute or clean away evidence.

Recovery-required facts are evaluated before unresolved outcomes, and
unresolved outcomes before ordinary progress. With neither present, the
furthest exactly proved closed state wins: for example, a correlated
`committed` operation supersedes its earlier staged, applied, and reviewed
facts. Equivalent owner evidence therefore produces the same projection
regardless of the order in which the caller obtained it.

Session-history operation summaries are durable owner facts, not hints. A
summary entry marked `recovery_required` or `outcome_unknown` controls the
projection even if the caller has no optional detailed operation record. If a
preserved checkpoint index is listed without the detailed checkpoint record
that explains it, the safe result is `recovery_required` with
`missing_predecessor` and `checkpoint` ownership. Likewise, an authoritative
`unchanged` application result does not excuse unexplained worktree, staged, or
unmerged changes: those produce `recovery_required` with
`repository_state_mismatch`.

### Sample input and output

The following JSON-shaped summaries are explanatory views of Python values,
not a wire format. Operational flags such as `provider_call`, `replay`, and
`new_workflow_mutation` annotate the scenario; they are not
`SessionRecoveryResult` fields. This package defines no serializer or legacy
reader.

An awaiting-application projection retains request `R9`, proposal `P7`
revision 3, and reviewed candidate `D7`, while deliberately retaining no
provider or write capability:

```text
input owner facts:
  lifecycle: ready + held_here for session story_review
  generation A/B: exact ready resources, stable linked status, no external Git
  collaboration A/B: conversation review_1, tail 12, proposal P7 revision 3
  reviewed proposal: candidate D7, correlation K1, request R9
  visible choices: [confirm, reject, revise]

output:
  {state: awaiting_application,
   next_action: submit_application_decision,
   owner: lead,
   failure_code: none,
   proposal_id: P7,
   confirmation_request_id: R9,
   reviewed_candidate_id: D7,
   correlation_id: K1,
   provider_call: false,
   new_workflow_mutation: false,
   replay: false}
```

An exact staged story commit can correlate operation `O4`, commit candidate
`C4`, and application `A2` without claiming the commit completed:

```text
input owner facts:
  lifecycle: ready + held_here
  reviewed proposal and session-bound application A2: exact
  generation A/B: candidate C4 and operation O4 have exact bindings
  linked status A/B: exactly C4 paths staged at the expected branch tip
  external Git A/B: none

output:
  {state: staged,
   next_action: inspect_story_commit,
   owner: story_commit,
   failure_code: none,
   application_id: A2,
   story_commit_candidate_id: C4,
   story_commit_operation_id: O4,
   commit_replay: false}
```

If an owner changes between the two generations, later-looking facts are not
combined with earlier ones:

```text
input: linked repository status A != linked repository status B, accepted tail 12
output:
  {state: outcome_unknown,
   next_action: inspect_owner_outcome,
   owner: repository_status,
   failure_code: owner_outcome_unknown,
   collaboration_tail_sequence: 12,
   new_workflow_mutation: false}
```

If lifecycle access is paused before any collaboration read, the unavailable
tail is explicit:

```text
input: lifecycle durable_state=paused
output:
  {state: recovery_required,
   next_action: operator_recovery,
   owner: session_lifecycle,
   failure_code: invalid_request,
   collaboration_tail_sequence: 0}
```

A claimed committed operation is accepted only when stable status names that
commit as `HEAD` and the nonempty log's newest entry is the same commit. For
example, if operation `O5` records commit `G5`, status names `G5`, but the log's
first commit is `F5`, the result is:

```text
{state: recovery_required,
 next_action: operator_recovery,
 owner: story_commit,
 failure_code: correlation_mismatch,
 replay: false}
```

If the session-bound application instead names a different proposal than its
reviewed predecessor, the classifier does not guess which owner is correct:

```text
input owner facts:
  reviewed proposal: P7, correlation K1
  session-bound application: proposal P8, correlation K1

output:
  {state: recovery_required,
   next_action: operator_recovery,
   owner: artifact_application,
   failure_code: correlation_mismatch}
```

An `ArtifactApplicationResult` by itself is not application evidence. It must
be wrapped in `SessionArtifactApplicationEvidence` with the exact session,
conversation, collaboration-tail, proposal, and correlation binding. The
relationship-validation digest is likewise supplied by its authoritative
owner and compared across those facts; this classifier does not privately
recompute it. A fresh idle session has no active correlated workflow for this
classifier and raises `SessionRecoveryError` with `unsupported_shape`; the
separate transition assessment below proves idle transition safety.

The projection contracts are frozen and side-effect-free. A result contains
identifiers, owner, state, action, and a content-safe failure code; fixed
errors contain only a content-safe code and message. Neither contains story
bytes or provider text. Pre-v2 dictionaries, mutable or subclassed shapes,
unknown fields, and invalid or duplicate values are not adapted. There is no
codec, migration, or compatibility reader. See the
[exact package imports](../reference/package-api.md#correlated-session-recovery-api).

<a id="session-transition-assessment"></a>

## Story-session transition assessment

The package-internal `SessionTransitionAssessor` gives the backend, CLI, and
interactive controller one content-safe answer about entering a destination,
leaving a source, or pausing a session. It does not perform or authorize the
transition. In particular, `enter_destination` means only that a controller may
proceed with attachment; it grants no provider, artifact Apply, staging, or
commit authority. A permitted leave or pause lets that controller proceed
through the existing ownership-release path, but this assessment itself
neither acquires nor releases a claim.

This is the canonical permission matrix:

The outer `scope` is exactly `idle` when no active conversation evidence is
accepted and `active` when a conversation/store pair is reconciled.
The ordered operation tokens are exactly `enter_destination`, `leave_source`,
and `pause_session`; “enter, leave, pause” below abbreviates those values.

| Nested/idle state | Outer state and next action | Outer owner / failure | Nested owner and next action | Allowed operations |
| --- | --- | --- | --- | --- |
| `idle_ready` | `idle_ready` / `begin_with_lead` | null / `none` | no nested result | enter, leave, pause |
| `proposed` | `active` / `follow_active_recovery` | null / `none` | `lead` / `continue_with_lead` | enter, leave, pause |
| `reviewed` | `active` / `follow_active_recovery` | null / `none` | `lead` / `request_application_confirmation` | enter, leave, pause |
| `awaiting_application` | `active` / `follow_active_recovery` | null / `none` | `lead` / `submit_application_decision` | enter, leave, pause |
| `applied_not_committed` | `active` / `follow_active_recovery` | null / `none` | `artifact_application` / `request_commit_confirmation` | enter only |
| `staged` | `active` / `follow_active_recovery` | null / `none` | `story_commit` / `inspect_story_commit` | enter only |
| `committed` | `active` / `follow_active_recovery` | null / `none` | `story_commit` / `continue_with_lead` | enter, leave, pause |
| `rejected` | `active` / `follow_active_recovery` | null / `none` | `collaboration_history` or `story_commit` / `continue_with_lead` | enter, leave, pause |
| `outcome_unknown` | `outcome_unknown` / `inspect_owner_outcome` | copied nested owner / `owner_outcome_unknown` | same owner / `inspect_owner_outcome` | none |
| `recovery_required` | `recovery_required` / `operator_recovery` | copied nested owner / exact non-`none` failure | same owner / `operator_recovery` | none |

For active work, the assessment contains the exact nested recovery result.
Normal active progress has outer state `active`; its allowed operations come
only from the nested state. Uncertainty or required recovery copies the nested
`owner` and `failure_code` and allows nothing. Safe known recovery takes
precedence over uncertainty.

For idle work, `conversation_id` and `recovery_result` are absent. Lifecycle
must be exactly `ready + held_here`. The assessor reads resources, complete
session history, external Git, and linked-worktree status twice and accepts
only equal generations. Ready resources must identify the exact session
worktree; status must be clean on its exact branch and current tip; external
Git must be `none`; every candidate must have one unique terminal committed or
rejected operation; the latest committed story tip must match the current
branch tip (or the initial tip when there is no commit), and the latest
checkpoint tip must likewise match the internal ref (or its initial tip); and
no running, uncertain, recovery-required or staged operation or preserved
checkpoint index may remain.

A generic failure on the first owner read is `recovery_required`; a typed
uncertain first read is `outcome_unknown`. A failure on the second read or any
A/B drift is `outcome_unknown`, because no single generation was proved. The
result identifies the owner and gives only `operator_recovery` or
`inspect_owner_outcome`; it never combines observations. Recovery-before-read
work performed inside an authoritative owner may finish or verify that owner's
already-journaled state. That is owner maintenance, not a new workflow
mutation by the assessor.

The result never exposes the worktree or proof paths, branch or internal ref,
proof or repository/worktree identity digests, story/provider content, process
identity, raw Git output, live handles, or capabilities. Its exact fields are
`session_id`, `conversation_id`, `scope`, `state`, `next_action`, `owner`,
`failure_code`,
`allowed_operations`, and `recovery_result`. The JSON-shaped examples below
are explanatory views; labels such as R9 are values inside the nested recovery
result, not additional assessment fields.

An idle session whose two observations agree can begin with the lead:

```text
input: lifecycle ready + held_here; stable ready resources/history;
       external Git none; exact clean session branch and tips
output: {session_id: story_review, conversation_id: null, scope: idle,
         state: idle_ready, next_action: begin_with_lead, owner: null,
         failure_code: none,
         allowed_operations: [enter_destination, leave_source, pause_session],
         recovery_result: null}
```

An active R9 confirmation request remains governed by its nested recovery:

```text
input: conversation review_1; #527 result awaiting_application for request R9
output: {scope: active, state: active,
         next_action: follow_active_recovery, owner: null, failure_code: none,
         allowed_operations: [enter_destination, leave_source, pause_session],
         recovery_result: {state: awaiting_application,
                           confirmation_request_id: R9,
                           next_action: submit_application_decision}}
```

A staged active operation permits attachment for inspection, but not leaving
or pausing while the Git outcome remains unresolved:

```text
input: #527 result staged for operation O4
output: {scope: active, state: active,
         next_action: follow_active_recovery,
         allowed_operations: [enter_destination],
         recovery_result: {state: staged,
                           story_commit_operation_id: O4,
                           next_action: inspect_story_commit}}
```

Stable external Git activity blocks every transition:

```text
input: idle generations A and B both report external Git merge
output: {scope: idle, state: recovery_required,
         next_action: operator_recovery, owner: external_git,
         failure_code: external_git_operation, allowed_operations: []}
```

Changed history between the two idle reads proves no safe generation:

```text
input: idle history A != idle history B
output: {scope: idle, state: outcome_unknown,
         next_action: inspect_owner_outcome, owner: story_commit,
         failure_code: owner_outcome_unknown, allowed_operations: []}
```

The installed CLI and controller now consume this evidence immediately before
their lifecycle mutations. Noninteractive commands serialize the full fixed
assessment projection, including the nested active result; the interactive
controller presents only `Scope`, `State`, `Next action`, `Owner`, `Failure`,
and `Allowed operations` on denial. For example, a staged pause denial appears
semantically as:

```text
session transition
Scope: active
State: active
Next action: follow_active_recovery
Owner: none
Failure: none
Allowed operations: enter_destination
```

The requested `pause_session` is absent, so the session stays ready and
attached. On a destination-entry denial, the destination claim is released,
the source is preserved, and no source-leave assessment occurs. On a
source-leave denial, only the destination is released; the source attachment
and default are preserved. Neither path resets, aborts, cleans, or deletes
recovery evidence.

Create/Reopen assessment follows resource/lifecycle mutation. A later denial
does not delete a provisioned triple or roll a reopened session back to paused;
the resulting lifecycle is released when possible and must be inspected before
retry. Pause assessment precedes durable pause, so its denial does not write
`paused`.

Assessment invokes no provider, reconstructs or exposes no confirmation
capability/content, and performs no projected recovery action. Allowed
lifecycle movement does not bypass the existing lead/user confirmation path
for later artifact or commit writes. `outcome_unknown` and
`recovery_required` cannot be cleared by reset, abort, cleanup, default changes,
or a lifecycle retry; preserve the evidence and follow the named owner/action.
The exact internal imports and signatures are in the
[package reference](../reference/package-api.md#session-transition-assessment-api).

## Session access recovery

| Process-access result | Meaning | Safe response |
| --- | --- | --- |
| `claimable` | No blocking claim: no claim exists, or an unchanged local POSIX claim is proved dead. Guarded acquisition may be attempted. | Inspect durable state: create when absent; resume when ready or paused. Acquisition rechecks the claim under the guard. |
| `held_here` | This backend owns the exact claim token. It does not by itself prove provider work is allowed. | Provider work also needs durable `ready`, valid configuration, and any required conversation focus. Otherwise resume paused state, repair scope/configuration, or close normally. |
| `blocked_by_live_local_owner` | Another process on this host is alive. | Close that process normally, then retry. |
| `ownership_unknown` | Host is remote, platform/probe is unsupported, permission is denied, or state changed. | Preserve the claim; verify ownership outside the runtime. |

Status/list only classify. Age is not liveness proof. Do not edit a claim or
`.access-claims.lock`. Release compares the exact token under the guard and
preserves foreign/changed ownership. No remote probe occurs.

A same-host missing PID needs no deletion: acquisition replaces its unchanged
claim under the guard. If ownership is unknown, back up and investigate before
retrying. Never manufacture `claimable` by removing files.

## Lead proposal and decision recovery

The collaboration tail determines the only safe next action. A caller must not
infer authority from visible text or replay incomplete work.

| Runtime result | User meaning | Safe response |
| --- | --- | --- |
| `retry_lead_turn` | An eligible read-only Lead attempt failed, timed out, was cancelled/interrupted, or returned rejected output/evidence. A typed failure closes it; exact legacy unstarted, prepared-only, or unmatched submitted attempts may await that closure. | Send a new message, revise the wording, or explicitly resubmit in the same conversation, including after restart. Runtime closes any pending prior attempt before appending the new turn and preserves its request and failure. Failed closure persistence still requires recovery. |
| `reply_to_lead` or `continue_with_lead` | Continue the conversation; no work is authorized. | Send a new lead turn. Ordinary text while a proposal is pending supersedes it first. |
| `submit_recommendation_decision` | One exact preparation recommendation is waiting. | Confirm one Lead preparation, Reject without preparation, or Revise with nonblank instructions for a new recommendation. Cancel/EOF submits no decision. |
| `finalize_recommendation` | Recommendation history has local closure work. | Let runtime validate and finalize the exact known result; do not replay provider work. |
| `submit_confirmation` | One exact proposal is waiting. | Submit `confirm`, `reject`, or `revise` with every returned identity/digest field. Text such as `Yes` is not confirmation. |
| `submit_application_decision` | The exact reviewed Apply request is still pending. | Use its structured Apply/Reject/Revise control. Ordinary Lead/advisory input rediscloses the same request without a provider call or history append. |
| `submit_commit_decision` | The exact local Commit request is still pending. | Use its separate structured Commit/Reject/Revise control. Ordinary Lead/advisory input rediscloses the same request without a provider call or history append. |
| `start_or_reconfirm` | A confirmation is durable, but projection alone is not a reusable capability. A live `decide_lead_step()` normally crosses this boundary immediately. | Let the owning runtime use its private single-use start authority. After restart, collect the fresh `reconfirmation_required` request instead of reconstructing authority. |
| `reconfirmation_required` | A confirmed proposal survived restart but its live execution capability did not. | Present the same proposal/digest with the fresh request and collect another exact decision. No provider call is needed. |
| `finalize_result` | `running` has an authoritative result batch but lacks its visible terminal state. | Let runtime append deterministic local finalization. Do not call a provider or replay assignments. |
| `continue_with_lead` after `failed` | At least one assignment or synthesis failed; every knowable sibling fact is retained. | Continue with the lead from those facts. Do not manufacture a semantic synthesis over an incomplete result set. |
| `continue_with_lead` after `interrupted` | Cancellation/interruption and every knowable result are durable. | Continue with the lead; do not infer missing success or replay automatically. |
| `outcome_unknown` | A running attempt has no authoritative terminal result. | Preserve it and investigate; never replay automatically. |
| `recovery_required` | History is corrupt, dangling, or otherwise unsafe to interpret. | Stop mutation, preserve the workspace, and inspect canonical collaboration history. |

Live read-only Lead failures attempt immediate durable closure. Inspection,
history projection, and reopening never close or resubmit an unfinished legacy
attempt. Explicit new input closes that pending attempt under the session and
conversation leases before appending the fresh turn. A failed Revise attempt
leaves its old proposal superseded and creates no replacement authority. Read-only retry excludes
successful results, uncertain specialist/synthesis work, and pending or
uncertain Apply/Commit outcomes. It neither replays a full turn or successful
specialist automatically nor revives Apply or Commit authority.

An eligible known read-only failure after references can expose the same retry
once runtime closes the exact attempt and reference accounting. Retry requires
explicit new input and reacquires current evidence. Failed closure or uncertain
reference cleanup remains recovery-gated; a recorded provider timeout alone
does not establish that cleanup succeeded.

If a provider invocation already has durable `SUBMITTED` call events and
reference cleanup then loses its session-artifact binding, that binding failure
remains the primary failure. Runtime still preserves the exact submitted-call
count in assignment/result/terminal evidence and carries forward the original
submission-uncertainty classification; cleanup failure neither resets the
count nor invents certainty. The durable count remains matched to `SUBMITTED`
events and never authorizes provider replay.

The owner-conversation authoring objective survives clarification, side
discussion, consultation, review, rejection, staleness, failure, and restart.
Only a matching validated Apply result of `applied` or `unchanged` satisfies it.
Null intent, a message saying the work is finished, or rejection of a step
does not cancel it; cancellation explicitly names the open objective. Session
context makes these facts visible across conversation switches without
transferring ownership or confirmation authority.

After known terminal Apply/Commit outcomes, a later authoring cycle may create
fresh review, Apply, and Commit requests while preserving the earlier records.
Pending controls must be resolved first. Unknown outcomes and recovery states
still block ordinary work. An exact current empty-core status can ground a
replacement only after revalidation; stale or nonempty targets require fresh
attached-reference/READ evidence before a new candidate is prepared.

Confirmed consultations create `running` plus all ordered dispatches as one
durable batch before any provider call. A complete result batch can be
finalized after restart with zero provider calls. Unresolved `running` without
a live owner/result becomes `outcome_unknown` and is never replayed. See
[Lead agent](../guides/lead-agent.md) for the complete state table.

Before a confirmed consultation starts, runtime revalidates the proposal's
exact evidence. A changed attached file or READ boundary, changed core status,
or changed authorized writing-artifact snapshot behind a zero-hit SEARCH makes
the decision stale and prevents specialist calls. Form a fresh lead turn from
current evidence; do not reconstruct an old receipt. Conversation events
appended by the same lead step are excluded from the stable fingerprint used
for a mixed conversation/writing zero-hit claim, so the workflow's own durable
records do not create false drift.

The runtime joins all parallel assignment tasks before classifying a known
partial failure. One failed peer does not cancel successful siblings, and no
lead synthesis is attempted over an incomplete set. Outer cancellation cancels
and joins every child, persists knowable terminal facts/result, removes the
live marker, releases bound-agent resources, and re-raises. For those
specialist/synthesis invocations, a submitted timeout or transport failure
marked outcome-uncertain enters `outcome_unknown`; the read-only Lead retry
path does not abandon them. A PREPARED call that never crossed the provider
boundary does not spend budget or justify that state.

### Recommendation preparation recovery

A recommendation request grants no preparation authority until its exact
structured Confirm or Revise is consumed. The request binds the final history
tail, action ID/revision/digest, sources, evidence, profiles, policies, and
physical-call spend. Reject makes no preparation call. Cancel or EOF leaves
the request undecided. Ordinary input atomically supersedes an unconfirmed
recommendation and starts a new contextual Lead instruction; it is not a
structured Confirm. Pending Apply and Commit controls still take precedence.

| Condition | Recovery behavior |
| --- | --- |
| Source, target, evidence, profile, policy, or spent-call basis changed | Close the decision as `stale`; request fresh planning and confirmation. |
| Repeated, foreign, obsolete, or competing decision | Reject mismatched authority; serialization permits at most one preparation. |
| Confirmed/started record but no provider call after restart | Close the unsubmitted preparation locally when provable. Another attempt needs fresh planning, a new request, and Confirm; the stored start cannot restore a capability. |
| Exact known result awaiting closure | Finalize local history without provider work or fabricated success. Preparation completion does not satisfy an authoring objective. |
| Submitted result unavailable | Preserve `outcome_unknown` and `inspect_and_resolve_outcome`; do not replay. |
| Failed closure, corrupt history, or unproved reference accounting | Preserve `recovery_required`; resolve the exact owner state before new work. |

A no-reference failure or proven-unsubmitted attempt may close locally. A
reference-backed failure requires the exact operation, invocation, and Lead
closure proof before its action result can be persisted. If restart lacks the
durable operation correlation for an unclosed reference-backed failure, it
remains `recovery_required` even when the Lead failure is recorded. Runtime
cannot reconstruct that proof from a recommendation, objective, or remembered
provider thread.

Confirm prepares the disclosed artifact-review or consultation proposal, or
asks a material question. It never starts an automatic sequence or requests
another confirmation for that same preparation. Revise permits only a fresh
recommendation, with no candidate, consultation execution, or objective
transition. Existing objectives remain open through rejection, revision,
staleness, and failure; only a matching successful Apply satisfies one.
Execution origin is separate from immutable objective provenance, and no fake
user event or provider replay supplies either.

Permitted native authentication assistance stays within the same Confirm or
Revise operation and physical-call reservation. Signing in does not repeat the
structured authority or authorize replay of an ambiguous submitted call.
See [authentication recovery](#authentication-recovery) and the
[exact recommendation history](../reference/collaboration-history.md#recommendation-preparation-events).

<a id="conversation-archive-recovery"></a>

## Conversation archive recovery

Stop or coordinate all ordinary conversation mutation before export or import.
The required workspace mutation lease validates the caller, thread, workspace,
and bounded lifetime, but ordinary catalog/event-store writers do not
automatically acquire it. Archive operations have no automatic retry, rollback
of already created exact files, cleanup, overwrite, or deletion.

| Result or failure | Meaning | Safe response |
| --- | --- | --- |
| `ConversationArchiveImportStatus.UNCHANGED` | The complete requested archive state already existed, or the archive was empty. | Treat the destination as unchanged. An empty archive does not prove that the destination is empty. |
| `ConversationArchiveImportStatus.IMPORTED` | At least one exact canonical destination conversation was created and the complete post-import scan matched. | Continue only after the calling workflow accepts that additive result. This is not proof of complete workspace parity. |
| `ConversationArchiveFormatError`, `ConversationArchiveVersionError`, `ConversationArchiveBoundsError`, or `ConversationArchiveDigestError` | The logical value or bytes are invalid, unsupported, oversized, modified, or noncanonical. | Reject the payload. Preserve untrusted input only according to caller policy; do not repair fields, recompute a digest to force acceptance, or import it. |
| `ConversationArchiveSourceError` | The authoritative source layout or values are malformed or unsafe. | Keep the source unchanged, quiesce all writers, and inspect the fixed conversation subtree offline. Do not copy around the rejected entry. |
| `ConversationArchiveSourceChangedError` | The two canonical export scans differed or the second scan became invalid. | Discard the attempted export, keep the source quiescent, investigate the change, and start a completely new export only after stability is established. |
| `ConversationArchiveConflictError` | An addressed destination identity has different metadata or collaboration-history values. | Preserve both inputs and investigate. The adapter does not merge, replace, or delete the destination. |
| `ConversationArchiveDestinationError` | Destination preflight found malformed/unsafe state, or a write could not complete safely. | Preserve the destination. Inspect before deciding whether it is unchanged or contains an exact metadata-first prefix; never remove an unknown artifact to force import. |
| `ConversationArchiveDestinationChangedError` | A target collision, path change, or complete post-import rescan differed from the preflight plan. | Stop all destination mutation and inspect every addressed identity. Some exact create-only files may already exist; do not assume rollback. |
| `ConversationStorageCoordinationError` | The lease is invalid, released, expired, wrong-thread, wrong-workspace, or failed during the operation. | End the old lease, quiesce the workspace, and acquire a fresh exact-workspace lease. Inspect possible exact prefixes before reapplying a payload. |

Import deliberately supports resuming only its recognized canonical empty
directory prefixes or exact metadata awaiting a nonempty history file. Reapply
only the identical canonical payload, under a fresh valid lease, after the
destination is quiescent and inspection shows no divergence. Exact complete
records are accepted; unrelated complete conversations are preserved. Unknown,
unsafe, or divergent state is not resumable.

The archive contains only catalog metadata and canonical v2 collaboration
history, not debates,
defaults, providers, prompts, manuscripts, artifacts, configuration, logs, or
other workspace state. It is transport infrastructure rather than a complete
backup, arbitrary restore, or backend switch. The separate same-workspace
transfer coordinator composes these archive adapters but does not broaden the
archive contents. See the exact
[archive contract](../reference/conversation-archives.md).

<a id="conversation-storage-transfer-recovery"></a>

## Conversation storage transfer recovery

Run transfer only after stopping or coordinating every unsupported direct
writer. `--from` must match the active backend, filesystem and SQLite must be
distinct, and the SQLite database must already be provisioned and ready. One
outer coordinator lease then covers planning through fresh final parity. The
workflow never provisions, activates, dual-writes, deletes, or retries
automatically.

Use the guide for the
[forward transfer](../guides/sqlite-conversation-storage.md#sqlite-storage-transfer)
and
[create-or-identical reverse workflow](../guides/sqlite-conversation-storage.md#sqlite-storage-reverse-transfer).

| Report or exit | Meaning | Safe response |
| --- | --- | --- |
| Dry-run `work_required` or `completed`, exit `0` | The fresh plan is valid. `work_required` has at least one `create`; `completed` needs no creation and reports verified parity. No destination or checkpoint was written, although the empty coordinator guard may have been created. | Review all five counts, keep both stores quiescent, then run the same non-dry command if copying is intended. A later run exports and plans again. |
| Transfer `completed` / `verified`, exit `0` | Fresh final source and destination canonical archives matched. A terminal per-direction checkpoint was retained. | Run `story storage verify` for an independent fresh comparison, then separately decide whether to activate in an approved workflow. Do not delete the source or checkpoint based only on this report. |
| Verify `parity_mismatch`, exit `1` | The otherwise valid plan has source records to create or destination-only records. Verify ignored any checkpoint and changed no conversation state. | Keep both stores, inspect ownership, and transfer only when the destination has no destination-only records. Never use activation to hide the mismatch. |
| `conflict`, `unsupported`, or transfer `destination_only`, exit `2` | A divergent identity, SQLite-unrepresentable integer, or extra destination identity blocks the complete plan. No transfer or checkpoint update occurred. | Preserve both stores and the old checkpoint. Investigate offline; do not overwrite, truncate, delete, or hand-edit one side to force success. |
| `activation_mismatch`, `activation_invalid`, `provisioning_needed`, `backend_unavailable`, `invalid_request`, `workspace_invalid`, `unsafe_state`, or `coordination_failed`, exit `2` | A precondition or safe-coordination boundary failed. | Correct the selected direction or workspace, restore trustworthy activation/path state, explicitly provision an intended empty SQLite target through its API, or wait for the supported owner. Do not treat transfer as installation or activation. |
| `checkpoint_invalid` or `checkpoint_unsupported`, exit `2` | Transfer could not trust its direction-specific checkpoint. Verify does not read checkpoints. | Preserve the file and both stores. Back up and inspect the fixed schema/path offline; do not rewrite progress. Retry transfer only after an approved recovery restores trustworthy checkpoint state. |
| `source_changed`, final `parity_mismatch`, `source_invalid`, `destination_invalid`, or `operational_failure`, exit `2` | Failure may have occurred after destination work. Filesystem-to-SQLite rolls back an uncommitted import, but a source change can follow a committed SQLite transaction. SQLite-to-filesystem can retain complete exact conversations, and checkpoint publication can fail after one completes. | Quiesce both stores. Freshly export/inspect or run verify when safe; never assume rollback. Re-running transfer revalidates exact destination records and any checkpoint before resuming. |

`source_invalid` or `destination_invalid` can mean that archive/transfer found
a filesystem `transcript.jsonl` or SQLite `conversation_events` row. Those are
unsupported owning-boundary inputs. Preserve the stores; do not rename,
parse-and-rewrite, or hand-copy them into v2. Strict workspace-v2 readiness
rejects unsupported whole-workspace state, and active adapters also reject the
addressed owning boundary.

Checkpoints remain at
`agent_states/orchestrator_state/conversation_storage_transfers/{filesystem-to-sqlite,sqlite-to-filesystem}.json`.
They are strict, bounded, regular, singly linked schema-v1 files. A matching
fingerprint is resumed only when its counts, completed identities, and fresh
destination records exactly match the fresh source. A changed source starts new
progress and replaces a safe old checkpoint only after the plan is otherwise
unblocked. A blocked plan preserves the old checkpoint. A successful terminal
checkpoint is retained as audit state; neither verify nor dry-run removes it.

Filesystem-to-SQLite destination insertion is one transaction. For the reverse
direction, complete global filesystem preflight precedes mutation and each
checkpoint advance follows confirmation and reread of one complete exact
conversation. This permits safe replay after publication/checkpoint uncertainty
without accepting an incomplete history. It is not rollback across both
stores. Exact command/report semantics are in the
[CLI reference](../reference/cli.md#conversation-storage-transfer).

<a id="conversation-storage-selection-and-activation-recovery"></a>

## Conversation storage selection and activation recovery

Keep desired and active state distinct while diagnosing a failed switch.
`config/system_defaults.json` stores desired
`conversation_storage_backend`; missing or null means filesystem.
`config/conversation_storage_state.json` stores strict schema-v1 active backend
and generation; a missing marker means filesystem generation zero. A completed
`storage use` or Settings Apply Changes updates desired state only. Do not
hand-edit either
file or delete one store to manufacture agreement.

The newcomer-oriented
[SQLite selection and activation scenarios](../guides/sqlite-conversation-storage.md#sqlite-storage-readiness)
show the normal path before the failure matrix below.

| Report, startup result, or exit | Meaning | Safe response |
| --- | --- | --- |
| `completed`, exit `0`, `activation: next_shell` | Readiness/parity and the final configuration CAS succeeded. The current shell was not switched, even when `configuration` is `unchanged`. | Exit the old shell normally and start a new one. Do not delete the old store; startup performs another fresh check before marker CAS. |
| `installation_required`, exit `2` | The active Python interpreter lacks functional SQLite, one exact supported host command exists, and installation was not authorized. | Review the command in the CLI reference. If host mutation is intended, rerun `story storage use sqlite --install-missing --yes`; `/settings` instead previews the command and requires exact YES. |
| `installation_disallowed`, exit `2` | The user declined the Settings prompt or a recognized nonempty CI marker forbids host mutation. | Preserve configuration. Install outside this workflow only under the host's approved operations policy, clear a CI marker only when it is genuinely incorrect, then retry without assuming readiness. |
| `installation_unsupported` or `installation_failed`, exit `2` | The exact OS/package-manager plan is unsupported, or the one authorized installer failed. | Inspect host/package-manager diagnostics. Do not substitute arbitrary arguments through Story. Complete approved host repair separately, then rerun selection. |
| `interpreter_unavailable`, exit `2` | The installer may have exited zero, but the same Python interpreter still failed its complete SQLite probe. The report can show `installation: completed`. | Rebuild or reinstall that Python interpreter with functional SQLite support, then rerun. Installing only a standalone `sqlite3` binary is insufficient. |
| `provisioning_failed`, exit `2` | The fixed SQLite database could not be safely proved ready or create-only provisioned. | Preserve the database, sidecars, and temporary residue. Run direct observational preflight or follow the SQLite recovery table; never delete suspect state to force an empty target. |
| `transfer_required`, exit `2` | The active canonical source is nonempty and the requested target is empty. An empty SQLite database created during selection can remain ready. | Run transfer dry-run, transfer, and verify from the active backend to the target; rerun `storage use`, then start a new shell. Selection itself never copies data. |
| `parity_mismatch`, exit `2` | Both canonical stores are nonempty and differ. | Preserve both. Use `story storage verify` in the active direction and investigate conflicts or destination-only state offline. Do not overwrite, merge, truncate, or switch around the mismatch. |
| `configuration_invalid`, `configuration_conflict`, or `configuration_write_failed`, exit `2` | Pending configuration was invalid, an opening config/persona fact changed, or publication/rollback could not be reported as a clean success. Host installation or an empty provisioned database can remain. | Preserve both config files and storage. Reopen Settings or rerun `storage use` from a fresh baseline. After write uncertainty, inspect both complete configuration files before another attempt. |
| `coordination_failed`, `workspace_invalid`, or `invalid_request`, exit `2` | The workspace/lease/request boundary failed before a safe desired save. | Correct exact syntax/workspace, wait for supported work, and retry with a fresh invocation. Without POSIX `fcntl`, selection, provisioning, config save, and activation remain unavailable. |
| Interactive startup aborts before the welcome | Desired and active state could not be safely reconciled; the active marker was preserved and there is no silent fallback. | Read the sanitized hint, quiesce unsupported writers, then run `storage use`, transfer, or verify as appropriate. Repair desired/readiness/parity rather than forcing the marker. |
| Startup advanced the marker, then later terminal/auth/backend/session startup failed | Activation CAS already succeeded; unrelated later failure is not compensation for that durable switch. | Fix the later failure and start Story again. The new active backend must now be treated as authoritative; do not restore the old marker manually. |

Selection proves coordination briefly, releases it during consent/installation,
then reacquires it for same-process reprobe, database preparation, parity, the
Settings persona guard, and configuration CAS. Host installation and empty
database creation deliberately fall outside configuration rollback. Startup
instead holds one outer lease through fresh active/target composition and
marker CAS. Direct low-level filesystem adapters and external editors do not
join this guard; stop or otherwise coordinate them before either workflow.

The exact report schema, failure mapping, platform commands, CI marker set, and
end-to-end command sequence are in the
[CLI reference](../reference/cli.md#conversation-storage-selection).

<a id="sqlite-conversation-storage-recovery"></a>

## SQLite conversation-storage recovery

SQLite preflight is strict, observational, and byte-preserving. It may load and
probe the host `sqlite3` runtime and open an existing database read-only, but it
does not create, repair, convert, rewrite, or remove storage. Readiness applies
to the authoritative main database; the derived FTS companion is not required
for preflight, provisioning, composition, reads, or exact search.

See the guide's
[data lifecycle](../guides/sqlite-conversation-storage.md#sqlite-storage-data-lifecycle)
for the plain-language authority, backup, and exposure model.

| Result or failure | Meaning | Safe response |
| --- | --- | --- |
| `provisioning_needed` | On a capable host, the fixed database is absent. | Provision only after deciding the SQLite destination is deliberately empty and acquiring the exact workspace mutation lease. Provisioning does not import filesystem conversations. |
| `unavailable` or `ConversationStorageUnavailableError` | The active Python interpreter's `sqlite3` is missing, older than 3.7.16, fails its functional runtime probe, the database is busy/locked, or an operational resource is unavailable. | Direct preflight remains observational. For a missing runtime, use the explicit selection workflow only when one allowlisted host command and its consent policy apply; otherwise repair the interpreter outside Story. There is no automatic retry, and the standalone SQLite CLI is not readiness proof. |
| `ConversationStorageProvisioningNeededError` | Composition or an adapter operation requires a database that has not been provisioned. | Run observational preflight and, only for an intended empty target, the explicit leased provisioning flow. |
| `WorkspaceVersionUnsupportedError` | The database has the recognized application identity but an unsupported schema version. | Stop workspace processes and preserve a complete backup. Restore an exact supported database from trusted state or create a separate new v2 workspace; Story provides no database or workspace migration/upgrade path. |
| `WorkspaceFormatError` | Header, schema manifest/fingerprint/cookie, encoding, integrity, foreign keys, required lexical generation/searchable values, event structure, or strict sidecar state is invalid, or the stable database-name entry is nonregular without being a symlink. | Preserve the database and sidecars unchanged. Restore a known-good complete database or investigate offline; do not hand-edit or ask preflight to repair it. |
| `WorkspaceSecurityError` | The database name is a symlink, or its named/opened path identity is unsafe, nonmeaningful, replaced, mismatched, or has an unexpected link count. | Stop all processes, back up the complete workspace, and restore safe exact path identities. Do not manually follow, unlink, or replace suspicious names. Explicit leased provisioning can recognize only the exact interrupted-publication case described below. |
| `ConversationStorageCoordinationError` | A lease is wrong-workspace, wrong-thread, expired, released, or cannot be acquired safely; the POSIX advisory guard may also be unavailable or unsafe. | Let the supported owner finish or close it, then acquire a fresh exact-workspace lease. Never reuse or manufacture lease evidence. Without `fcntl`, keep use observational; supported provisioning and mutations remain unavailable. |
| `ConversationArchiveConflictError` during canonical archive import | An existing identity differs from the incoming exact metadata or v2 history. | Keep both sources and investigate. The whole import transaction rolls back; there is no merge or overwrite mode. |

### Derived search companion recovery

The fixed `discussion_logs/.conversation_storage_fts5.sqlite3` file is a
private disposable hint resource, not a second authority. Missing, stale,
busy, malformed, unsafe, generation-mismatched, runtime-incompatible, or
FTS5-unavailable companion state always yields the same exact bounded
main-database search. Search does not create, repair, remove, replace, or follow
the rejected state, and these conditions do not change SQLite readiness.

After an effective authoritative mutation commits, the adapter may create an
absent companion or refresh a recognized safe one under the still-active
workspace lease. Failure in that post-commit refresh is contained: the main
mutation stays committed and subsequent searches use exact fallback. Do not
replay an append or other logical mutation merely to refresh hints. A required
main-database lexical-value or generation failure occurs before commit and
rolls back the mutation instead.

There is no delete API or general repair operation. The supported direct index
owner can create an absent resource or rebuild only a recognized safe one
through its validated publication API; it does not authorize manual path
changes. Do not hand-edit, unlink, or replace an unsafe or malformed companion
to force recovery; preserve it for offline investigation and rely on exact
fallback.
Owner cleanup is limited to its own unchanged temporary resources and one exact
interrupted create-only alias shape. A backup may omit the companion but must
retain the authoritative main database; if the companion is retained, protect
it as plaintext transcript data and keep it with its matching main-database
generation.

The amended exact schema remains version `1`. A database produced by older
unpublished pre-Issue-257 development code has the old fingerprint and can be
rejected as malformed. There is no released-user upgrade promise, v2 schema,
or automatic conversion for that development artifact.

Preflight and ordinary reads never repair or remove storage. Explicit leased
provisioning has one narrow cleanup for an interrupted no-overwrite
publication: the target and exactly one provisioning-pattern sibling must be
the same regular inode with link count two, with no sidecars, extra link, or
candidate ambiguity. Provisioning exhaustively validates that intact two-link
target before any unlink, then rechecks the lease, exact candidate set,
sidecars, and both identities. Only then does it remove the temporary alias and
synchronize the target and parent directory; it exhaustively validates the
recovered single-link target again before success. Symlinks, wrong or multiple
candidates, unrelated inodes, extra links, sidecars, and malformed, corrupt, or
unsupported target content are preserved and fail closed. This is not a
general database repair path.

Routine catalog and event-store calls verify bounded database/schema identity
and only the rows addressed by the operation. Append obtains the indexed
maximum sequence and at most one user/outcome pair for its turn; it does not
scan the full transcript. Run explicit preflight when full database health must
be established: it runs integrity and global foreign-key checks, rejects
orphans, and reconstructs every domain row. Archive export also validates all
metadata/event rows globally and rejects orphans. Archive import validates the
complete destination both before and after mutation in one transaction, so
preexisting or newly introduced orphan/malformed rows cannot be silently
adopted or dropped.

If POSIX `fcntl` is unavailable, a valid existing database can still be
preflighted and read through the direct API. The supported coordinator cannot
acquire its mutation lease, so provisioning and supported writes fail closed
without changing the SQLite database or conversation domain state. The lazy
coordination guard retains its documented lifecycle. Installation through
selection, desired-config save, and startup activation also remain unavailable
because each mutation requires that coordinator.

Strict preflight, composition, provisioning, and write boundaries reject and
preserve every `-journal`, `-wal`, or `-shm` sidecar. An ordinary adapter read
alone may tolerate a regular, singly linked DELETE rollback journal while a
supported writer is live. If strict preflight observes a journal, allow the
writer to finish and retry; never delete a sidecar to force readiness. A
lingering, malformed, linked, WAL, or shared-memory sidecar remains a
fail-closed condition requiring offline investigation or restoration.

Transactions roll back uncommitted work and close their connection. A commit
can succeed before a later close failure is reported, and provisioning can
install one complete database before file/directory synchronization reports a
failure. After either case, stop competing work and run fresh preflight plus a
domain read before deciding whether to retry; blindly replaying a logical
append could duplicate intended work. A failed best-effort companion refresh
does not change that committed main state and needs no mutation replay. Beyond
the exact leased alias cleanup above, the API performs no repair, compensation,
retry, schema upgrade, activation, or source deletion. Explicit conversation
data transfer between this ready database and the filesystem uses the separate
coordinator described above.

<a id="unsupported-v1-session-data"></a>

## Unsupported V1 session data

V1 provider manifests, `<session_id>.lease.json`, `.lease-claims.lock`, V1
`cli_context.json`, and `conversation_selections/` are rejected unchanged with
`WorkspaceVersionUnsupportedError`. There is no conversion or compatibility mode.
At the whole-workspace boundary, recognizable pre-v2 state is unsupported
before normal activation. Stop every process, preserve and verify an external
backup, and create a separate new v2 workspace. Do not move selected legacy
files out of the old root and rerun init, and do not copy old provider state,
access files, guards, defaults, CLI context, or configuration into the new
baseline. Any later reviewed content transfer must use a separately supported
workflow; #454 supplies none.

<a id="authentication-recovery"></a>

## Authentication recovery

Only a fresh native status probe authorizes a provider. Never infer success
from an authorization code, completed login process, previous picker row,
configured route, or provider session manifest. While native login remains
open, individually bounded status probes use null input and output and cannot
consume pasted input or capture provider output. They have no overall login
deadline.

| State or outcome | Meaning | Recovery |
| --- | --- | --- |
| `unavailable` | The provider executable could not be launched and its picker choice is disabled. | Install the Codex CLI or Antigravity `agy`, or correct the supported executable override or `PATH` in the environment that launches `story`, then retry startup or `/auth`. |
| `unauthenticated` | The exact probe returned the native unauthenticated code. | Select the provider to run `codex login` or the resolved Antigravity executable with no arguments (bare `agy` by default), then complete the provider-owned flow. A successful remote flow needs at most its one authorization-code paste; Story asks for no second paste or exit step. |
| `indeterminate` | Launch, wait, timeout, or return status did not prove either supported state. | Check `codex login status` or `<resolved Antigravity executable> models` (`agy models` by default) outside the runtime, repair the native CLI or environment, then retry. Selection itself performs another probe and never authorizes optimistically. |
| Native login remains open after authentication | A fresh status probe reports authenticated while the provider CLI still owns an interactive screen. | Wait for Story to boundedly stop and reap that exact login child and return automatically. Do not enter `/exit`, Ctrl-C, EOF, extra Enter, confirmation, or another authorization code. |
| Login fails or exits without authentication | The selected-provider probes, including the fresh probe after child exit, still do not report authenticated. | Read the native CLI's own terminal guidance, repair outside the runtime, and select it again. The runtime has no captured provider output, code, or URL to recover. |
| Startup picker Cancel | No provider was chosen. | The hard gate remains open; choose an enabled provider. Ctrl-C or EOF exits normally before context/backend/controller/session construction. |
| `/auth` picker Cancel, interrupt, or EOF | The picker ended without a new authorization. | The shell continues, explicit routes stay unchanged, and any already persisted default remains. Run `/auth` again when ready. |
| Ctrl-C during native login | The inherited provider process and runtime received the interrupt. | The runtime best-effort stops and reaps that child. Startup exits normally; an already running shell follows its fatal interruption cleanup. Retry after checking native state. |
| Settings Provider-editor authentication succeeds | Fresh authorization was proved within the subordinate editor, which returns to Provider. | The provider route and any missing workspace default remain pending in memory. Escape or cancellation before confirmed Apply Changes leaves configuration bytes unchanged; no configuration value is persisted. |
| Settings Provider-editor authentication is cancelled or fails | Fresh authorization was not proved before assignment. | The pending route is unchanged. Retry the provider choice or cancel settings; configuration bytes remain unchanged before confirmed Apply Changes. |
| Apply-time status is stale or fails after confirmation | One distinct effective provider did not freshly authenticate before the mixed-candidate file transaction. | Repair or complete native sign-in, return to Apply Changes, and reprobe. No pending settings file update occurs. Any missing default learned during the gate remains in the pending candidate and becomes durable only when the final storage-coordinated Apply completes. |
| Message-details-only Apply fails | The presentation-only fast path could not prepare, verify, or commit the exact candidate. It performs no provider, backend/runtime, storage, route-generation, or thread work. | The active display flag remains unchanged unless the configuration commit completed. Review the fixed settings error and retry Apply Changes. |
| Conversation-RAG-only Apply fails | The startup-policy fast path could not prepare, verify, or commit the exact system candidate. It performs no authentication, storage, backend/runtime, route/thread, composition, optional-package probe/install, provider-catalog, subprocess, socket, or network work. | The current RAG service and automatic policy are unchanged regardless. Inspect the fixed settings result and persisted configuration before retrying Apply Changes. |

Interactive startup first needs any authenticated provider, then every distinct
effective route under CLI override → agent route → workspace default → Codex.
Removing or rewriting an explicit route is not an authentication recovery
mechanism. Noninteractive commands do not open the picker; prepare native
authentication before using their provider routes.

<a id="settings-activation-and-route-recovery"></a>

## Settings activation and route recovery

Confirmed `/settings` Apply Changes uses the current-shell operation for
live-capable changes: authenticate and preflight all effective providers,
prepare/quiesce the runtime, validate the complete candidate, persist it, then
activate it. A candidate whose sole difference is `show_message_details`
instead uses the
prepare/verify/commit configuration path and updates the active presentation
flag only after commit; it skips the full provider, runtime, storage, route, and
thread activation path. A Conversation-RAG-only candidate uses the same guarded
configuration transaction without authentication, storage, runtime,
route/thread, composition, optional-package, provider-catalog, subprocess,
socket, or network work; its saved policy applies only to the next shell. Every
other mixed candidate uses the full operation. Its exact prepared boundary
is `config/system_defaults.json`, `config/agent_configs.json`, and optional
`agent_states/orchestrator_state/provider_route_generations.json`. Launch
overrides remain read-only and higher precedence.

| Failure point | Guaranteed state | Recovery |
| --- | --- | --- |
| Before persistence | Active runtime and every prepared target are unchanged. | Correct authentication, validation, conflict, preflight, or quiescence and retry Apply Changes. |
| During a prepared write, with proved rollback | Every attempted target is restored from exact opening bytes; active runtime is unchanged. | Review the fixed error and retry. |
| After persistence, with proved compensation | Exact prior file state is restored and the old runtime remains active. | Review settings and retry in the same shell. |
| Restoration or active generation cannot be proved | The shell reports `recovery_required`; persisted and in-memory state may not agree. | Stop using that shell immediately. Close it, inspect and back up the prepared targets, and restart against the persisted configuration. |

Restart is exceptional recovery, not the normal activation path for live-
capable changes. A successful live Apply says it was applied to the running
shell; a RAG-only Apply says only that its policy was saved for the next shell.
Changed profiles remain pending only until their first complete logical
provider call succeeds. Other active shells are not updated and retain their
captured state.

There is no watcher for manual changes to the two configuration JSON files. A
new backend launch validates those bytes and reconciles the runtime-owned route
generation record; an existing shell does not. Never decrement, delete, or edit
`provider_route_generations.json` to revive an older provider thread. Every
provider switch, including A→B→A, deliberately advances freshness. Restore a
verified complete backup if the record is corrupt or inconsistent.

A fresh target-provider thread starts a new contextual epoch and bootstraps
current role-relevant authoritative sources. For ordinary Lead turns and
consultation/artifact-review synthesis, this includes exact retained active and
archived session history, current directives, and complete current core bytes
on the next safe authorized work. It does not resolve or replay an unmatched
submitted call. It is expected to lack provider-native hidden history; that
history is never correctness authority.
Use `/history`, `/status`, live `[context]` progress, and reply metadata to
distinguish story-session continuity from native-thread reuse. See
[Story sessions](../guides/story-sessions.md#execution-identity-and-fresh-thread-continuity).

## Debate and shell-run recovery

Debate IDs reserve before providers and cannot resume; debate `active` is not
liveness. Inspect, fix, and use a new ID. Context sidecars do not enable replay.

Workspace failure returns safely only when the same session remains `ready` and
`held_here`; failed proof clears scope and exits. `ProgressObserverError` marks
preterminal debates `interrupted` when possible; later failures cannot rewrite results.

## Writing-artifact recovery

Every repository-owned artifact listing, read, reference capture, catalog
lookup, and application first checks for a pending application journal under
the verified readiness-triple lease. The schema-1 journal is one immutable,
content-bearing image containing the exact request, catalog-before
existence/hash/image, schema-2 catalog-after image, content-free application
record, and ordered targets with their complete new bytes and old/new hashes.
It is never rewritten to record a later phase.

Recovery compares the current catalog with those two exact positions. From
catalog-before, every target may equal only its recorded old or new bytes;
recovery rolls the complete set forward, publishes the exact immutable
application record, and publishes the current-membership catalog last. If the
record already exists, its canonical bytes must exactly equal the journal's
record. The projected catalog must equal the complete ordered application
history after that record. At catalog-after, recovery verifies the targets,
record, and catalog without republishing them. It then hash-checks the unchanged
journal and moves it with native no-replace rename to a `.completed` staging
name. A catalog matching neither position, an untrusted target, or a
missing/replaced journal or final application record blocks rather than
guessing. Users may see an infrastructure failure followed by successful
recovery on the next repository artifact access; inspect the associated debate
because its result and lifecycle are outside the artifact transaction.

The `apply_artifact_changes(request)` seam exposes only fixed content-free
outcomes:

| What happened | External result and safe next action |
| --- | --- |
| The exact first application completes. | Immutable `applied` result. Inspect its affected/final/rename/delete facts; call `catalog()` separately for complete current membership. |
| The same completed request is retried after a lost response. | Immutable `unchanged` result with the original historical facts and no republish. |
| Current artifact bytes no longer match the request before journaling. | `error.code == ArtifactApplicationErrorCode.DRIFT` (`"drift"`); reread, rebuild, review, and obtain fresh Apply confirmation. No target was written. |
| Durable catalog/application state is malformed or unsafe without a recovery block. | `error.code == ArtifactApplicationErrorCode.CORRUPTION` (`"corruption"`); stop writers, preserve evidence, and restore verified exact metadata. |
| Journal recovery cannot establish exact old/new authority and records a block marker. | `error.code == ArtifactApplicationErrorCode.RECOVERY_REQUIRED` (`"recovery_required"`); do not retry, reset, clean, or delete evidence. Perform operator recovery from a complete trusted state. |

A recoverable crash is not reported as a partial application on the next
authoritative call: that call completes or verifies the existing journal first.
Reject and Revise are not repository outcomes; runtime must leave this seam
uninvoked. Recovery does not infer confirmation, refresh agent context, call a
provider, or stage/commit Git. Any later Git operation has separate evidence
and confirmation.

Read-only `inspect_artifact_application(application_id)` uses the same
recovery-before-read boundary and audits the full application ledger/catalog
before deciding whether one safe identity exists:

| Inspection outcome | Meaning and safe action |
| --- | --- |
| Original immutable result with state `applied` | The application record exists and complete authority audited successfully. This is the historical first-application result, not the `unchanged` state returned by an exact apply retry. Inspect its content-free facts; use a separately correlated session record when session ownership matters. |
| `None` | The safe ID is absent after successful recovery and audit. Do not infer that a session did or did not apply some differently identified request. |
| `INVALID_IDENTIFIER` | The supplied value is not an exact safe built-in string. Correct the identifier; the repository did not take its lock or inspect persisted state. |
| `CORRUPTION` | Catalog/application records are malformed, unsafe, or inconsistent. Preserve them and restore one verified exact authority; absence was not established. |
| `RECOVERY_REQUIRED` | A durable block marker exists or a journal cannot be reconciled. Stop writers and perform operator recovery without reset, cleanup, replay, or evidence deletion. |

Inspection never reconstructs a request, starts a new application, refreshes
context, calls a provider, or touches Git. Its recovery prerequisite may finish
an already-journaled application; without one, it writes no canonical artifact
or application record. A first repository access may also create the standard
lock/state-directory bootstrap needed to hold the read lease. The internal
#527 reconciler can consume a result using correlation it already owns, but
inspection itself cannot discover a session or confirmation owner.

Application temporaries live under the repository's separate `staging/`
directory rather than `transactions/`. Failed `.tmp` bytes, prior `.held`
bytes, failed published `.retired` bytes, and finished `.completed` journals are
all retained there. This residue is normal internal state, is ignored and
preserved by transaction recovery and catalog reads, and may grow. Only exact
JSON journal names in `transactions/` are authoritative; any unexpected entry
there is ambiguity and blocks recovery.

If a journal is invalid or a target diverged, the repository writes
`agent_states/orchestrator_state/writing_artifacts/recovery_blocked.json` and
all later repository-owned artifact listing, reading, reference capture,
catalog, and application access fails with `writing-artifact recovery is
blocked`.
Stop workspace processes, preserve a complete backup, and investigate the
journal and target history. Do not delete or edit the lock, journal, catalog,
application records, or block marker to force recovery; no automatic repair
command exists. Restore a verified backup when trustworthy reconstruction is
not possible. See
[Canonical writing artifacts](../reference/writing-artifacts.md).

Filesystem-source discovery and selective prompt-context reads do not use the
application catalog as their source of path membership. They include present
safe manual and hash-divergent canonical files and omit an individual missing,
unsafe, hard-linked, nonregular, or invalid-path entry. They still acquire the
repository lease and run pending application recovery before reading. With no
pending journal or recovery-block marker, malformed catalog or application
record state blocks authoritative catalog/application access but does not blank
otherwise safe source listing or reads. Workspace-root, ancestor, lock-lease,
journal-recovery, or persistent recovery-block failures still propagate.

`catalog.json` schema 2 records only current membership. Immutable schema-1
application records preserve historical final facts, manuscript rename
lineage, and deletion provenance; omission from the catalog is not loss of that
history. A missing catalog beside records, a sequence gap, an unexpected or
malformed record, noncanonical JSON, or a catalog/history mismatch fails the
catalog/application audit. The runtime does not reconstruct, rewrite, or
convert this authority. Old nonempty `writing_artifacts/commits/`, catalog
schema 1, and transaction schema 2 are deliberately unsupported and fail
closed; no compatibility migration is provided.

Operators should stop workspace processes, preserve a complete backup, and
restore exact verified repository metadata. The runtime does not normalize a
hand-edited application record, transaction journal, or catalog.

The permanent artifact lock is similarly not repaired. Exact primary, anchor,
and `ready` aliases fail before final inspection or locking, and the three names
must be hard links to one regular inode with link count three. Initialization
holds a bootstrap `flock` on the open writing-artifact state directory, flushes
the retained ancestry, exclusively creates and flushes the primary, flushes its
parent, links and flushes the anchor, verifies that `ready` is still absent,
then links and flushes `ready`. Only after triple verification does it acquire
the primary `flock`, reverify, release bootstrap, and enter protected work.

Any failure avoids protected work. A partial one- or two-link state—including
the old development pair—a divergent inode, alias, or extra link fails again on
later artifact access and remains untouched. Stop all workspace processes,
preserve a backup, and investigate the triple; there is no cleanup,
reconstruction, or conversion.

Stop every workspace process before a manual or external filesystem edit. The
repository lease serializes supported repository writers, but an actively
hostile same-user process mutating live names during the native no-replace call
is outside the supported boundary. Darwin and Linux hosts without the required
native atomic no-replace rename fail on their first artifact operation; other
non-artifact workspace/runtime APIs remain usable.

## Explicit-reference evidence recovery

The repository persists content-free reference-operation and invocation
history, while local authorization capabilities and selected-byte carriers
remain transient. A provider may retain content after receipt as described in
[Privacy and provider exposure](privacy-and-provider.md#explicit-reference-data-boundary).
The backend drives each invocation through its exact provider action, delivery,
result receipt, revalidation, and acceptance. `StoryRuntime` finishes the
top-level operation only after every invocation is terminal and final
revalidation passes. After process loss, history can be inspected but an
interrupted authorization cannot be resumed or used to reconstruct content.
Start a new operation with a new ID after correcting any source problem.

For runtime-composed guarded references, selected-root security or binding
loss is preserved for the orchestration recovery boundary rather than reduced
to a generic workflow failure. No provider call or fallback source is
authorized by that failure. Guarded creation publishes its exact operation or
invocation owner before the access exit can fail. `StoryRuntime` retains that
owner strongly in one process-shared workspace/session/physical-worktree/
operation registry. Every failure after that publication defers cleanup.
Deferral immediately closes selected-byte results, content-bearing
authorization, capability authority, and the old binding access. The unfinished
operation retains only content-free cleanup ownership and one private,
non-traversed duplicated directory descriptor that pins the exact original
worktree; the pin is never read, serialized, provider-visible, or used as
repository authority.
Neither the failed invocation nor known provider work is replayed.

For a nonempty guarded reference batch, runtime acquires and validates the
non-inheritable session-root pin before `CAPTURED` authorization can become
durable. Supplier, validation, operation-ID, construction, and other
pre-transfer failures close the pin. A failure in the post-capture registry
callback is the exceptional unowned case: the content-bearing authorization
and pin close immediately, the callback error escapes unchanged, and the
durable history remains at `REQUESTED` → `CAPTURED`. Cleanup does not recursively
re-enter the repository from that callback. Because no transient owner survives
as cleanup authority, a later artifact-sensitive boundary discovers this
content-free operation as a restart orphan and terminalizes it through the
normal inventory path without replaying provider work.

The next same-process artifact-sensitive boundary drains a retained owner only
through a fresh binding with the same physical worktree identity. A replacement
root cannot receive the old cleanup: runtime closes the pin, removes that stale
transient owner, and does not include its operation ID in the replacement root's
live-owner exclusion set. Terminal success or failure, reconciled completion,
stale-owner abandonment, and every unretained preparation path each close the
pin exactly once. The original durable history remains
unchanged. A candidate-only consensus call enters this boundary only for known
same-process pending cleanup and otherwise stays worktree-free. After restart,
the next artifact-sensitive boundary inventories durable histories and
reconciles unowned nonterminal operations. It terminates every unfinished
invocation before the operation, is idempotent after interruption, and leaves
already terminal evidence unchanged. A restart candidate-only consensus call
does not scan the worktree, so an orphan waits for an artifact-sensitive
boundary. Corrupt evidence fails closed and blocks the boundary; it is never
skipped, rewritten, or used to authorize replay.

`inspect_reference_operation()` distinguishes absence from corruption. It
returns no history only when the named operation directory is absent. If any
part of a present operation is missing, changed, noncanonical, duplicated,
gapped, unexpectedly named, or inconsistent with the state transitions, the
operation fails closed with `evidence_corrupt`. Retain the directory for
inspection, stop workspace processes, and preserve a complete backup. Do not
edit or delete its manifest or events to force a retry.

One corrupt `input_snapshots/<operation-id>/` tree does not make another
healthy reference history, the writing-artifact catalog, exact artifact reads,
or artifact commits unusable. Repository-root, lock, storage, catalog, or
global provenance failures still retain their broader fail-closed effects. A
new reference operation may use a new ID once the underlying artifact is safe
and eligible; the corrupt evidence remains unchanged. A session-bound caller
can inspect only IDs belonging to its session.

Fixed terminal outcomes distinguish cancellation, provider/workflow failure,
drift, invalid content/range/limits, correlation/capability failure, and corrupt
evidence without storing content in the diagnostic. A `terminated` operation
or invocation is final. A failed runtime invocation terminates its top-level
operation. An explicit retry therefore uses a new operation, capture, ordinal
sequence, coordinator, and one-use capability.

The backend does not retry automatically. The runtime's narrow
[typed continuation replacement](../reference/backend-and-sessions.md#lead-session-continuation)
requires proof of pre-execution rejection, which current native adapters do
not emit. An explicit retry or replacement needs a fresh invocation ID,
ordinal/attempt correlation, private coordinator,
and one-use capability; it cannot reuse terminal evidence. Cancellation,
provider or workflow failure, drift, malformed or nonexact provider action,
and wrong correlation terminate the current coordinator with a fixed
content-free outcome. Once the provider sequence advances, the backend
best-effort taints the compatible thread where required so later work cannot
assume a clean continuation. The original failure remains primary.

Inline `${...}` input on a provider-bound Python/component path creates and
finalizes these states. Successful conversation, retrieval, and debate results carry an optional operation ID;
`StoryRuntime` and `RuntimeSession` expose content-free inspection. The shell
maps failures to existing safe error categories rather than displaying this
history. Pre-submit `$`/`$$` discovery and viewer selection create no operation
ID or evidence; their empty, invalid, stale, unreadable, and unavailable cases
use fixed content-free shell errors and restore the exact draft.

For eligible read-only Lead failures, successful closure of this exact
reference history and the Lead attempt allows `retry_lead_turn`, including
after reference content was delivered. The failure and open authoring
objective remain durable. A new turn creates a new reference operation;
uncertain cleanup or corrupt accounting keeps the existing recovery block.

## Controller cancellation proof

One `asyncio.Runner` serves provider work. SIGINT recovery requires
`KeyboardInterrupt` plus proof: pre-entry must be quiescent; entered work needs
`CancelledError` and a completed join. Otherwise `operation_interrupted` is fatal.

Consensus Ctrl-C after entry also requires durable `INTERRUPTED`; retry uses a
new debate ID. Missing proof abandons attachment bookkeeping and exits `2`.
Cancelled local reads keep completed rows without a second domain read/write;
their command/cancellation boundary may append bounded diagnostics.

## POSIX PTY boundary

POSIX tests use a controlling PTY and VINTR/VEOF. They prove line fallback,
static and live-resume suggestion editing, complete capable-frame clearing, and
restoration across terminal outcomes. Viewer coverage additionally proves real
Shift+Arrow decoding, next-key resize, repeated alternate-screen pairing,
VINTR/VEOF outcomes, and termios restoration. Physical viewer EOF remains
deterministic driver coverage because hanging up the only PTY also closes the
test's observation channel. Windows consoles remain unverified.

## Permanent input-mode recovery

A stream-backed terminal starts with no input mode selected. Its first actual
read permanently chooses capable `LIVE` input or ordinary `FALLBACK`, and the
same choice covers main, subject, composition, confirmation, prompt, picker,
and viewer interactions. Construction itself acquires no terminal resource.

There is no automatic live-to-fallback recovery or later fallback-to-live
upgrade. If live source, decoder, capability, or terminal configuration drifts,
correct or replace the stream/driver before retrying; `PENDIN`-only local-flag
drift and width/height changes are the documented exceptions. A poisoned live
decoder likewise requires a new driver. `KeyboardInterrupt` is narrower: it
clears an incomplete code point and pending decoded suffix, keeps `LIVE`, and
allows the next explicit input call. Clean EOF stays EOF for that driver;
partial/malformed EOF or invalid byte-reader output remains an error.

Do not concurrently read the same input stream or alternate wrapper-level and
raw-buffer reads outside the driver. The persistent decoder is the sole
supported byte consumer; competing reads can move bytes into a buffer the
driver cannot observe. These failures raise through the established terminal
error path after cleanup rather than silently repeating a user's input in a
different mode.

<a id="ranged-text-viewer-exits-and-recovery"></a>

## Ranged-text viewer exits and recovery

The viewer remains an unlisted package-internal supplied-text component rather
than a command or public API. Focused Lead input is its provider-bound shell
consumer. In that span, exact `$$`
immediately opens current-source selection on the second dollar; Enter/Tab
opens the highlighted source. A complete `$${path}` opens it directly on
Enter/Tab, while `$` completes a whole-file indicator without viewing. The controller owns
artifact listing, verified reads, exact originating-draft restoration, and
path/range attachment. The viewer owns none of those concerns.

Escape, Ctrl-C, EOF, an empty source list, invalid marker, stale selection,
unreadable artifact, or repository failure returns to the exact preactivation
buffer, cursor, focus, and input mode with no dispatch, history append, or
residue. Correct the cause and activate again; there is no resumable picker or
viewer operation. Fallback uses complete numbered source/text projections and
requires a second Enter to submit the amended draft. Capable nested input uses
the same permanent live latch and cannot downgrade after a failure.

Invalid source type, display metadata, strict UTF-8, private 8 MiB source
capacity, more than 65,536 LF-derived logical lines, a complete sanitized
fallback projection over 16 MiB UTF-8, or an invalid initial line fails before
terminal inspection or I/O. The projection budget includes display metadata,
numbered records, separators, notices, and prompt, so escape-expansion-dense
input can exceed it even when the source itself fits. Exact limits are
accepted. Correct the caller input and open a new viewer call; there is no
partially opened viewer to recover. These terminal limits are independent from
the writing-artifact reference limit, which the controller caller enforces before
supplying verified text.

Accepted fallback content is complete: the renderer never width-clips source
text, and long logical lines wrap naturally in scrollback. Live rows remain
cell-aware, one-row ellipsis projections and bound each redraw to a
width-relevant prefix plus a fixed allowance. Thus users do not receive a
silently truncated fallback, while pathological over-limit input never opens.

For byte-backed input, only codecs that the public registry resolves to
canonical `ascii`, `utf-8`, or `iso8859-1`, with the built-in `strict` or
`surrogateescape` handler, can select live mode; their aliases are accepted. On
an `UNDECIDED` driver's first input call, every other or unknown codec,
including ISO-2022 variants, HZ, UTF-7, UTF-8-SIG, and
`raw-unicode-escape`, selects permanent ordinary fallback before
`PC_VDISABLE`, control inspection, byte reads, termios, screen output, or raw
activation. This is an allow-policy rather than a deny-list.

Within an allowed codec, raw control bytes are compared with `PC_VDISABLE`
before decoding, so disabled controls remain absent. Enabled VINTR/VEOF bytes
must decode to one code point and round-trip exactly through the stream codec.
Only while input remains `UNDECIDED`, incomplete, lossy, or incompatible
mappings select ordinary fallback rather than guessing a control role. After
`LIVE`, changed mappings fail as drift. Text-only supplied one-code-point
controls retain their distinct collaborator semantics. Configured VINTR has
priority over VEOF and ordinary viewer keys.

`selected` returns only a normalized one-based inclusive range. `cancelled`,
`interrupted`, and `eof` return no range and do not submit or mutate anything.
Selection and cancellation both close the independent viewer after cleanup;
the caller is never left in viewer mode.
Operational read, render, write, flush, decode, setup, or collaborator failures
are raised after cleanup rather than converted to one of those results. Once
`LIVE` is selected, the viewer does not attempt a second fallback read.
Termios-restoration failure takes precedence over alternate-screen-exit
failure, which takes precedence over the active failure. Cursor visibility is
never changed.

Every return or raise drops the call-local source, display name, active line,
anchor, selection, and viewport. Reopening starts from caller-supplied text and
an optional initial line; there is no resume token or saved viewer state. This
is lifecycle disposal, not secure erasure from process or terminal memory.
The controller returns cancellation or selection to the exact originating
buffer/cursor with the message still unsubmitted; selection attaches only its
path/range indicator. The viewer itself never receives or preserves the outer
composer. Multiple ordered references use repeated fresh viewer calls owned by
that caller.

The independent ranged viewer is activated by `$$` in focused Lead input and
returns only an identity-only range indicator to the composer. See the
[interactive terminal reference](../reference/interactive-terminal.md) and
[terminal safety boundary](terminal-safety.md#ranged-text-viewer-boundary).

## Live-composer recovery boundary

The package-internal live composer lets the controller keep an editable
request visible while an already-confirmed operation runs. It does not decide
whether the operation committed. A runtime-owned callback supplies the
authoritative `precommit` or `postcommit` boundary when input is accepted.

| Returned outcome | Proven fact | Safe consumer action |
| --- | --- | --- |
| `operation_completed` | The operation finished before terminal input was accepted; its result and exact unfinished editor state are returned. | Present the operation result and preserve the draft/cursor for the next ordinary read. |
| `replan` | Input was submitted at `precommit`, and cancellation of the exact operation was proved joined. | Re-read authoritative state and plan from the literal submitted text. Slash-prefixed text is not a command here. |
| `next_turn` | Input was submitted at `postcommit`; the old operation was not cancelled and finalization completed. | Queue the literal text as the next lead turn. Do not roll back or reinterpret the completed operation. |
| `interrupted` / `eof` | Ctrl-C or EOF was accepted as its own terminal event. | Follow the controller's interruption or shutdown policy; do not treat the buffer as submitted text. |
| `recovery_required` | The precommit join was uncertain/invalid/failed, the boundary could not be trusted, or postcommit finalization failed or was cancelled. | Preserve durable evidence and stop automatic replay. Inspect runtime state before another mutation. |

Progress frames and a cleared terminal span are not durable-completion proof.
The terminal driver is the only writer during the span, preserves buffer and
cursor across redraws, and restores terminal state before returning. If live
input is unavailable, fallback performs no concurrent read: it lets the
operation finish and returns `operation_completed` with the initial draft.
Users therefore retain semantic continuity but cannot edit concurrently in
that environment.

The story controller uses this seam during confirmed work. These outcomes are
internal transition facts projected as progress/recovery guidance, not a
user-accessible command.

## Pause, quit, and cleanup

Declined or interrupted pause confirmation keeps the current attachment; EOF at
confirmation exits. Confirmed pause writes durable `paused`, releases access,
retains defaults, clears current attachment/current conversation focus, and
returns to the lobby. Quit and main/composer EOF release access without pausing.

Cleanup attempts each applicable stage once in this order: Runner close, known
access release, backend close, then state clearing. A failure in one stage does
not skip later stages. An uncaught body `KeyboardInterrupt` or `SystemExit`
outranks cleanup failures; without one, the first cleanup process control wins.
The winner propagates only after all later stages and state clearing run, with
the same exception object and traceback. The Runner reference remains only when
Runner close itself was interrupted by process control; successful close or an
ordinary close failure clears it. Attachment, focus, and memory always clear,
and the controller closes.

With no retained process control, ordinary cleanup failures return `2` and
attempt one nonduplicated `cleanup_failed`; a diagnostic process control cannot
replace a retained body or cleanup winner. This arbitration applies only to
controls that reach the outer lobby boundary. A command that handles Ctrl-C or
cancellation retains its documented cancelled/recovery result. Later startup
resumes a ready default or asks for confirmation before resuming a paused
default; only an active default can become focus.

Backend close releases recorded access claims and ephemeral Codex staging. It
does not pause or delete the durable story session, remove saved provider-thread
references, or erase provider-native conversations. Antigravity definitions,
work directories, and isolated native state remain plaintext under its
provider-state root. A transport or protocol failure can taint a compatible
thread; the next safe authorized work can start fresh while preserving old
native data. Lead recovery restores exact retained session context. Explicit
new input can retry an eligible failed read-only attempt in its original
conversation; only that attempt's matching unfinished native operation is
retired, preserving successful checkpoints from other conversations. Uncertain
specialist/synthesis or write outcomes remain gated. There is no user-facing
session-close command or secure-erasure workflow.

During a long-running process, a changed agent definition becomes current for
new work without disrupting an operation already using the old immutable
definition. The old binding and its prompt staging remain only until its final
debate, advisory, or retrieval use exits. Success, provider failure, and
cancellation all release that use; cleanup of a superseded active binding is
deferred rather than deleting files underneath it. At quiescence the runtime
retains at most one current binding and the backend one owned staging directory
per canonical agent. Transient overlap may use more than one directory.

This cleanup is internal and best-effort. It adds no status or error, and does
not change provider conversations, operation results, transcripts, manifests,
or durable session state. Users cannot require an obsolete definition for new
work; the newest desired definition is selected instead.

<a id="conversation-rag-recovery"></a>

## Conversation RAG recovery

Start with a no-write observation for the affected session:

```bash
story rag status --session SESSION
```

A valid status exits `0` even when its state is not ready. Interpret the fixed
state and compatibility separately:

| Observation | Safe next action |
| --- | --- |
| `disabled` | Install/review the optional dependency if needed, then explicitly run `story rag enable --provider local`. Enabling does not build a generation. |
| `unavailable` / `dependency_missing` | Reinstall the exact pinned `.[rag]` extra under the approved non-redistributing policy; enable never writes configuration when preflight fails. |
| `version_incompatible` / `capability_incompatible` | Restore the exact reviewed pin and capability version before retrying. Do not bypass the check or substitute an unreviewed package. |
| `missing`, `stale`, or `failed` with compatible local provider | Run `story rag rebuild --session SESSION --yes`. Rebuild restarts from current authority; it does not resume partial bytes. |
| `incompatible` with otherwise validated derived state | Rebuild explicitly so a separately verified generation is published with the current provider/model/projection identity. Existing bytes are never relabeled. |
| `corrupt` | A confirmed rebuild may publish a separate fully verified generation without trusting or relabeling the corrupt active state. If it still fails, preserve the workspace and report before manual intervention. |
| `unsafe` or repeated `operation_failed` | Preserve the workspace and report. Do not delete or rename unknown entries to force progress: mutations fail closed around an unsafe topology. Restore a known-good workspace or obtain repository-owner diagnosis before any manual derived-state quarantine. |

An interrupted or failed rebuild leaves the previous active pointer valid when
failure occurred before publication; a complete inactive generation may remain
as an orphan. Rerunning confirmed rebuild is the supported restart path. The
next safe generation mutation validates and cleans only recognized residue. It
never treats partial bytes as resumable progress. If authoritative conversation
state changes during publication validation, the operation fails and marks
derived state stale best-effort; wait for writers to quiesce and rerun.

`coordination_failed` means a bounded active participant or activation drift
prevented a stable operation. There is no automatic retry; let the owning
storage/RAG operation finish, then rerun status. `storage_unavailable` on search
or confirmed rebuild belongs to the configured conversation-storage startup
boundary. Repair/provision/transfer/select that authority through its documented
workflow before retrying RAG; RAG never provisions or transfers it.

If Local is saved but the next shell prints the fixed one-time Conversation RAG
unavailable warning, the shell and `/settings` remain usable and configuration
is unchanged. Restore the exact reviewed dependency with
`python -m pip install --editable '.[rag]'`, verify its pin/version/capabilities,
and restart. Startup never installs or silently disables the saved provider.
Choose Disabled and confirm Apply Changes only when that is the intended
durable policy.

The former one-turn interactive RAG preflight is unavailable. Use the six
provider-free top-level `story rag` lifecycle/search commands or the direct
service APIs; ordinary input does not retain an armed query or start a provider
turn.

A successful search can still report `literal_fallback` with a non-ready reason
and exit `0`. Its excerpts, if any, were reread from authority, but the result is
not proof that semantic state was ready; inspect status and rebuild when semantic
retrieval is required. Search never logs or echoes its query, so retain the
original query yourself if it is needed for a retry. Its excerpt-bearing stdout
is sensitive conversation data.

Disable and purge solve different problems. `story rag disable` clears opt-in
and automatic policy but deliberately leaves derived generations. Confirmed
`story rag purge --session SESSION --yes` removes only one validated derived
session and remains safe while disabled; it never deletes authoritative
conversations. Purge is not secure erasure and cannot clean an unvalidated
unsafe namespace.

For `configuration_conflict`, reload current settings and retry the complete
enable/disable decision. For `configuration_write_failed`, inspect both
configuration documents because rollback uncertainty is collapsed into the
same sanitized failure. Invalid workspace/session/provider and blank or
invalid-UTF-8 query requests fail before RAG/storage dispatch; later bounded
query-normalization failures remain sanitized by the operation boundary. RAG
commands create no runtime debug record, so their one-line report and separately
protected workspace are the recovery evidence.

<a id="rejected-provider-response-recovery"></a>

## Rejected provider-response recovery

For `provider_protocol_error`, use `debug_logs/runtime.jsonl` as the
content-safe source for exception type and frame metadata. If a completed Codex
or Antigravity response reached structured decoding, the backend may also have
stored its actual body beneath `debug_logs/provider_error_responses/`. Match a
record by provider, agent, session, and debate or conversation metadata. Never
assume that every provider failure creates an artifact or that the newest file
belongs to the failed operation.

The artifact store is best effort. Bounded contention, unsafe or malformed
existing entries, unavailable safe filesystem primitives, or another storage
failure can leave the lazy directory or one record absent while the original
provider exception, thread handling, output, and exit behavior remain
unchanged. Capturing a response is evidence retention; it does not repair a
provider protocol problem or a runtime/provider-thread lifecycle bug.

Inspect a body only as hostile plaintext in a trusted editor that does not
interpret terminal controls or render Markdown/HTML. Do not execute it or feed
it into a shell. When cleanup is needed, stop the runtime first. If ordinary
filesystem policy permits, delete only complete artifact `.json` files; do not
hand-edit or remove `.lock`, `.pending`, malformed entries, or unknown names to
force progress. Ordinary deletion and automatic oldest-first pruning are not
secure erasure. See the [complete eligibility, schema, and inspection
workflow](privacy-and-provider.md#rejected-provider-response-artifacts).

## Current limits

- Codex and Antigravity are supported. Nullable routes inherit the workspace
  default and final Codex fallback. The legacy backend name `gemini` is
  rejected with guidance to use `antigravity`.
- Provider calls default to a 900-second response timeout, overridable for both
  providers with `story chat --codex-timeout SECONDS`. Antigravity has a
  30-second outer watchdog grace; Codex's sole watchdog uses the configured
  timeout without grace. This is per call, not per turn. There is no general
  retry or rate-limit manager. Read-only Lead recovery requires explicit new
  user input; bounded malformed-response correction remains available under
  its existing authority and call budget.
  At most one typed continuation replacement requires deterministic
  pre-execution proof; current native Codex/Antigravity adapters do not provide
  it. Generic errors, timeouts, malformed responses, and thread mismatches
  do not by themselves authorize automatic full-turn replay.
- Owner history has no compaction, expiry, or retention policy.
- Agent-context planning, source inventory, SEARCH/READ rounds, observations,
  references, and non-session delta depth have independent finite limits.
  Required Lead memory is separate from SEARCH-first optional P3 material:
  exact delivery allows 4,096 conversations and at most 262,144 canonical
  payload UTF-8 bytes, without summary or silent omission. Excess reports
  `exact session context exceeds the delivery capacity`. Known material is
  checked before provider work; generated observations are checked before
  their subsequent substantive submission. Checkpoint
  absence/corruption/mismatch/taint/thread loss selects bootstrap on the next
  safe authorized work; corrupt canonical history fails closed, and uncertain
  specialist/synthesis or authoritative write outcomes remain blocked. Eligible
  read-only Lead attempts can retry after explicit new input. Provider usage is
  exact only when supplied and otherwise unavailable. There is no semantic
  artifact RAG, free-form durable memory,
  arbitrary filesystem discovery, or progress replay API. See
  [Bounded agent context](../guides/agent-context.md#c20-decisions-limits-and-user-impact).
- Artifact publication is recoverable and catalog-last, but does not share one
  transaction with debate logs, lifecycle, results, or provider state.
- Ordinary focused shell input calls the Lead. Removed slash execution commands
  remain unavailable, and there is no raw specialist or `/run` route. Lead
  consultation, artifact Apply, and local Commit use three separate structured
  authority boundaries; cancellation or free text grants none of them.
- Runtime enforces structural evidence scope/status/receipt rules for lead
  judgments, but it does not perform semantic NLP verification, prove provider
  comprehension, or guarantee that the lead selected every relevant artifact.
  Explicit missing `${path}` still raises `ReferenceOperationFailure`; use an
  absent core status or complete zero-hit writing-artifact SEARCH when absence
  itself is the intended evidence.
- Conversation search is active-only; transcript reads retain no cursor.
- Filesystem remains the missing/legacy conversation-storage default.
  `/settings` and provider-free `story storage use` save a desired filesystem
  or SQLite backend for the next shell; host installation is limited to one
  explicitly confirmed allowlisted command, and startup never installs or
  provisions. Transfer/verification remain separate operations over two ready
  stores and never select, activate, or delete one. There is no live switch,
  dual write, automatic retry, MySQL, or FTS-dependent result behavior.
  The private local FTS5 companion supplies non-authoritative hints only.
- Optional local conversation RAG defaults off and has explicit noninteractive
  status, enable, rebuild, cited search, disable, and derived-only purge.
  `/settings` provider/automatic editors preserve next-shell configuration.
  External
  or network RAG providers and MySQL coupling remain unavailable. Literal
  `/search` is unchanged.
- Artifact parsing, repository authorization/evidence, provider read actions,
  and the independent supplied-text viewer back interactive Lead composition.
  Lead input accepts multiple `${path}` and `${path}#Lx-Ly` indicators; the
  durable indicator is not embedded file content.

<a id="consensus-deadlock-continuation-recovery"></a>

## Consensus deadlock-continuation recovery

A terminal consensus deadlock publishes nothing. Applications may stop there,
resume the same workflow, or create a linked workflow through the Python
`continue_consensus_deadlock()` API. The continuation must name the exact
deadlock result and current tail, and supplies only a ruling plus a positive
additional repair-round budget. Runtime reuses immutable candidate and
unresolved-critique handles; do not copy their text into a new prompt.

| Observation | Safe response |
| --- | --- |
| Source tail/result, session/conversation, candidate lineage, or material does not match | Reinspect the exact source. Do not retry with guessed IDs or copied content. No provider or artifact mutation occurred. |
| Continuation is `awaiting_confirmation` with capability available | Show its exact REPAIR request and ask for a fresh Confirm, Reject, or Revise decision. |
| Restarted linked continuation is awaiting confirmation without capability | Do not reconstruct authority. Reinspect the still-terminal source and prepare a new linked continuation if it remains exact. |
| Restarted in-place continuation is awaiting confirmation without capability | Do not replay the repair. Preserve and inspect it; use explicit operator/application recovery or begin a separate new workflow. |
| Revise closes a REPAIR request | Prepare its explicit replacement; no repair round was consumed. |
| Completed REPAIR leaves rounds | Request and confirm REAUDIT, then SYNTHESIS. One round was consumed. |
| A repair successor is requested with no rounds left | Stop or obtain a new explicit continuation from the latest exact deadlock. Nothing is published. |
| Provider interruption has a proved cancellation | Preserve the `interrupted` terminal result; do not replay it. |
| Provider outcome or durability is uncertain | Stop automation and perform operator reconciliation. Never assume the repair ran or repeat it. |
| Continuation reaches FINALIZATION | Treat it only as a read-only handoff. Artifact Apply and Git Commit each require their own later confirmation. |

Both continuation modes preserve immutable provenance. `resume_in_place`
appends to the source workflow; `linked_workflow` preserves that source as a
terminal record and creates a distinct linked workflow. Repeated deadlocks
remain nonpublishing. Strict consensus workflow serialization format version 3
has no migration or legacy reader. The installed CLI and interactive shell
provide no continuation command; use the Python runtime API.

See [Session lifecycle](../design/session-lifecycle.md),
[Troubleshooting](../guides/troubleshooting.md), and
[Filesystem safety](filesystem-safety.md). Antigravity-specific state,
preflight, and restart contracts are in the
[Antigravity guide](../guides/antigravity.md).
