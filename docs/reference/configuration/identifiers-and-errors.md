# Identifiers and errors

> **Availability:** these validation rules and public exception types are
> implemented. Malformed persisted data fails closed and is not repaired.

Key terms: **safe ID**—a logical name that cannot traverse paths; **format
error**—invalid persisted bytes or schema; **security error**—an unsafe
filesystem operation; **collision**—attempted reuse of reserved identity.

## Exact token formats

| Value | Pattern and limits |
| --- | --- |
| Session, debate, conversation ID | `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; 1–64 ASCII characters, starting alphanumeric. |
| Persona identifier | `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`; no `.md`, slash, whitespace, traversal, or absolute path. |
| Language token | `[A-Za-z][A-Za-z0-9-]{0,15}`; case preserved. |
| Provider thread ID | `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`. |

Conversation subjects have a separate Unicode contract. Required manuscript
outputs use normalized `PART/PATH.md` without a leading `manuscript/`; they
cannot be absolute, traverse with `..`, target a symlink, or use a root-level
filename. The complete grammar is in
[Canonical writing artifacts](../writing-artifacts.md).

## Public exception hierarchy

All exported errors derive from `StoryWritingAgentsError`:

- `WorkspaceSecurityError` rejects escaping paths and symlinks.
- `WorkspaceFormatError` rejects invalid UTF-8, malformed JSON/JSONL, invalid
  exact schemas, mismatched embedded IDs, timestamps, and other corruption.
- `WorkspaceVersionUnsupportedError` is a `WorkspaceFormatError` for detected
  V1 session/default state or another unsupported persisted schema version.
- `ConfigurationError` rejects unknown keys/agents, invalid values, missing
  persona templates, unresolved defaults/input, and invalid backend overrides.
- `DirectiveError` rejects invalid or unappliable directives.
- `SessionIdError` rejects unsafe session, debate, or conversation IDs.
- `ConversationNotFoundError` and `ConversationExistsError` enforce
  conversation identity; `ManuscriptExistsError` prevents replacement.
- `BackendProtocolError` is the provider/backend contract base.
- `SessionExistsError` and `SessionNotFoundError` enforce durable preconditions.
- `SessionPausedError` rejects provider work while durable state is paused.
- `SessionAccessBlockedError` reports another live local process.
- `SessionOwnershipUnknownError` reports ownership that cannot be proved safe.
- `DebateExistsError` reports a reserved session/debate pair.
- `ProgressObserverError` reports invalid debate-progress delivery.
- `ConversationRetrievalError` is the retrieval base; its semantic, budget, and
  observer subclasses distinguish invalid actions, exhausted budgets, and
  progress failure.

Lifecycle errors expose no claim token. Concrete Python diagnostics may contain
the safe session ID and, for a live local conflict, bounded hostname/PID facts.
Shell errors use trusted fixed copy and never reproduce exception messages.

## Interactive lifecycle error mapping

| Condition | Stable code | User effect |
| --- | --- | --- |
| A command needs current attachment | `session_not_attached` | Create or resume a session first. |
| Durable state is paused | `session_paused` | Resume the session to make it ready before provider work. |
| Another live local process owns access | `session_access_blocked` | Close that process normally, then retry. |
| Ownership cannot be verified | `session_ownership_unknown` | Preserve the claim and inspect recovery guidance. |
| V1 state is detected | `workspace_version_unsupported` | Use the nondestructive manual reset procedure. |

Invalid/missing/existing IDs and provider configuration retain their established
`session_invalid`, `session_not_found`, `session_exists`, and
`provider_preflight_failed` codes. Conversation and debate errors remain
separate domains.

## Failure behavior

Validation precedes directive replacement, provider work where possible, and
approved artifact mutation. Lifecycle reads validate complete logical records
instead of skipping malformed files. Unsupported V1 data is left unchanged.
Catalog reads reject duplicate, missing, or extra fields. A duplicate debate ID
raises before providers; a manuscript collision after finalization returns a
failed debate and consumes the new debate ID.

Automation catches expected exceptions, writes `ExceptionType: message` to
standard error, and exits `2`. Debate deadlock and handled failure have distinct
result exits. Never use diagnostic text as access control; claim and debate
tokens coordinate cooperating processes and are not authentication secrets.
