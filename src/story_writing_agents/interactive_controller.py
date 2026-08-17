"""Synchronous interactive session, conversation, and advisory coordination."""

from __future__ import annotations

import asyncio

from .agents import AGENT_SPECS, LEAD_ORCHESTRATOR, ROSTER
from .backend import AgentBackend
from .cli_context import (
    CliContext,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from .conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    ConversationCitation,
    ConversationMemoryService,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    ConversationTranscriptPage,
)
from .conversation_retrieval import (
    ConversationRetrievalProgressEvent,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalResult,
    ConversationRetrievalSource,
)
from .conversations import (
    CONVERSATION_CONTEXT_POLICY,
    ConversationCatalog,
    ConversationContextWindow,
    ConversationEvent,
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
    select_bounded_conversation_context,
)
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationExistsError,
    ConversationNotFoundError,
    ConversationRetrievalBudgetError,
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
    DirectiveError,
    ProgressObserverError,
    SessionAccessBlockedError,
    SessionExistsError,
    SessionIdError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    SessionPausedError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from .interactive_commands import (
    AGENT_ROUTES,
    ActionKind,
    InteractiveAction,
    parse_interactive_line,
)
from .interactive_terminal import (
    AttachmentKind,
    ComposeMode,
    ConfirmationResult,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveTerminal,
    TerminalActionKind,
    TerminalMessageKind,
)
from .models import (
    DebateContextMode,
    DebateContextRequest,
    DebateFailureKind,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateResult,
    DebateStatus,
    InputKind,
)
from .orchestrator import StoryRuntime
from .run_inputs import read_draft_file
from .session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)
from .workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)


_UNAVAILABLE_SUMMARY = "This command is not available in the session lobby."
_LOBBY_COMMAND_HINT = (
    "Use /help, /session list, /session create NAME, /session open [NAME], "
    "/session pause, or /quit."
)
_MINIMUM_CONVERSATION_PREFIX_LENGTH = 12
_AGENT_ROUTE_ALIASES = frozenset(
    alias for route in AGENT_ROUTES for alias in route.aliases
)
_CONVERSATION_CATALOG_OPERATIONS = (
    "create",
    "get",
    "list",
    "rename",
    "archive",
    "unarchive",
)
_RECOVERABLE_SESSION_ERRORS = frozenset(
    {
        InteractiveErrorCode.SESSION_INVALID,
        InteractiveErrorCode.SESSION_EXISTS,
        InteractiveErrorCode.SESSION_NOT_FOUND,
        InteractiveErrorCode.SESSION_PAUSED,
        InteractiveErrorCode.SESSION_ACCESS_BLOCKED,
        InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN,
        InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED,
        InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
    }
)


class _ControllerAbort(Exception):
    """Carry one trusted fatal error to the controller's cleanup boundary."""

    def __init__(self, error: InteractiveError) -> None:
        """Store a typed display error without retaining raw failure prose."""

        super().__init__(error.code.value)
        self.error = error


class _ConversationResolutionFailure(Exception):
    """Carry one trusted lookup error code without retaining input prose."""

    def __init__(self, code: InteractiveErrorCode) -> None:
        """Store a typed resolution category without external side effects."""

        super().__init__(code.value)
        self.code = code


class _ConversationInputFailure(Exception):
    """Mark a failure from controller-owned subject or query validation."""


class _ConversationCatalogProtocolFailure(Exception):
    """Mark an injected catalog return that violates its public protocol."""


