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
