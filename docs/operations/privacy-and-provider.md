# Privacy and provider exposure

> **Availability:** these controls cover local reads, advisory turns, and
> retrieval-enabled turns, management commands, focused-context consensus, and
> runtime explicit-reference authorization and backend enforcement, plus direct
> Python conversation archive values, filesystem transfer, and optional SQLite
> conversation/search storage, provider-free desired selection, and
> conversation migration.
> They do not make either provider, its executable, the workspace, or the
> terminal trusted.
> Inline canonical references in provider-bound prompt text are active.
> `$`/`$$` discovery and viewer attachment are active in eligible interactive
> prompt spans; they remain local until the user normally submits the prompt.
> Settings Model/Effort catalog discovery is also bounded and local; only a
> user's selected opaque value can be persisted, never provider catalog data.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored or displayed failure text.

## Plaintext local data

There is no encryption, application access control, or secure erase.
Documents, debate/context records, provider metadata, subjects, transcripts,
scrollback, manuscripts, artifact catalogs, commit provenance, transaction
journals, migration checkpoints, reference-operation evidence, and recovery
markers have no application-managed retention policy and rely on
OS/terminal-service protection. The workspace debug log is the one
bounded-retention channel described below; it is still plaintext.

Blackboards can contain input, drafts, reviews, and errors. Provider output is
not generally scrubbed; catalog archive status neither deletes nor conceals
conversations; transcripts retain messages, replies, and runtime errors.

<a id="conversation-archive-privacy"></a>

### Conversation archive exposure

Canonical conversation archive values and serialized bytes contain exact
plaintext subjects, identities, timestamps, statuses, user messages, agent
replies, and runtime-error content. Serialization performs no redaction or
encryption. Its SHA-256 digest detects modification but provides no secrecy,
signature, origin authentication, or access control.

Filesystem export deliberately excludes debate/runtime logs, debate
state/results/context, credentials, provider and agent state, prompts,
configuration/defaults, storage activation and RAG state, manuscripts, and
writing artifacts. Those exclusions make the value conversation-only; they do
not make its included transcript content safe to disclose. Import copies the
included plaintext and neither deletes excluded state nor securely erases a
source or caller-owned archive copy.