class InteractiveController:
    """Own one interactive process's attachment and synchronous shell loop.

    Construction borrows collaborators without I/O. ``run_session_lobby`` is
    single-use; once it starts, this controller closes the backend on every
    exit and releases any attachment it can still identify safely. The retained
    ``StoryRuntime`` exclusively owns provider turns, transcript outcomes, and
    consensus durability; one attachment-scoped memory service owns provider-free
    search/read access. The controller only validates and renders trusted debate
    progress and results.
    """

    __slots__ = (
        "_attached_session_id",
        "_backend",
        "_closed",
        "_conversation_catalog",
        "_conversation_event_store",
        "_conversation_memory_service",
        "_context",
        "_last_started_debate_by_session",
        "_runner",
        "_runtime",
        "_focused_conversation",
        "_started",
        "_target_agent_id",
        "_terminal",
        "_workspace",
    )

    def __init__(
        self,
        workspace: Workspace,
        context: CliContext,
        backend: AgentBackend,
        terminal: InteractiveTerminal,
        *,
        conversation_catalog: ConversationCatalog | None = None,
    ) -> None:
        """Bind collaborators and construct one shared transcript adapter.

        The four positional arguments remain the established construction
        contract. An omitted keyword-only catalog creates the workspace-backed
        adapter; an injected protocol implementation is retained as borrowed
        state and is never closed by this controller. The event store is shared
        by the retained runtime and later attachment-scoped memory services.
        Construction performs no provider call, filesystem write, or access claim.
        """

        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        if not isinstance(context, CliContext):
            raise TypeError("context must be a CliContext")
        if not isinstance(backend, AgentBackend):
            raise TypeError("backend must be an AgentBackend")
        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._workspace = workspace
        self._context = context
        self._backend = backend
        self._terminal = terminal
        resolved_catalog = (
            FilesystemConversationCatalog(workspace)
            if conversation_catalog is None
            else conversation_catalog
        )
        if any(
            not callable(getattr(resolved_catalog, operation, None))
            for operation in _CONVERSATION_CATALOG_OPERATIONS
        ):
            raise TypeError(
                "conversation_catalog must implement ConversationCatalog"
            )
        self._conversation_catalog = resolved_catalog
        self._conversation_event_store = FilesystemConversationEventStore(
            workspace,
            catalog=resolved_catalog,
        )
        self._runtime = StoryRuntime(
            workspace,
            backend,
            conversation_event_store=self._conversation_event_store,
        )
        self._conversation_memory_service: (
            ConversationMemoryService | None
        ) = None
        self._attached_session_id: str | None = None
        self._focused_conversation: ConversationMetadata | None = None
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._last_started_debate_by_session: dict[str, str] = {}
        self._runner: asyncio.Runner | None = None
        self._started = False
        self._closed = False

    def run_session_lobby(
        self,
        *,
        requested_session: str | None = None,
    ) -> int:
        """Run one interactive attachment lifecycle and return status 0 or 2.

        ``requested_session`` chooses explicit create/open behavior; ``None``
        uses the saved default. Commands may mutate owned process state,
        established session/conversation persistence, or runtime-owned turn
        transcripts. One controller-owned ``asyncio.Runner`` serves every
        accepted provider coroutine and closes before session/backend cleanup.
        Every started exit releases known access and closes the borrowed
        backend; fatal uncertainty returns 2, and a second run raises
        ``RuntimeError`` without reacquiring resources.
        """

        if self._started:
            raise RuntimeError("interactive controller instances are single-use")
        if requested_session is not None and not isinstance(requested_session, str):
            raise TypeError("requested_session must be a string or None")
        self._started = True

        exit_code = 2
        cleanup_error_reported = False
        try:
            self._runner = asyncio.Runner()
            startup_requested_exit = self._enter_initial_session(
                requested_session
            )
            if startup_requested_exit:
                exit_code = 0
            else:
                if self._attached_session_id is None:
                    self._write_lobby_orientation()
                exit_code = self._read_session_lobby_commands()
        except _ControllerAbort as abort:
            diagnostic_written = self._write_error_best_effort(abort.error)
            cleanup_error_reported = (
                abort.error.code is InteractiveErrorCode.CLEANUP_FAILED
                and diagnostic_written
            )
            exit_code = 2
        except (asyncio.CancelledError, KeyboardInterrupt, OSError):
            self._write_error_best_effort(
                InteractiveError(InteractiveErrorCode.OPERATION_INTERRUPTED)
            )
            exit_code = 2
        except Exception:
            self._write_error_best_effort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
            exit_code = 2
        finally:
            exit_code = self._release_attachment_and_close_backend(
                exit_code,
                cleanup_error_reported=cleanup_error_reported,
            )
        return exit_code

    def _enter_initial_session(self, requested_session: str | None) -> bool:
        """Open an explicit or default session; return whether startup saw EOF.

        An explicit missing session is created. A paused default requires one
        confirmation before it is reopened. Recoverable failures leave the
        controller in the lobby; cleanup uncertainty reaches the outer boundary.
        """

        if requested_session is not None:
            self._enter_explicit_session(requested_session)
            return False

        default_session = self._context.default_session_id
        if default_session is None:
            return False
        try:
            state = self._backend.get_session_lifecycle(default_session)
            self._require_lifecycle_state(state, default_session)
        except Exception as exc:
            self._present_session_failure(exc, default_session)
            return False

        if state.durable_state is SessionDurableState.ABSENT:
            self._present_session_failure(
                SessionNotFoundError(f"session not found: {default_session}"),
                default_session,
            )
            return False
        if state.durable_state is SessionDurableState.PAUSED:
            decision = self._terminal.request_confirmation(
                f"Open default session {default_session}?",
                default=True,
            )
            if decision is ConfirmationResult.EOF:
                return True
            if decision is not ConfirmationResult.YES:
                self._write_session_open_cancelled_status()
                return False
        self._open_known_session(default_session)
        return False

    def _enter_explicit_session(self, session_id: str) -> None:
        """Create a missing explicit session or open an existing one."""

        try:
            state = self._backend.get_session_lifecycle(session_id)
            self._require_lifecycle_state(state, session_id)
        except Exception as exc:
            self._present_session_failure(exc, session_id)
            return
        if state.durable_state is SessionDurableState.ABSENT:
            self._create_and_attach_destination(session_id)
            return
        self._open_known_session(session_id)

    def _create_and_attach_destination(self, session_id: str) -> None:
        """Validate configuration, create with access, and commit attachment."""

        if session_id == self._attached_session_id:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_EXISTS)
            )
            return
        access_acquired = False
        try:
            configured = self._backend.validate_provider_configuration(session_id)
            if configured is not True:
                raise BackendProtocolError(
                    "provider configuration validation returned a non-true value"
                )
            before = self._backend.get_session_lifecycle(session_id)
            self._require_lifecycle_state(before, session_id)
            if before.durable_state is not SessionDurableState.ABSENT:
                raise SessionExistsError(f"session already exists: {session_id}")
            acquired = self._backend.create_session_with_access(session_id)
            access_acquired = True
            self._require_ready_access_state(acquired, session_id)
            observed = self._backend.get_session_lifecycle(session_id)
            self._require_correlated_state(
                None,
                acquired,
                observed,
                session_id,
            )
        except Exception as exc:
            if access_acquired:
                self._release_failed_new_destination(session_id)
            self._present_session_failure(exc, session_id)
            return
        self._accept_acquired_destination(
            acquired,
            AttachmentKind.CREATED,
        )

    def _open_default_or_named_session(
        self,
        requested_session: str | None,
    ) -> None:
        """Open an explicit or default session without creating missing state."""

        session_id = (
            requested_session
            if requested_session is not None
            else self._context.default_session_id
        )
        if session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_FOUND)
            )
            return
        if session_id == self._attached_session_id:
            self._terminal.write_status(
                TerminalMessageKind.SESSION,
                f"Already attached to {session_id}.",
            )
            return
        self._open_known_session(session_id)

    def _open_known_session(self, session_id: str) -> None:
        """Run validate, pre-read, acquire/reopen, and correlated post-read."""

        newly_acquired = False
        try:
            configured = self._backend.validate_provider_configuration(session_id)
            if configured is not True:
                raise BackendProtocolError(
                    "provider configuration validation returned a non-true value"
                )
            before = self._backend.get_session_lifecycle(session_id)
            self._require_lifecycle_state(before, session_id)
            if before.durable_state is SessionDurableState.ABSENT:
                raise SessionNotFoundError(f"session not found: {session_id}")
            newly_acquired = before.access_state is not SessionAccessState.HELD_HERE
            if before.durable_state is SessionDurableState.PAUSED:
                acquired = self._backend.reopen_session_with_access(session_id)
                attachment = AttachmentKind.REOPENED
            else:
                acquired = self._backend.acquire_session_access(session_id)
                attachment = AttachmentKind.OPENED
            self._require_ready_access_state(acquired, session_id)
            observed = self._backend.get_session_lifecycle(session_id)
            self._require_correlated_state(
                before,
                acquired,
                observed,
                session_id,
            )
        except Exception as exc:
            if newly_acquired:
                self._release_failed_new_destination(session_id)
            self._present_session_failure(exc, session_id)
            return
        self._accept_acquired_destination(acquired, attachment)

    def _accept_acquired_destination(
        self,
        state: SessionLifecycleState,
        attachment: AttachmentKind,
    ) -> None:
        """Load destination focus and commit an exact ready access state safely."""

        session_id = state.session_id
        try:
            self._require_ready_access_state(state, session_id)
        except (TypeError, ValueError, BackendProtocolError):
            self._release_uncommitted_destination(session_id)
            self._abandon_known_attachment_for_cleanup_abort()
        try:
            focused_conversation = self._load_destination_conversation(session_id)
        except Exception as exc:
            self._release_uncommitted_destination(session_id)
            self._present_destination_conversation_failure(exc)
            return
        try:
            memory_service = ConversationMemoryService(
                ConversationSearchScope(session_id),
                self._conversation_catalog,
                self._conversation_event_store,
            )
        except Exception:
            self._release_uncommitted_destination(session_id)
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None
        if self._attached_session_id is None:
            self._attach_destination_session(
                session_id,
                attachment,
                focused_conversation,
                memory_service,
            )
            return
        self._switch_attached_session(
            session_id,
            attachment,
            focused_conversation,
            memory_service,
        )

    def _load_destination_conversation(
        self,
        session_id: str,
    ) -> ConversationMetadata | None:
        """Load an existing active default for one acquired destination.

        Missing files, null choices, absent records, and archived records return
        ``None`` without rewriting persistence. Malformed default or catalog
        state propagates so the caller can release the uncommitted destination
        before presenting a safe error.
        """

        default_id = load_default_conversation(self._workspace, session_id)
        if default_id is None:
            return None
        try:
            metadata = self._conversation_catalog.get(session_id, default_id)
        except ConversationNotFoundError:
            return None
        validated = self._validated_catalog_metadata(
            metadata,
            session_id,
            expected_conversation_id=default_id,
        )
        if validated.status is ConversationStatus.ARCHIVED:
            return None
        return validated

    def _present_destination_conversation_failure(
        self,
        failure: Exception,
    ) -> None:
        """Render a released destination's typed catalog failure without prose.

        The caller has already returned destination access. Workspace
        failures render and preserve an existing source, but an initial
        attachment has no source and aborts. Protocol/internal failure always
        aborts so the outer boundary releases any known source and closes.
        """

        error = self._classify_conversation_failure(failure)
        if (
            error.code is InteractiveErrorCode.INTERNAL_ERROR
            or self._attached_session_id is None
        ):
            raise _ControllerAbort(error)
        self._terminal.write_error(error)

    def _attach_destination_session(
        self,
        session_id: str,
        attachment: AttachmentKind,
        focused_conversation: ConversationMetadata | None,
        memory_service: ConversationMemoryService,
    ) -> None:
        """Persist and commit one destination with its scoped memory service.

        The default is written only after access acquisition. A write failure
        releases the destination before it is reported; failed release makes
        the remaining access state fatal and delegates cleanup to backend close.
        The validated candidate is retained only by a successful commit.
        """

        next_context = self._persist_destination_default(session_id)
        if next_context is None:
            return
        self._commit_attached_session(
            next_context,
            session_id,
            attachment,
            focused_conversation,
            memory_service,
        )

    def _switch_attached_session(
        self,
        destination_session: str,
        attachment: AttachmentKind,
        focused_conversation: ConversationMetadata | None,
        memory_service: ConversationMemoryService,
    ) -> None:
        """Save the destination default before replacing source and read scope.

        Destination acquisition has already succeeded. Default-write failure
        releases it and preserves the source. Source-release failure after the
        default write is not rolled back; owned resources are closed best
        effort and the process exits with ``cleanup_failed``. The candidate
        memory service commits only after source access is released.
        """

        source_session = self._attached_session_id
        assert source_session is not None
        next_context = self._persist_destination_default(destination_session)
        if next_context is None:
            return
        try:
            released = self._backend.release_session_access(source_session)
            self._require_released_state(released, source_session)
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()
        self._conversation_memory_service = None
        self._commit_attached_session(
            next_context,
            destination_session,
            attachment,
            focused_conversation,
            memory_service,
        )

    def _persist_destination_default(
        self,
        destination_session: str,
    ) -> CliContext | None:
        """Persist a changed default after acquiring access, or return current.

        On write failure uncommitted destination access is released. The
        failure is then reported when recoverable or escalated when workspace
        state is unusable; no controller attachment state is changed here.
        """

        if self._context.default_session_id == destination_session:
            return self._context
        try:
            return save_default_session(self._workspace, destination_session)
        except Exception as exc:
            self._release_uncommitted_destination(destination_session)
            self._present_session_failure(exc, destination_session)
            return None

    def _release_uncommitted_destination(self, session_id: str) -> None:
        """Release destination access after pre-commit failure or abort.

        A failed release makes all attachment bookkeeping uncertain, so the
        source marker is cleared and the outer boundary performs only backend
        close before returning ``cleanup_failed``.
        """

        try:
            released = self._backend.release_session_access(session_id)
            self._require_released_state(released, session_id)
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

    def _release_failed_new_destination(self, session_id: str) -> None:
        """Compensate a failed newly acquired destination without source loss."""

        try:
            state = self._backend.get_session_lifecycle(session_id)
            self._require_lifecycle_state(state, session_id)
            if state.access_state is SessionAccessState.HELD_HERE:
                released = self._backend.release_session_access(session_id)
                self._require_released_state(released, session_id)
        except (SessionNotFoundError, WorkspaceVersionUnsupportedError):
            return
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

    def _commit_attached_session(
        self,
        context: CliContext,
        session_id: str,
        attachment: AttachmentKind,
        focused_conversation: ConversationMetadata | None,
        memory_service: ConversationMemoryService,
    ) -> None:
        """Commit attachment, memory scope, routing, and orientation together.

        Destination access and the story-session default are already durable.
        This method performs no backend or catalog lifecycle operation and
        retains the prebuilt read service plus validated destination metadata.
        """

        self._context = context
        self._attached_session_id = session_id
        self._focused_conversation = focused_conversation
        self._conversation_memory_service = memory_service
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._terminal.write_session_orientation(
            str(self._workspace.root),
            session_id,
            attachment,
            self._target_agent_id,
            history_count=None,
        )

    @staticmethod
    def _require_lifecycle_state(
        state: object,
        expected_session: str,
    ) -> SessionLifecycleState:
        """Return an exact correlated lifecycle value or raise protocol error."""

        if type(state) is not SessionLifecycleState:
            raise BackendProtocolError(
                "backend returned an invalid session lifecycle value"
            )
        try:
            provider_work_is_ready(
                state,
                provider_configuration_ready=False,
            )
        except (TypeError, ValueError) as exc:
            raise BackendProtocolError(
                "backend returned malformed session lifecycle facts"
            ) from exc
        if state.session_id != expected_session:
            raise BackendProtocolError(
                "backend returned lifecycle facts for another session"
            )
        return state

    @classmethod
    def _require_ready_access_state(
        cls,
        state: object,
        expected_session: str,
    ) -> SessionLifecycleState:
        """Require exact ready durable state with access held by this process."""

        value = cls._require_lifecycle_state(state, expected_session)
        if (
            value.durable_state is not SessionDurableState.READY
            or value.access_state is not SessionAccessState.HELD_HERE
        ):
            raise BackendProtocolError(
                "backend did not return ready access held by this process"
            )
        return value

    @classmethod
    def _require_correlated_state(
        cls,
        initial: SessionLifecycleState | None,
        transition: object,
        observed: object,
        expected_session: str,
    ) -> SessionLifecycleState:
        """Require the transition result and immediate post-read to be identical."""

        first = cls._require_ready_access_state(transition, expected_session)
        second = cls._require_ready_access_state(observed, expected_session)
        if initial is not None:
            before = cls._require_lifecycle_state(initial, expected_session)
            if first.created_at != before.created_at:
                raise BackendProtocolError(
                    "session creation identity changed during access acquisition"
                )
            if (
                before.durable_state is SessionDurableState.READY
                and first.updated_at != before.updated_at
            ):
                raise BackendProtocolError(
                    "ready session changed during access acquisition"
                )
            if (
                before.durable_state is SessionDurableState.PAUSED
                and first.updated_at == before.updated_at
            ):
                raise BackendProtocolError(
                    "reopened session did not advance its durable timestamp"
                )
        if second != first:
            raise BackendProtocolError(
                "session lifecycle changed during access acquisition"
            )
        return second

    @classmethod
    def _require_released_state(
        cls,
        state: object,
        expected_session: str,
    ) -> SessionLifecycleState:
        """Require a valid post-release state no longer held by this process."""

        value = cls._require_lifecycle_state(state, expected_session)
        if value.access_state is SessionAccessState.HELD_HERE:
            raise BackendProtocolError(
                "backend retained process access after release"
            )
        return value

    def _provider_work_is_ready(self, state: SessionLifecycleState) -> bool:
        """Derive readiness from a read-only configuration validation and state."""

        try:
            configured = self._backend.validate_provider_configuration(
                state.session_id
            )
        except (BackendProtocolError, ConfigurationError):
            configured = False
        if type(configured) is not bool:
            raise BackendProtocolError(
                "provider configuration validation returned a non-boolean value"
            )
        return provider_work_is_ready(
            state,
            provider_configuration_ready=configured,
        )

    @staticmethod
    def _next_session_action(
        state: SessionLifecycleState | None,
        *,
        attached: bool,
        focused: bool,
        provider_ready: bool,
    ) -> str:
        """Return the fixed next action for one read-only lifecycle snapshot."""

        if state is None or state.durable_state is SessionDurableState.ABSENT:
            return "Create a session."
        if (
            state.access_state
            is SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER
        ):
            return "Close the owning local process normally, then retry."
        if state.access_state is SessionAccessState.OWNERSHIP_UNKNOWN:
            return "Verify ownership; do not remove the access claim."
        if state.durable_state is SessionDurableState.PAUSED:
            return "Open the session to make it ready."
        if state.access_state is SessionAccessState.CLAIMABLE:
            return "Open the session."
        if not provider_ready:
            return "Repair provider configuration, then retry."
        if attached and not focused:
            return "Create or choose a conversation."
        if attached and focused:
            return "Continue with the current focus."
        return "Open the session."

    def _read_session_lobby_commands(self) -> int:
        """Read and dispatch interactive actions until normal exit status 0.

        Main Ctrl-C clears only the current input operation. Main EOF, quit, or
        EOF during a call-local subject or confirmation read returns 0. Each
        attached input is preceded immediately by its current conversation
        scope; advisory turns run sequentially, and fatal transition or turn
        uncertainty reaches the outer backend cleanup boundary.
        """

        while True:
            if self._attached_session_id is not None:
                self._write_attached_conversation_scope()
            terminal_action = self._terminal.read_main(
                session_id=self._attached_session_id,
                agent_id=(
                    self._target_agent_id
                    if self._attached_session_id is not None
                    else None
                ),
            )
            if terminal_action.kind is TerminalActionKind.INTERRUPT:
                self._write_main_input_cancelled_status()
                continue
            if terminal_action.kind is TerminalActionKind.EOF:
                return 0
            if terminal_action.kind is not TerminalActionKind.LINE:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            assert terminal_action.text is not None
            original_line = terminal_action.text
            action = parse_interactive_line(original_line)
            if self._dispatch_session_lobby_action(action, original_line):
                return 0

    def _dispatch_session_lobby_action(
        self,
        action: InteractiveAction,
        original_line: str,
    ) -> bool:
        """Execute one parser action and return whether the loop should exit.

        ``original_line`` is used only to choose the trusted parse-error
        category. Global help/status, directives, inspection, focused history,
        roster, story-session navigation, search, and transcript reads remain
        provider-free; routed and consensus content use the retained runtime.
        The returned boolean signals loop exit; command-specific state changes
        are delegated to their owners, and fatal failures reach outer cleanup
        without local resource acquisition.
        """

        if action.kind is ActionKind.IGNORED:
            return False
        if action.kind is ActionKind.HELP:
            self._terminal.write_help(action.argument)
            return False
        if action.kind is ActionKind.LIST_AGENTS:
            self._terminal.write_agent_roster()
            return False
        if action.kind is ActionKind.SESSION_LIST:
            self._list_story_sessions()
            return False
        if action.kind is ActionKind.SESSION_CREATE:
            assert action.argument is not None
            self._create_and_attach_destination(action.argument)
            return False
        if action.kind is ActionKind.SESSION_OPEN:
            self._open_default_or_named_session(action.argument)
            return False
        if action.kind is ActionKind.SESSION_PAUSE:
            return self._pause_attached_session()
        if action.kind is ActionKind.QUIT:
            return True
        if action.kind is ActionKind.PARSE_ERROR:
            self._write_parser_error(action, original_line)
            return False
        if action.kind is ActionKind.STATUS:
            self._write_runtime_status()
            return False
        if action.kind in {
            ActionKind.SET_LANGUAGE,
            ActionKind.SET_PERSONA,
            ActionKind.RESET,
        }:
            self._apply_attached_runtime_directive(action)
            return False
        if action.kind is ActionKind.INSPECT:
            self._inspect_attached_runtime(action)
            return False
        if action.kind is ActionKind.HISTORY:
            self._write_focused_conversation_history(action)
            return False
        if action.kind is ActionKind.SET_TARGET:
            self._set_target_agent(action)
            return False
        if action.kind in {
            ActionKind.MESSAGE,
            ActionKind.ROUTED_MESSAGE,
        }:
            self._run_advisory_turn(action)
            return False
        if action.kind in {
            ActionKind.RUN,
            ActionKind.RUN_FRESH,
            ActionKind.DRAFT_FILE,
        }:
            return self._run_consensus_action(action)
        if action.kind in {ActionKind.SEARCH, ActionKind.TRANSCRIPT}:
            if self._attached_session_id is None:
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
                )
                return False
            if action.kind is ActionKind.SEARCH:
                self._search_attached_conversations(action)
            else:
                self._read_attached_conversation_transcript(action)
            return False
        if action.kind in {
            ActionKind.CONVERSATION_NEW,
            ActionKind.CONVERSATION_LIST,
            ActionKind.CONVERSATION_LIST_ALL,
            ActionKind.CONVERSATION_USE,
            ActionKind.CONVERSATION_SUBJECT,
            ActionKind.CONVERSATION_ARCHIVE,
        }:
            if self._attached_session_id is None:
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
                )
                return False
            if action.kind is ActionKind.CONVERSATION_NEW:
                return self._create_subject_conversation(action.argument)
            if action.kind in {
                ActionKind.CONVERSATION_LIST,
                ActionKind.CONVERSATION_LIST_ALL,
            }:
                self._list_subject_conversations(
                    include_archived=(
                        action.kind is ActionKind.CONVERSATION_LIST_ALL
                    ),
                    query=action.argument,
                )
                return False
            if action.kind is ActionKind.CONVERSATION_USE:
                assert action.argument is not None
                self._focus_subject_conversation(action.argument)
                return False
            if action.kind is ActionKind.CONVERSATION_SUBJECT:
                assert action.argument is not None
                self._rename_focused_conversation(action.argument)
                return False
            return self._archive_subject_conversation(action.argument)
        if (
            self._attached_session_id is not None
            and self._focused_conversation is None
        ):
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
            )
            return False
        self._write_unavailable_lobby_command_error()
        return False

    def _write_runtime_status(self) -> None:
        """Render a read-only lifecycle record followed by detailed scope facts."""

        default_session = self._context.default_session_id
        attached_session = self._attached_session_id
        candidate = attached_session or default_session
        if candidate is None:
            state = SessionLifecycleState(
                session_id="none",
                durable_state=SessionDurableState.ABSENT,
                access_state=SessionAccessState.CLAIMABLE,
                owner_hostname=None,
                owner_pid=None,
                created_at=None,
                updated_at=None,
            )
            provider_ready = False
            default_conversation = None
        else:
            try:
                state = self._backend.get_session_lifecycle(candidate)
                self._require_lifecycle_state(state, candidate)
                provider_ready = self._provider_work_is_ready(state)
                default_conversation = load_default_conversation(
                    self._workspace,
                    candidate,
                )
            except Exception as exc:
                self._present_session_failure(exc, candidate)
                return

        focus = self._focused_conversation
        focused_id = (
            focus.conversation_id
            if attached_session is not None and focus is not None
            else None
        )
        next_action = self._next_session_action(
            state if candidate is not None else None,
            attached=(attached_session == candidate and candidate is not None),
            focused=(focused_id is not None),
            provider_ready=provider_ready,
        )
        record = (
            f"Session: {candidate or 'none'} | "
            f"Durable state: {state.durable_state.value} | "
            f"Process access: {state.access_state.value} | "
            f"Default session: {default_session or 'none'} | "
            f"Current attachment: {attached_session or 'none'} | "
            f"Default conversation: {default_conversation or 'none'} | "
            f"Current focus: {focused_id or 'none'} | "
            f"Agent focus: "
            f"{self._target_agent_id if attached_session is not None else 'none'} | "
            f"Provider readiness: {'yes' if provider_ready else 'no'} | "
            f"Next action: {next_action}"
        )
        self._terminal.write_status(TerminalMessageKind.SESSION, record)

        if attached_session is None:
            return
        if type(self._conversation_memory_service) is not ConversationMemoryService:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        if focus is None:
            self._terminal.write_status(
                TerminalMessageKind.CONTEXT,
                "Conversation focus: none | Context: unavailable | Last run: none.",
            )
            return
        try:
            current = self._conversation_catalog.get(
                attached_session,
                focus.conversation_id,
            )
            current = self._validated_catalog_metadata(
                current,
                attached_session,
                expected_conversation_id=focus.conversation_id,
            )
            if current.status is ConversationStatus.ARCHIVED:
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
                )
                return
            events, context = self._read_focused_transcript_snapshot(current)
        except ConversationNotFoundError as exc:
            self._present_conversation_failure(exc)
            return
        except _ControllerAbort:
            raise
        except Exception as exc:
            self._present_transcript_service_failure(exc)
            return
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            (
                f"Current focus: {current.conversation_id} | "
                f"Subject: {current.subject} | Status: {current.status.value} | "
                f"Revision: {current.subject_revision}."
            ),
        )
        self._terminal.write_status(
            TerminalMessageKind.CONTEXT,
            self._format_transcript_context_status(
                displayed_events=len(events),
                total_events=len(events),
                context=context,
            ),
        )
        last_debate = self._last_started_debate_by_session.get(attached_session)
        if last_debate is None:
            last_run_status = "none."
        else:
            try:
                last_run_lines = self._read_safe_debate_inspection(
                    attached_session,
                    last_debate,
                    include_context=True,
                )
            except Exception as exc:
                self._present_runtime_inspection_failure(exc)
                return
            last_run_status = f"{last_debate} | {' | '.join(last_run_lines)}"
        self._terminal.write_status(
            TerminalMessageKind.RESULT,
            f"Search: available, active-only | Last interactive run: {last_run_status}",
        )

    def _apply_attached_runtime_directive(
        self,
        action: InteractiveAction,
    ) -> None:
        """Persist one parsed directive and acknowledge only its changed field.

        Language, canonical per-agent persona, and full reset actions require an
        attachment but never a conversation focus or provider. A malformed
        runtime return fails closed; typed configuration and workspace failures
        retain their established safe interactive categories.
        """

        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        if action.kind is ActionKind.SET_LANGUAGE:
            assert action.argument is not None
            directive = f"[SYS: LANG={action.argument}]"
            acknowledgement = f"Language override set to {action.argument}."
        elif action.kind is ActionKind.SET_PERSONA:
            assert action.agent_id is not None
            assert action.argument is not None
            directive = (
                f"[OVERRIDE: {action.agent_id} Persona={action.argument}]"
            )
            acknowledgement = (
                f"Persona override for {action.agent_id} set to "
                f"{action.argument}."
            )
        else:
            assert action.kind is ActionKind.RESET
            directive = "[RESET: ALL]"
            acknowledgement = "Runtime overrides reset."

        try:
            updated = self._runtime.apply_directive(
                self._attached_session_id,
                directive,
            )
        except (DirectiveError, ConfigurationError):
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONFIGURATION_INVALID)
            )
            return
        except WorkspaceSecurityError:
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_UNSAFE)
            )
            return
        except WorkspaceVersionUnsupportedError:
            self._present_memory_error(
                InteractiveError(
                    InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
                )
            )
            return
        except (WorkspaceFormatError, OSError):
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_INVALID)
            )
            return
        except Exception:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None
        if not self._directive_result_matches_action(updated, action):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        self._terminal.write_status(TerminalMessageKind.RESULT, acknowledgement)

    @staticmethod
    def _directive_result_matches_action(
        result: object,
        action: InteractiveAction,
    ) -> bool:
        """Return whether a runtime directive result confirms the exact action.

        The validation accepts only the canonical override mapping shape and
        never renders or retains unrelated override values. It performs no I/O
        and treats malformed collaborator data as an internal protocol failure.
        """

        if type(result) is not dict or set(result) != {
            "language",
            "persona",
            "agents",
        }:
            return False
        if result["language"] is not None and not isinstance(
            result["language"], str
        ):
            return False
        if result["persona"] is not None and not isinstance(
            result["persona"], str
        ):
            return False
        agents = result["agents"]
        if type(agents) is not dict or set(agents).difference(ROSTER):
            return False
        for agent_id, values in agents.items():
            if (
                type(agent_id) is not str
                or type(values) is not dict
                or set(values) != {"language", "persona"}
                or any(
                    value is not None and not isinstance(value, str)
                    for value in values.values()
                )
            ):
                return False

        if action.kind is ActionKind.SET_LANGUAGE:
            return result["language"] == action.argument
        if action.kind is ActionKind.SET_PERSONA:
            assert action.agent_id is not None
            values = agents.get(action.agent_id)
            return type(values) is dict and values["persona"] == action.argument
        return (
            action.kind is ActionKind.RESET
            and result["language"] is None
            and result["persona"] is None
            and not agents
        )

    def _write_focused_conversation_history(
        self,
        action: InteractiveAction,
    ) -> None:
        """Render the focused transcript snapshot and context accounting.

        The command requires an attached active focus. It reads the
        authoritative event store exactly once, derives bounded context from
        that exact tuple, and displays either all events or the requested newest
        suffix in chronological order. No cursor, mutation, search, or provider
        operation occurs. Ctrl-C stops rendering after any completed rows and
        returns to the same attached prompt.
        """

        try:
            self._write_focused_conversation_history_rows(action)
        except KeyboardInterrupt:
            self._terminal.write_status(
                TerminalMessageKind.CANCELLED,
                "History cancelled; attached state is unchanged.",
            )

    def _write_focused_conversation_history_rows(
        self,
        action: InteractiveAction,
    ) -> None:
        """Read once and render history rows until completion or interruption."""

        assert action.kind is ActionKind.HISTORY
        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        focused = self._require_active_conversation_focus()
        if focused is None:
            return
        try:
            events, context = self._read_focused_transcript_snapshot(focused)
        except _ControllerAbort:
            raise
        except Exception as exc:
            if isinstance(exc, ConversationNotFoundError):
                self._focused_conversation = None
            self._present_transcript_service_failure(exc)
            return

        displayed = events if action.count is None else events[-action.count :]
        for event in displayed:
            self._terminal.write_conversation_transcript_event(
                focused.conversation_id,
                sequence=event.sequence,
                turn_id=event.turn_id,
                kind=event.kind.value,
                sender=event.sender,
                recipient=event.recipient,
                content=event.content,
            )
        self._terminal.write_status(
            TerminalMessageKind.RESULT,
            self._format_transcript_context_status(
                displayed_events=len(displayed),
                total_events=len(events),
                context=context,
            ),
        )

    def _read_focused_transcript_snapshot(
        self,
        focused: ConversationMetadata,
    ) -> tuple[tuple[ConversationEvent, ...], ConversationContextWindow]:
        """Read the focused transcript once and derive canonical context.

        The retained filesystem adapter remains the authoritative read owner;
        the pure selector consumes the exact immutable tuple it returns. The
        method performs no append, provider call, search, or private reach-through.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        events = self._conversation_event_store.read(
            session_id,
            focused.conversation_id,
        )
        context = select_bounded_conversation_context(events)
        return events, context

    @staticmethod
    def _format_transcript_context_status(
        *,
        displayed_events: int,
        total_events: int,
        context: ConversationContextWindow,
    ) -> str:
        """Format exact transcript and fixed-policy context counts without I/O."""

        return (
            f"History displayed: {displayed_events}/{total_events} events | "
            f"Complete turns: {context.total_complete_turns} | "
            f"Context retained: {context.retained_turns} turns, "
            f"{len(context.events)} events | Omitted turns: "
            f"{context.omitted_turns} | UTF-8 bytes: "
            f"{context.retained_utf8_bytes}/"
            f"{CONVERSATION_CONTEXT_POLICY.max_utf8_bytes} | Turn cap: "
            f"{CONVERSATION_CONTEXT_POLICY.max_complete_turns} | Truncated: "
            f"{'yes' if context.truncated else 'no'}."
        )

    def _inspect_attached_runtime(self, action: InteractiveAction) -> None:
        """Render a safe attached-session or debate inspection projection.

        Bare inspection lists only whitelisted persisted debate summaries.
        Explicit IDs are read exactly as supplied, while ``last`` resolves the
        process-local latest accepted STARTED run for this attached session.
        Inspection never updates that mapping or renders persisted content,
        errors, raw state, provider data, or candidate text.
        """

        assert action.kind is ActionKind.INSPECT
        session_id = self._attached_session_id
        if session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        if action.argument is None:
            try:
                value = self._runtime.inspect_session(session_id)
                summaries = self._validated_session_inspection_summaries(
                    value,
                    session_id=session_id,
                )
            except Exception as exc:
                self._present_runtime_inspection_failure(exc)
                return
            self._terminal.write_status(
                TerminalMessageKind.RESULT,
                (
                    f"Session {session_id}: {len(summaries)} persisted "
                    f"{'debate' if len(summaries) == 1 else 'debates'}."
                ),
            )
            for debate_id, summary in summaries:
                self._terminal.write_run(debate_id, summary)
            return

        if action.argument == "last":
            debate_id = self._last_started_debate_by_session.get(session_id)
            if debate_id is None:
                self._terminal.write_status(
                    TerminalMessageKind.RESULT,
                    (
                        "No interactive consensus run has started in "
                        f"session {session_id} during this process."
                    ),
                )
                return
        else:
            debate_id = action.argument

        try:
            lines = self._read_safe_debate_inspection(
                session_id,
                debate_id,
                include_context=True,
            )
        except Exception as exc:
            self._present_runtime_inspection_failure(exc)
            return
        for line in lines:
            self._terminal.write_run(debate_id, line)

    def _read_safe_debate_inspection(
        self,
        session_id: str,
        debate_id: str,
        *,
        include_context: bool,
    ) -> tuple[str, ...]:
        """Read and whitelist one debate plus optional context-sidecar facts.

        The returned lines contain only identity, lifecycle, input, revision,
        round, artifact, and context identity/mode/count fields. Raw messages,
        candidate/error/action values, state dictionaries, event content, and
        provider data are validated by their owners but never copied to output.
        """

        inspected = self._runtime.inspect_debate(session_id, debate_id)
        summary, status = self._validated_debate_inspection_summary(
            inspected,
            session_id=session_id,
            debate_id=debate_id,
        )
        if not include_context or status == "not_found":
            return (summary,)
        context = self._runtime.inspect_debate_context(session_id, debate_id)
        context_summary = self._validated_debate_context_summary(
            context,
            session_id=session_id,
            debate_id=debate_id,
        )
        return (summary, context_summary)

    def _validated_debate_inspection_summary(
        self,
        value: object,
        *,
        session_id: str,
        debate_id: str,
    ) -> tuple[str, str]:
        """Validate one full runtime inspection and return safe summary text."""

        expected = {
            "session_id",
            "debate_id",
            "status",
            "terminal",
            "state",
            "result",
            "messages",
        }
        if type(value) is not dict or set(value) != expected:
            raise ValueError("runtime debate inspection has an invalid shape")
        if (
            value["session_id"] != session_id
            or value["debate_id"] != debate_id
            or type(value["messages"]) is not list
            or any(type(message) is not dict for message in value["messages"])
        ):
            raise ValueError("runtime debate inspection identity is invalid")
        status = value["status"]
        terminal = value["terminal"]
        if status not in {
            "not_found",
            "active",
            "consensus",
            "deadlock",
            "failed",
            "interrupted",
        } or type(terminal) is not bool:
            raise ValueError("runtime debate inspection lifecycle is invalid")

        state = value["state"]
        result = value["result"]
        input_kind: object = None
        revision_id: object = None
        rounds_completed: object = 0
        artifact: object = None
        if result is not None:
            result_fields = {
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "revision_id",
                "rounds_completed",
                "final_candidate",
                "latest_candidate",
                "manuscript_path",
                "core_updates",
                "error",
                "action_required",
            }
            if type(result) is not dict or set(result) != result_fields:
                raise ValueError("runtime debate result has an invalid shape")
            if terminal is not True:
                raise ValueError("runtime terminal debate sources are inconsistent")
            if (
                result["session_id"] != session_id
                or result["debate_id"] != debate_id
                or result["status"] != status
            ):
                raise ValueError("runtime debate result identity is invalid")
            if state is not None:
                state_fields = {
                    "schema_version",
                    "session_id",
                    "debate_id",
                    "input_kind",
                    "status",
                    "terminal",
                    "created_at",
                    "updated_at",
                    "revision_id",
                    "rounds_completed",
                    "error",
                }
                if type(state) is not dict or set(state) != state_fields:
                    raise ValueError("runtime debate state has an invalid shape")
                if (
                    state["session_id"] != session_id
                    or state["debate_id"] != debate_id
                    or state["status"] != status
                    or state["terminal"] is not True
                    or state["input_kind"] != result["input_kind"]
                    or state["revision_id"] != result["revision_id"]
                    or state["rounds_completed"] != result["rounds_completed"]
                ):
                    raise ValueError("runtime debate state identity is invalid")
            input_kind = result["input_kind"]
            revision_id = result["revision_id"]
            rounds_completed = result["rounds_completed"]
            artifact = result["manuscript_path"]
        elif state is not None:
            state_fields = {
                "schema_version",
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "terminal",
                "created_at",
                "updated_at",
                "revision_id",
                "rounds_completed",
                "error",
            }
            if type(state) is not dict or set(state) != state_fields:
                raise ValueError("runtime debate state has an invalid shape")
            if (
                state["session_id"] != session_id
                or state["debate_id"] != debate_id
                or state["status"] != status
                or state["terminal"] is not terminal
            ):
                raise ValueError("runtime debate state identity is invalid")
            input_kind = state["input_kind"]
            revision_id = state["revision_id"]
            rounds_completed = state["rounds_completed"]
        elif status == "not_found":
            if terminal or value["messages"]:
                raise ValueError("not-found debate inspection is inconsistent")
        elif status != "interrupted" or terminal is not True or not value["messages"]:
            raise ValueError("runtime debate inspection has no lifecycle source")

        summary = self._format_validated_debate_summary(
            session_id=session_id,
            status=status,
            terminal=terminal,
            input_kind=input_kind,
            revision_id=revision_id,
            rounds_completed=rounds_completed,
            artifact=artifact,
        )
        return summary, status

    def _validated_session_inspection_summaries(
        self,
        value: object,
        *,
        session_id: str,
    ) -> tuple[tuple[str, str], ...]:
        """Validate a session inspection and project ordered safe debate rows."""

        if type(value) is not dict or set(value) != {
            "session_id",
            "overrides",
            "debates",
        }:
            raise ValueError("runtime session inspection has an invalid shape")
        if (
            value["session_id"] != session_id
            or type(value["overrides"]) is not dict
            or type(value["debates"]) is not list
        ):
            raise ValueError("runtime session inspection is invalid")

        summaries: list[tuple[str, str]] = []
        previous_debate_id: str | None = None
        for row in value["debates"]:
            if type(row) is not dict:
                raise ValueError("runtime session debate summary is invalid")
            result_fields = {
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "revision_id",
                "rounds_completed",
                "final_candidate",
                "latest_candidate",
                "manuscript_path",
                "core_updates",
                "error",
                "action_required",
                "terminal",
            }
            state_fields = {
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "terminal",
                "revision_id",
                "rounds_completed",
                "error",
            }
            fields = set(row)
            if fields == result_fields:
                if row["terminal"] is not True:
                    raise ValueError(
                        "runtime session terminal summary is inconsistent"
                    )
                artifact = row["manuscript_path"]
            elif fields == state_fields:
                artifact = None
            else:
                raise ValueError("runtime session debate summary has an invalid shape")
            if row["session_id"] != session_id:
                raise ValueError("runtime session debate identity is invalid")
            debate_id = row["debate_id"]
            self._validate_safe_inspection_identifier(
                debate_id,
                label="debate ID",
            )
            if previous_debate_id is not None and debate_id <= previous_debate_id:
                raise ValueError("runtime session debates are not uniquely ordered")
            previous_debate_id = debate_id
            summary = self._format_validated_debate_summary(
                session_id=session_id,
                status=row["status"],
                terminal=row["terminal"],
                input_kind=row["input_kind"],
                revision_id=row["revision_id"],
                rounds_completed=row["rounds_completed"],
                artifact=artifact,
            )
            summaries.append((debate_id, summary))
        return tuple(summaries)

    def _format_validated_debate_summary(
        self,
        *,
        session_id: str,
        status: object,
        terminal: object,
        input_kind: object,
        revision_id: object,
        rounds_completed: object,
        artifact: object,
    ) -> str:
        """Validate whitelisted lifecycle fields and format one safe summary."""

        if status not in {
            "not_found",
            "active",
            "consensus",
            "deadlock",
            "failed",
            "interrupted",
        }:
            raise ValueError("runtime debate status is invalid")
        if type(terminal) is not bool:
            raise ValueError("runtime debate terminal flag is invalid")
        if input_kind is not None and input_kind not in {
            InputKind.PROMPT.value,
            InputKind.DRAFT.value,
        }:
            raise ValueError("runtime debate input kind is invalid")
        if revision_id is not None:
            self._validate_safe_inspection_identifier(
                revision_id,
                label="revision ID",
            )
        if (
            isinstance(rounds_completed, bool)
            or not isinstance(rounds_completed, int)
            or rounds_completed < 0
        ):
            raise ValueError("runtime debate round count is invalid")
        if artifact is not None and not isinstance(artifact, str):
            raise ValueError("runtime debate artifact is invalid")
        return (
            f"Session: {session_id} | Status: {status} | Terminal: "
            f"{'yes' if terminal else 'no'} | Input: {input_kind or 'none'} | "
            f"Revision: {revision_id or 'none'} | Rounds: {rounds_completed} | "
            f"Artifact: {artifact or 'none'}."
        )

    def _validated_debate_context_summary(
        self,
        value: object,
        *,
        session_id: str,
        debate_id: str,
    ) -> str:
        """Project a strict sidecar to identity, mode, and count facts only."""

        if value is None:
            return "Context sidecar: none."
        if type(value) is not dict or set(value) != {
            "schema_version",
            "session_id",
            "debate_id",
            "conversation_id",
            "mode",
            "snapshot",
        }:
            raise ValueError("runtime debate context has an invalid shape")
        if (
            value["schema_version"] != 1
            or value["session_id"] != session_id
            or value["debate_id"] != debate_id
        ):
            raise ValueError("runtime debate context identity is invalid")
        conversation_id = value["conversation_id"]
        self._validate_safe_inspection_identifier(
            conversation_id,
            label="conversation ID",
        )
        mode = value["mode"]
        snapshot = value["snapshot"]
        if mode == DebateContextMode.FRESH.value:
            if snapshot is not None:
                raise ValueError("fresh debate context has a snapshot")
            return (
                f"Context: conversation {conversation_id} | Mode: fresh | "
                "Events: 0 | Complete turns: 0 | Retained turns: 0 | "
                "Omitted turns: 0 | UTF-8 bytes: 0 | Truncated: no."
            )
        if mode != DebateContextMode.INCLUDED.value or type(snapshot) is not dict:
            raise ValueError("runtime debate context mode is invalid")
        if set(snapshot) != {
            "events",
            "total_complete_turns",
            "retained_turns",
            "omitted_turns",
            "retained_utf8_bytes",
            "truncated",
        } or type(snapshot["events"]) is not list:
            raise ValueError("runtime debate context snapshot is invalid")
        event_fields = {
            "schema_version",
            "session_id",
            "conversation_id",
            "turn_id",
            "sequence",
            "timestamp",
            "sender",
            "recipient",
            "kind",
            "content",
        }
        if any(
            type(event) is not dict or set(event) != event_fields
            for event in snapshot["events"]
        ):
            raise ValueError("runtime debate context events are invalid")
        total = snapshot["total_complete_turns"]
        retained = snapshot["retained_turns"]
        omitted = snapshot["omitted_turns"]
        retained_bytes = snapshot["retained_utf8_bytes"]
        truncated = snapshot["truncated"]
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in (total, retained, omitted, retained_bytes)
        ) or type(truncated) is not bool:
            raise ValueError("runtime debate context counts are invalid")
        if (
            total != retained + omitted
            or len(snapshot["events"]) != retained * 2
            or truncated is not (omitted > 0)
        ):
            raise ValueError("runtime debate context counts are inconsistent")
        return (
            f"Context: conversation {conversation_id} | Mode: included | "
            f"Events: {len(snapshot['events'])} | Complete turns: {total} | "
            f"Retained turns: {retained} | Omitted turns: {omitted} | "
            f"UTF-8 bytes: {retained_bytes} | Truncated: "
            f"{'yes' if truncated else 'no'}."
        )

    def _validate_safe_inspection_identifier(
        self,
        value: object,
        *,
        label: str,
    ) -> str:
        """Return a canonical safe identifier or convert tampering to ValueError."""

        try:
            return self._workspace.validate_identifier(value, label=label)
        except (SessionIdError, TypeError, ValueError) as exc:
            raise ValueError(f"runtime inspection {label} is invalid") from exc

    def _present_runtime_inspection_failure(self, failure: Exception) -> None:
        """Render typed inspection failures or abort on collaborator tampering."""

        if isinstance(failure, SessionIdError):
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.COMMAND_INVALID)
            )
            return
        if isinstance(failure, WorkspaceSecurityError):
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_UNSAFE)
            )
            return
        if isinstance(failure, WorkspaceVersionUnsupportedError):
            self._present_memory_error(
                InteractiveError(
                    InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
                )
            )
            return
        if isinstance(failure, (WorkspaceFormatError, OSError)):
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_INVALID)
            )
            return
        raise _ControllerAbort(
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        ) from None

    def _set_target_agent(self, action: InteractiveAction) -> None:
        """Set one attached process's sticky responder without provider work.

        The validated action supplies a canonical agent and the method returns
        no value. An unattached controller renders ``session_not_attached``;
        success mutates only process-local routing and needs no cleanup.
        """

        assert action.kind is ActionKind.SET_TARGET
        assert action.agent_id is not None
        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        self._target_agent_id = action.agent_id
        self._terminal.write_status(
            TerminalMessageKind.ROUTING,
            f"Sticky target set to {action.agent_id}.",
        )

    def _run_advisory_turn(self, action: InteractiveAction) -> None:
        """Run one focused-conversation retrieval turn through the runtime.

        ``MESSAGE`` uses the sticky target and ``ROUTED_MESSAGE`` uses its
        one-shot canonical agent without changing sticky state. The method
        revalidates attachment and focus, passes the retained attachment-
        scoped memory service, validates and renders content-free progress,
        and submits exactly one coroutine to the controller-owned Runner before
        rendering the validated answer and sources. A first Ctrl-C is successful
        before coroutine entry only when the Runner is quiescent, or after entry
        only when cancellation reaches and joins the runtime coroutine.
        ``StoryRuntime`` remains the sole durability owner. Typed semantic and
        budget failures return to the prompt; observer, invalid-result, or
        uncertain ownership failures abort into the ordered cleanup boundary.
        """

        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        focused = self._require_active_conversation_focus()
        if focused is None:
            return

        session_id = self._attached_session_id
        assert session_id is not None
        assert action.text is not None
        if action.kind is ActionKind.ROUTED_MESSAGE:
            assert action.agent_id is not None
            agent_id = action.agent_id
            routing = (
                f"One-shot route to {agent_id}; sticky target remains "
                f"{self._target_agent_id}. "
            )
        else:
            assert action.kind is ActionKind.MESSAGE
            agent_id = self._target_agent_id
            routing = f"Routing this turn to {agent_id}. "
        display_name = AGENT_SPECS[agent_id].display_name
        routing += (
            "Direct routing selects the responder in this shared room; "
            "it does not make the turn private."
        )
        self._terminal.write_status(TerminalMessageKind.ROUTING, routing)
        self._terminal.write_status(
            TerminalMessageKind.WAITING,
            f"{display_name} is responding; Ctrl-C cancels this turn.",
        )

        memory_service = self._conversation_memory_service
        if type(memory_service) is not ConversationMemoryService:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        progress_events: list[ConversationRetrievalProgressEvent] = []
        progress_callback_failure: BaseException | None = None

        def accept_and_render_progress(
            event: ConversationRetrievalProgressEvent,
        ) -> None:
            """Retain callback failure identity while accepting one event."""

            nonlocal progress_callback_failure
            try:
                self._accept_conversation_retrieval_progress(
                    event,
                    expected_session_id=session_id,
                    expected_conversation_id=focused.conversation_id,
                    expected_agent_id=agent_id,
                    events=progress_events,
                )
            except BaseException as exc:
                progress_callback_failure = exc
                raise

        operation_entered = False
        cancellation_reached_operation = False
        operation_joined = False

        async def run_joined_advisory_operation() -> ConversationRetrievalResult:
            """Await the turn while recording cancellation and final joining."""

            nonlocal operation_entered
            nonlocal cancellation_reached_operation
            nonlocal operation_joined
            operation_entered = True
            try:
                return await self._runtime.converse_with_retrieval(
                    session_id,
                    focused.conversation_id,
                    agent_id,
                    action.text,
                    memory_service=memory_service,
                    progress_sink=accept_and_render_progress,
                )
            except asyncio.CancelledError:
                cancellation_reached_operation = True
                raise
            finally:
                operation_joined = True

        runner = self._runner
        if runner is None:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        operation_coroutine = run_joined_advisory_operation()
        try:
            result = runner.run(operation_coroutine)
        except KeyboardInterrupt:
            if progress_callback_failure is not None:
                self._raise_progress_callback_abort(progress_callback_failure)
            if not operation_entered:
                cancellation_is_safe = self._interactive_runner_is_quiescent()
            else:
                cancellation_is_safe = (
                    cancellation_reached_operation and operation_joined
                )
            if not cancellation_is_safe:
                raise _ControllerAbort(
                    InteractiveError(
                        InteractiveErrorCode.OPERATION_INTERRUPTED
                    )
                ) from None
            self._terminal.write_status(
                TerminalMessageKind.CANCELLED,
                f"{display_name} turn was interrupted.",
            )
            self._terminal.write_status(
                TerminalMessageKind.HINT,
                (
                    "The story session remains attached; the affected "
                    "provider thread may restart on the next message."
                )
            )
            return
        except asyncio.CancelledError:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.OPERATION_INTERRUPTED)
            ) from None
        except ConversationRetrievalObserverError as exc:
            self._raise_progress_callback_abort(
                progress_callback_failure or exc.__cause__ or exc
            )
        except Exception as exc:
            if progress_callback_failure is not None:
                self._raise_progress_callback_abort(progress_callback_failure)
            self._present_advisory_turn_failure(exc)
            return
        finally:
            if not operation_entered:
                operation_coroutine.close()

        try:
            result = self._validated_conversation_retrieval_result(
                result,
                expected_session_id=session_id,
                expected_conversation_id=focused.conversation_id,
                expected_agent_id=agent_id,
                progress_events=progress_events,
            )
        except (AttributeError, TypeError, ValueError):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None
        if result.context.truncated:
            event_count = len(result.context.events)
            retained_turns = result.context.retained_turns
            omitted_turns = result.context.omitted_turns
            self._terminal.write_status(
                TerminalMessageKind.CONTEXT,
                (
                    f"Context truncated: {event_count} "
                    f"{'event' if event_count == 1 else 'events'} retained "
                    f"across {retained_turns} "
                    f"{'turn' if retained_turns == 1 else 'turns'}; "
                    f"{omitted_turns} earlier "
                    f"{'turn' if omitted_turns == 1 else 'turns'} omitted."
                ),
            )
        self._terminal.write_agent(agent_id, result.content)
        self._render_conversation_retrieval_sources(result.sources)

    def _accept_conversation_retrieval_progress(
        self,
        event: ConversationRetrievalProgressEvent,
        *,
        expected_session_id: str,
        expected_conversation_id: str,
        expected_agent_id: str,
        events: list[ConversationRetrievalProgressEvent],
    ) -> None:
        """Validate, render, and retain one trusted retrieval progress fact.

        The exact event is reconstructed and correlated with this advisory turn.
        Search/read started-completed pairs and their action indexes must follow
        the bounded phase order. Rendering mutates terminal output only; invalid
        input or writer failure propagates through the runtime observer boundary
        and cannot add transcript events here.
        """

        if type(event) is not ConversationRetrievalProgressEvent:
            raise TypeError(
                "retrieval progress sink requires a "
                "ConversationRetrievalProgressEvent"
            )
        try:
            canonical = ConversationRetrievalProgressEvent(
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                agent_id=event.agent_id,
                action_index=event.action_index,
                phase=event.phase,
                search_hit_count=event.search_hit_count,
                transcript_turn_count=event.transcript_turn_count,
                transcript_event_count=event.transcript_event_count,
                observation_utf8_bytes=event.observation_utf8_bytes,
                truncated=event.truncated,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise TypeError("retrieval progress event is invalid") from exc
        if (
            canonical.session_id != expected_session_id
            or canonical.conversation_id != expected_conversation_id
            or canonical.agent_id != expected_agent_id
        ):
            raise ValueError("retrieval progress identity does not match the turn")

        previous = events[-1] if events else None
        if previous is None:
            if (
                canonical.phase
                is not ConversationRetrievalProgressPhase.SEARCH_STARTED
                or canonical.action_index != 1
            ):
                raise ValueError("retrieval progress must begin with search action 1")
        elif canonical.turn_id != previous.turn_id:
            raise ValueError("retrieval progress turn identity changed")
        elif previous.phase is ConversationRetrievalProgressPhase.SEARCH_STARTED:
            if (
                canonical.phase
                is not ConversationRetrievalProgressPhase.SEARCH_COMPLETED
                or canonical.action_index != previous.action_index
            ):
                raise ValueError("retrieval search progress pair is invalid")
        elif previous.phase is ConversationRetrievalProgressPhase.READ_STARTED:
            if (
                canonical.phase
                is not ConversationRetrievalProgressPhase.READ_COMPLETED
                or canonical.action_index != previous.action_index
            ):
                raise ValueError("retrieval read progress pair is invalid")
        else:
            expected_index = previous.action_index + 1
            if canonical.action_index != expected_index:
                raise ValueError("retrieval progress action index is not sequential")
            if previous.phase is ConversationRetrievalProgressPhase.SEARCH_COMPLETED:
                allowed = {
                    ConversationRetrievalProgressPhase.SEARCH_STARTED,
                    ConversationRetrievalProgressPhase.READ_STARTED,
                }
            else:
                assert (
                    previous.phase
                    is ConversationRetrievalProgressPhase.READ_COMPLETED
                )
                allowed = {ConversationRetrievalProgressPhase.READ_STARTED}
            if canonical.phase not in allowed:
                raise ValueError("retrieval progress phase order is invalid")

        self._render_conversation_retrieval_progress(canonical)
        events.append(canonical)

    def _render_conversation_retrieval_progress(
        self,
        event: ConversationRetrievalProgressEvent,
    ) -> None:
        """Render one validated content-free fact with the fixed context copy.

        Only decimal typed counters, the fixed phase wording, and ``yes``/``no``
        truncation are written through the existing sanitized status renderer.
        No conversation transcript, controller state, or provider resource is
        mutated; terminal writer failures propagate unchanged.
        """

        phase = event.phase
        if phase is ConversationRetrievalProgressPhase.SEARCH_STARTED:
            text = f"Retrieval search action {event.action_index} started."
        elif phase is ConversationRetrievalProgressPhase.SEARCH_COMPLETED:
            text = (
                f"Retrieval search action {event.action_index} completed: "
                f"{event.search_hit_count} hits; "
                f"{event.observation_utf8_bytes} observation bytes; "
                f"truncated: {'yes' if event.truncated else 'no'}."
            )
        elif phase is ConversationRetrievalProgressPhase.READ_STARTED:
            text = f"Retrieval read action {event.action_index} started."
        else:
            assert phase is ConversationRetrievalProgressPhase.READ_COMPLETED
            text = (
                f"Retrieval read action {event.action_index} completed: "
                f"{event.transcript_turn_count} turns; "
                f"{event.transcript_event_count} events; "
                f"{event.observation_utf8_bytes} observation bytes; "
                f"truncated: {'yes' if event.truncated else 'no'}."
            )
        self._terminal.write_status(TerminalMessageKind.CONTEXT, text)

    @staticmethod
    def _validated_conversation_retrieval_result(
        value: object,
        *,
        expected_session_id: str,
        expected_conversation_id: str,
        expected_agent_id: str,
        progress_events: list[ConversationRetrievalProgressEvent],
    ) -> ConversationRetrievalResult:
        """Return one exact correlated result after pure deep reconstruction.

        Sources and citations are copied into exact built-in contracts, progress
        must end on a completed pair when present, and its turn must match the
        result. No output, provider state, controller state, or transcript is
        mutated; invalid collaborator values raise ``TypeError`` or ``ValueError``.
        """

        if type(value) is not ConversationRetrievalResult:
            raise TypeError("runtime returned an invalid retrieval result")
        sources: list[ConversationRetrievalSource] = []
        if type(value.sources) is not tuple:
            raise TypeError("retrieval result sources must be a tuple")
        for source in value.sources:
            if type(source) is not ConversationRetrievalSource:
                raise TypeError("retrieval result source is invalid")
            citation = source.citation
            if type(citation) is not ConversationCitation:
                raise TypeError("retrieval result citation is invalid")
            sources.append(
                ConversationRetrievalSource(
                    subject=source.subject,
                    citation=ConversationCitation(
                        session_id=citation.session_id,
                        conversation_id=citation.conversation_id,
                        turn_id=citation.turn_id,
                        sequence=citation.sequence,
                        event_kind=citation.event_kind,
                    ),
                )
            )
        canonical = ConversationRetrievalResult(
            session_id=value.session_id,
            conversation_id=value.conversation_id,
            turn_id=value.turn_id,
            agent_id=value.agent_id,
            content=value.content,
            context=value.context,
            sources=tuple(sources),
        )
        if (
            canonical.session_id != expected_session_id
            or canonical.conversation_id != expected_conversation_id
            or canonical.agent_id != expected_agent_id
        ):
            raise ValueError("retrieval result identity does not match its turn")
        if canonical.sources and not progress_events:
            raise ValueError("retrieval sources require completed search progress")
        if progress_events:
            final_progress = progress_events[-1]
            if final_progress.phase not in {
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                ConversationRetrievalProgressPhase.READ_COMPLETED,
            }:
                raise ValueError("retrieval progress ended before completion")
            if final_progress.turn_id != canonical.turn_id:
                raise ValueError("retrieval progress turn does not match its result")
        return canonical

    def _render_conversation_retrieval_sources(
        self,
        sources: tuple[ConversationRetrievalSource, ...],
    ) -> None:
        """Render validated ordered sources through existing sanitized status I/O.

        Subject-only citations omit event fields; exact event citations include
        turn, sequence, and event-kind values. Rendering is non-durable and does
        not mutate focus, routing, memory, or transcripts. Invalid sources
        and terminal writer failures propagate to fatal controller cleanup.
        """

        for ordinal, source in enumerate(sources, start=1):
            citation = source.citation
            text = (
                f"Source {ordinal}: Subject: {source.subject} | "
                f"Conversation: {citation.conversation_id}"
            )
            if citation.sequence is not None:
                assert citation.turn_id is not None
                assert citation.event_kind is not None
                text += (
                    f" | Turn: {citation.turn_id} | Sequence: "
                    f"{citation.sequence} | Kind: {citation.event_kind.value}"
                )
            self._terminal.write_status(TerminalMessageKind.CONTEXT, text)

    def _run_consensus_action(self, action: InteractiveAction) -> bool:
        """Execute one interactive prompt, fresh prompt, or draft-file debate.

        The attached session and focused active conversation are revalidated
        before composition or file access. Bare ``/run`` submits only a terminal
        ``SEND`` result; cancel and interrupt discard locally, while compose EOF
        requests normal shell exit. Draft files use the shared strict loader.
        Accepted input reaches the controller-owned Runner, and runtime context,
        sidecar, progress, provider, result, and artifact durability remain owned
        by ``StoryRuntime``. A joined cancellation returns to the shell only with
        trusted interruption proof or confirmed pre-start state.
        """

        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return False
        focused = self._require_active_conversation_focus()
        if focused is None:
            return False

        if action.kind is ActionKind.RUN:
            prompt = action.text
            if prompt is None:
                composed = self._terminal.compose(ComposeMode.RUN)
                if composed.kind is TerminalActionKind.EOF:
                    return True
                if composed.kind in {
                    TerminalActionKind.CANCEL,
                    TerminalActionKind.INTERRUPT,
                }:
                    return False
                if composed.kind is not TerminalActionKind.SEND:
                    raise _ControllerAbort(
                        InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                    )
                assert composed.text is not None
                prompt = composed.text
            input_kind = InputKind.PROMPT
            context_mode = DebateContextMode.INCLUDED
            payload = prompt
        elif action.kind is ActionKind.RUN_FRESH:
            assert action.text is not None
            input_kind = InputKind.PROMPT
            context_mode = DebateContextMode.FRESH
            payload = action.text
        else:
            assert action.kind is ActionKind.DRAFT_FILE
            assert action.argument is not None
            try:
                payload = read_draft_file(action.argument)
            except ConfigurationError:
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.DRAFT_INPUT_INVALID)
                )
                return False
            input_kind = InputKind.DRAFT
            context_mode = DebateContextMode.INCLUDED

        draft_payload_was_empty = input_kind is InputKind.DRAFT and payload == ""
        session_id = self._attached_session_id
        assert session_id is not None
        request = DebateContextRequest(
            conversation_id=focused.conversation_id,
            mode=context_mode,
        )
        progress_events: list[DebateProgressEvent] = []
        progress_callback_failure: BaseException | None = None

        def accept_and_render_progress(event: DebateProgressEvent) -> None:
            """Retain callback failure identity while accepting one event."""

            nonlocal progress_callback_failure
            try:
                self._accept_debate_progress(
                    event,
                    expected_session_id=session_id,
                    expected_input_kind=input_kind,
                    expected_context_mode=context_mode,
                    events=progress_events,
                )
            except BaseException as exc:
                progress_callback_failure = exc
                raise

        operation_entered = False
        cancellation_reached_operation = False
        operation_joined = False

        async def run_joined_consensus_operation() -> DebateResult:
            """Await the debate while recording cancellation and final joining."""

            nonlocal operation_entered
            nonlocal cancellation_reached_operation
            nonlocal operation_joined
            operation_entered = True
            try:
                if input_kind is InputKind.PROMPT:
                    return await self._runtime.run_prompt(
                        session_id,
                        payload,
                        conversation_context=request,
                        progress_sink=accept_and_render_progress,
                    )
                return await self._runtime.run_draft(
                    session_id,
                    payload,
                    conversation_context=request,
                    progress_sink=accept_and_render_progress,
                )
            except asyncio.CancelledError:
                cancellation_reached_operation = True
                raise
            finally:
                operation_joined = True

        runner = self._runner
        if runner is None:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        operation_coroutine = run_joined_consensus_operation()
        try:
            result = runner.run(operation_coroutine)
        except KeyboardInterrupt:
            if progress_callback_failure is not None:
                self._raise_progress_callback_abort(progress_callback_failure)
            if not operation_entered:
                if not self._interactive_runner_is_quiescent():
                    raise _ControllerAbort(
                        InteractiveError(
                            InteractiveErrorCode.OPERATION_INTERRUPTED
                        )
                    ) from None
                self._write_consensus_cancelled_before_start()
                return False
            if not cancellation_reached_operation or not operation_joined:
                raise _ControllerAbort(
                    InteractiveError(
                        InteractiveErrorCode.OPERATION_INTERRUPTED
                    )
                ) from None
            self._present_joined_consensus_cancellation(
                session_id,
                progress_events,
            )
            return False
        except asyncio.CancelledError:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.OPERATION_INTERRUPTED)
            ) from None
        except ProgressObserverError as exc:
            self._raise_progress_callback_abort(
                progress_callback_failure or exc.__cause__ or exc
            )
        except Exception as exc:
            if progress_callback_failure is not None:
                self._raise_progress_callback_abort(progress_callback_failure)
            self._present_consensus_execution_failure(
                exc,
                draft_payload_was_empty=draft_payload_was_empty,
                progress_events=progress_events,
            )
            return False
        finally:
            if not operation_entered:
                operation_coroutine.close()

        terminal_event, artifact_event = self._validated_consensus_result(
            result,
            expected_session_id=session_id,
            expected_input_kind=input_kind,
            progress_events=progress_events,
        )
        self._present_consensus_result(result, terminal_event, artifact_event)
        return False

    def _present_joined_consensus_cancellation(
        self,
        session_id: str,
        progress_events: list[DebateProgressEvent],
    ) -> None:
        """Render a joined cancellation or fail closed without durable proof.

        A trusted terminal ``INTERRUPTED`` event proves the immutable persisted
        outcome and has already rendered its sole run row. An entered operation
        with no event is still unproven because reservation may precede progress.
        Every entered run without that terminal proof fails closed rather than
        treating Runner quiescence or retained access as durability evidence.
        """

        terminal_event = progress_events[-1] if progress_events else None
        if (
            terminal_event is not None
            and terminal_event.phase is DebateProgressPhase.INTERRUPTED
        ):
            self._terminal.write_status(
                TerminalMessageKind.CANCELLED,
                f"Debate {terminal_event.debate_id} was marked interrupted.",
            )
            self._terminal.write_status(
                TerminalMessageKind.HINT,
                "It cannot resume. Use /run to create a new debate.",
            )
            self._terminal.write_status(
                TerminalMessageKind.SESSION,
                f'"{session_id}" remains ready and attached.',
            )
            return

        self._write_error_best_effort(
            InteractiveError(InteractiveErrorCode.OPERATION_INTERRUPTED)
        )
        self._abandon_known_attachment_for_cleanup_abort()

    def _write_consensus_cancelled_before_start(self) -> None:
        """Render the fixed pre-reservation cancellation acknowledgement."""

        self._terminal.write_status(
            TerminalMessageKind.CANCELLED,
            "Consensus run was cancelled before start.",
        )

    def _interactive_runner_is_quiescent(self) -> bool:
        """Return whether the owned Runner has no unfinished public tasks."""

        runner = self._runner
        if runner is None:
            return False
        try:
            return not asyncio.all_tasks(runner.get_loop())
        except (Exception, KeyboardInterrupt):
            return False

    @staticmethod
    def _raise_progress_callback_abort(failure: BaseException) -> None:
        """Raise the fixed fatal code for one synchronous progress failure."""

        code = (
            InteractiveErrorCode.OPERATION_INTERRUPTED
            if isinstance(failure, (OSError, KeyboardInterrupt))
            else InteractiveErrorCode.INTERNAL_ERROR
        )
        raise _ControllerAbort(InteractiveError(code)) from None

    def _accept_debate_progress(
        self,
        event: DebateProgressEvent,
        *,
        expected_session_id: str,
        expected_input_kind: InputKind,
        expected_context_mode: DebateContextMode,
        events: list[DebateProgressEvent],
    ) -> None:
        """Validate, render, and retain one trusted runtime progress event.

        Only exact built-in events with matching run identity and legal phase
        succession are accepted. ``INTERRUPTED`` may be first when cancellation
        follows reservation but precedes ``STARTED``; only an accepted
        ``STARTED`` updates the process-local per-session ``last`` mapping.
        Rendering uses typed fields and fixed copy only; invalid events or
        terminal writer failures propagate to the runtime sink boundary, which
        owns interruption and observer-error semantics.
        """

        if type(event) is not DebateProgressEvent:
            raise TypeError("progress sink requires a DebateProgressEvent")
        try:
            canonical = DebateProgressEvent(
                session_id=event.session_id,
                debate_id=event.debate_id,
                input_kind=event.input_kind,
                phase=event.phase,
                revision_id=event.revision_id,
                round_number=event.round_number,
                context_mode=event.context_mode,
                context_event_count=event.context_event_count,
                context_omitted_turns=event.context_omitted_turns,
                conflicting_agents=event.conflicting_agents,
                manuscript_path=event.manuscript_path,
                core_updates=event.core_updates,
                failure_kind=event.failure_kind,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise TypeError("progress event is invalid") from exc
        if (
            canonical.session_id != expected_session_id
            or canonical.input_kind is not expected_input_kind
        ):
            raise ValueError("progress event identity does not match the run")
        previous = events[-1] if events else None
        if previous is None:
            if canonical.phase not in {
                DebateProgressPhase.STARTED,
                DebateProgressPhase.INTERRUPTED,
            }:
                raise ValueError(
                    "the first progress event must be started or interrupted"
                )
            if (
                canonical.phase is DebateProgressPhase.STARTED
                and canonical.context_mode is not expected_context_mode
            ):
                raise ValueError("started progress context mode does not match")
        else:
            if canonical.debate_id != previous.debate_id:
                raise ValueError("progress debate identity changed")
            if not self._debate_progress_transition_is_valid(
                previous.phase,
                canonical.phase,
            ):
                raise ValueError("progress phase order is invalid")
        self._render_debate_progress(canonical)
        events.append(canonical)
        if canonical.phase is DebateProgressPhase.STARTED:
            self._last_started_debate_by_session[
                canonical.session_id
            ] = canonical.debate_id

    @staticmethod
    def _debate_progress_transition_is_valid(
        previous: DebateProgressPhase,
        current: DebateProgressPhase,
    ) -> bool:
        """Return whether two adjacent trusted phases can occur in one workflow."""

        terminal = {
            DebateProgressPhase.CONSENSUS,
            DebateProgressPhase.DEADLOCK,
            DebateProgressPhase.FAILED,
            DebateProgressPhase.INTERRUPTED,
        }
        if previous in terminal:
            return False
        if current is DebateProgressPhase.INTERRUPTED:
            return True
        allowed = {
            DebateProgressPhase.STARTED: {
                DebateProgressPhase.DRAFTED,
                DebateProgressPhase.FAILED,
            },
            DebateProgressPhase.DRAFTED: {
                DebateProgressPhase.AUDIT_STARTED,
            },
            DebateProgressPhase.AUDIT_STARTED: {
                DebateProgressPhase.AUDIT_COMPLETED,
                DebateProgressPhase.FAILED,
            },
            DebateProgressPhase.AUDIT_COMPLETED: {
                DebateProgressPhase.SYNTHESIZED,
                DebateProgressPhase.ARTIFACTS_COMMITTED,
                DebateProgressPhase.DEADLOCK,
                DebateProgressPhase.FAILED,
            },
            DebateProgressPhase.SYNTHESIZED: {
                DebateProgressPhase.AUDIT_STARTED,
            },
            DebateProgressPhase.ARTIFACTS_COMMITTED: {
                DebateProgressPhase.CONSENSUS,
            },
        }
        return current in allowed.get(previous, set())

    def _render_debate_progress(self, event: DebateProgressEvent) -> None:
        """Render one validated event using only typed fields and fixed labels."""

        phase = event.phase
        if phase is DebateProgressPhase.STARTED:
            self._terminal.write_run(
                event.debate_id,
                f"Started {event.input_kind.value} consensus.",
            )
            if event.context_mode is DebateContextMode.INCLUDED:
                self._terminal.write_status(
                    TerminalMessageKind.CONTEXT,
                    (
                        f"Included {event.context_event_count} focused-conversation "
                        f"events; {event.context_omitted_turns} earlier turns omitted."
                    ),
                )
            elif event.context_mode is DebateContextMode.FRESH:
                self._terminal.write_status(
                    TerminalMessageKind.CONTEXT,
                    "Fresh run; no conversation events were included.",
                )
            return
        if phase is DebateProgressPhase.DRAFTED:
            text = "Drafted revision r0."
        elif phase is DebateProgressPhase.AUDIT_STARTED:
            text = (
                f"Audit started for {event.revision_id} at round "
                f"{event.round_number}."
            )
        elif phase is DebateProgressPhase.AUDIT_COMPLETED:
            conflicts = (
                ", ".join(event.conflicting_agents)
                if event.conflicting_agents
                else "none"
            )
            text = (
                f"Audit completed for {event.revision_id} at round "
                f"{event.round_number}; conflicts: {conflicts}."
            )
        elif phase is DebateProgressPhase.SYNTHESIZED:
            text = (
                f"Synthesized {event.revision_id} at round "
                f"{event.round_number}."
            )
        elif phase is DebateProgressPhase.ARTIFACTS_COMMITTED:
            updates = ", ".join(event.core_updates) if event.core_updates else "none"
            text = (
                f"Artifacts committed for {event.revision_id}: "
                f"{event.manuscript_path}; core updates: {updates}."
            )
        elif phase is DebateProgressPhase.CONSENSUS:
            text = (
                f"Consensus reached on {event.revision_id} after "
                f"{event.round_number} revision rounds."
            )
        elif phase is DebateProgressPhase.DEADLOCK:
            text = (
                f"Deadlock on {event.revision_id} after "
                f"{event.round_number} revision rounds."
            )
        elif phase is DebateProgressPhase.FAILED:
            assert event.failure_kind is not None
            text = f"Run failed ({event.failure_kind.value})."
        else:
            assert phase is DebateProgressPhase.INTERRUPTED
            text = "Run interrupted after durable state transition."
        self._terminal.write_run(event.debate_id, text)

    def _validated_consensus_result(
        self,
        result: DebateResult,
        *,
        expected_session_id: str,
        expected_input_kind: InputKind,
        progress_events: list[DebateProgressEvent],
    ) -> tuple[DebateProgressEvent, DebateProgressEvent | None]:
        """Validate terminal result identity and typed progress correlation.

        The operation reads no persistence and exposes no result prose. Any type,
        identity, status, revision, round, or committed-artifact mismatch aborts
        through the controller's fatal cleanup boundary.
        """

        invalid = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        if type(result) is not DebateResult or not progress_events:
            raise _ControllerAbort(invalid)
        started = progress_events[0]
        terminal = progress_events[-1]
        if (
            started.phase is not DebateProgressPhase.STARTED
            or result.session_id != expected_session_id
            or result.session_id != started.session_id
            or result.debate_id != started.debate_id
            or result.input_kind is not expected_input_kind
            or result.input_kind is not started.input_kind
            or not isinstance(result.status, DebateStatus)
            or result.revision_id != terminal.revision_id
            or isinstance(result.rounds_completed, bool)
            or not isinstance(result.rounds_completed, int)
            or result.rounds_completed != terminal.round_number
            or type(result.core_updates) is not tuple
        ):
            raise _ControllerAbort(invalid)
        expected_terminal = {
            DebateStatus.CONSENSUS: DebateProgressPhase.CONSENSUS,
            DebateStatus.DEADLOCK: DebateProgressPhase.DEADLOCK,
            DebateStatus.FAILED: DebateProgressPhase.FAILED,
        }[result.status]
        if terminal.phase is not expected_terminal:
            raise _ControllerAbort(invalid)

        artifact_event: DebateProgressEvent | None = None
        if result.status is DebateStatus.CONSENSUS:
            if len(progress_events) < 2:
                raise _ControllerAbort(invalid)
            artifact_event = progress_events[-2]
            if (
                artifact_event.phase
                is not DebateProgressPhase.ARTIFACTS_COMMITTED
                or result.manuscript_path != artifact_event.manuscript_path
                or result.core_updates != artifact_event.core_updates
                or result.manuscript_path is None
            ):
                raise _ControllerAbort(invalid)
        elif (
            result.manuscript_path is not None
            or result.core_updates
            or result.final_candidate is not None
            or any(
                event.phase is DebateProgressPhase.ARTIFACTS_COMMITTED
                for event in progress_events
            )
        ):
            raise _ControllerAbort(invalid)
        return terminal, artifact_event

    def _present_consensus_result(
        self,
        result: DebateResult,
        terminal_event: DebateProgressEvent,
        artifact_event: DebateProgressEvent | None,
    ) -> None:
        """Render only trusted terminal metadata from a validated result.

        Workspace failures first prove the same ready access held here. No
        terminal outcome exposes provider or result prose.
        """

        if result.status is DebateStatus.CONSENSUS:
            assert artifact_event is not None
            updates = ", ".join(artifact_event.core_updates) or "none"
            self._terminal.write_run(
                result.debate_id,
                (
                    f"Consensus | revision: {terminal_event.revision_id} | "
                    f"revision rounds: {terminal_event.round_number} | "
                    f"manuscript: {artifact_event.manuscript_path} | "
                    f"core updates: {updates}."
                ),
            )
            return
        if result.status is DebateStatus.DEADLOCK:
            code = InteractiveErrorCode.CONSENSUS_DEADLOCK
        else:
            assert terminal_event.failure_kind is not None
            code = self._interactive_code_for_debate_failure(
                terminal_event.failure_kind
            )
        self._present_consensus_error_after_required_attachment_check(code)

    def _present_consensus_execution_failure(
        self,
        failure: Exception,
        *,
        draft_payload_was_empty: bool,
        progress_events: list[DebateProgressEvent],
    ) -> None:
        """Select a fixed pre-result error from trusted controller evidence.

        The exact empty-draft flag and accepted progress history decide whether
        a recoverable code is safe; ambiguous failures abort. Workspace errors
        require attachment proof before output, and exception prose is never
        rendered.
        """

        if isinstance(failure, ConfigurationError):
            code = InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED
        elif isinstance(failure, SessionNotFoundError):
            code = InteractiveErrorCode.SESSION_NOT_FOUND
        elif isinstance(failure, SessionPausedError):
            code = InteractiveErrorCode.SESSION_PAUSED
        elif isinstance(failure, SessionAccessBlockedError):
            code = InteractiveErrorCode.SESSION_ACCESS_BLOCKED
        elif isinstance(failure, SessionOwnershipUnknownError):
            code = InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN
        elif isinstance(failure, BackendProtocolError):
            code = InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR
        elif isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.TRANSCRIPT_INVALID
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(failure, WorkspaceFormatError):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        elif isinstance(failure, OSError):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        elif (
            draft_payload_was_empty
            and not progress_events
            and isinstance(failure, (TypeError, ValueError))
        ):
            code = InteractiveErrorCode.DRAFT_INPUT_INVALID
        elif not progress_events and isinstance(failure, (TypeError, ValueError)):
            code = InteractiveErrorCode.TRANSCRIPT_INVALID
        else:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None
        self._present_consensus_error_after_required_attachment_check(code)

    def _present_consensus_error_after_required_attachment_check(
        self,
        code: InteractiveErrorCode,
    ) -> None:
        """Render a fixed consensus error after any required access proof.

        Workspace errors return to the prompt only when public state proves the
        the same ready access held here. Failed proof uses the existing fatal
        cleanup boundary before any terminal error is written; other codes
        render immediately without changing consensus mappings.
        """

        if code in {
            InteractiveErrorCode.SESSION_NOT_FOUND,
            InteractiveErrorCode.SESSION_PAUSED,
            InteractiveErrorCode.SESSION_ACCESS_BLOCKED,
            InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN,
            InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED,
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.WORKSPACE_UNSAFE,
        }:
            self._confirm_attachment_after_command_failure()
        self._terminal.write_error(InteractiveError(code))

    @staticmethod
    def _interactive_code_for_debate_failure(
        failure_kind: DebateFailureKind,
    ) -> InteractiveErrorCode:
        """Map one trusted runtime failure category to a fixed terminal code."""

        return {
            DebateFailureKind.PREFLIGHT: InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
            DebateFailureKind.PROTOCOL: InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR,
            DebateFailureKind.CONTEXT: InteractiveErrorCode.TRANSCRIPT_INVALID,
            DebateFailureKind.WORKSPACE: InteractiveErrorCode.WORKSPACE_INVALID,
            DebateFailureKind.ARTIFACT_CONFLICT: InteractiveErrorCode.ARTIFACT_CONFLICT,
            DebateFailureKind.ARTIFACT_FAILURE: InteractiveErrorCode.CONSENSUS_FAILED,
            DebateFailureKind.INTERNAL: InteractiveErrorCode.CONSENSUS_FAILED,
        }[failure_kind]

    def _present_advisory_turn_failure(self, failure: Exception) -> None:
        """Render one typed turn failure or abort when recovery is uncertain.

        Exception classes, never exception prose, select the result. Missing
        conversations clear only stale effective in-memory focus, leaving the
        persisted default and catalog untouched. Session/workspace failures
        require proof that ready access remains held here. Unknown failures
        abort, while runtime remains the sole owner of durable turn outcomes.
        """

        error = self._classify_advisory_turn_failure(failure)
        if error.code is InteractiveErrorCode.INTERNAL_ERROR:
            raise _ControllerAbort(error)
        if error.code is InteractiveErrorCode.CONVERSATION_NOT_FOUND:
            self._focused_conversation = None
        if error.code in {
            InteractiveErrorCode.SESSION_INVALID,
            InteractiveErrorCode.SESSION_EXISTS,
            InteractiveErrorCode.SESSION_NOT_FOUND,
            InteractiveErrorCode.SESSION_PAUSED,
            InteractiveErrorCode.SESSION_ACCESS_BLOCKED,
            InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN,
            InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED,
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.WORKSPACE_UNSAFE,
        }:
            self._confirm_attachment_after_command_failure()
        self._terminal.write_error(error)

    @staticmethod
    def _classify_advisory_turn_failure(
        failure: Exception,
    ) -> InteractiveError:
        """Map exact turn exception types to safe codes without mutation.

        Established session, conversation, and workspace categories take
        precedence over provider categories. The returned error carries no raw
        diagnostic; unknown exceptions become ``internal_error`` for fatal
        cleanup at the caller.
        """

        if isinstance(failure, SessionIdError):
            code = InteractiveErrorCode.SESSION_INVALID
        elif isinstance(failure, ConversationRetrievalSemanticError):
            code = InteractiveErrorCode.RETRIEVAL_INVALID
        elif isinstance(failure, ConversationRetrievalBudgetError):
            code = InteractiveErrorCode.RETRIEVAL_BUDGET_EXHAUSTED
        elif isinstance(failure, ConversationRetrievalObserverError):
            code = InteractiveErrorCode.INTERNAL_ERROR
        elif isinstance(failure, SessionExistsError):
            code = InteractiveErrorCode.SESSION_EXISTS
        elif isinstance(failure, SessionNotFoundError):
            code = InteractiveErrorCode.SESSION_NOT_FOUND
        elif isinstance(failure, SessionPausedError):
            code = InteractiveErrorCode.SESSION_PAUSED
        elif isinstance(failure, SessionAccessBlockedError):
            code = InteractiveErrorCode.SESSION_ACCESS_BLOCKED
        elif isinstance(failure, SessionOwnershipUnknownError):
            code = InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN
        elif isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(failure, (WorkspaceFormatError, OSError)):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        elif isinstance(failure, ConfigurationError):
            code = InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED
        elif isinstance(failure, BackendProtocolError):
            code = InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        return InteractiveError(code)

    def _write_unavailable_lobby_command_error(self) -> None:
        """Render the stable later-feature error without mutating controller state."""

        self._terminal.write_error(
            InteractiveError(
                InteractiveErrorCode.COMMAND_INVALID,
                summary=_UNAVAILABLE_SUMMARY,
                hint=_LOBBY_COMMAND_HINT,
            )
        )

    def _search_attached_conversations(
        self,
        action: InteractiveAction,
    ) -> None:
        """Search the attached session and render ordered active-only hits.

        The parsed action supplies one query. Catalog and service validation
        precede primitive rendering; expected failures retain attachment and
        routing state, while Ctrl-C acknowledges cancellation without cleanup.
        """

        assert action.kind is ActionKind.SEARCH
        assert action.text is not None
        session_id = self._attached_session_id
        assert session_id is not None

        try:
            try:
                query = ConversationSearchQuery(action.text)
            except (TypeError, ValueError):
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.COMMAND_INVALID)
                )
                return

            try:
                entries = self._catalog_entries(session_id)
            except Exception as exc:
                self._present_memory_catalog_or_reference_failure(exc)
                return

            memory_service = self._conversation_memory_service
            if memory_service is None:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            try:
                result = memory_service.search(query)
                handles = self._validated_search_result_handles(
                    result,
                    query=query,
                    session_id=session_id,
                    entries=entries,
                )
            except Exception as exc:
                self._present_search_service_failure(exc)
                return

            for handle, hit in zip(handles, result.hits, strict=True):
                citation = hit.citation
                self._terminal.write_conversation_search_hit(
                    handle,
                    hit.subject,
                    hit.match_kind.value,
                    hit.snippet,
                    archived=(hit.status is ConversationStatus.ARCHIVED),
                    citation_turn_id=citation.turn_id,
                    citation_sequence=citation.sequence,
                    citation_kind=(
                        None
                        if citation.event_kind is None
                        else citation.event_kind.value
                    ),
                )
            self._terminal.write_status(
                TerminalMessageKind.RESULT,
                (
                    f"Search returned {len(result.hits)} of "
                    f"{result.matched_conversations} matching conversations."
                ),
            )
            if result.truncated:
                self._terminal.write_status(
                    TerminalMessageKind.HINT,
                    (
                        "Search was truncated by fixed scan or result "
                        "limits; refine the query."
                    ),
                )
        except KeyboardInterrupt:
            self._terminal.write_status(
                TerminalMessageKind.CANCELLED,
                "Search cancelled; attached state is unchanged.",
            )

    def _read_attached_conversation_transcript(
        self,
        action: InteractiveAction,
    ) -> None:
        """Resolve and render one fresh bounded transcript page.

        The reference may name an active or archived conversation. The method
        retains no cursor and mutates no attachment, focus, routing, or
        persistence state; Ctrl-C returns to the same prompt after a status.
        """

        assert action.kind is ActionKind.TRANSCRIPT
        assert action.argument is not None
        session_id = self._attached_session_id
        assert session_id is not None

        try:
            try:
                metadata, entries = self._resolve_conversation_reference(
                    session_id,
                    action.argument,
                )
            except Exception as exc:
                self._present_memory_catalog_or_reference_failure(exc)
                return

            memory_service = self._conversation_memory_service
            if memory_service is None:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            start_sequence = action.start_sequence or 1
            page_limit = (
                action.count
                or CONVERSATION_MEMORY_POLICY.default_page_events
            )
            try:
                page = memory_service.read_transcript(
                    metadata.conversation_id,
                    start_sequence=start_sequence,
                    max_events=page_limit,
                )
                page = self._validated_transcript_page(
                    page,
                    session_id=session_id,
                    conversation_id=metadata.conversation_id,
                    start_sequence=start_sequence,
                    page_limit=page_limit,
                )
            except Exception as exc:
                self._present_transcript_service_failure(exc)
                return

            handle = self._visible_conversation_handle(metadata, entries)
            for event in page.events:
                self._terminal.write_conversation_transcript_event(
                    handle,
                    sequence=event.sequence,
                    turn_id=event.turn_id,
                    kind=event.kind.value,
                    sender=event.sender,
                    recipient=event.recipient,
                    content=event.content,
                )
            self._terminal.write_status(
                TerminalMessageKind.RESULT,
                f"Transcript page returned {len(page.events)} events.",
            )
            if page.next_cursor is not None:
                self._terminal.write_status(
                    TerminalMessageKind.HINT,
                    (
                        f"Continue with /transcript {handle} "
                        f"{page.next_cursor.max_events} --from "
                        f"{page.next_cursor.next_sequence}."
                    ),
                )
        except KeyboardInterrupt:
            self._terminal.write_status(
                TerminalMessageKind.CANCELLED,
                "Transcript read cancelled; attached state is unchanged.",
            )

    @staticmethod
    def _validated_search_result_handles(
        result: object,
        *,
        query: ConversationSearchQuery,
        session_id: str,
        entries: tuple[ConversationMetadata, ...],
    ) -> tuple[str, ...]:
        """Validate one search result against the complete catalog snapshot.

        Exact result/query identity and active metadata must agree before
        copyable handles are returned in service order. The check is pure and
        raises ``TypeError`` or ``ValueError`` for collaborator violations.
        """

        if type(result) is not ConversationSearchResult:
            raise TypeError("conversation search returned an invalid result")
        if result.query != query:
            raise ValueError("conversation search changed its query")
        metadata_by_id = {
            entry.conversation_id: entry
            for entry in entries
        }
        handles: list[str] = []
        for hit in result.hits:
            if hit.citation.session_id != session_id:
                raise ValueError("conversation search crossed the attached session")
            metadata = metadata_by_id.get(hit.citation.conversation_id)
            if metadata is None:
                raise ValueError("conversation search returned an unknown identity")
            if (
                hit.status is not ConversationStatus.ACTIVE
                or metadata.status is not ConversationStatus.ACTIVE
                or hit.status is not metadata.status
                or hit.subject != metadata.subject
                or hit.subject_revision != metadata.subject_revision
                or hit.updated_at != metadata.updated_at
            ):
                raise ValueError("conversation search metadata is inconsistent")
            handles.append(
                InteractiveController._visible_conversation_handle(
                    metadata,
                    entries,
                )
            )
        return tuple(handles)

    @staticmethod
    def _validated_transcript_page(
        page: object,
        *,
        session_id: str,
        conversation_id: str,
        start_sequence: int,
        page_limit: int,
    ) -> ConversationTranscriptPage:
        """Return a page matching one fresh no-filter transcript request.

        The pure validation requires exact scope, full-snapshot range, count,
        contiguous event, and cursor boundaries. Invalid collaborator output
        raises ``TypeError`` or ``ValueError`` without lifecycle mutation.
        """

        if type(page) is not ConversationTranscriptPage:
            raise TypeError("conversation transcript returned an invalid page")
        if (
            page.session_id != session_id
            or page.conversation_id != conversation_id
        ):
            raise ValueError("conversation transcript crossed its attached scope")
        if page.requested_start_sequence != start_sequence:
            raise ValueError("conversation transcript changed its requested start")
        if page.requested_end_sequence != page.snapshot_last_sequence:
            raise ValueError("conversation transcript shortened its requested range")
        if len(page.events) > page_limit:
            raise ValueError("conversation transcript exceeded its requested count")
        if (
            page.next_cursor is not None
            and page.next_cursor.max_events != page_limit
        ):
            raise ValueError("conversation transcript cursor changed its count")

        if not page.events:
            if (
                page.requested_start_sequence
                != page.snapshot_last_sequence + 1
            ):
                raise ValueError(
                    "conversation transcript returned an invalid empty page"
                )
            return page

        for expected_sequence, event in enumerate(
            page.events,
            start=start_sequence,
        ):
            if event.sequence != expected_sequence:
                raise ValueError(
                    "conversation transcript returned non-contiguous events"
                )

        last_returned_sequence = page.events[-1].sequence
        if page.next_cursor is not None:
            if page.next_cursor.next_sequence != last_returned_sequence + 1:
                raise ValueError(
                    "conversation transcript cursor skipped returned events"
                )
        elif last_returned_sequence != page.requested_end_sequence:
            raise ValueError("conversation transcript returned an incomplete page")
        return page

    def _present_memory_catalog_or_reference_failure(
        self,
        failure: Exception,
    ) -> None:
        """Render safe reference/catalog failures under retained access.

        Resolution categories preserve their exact public code. Unsafe paths
        and invalid complete-catalog behavior are access-confirmed before
        rendering; unknown failures abort to final controller cleanup.
        """

        if isinstance(failure, _ConversationResolutionFailure):
            code = failure.code
        elif isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(
            failure,
            (
                _ConversationCatalogProtocolFailure,
                WorkspaceFormatError,
                OSError,
                SessionIdError,
                TypeError,
                ValueError,
            ),
        ):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        self._present_memory_error(InteractiveError(code))

    def _present_search_service_failure(self, failure: Exception) -> None:
        """Classify a valid-query search failure without exposing its prose.

        Missing identities retain their conversation code; unsafe paths and
        invalid storage/collaborator results are access-confirmed workspace
        failures. Unknown failures abort through the controller lifecycle.
        """

        if isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(
            failure,
            (
                _ConversationCatalogProtocolFailure,
                WorkspaceFormatError,
                OSError,
                SessionIdError,
                TypeError,
                ValueError,
            ),
        ):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        self._present_memory_error(InteractiveError(code))

    def _present_transcript_service_failure(self, failure: Exception) -> None:
        """Classify one post-catalog transcript failure without raw prose.

        Missing identities and unsafe paths keep their public categories.
        Transcript format/value failures are transcript-invalid, ordinary I/O
        or unsafe identifiers are workspace-invalid, and unknowns are fatal.
        """

        if isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(failure, WorkspaceFormatError):
            code = InteractiveErrorCode.TRANSCRIPT_INVALID
        elif isinstance(failure, (TypeError, ValueError)):
            code = InteractiveErrorCode.TRANSCRIPT_INVALID
        elif isinstance(
            failure,
            (_ConversationCatalogProtocolFailure, OSError, SessionIdError),
        ):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        self._present_memory_error(InteractiveError(code))

    def _present_memory_error(self, error: InteractiveError) -> None:
        """Render one trusted read error after any required access proof.

        Workspace-invalid and unsafe outcomes confirm the same ready access
        attachment before output. Internal errors abort; other codes render
        without changing memory scope, focus, routing, or persistence.
        """

        if error.code is InteractiveErrorCode.INTERNAL_ERROR:
            raise _ControllerAbort(error)
        if error.code in {
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.WORKSPACE_UNSAFE,
        }:
            self._confirm_attachment_after_command_failure()
        self._terminal.write_error(error)

    def _write_attached_conversation_scope(self) -> None:
        """Render the effective current focus immediately before main input.

        The current metadata is revalidated against the complete catalog so a
        unique display handle can be derived across active and archived IDs.
        Missing or archived external state drops only in-memory focus and
        reports its typed category; the durable default is not rewritten here.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        focused = self._focused_conversation
        if focused is None:
            self._terminal.write_conversation_scope(
                session_id=session_id,
                conversation_handle=None,
                conversation_subject=None,
                agent_id=self._target_agent_id,
            )
            return

        try:
            entries = self._catalog_entries(session_id)
        except Exception as exc:
            self._present_conversation_failure(exc)
            handle = focused.conversation_id
        else:
            current = next(
                (
                    entry
                    for entry in entries
                    if entry.conversation_id == focused.conversation_id
                ),
                None,
            )
            if current is None:
                self._focused_conversation = None
                self._terminal.write_error(
                    InteractiveError(
                        InteractiveErrorCode.CONVERSATION_NOT_FOUND
                    )
                )
                self._terminal.write_conversation_scope(
                    session_id=session_id,
                    conversation_handle=None,
                    conversation_subject=None,
                    agent_id=self._target_agent_id,
                )
                return
            if current.status is ConversationStatus.ARCHIVED:
                self._focused_conversation = None
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
                )
                self._terminal.write_conversation_scope(
                    session_id=session_id,
                    conversation_handle=None,
                    conversation_subject=None,
                    agent_id=self._target_agent_id,
                )
                return
            self._focused_conversation = current
            focused = current
            handle = self._visible_conversation_handle(current, entries)

        self._terminal.write_conversation_scope(
            session_id=session_id,
            conversation_handle=handle,
            conversation_subject=focused.subject,
            agent_id=self._target_agent_id,
        )

    def _create_subject_conversation(self, subject: str | None) -> bool:
        """Create and focus one active conversation, returning EOF exit intent.

        A missing subject causes exactly one call-local terminal read. Interrupt
        cancels without mutation and EOF requests normal process cleanup. After
        catalog creation, the default is saved before focus changes; a save
        failure leaves the created entry and prior process state intact.
        """

        if subject is None:
            subject_action = self._terminal.request_conversation_subject()
            if subject_action.kind is TerminalActionKind.INTERRUPT:
                self._write_conversation_cancelled_status(
                    "Conversation creation cancelled."
                )
                return False
            if subject_action.kind is TerminalActionKind.EOF:
                return True
            if subject_action.kind is not TerminalActionKind.LINE:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            assert subject_action.text is not None
            subject = subject_action.text

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            self._normalized_conversation_input(subject)
            created = self._conversation_catalog.create(session_id, subject)
            created = self._validated_catalog_metadata(
                created,
                session_id,
            )
            if created.status is not ConversationStatus.ACTIVE:
                raise _ConversationCatalogProtocolFailure(
                    "new conversation metadata is not active"
                )
        except Exception as exc:
            self._present_conversation_failure(exc, creating=True)
            return False

        try:
            save_default_conversation(
                self._workspace,
                session_id,
                created.conversation_id,
            )
        except Exception as exc:
            self._confirm_attachment_after_command_failure()
            error = self._classify_conversation_failure(exc)
            self._terminal.write_error(
                InteractiveError(
                    error.code,
                    summary=(
                        f"Conversation {created.conversation_id} was created "
                        "but did not become the default."
                    ),
                    hint=(
                        "Repair workspace storage, then use the full ID to "
                        "make it the current focus."
                    ),
                )
            )
            return False

        self._focused_conversation = created
        handle = self._visible_handle_after_mutation(created)
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            f"Created and focused {handle} | Subject: {created.subject}",
        )
        return False

    def _list_subject_conversations(
        self,
        *,
        include_archived: bool,
        query: str | None,
    ) -> None:
        """Render a filtered catalog in storage order without durable mutation.

        Handles are derived from the complete active-and-archived ID set even
        when only active rows are requested. The optional query uses the
        established normalized-subject substring rule; external loss or
        archival of the in-memory focus clears memory but not its default file.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            normalized_query = (
                None
                if query is None
                else self._normalized_conversation_input(query)
            )
            entries = self._catalog_entries(session_id)
        except Exception as exc:
            self._present_conversation_failure(exc)
            return

        self._reconcile_conversation_focus(entries)
        visible_entries = tuple(
            entry
            for entry in entries
            if (
                include_archived
                or entry.status is ConversationStatus.ACTIVE
            )
            and (
                normalized_query is None
                or normalized_query
                in normalize_conversation_subject(entry.subject)
            )
        )
        if not visible_entries:
            self._terminal.write_status(
                TerminalMessageKind.CONVERSATION,
                "No conversations found.",
            )
            return

        focused_id = (
            None
            if self._focused_conversation is None
            else self._focused_conversation.conversation_id
        )
        for entry in visible_entries:
            self._terminal.write_conversation_list_entry(
                self._visible_conversation_handle(entry, entries),
                entry.subject,
                archived=(entry.status is ConversationStatus.ARCHIVED),
                focused=(entry.conversation_id == focused_id),
            )

    def _focus_subject_conversation(self, reference: str) -> None:
        """Resolve, save, and focus one active conversation without rerouting.

        Exact IDs win over case-sensitive prefixes of at least twelve
        characters. Resolution includes archived records, which receive a
        typed rejection. Default persistence completes before in-memory focus
        and presentation change.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            focused, entries = self._resolve_conversation_reference(
                session_id,
                reference,
            )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        if focused.status is ConversationStatus.ARCHIVED:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return
        try:
            save_default_conversation(
                self._workspace,
                session_id,
                focused.conversation_id,
            )
        except Exception as exc:
            self._confirm_attachment_after_command_failure()
            self._terminal.write_error(
                self._classify_conversation_failure(exc)
            )
            return

        self._focused_conversation = focused
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            (
                "Current focus: "
                f"{self._visible_conversation_handle(focused, entries)} | "
                f"Subject: {focused.subject}"
            ),
        )

    def _rename_focused_conversation(self, subject: str) -> None:
        """Atomically rename the focused active conversation, then update memory.

        The exact display subject is validated without normalization replacement.
        Missing or externally archived focus enters the conversation lobby
        without rewriting the stale durable default; catalog failure leaves
        the previous process metadata intact until later revalidation.
        """

        current = self._require_active_conversation_focus()
        if current is None:
            return
        session_id = self._attached_session_id
        assert session_id is not None
        try:
            self._normalized_conversation_input(subject)
            renamed = self._conversation_catalog.rename(
                session_id,
                current.conversation_id,
                subject,
            )
            renamed = self._validated_catalog_metadata(
                renamed,
                session_id,
                expected_conversation_id=current.conversation_id,
            )
        except ConversationNotFoundError as exc:
            self._focused_conversation = None
            self._present_conversation_failure(exc)
            return
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        if renamed.status is ConversationStatus.ARCHIVED:
            self._focused_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return

        self._focused_conversation = renamed
        handle = self._visible_handle_after_mutation(renamed)
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            f"Renamed {handle} | Subject: {renamed.subject}",
        )

    def _archive_subject_conversation(self, reference: str | None) -> bool:
        """Archive one resolved conversation and return confirmation EOF intent.

        Bare archive requires the focused active record. Explicit lookup can
        target any record; an already archived unfocused record is an acknowledged
        no-op. Archiving the current focus delegates to the compensated
        successor transition, while other targets require one catalog mutation.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        if reference is None:
            target = self._require_active_conversation_focus()
            if target is None:
                return False
            try:
                entries = self._catalog_entries(session_id)
            except Exception as exc:
                self._present_conversation_failure(exc)
                return False
        else:
            try:
                target, entries = self._resolve_conversation_reference(
                    session_id,
                    reference,
                )
            except Exception as exc:
                self._present_conversation_failure(exc)
                return False

        focused_id = (
            None
            if self._focused_conversation is None
            else self._focused_conversation.conversation_id
        )
        is_focused = target.conversation_id == focused_id
        if target.status is ConversationStatus.ARCHIVED:
            if is_focused:
                self._focused_conversation = None
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
                )
            else:
                self._terminal.write_status(
                    TerminalMessageKind.CONVERSATION,
                    (
                        "Already archived "
                        f"{self._visible_conversation_handle(target, entries)} | "
                        f"Subject: {target.subject}"
                    ),
                )
            return False
        if is_focused:
            return self._archive_focused_conversation(target, entries)
        self._archive_unfocused_conversation(target, entries)
        return False

    def _archive_unfocused_conversation(
        self,
        target: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> None:
        """Archive one active unfocused conversation with one catalog mutation.

        The durable default and in-memory focus are never changed. A validated
        archived result is rendered using the pre-mutation all-record handle;
        failures follow command-time access confirmation rules.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            archived = self._conversation_catalog.archive(
                session_id,
                target.conversation_id,
            )
            archived = self._validated_catalog_metadata(
                archived,
                session_id,
                expected_conversation_id=target.conversation_id,
            )
            if archived.status is not ConversationStatus.ARCHIVED:
                raise _ConversationCatalogProtocolFailure(
                    "archived conversation metadata is not archived"
                )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            (
                f"Archived {self._visible_conversation_handle(target, entries)} | "
                f"Subject: {archived.subject}"
            ),
        )

    def _archive_focused_conversation(
        self,
        target: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> bool:
        """Confirm, archive, and make the first active successor the default.

        No, blank, or interrupt cancels without mutation; EOF requests normal
        access release. After archive, successor/null persistence precedes memory. A
        failed default write best-effort unarchives and proves both original
        active metadata and original durable default before recovery is
        claimed; any uncertainty aborts through cleanup-failed.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        successor = next(
            (
                entry
                for entry in entries
                if entry.conversation_id != target.conversation_id
                and entry.status is ConversationStatus.ACTIVE
            ),
            None,
        )
        target_handle = self._visible_conversation_handle(target, entries)
        decision = self._terminal.request_confirmation(
            (
                f'Archive current conversation "{target.subject}" '
                f"({target_handle})?"
            ),
            default=False,
        )
        if decision is ConfirmationResult.EOF:
            return True
        if decision is not ConfirmationResult.YES:
            self._write_conversation_cancelled_status(
                "Conversation archive cancelled."
            )
            return False

        try:
            archived = self._conversation_catalog.archive(
                session_id,
                target.conversation_id,
            )
            archived = self._validated_catalog_metadata(
                archived,
                session_id,
                expected_conversation_id=target.conversation_id,
            )
            if archived.status is not ConversationStatus.ARCHIVED:
                raise _ConversationCatalogProtocolFailure(
                    "archived conversation metadata is not archived"
                )
        except ConversationNotFoundError as exc:
            self._focused_conversation = None
            self._present_conversation_failure(exc)
            return False
        except Exception as exc:
            self._present_conversation_failure(exc)
            return False

        try:
            save_default_conversation(
                self._workspace,
                session_id,
                (
                    None
                    if successor is None
                    else successor.conversation_id
                ),
            )
        except Exception as exc:
            self._compensate_failed_focused_archive(target, exc)
            return False

        self._focused_conversation = successor
        status = f"Archived {target_handle} | Subject: {archived.subject}"
        if successor is None:
            status += "; current focus is none."
        else:
            status += (
                "; current focus: "
                f"{self._visible_conversation_handle(successor, entries)} | "
                f"Subject: {successor.subject}"
            )
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            status,
        )
        return False

    def _compensate_failed_focused_archive(
        self,
        original: ConversationMetadata,
        default_failure: Exception,
    ) -> None:
        """Restore and prove the original focus after a default-save failure.

        Compensation uses only public catalog and default APIs. It succeeds
        only when unarchive/get both show the original active metadata, the
        durable default still names it, and the current session remains ready
        with access held here. Otherwise controller bookkeeping
        is abandoned for best-effort backend close and ``cleanup_failed``.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            restored = self._conversation_catalog.unarchive(
                session_id,
                original.conversation_id,
            )
            restored = self._validated_catalog_metadata(
                restored,
                session_id,
                expected_conversation_id=original.conversation_id,
            )
            confirmed = self._conversation_catalog.get(
                session_id,
                original.conversation_id,
            )
            confirmed = self._validated_catalog_metadata(
                confirmed,
                session_id,
                expected_conversation_id=original.conversation_id,
            )
            durable_default = load_default_conversation(
                self._workspace,
                session_id,
            )
            if (
                restored.status is not ConversationStatus.ACTIVE
                or confirmed.status is not ConversationStatus.ACTIVE
            ):
                raise _ConversationCatalogProtocolFailure(
                    "unarchived conversation metadata is not active"
                )
            if (
                durable_default != original.conversation_id
                or not self._attachment_is_safely_retained(session_id)
            ):
                raise WorkspaceFormatError(
                    "focused archive compensation could not be confirmed"
                )
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

        self._focused_conversation = confirmed
        self._terminal.write_error(
            self._classify_conversation_failure(default_failure)
        )
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            "Archive did not complete; the original conversation remains focused.",
        )

    def _require_active_conversation_focus(
        self,
    ) -> ConversationMetadata | None:
        """Return the revalidated active focus or render its typed absence.

        Missing and archived records clear only effective in-memory focus,
        leaving the durable stale ID for safe restart behavior. Other failures
        preserve memory and follow command-time access confirmation rules.
        """

        focused = self._focused_conversation
        if focused is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
            )
            return None
        session_id = self._attached_session_id
        assert session_id is not None
        try:
            current = self._conversation_catalog.get(
                session_id,
                focused.conversation_id,
            )
            current = self._validated_catalog_metadata(
                current,
                session_id,
                expected_conversation_id=focused.conversation_id,
            )
        except ConversationNotFoundError as exc:
            self._focused_conversation = None
            self._present_conversation_failure(exc)
            return None
        except Exception as exc:
            self._present_conversation_failure(exc)
            return None
        if current.status is ConversationStatus.ARCHIVED:
            self._focused_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return None
        self._focused_conversation = current
        return current

    def _reconcile_conversation_focus(
        self,
        entries: tuple[ConversationMetadata, ...],
    ) -> None:
        """Refresh, drop, and report current focus against one catalog read.

        The operation changes only effective in-memory metadata. Missing or
        archived durable defaults remain untouched so no silent rewrite occurs.
        """

        focused = self._focused_conversation
        if focused is None:
            return
        current = next(
            (
                entry
                for entry in entries
                if entry.conversation_id == focused.conversation_id
            ),
            None,
        )
        if current is None:
            self._focused_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_NOT_FOUND)
            )
        elif current.status is ConversationStatus.ARCHIVED:
            self._focused_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
        else:
            self._focused_conversation = current

    def _resolve_conversation_reference(
        self,
        session_id: str,
        reference: str,
    ) -> tuple[ConversationMetadata, tuple[ConversationMetadata, ...]]:
        """Resolve exact ID or one case-sensitive unique prefix across all rows.

        Exact identity wins at any valid length. Nonexact prefixes shorter than
        twelve characters, zero matches, and multiple matches raise private
        trusted categories; catalog/storage failures propagate unchanged.
        """

        try:
            safe_reference = self._workspace.validate_identifier(
                reference,
                label="conversation ID",
            )
        except SessionIdError as exc:
            raise _ConversationResolutionFailure(
                InteractiveErrorCode.CONVERSATION_INVALID
            ) from exc
        entries = self._catalog_entries(session_id)
        exact = next(
            (
                entry
                for entry in entries
                if entry.conversation_id == safe_reference
            ),
            None,
        )
        if exact is not None:
            return exact, entries
        if len(safe_reference) < _MINIMUM_CONVERSATION_PREFIX_LENGTH:
            raise _ConversationResolutionFailure(
                InteractiveErrorCode.CONVERSATION_INVALID
            )
        matches = tuple(
            entry
            for entry in entries
            if entry.conversation_id.startswith(safe_reference)
        )
        if not matches:
            raise _ConversationResolutionFailure(
                InteractiveErrorCode.CONVERSATION_NOT_FOUND
            )
        if len(matches) > 1:
            raise _ConversationResolutionFailure(
                InteractiveErrorCode.CONVERSATION_AMBIGUOUS
            )
        return matches[0], entries

    def _catalog_entries(
        self,
        session_id: str,
    ) -> tuple[ConversationMetadata, ...]:
        """Read and validate the complete ordered catalog for one session.

        The method requests active and archived rows, rejects malformed return
        shapes, wrong owners, and duplicate IDs, and performs no sorting or
        persistence mutation so adapter order remains authoritative.
        """

        entries = self._conversation_catalog.list(
            session_id,
            include_archived=True,
        )
        if type(entries) is not tuple:
            raise _ConversationCatalogProtocolFailure(
                "conversation catalog returned an invalid collection"
            )
        validated = tuple(
            self._validated_catalog_metadata(entry, session_id)
            for entry in entries
        )
        identifiers = tuple(entry.conversation_id for entry in validated)
        if len(set(identifiers)) != len(identifiers):
            raise _ConversationCatalogProtocolFailure(
                "conversation catalog contains duplicate identifiers"
            )
        return validated

    @staticmethod
    def _validated_catalog_metadata(
        metadata: object,
        expected_session_id: str,
        *,
        expected_conversation_id: str | None = None,
    ) -> ConversationMetadata:
        """Return exact session-owned metadata or raise a safe format failure.

        Validation is read-only and ensures an injected or filesystem catalog
        cannot make the controller commit foreign or malformed identity state.
        """

        if type(metadata) is not ConversationMetadata:
            raise _ConversationCatalogProtocolFailure(
                "conversation catalog returned invalid metadata"
            )
        if metadata.session_id != expected_session_id:
            raise _ConversationCatalogProtocolFailure(
                "conversation metadata belongs to another session"
            )
        if (
            expected_conversation_id is not None
            and metadata.conversation_id != expected_conversation_id
        ):
            raise _ConversationCatalogProtocolFailure(
                "conversation metadata identity changed unexpectedly"
            )
        return metadata

    @staticmethod
    def _normalized_conversation_input(value: str) -> str:
        """Return a normalized valid subject/query or raise trusted invalid input.

        Only ``TypeError`` and ``ValueError`` raised directly by the stable
        normalization contract are wrapped. Catalog and callback exceptions are
        classified independently and cannot masquerade as user-input failures.
        """

        try:
            return normalize_conversation_subject(value)
        except (TypeError, ValueError) as exc:
            raise _ConversationInputFailure from exc

    @staticmethod
    def _visible_conversation_handle(
        metadata: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> str:
        """Return a unique twelve-character prefix or the immutable full ID.

        Uniqueness is evaluated across every supplied active and archived row.
        The function performs no I/O or mutation and assumes validated metadata.
        """

        prefix = metadata.conversation_id[:_MINIMUM_CONVERSATION_PREFIX_LENGTH]
        collisions = sum(
            entry.conversation_id.startswith(prefix) for entry in entries
        )
        return prefix if collisions == 1 else metadata.conversation_id

    def _visible_handle_after_mutation(
        self,
        metadata: ConversationMetadata,
    ) -> str:
        """Derive a post-mutation handle, falling back safely to the full ID.

        Catalog-read failure is reported under command-time access rules. The
        completed durable mutation is never rolled back merely for presentation.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            entries = self._catalog_entries(session_id)
        except Exception as exc:
            self._present_conversation_failure(exc)
            return metadata.conversation_id
        return self._visible_conversation_handle(metadata, entries)

    def _confirm_attachment_after_command_failure(self) -> None:
        """Require public proof that ready access remains held after failure.

        The provider-free lifecycle read performs no mutation. Missing, foreign,
        paused, unowned, or unreadable state abandons explicit release and
        raises ``cleanup_failed`` so backend close remains best effort.
        """

        session_id = self._attached_session_id
        if (
            session_id is None
            or not self._attachment_is_safely_retained(session_id)
        ):
            self._abandon_known_attachment_for_cleanup_abort()

    def _present_conversation_failure(
        self,
        failure: Exception,
        *,
        creating: bool = False,
    ) -> None:
        """Map one conversation failure and render it without exception prose.

        Invalid input and deterministic lookup categories are immediately safe.
        Workspace, I/O, and unexpected command-time failures require public
        proof that the session remains ready with access held here before the
        controller returns to its prompt; failed proof aborts cleanup.
        """

        error = self._classify_conversation_failure(
            failure,
            creating=creating,
        )
        if error.code is InteractiveErrorCode.INTERNAL_ERROR:
            raise _ControllerAbort(error)
        if error.code in {
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.WORKSPACE_UNSAFE,
        }:
            self._confirm_attachment_after_command_failure()
        self._terminal.write_error(error)

    @staticmethod
    def _classify_conversation_failure(
        failure: Exception,
        *,
        creating: bool = False,
    ) -> InteractiveError:
        """Select the exact safe conversation/workspace code by exception type.

        Resolution categories take precedence, followed by creation exhaustion,
        missing records, invalid subject/ID values, workspace security/format,
        and finally unexpected internal failure. Raw diagnostics are discarded.
        """

        if isinstance(failure, _ConversationResolutionFailure):
            code = failure.code
        elif isinstance(failure, _ConversationInputFailure):
            code = InteractiveErrorCode.CONVERSATION_INVALID
        elif isinstance(failure, _ConversationCatalogProtocolFailure):
            code = InteractiveErrorCode.INTERNAL_ERROR
        elif creating and isinstance(failure, ConversationExistsError):
            code = InteractiveErrorCode.CONVERSATION_CREATE_FAILED
        elif isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
        elif isinstance(failure, SessionIdError):
            code = InteractiveErrorCode.CONVERSATION_INVALID
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(failure, (WorkspaceFormatError, OSError)):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        return InteractiveError(code)

    def _write_conversation_cancelled_status(self, text: str) -> None:
        """Acknowledge one cancelled conversation action without state mutation."""

        self._terminal.write_status(TerminalMessageKind.CANCELLED, text)

    def _list_story_sessions(self) -> None:
        """Render canonical read-only lifecycle rows in backend order."""

        try:
            states = self._backend.list_session_lifecycles()
            if type(states) is not tuple:
                raise BackendProtocolError(
                    "backend returned an invalid lifecycle collection"
                )
            validated = tuple(
                self._require_lifecycle_state(state, state.session_id)
                if type(state) is SessionLifecycleState
                else self._require_lifecycle_state(state, "invalid")
                for state in states
            )
        except Exception as exc:
            self._present_session_failure(exc, self._context.default_session_id)
            return
        if not validated:
            self._terminal.write_status(
                TerminalMessageKind.SESSION,
                "No story sessions found.",
            )
            return
        for state in validated:
            try:
                provider_ready = self._provider_work_is_ready(state)
            except Exception as exc:
                self._present_session_failure(exc, state.session_id)
                return
            attached = self._attached_session_id == state.session_id
            focused = attached and self._focused_conversation is not None
            next_action = self._next_session_action(
                state,
                attached=attached,
                focused=focused,
                provider_ready=provider_ready,
            )
            self._terminal.write_status(
                TerminalMessageKind.SESSION,
                (
                    f"Session: {state.session_id} | "
                    f"Durable state: {state.durable_state.value} | "
                    f"Process access: {state.access_state.value} | "
                    f"Default session: "
                    f"{'yes' if self._context.default_session_id == state.session_id else 'no'} | "
                    f"Provider readiness: {'yes' if provider_ready else 'no'} | "
                    f"Next action: {next_action}"
                ),
            )

    def _pause_attached_session(self) -> bool:
        """Confirm, pause, release access, and return to the session lobby."""

        session_id = self._attached_session_id
        if session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return False
        decision = self._terminal.request_confirmation(
            f"Pause session {session_id}?",
            default=False,
        )
        if decision is ConfirmationResult.EOF:
            return True
        if decision is not ConfirmationResult.YES:
            self._write_session_pause_cancelled_status()
            return False
        try:
            state = self._backend.pause_session_and_release_access(session_id)
            state = self._require_lifecycle_state(state, session_id)
            if (
                state.durable_state is not SessionDurableState.PAUSED
                or state.access_state is SessionAccessState.HELD_HERE
            ):
                raise BackendProtocolError(
                    "backend did not return a released paused session"
                )
        except Exception as exc:
            if not self._attachment_is_safely_retained(session_id):
                self._abandon_known_attachment_for_cleanup_abort()
            self._terminal.write_error(self._classify_session_failure(exc))
            return False

        self._attached_session_id = None
        self._focused_conversation = None
        self._conversation_memory_service = None
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._terminal.write_status(
            TerminalMessageKind.SESSION,
            f"Paused {session_id}; default retained.",
        )
        self._write_lobby_orientation()
        return False

    def _attachment_is_safely_retained(self, session_id: str) -> bool:
        """Return whether public state proves ready access is still held here."""

        try:
            state = self._backend.get_session_lifecycle(session_id)
            self._require_lifecycle_state(state, session_id)
        except (Exception, KeyboardInterrupt):
            return False
        return (
            state.durable_state is SessionDurableState.READY
            and state.access_state is SessionAccessState.HELD_HERE
        )

    def _write_parser_error(
        self,
        action: InteractiveAction,
        original_line: str,
    ) -> None:
        """Render a routed-agent or general parse error from original syntax.

        The raw line's first exact ASCII-delimited token, never parser error
        prose, selects ``agent_invalid`` for ``@``, ``/to``, and ``/agent``
        forms. The method returns no value, mutates no state, and relies on the
        terminal writer to sanitize copy; writer failure needs no local cleanup.
        """

        assert action.kind is ActionKind.PARSE_ERROR
        assert action.error is not None
        code = (
            InteractiveErrorCode.AGENT_INVALID
            if self._parse_error_targets_agent(original_line)
            else InteractiveErrorCode.COMMAND_INVALID
        )
        hint = (
            f"Try {action.suggestion}, or type /help."
            if action.suggestion is not None
            else None
        )
        self._terminal.write_error(
            InteractiveError(
                code,
                summary=action.error,
                hint=hint,
            )
        )

    @staticmethod
    def _parse_error_targets_agent(original_line: str) -> bool:
        """Return whether a parse failure identifies an invalid agent route.

        Direct routing forms are always agent-scoped. For ``/persona``, only an
        explicitly supplied unknown first argument is agent-scoped; missing or
        malformed persona syntax remains ``command_invalid``. The pure check
        uses exact ASCII delimiters and never inspects parser failure prose.
        """

        if original_line.startswith("@"):
            return True
        first_delimiter = next(
            (
                index
                for index, character in enumerate(original_line)
                if character in " \t"
            ),
            len(original_line),
        )
        command = original_line[:first_delimiter]
        if command in {"/to", "/agent"}:
            return True
        if command != "/persona" or first_delimiter == len(original_line):
            return False
        arguments = tuple(
            token
            for token in original_line[first_delimiter:]
            .replace("\t", " ")
            .split(" ")
            if token
        )
        return bool(arguments) and arguments[0] not in _AGENT_ROUTE_ALIASES

    def _present_session_failure(
        self,
        failure: Exception,
        _session_id: str | None,
    ) -> None:
        """Map a typed operation failure to safe output or a fatal abort.

        Mapping uses exception classes, never exception prose. Recoverable
        session/preflight categories return to the
        prompt; workspace, internal, and cleanup uncertainty abort the run.
        """

        error = self._classify_session_failure(failure)
        if error.code not in _RECOVERABLE_SESSION_ERRORS:
            raise _ControllerAbort(error)
        self._terminal.write_error(error)

    @staticmethod
    def _classify_session_failure(failure: Exception) -> InteractiveError:
        """Select one safe interactive code from type, without raw diagnostics.

        The function does not mutate state. Backend protocol/configuration
        failures at this boundary mean lifecycle preflight failure; workspace
        security/format and unknown failures remain fatal to the controller.
        """

        if isinstance(failure, SessionIdError):
            code = InteractiveErrorCode.SESSION_INVALID
        elif isinstance(failure, SessionExistsError):
            code = InteractiveErrorCode.SESSION_EXISTS
        elif isinstance(failure, SessionNotFoundError):
            code = InteractiveErrorCode.SESSION_NOT_FOUND
        elif isinstance(failure, SessionPausedError):
            code = InteractiveErrorCode.SESSION_PAUSED
        elif isinstance(failure, SessionAccessBlockedError):
            code = InteractiveErrorCode.SESSION_ACCESS_BLOCKED
        elif isinstance(failure, SessionOwnershipUnknownError):
            code = InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
        elif isinstance(failure, WorkspaceVersionUnsupportedError):
            code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
        elif isinstance(failure, (WorkspaceFormatError, OSError)):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        elif isinstance(failure, (ConfigurationError, BackendProtocolError)):
            code = InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        return InteractiveError(code)

    def _write_main_input_cancelled_status(self) -> None:
        """Acknowledge main-input Ctrl-C without changing controller state."""

        self._terminal.write_status(
            TerminalMessageKind.CANCELLED,
            "No action was taken.",
        )

    def _write_session_open_cancelled_status(self) -> None:
        """Acknowledge declined default-session open without mutation."""

        self._terminal.write_status(
            TerminalMessageKind.CANCELLED,
            "Session open cancelled.",
        )

    def _write_session_pause_cancelled_status(self) -> None:
        """Acknowledge declined session pause while retaining attachment."""

        self._terminal.write_status(
            TerminalMessageKind.CANCELLED,
            "Session pause cancelled.",
        )

    def _write_lobby_orientation(self) -> None:
        """Render workspace-scoped lobby help without acquiring resources."""

        self._terminal.write_lobby_orientation(str(self._workspace.root))

    def _abandon_known_attachment_for_cleanup_abort(self) -> None:
        """Clear uncertain bookkeeping and abort into backend-close cleanup.

        No further explicit release is attempted because a prior lifecycle
        failure made source/destination ownership ambiguous. Process attachment,
        focus, and memory scope are cleared while backend ``close`` remains
        the best-effort resource cleanup operation.
        """

        self._attached_session_id = None
        self._focused_conversation = None
        self._conversation_memory_service = None
        raise _ControllerAbort(
            InteractiveError(InteractiveErrorCode.CLEANUP_FAILED)
        )

    def _write_error_best_effort(self, error: InteractiveError) -> bool:
        """Attempt one diagnostic without delaying lifecycle cleanup.

        Ordinary callback failures are suppressed after the attempted write.
        ``KeyboardInterrupt`` and every other ``BaseException`` retain their
        fatal process semantics while the caller's cleanup ``finally`` runs.
        """

        try:
            self._terminal.write_error(error)
        except Exception:
            return False
        return True

    def _release_attachment_and_close_backend(
        self,
        exit_code: int,
        *,
        cleanup_error_reported: bool,
    ) -> int:
        """Close Runner, release access, close backend, and rank failures.

        Normal quit/EOF releases access without pausing. Each cleanup stage runs
        after the preceding stage even when that stage fails. Runner, access,
        backend, attachment, focus, and memory state are cleared before the
        method returns; one best-effort cleanup diagnostic accompanies status 2.
        A ``BaseException`` from any stage propagates only after later stages
        run. If Runner close is interrupted, its private reference is retained
        because successful close was not established.
        """

        if self._closed:
            return exit_code
        cleanup_failed = False
        runner = self._runner
        discard_runner_state_after_cleanup = runner is None
        attached_session = self._attached_session_id
        try:
            if runner is not None:
                try:
                    runner.close()
                except Exception:
                    cleanup_failed = True
                    discard_runner_state_after_cleanup = True
                else:
                    discard_runner_state_after_cleanup = True
        finally:
            try:
                if attached_session is not None:
                    try:
                        released = self._backend.release_session_access(
                            attached_session
                        )
                        self._require_released_state(
                            released,
                            attached_session,
                        )
                    except Exception:
                        cleanup_failed = True
            finally:
                try:
                    try:
                        self._backend.close()
                    except Exception:
                        cleanup_failed = True
                finally:
                    if discard_runner_state_after_cleanup:
                        self._runner = None
                    self._attached_session_id = None
                    self._focused_conversation = None
                    self._conversation_memory_service = None
                    self._closed = True

        if cleanup_failed and not cleanup_error_reported:
            self._write_error_best_effort(
                InteractiveError(InteractiveErrorCode.CLEANUP_FAILED)
            )
        return 2 if cleanup_failed else exit_code


__all__ = ["InteractiveController"]
