# Persistence design

> **Availability:** filesystem persistence covers configuration, sessions,
> conversations, debates, optional debate-context sidecars, canonical writing
> artifacts, content-free explicit-reference authorization evidence, and
> bounded runtime debug-log infrastructure and automatic bounded agent-exchange
> diagnostics.
> Direct Python callers can also
> construct canonical conversation-only archive bytes and use the privileged
> filesystem transfer adapter. A separate optional SQLite conversation store is
> available through direct Python modules and as an explicitly selected
> next-shell bundle. Provider-free storage commands can select desired storage
> or transfer and verify two already-ready stores in one workspace.
> Optional conversation RAG has a separate disposable generation store, direct
> authorization/projection services, persisted default-disabled local
> provider/automatic values, and noninteractive lifecycle/search commands.
> Interactive `/settings` persists next-shell selection, but there is no current
> one-turn provider preflight.
> Direct repository components create and finalize reference evidence for
> authorized operations; Python callers can inspect it by operation ID. The
> Python lead workflow consumes it for ordered `${path}` and ranged indicators.
> Separately, the
> pure schema-v2 collaboration-history values and store port are available from
> `story_writing_agents.conversations`. Direct Python callers can persist that
> port with uncomposed filesystem or SQLite adapters. Canonical archive and
> backend transfer use those v2 histories, and the Python lead workflow appends
> through the selected filesystem/SQLite bundle. The shell remains read-only
> with respect to lead turns.

Key terms: **blackboard**—public debate JSONL; **sidecar**—context stored apart
from established debate records; **create-only**—never replaces an existing
path; **context-omitted run**—a debate created without a context request.

The installed runtime defaults to separate inspectable conversation files. An
explicitly prepared and selected SQLite database can instead serve the next
shell's complete conversation catalog, event-store, and reference-search
bundle. Direct Python callers may also compose it explicitly. Neither database
provisioning nor transfer alone selects runtime authority.

| Channel | Owner |
| --- | --- |
| Debate log, lifecycle, and result | Runtime and normal inspection |
| Debate context sidecar | Runtime; separate context inspection |
| Per-agent history | Its owning agent |
| Provider manifest and access claim | `AgentBackend` |
| Session/conversation defaults | CLI context helpers |
| Conversation metadata, transcript, and search | `conversation_storage.filesystem` owns filesystem metadata/transcript paths, bytes, and locking; the active filesystem or SQLite bundle supplies the complete catalog, event-store, and search authority |
| Schema-v2 structured collaboration history | `conversation_storage.filesystem` and `conversation_storage.sqlite` supply direct, uncomposed event stores; conversations still owns immutable values, canonical encoding, validation, and projection |
| Canonical conversation archive value and bytes | `conversation_storage.archive`; caller owns any stored or transmitted bytes |
| Same-workspace conversation transfer and checkpoints | Transfer-named API retained in `conversation_storage.migration`; canonical archive adapters remain the data boundary |
| Desired storage, readiness, and startup activation | Configuration owns desired value; `conversation_storage.selection` owns preparation/parity and next-shell marker CAS |
| Disposable conversation RAG generations | `conversation_rag_store`; derived per-session state only, never conversation authority; the local composition or an injected runtime service schedules and verifies use through an explicit state port |
| Conversation RAG opt-in defaults and CLI reports | Configuration owns nullable provider/exact automatic values; `conversation_rag_cli` owns complete noninteractive CAS workflows and ephemeral schema-v1 reports; interactive Settings reuses its guarded configuration transaction |
| Bounded runtime diagnostics | Internal `debug_log` writer |
| Bounded agent request/response/outcome records | `AgentBackend` capture projects each physical structured call; `agent_exchanges` independently owns storage |
| Retrieval actions, observations, progress, sources | Context service and caller callback; not durable authority |
| Current conversation focus | Controller memory only |
| Canonical writing artifacts and provenance | `WritingArtifactRepository` |
| Explicit-reference operation and invocation evidence | `WritingArtifactRepository`; content-free create-only records |
| Explicit-reference selected content | Transient repository/backend carriers and one immediate provider payload; no workspace evidence field |

`workspace.core` owns the generic secure workspace filesystem primitives and
the existing session/debate persistence. It does not own filesystem
conversation catalog or transcript persistence. Conversation-storage modules
that need those resources depend directly on
`conversation_storage.filesystem` and `workspace.core`, not on the stable
`story_writing_agents.workspace` user facade; the dependency graph remains
acyclic.

<a id="structured-collaboration-history-persistence"></a>

## Structured collaboration history persistence boundary

Schema-v2 collaboration history is the sole active transcript/history
contract. `story_writing_agents.conversations` owns immutable
events, proposal and result values, canonical JSON encoders/decoders, atomic
batch validation, and pure history projection. `CollaborationEventStore` is a
three-method protocol for `append_batch()`, `read()`, and `read_context()`.

`conversation_storage.filesystem.FilesystemCollaborationEventStore` and
`conversation_storage.sqlite.SQLiteCollaborationEventStore` implement that
port. Both are intentionally absent from the `conversation_storage`,
`workspace`, and project-root facades; active filesystem/SQLite composition
places one behind the storage-neutral service bundle.

An eligible read-only Lead attempt without a usable result is durably closed
by `lead_outcome_failure`. It preserves the original user request and records
only a closed reason and exact invocation/call identity. Both identities are
null only for `request_not_submitted`, before any provider accounting exists.
It does not create a successful judgment or execution result.

Exact legacy unstarted, prepared-only, or unmatched submitted requests expose
`retry_lead_turn` through pure projection. Inspection and reopening leave their
bytes unchanged. On explicit new user input, the runtime holds session and
conversation leases, closes the old attempt, verifies the authoritative tail,
then appends the new turn in the same conversation. A failed Revise request
leaves its old proposal superseded without replacement authority. Failed
closure writes, corrupt history, uncertain specialist/synthesis outcomes, and
pending or uncertain Apply/Commit writes remain recovery-gated.

