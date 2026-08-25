# Recovery and limitations

> **Available:** guarded access recovery, shell workflows, and process-level
> POSIX PTY verification, including the package-internal ranged viewer.
> Interactive `$`/`$$` artifact discovery and selection are available in
> provider-bound prompt spans. Automatic retry, destructive cleanup, event
> repair, automation picker UI, and Windows console emulation are not available.
> Optional SQLite conversation storage also fails closed without automatic
> installation, provisioning, repair, migration, or retry.

Key terms: **fail closed** means preserve uncertain state; **taint** means stop
continuing incomplete provider history; **external backup** is outside the workspace.

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

<a id="sqlite-conversation-storage-recovery"></a>

## SQLite conversation-storage recovery

SQLite preflight is strict, observational, and byte-preserving. It may load and
probe the host `sqlite3` runtime and open an existing database read-only, but it
does not create, repair, migrate, rewrite, or remove storage.

| Result or failure | Meaning | Safe response |
| --- | --- | --- |
| `provisioning_needed` | On a capable host, the fixed database is absent. | Provision only after deciding the SQLite destination is deliberately empty and acquiring the exact workspace mutation lease. Provisioning does not import filesystem conversations. |
| `unavailable` or `ConversationStorageUnavailableError` | `sqlite3` is missing, older than 3.7.16, fails its runtime probe, the database is busy/locked, or an operational resource is unavailable. | Inspect the host/runtime and competing processes outside this API, then retry explicitly. Issue #261 owns integrating detection/installation into the user-facing flow; direct preflight only reports capability, this release installs nothing, and there is no automatic retry. |
| `ConversationStorageProvisioningNeededError` | Composition or an adapter operation requires a database that has not been provisioned. | Run observational preflight and, only for an intended empty target, the explicit leased provisioning flow. |
| `WorkspaceVersionUnsupportedError` | The file has the recognized application identity but an unsupported schema version. | Stop workspace processes, preserve and verify a complete backup, then use a compatible runtime or a separately approved migration. This adapter supplies no migration. |
| `WorkspaceFormatError` | Header, schema manifest/fingerprint/cookie, encoding, integrity, foreign keys, normalized subject, event structure, or strict sidecar state is invalid. | Preserve the database and sidecars unchanged. Restore a known-good complete database or investigate offline; do not hand-edit or ask preflight to repair it. |
| `WorkspaceSecurityError` | The database path is unsafe, replaced, or multiply linked. | Stop all processes, back up the complete workspace, and restore safe exact path identities. Do not follow, unlink, or replace suspicious names while live. |
| `ConversationStorageCoordinationError` | A lease is wrong-workspace, wrong-thread, expired, released, or cannot be acquired safely; the coordinator guard may also be unavailable or unsafe. | Let the supported owner finish or close it, then acquire a fresh exact-workspace lease. Never reuse or manufacture lease evidence. |
| `ConversationExistsError` during private archive import | An existing identity differs from the incoming exact metadata or transcript. | Keep both sources and investigate. The whole import transaction rolls back; there is no merge or overwrite mode. |

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
append could duplicate intended work. No operation performs automatic repair,
compensation, retry, filesystem-to-SQLite migration, or SQLite-to-filesystem
migration.

<a id="unsupported-v1-session-data"></a>

## Unsupported V1 session data

V1 provider manifests, `<session_id>.lease.json`, `.lease-claims.lock`, V1
`cli_context.json`, and `conversation_selections/` are rejected unchanged with
`WorkspaceVersionUnsupportedError`. There is no migration or compatibility mode.

Use this nondestructive reset:

1. Stop every process using the workspace.
2. Copy the complete workspace to an external backup and verify the copy.
3. Prefer a fresh workspace. Otherwise move—never delete—the old
   `provider_sessions/`, `cli_context.json`, and `conversation_selections/`
   outside the active workspace.
4. Run `story init WORKSPACE` to recreate current scaffold directories.
5. Recreate sessions with `story session create SESSION`, then recreate needed
   conversation defaults through the shell.

