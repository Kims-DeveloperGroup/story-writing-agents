# Identifiers and errors

> **Availability:** the validation rules and public exception types on this
> page are implemented. Persisted malformed data fails closed; it is not
> repaired or migrated automatically.

Key terms: **safe ID**—a validated logical name that cannot traverse paths;
**format error**—invalid persisted bytes or schema; **security error**—an unsafe
filesystem operation; **collision**—an attempted reuse of reserved identity.

## Exact token formats

| Value | Pattern and limits |
| --- | --- |
| Session, debate, conversation ID | `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`: 1–64 ASCII letters, digits, `_`, or `-`, starting alphanumeric. |
| Persona identifier | `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`: 1–64 ASCII letters, digits, `_`, `.`, or `-`, starting alphanumeric; no `.md`, slash, whitespace, traversal, or absolute path. |
| Language token | `[A-Za-z][A-Za-z0-9-]{0,15}`: 1–16 ASCII characters, starting with a letter; case preserved and no fixed language registry. |
| Provider thread ID | `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`. |

Conversation display subjects have a separate Unicode contract in the
[conversation catalog](../conversation-catalog.md). Manuscript names may use
safe relative subdirectories, but not absolute paths, traversal, or symlinks.

## Public exception hierarchy

All exported runtime errors derive from `StoryWritingAgentsError`:

- `WorkspaceSecurityError` rejects absolute/traversing/escaping paths,
  symlinks, and unsafe direct workspace operations.
- `WorkspaceFormatError` rejects invalid UTF-8, malformed JSON or JSONL,
  unexpected exact-schema fields or types, path/embedded-ID mismatch, changed
  prompt-reference bytes, invalid timestamps, and other persisted corruption.
- `ConfigurationError` rejects unknown configuration keys or agents, invalid
  configuration values, missing persona templates, CLI context/resolution
  errors, input selection errors, and invalid backend overrides.
- `DirectiveError` rejects non-standalone, invalid, unknown-agent, or otherwise
  unappliable directives.
- `SessionIdError` rejects unsafe session, debate, or conversation IDs.
- `ConversationNotFoundError` reports an unknown conversation within its
  session; `ConversationExistsError` reports exhausted collision-safe ID
  allocation instead of overwriting.
- `SessionNotFoundError` and `SessionExistsError` enforce strict provider
  lifecycle preconditions.
- `SessionInUseError` reports a live or unverifiable lease owner without
  exposing the opaque ownership token.
- `DebateExistsError` reports an already reserved session/debate pair before
  provider work begins.
- `ManuscriptExistsError` prevents overwriting an existing manuscript.
- `BackendProtocolError` reports missing execution backend, inactive-session
  use, unsupported provider, executable/preflight failure, invalid or
  mismatched replies, provider process errors/timeouts, thread ownership
  changes, and other backend protocol failures.

## Failure behavior

Validation is completed before directive-state replacement, debate provider
construction where possible, and approved artifact mutation. Rejected
directives preserve prior overrides. A duplicate debate ID raises before a
backend call. A manuscript collision can occur after finalization and returns a
failed debate; its new debate ID remains consumed.

Catalog reads reject duplicate, missing, or extra JSON fields. Provider-session
status reads validate every encountered logical manifest and lease rather than
skipping malformed files. Workspace reads do not silently rewrite legacy data.

The CLI catches ordinary exceptions, writes `ExceptionType: message` to
standard error, and exits `2`. Debate deadlock and handled failure are returned
results with distinct exit codes; see the [CLI reference](../cli.md).

Never use exception messages as an access-control mechanism. Lease diagnostics
may include the safe session ID, PID, and a control-sanitized length-limited
hostname, but not the token. Debate owner and lease tokens coordinate
cooperating processes; they are not authentication secrets.
