# Privacy and provider exposure

> **Availability:** these controls cover local reads, advisory turns, and
> retrieval-enabled turns, management commands, and focused-context consensus.
> They do not make either provider, its executable, the workspace, or the
> terminal trusted.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored or displayed failure text.

## Plaintext local data

There is no encryption, access control, secure erase, or retention policy.
Documents, debate/context records, provider metadata, subjects, transcripts,
scrollback, and manuscripts rely on OS/terminal-service protection.

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

Help/status/history/inspection are provider-free and read-only. Directives are
provider-free but atomically update validated overrides. Other scope, thread,
catalog, transcript, debate, artifact, and core state is preserved.

Provider-free is not exposure-free: subjects, snippets, metadata, errors, and
content reach callbacks/scrollback. Lobby/session orientation intentionally
prints the normalized workspace root; unexpected diagnostic paths do not.
Terminal escaping is not redaction. See
[Conversation search](../reference/conversation-search.md) and
[Terminal safety](terminal-safety.md).

## Provider disclosure

A Python one-step call sends current text and bounded complete turns. Retrieval
starts there, then may add authorized active-conversation hits and complete
user/reply pairs from the current session. It cannot request archives, another
session/workspace, arbitrary IDs, runtime errors, private state, tools, or native
data. Observations are framed as untrusted story data.

Agent definitions separately supply allowlisted core documents. Debates,
manuscripts, core updates, and owner state are not retrieval memory. Direct
routing selects a shared-transcript responder, not a private channel.

Included consensus sends one focused-transcript snapshot through every phase.
Fresh and context-omitted modes omit `conversation_context` entirely.

Provider threads are keyed by session/backend/agent and can span conversations
and debates. Changed visible context does not erase private history. Switch,
pause, quit, and close preserve thread references; fresh/omitted context cannot make the
provider forget.

<a id="runtime-error-diagnostics"></a>

## Diagnostic redaction

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

Separately, after a durable advisory user event, generic `StoryRuntime`
persistence stores failure type plus sanitized detail. Its known-value sanitizer:

- replaces current/context/retrieval variants with `<redacted>` and workspace
  roots with `<workspace>`;
- removes control/format characters, collapses whitespace, and trims;
- falls back to `ExceptionType: unavailable diagnostic`; and
- caps output at 4,096 characters with an in-limit truncation marker.

Cancellation stores fixed `CancelledError: conversation turn was cancelled`.
Only supplied/known variants are redacted; this is not a general secret detector.
Replies are unchanged. Terminal summaries use a separate boundary.

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
provider conversation without deleting prior provider data. Protect the
workspace and provider-state paths with OS-level controls; the runtime provides
neither encryption nor secure erase.

Use `codex login` or process-environment `CODEX_API_KEY`. Never put credentials
in story content, configuration, filenames, prompts, replies, transcripts, or
logs. For Antigravity installation and browser/SSH authentication, follow the
[Antigravity guide](../guides/antigravity.md); do not copy authentication
material into story configuration. Public models have no hidden-reasoning
field; ordinary returned content is visible and may persist. `story smoke`
uses synthetic data and neither provider.
