# Privacy and provider exposure

> **Availability:** these controls cover local reads, advisory turns, and
> retrieval-enabled turns, management commands, focused-context consensus, and
> runtime explicit-reference authorization and backend enforcement.
> They do not make either provider, its executable, the workspace, or the
> terminal trusted.
> Inline canonical references in provider-bound prompt text are active.
> `$`/`$$` discovery and viewer attachment are active in eligible interactive
> prompt spans; they remain local until the user normally submits the prompt.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored or displayed failure text.

## Plaintext local data

There is no encryption, application access control, or secure erase.
Documents, debate/context records, provider metadata, subjects, transcripts,
scrollback, manuscripts, artifact catalogs, commit provenance, transaction
journals, reference-operation evidence, and recovery markers have no
application-managed retention policy and rely on OS/terminal-service
protection. The workspace debug log is the one bounded-retention channel
described below; it is still plaintext.

Blackboards can contain input, drafts, reviews, and errors. Provider output is
not generally scrubbed; archive neither deletes nor conceals conversations;
transcripts retain messages, replies, and runtime errors.

## Local management, search, and reads

`/search` shows active subjects/user/replies; `/transcript` shows active or
archived metadata/content; `/history` shows one full or newest-suffix snapshot,
including errors, unmatched users, and whitespace. `/status` exposes
workspace/session/subject/context/run metadata. Session status shows durable
state, process access, defaults, current attachment/focus, readiness, and next
action, but never owner hostname, PID, or claim token. `/inspect` whitelists
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

## Explicit-reference data boundary

Reference indicators and durable authorization history are content-free. The
history records ordered canonical paths and ranges, catalog provenance,
selected hashes and counts, agent/invocation correlation, lifecycle states,
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

Interactive catalog entries contain canonical paths only. Opening an artifact
loads verified plaintext into local process memory and renders it in the
terminal. A capable viewer uses an alternate screen, but closing it is cleanup,
not secure erasure from the process, emulator, multiplexer, or remote terminal.
Fallback prints the complete numbered content into scrollback. Catalog/viewer
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

A no-reference Python one-step call sends current text and bounded complete
turns. Retrieval starts there, then may add authorized active-conversation hits
and complete user/reply pairs from the current session. It cannot request
archives, another session/workspace, arbitrary IDs, runtime errors, private
state, tools, or native data. Observations are framed as untrusted story data.

Agent definitions separately supply only current provenance-backed allowlisted
core documents. A generic manual edit remains on disk but is omitted after its
hash diverges; the runtime does not send stale bytes or delete them. Debates,
manuscripts, core updates, and owner state are not retrieval memory. Direct
routing selects a shared-transcript responder, not a private channel.

Included consensus sends one focused-transcript snapshot through every phase.
Fresh and context-omitted modes omit `conversation_context` entirely.

Provider threads are keyed by session/backend/agent and can span conversations
and debates. Changed visible context does not erase private history. Switch,
pause, quit, and close preserve thread references; fresh/omitted context cannot make the
provider forget.

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

Use `codex login` or process-environment `CODEX_API_KEY`. Never put credentials
in story content, configuration, filenames, prompts, replies, transcripts, or
logs. For Antigravity installation and browser/SSH authentication, follow the
[Antigravity guide](../guides/antigravity.md); do not copy authentication
material into story configuration. Public models have no hidden-reasoning
field; ordinary returned content is visible and may persist. `story smoke`
uses synthetic data and neither provider.
