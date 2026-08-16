"""Public exception hierarchy for the story-writing runtime."""


class StoryWritingAgentsError(Exception):
    """Base class for expected runtime errors."""


class WorkspaceSecurityError(StoryWritingAgentsError):
    """Raised when a path would escape the configured workspace."""


class WorkspaceFormatError(StoryWritingAgentsError):
    """Raised when a persisted workspace file has an invalid format."""


class ConfigurationError(StoryWritingAgentsError):
    """Raised when global, agent, or runtime configuration is invalid."""


class DirectiveError(StoryWritingAgentsError):
    """Raised when a runtime directive is invalid or cannot be applied."""


class SessionIdError(StoryWritingAgentsError):
    """Raised when a session, debate, or conversation identifier is unsafe."""


class ConversationNotFoundError(StoryWritingAgentsError):
    """Raised when a conversation is absent from its owning story session."""


class ConversationExistsError(StoryWritingAgentsError):
    """Raised rather than overwriting an existing conversation identity."""


class ManuscriptExistsError(StoryWritingAgentsError):
    """Raised rather than silently overwriting an existing manuscript."""


class BackendProtocolError(StoryWritingAgentsError):
    """Raised when a backend returns a reply that violates the public protocol."""


class SessionExistsError(BackendProtocolError):
    """Raised when creating an already persisted provider session."""


class SessionNotFoundError(BackendProtocolError):
    """Raised when a requested provider session does not exist."""


class SessionInUseError(BackendProtocolError):
    """Raised when another process may own a provider session lease."""


class DebateExistsError(StoryWritingAgentsError):
    """Raised when a session/debate identifier has already been reserved."""


class ProgressObserverError(StoryWritingAgentsError):
    """Fixed nonsensitive failure raised for a debate progress observer."""

    def __init__(self) -> None:
        """Create the fixed-message error without public payload or mutation."""

        super().__init__("debate progress observer failed")


class ConversationRetrievalError(StoryWritingAgentsError):
    """Base class for expected bounded conversation-retrieval failures."""

    def __init__(self) -> None:
        """Create the fixed base failure without public payload or mutation."""

        super().__init__("conversation retrieval failed")


class ConversationRetrievalSemanticError(ConversationRetrievalError):
    """Fixed failure for a well-shaped but unauthorized retrieval action."""

    def __init__(self) -> None:
        """Create the fixed semantic failure without public payload or mutation."""

        StoryWritingAgentsError.__init__(
            self,
            "conversation retrieval semantic validation failed",
        )


class ConversationRetrievalBudgetError(ConversationRetrievalError):
    """Fixed failure for exhausting one bounded retrieval-turn policy."""

    def __init__(self) -> None:
        """Create the fixed budget failure without public payload or mutation."""

        StoryWritingAgentsError.__init__(
            self,
            "conversation retrieval budget exhausted",
        )


class ConversationRetrievalObserverError(ConversationRetrievalError):
    """Fixed nonsensitive failure for a retrieval progress observer."""

    def __init__(self) -> None:
        """Create the fixed observer failure without public payload or mutation."""

        StoryWritingAgentsError.__init__(
            self,
            "conversation retrieval progress observer failed",
        )