The runtime owns no archive file or retention policy. A caller that stores,
transmits, logs, or otherwise retains the returned bytes owns protection and
cleanup for every copy. See
[Canonical conversation archives](../reference/conversation-archives.md#archive-non-goals).

The optional `discussion_logs/conversation_storage.sqlite3` database is also
plaintext and can coexist with the filesystem catalog and transcripts, leaving
two independent copies under caller control. It contains exact subjects,
identities, timestamps, status, and transcript content plus derived normalized
subjects, normalized transcript-search text, lexical generation, and private
turn slots. Protect the database, any live rollback journal, external backups,
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

Provisioning is creation, not migration or deletion; archive export/import and
the explicit storage migration workflow copy plaintext rather than securely
erasing a source. The canonical archive payload contains exact metadata and
events in memory, and any persistence or transmission performed by its caller
is outside the adapters' controls.

`story storage migrate` and `story storage verify` emit one content-free report
containing backend names, fixed outcomes/failures, parity, checkpoint state, and
aggregate classification/source counts. They never print subjects, conversation
IDs, event text, paths, malformed values, or FTS content and never create a
runtime debug record. This output boundary does not make the copied destination
confidential: migration can leave the same plaintext conversations in both
stores, never deletes the source, and does not impose a retention policy.

`story storage use` emits a different counts-free content-free report containing
only fixed operation/outcome/backend, installation/provisioning/parity,
configuration/activation, failure, and schema values. It never prints
conversation identities, subjects, events, paths, exceptions, installer
output, provider data, or parity counts and never creates a runtime debug
record. An explicitly run installer instead writes its native stdout and
stderr to the CLI's stderr stream, outside that JSON boundary.

Retained migration checkpoints contain session and conversation IDs, a SHA-256
fingerprint of the canonical source archive, source conversation/event counts,
and completed/total progress. They omit subjects, timestamps, statuses, events,
runtime errors, and FTS data, but identities, counts, timing of file updates, and
the fingerprint can still be sensitive. Protect both per-direction checkpoint
files with the workspace; terminal completion is audit state, not secure
erasure or authorization.

## Local management, search, and reads

`/search` shows active subjects/user/replies; `/transcript` shows active or
archived metadata/content; `/history` shows one full or newest-suffix snapshot,
including errors, unmatched users, and whitespace. `/status` exposes
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

Direct SQLite catalog, transcript, context, and exact literal-search operations
are provider-free and add no network call, authentication, RAG, or new citation
mechanism. Optional FTS5 hint lookup adds no network path and never becomes
search truth. Returned Python values, application logs, process memory, and
caller-created backups can still move or expose plaintext beyond the
workspace. Storage migration/verification is also provider-free and dispatches
before authentication, broader settings/context, backend, runtime, session, or
debug-log construction. It still performs local archive reads and, outside
dry-run/verify, destination and checkpoint writes. `story storage use` likewise
dispatches provider-free before debug logging and provider/runtime construction,
but can explicitly run a host installer, provision an empty database, read both
canonical stores for parity, and write desired configuration. `/settings`
uses the same storage selection boundary only on its full Apply Changes path,
after the separate provider-auth gate. A Message-details-only Apply Changes
never authenticates or enters storage selection. The next shell activates the
selected ready bundle before provider authentication.

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

Before provider delivery, selected artifact byte tuples are held in closeable,
nonserializable in-process authorization, delivery, and result carriers whose
`repr` hides content. The backend transiently decodes them for the one provider
payload described below. Closing drops the carriers' references to their byte
tuples; Python and the operating system do not provide a secure memory-zeroing
guarantee. The durable evidence remains plaintext and has no encryption,
access control, secure erase, or retention policy.

Interactive source entries contain canonical paths only. Listing reads path and
type metadata, not file bytes. Opening an artifact
loads verified plaintext into local process memory and renders it in the
terminal. A capable viewer uses an alternate screen, but closing it is cleanup,
not secure erasure from the process, emulator, multiplexer, or remote terminal.
Fallback prints the complete numbered content into scrollback. Source-list/viewer
activity is not sent to a provider and creates no durable reference-operation
evidence; only a later submitted `${...}` indicator starts the runtime path.
UI error output is fixed and content-free.

For an optional referenced backend call, the first provider payload contains
only fixed rules, exact operation/invocation correlation, and the complete
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
Baseline core context cannot satisfy explicit-reference evidence.

Closing local authorization, delivery, and result carriers drops their Python
references but offers neither secure memory erasure nor provider erasure. Once
the substantive request is sent, provider-private or remote systems may retain
its context under their own policy. `StoryRuntime` now constructs this path
from inline `${...}` indicators. The interactive controller can now construct
those same indicators through `$`/`$$` discovery and viewer selection without
copying selected content into the prompt or work history.

## Provider disclosure

A runtime-built substantive call sends current text where applicable plus one
schema-v1 `story_session_context` envelope. That envelope exposes the receiving
agent's newest owner outcomes (at most 24 entries and 32,768 compact UTF-8
bytes), the focused context in included/fresh/omitted mode, and canonical bound
core-document path/hash identities. It does not copy core bytes; those are
already separately bound into the definition. A direct Python request whose
optional envelope is `None` keeps the legacy focused-context/owner-state
payload.

Retrieval starts with that bounded envelope, then may add authorized active-
conversation hits and complete user/reply pairs from the current session. It
cannot request archives, another session/workspace, arbitrary IDs, runtime
errors, private state, tools, or native data. Observations are framed as
untrusted story data.
Continuations do not repeat the question or envelope.

Agent definitions separately supply present safe current allowlisted core
documents. A manual, pre-provenance, published, or hash-divergent safe file may
therefore reach the provider in a later definition; this does not create
publication provenance. The runtime does not delete those bytes. Retrieval
actions do not search debates, manuscripts, core updates, or owner state;
bounded owner outcomes reach the provider only through the continuity envelope.
Direct routing selects a shared-transcript responder, not a private channel.

Included consensus sends one focused-transcript snapshot through every phase.
Fresh mode keeps the conversation identity but omits its transcript;
context-omitted mode keeps neither. Both still carry bounded owner outcomes and
core identities, so a fresh provider-native thread is not a fresh story
session. The envelope intentionally omits another agent's owner outcomes,
events outside the bounded window, provider-hidden history, and artifact bytes.

