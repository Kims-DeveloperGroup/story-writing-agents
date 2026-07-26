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
    """Raised when a session or debate identifier is unsafe."""


class ManuscriptExistsError(StoryWritingAgentsError):
    """Raised rather than silently overwriting an existing manuscript."""


class BackendProtocolError(StoryWritingAgentsError):
    """Raised when a backend returns a reply that violates the public protocol."""


class DebateExistsError(StoryWritingAgentsError):
    """Raised when a session/debate identifier has already been reserved."""
