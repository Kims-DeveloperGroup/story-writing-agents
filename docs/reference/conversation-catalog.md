# Conversation catalog

> **Availability:** the provider-neutral catalog and filesystem adapter are
> implemented Python APIs. They are not wired to the CLI or `StoryRuntime`.
> Current selection, indexing, search, and interactive dispatch are not
> implemented.

Key terms: **conversation ID**—an immutable identity scoped to one session;
**display subject**—the exact stored title; **normalized key**—a derived
comparison form that is not identity; **archive**—a reversible status, not
deletion; **transcript**—visible events stored separately from metadata.

The catalog answers “which conversation is this?” and “how is it discovered?”
The separate [conversation transcript](conversation-transcripts.md) answers
“what visible events happened?” Archiving changes discovery only; it does not
make the transcript unreadable.

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

An absent catalog lists as `()` without creating directories or migrating
legacy data. Reads validate strict UTF-8, exact JSON shape, values, timestamps,
and path ownership. Missing records raise `ConversationNotFoundError`; an
exhausted generated-ID allocation raises `ConversationExistsError`; unsafe IDs
raise `SessionIdError`; malformed records raise `WorkspaceFormatError`; and
containment or symlink violations raise `WorkspaceSecurityError`.

Creation is exclusive and never overwrites an identity. Later mutations
atomically replace only the complete metadata file. The catalog never invokes
a provider or changes CLI selection, sessions, debates, manuscripts, core
documents, or agent state. Metadata is plaintext and archiving is not deletion.