Provider threads are keyed by session/backend/agent and can span conversations
and debates. Changed visible context does not erase private history. Session
switch, pause, quit, and close preserve compatible thread references;
fresh/omitted context cannot make that provider forget. A provider-route switch
instead advances generation and starts a fresh target-provider thread without
deleting older native data.

The backend-selected provider/model/effort profile and provider argv are the
execution evidence shown by the terminal. Provider reply prose, including a
self-identification such as “I am Codex,” is untrusted content and cannot prove
which provider or model executed the turn.

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
arguments, notes, causes, or contexts. The concrete backend continues to drain
and discard nonzero provider stderr without decoding it, so that stream is not
available to this log.

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
runtime, controller, and smoke each add a private submission guard that catches
diagnostic `BaseException`; diagnostic-handle acquisition by CLI and smoke paths
is guarded separately. Even a sink `KeyboardInterrupt` or `SystemExit` therefore
cannot replace the application's result, exception, cancellation, cleanup,
output, or exit status. Explicit smoke retains any best-effort records
successfully written; implicit smoke removes its temporary workspace and any log
that was successfully written. A hint or retained explicit workspace therefore
does not guarantee that a record exists.

Interactive errors after workspace resolution add exactly
`[debug log] If available, workspace-relative: debug_logs/runtime.jsonl`.
A caught noninteractive exception adds the fixed path as a second stderr line
after CLI workspace initialization or discovery succeeds, even if optional
handle acquisition failed and no record or file is available. Initialization
and discovery failures, implicit smoke, returned domain outcomes, argparse, and
other pre-workspace errors add no debug-log path. See
[Persistence](../design/persistence.md#workspace-runtime-debug-log) for the
schema, [CLI](../reference/cli.md#runtime-debug-logs) for exact output, and
[Filesystem safety](filesystem-safety.md#debug-log-filesystem-safety) for
fail-open storage behavior.

<a id="runtime-error-diagnostics"></a>

## Transcript and backend diagnostic redaction

Concrete `AgentBackend` drains nonzero provider stderr but never includes it in
the raised or persisted diagnostic. For example, Codex nonzero exit raises
`BackendProtocolError` with exact `Codex CLI exited with status N`;
Antigravity uses fixed provider-specific diagnostics for capability, transport,
protocol, and timeout failures. Python callers retain
the exception category, but lose stderr detail; reproduce the provider failure
outside this runtime if that detail is required.

Provider-controlled revision IDs, duplicate JSON keys, and disallowed/duplicate
core-update filenames produce fixed protocol copy without echoing values. Other
concrete backend protocol diagnostics use fixed categories or runtime-owned
metadata and do not echo provider response values. Progress exposes fixed facts;
sources intentionally reveal subject/citation, never
query/event/candidate/critique/error/model prose.

Separately, after a durable advisory user event, an ordinary `Exception` from a
`StoryRuntime` turn attempts to store failure type plus sanitized detail in the
transcript. Its known-value sanitizer:

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

Antigravity also runs without shell evaluation. The backend sends confidential
story request JSON through standard input and requests streamed JSON output. It
disables slash commands, selects plan mode, applies sandbox controls, and does
not request an approval bypass. The process argument list still contains
runtime-owned output schema, the isolated provider-state path, optional
model/effort, and an optional provider conversation ID. The child process
inherits the runtime environment. Do not place credentials in model names,
paths, configuration fields, shell history, or diagnostics, and start the
runtime only from an environment whose inherited variables are suitable for a
provider subprocess.

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
workspace `AGENTS.md` rules leaves are verified as regular and singly linked
when present, then installed through an exclusively created same-directory
temporary with requested `0600` mode and atomic replacement. Detected symbolic
links, non-regular entries, and multiply linked leaves are preserved and
rejected before provider launch.
Unsupported platforms, including Windows, and ordinary mode-setting failures
retain platform behavior; parent-directory traversal is not descriptor-anchored,
so these protections are neither application access control nor race-proof
containment. Protect the complete workspace and provider-state paths with
OS-level controls; the runtime provides neither encryption nor secure erase.

Use interactive `/auth` or the startup picker to enter native sign-in. Never put
credentials in story content, configuration, filenames, prompts, replies,
transcripts, or logs. For Antigravity installation and native authentication,
follow the [Antigravity guide](../guides/antigravity.md); do not copy
authentication material into story configuration. Public models have no
hidden-reasoning field; ordinary returned content is visible and may persist.
`story smoke` uses synthetic data and neither provider.
