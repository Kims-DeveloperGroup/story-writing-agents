"""Public exception hierarchy for the story-writing runtime."""

from collections.abc import Callable


class StoryWritingAgentsError(Exception):
    """Base class for expected runtime errors."""


class SessionArtifactGuardFailure(StoryWritingAgentsError):
    """Package-internal marker for loss of guarded session artifact access."""


class WorkspaceSecurityError(StoryWritingAgentsError):
    """Raised when a path would escape the configured workspace."""


class WorkspaceFormatError(StoryWritingAgentsError):
    """Raised when a persisted workspace file has an invalid format."""


class WorkspaceVersionUnsupportedError(WorkspaceFormatError):
    """Raised when persisted workspace state uses an unsupported old schema."""


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


class ProviderAuthenticationRequiredError(BackendProtocolError):
    """Native authentication failure with a separate physical replay decision."""

    def __init__(self, provider: str, *, replay_safe: bool = False) -> None:
        if provider not in {"codex", "antigravity"}:
            raise ValueError("authentication provider is unsupported")
        if type(replay_safe) is not bool:
            raise TypeError("authentication replay decision must be boolean")
        super().__init__(f"{provider} authentication is required; use native sign-in")
        self.provider = provider
        self.replay_safe = replay_safe
        self.auth_generation = 0
        self.replay_aborted = False
        self._replay_validator: Callable[[], bool] | None = None
        self._validator_attached = False

    def attach_replay_validator(self, validator: Callable[[], bool]) -> None:
        """Attach one operation-local backend veto before offering recovery."""

        if self._validator_attached or not callable(validator):
            raise ValueError("authentication replay validator is invalid")
        self._validator_attached = True
        self._replay_validator = validator

    def validate_replay(self) -> bool:
        """Require an exact synchronous true; validation never grants replay."""

        validator = self._replay_validator
        if validator is None:
            return False
        try:
            return validator() is True
        except Exception:
            return False

    def clear_replay_validator(self) -> None:
        """Release the callback before submission or public failure propagation."""

        self._replay_validator = None

    def abort_replay(self) -> None:
        """Retain original physical facts when a prepared retry was vetoed."""

        self.replay_aborted = True
        self.clear_replay_validator()


class SessionExistsError(BackendProtocolError):
    """Raised when creating an already persisted provider session."""


class SessionNotFoundError(BackendProtocolError):
    """Raised when a requested provider session does not exist."""


class SessionPausedError(BackendProtocolError):
    """Raised when provider work requires a ready story session."""


class SessionAccessBlockedError(BackendProtocolError):
    """Raised when a live local process owns story-session access."""


class SessionOwnershipUnknownError(BackendProtocolError):
    """Raised when safe access ownership cannot be determined."""


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
