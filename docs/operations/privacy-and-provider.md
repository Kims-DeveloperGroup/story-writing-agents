# Privacy and provider exposure

> **Availability:** these controls describe the implemented runtime, including
> local interactive search and transcript reads. They reduce exposure but do
> not make Codex, its executable, the workspace, or terminal a trusted boundary.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a provider request; **provider-private history**—vendor thread state;
**diagnostic**—stored failure text; **credential**—provider authentication.

## Local data is plaintext

There is no encryption, access control, secure erase, or retention policy.
Project documents, state, provider metadata, subjects, transcripts, search
results, terminal scrollback, and manuscripts are protected only by the
operating system and any surrounding terminal service.

The debate blackboard can contain complete input, drafts, reviews, errors, and
commit metadata. Owner state is narrower, but provider output is not scrubbed.
Archive does not delete or conceal a conversation. Transcripts retain exact
visible messages, replies, and runtime errors; direct event-store callers must
redact their own error text.

## Local search and transcript reads

After attachment, `/search` reads active subjects plus user/reply events and
may display normalized snippets and stable citations. `/transcript` can
explicitly display active or archived event metadata and complete content.
Neither needs a selected conversation.

These commands make no provider, network, process, or binding call. They do not
change conversation selection, sticky target, provider threads, catalog,
transcript, CLI context, debate/manuscript artifacts, or workspace core/index
state. Search is a bounded local scan with no RAG, semantic retrieval, or
persisted index; each transcript command is a fresh local read.

Provider-free does not mean exposure-free. Subjects, snippets, sender and
recipient IDs, turn IDs, runtime errors, whitespace, and multiline content can
reach the output callback and scrollback. Display escaping neutralizes terminal
controls but is not redaction or anonymization. Review the exact
[search contract](../reference/conversation-search.md) and
[terminal threat boundary](terminal-safety.md) before displaying sensitive
material.

## What an advisory turn sends

`StoryRuntime.converse()` sends current text and bounded complete-turn context
from the addressed conversation to one agent. Visible context excludes other
conversations, debates, manuscripts, core updates, and owner state. The agent
definition separately provides that role's allowlisted core files. See the
[provider handoff](../design/prompts-and-provider.md#conversation-turn-flow).

Ordinary, `@AGENT`, and `/say` turns can contact the provider and append the
selected transcript. `/agents`, target changes, conversation management,
search, and transcript reads are provider-free. Direct routing is not private:
it chooses one responder in a shared conversation, and later agents can receive
that bounded context.

Compatible provider threads are keyed by session, backend, and agent, so they
can span conversations and debates. A different conversation separates visible
event context, not provider-private history. The provider may remember content
absent from the visible payload.

<a id="runtime-error-diagnostics"></a>

## Runtime-error diagnostics

After a user event is durable, `StoryRuntime` stores an ordinary failure as the
exception type alone or `ExceptionType: sanitized detail`. Before storage it:

- replaces current/context text and raw, JSON, collapsed, or control-normalized
  variants with `<redacted>`;
- replaces literal and resolved workspace-root spellings with `<workspace>`;
- replaces Unicode `Cc`/`Cf`, collapses whitespace, and trims;
- falls back to `ExceptionType: unavailable diagnostic` when needed; and
- caps the result at 4,096 characters with ` …[truncated]` inside that limit.

Cancellation uses the fixed
[runtime diagnostic](../reference/runtime-api.md#one-conversation-turn).
Replies remain unchanged. Sanitization is not a general secret detector.
Terminal summaries use a separate 512/256-character boundary and are not
stored as conversation events.

## Codex process boundary

Codex runs without shell evaluation in isolated staging: read-only sandbox, no
approvals, ignored project/user configuration, and disabled shell, web, app,
and multi-agent tools. Verified role context is staged; JSON enters standard
input. SHA-256 detects changes but does not encrypt or authorize content.

Installation-level or vendor-global instructions can still apply. Codex may
retain remote conversations under vendor and account policy. Session end,
detach, and backend close keep local thread references; a changed agent
fingerprint starts a new thread but does not erase the old one.

## Credentials and visible reasoning

Use `codex login` or process-environment `CODEX_API_KEY`. Never place
credentials in story content, configuration, filenames, prompts, replies,
searchable transcripts, or logs. Public models have no hidden-reasoning field;
ordinary returned `content` is visible and may be persisted.

`story smoke` uses synthetic data and no provider. A pass says nothing about
provider privacy, authentication, network behavior, or real story content.
