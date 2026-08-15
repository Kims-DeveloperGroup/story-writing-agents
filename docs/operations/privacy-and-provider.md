# Privacy and provider exposure

> **Availability:** these controls describe the implemented runtime. They
> reduce exposure but do not make Codex, its executable, or the workspace a
> trusted security boundary.

Key terms: **plaintext**—unencrypted local content; **visible context**—prior
events deliberately sent in a request; **provider-private history**—vendor
state behind a thread ID; **diagnostic**—stored failure text; **credential**—a
secret used to authenticate provider access.

## Local data is plaintext

The runtime provides no encryption, access control, secure erase, or retention
policy. Project content, runtime state, provider metadata, transcripts, and
manuscripts are ordinary files protected only by the operating system.

The debate blackboard can contain complete input, candidates, reviews, errors,
and commit metadata. Owner-isolated state is narrower and length-bounded, but
provider output is not semantically scrubbed.

A conversation subject and transcript are also plaintext. The transcript keeps
exact visible user messages, agent replies, and runtime errors. Archiving changes
discovery status; it does not delete or hide either file. `read_context()`
returns a bounded view without rewriting the full transcript. A caller that
uses the event store directly remains responsible for any error text it appends;
the store itself performs no redaction.

Local search and cited reads make no provider call or index, but can expose
subjects, snippets, and events. See [Conversation search](../reference/conversation-search.md).

## What a conversation sends

`StoryRuntime.converse()` sends the current text and the explicitly addressed
conversation's bounded, validated complete-turn context to exactly one bound
agent. It does not add another conversation, debate history, manuscripts, core
updates, or another agent's owner-isolated state to that visible context. The
agent definition separately provides only that role's allowlisted core files.
See the [provider handoff](../design/prompts-and-provider.md#conversation-turn-flow).

`BoundAgent.converse()` projects the request to provider JSON and may update
thread metadata, but never reads or writes the transcript. The runtime owns
that sequencing; automatic interactive selection and dispatch are unavailable.

Visible context is conversation-scoped; provider-private history is not. A
compatible thread is keyed by session, backend, and agent and can span
conversation IDs and debate calls. A provider may therefore remember material
absent from the current visible payload. Separate conversation IDs are not a
provider-isolation boundary.

## Runtime-error diagnostics

After a user event is durable, `StoryRuntime` stores an ordinary failure as the
exception type alone or `ExceptionType: sanitized detail`. Before storing it,
the runtime:

- replaces the exact current text and every context-event content value, their
  JSON-escaped forms, and their control/format-to-space whitespace-collapsed
  equivalents with `<redacted>`;
- replaces literal and resolved workspace-root spellings and their collapsed
  equivalents with `<workspace>`;
- turns every Unicode `Cc` or `Cf` character into a space, collapses whitespace,
  and trims the detail;
- uses `ExceptionType: unavailable diagnostic` when exception stringification
  fails or the result cannot be strict UTF-8; and
- caps the complete stored value at 4,096 characters, ending a truncated value
  with ` …[truncated]` inside that limit.

Cancellation uses the fixed [runtime diagnostic](../reference/runtime-api.md#one-conversation-turn).
Replies are stored unchanged. Sanitization protects exact values; it is not a
general secret detector.

The backend also sanitizes a nonzero Codex-exit diagnostic before raising it;
the runtime sanitizer applies again before that exception becomes a transcript
event.

## Codex process boundary

Codex runs without shell evaluation in isolated staging. The runtime requests a
read-only sandbox, no approvals, ignored user/project configuration, and
disabled shell, web, app, and multi-agent tools. Verified role context is staged
in a temporary `AGENTS.md`; request JSON enters on standard input.

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
