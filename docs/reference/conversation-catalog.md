# Conversation catalog

> **Availability:** the provider-neutral catalog and filesystem adapter serve
> the installed TTY lobby's subject metadata. The lobby adds defaults/focus and
> display handles; `StoryRuntime` does not depend on the catalog.

Key terms: **conversation ID**—an immutable identity scoped to one session;
**display subject**—the exact stored title; **normalized key**—a derived
comparison form that is not identity; **archive**—a reversible status, not
deletion; **transcript**—visible events stored separately from metadata.

The catalog answers “which conversation is this?” The separate
[transcript](conversation-transcripts.md) answers “what happened?” Archive
changes discovery, not transcript readability. [Conversation search](conversation-search.md)
consumes both contracts without changing them.

```python
CONVERSATION_METADATA_SCHEMA_VERSION = 1
CONVERSATION_SUBJECT_NORMALIZATION_VERSION = 1
MAX_CONVERSATION_SUBJECT_LENGTH = 256

ConversationMetadata(
    schema_version: int,
    session_id: str,
    conversation_id: str,
    subject: str,
    subject_revision: int,
    status: ConversationStatus,
    created_at: str,
    updated_at: str,
)
normalize_conversation_subject(subject: str) -> str
FilesystemConversationCatalog(workspace: Workspace | str | os.PathLike[str])
```

`ConversationMetadata` is frozen and slotted. `to_dict()` returns exactly its
eight JSON-compatible fields and converts status to `active` or `archived`.
The storage-neutral `ConversationCatalog` protocol and filesystem adapter share
these methods:

```python
create(session_id: str, subject: str) -> ConversationMetadata
get(session_id: str, conversation_id: str) -> ConversationMetadata
list(session_id: str, *, include_archived: bool = False) -> tuple[ConversationMetadata, ...]
rename(session_id: str, conversation_id: str, subject: str) -> ConversationMetadata
archive(session_id: str, conversation_id: str) -> ConversationMetadata
unarchive(session_id: str, conversation_id: str) -> ConversationMetadata
```

## Exact metadata schema

Each identity is stored at
`discussion_logs/<session_id>/interactive/conversations/<conversation_id>/metadata.json`:

```json
{
  "schema_version": 1,
  "session_id": "demo",
  "conversation_id": "c_0123456789abcdef0123456789abcdef",
  "subject": "Opening scene",
  "subject_revision": 1,
  "status": "active",
  "created_at": "2026-08-12T01:02:03.456789+00:00",
  "updated_at": "2026-08-12T01:02:03.456789+00:00"
}
```

No field may be missing, extra, or duplicated. Version is integer `1`, not a
boolean. Embedded safe IDs must match the owning path. Subject revision is a
positive integer. Timestamps are canonical ISO-8601 UTC with explicit
`+00:00`; update time cannot precede creation time.

A display subject must be a nonblank, strict-UTF-8-encodable string of at most
256 Unicode code points with no Unicode `Cc` control. Accepted leading,
trailing, and repeated whitespace is preserved. Normalization applies NFKC,
case folding, then trims and collapses Unicode whitespace runs to one ASCII
space. The key is derived, not stored, and does not enforce uniqueness.

The installed `/conversation list [--all] [QUERY]` reads catalog order and
uses that same normalization for case-insensitive subject substring matching.
It never replaces the stored display subject with the normalized key.

## Operation semantics

`create()` allocates an immutable ID scoped to the story session, starts at
revision `1` and `active`, and sets equal timestamps. Duplicate display or
normalized subjects create independent identities. `get()` resolves active or
archived metadata only within its owner session.

`list()` returns active records by default, ordered by creation time then ID;
`include_archived=True` adds archived records and must be a real `bool`.
`rename()` preserves identity, creation time, and status, increments revision
on every success—including the same display value—and refreshes update time.
Archive transitions preserve subject revision. Repeating the current status is
idempotent and leaves update time unchanged; a real transition refreshes it.

## IDs in the installed lobby

The immutable catalog ID is the durable identity; subject text may change.
`/conversation resume` and explicit archive accept an exact full ID or a
case-sensitive unique prefix. An exact ID wins; a nonexact prefix must be at
least 12 characters. Resolution includes active and archived records, so an
archived match is found and then rejected for current focus.

For display, the lobby uses the first 12 ID characters only when that prefix is
unique across all active and archived records. Otherwise it prints the full ID.
Copy the shown handle back into commands. Conversation default/focus is a
separate per-session record; see [Session storage](configuration/session-storage.md).

An absent catalog lists as `()` without creating or migrating data. Reads
validate UTF-8, schema, values, timestamps, and path ownership. Failures use
`ConversationNotFoundError`, `ConversationExistsError`, `SessionIdError`,
`WorkspaceFormatError`, or `WorkspaceSecurityError` as applicable.

Creation is exclusive and never overwrites an identity. Later mutations
atomically replace only the complete metadata file. The catalog never invokes
a provider or changes CLI defaults/focus, sessions, transcripts, debates,
manuscripts, core documents, or agent state. Metadata is plaintext; archive is
reversible status, not deletion.
