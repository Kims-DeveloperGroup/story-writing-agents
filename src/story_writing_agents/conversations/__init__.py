"""Provider-neutral subject-conversation persistence contracts."""

from .catalog import (
    CONVERSATION_METADATA_SCHEMA_VERSION,
    CONVERSATION_SUBJECT_NORMALIZATION_VERSION,
    MAX_CONVERSATION_SUBJECT_LENGTH,
    ConversationCatalog,
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
)
from .context import (
    CONVERSATION_CONTEXT_POLICY,
    ConversationContextPolicy,
    ConversationContextWindow,
    ConversationEventStore,
    select_bounded_conversation_context,
    validate_conversation_context_window,
)
from .events import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
)


_PUBLIC_TYPES = (
    ConversationCatalog,
    ConversationContextPolicy,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationEventStore,
    ConversationMetadata,
    ConversationStatus,
)
for _public_type in _PUBLIC_TYPES:
    _public_type.__module__ = __name__
normalize_conversation_subject.__module__ = __name__
select_bounded_conversation_context.__module__ = __name__
validate_conversation_context_window.__module__ = __name__
del _public_type


__all__ = [
    "CONVERSATION_CONTEXT_POLICY",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CONVERSATION_METADATA_SCHEMA_VERSION",
    "CONVERSATION_SUBJECT_NORMALIZATION_VERSION",
    "MAX_CONVERSATION_SUBJECT_LENGTH",
    "ConversationCatalog",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
    "ConversationEventStore",
    "ConversationMetadata",
    "ConversationStatus",
    "normalize_conversation_subject",
    "select_bounded_conversation_context",
    "validate_conversation_context_window",
]