Do not copy old provider state, access files, guards, defaults, or CLI context
into V2. Copy editable story/configuration files only after review; keep backup.

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
| Settings provider authentication is cancelled or fails | Fresh authorization was not proved before assignment. | The pending route is unchanged. Retry the provider choice or cancel settings. Any missing default already established by completed authentication remains durable. |
| Save-time status is stale or fails | One distinct effective provider did not freshly authenticate before the file transaction. | Repair or complete native sign-in, return to Save, and reprobe. No pending settings file update occurs; completed auth/default writes are not rolled back. |

Interactive startup first needs any authenticated provider, then every distinct
effective route under CLI override → agent route → workspace default → Codex.
Removing or rewriting an explicit route is not an authentication recovery
mechanism. Noninteractive commands do not open the picker; prepare native
authentication before using their provider routes.

## Debate and shell-run recovery

Debate IDs reserve before providers and cannot resume; debate `active` is not
liveness. Inspect, fix, and use a new ID. Context sidecars do not enable replay.

Workspace failure returns safely only when the same session remains `ready` and
`held_here`; failed proof clears scope and exits. `ProgressObserverError` marks
preterminal debates `interrupted` when possible; later failures cannot rewrite results.

## Writing-artifact recovery

Every artifact catalog read or commit first checks for a pending publication
journal under the verified readiness-triple lease. Filesystem-source listing
and reads use the same lease but neither inspect nor recover the journal. The
schema-v2 journal is one immutable image containing the exact catalog-before
existence/hash/image, catalog-after image, commit, and ordered targets with
their complete new bytes and old/new hashes. It is never rewritten to record a
later phase.

Recovery compares the current catalog with those two exact positions. From
catalog-before, every target may equal only its recorded old or new bytes;
recovery rolls the set forward, publishes immutable commit history, and
publishes the catalog last. If the pending commit record already exists here,
the audit exempts it only when its byte-canonical record exactly equals this
journal's commit. That exempt commit contributes neither paths nor a
newest-core candidate to the expected before catalog; paths and unique newest
core authority from every older established commit remain required. The
projected after catalog includes the pending commit and must pass the full audit.
An unrelated record absent from the exact before view or divergent ledger state
still blocks. At catalog-after there is no exemption: every target and canonical
commit must be exact, catalog paths must equal the full union, and each repeated
core path must project the unique newest offset-aware commit, so recovery only
verifies them. It
then hash-checks the unchanged journal and moves it with native no-replace rename to
a `.completed` name in staging. A catalog matching neither position, an
untrusted target, or missing/replaced journal or final commit authority blocks
rather than guessing. Users may see an infrastructure failure followed by
successful recovery on the next catalog or publication access; inspect the associated debate
because its result and lifecycle are outside the artifact transaction.

Publication temporaries live under the repository's separate `staging/`
directory rather than `transactions/`. Failed `.tmp` bytes, prior `.held`
bytes, failed published `.retired` bytes, and finished `.completed` journals are
all retained there. This residue is normal internal state, is ignored and
preserved by transaction recovery and catalog reads, and may grow. Only exact
JSON journal names in `transactions/` are authoritative; any unexpected entry
there is ambiguity and blocks recovery.

If a journal is invalid or a target diverged, the repository writes
`agent_states/orchestrator_state/writing_artifacts/recovery_blocked.json` and
all later catalog/publication access fails with `writing-artifact recovery is
blocked`. Safe filesystem-source listing and reads remain independent.
Stop workspace processes, preserve a complete backup, and investigate the
journal and target history. Do not delete or edit the lock, journal, catalog,
commit records, or block marker to force recovery; no automatic repair command
exists. Restore a verified backup when trustworthy reconstruction is not
possible. See [Canonical writing artifacts](../reference/writing-artifacts.md).

Filesystem-source discovery and selective prompt-context reads are independent
of the publication catalog. They include present safe manual and hash-divergent
canonical files and omit an individual missing, unsafe, hard-linked,
nonregular, or invalid-path entry. Workspace-root, ancestor, or lock-lease
failures still propagate because safe source access is unavailable. Catalog,
ledger, journal, or recovery corruption does not blank safe source listing or
reads; it continues to block `catalog()` and publication until the provenance
authority is restored.