The continuation owner retires only the unfinished native operation matching
an abandoned Lead attempt; successful checkpoints from other conversations
remain intact. Cancellation attempts closure and is re-raised after resource
release. This adds no automatic full-turn or successful-specialist replay. See
the [failure payload](../reference/collaboration-history.md#ordered-actors-events-and-result-correlation)
and [Lead recovery](../guides/lead-agent.md#submitted-lead-outcome-unavailable-after-restart).

```text
discussion_logs/<session>/interactive/conversations/<conversation>/
├── metadata.json
├── transcript.jsonl                # unsupported owning-boundary sentinel
├── collaboration-history.jsonl     # active v2 authority
└── .collaboration-history.lock
```

Construction performs no I/O. An absent history reads empty without creating
the v2 files. Present reads and appends combine a weak per-history process lock
with a blocking POSIX advisory lock, validate catalog/path identity and the
complete canonical history, and reject v1 or corrupt bytes without rewriting
them. The adapter assigns one contiguous sequence range, zero-based complete
ordinals, recomputed digest, and canonical UTC timestamps to a valid batch.
Exact committed retry is detected before tail CAS and returns the original
receipt.

Publication rewrites the complete canonical JSONL through a same-directory
mode-`0600` temporary file, file fsync, atomic replace, and mandatory directory
fsync. Only then can `APPENDED` escape. Pre-replace failure preserves the old
history. Post-replace failure is uncertain: complete new bytes may exist, and
retrying the exact same batch resolves them as `ALREADY_COMMITTED`. Newly
published history and a newly created guard request `0600`, but this is
plaintext persistence, not encryption, access control, backup, retention, or
permission repair. See
[filesystem safety](../operations/filesystem-safety.md#schema-v2-collaboration-history-safety)
for operator response and
[Structured collaboration history](../reference/collaboration-history.md#direct-filesystem-storage-and-retry)
for the direct API scenario.

The SQLite implementation uses
`discussion_logs/conversation_storage.sqlite3` with exact database schema
version `2`. Its manifest includes catalog and collaboration resources. Any
row in the unsupported `conversation_events` table is rejected before
mutation. Fresh provisioning owns the existing UTF-8/DELETE/FULL,
five-second-busy-timeout, contained-path, synchronization, and owner-only file
boundary.

One direct collaboration append borrows the workspace coordinator, opens
`BEGIN IMMEDIATE`, validates the complete addressed history, applies exact
retry/conflict/CAS/transition precedence, and inserts the whole canonical batch
or none. Precommit failure rolls back. `APPENDED` escapes only after commit and
final checks; if a later check reports failure after the commit became durable,
retrying the exact batch resolves to its original `ALREADY_COMMITTED` receipt.
The coordinator and transaction serialize threads and processes. Canonical
public payloads are plaintext BLOBs with redundant validated facts, never
hidden reasoning, provider diagnostics, credentials, or reference bytes.

The store can refresh a non-authoritative FTS candidate index after canonical
commit; search still reauthorizes exact v2 event fields. Archive and transfer
consume canonical v2 history. See the
[direct SQLite scenario](../reference/collaboration-history.md#direct-sqlite-storage-and-retry).

Stored confirmation and projected next-action values remain evidence. They
cannot preserve, serialize, or reconstruct a provider capability, workspace
lease, Git authority, credential, or any other executor. After restart, the
runtime must reacquire live authority. A confirmed proposal requires a
fresh `reconfirmation_required` request when that authority was lost; a running
attempt without an authoritative result becomes `outcome_unknown` and is never
replayed. No call-bearing work is valid after that state. See
[Structured collaboration history](../reference/collaboration-history.md) for
the exact user, state, batch, retry, privacy, and recovery contracts.

<a id="conversation-archive-persistence"></a>

## Conversation archive persistence boundary

`conversation_storage.archive` owns immutable version-2 archive, session, and
conversation values. Each conversation carries exact canonical
`CollaborationEvent` values rather than an archive-specific event type. It
canonicalizes logical ordering and emits one
strict-UTF-8 JSON byte image with a SHA-256 integrity digest. The pure value and
serialization layer performs no filesystem I/O and owns no archive file;
storage, transmission, retention, and access control for returned bytes belong
to the calling application.

`conversation_storage.filesystem_archive` owns this canonical archive's
layout-aware filesystem transfer. Its adapter captures one private immutable
construction-time root/key, retains no caller `Workspace` or public mutable
catalog/event-store collaborator, and creates one operation-local collaborator
set only after validating the operation's lease. This pinning is archive-owned,
not the composed-service topology or SQLite database owner. Under a
caller-held workspace mutation lease, export reads addressed exact metadata and
complete v2 collaboration histories from the fixed
`discussion_logs/<session>/interactive/conversations/` namespace. It requires
an already quiescent source and returns only after two complete canonical
archive images agree. The lease and double scan detect unsafe or changed
conversation state, but are neither authorization nor a linearizable snapshot
for ordinary catalog/event-store writers.

Import canonicalizes and bounds the whole payload, scans and classifies the
complete destination-plus-payload union, builds its exact planned files,
projects every count/path/byte bound, and checks all target ancestors before
mutation. Create-only metadata-first installation accepts exact domain state,
adds absent identities, preserves unrelated complete conversations and
excluded workspace channels, rejects divergence, and verifies the expected
destination afterward. It makes no rollback or deletion claim: an operational
failure may leave only recognized exact prefixes that the same payload can
complete under a fresh valid lease.

The filesystem adapter publishes `collaboration-history.jsonl` as one complete
create-only file and excludes the safe `.collaboration-history.lock`. Any
legacy `transcript.jsonl` rejects export or import before destination mutation.
The SQLite adapter uses `collaboration_events` canonical BLOBs and one import
transaction; any `conversation_events` row rejects the operation. Derived FTS
is excluded, and best-effort refresh cannot change canonical parity.

This archive boundary neither persists an archive automatically nor combines
conversation files with debates, defaults, provider state, prompts,
manuscripts, artifacts, diagnostics, or configuration. It is not a complete
workspace transaction, backup, arbitrary restore, activation, or dual-write
mechanism. The separate transfer coordinator described below composes the
archive adapters for one same-workspace copy/parity workflow. See the exact
[archive reference](../reference/conversation-archives.md) and
[filesystem boundary](../operations/filesystem-safety.md#conversation-archive-filesystem-safety).

<a id="optional-conversation-rag-derived-persistence"></a>

## Optional conversation RAG derived persistence

`ConversationRagGenerationStore` solely owns the derived subtree
`agent_states/orchestrator_state/conversation_rag/`. Construction retains the
supplied identity-bound `Workspace` without I/O. Each stateful operation opens
and verifies the established root and fixed hierarchy by descriptor, then
acquires process-local and bounded POSIX advisory locking for the addressed
session. This is independent of filesystem and SQLite conversation authority;
neither backend imports, selects, transfers, or validates it.

```text
conversation_rag/
├── .lock
└── sessions/
    └── SESSION_ID/
        ├── .lock
        ├── active.json
        ├── status.json
        └── generations/
            ├── build-RANDOM/       # inactive, nonresumable work
            └── gen-SHA256/
                ├── generation.json
                ├── generic-manifest.json
                ├── source-map.jsonl
                └── vectors.jsonl
```

Owned directories/files require modes `0700`/`0600`. Completed generations
contain the generic checkpoint manifest, stable story source map, finite local
vectors, provider/model/schema/chunking identity, content digests, and bounded
counts. `story-conversation-rag-store-v1` versions each persisted envelope;
the caller-owned projection schema is recorded separately as
`projection_schema_id` and as the checkpoint's `schema_id`. The source map
repeats plaintext conversation fragments; derived and disposable does not mean
redacted or encrypted. The pointers, public snapshots, errors, and diagnostics
contain no query or source text.

Reads require canonical strict-UTF-8 JSON/JSONL and exact fields, ordering,
modes, identities, sizes, digests, counts, ranges/overlap, reconstructed source,
checkpoint identity, and finite canonical float values. Bounded parsing maps
duplicate fields, deep recursion, non-finite constants, numeric overflow,
malformed bytes, and noncanonical re-encoding to fixed content-free corruption.
Inspection validates its exact nonblank strict-UTF-8 expected projection schema
before I/O. The default expects the legacy store-v1 projection schema. For a
fully verified generation, provider/model/projection mismatch yields
`incompatible` status while retaining a `present` checkpoint and safe
generation/count fields. Store-envelope or otherwise unverifiable
incompatibility exposes no trusted checkpoint. Status is advisory: `ready`,
`stale`, or `provider_failed` can qualify a compatible identified checkpoint
but cannot mask incompatibility or commit another generation.

Projection writes one inactive `build-*` directory. On success it synchronizes
and verifies the payload, renames it to a content-derived `gen-*`, synchronizes
the generations directory, reopens/verifies it, and prepares advisory status.
`active.json` is the sole commit record and is replaced last. Its complete
temporary pointer and session directory are synchronized before the atomic
rename, which is the final callable publication operation. There is no forced
post-rename synchronization: an immediate crash may retain either the old or
new valid pointer. A reported precommit failure leaves the prior pointer
byte-for-byte unchanged.

An interrupted build is an orphan, not resumable progress. `inspect()` reports
validated orphan counts without cleanup. A later generation mutation removes
validated partial builds and nonactive completed generations before new work.
Rebuild restarts deterministically; incremental projection may use a compatible
verified active generation as its base. Local retrieval converts missing,
stale, corrupt, incompatible, and provider-failed state to fixed literal
fallback rather than trusting partial state.

Generations built with the store-v1 value as their projection schema remain
legacy-ready. A caller expecting another projection schema receives a verified
incompatible state; no persisted generation is relabeled or converted. Explicit
rebuild writes the request's projection schema into a new inactive generation
and publishes it only after complete verification. A failed precommit rebuild
preserves the prior active pointer and generation.

Confirmed purge prevalidates one session namespace, removes its active pointer
first, then removes only advisory status and derived generation contents. It
does not remove the session locks/directories or touch authoritative
conversations, storage activation, configuration, SQLite, manuscripts,
artifacts, provider state, or another RAG session. Derived state remains
rebuildable from separately authorized authoritative sources through the
programmatic `conversation_rag_runtime` orchestration or confirmed CLI rebuild.

`story rag status` is observational across configuration, the persisted active
conversation backend, and this derived subtree. It uses a bounded optimistic
double read and creates no storage coordinator lock, RAG directory, or rewritten
configuration. Confirmed rebuild holds the authoritative mutation lease across
target construction, derived publication, and final target revalidation. Search
may activate an already-ready desired authoritative backend but does not write a
generation; purge consults no authoritative backend. Enable/disable modify only
the canonical system-default configuration through exact-byte compare-and-set.
See the
[direct API](../reference/package-api.md#optional-conversation-rag-boundary),
[filesystem guarantees](../operations/filesystem-safety.md#conversation-rag-generation-store-safety),
and [current commands, runtime, and interactive workflow](../guides/conversation-rag.md).

<a id="optional-sqlite-conversation-persistence"></a>

## Optional SQLite conversation persistence

The optional adapter owns one fixed contained file:
`discussion_logs/conversation_storage.sqlite3`. Its database identity is schema
version `2`, SQLite application ID `0x53574143` (`SWAC`), UTF-8 encoding, and a
fixed schema cookie and schema-manifest fingerprint. The internal database
owner reads the canonical `Workspace.root` once at construction and pins both
its workspace key and database path. Later mutation of the supplied
`Workspace.root` cannot retarget database I/O. That capture is path arithmetic
only: the SQLite layer performs no database I/O and does not load `sqlite3`
during direct adapter/database construction. Resolving a string or path-like
argument still follows ordinary `Workspace` construction, and public
composition still performs its documented readiness preflight.

Strict preflight and composition readiness, plus new, existing, recovered, and
race-winner provisioning paths, verify the exact table, index, and trigger
manifest, run `PRAGMA integrity_check` and a global foreign-key check, reject
orphan events, and reconstruct every domain row. That authoritative readiness
boundary does not require, inspect, or repair the derived FTS companion.
Routine adapter calls instead verify bounded database/schema identity and
validate only the rows involved in the requested operation; they do not scan
unrelated database contents.

Schema v2 has 14 explicit application-owned objects: four authoritative tables,
six indexes, and four insert triggers. The retained v1 resources hold the
singleton identity/lexical generation, catalog rows, and ordered transcript
rows. The added collaboration table stores canonical schema-v2 events as
plaintext BLOBs plus redundant identity, sequence, batch, and digest facts; two
unique indexes protect event IDs and batch ordinals, and one trigger protects
contiguous sequence insertion. Ordered reads still use composite-primary-key
autoindexes, so there are no redundant order indexes.

Catalog rows retain exact public metadata plus required canonical
`normalized_subject`; event rows retain the exact public event plus required
canonical `searchable_content` and `turn_slot`. Verification rejects divergence
between source text and either lexical projection. The slot, constraints, and
triggers enforce contiguous history, one user/outcome slot, addressed replies,
and catalog ownership. These database-only values do not change the public
catalog, transcript, context, search, or citation contracts. FTS and its shadow
objects never enter this authoritative database.

Every ordinary adapter read owns a read-only `mode=ro` connection and explicit
`BEGIN`/`COMMIT` transaction. Bounded identity verification and the requested
row validation observe one stable snapshot; the connection is then closed.
That boundary is one catalog, collaboration-event-store, or search operation.
`SQLiteCollaborationHistorySearch` performs its complete bounded event-field
search in one main-database read transaction. It preserves the v2 readable
field, exact citation, snippet, count, limit, and result-order contracts while
reading authoritative rows directly. Catalog subjects never create hits.

Every supported mutation acquires the workspace conversation-storage
coordinator before opening its `mode=rw` connection. It verifies the database
and active workspace/owner-thread lease, uses `BEGIN IMMEDIATE`, performs the
operation with operation-local row validation and database constraints, and
advances the required lexical generation for an effective searchable change.
It then
reverifies schema identity plus the lease, commits, checks the named database
identity, and closes. A required lexical-state failure rolls back the
authoritative mutation; an already-effective archive or unarchive is a no-op
and leaves the generation unchanged. The connection policy is DELETE journal
mode,
`synchronous=FULL`, foreign keys enabled and enforced, a fixed five-second
SQLite busy timeout, and strict UTF-8 decoding for database `TEXT`. Malformed
stored text encountered by verification or the requested operation fails
closed through a content-free storage-format error; a
caller-originated `UnicodeDecodeError` is not silently reclassified as stored
corruption.

The fixed private `discussion_logs/.conversation_storage_fts5.sqlite3`
companion is derived and disposable. It stores normalized closed readable
event fields and stable event/field coordinates, and records the committed
source generation plus exact normalization, schema, tokenizer, and
SQLite-runtime identities. Catalog subjects remain display-only.

After an effective main mutation commits, the adapter may refresh the
companion under the same workspace lease. A refresh failure cannot roll back
the already committed main transaction. Search treats every FTS candidate as a
hint and still applies exact literal comparison over the full bounded
main-database scan. Missing, stale, busy, malformed, unsafe,
capability-mismatched, or otherwise failed companion state is preserved and
falls back without changing results; search performs no repair.

This serializes supported writers through the workspace guard but is not a
transaction with filesystem conversations, defaults, sessions, debates,
artifacts, provider state, or other workspace channels. Routine writes do not
run the exhaustive preflight scan over unrelated rows. In particular, append
uses the indexed maximum sequence and reads at most the addressed turn's one
user row and one outcome row; it does not reconstruct the full transcript.
`read_context()` for the v1 transcript obtains an exact complete-turn count and
materializes at most the newest 25 addressed complete pairs—50 events—to retain
the fixed 24-turn window and detect its boundary. A full `read()` still
validates and returns the complete transcript. Direct v2 collaboration reads
and append validation reconstruct the complete addressed collaboration history
instead.

Database provisioning builds and verifies one private temporary database,
installs it create-only, and synchronizes the installed file and containing
directory. That operation does not install a host SQLite prerequisite or
ingest the filesystem store. It may initialize the
derived FTS companion after the main database is ready, but FTS absence or
failure is not a provisioning or readiness failure. The privileged archive
adapter round-trips the public canonical `ConversationArchive` value under an
explicit lease. Export globally reconstructs all metadata and event rows and
rejects every orphan event. Import validates the complete destination before
mutation and again after mutation in the same transaction, so an orphan,
malformed row, or divergent existing identity cannot be silently adopted or
dropped; failure rolls back the whole import.

Provisioning creates schema v2 only. Unsupported databases and owning-boundary
event rows are preserved and rejected. Archive and same-workspace transfer
cover catalog metadata plus exact v2 collaboration rows only.

Without POSIX `fcntl`, a valid existing database can still be preflighted and
read observationally. Supported coordinator acquisition, installation through
selection, provisioning, configuration save, activation, and mutations fail
closed without changing conversation state.

`story init` and direct `StoryRuntime` construction do not provision or select
this database. `/settings` and `story storage use` may explicitly prepare it
and save SQLite as desired storage, but neither activates or transfers it. The
explicit transfer coordinator can copy canonical conversations between an
already-ready database and the filesystem, but it does not install, provision,
or activate SQLite. Shell startup never installs or provisions. The composed
SQLite service includes the v2 collaboration store and aggregate search. There
is no live switch, source deletion, RAG coupling, MySQL, or FTS-dependent
search authority. The separate disposable RAG
store is neither a SQLite table nor a conversation backend. See the
[direct API](../reference/package-api.md#optional-sqlite-conversation-storage),
[filesystem boundary](../operations/filesystem-safety.md#sqlite-conversation-storage-safety),
and [recovery table](../operations/recovery-and-limitations.md#sqlite-conversation-storage-recovery).

<a id="conversation-storage-selection-and-activation"></a>

## Conversation storage selection and activation

Configuration owns only the desired `conversation_storage_backend`. New
workspaces save `filesystem`; missing or null legacy values resolve to that same
effective default without read-time rewriting. The independent strict schema-v1
`config/conversation_storage_state.json` owns active backend and CAS generation;
its absence is filesystem generation zero.

`conversation_storage.selection` is a direct-only workflow module above
configuration, provisioning, canonical archives, and backend composition. It
does not import the transfer coordinator or consume transfer checkpoints. `story storage use`
dispatches to it before debug logging and provider/runtime construction.
`/settings` supplies its complete pending configuration plus the
settings-owned fresh persona-catalog guard. Storage choice is independent from
the Agents editor and provider routing. A mixed Settings Apply retains provider
authentication before selection begins. A Message-details-only Apply never
enters selection: it prepares, verifies, and commits the presentation preference
without provider, storage, backend/runtime, route, or thread activation work. A
Conversation-RAG-only Apply also bypasses selection and authentication: it
prepares, verifies, and commits only the startup-fixed system configuration,
with no backend/runtime, route/thread, composition, optional-package probe,
provider catalog, subprocess, socket, or network work.

Selection first briefly proves the workspace coordinator, then releases it
during any consent interaction and host installer. For SQLite it functionally
probes the active Python interpreter, optionally executes one explicitly
authorized allowlisted host command, and reprobes the same interpreter. It then
holds the coordinator while it provisions an absent empty database, composes
the requested backend, reads active state, and compares fresh source/target
canonical archive bytes. Same-backend or empty-source selection needs no
parity; a nonempty source requires exact equality. Lease validation, the
optional Settings guard, another lease validation, and the config-owned exact-
byte complete-document CAS form the final publication boundary.

Successful selection changes desired configuration only. The current process
retains its construction-time bundle. Host installation and an empty provisioned
database intentionally survive a later parity or configuration failure; source
conversations and active state do not change.

Interactive startup runs after workspace/configuration loading but before
terminal construction and provider authentication. It holds one outer lease
while it reloads configuration, composes the current active bundle, freshly
checks desired-target readiness and canonical parity, composes the complete
target bundle, and compare-and-set advances the activation marker. Any pre-CAS
failure aborts startup and preserves the prior marker without a silent
fallback. A successful CAS is not rolled back by later terminal,
authentication, backend, or session failure. The resulting immutable bundle is
injected into controller, runtime, and memory; no live switch or dual write is
owned. Exact user commands, reports, installation policy, and recovery links
are centralized in the
[CLI reference](../reference/cli.md#conversation-storage-selection).

<a id="conversation-storage-transfer"></a>

## Conversation storage transfer

`conversation_storage.migration` owns one provider-free, same-workspace
coordinator over the canonical filesystem and SQLite archive adapters. The
physical filename is retained, but its public API is transfer-named and it is
not a schema converter. The installed `story storage transfer` and
`story storage verify` commands dispatch
to it before debug logging, broader CLI context, authentication, provider,
runtime, settings, or session construction. Importing the module performs no
I/O and keeps `sqlite3` lazy until an operation needs an adapter.

One outer workspace conversation-storage lease spans active-backend validation,
SQLite readiness, fresh source and destination exports, deterministic planning,
transfer, checkpoint publication, fresh final exports, and exact canonical-byte
parity. The source and destination must be distinct; `--from` must equal the
persisted active backend (an absent activation record means filesystem), and
SQLite must already be provisioned and ready. The coordinator never installs or
provisions SQLite, changes activation/configuration, deletes source data, or
dual-writes later mutations.

Only archive schema 2 is accepted. The archive contains exact catalog metadata
(whose own schema remains 1) and exact `CollaborationEvent` values. A
filesystem `transcript.jsonl`, SQLite `conversation_events` row, or schema-1
archive stops the operation before destination mutation. No conversion or
compatibility path is inferred from the retained module filename.

Planning classifies every `(session_id, conversation_id)` as `create`,
`identical`, `conflict`, `unsupported`, or `destination_only`. Any conflict or
SQLite-unrepresentable canonical integer blocks every transfer. Destination-only
state also blocks transfer because copying must not silently accept asymmetric
authority. Verification treats otherwise valid create or destination-only
state as a parity mismatch. Exact comparison includes active and archived
metadata, empty histories, and every schema-v2 collaboration event; the
derived FTS companion is outside both transfer and parity.

Filesystem-to-SQLite import is one complete SQLite transaction. A failed
uncommitted import rolls back all destination rows. SQLite-to-filesystem import
first preflights the complete global destination and then publishes complete
conversations create-only. After each conversation is reread and proved exact,
the coordinator atomically advances a per-direction checkpoint. If publication
or checkpointing is interrupted, a retry freshly exports both stores and
accepts only exact completed records before resuming.

Checkpoints are retained beneath
`agent_states/orchestrator_state/conversation_storage_transfers/` as
`filesystem-to-sqlite.json` and `sqlite-to-filesystem.json`. Strict schema v1
contains only direction, a SHA-256 source fingerprint, aggregate counts,
completed conversation identities, and progress counts. It contains no subject,
event, error, or FTS content. A successful transfer retains a terminal complete
checkpoint as audit state. Dry-run never writes one; verify neither reads nor
writes one. Either read-only workflow may still create the empty
`config/.conversation_storage.lock` guard required to acquire its outer lease.

After transfer, fresh canonical exports must still equal the original source.
A source change or final mismatch can be reported after complete destination
state was committed or published; the coordinator makes no cross-store rollback
claim. Recovery therefore starts with both stores quiescent and fresh
verification, not activation or source deletion. See the
[CLI report and exit contract](../reference/cli.md#conversation-storage-transfer),
[checkpoint layout](../reference/configuration/session-storage.md#conversation-transfer-checkpoints),
and [recovery procedure](../operations/recovery-and-limitations.md#conversation-storage-transfer-recovery).

Externally, dry-run can report `work_required`; successful transfer reports
`completed`, verified parity, and a completed checkpoint; independent verify
recomputes parity without consulting the checkpoint. A later `storage use`
saves the desired backend, and only the next launch may activate it after
fresh readiness/parity checks. This separation prevents a copy operation from
silently becoming an authority switch.

<a id="agent-exchange-persistence"></a>

## Agent-exchange persistence

The `story_writing_agents.agent_exchanges` package owns one bounded diagnostic
store independently of conversations, debate blackboards, agent state, runtime
debug records, and rejected-provider-response artifacts. Its direct store
constructor is I/O-free. `AgentBackend` now composes one workspace capture and
offers every physical structured call at both the established and contextual
provider seams. Bare `/exchanges` is a separate controller/terminal consumer of
the store's immutable inventory and snapshot contracts.

The caller supplies one immutable strict-UTF-8 `AgentExchangeRecordInput` with
session, conversation/debate scope, canonical agent, provider, physical-call
`exchange_id`, `request`/`response`/`no_response`/`outcome` type, and content.
The storage API remains policy-free: it does not call a provider, choose an
outcome, synthesize a record sequence, or require an exchange ID to be unique.
The capture owner generates one fresh exchange ID and reuses it for the
physical request, any valid completed response, and exactly one terminal
attempt.

Capture is ordered around provider behavior, not as one storage transaction:

1. append the canonical request immediately before `adapter.invoke()`;
2. if the adapter returns an exact `ProviderResult`, append its canonical
   projection immediately before accounting observation, decoding, the
   rejected-response artifact, or provider-thread persistence; and
3. append one closed outcome when the owning layer can classify the call as
   `accepted`, `rejected`, `provider_failure`, `cancelled`, or
   `post_response_runtime_failure`.

A provider failure before a result has request plus outcome and no response. A
cancellation may close before or after a response. Context SEARCH/READ calls
are accepted after completion-observer delivery, ANSWER is accepted after the
checkpoint commit but before the later checkpoint observer. Local reference
loading has no provider-call record.
These are append-only historical facts: a later failure does not rewrite an
already accepted call.

One append holds both the weakly retained process lock and the store's
cross-process advisory guard. Beneath that guard it performs this ordered
lifecycle:

1. Reverify the workspace and exchange namespace, remove only recognized safe
   pending remnants from an interrupted prior mutation, and validate the
   complete bounded store.
2. If a prior failed post-append prune left managed JSONL over the global cap,
   prune it back to the cap before reserving another identity.
3. Atomically replace the workspace high-water state with the next
   `workspace_sequence`, then atomically replace the selected
   `(session_id, agent_id)` stream high-water state with its next `sequence`.
4. Assign the UTC millisecond timestamp; compute complete and retained UTF-8
   byte counts plus Python Unicode-code-point counts; take the complete UTF-8
   digest; retain the complete-code-point prefix; and serialize one canonical
   schema-v1 line. Character counts are not grapheme or display-width metrics.
5. Rotate before the active two-MiB segment would overflow, append and `fsync`
   the complete line, and roll the file back to its exact prior length if a
   write, sync, identity, or size check fails.
6. Prune the oldest currently retained complete records by global workspace
   sequence until managed exchange JSONL is at most 16 MiB, then rescan and
   require the just-appended record before returning its content-free receipt.

Global and stream high-water reservations deliberately precede the record.
They survive a new store instance or process, so a failed operation can consume
one or both numbers but a later append cannot reuse them. Such gaps are valid.
Every retained stream must still be strictly increasing in both stream and
workspace sequence, and workspace sequences must be unique across streams.
Duplicate or decreasing values, state below retained logs, missing required
state, or a pending file visible to a reader fail closed as corruption.

Rotation keeps complete records only. `.1` is the newest backup and `.7` the
oldest; the next rotation removes the verified `.7`, shifts verified identities,
and moves the active segment to `.1`. This per-stream window can expire records
before the global cap does. Global pruning considers all currently retained
streams and removes oldest workspace sequences first; when only a prefix of a
segment expires, it atomically replaces that segment with the exact canonical
retained suffix. Neither mechanism rewrites high-water state, so inventory and
snapshots can legitimately show noncontiguous ranges.

Readers acquire the same bounded guards and validate the complete owned store.
`inventory(session_id)` is content-free and groups retained records by exact
scope kind, scope ID, and agent, ordered by each group's first workspace
sequence. `snapshot(selection)` copies the exact retained canonical lines for
one session/scope/agent into immutable strict-UTF-8 `bytes`, ordered by stream
sequence and ending at a complete newline. It returns no storage path or handle
and does not retain a live view. An absent store remains absent; inventory is
empty and snapshot reports `not_found`.

State replacement, rotation, append, pruning, pending cleanup, advisory-lock
release, and descriptor close are not one filesystem transaction. A primary
body failure takes precedence over a cleanup failure. If the body succeeds but
cleanup alone fails, the caller receives fixed `io_failed` even though the
record can already be durable. Callers must therefore treat an I/O failure as
an indeterminate append and inspect inventory/snapshot before retrying; blind
retry can add another record because `exchange_id` is correlation, not an
idempotency key. `KeyboardInterrupt` and other control exceptions retain
priority over cleanup failure, while all acquired resources are still released.

Capture is fail-open and advances its in-memory lifecycle before each append.
It catches every storage/serialization failure, never retries, and cannot
replace provider behavior. Thus a physical call can have no diagnostic, or a
recorded request can lack its response or outcome; durable sequence gaps and a
cleanup-only `io_failed` remain inconclusive. Blind diagnostic retry is not an
idempotent recovery mechanism.

The `/exchanges` integration owns session/scope/agent pickers, subject
resolution, optional-package validation, and isolated viewer lifecycle. It
derives scope rows from authoritative inventory, passes only one immutable
exact snapshot, and discards all picker/view state on return. The storage
package intentionally has no subject field, viewer dependency, terminal
ownership, provider import, or capture policy. Capture does not import raw
provider streams and neither imports nor double-writes sibling
runtime/provider-error records into exchange JSONL. See the exact
[layout and schema](../reference/configuration/session-storage.md#agent-exchange-storage),
[filesystem guarantees](../operations/filesystem-safety.md#agent-exchange-filesystem-safety),
the [privacy boundary](../operations/privacy-and-provider.md#agent-exchange-privacy),
and the [installed inspection workflow](../guides/agent-exchanges.md).

<a id="workspace-runtime-debug-log"></a>

## Workspace runtime debug log

The internal diagnostics writer appends compact, ASCII-safe JSONL. Ordinary
operational records retain schema version 1. Every schema-v1 record has
`schema_version`, a writer-owned UTC `timestamp`, and fixed `level`,
`component`, `operation`, and `phase` values. It may add an `outcome` from its
fixed taxonomy and exact validated `session_id`, `conversation_id`, `debate_id`,
or canonical `agent_id`; absent optional fields are omitted rather than written
as `null`.

An error record additionally has an `exception` object with only `type`,
`frames`, and `frames_truncated`. A frame has only a safe module name, function
name, and positive line number. The writer keeps the nearest 32 frames and uses
fixed replacements for unsafe type, module, or function names. It does not
accept a caller timestamp, message, path, traceback text, or arbitrary detail
or payload mapping.

Execution identity records use schema version 2 and one of these operations:
`execution_setting_change`, `execution_profile_applied`,
`execution_profile_confirmed`, or `execution_profile_recovery_required`. Every
record has `schema_version: 2`, timestamp, level, `component: "backend"`,
operation, phase, `correlation_id`, canonical `agent_id`, nonnegative
`route_generation`, and `activation_state`. It has exactly one profile form:
either `active_profile`, or both `old_profile` and `new_profile`. Each profile
has exactly `provider`, `provider_source`, `model`, `model_source`, `effort`,
and `effort_source`; provider is `codex` or `antigravity`, sources are
`launch_override`, `agent_config`, `system_default`, `built_in_default`, or
`provider_default`, and model/effort may be null. Optional
`thread_disposition` is `fresh`, `reused`, or
`fresh_after_provider_switch`; optional `context_state` is `rehydrated` or
`unavailable`.

After successful backend construction, four `execution_profile_applied`
records use trace level, succeeded phase, correlation `startup`,
`activation_state: "active"`, and only an
`active_profile`; they do not fabricate old values. A changed hot profile emits
`activation_state: "accepted"` for `execution_setting_change`,
`activation_state: "active"` for `execution_profile_applied`, then
`activation_state: "confirmed"` once after the first complete logical call; all
three successful hot records use trace level and succeeded phase. Uncertain
recovery uses `execution_profile_recovery_required`, error level, failed phase,
and `activation_state: "recovery_required"`. A no-op, cancelled save, or
pre-persist failure emits no successful transition.

Workspace lifecycle persistence has three owners. Internal `workspace.schema`
defines the pure canonical v2 marker, initializing journal, exact 25-file
scaffold, four empty core identities, and static session naming policy.
Internal `workspace.initialization` owns root classification, descriptor-bound
journal/scaffold convergence, Git composition, ready verification, and nearest-
recognizable-boundary discovery. `workspace.core` owns the public `Workspace`
object, generic contained persistence, and package-internal root helpers, and
delegates lifecycle policy instead of interpreting a second schema.

Construction is observational. Missing and empty roots remain pending; one
exact journal-backed partial root is initializing. Its recursive inventory is
closed, so unknown content cannot be adopted or deleted. A journal-free root is
ready when required paths and exact Git invariants pass. Readiness does not
enumerate, inspect, open, decode, or descend into other filesystem entries and
does not enumerate ignored/untracked Git entries. Such optional state is opaque
and permitted, not trusted; its owner validates it before consumption.
Recognizable legacy, markerless arbitrary nonempty, malformed required state,
staged/disallowed tracked state, tracked unknown additions, and unsafe
topology fail closed. Before mutation, initialization rejects repository
ancestors and unsupported Git topology and preflights descriptor-relative,
no-follow, exclusive-create, synchronization, identity, and cleanup support.

The first scaffold mutation is the strict
`.story-writing-agents-initialize-v2.json` containing schema version `2`, state
`initializing`, and the exact manifest digest. The owner installs only missing
byte-identical scaffold entries, then composes the #448 adapter for one
parentless `main` commit named `Initialize story workspace` using the effective
Git author and committer. It verifies the complete baseline with only the
untracked journal, removes and synchronizes that journal, then verifies the
exact clean baseline again. `story init` emits no debug record after the
baseline.

Failures preserve exact durable partial progress. The failed `Workspace` object
becomes terminal before any second attempt; a fresh object/process must
reclassify and may resume only the exact journal plus matching bytes. A ready
reopen is read-only. Unknown content in a partial initialization and
divergent required state are never overwritten, reset, migrated, adopted, or
deleted. Journal-free unknown content is left opaque. `Workspace.require_ready()`
checks the readiness captured for that object and is not a live rescan; fresh
supported entry points construct and validate a fresh object before activation.

For each supported storage attempt, the internal writer owns the lifetimes of
the root, debug-directory, guard, and active-log descriptors. It creates or opens
the debug directory relative to the verified root, performs leaf operations
relative to the verified child, tracks expected rotation absences/inodes, and
owns this schema plus lazy leaf creation, single-link regular-file validation,
active/backup size validation, append, best-effort exact-inode rollback,
rotation, and cleanup. An unavailable or replaced root, or a platform without
the required safe descriptor-relative primitives, drops the record instead of
using a weaker fallback. Constructing the internal writer remains
filesystem-I/O-free.
`StoryRuntime` construction only retains that I/O-free handle and emits nothing.
`AgentBackend` begins emitting only after the complete configuration snapshot
loads successfully; CLI, controller, and smoke emission starts only after
workspace resolution or initialization.

Only package-internal diagnostic storage consults the retained root identity.
Ordinary `Workspace` domain I/O remains pathname-based and may observe a real
directory installed later at the same root path. It is not an identity-pinned
filesystem snapshot or a transaction across operations.

Explicit CLI smoke resolves an already ready v2 root and its smoke owner
constructs another fresh `Workspace` to reverify exact clean state. This path
does not create a debug-log handle, directive, catalog, event, session, or other
record. It returns null session/conversation IDs and only initialization plus
unchanged-execution checks. `retained:true` describes the caller-owned path,
not retained smoke output.

Implicit smoke creates, initializes, exercises, and removes its own temporary
workspace. It uses the real identity-sensitive initializer and can exercise
debug, directive, catalog, and empty-history owners before the directory is
deleted. Identity or initialization failures propagate while temporary cleanup
still runs.

The low-level writer attempts cleanup for every independently acquired
descriptor and lock and drops ordinary `Exception` failures. An original body
`KeyboardInterrupt` or `SystemExit` takes precedence; otherwise the first
cleanup process-control failure propagates after later cleanup attempts. Each of
the five consumers adds a private `BaseException` guard around submissions, so
diagnostic failure or an absent record cannot replace established results,
exceptions, cancellation, persistence, cleanup, output, or exits.
Explicit smoke writes no record; implicit smoke removes any best-effort record
with its temporary workspace.

For the five synchronous provider-free `StoryRuntime` methods—directive
application, runtime-override saving, and session, debate, or context
inspection—a successful directive or configuration operation attempts
`succeeded`/`completed`, while successful inspection attempts
`observed`/`completed`. An ordinary underlying `Exception` attempts
`failed`/`failed`. `KeyboardInterrupt` or `SystemExit` instead attempts
`cancelled`/`interrupted`, then is bare-reraised unchanged; diagnostic failure
cannot replace that control.

Storage paths and retention are in [Session storage](../reference/configuration/session-storage.md#runtime-debug-log-storage),
failure guarantees in [Filesystem safety](../operations/filesystem-safety.md#debug-log-filesystem-safety),
and excluded content in [Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

<a id="debate-context-sidecar"></a>

## Debate-context sidecar

The exact lazy path is
`discussion_logs/<session_id>/<debate_id>.context.json`. Its version-1 included
shape is:

```json
{
  "schema_version": 1,
  "session_id": "novel",
  "debate_id": "opening_01",
  "conversation_id": "c_opening",
  "mode": "included",
  "snapshot": {
    "events": [{
      "schema_version": 1,
      "session_id": "novel",
      "conversation_id": "c_opening",
      "turn_id": "t_01",
      "sequence": 1,
      "timestamp": "2026-01-02T00:00:00+00:00",
      "sender": "user",
      "recipient": "plot_architect",
      "kind": "user_message",
      "content": "Question"
    }, {
      "schema_version": 1,
      "session_id": "novel",
      "conversation_id": "c_opening",
      "turn_id": "t_01",
      "sequence": 2,
      "timestamp": "2026-01-02T00:00:01+00:00",
      "sender": "plot_architect",
      "recipient": "user",
      "kind": "agent_reply",
      "content": "Answer"
    }],
    "total_complete_turns": 1,
    "retained_turns": 1,
    "omitted_turns": 0,
    "retained_utf8_bytes": 14,
    "truncated": false
  }
}
```

The paired events are one complete turn. Top-level objects have exactly the six
shown keys, event objects exactly the ten shown keys, and snapshot objects
exactly the six shown keys. Snapshot metadata must match ordered,
identity-bound events and fixed context caps.

`workspace.core` validates these embedded debate-sidecar events with a distinct
internal field set. That validation does not share physical persistence,
serialization, path, or lock-registry ownership with authoritative
conversation transcripts.

Included mode always stores an exact snapshot, including the empty form
`events: []` with zero counts and `truncated: false`. Fresh mode keeps the same
six top-level fields, sets `mode` to `fresh`, and sets `snapshot` to `null`.
A context-omitted run has no sidecar; absence is read as `None`.

Creation occurs after debate reservation and before initiation or provider
work. The file is strict UTF-8 JSON, identity-bound to its path, rejects
missing, extra, or duplicate keys, and is never overwritten. A collision raises
`DebateExistsError`. `Workspace.inspect_debate_context()` and the matching
runtime methods are read-only for debate/session state; runtime wrappers may
append a bounded operational debug record. The sidecar itself does not change
inspection JSON, debate logs, result JSON, or `DebateResult`; a separate
referenced run may add its optional content-free operation ID to the result.

## Durability boundaries

Explicit-reference authorization has a separate durability boundary under
`writing_artifacts/input_snapshots/`. For each nonempty operation, the
repository publishes a schema-v2 manifest and `requested` event before reading
artifact source content. Later operation and invocation events are
create-only, canonically encoded records with contiguous sequences and
contiguous invocation ordinals. A delivery event is durable before selected
bytes leave the repository boundary. The same repository lease serializes
these evidence writes with source reads, filesystem-identity revalidation, and
artifact application recovery. Recovery runs before the authoritative
source/reference observation, while reference evidence remains outside the
application catalog and ledger.

Durable evidence contains schema and operation/invocation identity,
path/range, content-free filesystem source identity, hashes, counts,
correlation, states, checkpoints, and
fixed outcomes—not selected bytes, source/provider text, prompts, or results.
The private invocation manifest also binds a capability identifier; the
validated history returned to callers does not expose it. Captured and
delivered byte tuples are process-local, closeable values. The capability
object is never serialized or sent to a provider; its identifier remains
repository-private and is absent from provider payloads and validated history.
A backend-created read coordinator advances the existing invocation records:
the exact full provider action precedes `delivered`, a structurally valid
substantive result precedes `result_received`, and final source-identity
revalidation precedes `accepted`. Selected text, provider payloads, and provider
results never become evidence fields. Schema-v1 evidence cannot be reused or
converted to authorize or resume work. A restart can validate schema-v2 history
but cannot reconstruct or resume transient capabilities. An empty reference batch creates
neither evidence nor artifact I/O. These records are not part of the canonical
writing-artifact catalog, completion, or picker namespace.

The blackboard retains input, candidates, audits, proposals, visible errors,
finalization metadata, and commit record. Result JSON is a projection and omits
its duplicated message list. An untraced blackboard row omits `trace`; a traced
row stores the exact trace object and reopens with a typed `ReplyTrace`. The
reader accepts only the closed base-message shape or that shape plus a valid
trace object, and reports unknown or malformed fields against their exact JSONL
line. Agent history remains owner-isolated and bounded, though provider output
in visible fields may still be sensitive.

Terminal result creation and lifecycle transition finish before terminal
progress. Artifact application is a separate recoverable transaction and still
does not combine debate messages, lifecycle/result state, conversations,
configuration, provider state, or Git into one transaction.

Private `writing_artifacts._application_store` is the sole persistence
interpreter for #519. `WritingArtifactRepository` owns one in-process lock and
one permanent workspace lease; every authoritative source, explicit-reference,
catalog, and application operation acquires both and runs recovery before it
reads current artifact authority. The private store uses existing
descriptor-safe storage and is not exported. #520 adds the module-supported
internal `apply_artifact_changes(request)` repository seam; neither repository
nor store is exported by a package facade. Runtime separately supplies
lead/user Apply-confirmation authority.

The seam accepts exactly one frozen `ArtifactApplicationRequest`, never prose,
a mapping, a mutable wrapper, or a subclass. The request owns its exact
content-bearing change set and sorted canonical `attached_core_paths`; those
paths are review evidence, not implicit write targets. Before taking the lease,
the repository revalidates the request shape and correlation. It then takes one
process lock and lease and delegates once to the private store, which rereads
the complete current state and recomputes change-set and relationship authority
before journaling. The immutable result is `applied` for the first effective
invocation or `unchanged` for an exact completed retry. The request/result pair,
not the result alone, supplies operation classification plus affected, final,
rename-lineage, and deletion facts; a result is not a complete catalog.

One immutable schema-1 transaction journal contains the exact request and target
bytes, catalog-before and schema-2 catalog-after images, and the immutable
content-free application record. Under the same lease the store:

```text
revalidates current bytes, relationships, and all request correlations
-> publishes the content-bearing journal
-> publishes each ordered create/patch/replace/rename/delete target
-> publishes the immutable content-free application record
-> publishes the current-membership catalog last
-> verifies the complete new authority
-> retires the unchanged journal to non-authoritative staging
```

A rename has separate source and destination targets. Core identities allow
create/patch/replace but never rename/delete. The application does not stage or
commit Git. The catalog retains only current paths; immutable records retain the
exact historical result, including manuscript rename lineage and deletion
provenance.

This is recovery-before-read atomicity, not simultaneous multi-file filesystem
replacement. A raw external reader may see partial target publication after a
crash. A repository reader first recovers: at exact catalog-before it permits
only each target's recorded old/new value and rolls forward; at exact
catalog-after it verifies all new targets and the record; any other position or
divergent evidence creates/preserves `recovery_blocked.json` and returns no
authoritative data. Recovery also precedes reference reads, so agent context
cannot observe a repository-returned partial application.

Failure after revalidation but before the journal leaves old authority and a
fresh retry may apply once. Failure from journal publication through catalog or
journal retirement is completed or verified by recovery. A byte-identical retry
after a completed application or lost response returns `unchanged` with the
original exact result facts and no republish. Reusing the application ID with
different request bytes is correlation drift and fails without mutation.

Immediate base-state drift maps to public content-free `drift`; malformed or
unsafe durable state maps to `corruption`; a durable recovery-block marker maps
to `recovery_required`. These errors do not expose story bytes or underlying
exception context. A recoverable interruption with an exact journal is instead
completed or verified before the next repository-owned authoritative access.

This seam neither confirms nor rejects a proposal, refreshes prompt context,
calls a provider or agent, nor stages or commits Git. Reject and Revise mean
runtime does not invoke it. A later story-commit action must independently inspect
the applied state and obtain fresh Git-specific authority.

For a successful runtime Apply, the exact physical/semantic session binding is
reopened across follow-on `story_commit_request` publication and in-memory
service registration. Detected binding loss suppresses the returned request
and removes the in-memory service, while the already published Apply result and
any already-completed request append remain durable. There is intentionally no
durable post-Apply handoff-failure state, schema field, or restart projection
yet; that separate persistence change is deferred to sibling
[Issue #662](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/662).

The additive `inspect_artifact_application(application_id)` method shares the
same repository boundary but never calls the apply path. It validates one exact
safe built-in string before locking, then acquires one process lock and lease,
delegates once to the store, recovers pending journals, and audits the complete
ordered record history against the schema-2 current catalog before lookup. An
existing identity returns its original immutable `applied` result; it never
converts that history to retry-state `unchanged`. A safe absent identity returns
`None` only after the same recovery/audit succeeds. First access may bootstrap
the standard repository lock/state directories. Recovery may finish an existing
journal, but inspection never initiates a new application or reconstructs its
content-bearing request; without pending recovery it creates no artifact,
application record, journal, or catalog entry.

Invalid IDs fail as `INVALID_IDENTIFIER` before the lease; malformed persisted
authority maps to content-safe `CORRUPTION`; a block marker or unrecoverable
journal maps to `RECOVERY_REQUIRED`. The result exposes content-free
path/digest/correlation/lineage/delete facts. The method neither infers session
ownership nor touches provider, context, confirmation, agent, or Git state. A
later #527 owner must supply its own stored session/application correlation
before using this application fact.

Application records, catalog, and results contain paths, identifiers, digests,
current membership, lineage, and delete provenance but no story bodies.
Transaction journals and staging residue can contain exact plaintext story
bytes. A nonempty legacy `commits/` directory, catalog schema 1, transaction
schema 2, malformed ledger, or noncanonical/aliased evidence is preserved and
fails closed; there is no migration, compatibility, reconstruction, or
normalization route.

The permanent repository readiness triple has its own durability boundary:
primary lock, anchor, and `ready` are exact hard-link names for one regular inode
with link count three. Initialization first takes a transient bootstrap `flock`
on the open writing-artifact state directory and flushes its retained ancestry.
It exclusively creates and flushes the primary, flushes the parent, links and
flushes the anchor, verifies that `ready` is absent, then links and flushes
`ready`. It verifies the triple, acquires the primary `flock`, reverifies, and
releases bootstrap before work. Partial or old two-link state, aliases,
divergent or extra links, and any failure prevent protected work and remain
unchanged; no cleanup, reconstruction, or conversion is attempted.

That artifact transaction does not combine sidecars, blackboards, defaults,
transcripts, debate lifecycle, results, provider state, and agent state into one
database transaction. Infrastructure interruption can therefore leave a
recoverable artifact journal alongside an incomplete non-artifact operation.
Exact artifact paths, eligibility, recovery positions, and user responses are in
[Canonical writing artifacts](../reference/writing-artifacts.md).

One-step and retrieval turns append exactly one user event before provider work.
Success appends one reply; ordinary failures attempt one sanitized runtime error,
and `asyncio.CancelledError` attempts its fixed runtime error. Provider
`KeyboardInterrupt` or `SystemExit` appends no transcript outcome: the runtime
instead attempts a separate `cancelled`/`interrupted` operational record and
re-raises the identical exception without automatic retry. The unmatched user
event remains visible to full transcript reads and is excluded from later context.
Retrieval actions and observations add no control, source, sidecar, catalog,
default, debate, state, or canonical-artifact publication; compatible
provider-thread metadata may update. A referenced top-level turn separately
adds the content-free operation and invocation evidence described above.
Consensus snapshots never append the transcript.

The canonical path tree is in [Session storage](../reference/configuration/session-storage.md).
Accessor signatures are in [Workspace API](../reference/workspace-api.md),
containment guarantees in [Filesystem safety](../operations/filesystem-safety.md),
and consequences in [Recovery](../operations/recovery-and-limitations.md).
