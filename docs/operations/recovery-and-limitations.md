# Recovery and limitations

> **Available:** guarded access recovery, shell workflows, and process-level
> POSIX PTY verification, including the package-internal ranged viewer.
> Automatic retry, destructive cleanup, event repair, installed-shell artifact
> reference syntax, and Windows console emulation are not available.

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

## Debate and shell-run recovery

Debate IDs reserve before providers and cannot resume; debate `active` is not
liveness. Inspect, fix, and use a new ID. Context sidecars do not enable replay.

Workspace failure returns safely only when the same session remains `ready` and
`held_here`; failed proof clears scope and exits. `ProgressObserverError` marks
preterminal debates `interrupted` when possible; later failures cannot rewrite results.

## Writing-artifact recovery

Every artifact catalog read, exact artifact read, or commit first checks for a
pending publication journal under the verified readiness-triple lease. The
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
successful recovery on the next artifact access; inspect the associated debate
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
all later artifact access fails with `writing-artifact recovery is blocked`.
Stop workspace processes, preserve a complete backup, and investigate the
journal and target history. Do not delete or edit the lock, journal, catalog,
commit records, or block marker to force recovery; no automatic repair command
exists. Restore a verified backup when trustworthy reconstruction is not
possible. See [Canonical writing artifacts](../reference/writing-artifacts.md).

Catalog listing and selective prompt-context reads omit an individual missing,
unsafe, hard-linked, nonregular, or hash-stale artifact. They do not hide a
workspace-root, ancestor, lock-lease, catalog-schema, or global provenance
integrity failure; those errors propagate because safe enumeration is no longer
possible. A manual byte edit is therefore nondestructive but makes that entry
ineligible. Restoring the exact cataloged bytes can make it eligible again when
the commit and catalog evidence remain valid.

A cataloged manuscript identity is never reusable. If its file is deleted, the
permanent commit ledger retains the historical reservation while eligible
listing omits the stale entry. A later commit to the same path raises
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

## Controller cancellation proof

One `asyncio.Runner` serves provider work. SIGINT recovery requires
`KeyboardInterrupt` plus proof: pre-entry must be quiescent; entered work needs
`CancelledError` and a completed join. Otherwise `operation_interrupted` is fatal.

Consensus Ctrl-C after entry also requires durable `INTERRUPTED`; retry uses a
new debate ID. Missing proof abandons attachment bookkeeping and exits `2`.
Cancelled local reads keep completed rows without a second read/write.

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

The viewer is an unlisted package-internal supplied-text component. Users
cannot invoke it from the shell in this delivery. `$`, `$${file}`, `$$`,
artifact listing and secure reads, path/range reference construction, exact
origin-composer restoration, ordered multi-reference composition, and
mandatory per-agent reads remain unavailable until #163 and #164.

Invalid source type, display metadata, strict UTF-8, private 8 MiB source
capacity, more than 65,536 LF-derived logical lines, a complete sanitized
fallback projection over 16 MiB UTF-8, or an invalid initial line fails before
terminal inspection or I/O. The projection budget includes display metadata,
numbered records, separators, notices, and prompt, so escape-expansion-dense
input can exceed it even when the source itself fits. Exact limits are
accepted. Correct the caller input and open a new viewer call; there is no
partially opened viewer to recover. These terminal limits are independent from
the writing-artifact reference limit, which a later caller must enforce before
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
When #164 lands, cancelling or selecting will return to the exact originating
composer buffer/cursor with the message still unsubmitted, and a selection will
attach only its path/range indicator. The viewer itself never receives or
preserves the composer, and #162 makes none of that syntax shell-reachable.
Multiple ordered references likewise require repeated fresh viewer calls owned
by that caller.

See the [exact viewer contract](../reference/interactive-terminal.md#independent-ranged-text-viewer)
and [terminal safety boundary](terminal-safety.md#ranged-text-viewer-boundary).

## Pause, quit, and cleanup

Declined or interrupted pause confirmation keeps the current attachment; EOF at
confirmation exits. Confirmed pause writes durable `paused`, releases access,
retains defaults, clears current attachment/current focus/agent focus, and
returns to the lobby. Quit and main/composer EOF release access without pausing.

Cleanup attempts Runner close → access release → backend close, clears process
scope, and reports at most one `cleanup_failed`. Later startup resumes a ready
default or asks for confirmation before resuming a paused default; only an
active default can become focus.

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

- Codex is the only provider; reserved `gemini` fails validation.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact publication is recoverable and catalog-last, but does not share one
  transaction with debate logs, lifecycle, results, or provider state.
- Advisory input is one line; `/run --output PART/PATH.md` without prompt text
  owns multiline composition. Bare `/run` first prompts for the output.
- Conversation search is active-only; transcript reads retain no cursor.
- The supplied-text viewer exists only as a package-internal terminal
  component. Standalone compose/artifact commands and `$`/`$$`
  artifact-reference UI remain unavailable.

See [Session lifecycle](../design/session-lifecycle.md),
[Troubleshooting](../guides/troubleshooting.md), and
[Filesystem safety](filesystem-safety.md).
