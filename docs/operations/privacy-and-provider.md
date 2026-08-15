# Privacy and provider exposure

> **Availability:** these controls cover local reads, advisory turns, and
> selected-context consensus. They do not make Codex, its executable, workspace,
> or terminal trusted.

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

## Local search and reads

`/search` scans active subjects and user/reply events; `/transcript` can display
active or archived event metadata and full content. They make no provider,
network, process, or binding call and change no selection, target, thread,
catalog, transcript, debate, artifact, or core document.

Provider-free is not exposure-free. Subjects, snippets, identities, errors,
whitespace, and multiline content can reach the output callback and scrollback.
Terminal escaping is not redaction or anonymization. See
[Conversation search](../reference/conversation-search.md) and
[Terminal safety](terminal-safety.md).

## Provider disclosure

An advisory call sends current text and bounded complete turns from the selected
conversation to one agent. It excludes other conversations, debates,
manuscripts, core updates, and owner state. The agent definition separately
provides that role's allowlisted core documents. Direct routing selects one
responder in a shared transcript; it is not a private channel.

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

The backend treats included context content as sensitive when sanitizing
process, timeout, and protocol diagnostics. Trusted progress and shell results
show only typed metadata and fixed labels—never context, candidates, critiques,
raw exceptions, result error/action text, or model prose.

After an advisory user event is durable, `StoryRuntime` stores an ordinary
failure as its type plus sanitized detail. The sanitizer:

- replaces current/context variants with `<redacted>` and workspace-root forms
  with `<workspace>`;
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
