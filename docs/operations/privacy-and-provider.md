# Privacy and provider exposure

> **Availability:** these controls cover current management/search/inspection
> commands and retained low-level backend, contextual execution, debate, and
> explicit-reference components, plus direct
> Python conversation archive values, filesystem transfer, and optional SQLite
> conversation/search storage, provider-free desired selection, and
> conversation transfer. They also cover the optional fixed-local
> conversation-RAG store and noninteractive lifecycle/authorized-search
> commands, plus interactive settings and startup composition.
> They also cover the content-safe runtime debug log, automatic bounded
> agent-exchange store, required base-installed isolated `/exchanges` viewer,
> and the separate bounded store for eligible rejected provider responses.
> They do not make either provider, its executable, the workspace, or the
> terminal trusted.
> `StoryRuntime.lead_turn()` and confirmed specialist/synthesis invocations are
> provider-bound story spans reached from Python or ordinary focused shell
> input. Inline `${...}` references are active; `$` and `$$` activate canonical
> whole-file and ranged selection in focused Lead input. All Lead/consultation
> references follow the boundaries below.
> Settings Model/Effort catalog discovery is also bounded and local; only a
> user's selected opaque value can be persisted, never provider catalog data.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored or displayed failure text.

## Plaintext local data

There is no encryption, application access control, or secure erase.
Documents, debate/context records, provider metadata, subjects, transcripts,
scrollback, manuscripts, artifact catalogs, commit provenance, transaction
journals, transfer checkpoints, reference-operation evidence, and recovery
markers have no application-managed retention policy and rely on
OS/terminal-service protection. The workspace debug log, agent-exchange store,
and rejected-provider-response store are bounded-retention channels described
below. All remain plaintext. The runtime log is content-safe; the other two
deliberately admit provider-call content within different schemas and lifecycle
boundaries.

Blackboards can contain input, drafts, reviews, and errors. Provider output is
not generally scrubbed; catalog archive status neither deletes nor conceals
conversations; transcripts retain messages, replies, and runtime errors.

### Session Git-resource exposure

The package-internal #504 lifecycle is local and provider-free, but it is not
secret storage. During provisioning, the owner-only plaintext journal beneath
`.git/story-writing-agents/runtime/session-provisioning/` contains the session
ID, random provisioning ID, baseline object ID, one UTC timestamp, and the
effective author and committer names and email addresses. Its exact path and
filesystem metadata also reveal the session identity and operation timing. The
journal is removed only after the correlated triple verifies and removal is
synchronized; interruption or recovery-required state can retain it.

The permanent internal seed commit retains the effective author/committer,
timestamp, and session ID in its message
`Initialize story session runtime: <session_id>`. It has an empty tree and no
story content, but it remains local Git history and is never securely erased by
the runtime. Branch/ref names and the sibling worktree path also expose the
session ID. The persistent lock contains no story content but its metadata can
expose use timing.

Provisioning sends none of these values to Codex, Antigravity, another
provider, or a remote. That does not encrypt them or prevent another local
process with repository access from reading them. Protect the complete main
repository, sibling worktree directory, `.git` storage, backups, and process
memory. Pause, release, close, and process exit are not retention or secure-
erase operations; there is no automatic deletion, migration, compatibility,
push, or remote-cleanup path.

<a id="session-history-exposure"></a>

### Session-history exposure

The package-internal #508 history beneath
`.git/story-writing-agents/runtime/session-history/<session_id>/` is also local,
provider-free plaintext. Its JSON records are content-free, but not necessarily
anonymous: they retain session, operation, application, proposal, and
correlation IDs; canonical relative artifact paths; before/after SHA-256
digests; expected Git object IDs; a worktree identity digest; a bounded proposed
commit message and impact labels; fixed state/sequence facts; and whether
author/committer identity came from environment, repository, global, or system
configuration. They do not retain effective names/emails, core-document or
manuscript bytes, provider prompts/replies, raw Git output, credentials,
descriptors, or serialized confirmation authority.

Paths, identifiers, messages, labels, digests, object IDs, state transitions,
and filesystem timestamps can still reveal project structure, intent, identity
correlation, and operation timing. Hashes provide content identity, not secrecy,
authentication, authorization, or proof that the original content can be
restored. Protect these records with the repository and include them in a
consistent operational-recovery backup; do not publish them as harmless
telemetry.

The optional `checkpoint-inputs/<operation_id>/index` is operation-owned Git
metadata, not a redaction boundary. It can expose paths, object identities, and
staging facts and is deliberately preserved after ambiguous or recovery-
required outcomes. Only an exact durably committed checkpoint permits its
narrow removal. Candidate and operation records have no expiry, compaction, or
automatic cleanup.

#508 itself creates no story commit or checkpoint. The package-internal #509
service can create literal blobs/tree/commit objects and update one local
session branch after receiving typed applied-change evidence and a fresh exact
Commit decision. The package-internal #510 service can create literal runtime-
state blobs/tree/commit objects and update only one local session internal ref
from exact owner-supplied deltas. The separately confirmed Lead UI invokes the
story-commit service through runtime orchestration; there is no standalone Git
command or provider capability. No ordinary Lead action invokes checkpoints.

Prepare persists only the content-free candidate metadata described above. A
Commit authority remains process/service/thread-bound, nonserializable, and
one-use; the user decision itself is not stored as reusable executable
authority. Reject/Revise persist only a terminal operation correlation and
state, not the confirmation payload or story bytes.

During Commit, Git object storage receives the exact candidate file bytes as
literal filter-free blobs before the branch update, and the real session index
receives exact candidate path entries. An interruption can therefore leave
unreachable blob/tree/commit objects or a staged index even when no successful
result was returned. Recovery preserves these facts and never treats deletion
as privacy cleanup. A completed commit permanently exposes its message,
effective author/committer names and emails, paths, parent/tree IDs, and story
content to local Git readers according to Git reachability/retention behavior.
SHA-256 object IDs improve collision properties but do not encrypt content;
SHA-1 and SHA-256 repositories have the same disclosure boundary.

During a checkpoint, the content-free operation JSON stores session,
correlation and operation IDs, expected internal tip/tree, salted commit-intent
digest, state/sequence, optional checkpoint ID, and alternate-index preservation
flag. It does not store the supplied runtime-state bodies or effective identity.
Those bodies do enter local Git object storage as filter-free blobs, and the
checkpoint commit permanently records their paths plus the fixed message,
effective author/committer names and emails, and captured UTC timestamps. The
operation-owned alternate index can reveal paths, object IDs, and staging facts.
Provider-free and content-free history therefore do not make checkpointed
runtime state encrypted, anonymous, remotely backed up, or securely erasable.

Removing a session-history record, staged index entry, or alternate checkpoint
index would neither securely erase Git objects nor revoke a prior confirmation.
Provider-free means the service makes no network/provider call; it does not
mean encryption, anonymity, backup, retention, or secure erase. Protect the
main repository, sibling worktree, common Git object/index/ref storage, and
session-history records as one sensitive recovery set.

<a id="conversation-archive-privacy"></a>

### Conversation archive exposure

Canonical conversation archive values and serialized bytes contain exact
plaintext subjects, identities, timestamps, statuses, proposals,
confirmations, specialist replies, execution outcomes, interruption/recovery
facts, and other typed collaboration payloads. Serialization performs no redaction or
encryption. Its SHA-256 digest detects modification but provides no secrecy,
signature, origin authentication, or access control.

