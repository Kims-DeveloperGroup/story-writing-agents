# Privacy and provider exposure

> **Availability:** these controls cover local reads, advisory turns, and
> retrieval-enabled turns, management commands, and selected-context consensus.
> They do not make Codex, its executable, workspace, or terminal trusted.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored or displayed failure text.

## Plaintext local data

There is no encryption, access control, secure erase, or retention policy.
Project documents, debate records, context sidecars, provider metadata,
subjects, transcripts, terminal scrollback, and manuscripts rely on operating-
system and terminal-service protection.

Blackboards can contain complete input, drafts, reviews, and errors. Owner state
is narrower, but provider output is not generally scrubbed. Archive does not
delete or conceal a conversation. Transcripts retain visible messages, replies,
and runtime errors.

## Local management, search, and reads

`/search` scans active subjects and user/reply events; `/transcript` can display
active or archived event metadata and full content. Selected `/history` displays
all or a newest suffix from one full snapshot, including runtime errors,
unmatched users, and whitespace. `/status` exposes workspace/session and
selected subject/context/run metadata. Safe shell `/inspect` exposes only
whitelisted lifecycle/artifact metadata and context identity/mode/counts—never
raw messages, candidates, errors/actions, context content, or provider fields.

Help, status, history, and inspection make no provider/network/binding call or
story-data write. Directives are also provider-free but atomically update only
validated session overrides. These commands otherwise preserve attachment,
selection, target, thread, catalog, transcript, debate, artifact, and core data.

Provider-free is not exposure-free. Subjects, snippets, identities, safe
metadata, errors, whitespace, and multiline content can reach the output
callback and scrollback.
Terminal escaping is not redaction or anonymization. See
[Conversation search](../reference/conversation-search.md) and
[Terminal safety](terminal-safety.md).

## Provider disclosure

A direct Python one-step call sends current text and bounded complete turns from
its conversation. A retrieval turn begins with the same data, then may add
runtime-authorized active-conversation search hits and complete user/reply pairs
from the current story session. It cannot request archives, another session or
workspace, arbitrary IDs, runtime errors, private state, tools, or native data
access. Search/read observations are explicitly framed as untrusted story data.

The agent definition separately supplies that role's allowlisted core documents.
Debates, manuscripts, core updates, and owner state are not retrieval memory.
Direct routing selects one responder in a shared transcript, not a private channel.

Included consensus sends the captured selected-transcript event content in
every agent request made across drafting, audits, proposals, synthesis, and
finalization. Every request receives the same bounded snapshot. Fresh and
legacy modes omit the `conversation_context` field entirely.

Compatible provider threads are keyed by story session, backend, and agent.
Within that session they can span subject conversations and debate calls;
different visible context does not erase provider-private history. Detach, end,
and backend close preserve thread references, so fresh or omitted context cannot
make the provider forget earlier content.

<a id="runtime-error-diagnostics"></a>

## Diagnostic redaction

The backend redacts current context plus retrieval actions, queries, results,
subjects, snippets, events, citations, canonical frames, and JSON-escaped
variants from process, timeout, and protocol diagnostics. Retrieval progress
shows only counts and fixed labels; source lines intentionally reveal subject
and stable citation after the visible answer, never query, snippet, or event
prose. Consensus progress still excludes candidates, critiques, raw exceptions,
result errors/actions, and model prose.

After an advisory user event is durable, `StoryRuntime` stores an ordinary
failure as its type plus sanitized detail. The sanitizer:

- replaces current/context/retrieval variants with `<redacted>` and workspace
  roots with `<workspace>`;
- removes control/format characters, collapses whitespace, and trims;
- falls back to `ExceptionType: unavailable diagnostic`; and
- caps output at 4,096 characters with an in-limit truncation marker.

Cancellation uses a fixed diagnostic. Replies are unchanged, and sanitization
is not a general secret detector. Terminal summaries use a separate shorter
boundary and are not transcript events.

## Codex and credentials

Codex runs without shell evaluation in isolated staging: read-only sandbox, no
approvals, ignored project/user configuration, and disabled shell, web, app,
and multi-agent tools. Installation-level or vendor-global instructions can
still apply, and the vendor may retain remote conversations.

Use `codex login` or process-environment `CODEX_API_KEY`. Never put credentials
in story content, configuration, filenames, prompts, replies, transcripts, or
logs. Public models have no hidden-reasoning field; ordinary returned content is
visible and may persist. `story smoke` uses synthetic data and no provider.