A cataloged manuscript identity is never reusable. If its file is deleted, the
permanent commit ledger retains the historical reservation while source
discovery omits the absent entry. A later commit to the same path raises
`ManuscriptExistsError`; choose a new part-scoped revision path. Catalog loss
does not free reservations: a missing, empty, or subset catalog beside existing
commits fails closed. Malformed, unexpected, extra, mismatched, or
duplicate-reservation commit records also fail global audit. Immutable commit
JSON must have the repository's exact canonical bytes; merely reformatting an
otherwise equivalent record invalidates the ledger. Historical core commits may
remain, but the catalog must select the unique newest normalized instant for a
repeated path. An equal newest tie or an older projection is ambiguous and fails
closed. These records are preserved rather than rewritten or migrated.

Operators should stop workspace processes, preserve a complete backup, and
restore exact verified repository metadata. The runtime does not guess a tie or
normalize a hand-edited commit. New repository timestamps remain strictly later
than all ledger timestamps even when the wall clock moves backward. If the
latest representable instant cannot advance, a new commit fails before creating
a journal or mutating an artifact; it does not reinterpret existing history.

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
reconstruction, or migration.

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

The backend does not retry automatically. An explicit retry or replacement
needs a fresh invocation ID, ordinal/attempt correlation, private coordinator,
and one-use capability; it cannot reuse terminal evidence. Cancellation,
provider or workflow failure, drift, malformed or nonexact provider action,
and wrong correlation terminate the current coordinator with a fixed
content-free outcome. Once the provider sequence advances, the backend
best-effort taints the compatible thread where required so later work cannot
assume a clean continuation. The original failure remains primary.

Inline `${...}` input now creates and finalizes these states. Successful
conversation, retrieval, and debate results carry an optional operation ID;
`StoryRuntime` and `RuntimeSession` expose content-free inspection. The shell
maps failures to existing safe error categories rather than displaying this
history. Pre-submit `$`/`$$` discovery and viewer selection create no operation
ID or evidence; their empty, invalid, stale, unreadable, and unavailable cases
use fixed content-free shell errors and restore the exact draft.

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
than a command or public API. In provider-bound shell prompt spans, exact `$$`
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

See the [exact viewer contract](../reference/interactive-terminal.md#independent-ranged-text-viewer)
and [terminal safety boundary](terminal-safety.md#ranged-text-viewer-boundary).

## Pause, quit, and cleanup

Declined or interrupted pause confirmation keeps the current attachment; EOF at
confirmation exits. Confirmed pause writes durable `paused`, releases access,
retains defaults, clears current attachment/current focus/agent focus, and
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
provider-state root. A transport or protocol failure can taint one
session/backend/agent thread; its next call starts a fresh provider conversation
and preserves the old native data. There is no user-facing session-close
command or secure-erasure workflow.

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

## Current limits

- Codex and Antigravity are supported. Nullable routes inherit the workspace
  default and final Codex fallback. The legacy backend name `gemini` is
  rejected with guidance to use `antigravity`.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact publication is recoverable and catalog-last, but does not share one
  transaction with debate logs, lifecycle, results, or provider state.
- Advisory input is one line; `/run --output PART/PATH.md` without prompt text
  owns multiline composition. Bare `/run` first prompts for the output.
- Conversation search is active-only; transcript reads retain no cursor.
- Filesystem conversation storage remains selected by the installed shell and
  runtime. SQLite is a direct optional Python API only; there is no `/settings`
  storage choice, installer, automatic provisioning, default switch,
  migration, FTS, RAG, or MySQL implementation. Issue #261 owns later
  user-facing integration of detection, installation, and selection; direct
  preflight here only reports capability.
- Parsing, repository authorization/evidence, top-level `StoryRuntime`
  propagation, and provider-enforced reads are active for inline canonical
  references. Provider-bound interactive spans add `$` completion and
  `$${path}`/`$$` ranged selection. Standalone compose/artifact commands,
  automation selection UI, and public selection APIs remain unavailable. The
  supplied-text viewer remains independent and package-internal: its controller
  caller discovers artifacts and attaches references.

See [Session lifecycle](../design/session-lifecycle.md),
[Troubleshooting](../guides/troubleshooting.md), and
[Filesystem safety](filesystem-safety.md). Antigravity-specific state,
preflight, and restart contracts are in the
[Antigravity guide](../guides/antigravity.md).
