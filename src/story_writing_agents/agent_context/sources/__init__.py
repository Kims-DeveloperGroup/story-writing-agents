"""Public sources owners, loaded only when requested."""

__all__ = ['CONTEXT_SOURCE_POLICY', 'ActiveDebateSourceAdapter', 'ContextSourceDescriptor', 'ContextSourceError', 'ContextSourceErrorCode', 'ContextSourcePolicy', 'ContextSourceRecord', 'ContextSourceRegistry', 'ContextSourceSearchMatch', 'ContextSourceSnapshot', 'FocusedConversationSourceAdapter', 'SessionHistorySourceAdapter', 'OwnAgentStateSourceAdapter', 'WritingArtifactSourceAdapter']


def __getattr__(name: str) -> object:
    """Resolve one declared owner without loading unrelated state."""
    if name in ('CONTEXT_SOURCE_POLICY', 'ContextSourceDescriptor', 'ContextSourceError', 'ContextSourceErrorCode', 'ContextSourcePolicy', 'ContextSourceRecord', 'ContextSourceSearchMatch', 'ContextSourceSnapshot'):
        from .capture_values import (
            CONTEXT_SOURCE_POLICY,
            ContextSourceDescriptor,
            ContextSourceError,
            ContextSourceErrorCode,
            ContextSourcePolicy,
            ContextSourceRecord,
            ContextSourceSearchMatch,
            ContextSourceSnapshot,
        )
        names = ('CONTEXT_SOURCE_POLICY', 'ContextSourceDescriptor', 'ContextSourceError', 'ContextSourceErrorCode', 'ContextSourcePolicy', 'ContextSourceRecord', 'ContextSourceSearchMatch', 'ContextSourceSnapshot')
        values = (CONTEXT_SOURCE_POLICY, ContextSourceDescriptor, ContextSourceError, ContextSourceErrorCode, ContextSourcePolicy, ContextSourceRecord, ContextSourceSearchMatch, ContextSourceSnapshot)
    elif name in ('ActiveDebateSourceAdapter',):
        from .active_debate import ActiveDebateSourceAdapter
        names = ('ActiveDebateSourceAdapter',)
        values = (ActiveDebateSourceAdapter,)
    elif name in ('ContextSourceRegistry',):
        from .registry import ContextSourceRegistry
        names = ('ContextSourceRegistry',)
        values = (ContextSourceRegistry,)
    elif name in ('FocusedConversationSourceAdapter',):
        from .focused_conversation import FocusedConversationSourceAdapter
        names = ('FocusedConversationSourceAdapter',)
        values = (FocusedConversationSourceAdapter,)
    elif name in ('SessionHistorySourceAdapter',):
        from .session_history import SessionHistorySourceAdapter
        names = ('SessionHistorySourceAdapter',)
        values = (SessionHistorySourceAdapter,)
    elif name in ('OwnAgentStateSourceAdapter',):
        from .own_agent_state import OwnAgentStateSourceAdapter
        names = ('OwnAgentStateSourceAdapter',)
        values = (OwnAgentStateSourceAdapter,)
    elif name in ('WritingArtifactSourceAdapter',):
        from .writing_artifact import WritingArtifactSourceAdapter
        names = ('WritingArtifactSourceAdapter',)
        values = (WritingArtifactSourceAdapter,)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals().update(zip(names, values))
    return globals()[name]


def __dir__() -> list[str]:
    """Include every declared public name before lazy resolution."""
    return sorted(set(globals()) | set(__all__))
