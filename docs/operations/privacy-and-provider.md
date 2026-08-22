# Privacy and provider exposure

> **Availability:** these controls cover local reads, advisory turns, and
> retrieval-enabled turns, management commands, focused-context consensus, and
> the Python/internal explicit-reference authorization foundation. They do not
> make Codex, its executable, workspace, or terminal trusted, and explicit
> references are not yet connected to provider calls or story agents.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored or displayed failure text.

## Plaintext local data

There is no encryption, access control, secure erase, or retention policy.
Documents, debate/context records, provider metadata, subjects, transcripts,
scrollback, manuscripts, artifact catalogs, commit provenance, transaction
journals, reference-operation evidence, and recovery markers rely on
OS/terminal-service protection.

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

## Explicit-reference data boundary

Reference indicators and durable authorization history are content-free. The
history records ordered canonical paths and ranges, catalog provenance,
selected hashes and counts, agent/invocation correlation, lifecycle states,
checkpoints, and fixed outcomes. It excludes artifact bytes, parser source and
provider projections, prompts, provider results, and diagnostic values derived
from content. Authorization errors use fixed coded messages without echoing a
path or selected text.

Selected artifact bytes exist only in closeable, nonserializable in-process
authorization and delivery carriers whose `repr` hides content. Closing drops
the carrier's references to its byte tuples; Python and the operating system do
not provide a secure memory-zeroing guarantee. The durable evidence remains
plaintext and has no encryption, access control, secure erase, or retention
policy.

The repository writes `delivered` evidence before returning selected bytes and
can record that its caller reported a result. Neither event proves that a
provider or story agent received, read, understood, or used the text. Baseline
core context does not satisfy explicit-reference evidence. #189 and #190 must
connect the repository protocol to provider actions and agent enforcement;
#164 must connect references to the shell. Until then, `${...}`, `$`, and `$$`
are not operational user commands.

## Provider disclosure

A Python one-step call sends current text and bounded complete turns. Retrieval
starts there, then may add authorized active-conversation hits and complete
user/reply pairs from the current session. It cannot request archives, another
session/workspace, arbitrary IDs, runtime errors, private state, tools, or native
data. Observations are framed as untrusted story data.

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

<a id="runtime-error-diagnostics"></a>

## Diagnostic redaction

Concrete `AgentBackend` drains nonzero Codex stderr but never decodes, exposes,
or persists it. It raises `BackendProtocolError` with exact
`Codex CLI exited with status N`. Python callers retain exception type/status,
but lose stderr detail; reproduce the provider failure directly if that detail
is required.

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

## Codex and credentials

Codex runs without shell evaluation in isolated staging: read-only sandbox, no
approvals, ignored project/user configuration, and disabled shell, web, app,
and multi-agent tools. Installation-level or vendor-global instructions can
still apply, and the vendor may retain remote conversations.

Workspace fallback and per-agent Codex model/reasoning values are plaintext
configuration. An `AgentBackend` passes each bound agent's effective non-null
values to the local Codex process as `--model` and
`--config model_reasoning_effort="LEVEL"` arguments;
they are not inserted into request payloads, definitions, fingerprints, or
provider-thread identity. Treat them as locally exposed process configuration,
not as a place to store secrets.

Use `codex login` or process-environment `CODEX_API_KEY`. Never put credentials
in story content, configuration, filenames, prompts, replies, transcripts, or
logs. Public models have no hidden-reasoning field; ordinary returned content is
visible and may persist. `story smoke` uses synthetic data and no provider.
