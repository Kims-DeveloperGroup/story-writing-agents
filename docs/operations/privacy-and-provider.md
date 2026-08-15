# Privacy and provider exposure

> **Availability:** these controls describe the implemented runtime. They
> reduce exposure but do not make Codex, its executable, or the workspace a
> trusted security boundary.

Key terms: **plaintext**—unencrypted content; **visible context**—events sent in
a request; **provider-private history**—vendor thread state; **diagnostic**—
stored failure text; **credential**—provider authentication.

## Local data is plaintext

There is no encryption, access control, secure erase, or retention policy.
Project content, state, provider metadata, transcripts, and manuscripts are
ordinary files protected only by the operating system.

The blackboard can contain complete input, drafts, reviews, errors, and commit
metadata. Owner state is narrower, but provider output is not scrubbed.

Subjects and transcripts are plaintext. Transcripts keep exact visible messages,
replies, and runtime errors; archive does not delete or hide them. `read_context()`
is a bounded view. Direct event-store callers must redact their own error text.

Local search and cited reads make no provider call or index, but can expose
subjects, snippets, and events. See [Conversation search](../reference/conversation-search.md).

## What a conversation sends

`StoryRuntime.converse()` sends current text and bounded complete-turn context
from the addressed conversation to one agent. Visible context excludes other
conversations, debates, manuscripts, core updates, and owner state. The agent
definition separately provides that role's allowlisted core files.
See the [provider handoff](../design/prompts-and-provider.md#conversation-turn-flow).

`BoundAgent.converse()` may update thread metadata, not transcripts. `/agents`,
target changes, and subject management are provider-free. Ordinary, `@AGENT`,
and `/say` turns can contact the provider and append the selected transcript;
the shell cannot view or search it. See [Story sessions](../guides/story-sessions.md).

Direct routing is not private: it chooses one responder in a shared conversation.
Later agents can receive its bounded context. A different conversation separates
visible context, not provider history.

Compatible provider threads are keyed by session, backend, and agent, so they
can span conversations and debates. The provider may remember content absent
from the visible payload.

## Runtime-error diagnostics

After a user event is durable, `StoryRuntime` stores an ordinary failure as the
exception type alone or `ExceptionType: sanitized detail`. Before storing it,
the runtime:

- replaces current/context text, JSON-escaped forms, and control-normalized
  collapsed forms with `<redacted>`;
- replaces literal and resolved workspace-root spellings and their collapsed
  equivalents with `<workspace>`;
- replaces Unicode `Cc`/`Cf` with spaces, collapses whitespace, and trims;
- uses `ExceptionType: unavailable diagnostic` when exception stringification
  fails or the result cannot be strict UTF-8; and
- caps the complete stored value at 4,096 characters, ending a truncated value
  with ` …[truncated]` inside that limit.

Cancellation uses the fixed [runtime diagnostic](../reference/runtime-api.md#one-conversation-turn).
Replies remain unchanged. Sanitization is not a general secret detector.

Backend exit diagnostics are sanitized before raising, then again before
transcript storage.

Terminal error summary/hint caps are 512/256 characters. Display sanitization
does not persist text or sanitize provider payloads. See
[Terminal display safety](terminal-safety.md).

## Codex process boundary

Codex runs without shell evaluation in isolated staging: read-only sandbox, no
approvals, ignored project/user configuration, and disabled shell, web, app, and
multi-agent tools. Verified role context is staged; JSON enters on standard input.

SHA-256 detects changed files but does not encrypt or authorize content.
Installation-level or vendor-global instructions can still apply. Codex may
retain remote conversations according to vendor and account policy. Session
end, detach, and backend close keep local thread references; a changed agent
fingerprint starts a new thread but does not erase the old one.

## Credentials and visible reasoning

Use `codex login` or process-environment `CODEX_API_KEY`. Never put credentials
in story content, configuration, filenames, prompts, replies, or logs. Public
models have no hidden-reasoning field; text returned as ordinary `content` is
visible and may be persisted.

`story smoke` uses synthetic data and no provider. A pass says nothing about
provider privacy, authentication, network behavior, or real story content.