Filesystem export deliberately excludes debate/runtime logs, debate
state/results/context, credentials, provider and agent state, prompts,
configuration/defaults, storage activation and RAG state, manuscripts, and
writing artifacts. Those exclusions make the value conversation-only; they do
not make its included collaboration content safe to disclose. Import copies the
included plaintext and neither deletes excluded state nor securely erases a
source or caller-owned archive copy.

Provider-private state, hidden reasoning, raw provider responses and
diagnostics, credentials, capabilities, full referenced-file bytes, artifact
contents, and derived FTS/RAG data are not archive payloads. Therefore an
archive or backend transfer is not a workspace, provider-session, or recovery
backup. Only canonical archive schema 2 is accepted; legacy transcript files,
database event rows, and schema-1 archive bytes are rejected rather than
redacted or converted.

The runtime owns no archive file or retention policy. A caller that stores,
transmits, logs, or otherwise retains the returned bytes owns protection and
cleanup for every copy. See
[Canonical conversation archives](../reference/conversation-archives.md#archive-non-goals).

The optional `discussion_logs/conversation_storage.sqlite3` database is also
plaintext and can coexist with the filesystem catalog and histories, leaving
two independent copies under caller control. It contains exact subjects,
identities, timestamps, catalog status, canonical collaboration BLOBs, and the
still-present v1/derived search resources. Protect the database, any live rollback journal, external backups,
and process memory with the rest of the workspace.

Desired `conversation_storage_backend` in `config/system_defaults.json` and
active `config/conversation_storage_state.json` are also plaintext. They expose
backend choice, and the active marker exposes a nonnegative generation; they do
not contain subjects or transcript content. File metadata and changes can still
reveal operational timing. Protect them with the complete workspace and do not
publish them as proof of authorization or parity.

The private derived
`discussion_logs/.conversation_storage_fts5.sqlite3` companion repeats
normalized non-runtime visible event text and stable conversation/event
coordinates. Owner-only permissions and its disposable status do not encrypt,
redact, securely erase, or make that material safe to disclose. Protect it and
any retained backup as transcript data. An authoritative backup must preserve
the main database; it may omit the companion because exact search falls back
to the main snapshot. Search preserves and ignores an unsafe companion rather
than reading or repairing it.

Provisioning is creation, not transfer or deletion; archive export/import and
the explicit storage transfer workflow copy plaintext rather than securely
erasing a source. The canonical archive payload contains exact metadata and
events in memory, and any persistence or transmission performed by its caller
is outside the adapters' controls.

`story storage transfer` and `story storage verify` emit one content-free report
containing backend names, fixed outcomes/failures, parity, checkpoint state, and
aggregate classification/source counts. They never print subjects, conversation
IDs, event text, paths, malformed values, or FTS content and never create a
runtime debug record. This output boundary does not make the copied destination
confidential: transfer can leave the same plaintext conversations in both
stores, never deletes the source, and does not impose a retention policy.

`story storage use` emits a different counts-free content-free report containing
only fixed operation/outcome/backend, installation/provisioning/parity,
configuration/activation, failure, and schema values. It never prints
conversation identities, subjects, events, paths, exceptions, installer
output, provider data, or parity counts and never creates a runtime debug
record. An explicitly run installer instead writes its native stdout and
stderr to the CLI's stderr stream, outside that JSON boundary.

Retained transfer checkpoints contain session and conversation IDs, a SHA-256
fingerprint of the canonical source archive, source conversation/event counts,
and completed/total progress. They omit subjects, timestamps, statuses, events,
runtime errors, and FTS data, but identities, counts, timing of file updates, and
the fingerprint can still be sensitive. Protect both per-direction checkpoint
files with the workspace; terminal completion is audit state, not secure
erasure or authorization.

## Local management, search, and reads

`/search` shows snippets from closed readable schema-v2 event fields plus a
display-only subject and exact event/field/range citations. `/transcript` shows
active or archived structured readable records; `/history` shows one full or
newest-record suffix plus state and next-safe-action meanings. `/status` exposes
workspace/session/subject/context/run metadata. Session status shows durable
state, process access, defaults, current attachment/focus, readiness, and next
action, then the authoritative provider/model/effort profile, sources,
confirmation, thread state, and route generation for every agent. `/agents`
shows the compact profile and confirmation. Neither command shows owner
hostname, PID, or claim token. `/inspect` whitelists
lifecycle/artifact/context identity/mode/counts, never raw messages, candidates,
errors/actions, context content, or provider fields.

Help/status/history/inspection are provider-free and read-only with respect to
story and session domain state. Workspace-aware CLI/controller activity and
`StoryRuntime` or `AgentBackend` inspection/lifecycle reads may still append
bounded operational debug records. Directives are provider-free but atomically
update validated overrides. Other scope, thread, catalog, transcript, debate,
artifact, and core state is preserved.

Provider-free is not exposure-free: subjects, snippets, metadata, errors, and
content reach callbacks/scrollback. Lobby/session orientation intentionally
prints the normalized workspace root. Post-workspace error hints expose only the
fixed relative path `debug_logs/runtime.jsonl`, not a dynamically discovered or
absolute diagnostic path. Terminal escaping is not redaction. See
[Conversation search](../reference/conversation-search.md) and
[Terminal safety](terminal-safety.md).

Direct SQLite catalog, collaboration history, context, and exact search operations
are provider-free and add no network call, authentication, RAG, or new citation
mechanism. Optional FTS5 hint lookup adds no network path and never becomes
search truth. Returned Python values, application logs, process memory, and
caller-created backups can still move or expose plaintext beyond the
workspace. Storage transfer/verification is also provider-free and dispatches
before authentication, broader settings/context, backend, runtime, session, or
debug-log construction. It still performs local archive reads and, outside
dry-run/verify, destination and checkpoint writes. `story storage use` likewise
dispatches provider-free before debug logging and provider/runtime construction,
but can explicitly run a host installer, provision an empty database, read both
canonical stores for parity, and write desired configuration. `/settings`
uses the same storage selection boundary only on its full Apply Changes path,
after the separate provider-auth gate. A Message-details-only Apply Changes
never authenticates or enters storage selection. A Conversation-RAG-only Apply
Changes likewise performs no authentication or storage selection/readiness and
adds no backend/runtime, route/thread, composition, probe/install, provider-
catalog, subprocess, socket, or network work. The next shell activates the
selected ready storage bundle before provider authentication.

<a id="conversation-rag-command-privacy"></a>

## Conversation RAG command privacy

The `story rag` family never authenticates or invokes Codex, Antigravity, or an
external RAG provider. Runtime provider `local` is a fixed deterministic
in-process embedder/ranker and performs no network, credential, subprocess, or
environment discovery. The separate `pip install '.[rag]'` step may need Git,
network, and repository access to fetch the exact VCS pin; command-time
compatibility checks do not install or fetch anything.

The command/output boundaries differ by operation:

| Operation | Local reads/writes and exposed output |
| --- | --- |
| `status` | Reads configuration, active authoritative conversation metadata/content, and derived metadata under bounded budgets so it can compute checkpoints and counts. It emits only fixed state, compatibility, checkpoints, counts, and rebuild facts. It creates no coordinator lock, RAG path, configuration rewrite, or runtime debug record. |
| `enable` / `disable` | Read and compare-and-set the complete configuration pair. Reports expose only fixed provider/automatic/compatibility/configuration facts. They do not read conversations or delete derived state. |
| confirmed `rebuild` | Reads all authorized active complete visible turns for the session and writes plaintext fragments/vectors into the derived generation store. Its report is content-free: only fixed state, checkpoints, aggregate counts, and compatibility/configuration values. |
| `search` | Reads active conversation authority and derived state, then deliberately writes bounded authoritative excerpt text plus stable session/conversation/turn/event and derived-reference coordinates to standard output. It omits query text, subjects, scores, paths, credentials, provider text, exceptions, and diagnostics. Treat the JSON line, pipes, redirected files, and terminal scrollback as conversation data. |
| confirmed `purge` | Deletes only validated derived contents for one session and exposes aggregate removed-generation counts. It is not secure erase; backups, process memory, terminal output, filesystem remnants, and independently authoritative conversation data remain outside its guarantee. |

All six operations dispatch before runtime debug-log construction. Their fixed
failure reports contain no query, excerpt, persisted malformed value, path, or
exception text. Content-free output is not proof that no sensitive local read
occurred: status/rebuild inspect authority, and search is intentionally
content-bearing after authoritative reread.

`conversation_rag_provider` and `conversation_rag_automatic` are plaintext
configuration facts, not credentials. Automatic use defaults off. The
Conversation RAG `/settings` editors change only pending values and perform no
optional-package probe, install, network access, or credential prompt; the
full mixed Settings path retains its independent agent-authentication and
storage-readiness boundaries. A RAG-only Apply prepares, verifies, and commits
only the system configuration without entering either gate or activating RAG.
The saved choice applies at the next shell, while the current service and
automatic policy remain unchanged.

The former one-turn interactive RAG preflight is unavailable. Ordinary input
does not retain a query or send excerpts to a story provider. The six top-level
RAG lifecycle/search commands retain their documented local data boundaries;
direct callers of the low-level service own any subsequent provider use. Raw
optional-package and composition diagnostics are replaced with fixed guidance
and do not enter configuration, transcript, debug, or semantic result output.
The fixed local provider has no runtime network or credential flow. Provider fragment text and
vectors never become authority, and canonical writing-artifact references
remain an independent authorization path.

<a id="sqlite-installation-boundary"></a>

## SQLite installation boundary

SQLite readiness probes only the active Python interpreter through an in-memory
connection and query; it does not execute or inspect a standalone `sqlite3`
binary or require FTS5. A missing prerequisite triggers no process unless the
CLI received both explicit automation flags or the user accepted the separate
Settings preview with exact YES. Unsupported hosts and recognized CI markers
run nothing.

The one allowlisted package-manager child runs at the normalized workspace root
with inherited stdin and environment. Its stdout and stderr both reach the
caller's diagnostic stream; for the CLI that is real stderr. The package
manager may prompt, access a network, modify host packages, write its own logs,
or retain terminal scrollback under its own policy. Story neither captures nor
redacts that output, supplies a custom environment, nor adds a shell, `sudo`,
`-y`, timeout, retry, or cleanup. A host package and any later empty provisioned
database are not rolled back after parity or configuration failure.

The Settings preview itself persists in ordinary semantic output/scrollback and
shows the exact command plus a host-mutation warning; it contains no provider or
story content. See the exact commands, platform matching, CI marker set, and
report mapping in the
[CLI selection contract](../reference/cli.md#conversation-storage-selection).

<a id="native-authentication-boundary"></a>

## Native authentication boundary

Interactive authentication runs only `codex login status`, `codex login`, the
resolved Antigravity executable with `models`, and that executable with no
arguments, without shell evaluation. Antigravity defaults to `agy models` for
status and bare `agy` for sign-in; a supported
`--antigravity-executable PATH` override changes only that executable. Status
probes use null stdin/stdout/stderr and a bounded wait at the resolved workspace
root. The runtime keeps only one of four content-free states in memory:
authenticated, unauthenticated, unavailable, or indeterminate. It does not
scan, decode, retain, display, or log status output.

The same native commands handle a call-time authentication challenge during
interactive Lead work. This recovery path starts `codex login` or bare `agy`
even when the preceding status probe still reports authenticated. Story first
blocks new provider activity and waits for already active calls to finish, then
returns the real terminal to one native login owner. Same-provider challenges
from one authentication generation share that handoff; provider handoffs are
serialized. Python and other noninteractive calls launch no login process.
That handoff changes no story-authoring authority: completed sibling replies
remain retained, current task/revision and draft/review requirements still
apply, and review, Apply, and Commit keep their separate confirmations.

Native sign-in runs at the same workspace root and inherits the real stdin,
stdout, stderr, process environment, and process group. The provider CLI may
therefore display prompts in terminal scrollback and open a
provider-controlled browser. The runtime does not synthesize, inspect, or
capture a browser URL; parse provider output or account facts; read tokens or
credentials; or inspect, copy, replace, or log the provider's native credential
persistence. The provider and terminal may retain their own data independently.

While that exact login child remains open, Story waits in bounded intervals and
uses the same selected-provider status command with null stdin/stdout/stderr.
Each probe is bounded and silent, cannot consume pasted terminal input, and
cannot capture provider output. Non-authenticated results leave native sign-in
in control with no overall Story login deadline. A fresh authenticated result
is the sole completion authority. Story then boundedly stops and reaps only the
exact login child if it remains open and returns to the calling startup,
`/auth`, or Settings flow; it does not require provider `/exit`, Ctrl-C, EOF,
extra Enter, confirmation, or a second paste.

Call-time recovery is deliberately stricter when the baseline already reported
authenticated. An unchanged authenticated probe does not stop a still-running
native login child. Recovery can complete after native exit status zero plus a
fresh authenticated status, or after an observed unauthenticated-to-authenticated
transition. A nonzero native exit and indeterminate or non-authenticated fresh
status fail closed. If the provider interface stays open after the user
finishes sign-in, the user exits that native interface normally; Story neither
sends nor records the exit input.

Story never copies, parses, synthesizes, displays, persists, or logs an
authorization code. Local browser-capable provider flows may need no code.
When a remote provider requires its own code copy/paste flow, the code is pasted
once into the provider-owned prompt and Story adds no code prompt of its own.
The code text, login return code, and child lifecycle are not authorization.

The workspace stores only a provider name when fresh authentication fills a
missing `default_backend`; it stores no status, account, login return code,
token, credential, or OAuth URL. Authentication guidance and results use fixed,
sanitized copy. Debug logs may contain ordinary fixed CLI operation facts but
have no authentication-state or provider-output field. A sign-in exit code,
authorization-code text, and prior state are never treated as authorization;
the runtime uses fresh status during login completion and again at each
effective-route gate.

<a id="provider-catalog-discovery-boundary"></a>

## Provider catalog discovery boundary

Settings discovery is separate from authentication and story-provider turns.
It occurs only on entering Model or Effort, not when Settings opens or while
agents/providers are navigated. Each entry launches one fresh pending-provider
command without a shell: `codex debug models --bundled` or the resolved
Antigravity executable with `models` (`agy models` by default). Effort also
uses the pending effective model.

The command runs at the workspace root with inherited environment and PATH,
null stdin, piped stdout, and null stderr. Story retains at most one MiB of
stdout, enforces a five-second deadline, and boundedly cleans up the exact child
and reader. There is no automatic retry, cache, persistence, stderr capture, or
raw diagnostic. These limits constrain Story's capture; the executable can
still access its inherited environment and workspace-visible data.

Strict parsing projects only safe immutable model/effort values. Codex keeps
list-visible rows and exact model-specific effort/default sequences.
Antigravity keeps aligned opaque slug/display rows in provider order and uses
global `low`/`medium`/`high` effort without inferring it from model text. Raw
bytes are dropped after projection. Fixed content-free states replace missing,
timed-out, nonzero, malformed, empty, oversized, and unexpected failure
details; stdout, stderr, return codes, and exception prose do not enter terminal
output or the workspace debug log.

No discovery fact is written to configuration. Apply Changes can persist only
the selected opaque model/effort token or null for inheritance—not labels,
descriptions, defaults, supported-effort lists, failure state, stderr, or raw
stdout. Chosen tokens remain plaintext local process settings and may later
appear in provider process arguments as described below.

## Explicit-reference data boundary

Reference indicators and durable authorization history are content-free. The
schema-v2 history records ordered canonical paths and ranges, filesystem source
identity and full/selected hashes and counts, agent/invocation correlation, lifecycle states,
checkpoints, and fixed outcomes. It excludes artifact bytes, parser source and
provider projections, prompts, provider results, and diagnostic values derived
from content. Authorization errors use fixed coded messages without echoing a
path or selected text.

In a direct low-level provider integration, selected artifact byte tuples are
held in closeable,
nonserializable in-process authorization, delivery, and result carriers whose
`repr` hides content. The backend transiently decodes them for the one provider
payload described below. Closing drops the carriers' references to their byte
tuples; Python and the operating system do not provide a secure memory-zeroing
guarantee. The durable evidence remains plaintext and has no encryption,
access control, secure erase, or retention policy.

Direct artifact-source entries contain canonical paths only. Listing reads path
and type metadata, not file bytes. Opening an artifact through the generic viewer
loads verified plaintext into local process memory and renders it in the
terminal. A capable viewer uses an alternate screen, but closing it is cleanup,
not secure erasure from the process, emulator, multiplexer, or remote terminal.
Fallback prints the complete numbered content into scrollback. Source-list/viewer
activity alone is not sent to a provider and creates no durable
reference-operation evidence. Submitting the composed Lead input starts the
separate authorized reference operation. Component error output is fixed and
content-free.

For a referenced lead, specialist, or synthesis call, the first provider
payload contains only fixed rules, exact operation/invocation correlation, and the complete
ordered canonical path/range indicators. The capability object is never
serialized, and its repository-private identifier is absent from provider
requests, replies, validated public history, and diagnostics. After the
provider returns the exact full read action, the backend places selected text
only under `untrusted_writing_artifact_context` on the immediately following
substantive payload. It does not copy the text into task, user input, candidate,
blackboard messages, agent state, conversation history, retrieval observations,
durable evidence, or diagnostics.

The repository writes `delivered` before returning selected bytes to that
backend path. The backend records `result_received` only after a structurally
valid substantive result, performs final revalidation, records `accepted`, and
then returns the reply. This proves the exact request, delivery sequence,
result, and acceptance—not provider comprehension, obedience, or correct use.
Baseline core context cannot satisfy explicit-reference evidence. Confirmed
collaboration creates a separate reference operation for each addressed
invocation. A specialist receives only its assignment's ordered reference
subset and cannot inventory, SEARCH, or READ a sibling-only artifact path.
Lead synthesis receives fresh direct reference authority rather than borrowing
a specialist receipt.

Closing local authorization, delivery, and result carriers drops their Python
references but offers neither secure memory erasure nor provider erasure. Once
the substantive request is sent, provider-private or remote systems may retain
its context under their own policy. `StoryRuntime.lead_turn()`, confirmed
consultation execution, and `propose_specialist_advisory()` construct this
path; the interactive controller does not.

### Lead-evidence retention

Fresh lead judgments add content-free evidence to canonical collaboration
history. A receipt can retain relative locator/source identity, state,
revision/content/manifest digests, UTF-8 size, exact line or record boundary,
and action ordinal. A zero-hit receipt retains a query digest and count,
authorized source identities/kinds, optional artifact prefix, and a
deterministic fingerprint of the authorized source snapshot. It does not
retain the query, snippet, excerpt, or source body. The typed lead message and
later specialist replies/synthesis remain visible plaintext story analysis.

These receipts disclose topology and change facts even though they are
content-free. Protect them like other workspace metadata. They prove only that
runtime authorized and revalidated the cited acquisition. They do not prove
provider comprehension, semantic relevance, exhaustive search outside the
authorized scope, or story consistency. Runtime does not inspect prose to
correct a provider's `story_specific`/`workflow_meta` classification.

An evidence-validation rejection writes only a closed failure code plus the
submitted invocation/call IDs to canonical history. The rejected response may
still exist in the separate bounded provider-exchange or rejected-response
diagnostic stores under their documented best-effort policies; the failure
event does not copy that response into collaboration history.

### Artifact-candidate and review retention

A valid non-null lead `artifact_intent` contains proposed story text. Runtime
resolves it against a complete in-process capture of all canonical artifacts.
That capture is used to build and validate the candidate; it is not copied as
a second complete-workspace blob into collaboration history. The resulting
`ArtifactReviewCandidate`, however, deliberately contains the exact proposed
change set and projected relationship facts and is stored in the typed
`artifact_review_candidate` event. A successful `propose` synthesis stores the
same candidate inside `ArtifactReviewedProposal` so restart and storage
transfer can prove what every reviewer assessed.

After exact confirmation, each disclosed specialist and the lead synthesis
provider receive the same content-bearing candidate in their runtime input.
They may also receive separately authorized reference bytes through the
ordinary reference boundary. Candidate text can therefore be retained by the
configured provider under its policy and by local provider-exchange diagnostics
under their documented limits. The candidate is hidden from `repr`, but that
is only accidental-display reduction, not encryption or erasure.

Readable collaboration projection exposes only artifact review message,
decision, state, and closed failure code. Candidate and reviewed-proposal bytes
are not searchable readable fields in the bounded SEARCH/READ adapter.
Required Lead session memory separately includes visible review rationale,
impact, limitations, and exact proposed-operation text/bytes from retained
candidate and reviewed-proposal events. Later Lead calls can therefore disclose
these values again, including after catalog archiving. Direct event-store/API
readers and canonical filesystem/SQLite/archive/transfer bytes can still access
them; protect those stores as story content.

If non-null artifact intent is malformed or claims Apply/Git authority,
canonical collaboration history records only content-free
`artifact_intent_rejected` and `intent_rejected` failures. The rejected raw
provider response may still appear in the separate rejected-response or
provider-exchange diagnostic boundary. No artifact write, Apply authority, or
Git action occurs for candidate construction, review, or reviewed-proposal
finalization.

<a id="agent-context-progress-privacy"></a>

## Agent-context progress privacy

The optional public context lifecycle observer and any caller projection contain
only validated scope and operation identities, fixed lifecycle kinds,
nonnegative counters, and an optional truncation boolean. They have no field
for prompts, search queries, candidates, excerpts, mandatory-reference bodies,
provider responses, source or reference paths, source digests, private agent
state, exception details, or provider-thread identities. Debate events add only
the canonical agent, phase, and round needed to distinguish logical
invocations. This content-free `ContextExecutionEvent` contract remains the
supported Python observer.

Supported runtime/session Lead methods separately expose an additive
keyword-only `action_progress_sink` for package-internal-typed, process-local
action events. They contain exact SEARCH query literals, requested source kinds,
an optional validated workspace-relative artifact prefix, and completion
locators, ranges, truncation, and UTF-8 byte counts. READ trace edges contain
safe relative locators, exact whole/line/record boundaries, and completion byte
counts. They have no source snippets or excerpts, provider answers, absolute
paths, credentials, or hidden reasoning, and are not a supported general
observer callback, durable event, ledger, or compatibility surface. Their type
is not root- or facade-exported, and the interactive controller is the intended
consumer. Python callers can nevertheless supply this callback and retain its
events; the caller then owns access control, retention, and cleanup for that
copy.

Observer delivery adds no provider call, result field, story content,
conversation event, debate record, or second context ledger. The component has
no supported post-hoc observer inspection. A consumer may render public compact
facts into terminal output, where an emulator, multiplexer, or remote terminal
may retain them in scrollback. The installed shell's detailed frame is actively
redrawn and cleared, but that is presentation cleanup rather than secure
erasure: terminal capture, scrollback, multiplexers, remote terminals, or
accessibility tooling may retain its exact queries and relative locators/ranges.
A Python caller may deliberately retain
callback values, in which case that caller owns access control, retention, and
cleanup for the new copy.

The context ledger and runtime debug log remain separate owners. The ledger
retains its existing content-free checkpoint/accounting authority; progress
does not duplicate it. Independently of whether a generic observer is supplied,
the debug log can best-effort record `agent_context` with a fixed
`context_*` outcome and safe session, conversation-or-debate, and agent IDs. It
does not retain the observer's operation ID, detailed counters, provider-token
facts, paths, or story/provider text and is not a replayable event stream.

These boundaries make progress content-free, not anonymous. Workspace,
session, subject, operation, agent, phase, and round identities; counts; token
usage; timing; and terminal scrollback can still reveal sensitive operational
patterns. Treat caller-retained events, debug records, and captured terminal
output as plaintext workspace/session metadata.

Mandatory references remain a separate exact-read authorization path. Their
ordered paths/ranges and durable content-free evidence retain the exposure
described above, while their selected bodies never enter context lifecycle
events. Optional conversation RAG is also independent: its query, excerpts,
citations, and derived state do not widen the observer schema. See
[Agent-context progress](../guides/agent-context-progress.md#privacy-references-and-limits)
for the user-visible lifecycle and limits.

## Provider disclosure

A built-in substantive call uses typed agent-context wire v7. Codex and
Antigravity share the same closed domain constraints and payload semantics: the
required root fields are `correlation` and `action`, and `action` is one closed
SEARCH, READ, or ANSWER object. ANSWER embeds its invocation-specific result
object directly and names only successful supporting READ handles in
`used_handles`, or `[]` when none was used. There is no JSON-in-string layer,
compatibility decoder, or migration. Typed structure does not redact or reduce
the sensitivity of action data.

Antigravity uses an equivalent local rendering of singleton null/integer enum
constraints for native function-schema conversion. Its `ProviderInvocation`,
request capture, schema-byte accounting, argv, and required echo contain the
actual rendered schema; those bytes can differ from and exceed the unchanged
domain/Codex schema. Payload bytes and shared accounting definitions remain
unchanged. See the [rendering rules](../guides/antigravity.md#shared-structured-output-contract).

Stable binding material contains role/persona/language/behavior/protocol, not
mutable core bytes. Initial contextual input can expose the current command,
complete logical candidate or exact pointer/splice operation, selected
role-relevant P2 source bodies, focused record changes, content-free catalog
facts, and applicable mandatory-reference bodies. Continuations expose only
the newly authorized SEARCH/READ observation.

Every ordinary Lead invocation and consultation/artifact-review synthesis also
receives required session context (`h`). Bootstrap includes all retained active
and archived conversations; compatible continuation includes unseen events.
This contains exact readable fields, original timestamps, visible workflow
facts, review rationale/impact/limitations, proposed operation bytes, proposed
commit messages, and application/commit decision or revision-request text.
It also carries current
session directives and complete current canonical core-document bytes on both
bootstrap and delta. No current receipt or private confirmation, write, or Git
capability is copied. These are sensitive provider-readable inputs, subject
to provider retention and the separate local exchange-diagnostic limits.

Catalog archive status is not deletion, a session-memory exclusion, or a
provider-disclosure opt-out. Canonical archive export/import values are a
separate storage/transfer representation; creating an archive copy does not
alter the retained history that Lead context captures.

The lead initial payload also exposes four content-free canonical-core status
rows, active same-session authoring task facts, and the strict
scope/authoring-scope/status/evidence answer contract. Scope interpretation,
target paths, domains, task identity/revision, assignment mode, review candidate
aliases, and criteria may disclose story plans and are retained in collaboration
history where applicable. Archived history is still provider-readable memory,
but its tasks supply no current routing authority. Status inventory
does not itself expose core bytes or grant a story-body receipt. Even with no
explicit reference or provider SEARCH/READ, `workflow_meta` turns receive
current core bytes and retained history through the separate required `h`
context. Memory cannot satisfy the current evidence contract.

SEARCH/READ is route-restricted to canonical writing artifacts, focused
same-session conversation, active debate, and the invoking agent's own state.
Provider-visible evidence deliberately contains relative source identity,
revision, boundary, digests, UTF-8 sizes, provenance, fresh handle, and
untrusted content so the agent can reason from exact evidence. It excludes
absolute paths, internal files, another agent's state, and incompatible scopes.
These evidence facts are provider input; public progress and the ledger remain
content-free.

A specialist-assignment payload also exposes its visible instruction,
authoring scope, draft/review mode, review basis, exact input aliases/revisions,
and strict assessment/message contract. A drafted reply can contain substantive
new story text even though the specialist cannot write files. A synthesis
payload exposes every successful specialist's assessment, message, ordered
input IDs, reply-event attribution, all direct story inputs, mandatory
unresolved IDs, and the optional round-two contract. Those values can contain
sensitive story analysis and are captured as plaintext provider input/output
and canonical collaboration events as applicable. Objective draft sources
retain complete original messages and exact digests, even when a prepared
candidate omits their conversational framing. The displayed runtime drafting
notice is separate from that stored source identity. The provider receives no
write/Git capability, credential, absolute path, sibling-only artifact source,
or API for calling another agent.

`StorySessionContext` remains an internal capture and direct-request
compatibility value, not the current built-in provider envelope. Direct eager
`context_document_files` also remains compatible, but the four built-in roles
use provider-managed context with empty mutable-core bindings. Optional
conversation RAG stays separate from deterministic lexical context actions.

Ordinary Lead turns and consultation/artifact-review synthesis share compatible
workspace/session/agent continuation across conversations. Specialists and
consensus retain their exact scopes and do not share that Lead session thread.
Cache/history/compaction may retain previously disclosed content under provider
policy, but never grant current authority. Missing or tainted native history
selects exact recovery from local retained history/current state on the next
safe authorized work. An eligible failed read-only Lead attempt can be closed
and followed by explicit new input in the same conversation. Its original
request and content-free failure remain in history; only the matching
unfinished native operation is retired, preserving a successful checkpoint
from another conversation. Uncertain specialist/synthesis and authoritative
write outcomes remain recovery-gated. Switching provider routes starts a fresh
target thread without deleting older native data. Exact delivery has a finite
capacity and never silently summarizes or omits required history; see
[Lead session memory](../guides/agent-context.md#lead-session-memory).

The backend-selected provider/model/effort profile and provider argv are the
execution evidence shown by the terminal. Provider reply prose, including a
self-identification such as “I am Codex,” is untrusted content and cannot prove
which provider or model executed the turn. See
[Agent-context design](../design/agent-context.md#privacy-recovery-and-compatibility).

<a id="workspace-debug-log-privacy"></a>

## Workspace debug-log privacy

The internal debug writer admits only fixed operational categories, its own UTC
timestamp, an optional fixed outcome, and exact validated session,
conversation, debate, or canonical agent IDs. Error records add a safe
qualified exception type and at most the nearest 32 module/function/positive
line-number frame facts. This makes the format content-safe, not encrypted or
anonymous; identifiers and operational timing can still be sensitive.

There is no field for story prose, prompts, replies, provider payloads,
arbitrary details, or caller-supplied messages. Exception projection does not
read exception text or representation, filenames, source text, locals,
arguments, notes, causes, or contexts. Provider stdout/stderr and typed native
diagnostic bytes never enter this log. Completed Antigravity failures can expose
those bytes through the separate private [agent-exchange channel](#agent-exchange-privacy);
the fixed exception/debug-log projection does not read them. Codex native stderr
remains discarded by its adapter.

Ordinary operational records remain schema version 1. Execution-profile
records use schema version 2 and one of `execution_setting_change`,
`execution_profile_applied`, `execution_profile_confirmed`, or
`execution_profile_recovery_required`. They intentionally expose exact
sanitized provider, model, effort, each value's source, route generation,
activation state, correlation/timing, and optional thread/context state. A
startup active snapshot uses correlation `startup` and only `active_profile`;
it never invents old values. A hot transition uses `old_profile` and
`new_profile`, followed by active and first-success confirmation records.
Recovery uncertainty is error/failed.

These profile records are still content-free: they exclude story prose,
prompts, replies, request payloads, provider stderr, context-envelope contents,
credentials, and authentication output. Model identifiers and timing remain
sensitive plaintext operational metadata. Do not place secrets in model or
effort configuration.

These files are separate from conversation transcript `runtime_error` events
and their 4,096-character known-value sanitizer. `StoryRuntime` construction
retains an I/O-free handle but emits and creates nothing. Each `AgentBackend`
also retains an I/O-free workspace-bound debug writer. Backend emission begins
only after the complete configuration snapshot loads successfully. The initial
Codex executable check and snapshot-load failures precede diagnostic emission;
later constructor validation failures are eligible. CLI, controller, and smoke
emission begins only after workspace resolution or initialization.

The low-level writer suppresses ordinary `Exception` failures. CLI, backend,
runtime, controller, and disposable smoke each add a private submission guard
that catches diagnostic `BaseException`; diagnostic-handle acquisition is
guarded separately. Even a sink `KeyboardInterrupt` or `SystemExit` therefore
cannot replace the application's result, exception, cancellation, cleanup,
output, or exit status. Explicit `story smoke --workspace` is read-only and
creates no diagnostic handle or record. Implicit smoke removes its temporary
workspace and any log that was successfully written. A debug-log hint never
guarantees that a record exists.

Interactive errors after workspace resolution add exactly
`[debug log] If available, workspace-relative: debug_logs/runtime.jsonl`.
A caught noninteractive exception adds the fixed path as a second stderr line
after CLI workspace discovery succeeds, even if optional handle acquisition
failed and no record or file is available. Initialization and discovery
failures, both smoke forms, returned domain outcomes, argparse, and other
pre-workspace errors add no debug-log path. See
[Persistence](../design/persistence.md#workspace-runtime-debug-log) for the
schema, [CLI](../reference/cli.md#runtime-debug-logs) for exact output, and
[Filesystem safety](filesystem-safety.md#debug-log-filesystem-safety) for
fail-open storage behavior.

<a id="agent-exchange-privacy"></a>

## Agent-exchange privacy

`debug_logs/agent_exchanges/` is intentionally content-bearing. Its schema-v1
records can persist a provider `request`, `response`, `no_response`, or
`outcome` event, correlated by session, conversation/debate scope, canonical
story agent, provider, and physical-call exchange ID. `AgentBackend` now
automatically offers every established or contextual physical structured call
to this channel: request before invocation, valid `ProviderResult` before
accounting/decode/thread persistence, then one closed terminal attempt. A typed
completed Codex or Antigravity failure can also offer a diagnostic response
before its failure outcome, even though it has no valid `ProviderResult`.
Direct store use remains possible.

Each content value is plaintext strict UTF-8. The retained value is the longest
complete prefix at or below 262,144 bytes; its retained/full UTF-8 byte counts
and retained/full character counts distinguish a complete value from a prefix.
Character counts are Python Unicode-code-point counts for the full input and
decoded retained prefix. They do not measure grapheme clusters, user-perceived
characters, terminal columns, UTF-16 code units, or rendered width. The stored
`sha256:` digest still covers the complete UTF-8 caller value, including an
omitted suffix. It is a comparison fact, not encryption, redaction, a signature,
origin authentication, or a way to reconstruct truncated text.

Automatic request content is canonical JSON of only `call_kind`, `payload`,
`prior_thread_id`, and `schema`. A normal returned response contains
`actual_transport_utf8_bytes`, `response_text`, `thread_id`, and nullable
validated usage. A recovered Antigravity native `ERROR`, or a timeout/truncation
warning accepted with exact current-call completion proof, adds
`response_diagnostic`, preserving its completed stdout, stderr, and exit code.
A completed provider failure instead uses a response object containing
`provider_failure: true` and, when available, `response_diagnostic`, followed by a fixed
`provider_failure` outcome. Inside `response_diagnostic`, `exit_code` is the
native integer status; `stdout` and `stderr` each contain `encoding` and `data`.
Each stream uses strict `utf-8` when possible, otherwise reversible `base64`.
This is private diagnostic evidence, not a semantic result or a retry grant.

Terminal content still contains only a closed `outcome` and fixed `code`, never
arbitrary exception text. “Exact” describes the capture input subject to the
existing prefix bound and retention. Encoding does not remove sensitive data;
inspect the outer full/retained counts, truncation flag, and full-input digest
before treating nested JSON or a base64 stream as complete.

Automatic capture consumes only these typed completed-process diagnostics. It
does not import partial timed-out or cancelled streams, Antigravity's native
proof transcript or private thinking, or native login/status/catalog output.
Native login terminal bytes are never recorded. Authentication and discovery
probes retain their existing output exclusions. Invocation diagnostics receive
no semantic redaction: exact native stdout/stderr may contain provider-emitted
OAuth URLs or status, credentials, tokens, story content, environment values,
or other secrets. Capture does not independently inspect the environment,
browser, or credential store. A direct caller can also place arbitrary material
inside `content`.

Sequence values, millisecond timestamps, provider/agent/scope identities,
exchange correlation, byte counts, truncation, and digests remain sensitive
operational metadata even when retained content is empty. Character counts are
sensitive operational metadata too: they can reveal length even when byte
counts differ because of Unicode encoding. The content-bearing input and
snapshot bytes are omitted from their Python `repr`, and inventory is
content-free, but those conveniences do not scrub process memory, caller logs,
debuggers, tracebacks created outside this API, terminal output, or copies made
by a consumer.

The exchange and session directories use mode `0700`, and managed files use
`0600`; these owner permissions are not application authentication or
encryption. Protect the complete workspace, local backups, filesystem
snapshots, process memory, and any copied `AgentExchangeSnapshot.jsonl_bytes`.
Do not publish the store or attach it to an issue without reviewing every
retained line. Provider text is untrusted even when it resembles JSON,
Markdown, shell code, or instructions.

Bare `/exchanges` is the installed read-only inspection route after a session
is attached. Its transient first picker is derived only from that session's
content-free inventory; its second picker contains the complete canonical
roster. The controller then requests one exact immutable
session/scope/agent snapshot, or supplies immutable empty bytes for an agent
without retained records. It never passes a path, descriptor, or writable
handle to the required independent viewer and never invokes, retries, replays,
edits, or deletes provider work.

The viewer header deliberately exposes the attached session, exact
Conversation or Debate full ID and subject, and exact agent ID. Simple mode
shows timestamp, request type, and content, so it is an orientation view rather
than redaction, authorization, or access control. Verbose may display every
outer field. Full-text search includes all retained keys and values, including
hidden fields and bounded decoded JSON inside strings. Preview limits and
responsive clipping constrain rendering but do not redact the immutable snapshot; the
outer full/retained counts, truncation flag, and full-input digest must be
checked separately.

The isolated package neutralizes terminal and bidirectional controls and emits
only printable text, newlines, and optionally semantic SGR. Story remains sole
owner of input, output, raw mode, alternate screen, signals, geometry, and
restoration. Capable close restores the prior shell surface; ordinary-line
fallback necessarily leaves its plain frames and input in scrollback. Either
path can be retained by terminal, remote-session, accessibility, recording, or
operating-system infrastructure. Story picker state and viewer-owned drafts,
logical cursor, mode, search, match position, footer result/index, and help
state are process-local and discarded on close;
that does not erase exchange files, process memory, scrollback, screenshots, or
copied frames. See [Inspect agent exchanges](../guides/agent-exchanges.md) and
the pinned external [design system](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/ce57f560f3ca1366ea69dfe15cb1c598513e2c82/docs/design-system.md).

Retention is bounded but not secure erasure. Per-stream rotation and global
oldest-record pruning use ordinary unlink or replacement. User deletion,
quarantine, backup expiry, and filesystem free-space reuse likewise make no
secure-erasure promise; provider-native or remote copies and process/terminal
copies remain independent. There is no retention setting, redaction hook,
selective delete command, or secure-delete API.

Capture advances call-local state before each append, suppresses every capture
or storage failure, and never retries. A request failure disables the exchange;
a later failure can leave request-only or response-without-outcome history.
Consequently, a missing store, gap, exchange, response, or outcome never proves
that no provider call or result occurred. Conversely, `io_failed` does not
prove a record is absent. Do not retry provider work merely to fill diagnostics.

Completed Codex or Antigravity nonzero exits, malformed UTF-8/JSON, unknown
error status, or failed schema/identity checks can retain a diagnostic response
before the fixed failure outcome. A response-append failure still permits the
attempt and can leave a gap. Process-start failures, outer watchdog timeouts,
and other failures before a completed process supply no partial bytes through
the shared process runner; their provider-failure response contains only
`provider_failure: true` when that append succeeds. A completed Antigravity process with a native
print-timeout or truncation warning can instead retain its diagnostic envelope,
including on exit zero. That envelope does not establish a usable result;
acceptance requires independent completion evidence for the exact current call
plus ordinary validation. Cancellation uses its separate `cancelled` terminal
without a provider-failure marker. Earlier records without this response channel
can contain only request and outcome.

The narrowly recovered malformed-function-call `ERROR` retains its native
diagnostic envelope alongside the structured result, which still undergoes
ordinary domain validation. The recovery reader checks bounded current-call
native evidence without copying its transcript or private thinking. Its format
and filesystem requirements are described in the
[recovery gates](../guides/antigravity.md#recover-a-completed-native-error); they
do not attest provider tool containment. Separate live-test evidence can retain
raw native files and PTY output beyond this store's caps, so it requires its own
private directory and review before sharing. See the
[live matrix](../guides/antigravity.md#opt-in-live-conversation-matrix).

These exchange codes are diagnostic boundaries, not durable story-history
prose. `provider_invocation_failed` is intentionally coarse and does not expose
whether output-schema rejection, authentication, configuration, transport, or
another provider-side condition caused the missing result. When a completed
native response meets the authentication classifier, the narrower terminal
code is `provider_authentication_required`; its diagnostic still grants no
replay authority. Authentication and timeout remain independent facts: timeout
does not suppress sign-in assistance or that exchange classification, but it
defeats replay and continuation reuse and controls eligible read-only Lead
closure as `provider_timed_out` with `retry_lead_turn`. When a completed Lead
result falls into one of the five
deterministic rejection classes,
collaboration history instead stores the content-free
`provider_result_rejected` closure and exposes `retry_lead_turn`; it does not
copy the raw result. Eligible read-only Lead provider failures, timeouts,
cancellations, and interruptions likewise close with a typed content-free
failure. Exact legacy requests without closure expose retry, but only explicit
new user input closes them and starts a fresh turn. The original request and
failure remain history; projection, inspection, and reopening never retry.
Uncertain specialist/synthesis or authoritative write outcomes and failed
closure persistence retain their recovery gates. Diagnostic classification
alone never authorizes provider replay.

This store is independent of `debug_logs/runtime.jsonl` and
`debug_logs/provider_error_responses/`. It never imports, validates, counts,
prunes, or repairs either sibling. The runtime log remains content-safe. The
provider-error store remains the separate best-effort channel for an eligible
completed response rejected by decoding. Ordering is unchanged: the exchange
response is attempted first, decode may submit the #375 artifact, then exchange
capture attempts its rejected outcome. The stores do not import or double-write
one another. See the exact
[schema and bounds](../reference/configuration/session-storage.md#agent-exchange-storage),
[persistence lifecycle](../design/persistence.md#agent-exchange-persistence),
and [filesystem controls](filesystem-safety.md#agent-exchange-filesystem-safety).

<a id="rejected-provider-response-artifacts"></a>

## Rejected provider-response artifacts

When a completed Codex or Antigravity response is rejected during structured
or domain decoding with `BackendProtocolError`, `JSONDecodeError`, or
`UnicodeError`, the backend best-effort stores the exact
`ProviderResult.response_text` beneath the fixed workspace-relative directory
`debug_logs/provider_error_responses/`. This preserves evidence such as
malformed JSON, invalid Unicode decoding, or a domain/identity mismatch such as
an unexpected `revision_id`; it does not fix or retry the provider or runtime
failure.

This content-bearing channel is deliberately separate from
`debug_logs/runtime.jsonl`. The runtime log remains the content-safe source for
the sanitized exception type and frame metadata. The terminal and runtime log
do not render or contain the rejected body. Direct backend callers retain the
sanitized `provider_protocol_error` category; eligible Lead orchestration
projects only the fixed `provider_result_rejected` closure and
`retry_lead_turn`. No artifact path is printed.

Each complete schema-v1 JSON record contains its UTC timestamp, provider,
canonical agent and session, exactly one `debate_id` or `conversation_id`, the
fixed `structured_response_rejected` category, full response UTF-8 byte count
and SHA-256, retained UTF-8 byte count, truncation flag, and `body`. The body is
exact valid plaintext up to 262,144 UTF-8 bytes, truncated only at a complete
code-point boundary. Each serialized file is at most 2,097,152 bytes. The store
keeps at most 32 complete files and 16,777,216 serialized bytes, pruning the
oldest complete records first.

Not every provider failure creates a record. Eligible input begins only after
an adapter has returned a completed response that the shared decoder then
rejects. Successful replies, prompts and requests, stderr, raw stdout or NDJSON,
authentication or model-catalog output, credentials and environment values,
provider-native history, and pre-result nonzero exits, transport failures,
timeouts, or cancellations are excluded. Capture is best effort: contention,
an unsafe or malformed artifact namespace, unavailable safe filesystem
primitives, or another storage failure can leave the lazy directory or one
record absent without changing the original provider exception.

To inspect a possible record safely:

1. Resolve `debug_logs/provider_error_responses/` beneath the affected
   workspace. Its absence means no artifact is available; it does not narrow
   the underlying error.
2. Open the raw candidate `.json` files with a trusted plain-text editor that
   does not interpret terminal control sequences or render the content as
   Markdown or HTML. Do not execute the file, feed it to a shell, or copy its
   body into a command.
3. Match `provider`, `agent_id`, `session_id`, and the relevant `debate_id` or
   `conversation_id` to the failed operation. Do not assume the newest file or
   every provider error belongs to that operation.
4. Check `truncated`, the byte counts, and `response_sha256` before interpreting
   `body`. A truncated body is only a prefix; its digest describes the full
   response and cannot reconstruct the omitted suffix.

Treat `body` as hostile, untrusted provider-controlled plaintext even when it
looks like JSON, Markdown, HTML, instructions, or terminal output. Workspace
protection and backup decisions must include these artifacts. With the runtime
stopped and ordinary filesystem policy permitting, users may delete complete
artifact `.json` files; ordinary deletion and retention pruning are not secure
erasure. Do not hand-edit the store's `.lock` or `.pending` leaves. There is no
configuration switch, deletion command, or new public backend API for this
internal compatibility-preserving capture.

<a id="runtime-error-diagnostics"></a>

## Transcript and backend diagnostic redaction

Concrete `AgentBackend` keeps exception text and runtime JSONL diagnostics fixed
and content-safe. For example, Codex nonzero exit raises
`BackendProtocolError` with exact `Codex CLI exited with status N`;
Antigravity uses fixed provider-specific diagnostics for capability, transport,
protocol, and timeout failures. A completed Codex or Antigravity
`ProviderInvocationError` can carry a private typed `response_diagnostic` with
stdout/stderr bytes and exit status for the optional agent-exchange capture
path. Those bytes do not enter exception text, semantic collaboration history,
or `debug_logs/runtime.jsonl`; inspect retained exchanges for available native
detail without replaying the failed call. Absence of a record does not prove
that no output existed.

If a completed response instead reaches and fails the shared decoder, its body
may be available through the separate
[rejected provider-response artifact](#rejected-provider-response-artifacts).
That best-effort write does not change the exception seen by direct
`AgentBackend` callers or add response content to runtime JSONL.

Provider-controlled revision IDs, duplicate JSON keys, and disallowed/duplicate
core-update filenames produce fixed protocol copy without echoing values. Other
concrete backend protocol diagnostics use fixed categories or runtime-owned
metadata and do not echo provider response values. Progress exposes fixed facts;
sources intentionally reveal subject/citation, never
query/event/candidate/critique/error/model prose.

The retired schema-v1 direct-turn consumer used the following failure-storage
boundary. It is retained only to interpret preexisting records. Current Lead
input writes schema-v2 collaboration history through its separate authority;
it does not revive this legacy failure transcript. The legacy known-value
sanitizer:

- replaces current/context/retrieval variants with `<redacted>` and workspace
  roots with `<workspace>`;
- removes control/format characters, collapses whitespace, and trims;
- falls back to `ExceptionType: unavailable diagnostic`; and
- caps output at 4,096 characters with an in-limit truncation marker.

`asyncio` cancellation stores fixed
`CancelledError: conversation turn was cancelled`. Provider
`KeyboardInterrupt` and `SystemExit` store no transcript outcome or exception
value. Their separate runtime debug submission admits only the fixed operational
fields and safe exception type/frame projection described above, and its failure
does not replace the original exception. Only supplied/known variants are
redacted; this is not a general secret detector. Replies are unchanged. Terminal
summaries use a separate boundary.

## Provider processes and credentials

Codex runs without shell evaluation in isolated staging: read-only sandbox, no
approvals, ignored project/user configuration, and disabled shell, web, app,
and multi-agent tools. Installation-level or vendor-global instructions can
still apply, and the vendor may retain remote conversations.

Antigravity also runs without shell evaluation. The backend passes the exact
confidential story request JSON as one `--print` argument, uses
`--input-format text --output-format json`, and sends empty stdin. The full
request can therefore appear in local process listings and inspection tools,
including any story text, drafts, references, and retrieval observations it
contains. CLI 1.1.27 ignored raw text stdin in transport probes, so this adapter
uses the prompt argument. OS argument-size limits may reject large requests
before process creation with a typed start failure and no submitted transport
count.

It stages and selects a custom main agent beneath an isolated provider
workspace, uses `--new-project` for fresh history or the exact retained
`--conversation` ID for resume, and passes sandbox, plan-mode, and
disabled-slash flags without an approval bypass. It never registers the
canonical story root as the provider project. CLI 1.1.27 warns that plan mode
has no effect when slash commands are disabled. The argument list also contains
the output schema, isolated state path, optional model/effort, and conversation
ID. The child inherits the runtime environment. Keep credentials out of request
content, model names, paths, configuration fields, shell history, and
diagnostics; inherited variables must be suitable for a provider subprocess.

Workspace fallback and per-agent Codex model/reasoning values are plaintext
configuration. An `AgentBackend` passes each bound agent's effective non-null
values to the local Codex process as `--model` and
`--config model_reasoning_effort="LEVEL"` arguments;
they are not inserted into request payloads, definitions, fingerprints, or
provider-thread identity. Antigravity model/effort values are likewise
plaintext local process configuration and appear in arguments when set. Treat
all provider settings as locally exposed process configuration, not as a place
to store secrets.

Antigravity's isolated provider-native state is also plaintext and is retained
when a backend closes. Taint or definition-fingerprint mismatch starts a fresh
provider conversation without deleting prior provider data. On supported POSIX
systems, the adapter best-effort requests owner-only (`0700`) modes for its
Antigravity state root and adapter-owned definition and session directory
chains, and it rejects a detected link or non-directory in those components
before a provider launch. It does not recursively change the backend-owned
containing directory or provider-created files. Adapter-owned staged and
workspace custom-agent definition leaves are verified as regular and singly linked
when present, then installed through an exclusively created same-directory
temporary with requested `0600` mode and atomic replacement. Detected symbolic
links, non-regular entries, and multiply linked leaves are preserved and
rejected before provider launch.
Unsupported platforms, including Windows, and ordinary mode-setting failures
retain platform behavior; parent-directory traversal is not descriptor-anchored,
so these protections are neither application access control nor race-proof
containment. Protect the complete workspace and provider-state paths with
OS-level controls; the runtime provides neither encryption nor secure erase.

The custom definition declares only `finish`, but earlier CLI 1.1.27 stream
probes advertised a 57-tool main-agent catalog. Neither that declaration nor
the launch flags prove capability removal. The JSON result has no `init` or
step metadata, so Story cannot attest selected agent, cwd, permissions, or
tool/subagent behavior from the output. Story requires one valid UTF-8 JSON
object with a safe conversation ID matching the retained ID on resume, object
`structured_output`, and an exact object echo of the rendered `json_schema`.
Status must be native `SUCCESS` or the known malformed-function-call `ERROR`
after exit zero and matching current-call native finish/completion proof.
Missing, unsafe, stale, or unknown proof remains failure; recovery adds no model
retry. Duplicate fields, non-finite numbers, and trailing data are rejected.
There is no response-string fallback. Shared correlation, evidence,
artifact-authority, and confirmation checks still apply to native success and
recovered results. See the sanitized command and JSON shape in the
[Antigravity guide](../guides/antigravity.md#sanitized-provider-call-and-json-result).

Use interactive `/auth` or the startup picker to enter native sign-in. Never put
credentials in story content, configuration, filenames, prompts, replies,
transcripts, or logs. For Antigravity installation and native authentication,
follow the [Antigravity guide](../guides/antigravity.md); do not copy
authentication material into story configuration. Public models have no
hidden-reasoning field; ordinary returned content is visible and may persist.
`story smoke` uses synthetic data and neither provider.
