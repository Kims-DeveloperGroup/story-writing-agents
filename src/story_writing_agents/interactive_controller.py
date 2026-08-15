"""Synchronous interactive session, conversation, and advisory coordination."""

from __future__ import annotations

import asyncio

from .agents import LEAD_ORCHESTRATOR
from .backend import AgentBackend
from .cli_context import (
    CliContext,
    load_conversation_selection,
    save_conversation_selection,
    select_session,
)
from .conversations import (
    ConversationCatalog,
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
)
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationExistsError,
    ConversationNotFoundError,
    SessionExistsError,
    SessionIdError,
    SessionInUseError,
    SessionNotFoundError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .interactive_commands import (
    ActionKind,
    InteractiveAction,
    parse_interactive_line,
)
from .interactive_terminal import (
    AttachmentKind,
    ConfirmationResult,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveTerminal,
    TerminalActionKind,
    TerminalMessageKind,
)
from .models import ConversationResult, SessionLeaseState, SessionSummary
from .orchestrator import StoryRuntime
from .workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)


_UNAVAILABLE_SUMMARY = "This command is not available in the session lobby."
_LOBBY_COMMAND_HINT = (
    "Use /help, /session list, /session new NAME, /session resume [NAME], "
    "/session end, or /quit."
)
_MINIMUM_CONVERSATION_PREFIX_LENGTH = 12
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
        InteractiveErrorCode.SESSION_IN_USE,
        InteractiveErrorCode.SESSION_INACTIVE,
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
    ``StoryRuntime`` exclusively owns provider turns and transcript outcomes.
    """

    __slots__ = (
        "_attached_session_id",
        "_backend",
        "_closed",
        "_conversation_catalog",
        "_context",
        "_runtime",
        "_selected_conversation",
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
        """Bind collaborators and construct the retained advisory runtime.

        The four positional arguments remain the established construction
        contract. An omitted keyword-only catalog creates the workspace-backed
        adapter; an injected protocol implementation is retained as borrowed
        state and is never closed by this controller. Construction performs no
        provider call or filesystem write and acquires no cleanup lifecycle.
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
        conversation_event_store = FilesystemConversationEventStore(
            workspace,
            catalog=resolved_catalog,
        )
        self._runtime = StoryRuntime(
            workspace,
            backend,
            conversation_event_store=conversation_event_store,
        )
        self._attached_session_id: str | None = None
        self._selected_conversation: ConversationMetadata | None = None
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._started = False
        self._closed = False

    def run_session_lobby(
        self,
        *,
        requested_session: str | None = None,
    ) -> int:
        """Run one interactive attachment lifecycle and return status 0 or 2.

        ``requested_session`` selects explicit create/attach/resume behavior;
        ``None`` uses saved selection. Commands may mutate owned process state,
        established session/conversation persistence, or runtime-owned turn
        transcripts. Every started exit detaches known ownership and closes the
        borrowed backend; fatal uncertainty returns 2, and a second run raises
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
            self._terminal.write_error(abort.error)
            cleanup_error_reported = (
                abort.error.code is InteractiveErrorCode.CLEANUP_FAILED
            )
            exit_code = 2
        except Exception:
            self._terminal.write_error(
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
        """Apply explicit or saved startup selection; return whether EOF exits.

        Recoverable acquisition failures are rendered and leave the controller
        unattached. Fatal workspace or cleanup uncertainty raises a typed abort
        for the outer resource boundary.
        """

        if requested_session is not None:
            self._enter_explicit_session(requested_session)
            return False

        selected_session = self._context.selected_session
        if selected_session is None:
            return False
        try:
            summary = self._backend.get_session_summary(
                selected_session,
                selected_session=selected_session,
            )
        except Exception as exc:
            self._present_session_failure(exc, selected_session)
            return False

        if not self._summary_matches_requested_session(
            summary,
            selected_session,
        ):
            self._present_session_failure(
                BackendProtocolError("session summary did not match request"),
                selected_session,
            )
            return False

        if summary.active:
            self._acquire_known_session(selected_session, summary)
            return False

        decision = self._terminal.request_confirmation(
            f"Resume selected session {selected_session}?",
            default=True,
        )
        if decision is ConfirmationResult.EOF:
            return True
        if decision is not ConfirmationResult.YES:
            self._write_session_resume_cancelled_status()
            return False
        self._acquire_known_session(selected_session, summary)
        return False

    def _enter_explicit_session(self, session_id: str) -> None:
        """Create a missing explicit session or attach/resume an existing one.

        The method performs no confirmation. Recoverable failures keep the
        controller unattached; successful lease acquisition is followed by
        selection persistence and orientation rendering.
        """

        try:
            summary = self._backend.get_session_summary(
                session_id,
                selected_session=self._context.selected_session,
            )
        except SessionNotFoundError:
            self._create_and_attach_session(session_id)
            return
        except Exception as exc:
            self._present_session_failure(exc, session_id)
            return
        self._acquire_known_session(session_id, summary)

    def _create_and_attach_session(self, session_id: str) -> None:
        """Create and lease one destination, then select or switch to it.

        Acquisition failures preserve the current attachment. A successful
        create is cleaned up if selection cannot be persisted; cleanup
        uncertainty aborts the process with ``cleanup_failed``.
        """

        try:
            summary = self._backend.create_session(session_id)
        except Exception as exc:
            self._present_session_failure(exc, session_id)
            return
        self._accept_acquired_destination(
            session_id,
            summary,
            AttachmentKind.CREATED,
        )

    def _resume_selected_or_named_session(
        self,
        requested_session: str | None,
    ) -> None:
        """Resume an explicit or saved session without creating missing state.

        The already-attached destination is an acknowledged no-op. Otherwise
        lifecycle failures retain the source attachment and selection whenever
        their public state remains trustworthy.
        """

        session_id = (
            requested_session
            if requested_session is not None
            else self._context.selected_session
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
        try:
            summary = self._backend.get_session_summary(
                session_id,
                selected_session=self._context.selected_session,
            )
        except Exception as exc:
            self._present_session_failure(exc, session_id)
            return
        self._acquire_known_session(session_id, summary)

    def _acquire_known_session(
        self,
        expected_session: str,
        summary: SessionSummary,
    ) -> None:
        """Lease a known session with attach or resume semantics, then commit.

        The summary must exactly match ``expected_session`` before its active
        state selects the public backend operation. Races and preflight failures
        are reported without changing the current source.
        """

        if not self._summary_matches_requested_session(
            summary,
            expected_session,
        ):
            self._present_session_failure(
                BackendProtocolError("session summary did not match request"),
                expected_session,
            )
            return
        session_id = expected_session
        attachment = (
            AttachmentKind.ATTACHED
            if summary.active
            else AttachmentKind.RESUMED
        )
        try:
            acquired = (
                self._backend.attach_session(session_id)
                if summary.active
                else self._backend.resume_session(session_id)
            )
        except Exception as exc:
            self._present_session_failure(
                exc,
                session_id,
                check_for_inactive_race=summary.active,
            )
            return
        self._accept_acquired_destination(session_id, acquired, attachment)

    def _accept_acquired_destination(
        self,
        expected_session: str,
        summary: SessionSummary,
        attachment: AttachmentKind,
    ) -> None:
        """Validate the requested acquired lease, then attach or switch safely.

        The exact ``SessionSummary`` must identify ``expected_session`` as
        active and owned. Any mismatch releases the requested destination when
        possible and aborts into backend-close cleanup before selection writes.
        """

        if (
            not self._summary_matches_requested_session(
                summary,
                expected_session,
            )
            or summary.active is not True
            or summary.lease_state is not SessionLeaseState.OWNED
        ):
            self._release_uncommitted_destination(expected_session)
            self._abandon_known_attachment_for_cleanup_abort()
        try:
            selected_conversation = self._load_destination_conversation(
                summary.session_id
            )
        except Exception as exc:
            self._release_uncommitted_destination(expected_session)
            self._present_destination_conversation_failure(exc)
            return
        if self._attached_session_id is None:
            self._attach_selected_session(
                summary.session_id,
                attachment,
                selected_conversation,
            )
            return
        self._switch_attached_session(
            summary.session_id,
            attachment,
            selected_conversation,
        )

    def _load_destination_conversation(
        self,
        session_id: str,
    ) -> ConversationMetadata | None:
        """Load an existing active saved choice for one acquired destination.

        Missing files, null choices, absent records, and archived records return
        ``None`` without rewriting persistence. Malformed selection or catalog
        state propagates so the caller can release the uncommitted destination
        before presenting a safe error.
        """

        selected_id = load_conversation_selection(self._workspace, session_id)
        if selected_id is None:
            return None
        try:
            metadata = self._conversation_catalog.get(session_id, selected_id)
        except ConversationNotFoundError:
            return None
        validated = self._validated_catalog_metadata(
            metadata,
            session_id,
            expected_conversation_id=selected_id,
        )
        if validated.status is ConversationStatus.ARCHIVED:
            return None
        return validated

    def _present_destination_conversation_failure(
        self,
        failure: Exception,
    ) -> None:
        """Render a released destination's typed catalog failure without prose.

        The caller has already returned the destination lease. Workspace
        failures render and preserve an existing source, but an initial
        attachment has no source and aborts. Protocol/internal failure always
        aborts so the outer boundary detaches any known source and closes.
        """

        error = self._classify_conversation_failure(failure)
        if (
            error.code is InteractiveErrorCode.INTERNAL_ERROR
            or self._attached_session_id is None
        ):
            raise _ControllerAbort(error)
        self._terminal.write_error(error)

    def _attach_selected_session(
        self,
        session_id: str,
        attachment: AttachmentKind,
        selected_conversation: ConversationMetadata | None,
    ) -> None:
        """Persist an initial destination and commit it as process attachment.

        Selection is written only after lease acquisition. A selection failure
        releases the destination before it is reported; failed release makes
        the remaining lease state fatal and delegates cleanup to backend close.
        """

        next_context = self._persist_destination_selection(session_id)
        if next_context is None:
            return
        self._commit_attached_session(
            next_context,
            session_id,
            attachment,
            selected_conversation,
        )

    def _switch_attached_session(
        self,
        destination_session: str,
        attachment: AttachmentKind,
        selected_conversation: ConversationMetadata | None,
    ) -> None:
        """Select a leased destination before detaching and replacing source.

        Destination acquisition has already succeeded. Selection failure
        releases it and preserves the source. Source-detach failure after the
        selection write is not rolled back; owned resources are closed best
        effort and the process exits with ``cleanup_failed``.
        """

        source_session = self._attached_session_id
        assert source_session is not None
        next_context = self._persist_destination_selection(destination_session)
        if next_context is None:
            return
        try:
            self._backend.detach_session(source_session)
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()
        self._commit_attached_session(
            next_context,
            destination_session,
            attachment,
            selected_conversation,
        )

    def _persist_destination_selection(
        self,
        destination_session: str,
    ) -> CliContext | None:
        """Persist a changed destination after leasing it, or return current.

        On write failure the uncommitted destination lease is released. The
        failure is then reported when recoverable or escalated when workspace
        state is unusable; no controller attachment state is changed here.
        """

        if self._context.selected_session == destination_session:
            return self._context
        try:
            return select_session(self._workspace, destination_session)
        except Exception as exc:
            self._release_uncommitted_destination(destination_session)
            self._present_session_failure(exc, destination_session)
            return None

    def _release_uncommitted_destination(self, session_id: str) -> None:
        """Release a leased destination after pre-commit failure or abort.

        A failed release makes all attachment bookkeeping uncertain, so the
        source marker is cleared and the outer boundary performs only backend
        close before returning ``cleanup_failed``.
        """

        try:
            self._backend.detach_session(session_id)
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

    def _commit_attached_session(
        self,
        context: CliContext,
        session_id: str,
        attachment: AttachmentKind,
        selected_conversation: ConversationMetadata | None,
    ) -> None:
        """Commit process state, reset routing to lead, and render orientation.

        The destination lease and story-session selection are already durable.
        This method performs no backend or catalog lifecycle operation and
        stores only validated conversation metadata owned by the destination.
        """

        self._context = context
        self._attached_session_id = session_id
        self._selected_conversation = selected_conversation
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._terminal.write_session_orientation(
            str(self._workspace.root),
            session_id,
            attachment,
            self._target_agent_id,
            history_count=None,
        )

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
        category. Global help, roster, and story-session navigation remain
        provider-free; routed content uses the retained runtime, while
        later-feature actions preserve their selection-aware rejection. The
        returned boolean signals loop exit; command-specific state changes are
        delegated to their owners, and fatal failures reach the outer cleanup
        boundary without local resource acquisition.
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
        if action.kind is ActionKind.SESSION_NEW:
            assert action.argument is not None
            self._create_and_attach_session(action.argument)
            return False
        if action.kind is ActionKind.SESSION_RESUME:
            self._resume_selected_or_named_session(action.argument)
            return False
        if action.kind is ActionKind.SESSION_END:
            return self._end_attached_session()
        if action.kind is ActionKind.QUIT:
            return True
        if action.kind is ActionKind.PARSE_ERROR:
            self._write_parser_error(action, original_line)
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
            ActionKind.CONVERSATION_NEW,
            ActionKind.CONVERSATION_LIST,
            ActionKind.CONVERSATION_LIST_ALL,
            ActionKind.CONVERSATION_USE,
            ActionKind.CONVERSATION_SUBJECT,
            ActionKind.CONVERSATION_ARCHIVE,
        }:
            if self._attached_session_id is None:
                self._terminal.write_error(
                    InteractiveError(InteractiveErrorCode.SESSION_INACTIVE)
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
                self._select_subject_conversation(action.argument)
                return False
            if action.kind is ActionKind.CONVERSATION_SUBJECT:
                assert action.argument is not None
                self._rename_selected_conversation(action.argument)
                return False
            return self._archive_subject_conversation(action.argument)
        if (
            self._attached_session_id is not None
            and self._selected_conversation is None
        ):
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
            )
            return False
        self._write_unavailable_lobby_command_error()
        return False

    def _set_target_agent(self, action: InteractiveAction) -> None:
        """Set one attached process's sticky responder without provider work.

        The validated action supplies a canonical agent and the method returns
        no value. An unattached controller renders ``session_inactive``;
        success mutates only process-local routing and needs no cleanup.
        """

        assert action.kind is ActionKind.SET_TARGET
        assert action.agent_id is not None
        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_INACTIVE)
            )
            return
        self._target_agent_id = action.agent_id
        self._terminal.write_status(
            TerminalMessageKind.ROUTING,
            f"Sticky target set to {action.agent_id}.",
        )

    def _run_advisory_turn(self, action: InteractiveAction) -> None:
        """Run one selected-conversation turn through the retained runtime.

        ``MESSAGE`` uses the sticky target and ``ROUTED_MESSAGE`` uses its
        one-shot canonical agent without changing sticky state. The method
        revalidates attachment and selection, renders safe progress, performs
        exactly one synchronous ``asyncio.run`` boundary, and delegates durable
        turn outcome ownership to ``StoryRuntime``. Typed expected failures
        return to the prompt; invalid results or uncertain ownership abort into
        the controller's existing detach/close cleanup boundary.
        """

        if self._attached_session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_INACTIVE)
            )
            return
        selected = self._require_active_selected_conversation()
        if selected is None:
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
        routing += (
            "Direct routing selects the responder in this shared room; "
            "it does not make the turn private."
        )
        self._terminal.write_status(TerminalMessageKind.ROUTING, routing)
        self._terminal.write_status(
            TerminalMessageKind.WAITING,
            f"Waiting for {agent_id}.",
        )

        try:
            result = asyncio.run(
                self._runtime.converse(
                    session_id,
                    selected.conversation_id,
                    agent_id,
                    action.text,
                )
            )
        except Exception as exc:
            self._present_advisory_turn_failure(exc)
            return

        if (
            type(result) is not ConversationResult
            or result.session_id != session_id
            or result.conversation_id != selected.conversation_id
            or result.agent_id != agent_id
        ):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
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

    def _present_advisory_turn_failure(self, failure: Exception) -> None:
        """Render one typed turn failure or abort when recovery is uncertain.

        Exception classes, never exception prose, select the result. Missing
        conversations clear only stale effective in-memory selection, leaving
        the persisted selection and catalog untouched. Session/workspace
        failures require proof that the existing lease remains active and
        owned. Unknown failures enter fatal internal cleanup, while runtime
        remains the sole owner of any durable turn outcome.
        """

        error = self._classify_advisory_turn_failure(failure)
        if error.code is InteractiveErrorCode.INTERNAL_ERROR:
            raise _ControllerAbort(error)
        if error.code is InteractiveErrorCode.CONVERSATION_NOT_FOUND:
            self._selected_conversation = None
        if error.code in {
            InteractiveErrorCode.SESSION_INVALID,
            InteractiveErrorCode.SESSION_EXISTS,
            InteractiveErrorCode.SESSION_NOT_FOUND,
            InteractiveErrorCode.SESSION_IN_USE,
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
        elif isinstance(failure, SessionExistsError):
            code = InteractiveErrorCode.SESSION_EXISTS
        elif isinstance(failure, SessionNotFoundError):
            code = InteractiveErrorCode.SESSION_NOT_FOUND
        elif isinstance(failure, SessionInUseError):
            code = InteractiveErrorCode.SESSION_IN_USE
        elif isinstance(failure, ConversationNotFoundError):
            code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
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

    def _write_attached_conversation_scope(self) -> None:
        """Render the effective selected scope immediately before main input.

        The current metadata is revalidated against the complete catalog so a
        unique display handle can be derived across active and archived IDs.
        Missing or archived external state drops only in-memory selection and
        reports its typed category; durable selection is never rewritten here.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        selected = self._selected_conversation
        if selected is None:
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
            handle = selected.conversation_id
        else:
            current = next(
                (
                    entry
                    for entry in entries
                    if entry.conversation_id == selected.conversation_id
                ),
                None,
            )
            if current is None:
                self._selected_conversation = None
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
                self._selected_conversation = None
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
            self._selected_conversation = current
            selected = current
            handle = self._visible_conversation_handle(current, entries)

        self._terminal.write_conversation_scope(
            session_id=session_id,
            conversation_handle=handle,
            conversation_subject=selected.subject,
            agent_id=self._target_agent_id,
        )

    def _create_subject_conversation(self, subject: str | None) -> bool:
        """Create and select one active conversation, returning EOF exit intent.

        A missing subject causes exactly one call-local terminal read. Interrupt
        cancels without mutation and EOF requests normal process cleanup. After
        catalog creation, selection is saved before memory changes; a save
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
            save_conversation_selection(
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
                        "but was not selected."
                    ),
                    hint=(
                        "Repair workspace storage, then use the full ID to "
                        "select it."
                    ),
                )
            )
            return False

        self._selected_conversation = created
        handle = self._visible_handle_after_mutation(created)
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            f"Created and selected {handle} | Subject: {created.subject}",
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
        archival of the in-memory selection clears memory but not its file.
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

        self._reconcile_in_memory_selection(entries)
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

        selected_id = (
            None
            if self._selected_conversation is None
            else self._selected_conversation.conversation_id
        )
        for entry in visible_entries:
            self._terminal.write_conversation_list_entry(
                self._visible_conversation_handle(entry, entries),
                entry.subject,
                archived=(entry.status is ConversationStatus.ARCHIVED),
                selected=(entry.conversation_id == selected_id),
            )

    def _select_subject_conversation(self, reference: str) -> None:
        """Resolve and durably select one active conversation without rerouting.

        Exact IDs win over case-sensitive prefixes of at least twelve
        characters. Resolution includes archived records, which receive a
        typed rejection. Selection persistence completes before in-memory
        metadata and presentation change.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            selected, entries = self._resolve_conversation_reference(
                session_id,
                reference,
            )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        if selected.status is ConversationStatus.ARCHIVED:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return
        try:
            save_conversation_selection(
                self._workspace,
                session_id,
                selected.conversation_id,
            )
        except Exception as exc:
            self._confirm_attachment_after_command_failure()
            self._terminal.write_error(
                self._classify_conversation_failure(exc)
            )
            return

        self._selected_conversation = selected
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            (
                "Selected "
                f"{self._visible_conversation_handle(selected, entries)} | "
                f"Subject: {selected.subject}"
            ),
        )

    def _rename_selected_conversation(self, subject: str) -> None:
        """Atomically rename the selected active conversation, then update memory.

        The exact display subject is validated without normalization replacement.
        Missing or externally archived selections enter the conversation lobby
        without rewriting their stale durable choice; catalog failure leaves
        the previous process metadata intact until later revalidation.
        """

        current = self._require_active_selected_conversation()
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
            self._selected_conversation = None
            self._present_conversation_failure(exc)
            return
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        if renamed.status is ConversationStatus.ARCHIVED:
            self._selected_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return

        self._selected_conversation = renamed
        handle = self._visible_handle_after_mutation(renamed)
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            f"Renamed {handle} | Subject: {renamed.subject}",
        )

    def _archive_subject_conversation(self, reference: str | None) -> bool:
        """Archive one resolved conversation and return confirmation EOF intent.

        Bare archive requires the selected active record. Explicit lookup can
        target any record; an already archived nonselection is an acknowledged
        no-op. Archiving the current selection delegates to the compensated
        successor transition, while other targets require one catalog mutation.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        if reference is None:
            target = self._require_active_selected_conversation()
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

        selected_id = (
            None
            if self._selected_conversation is None
            else self._selected_conversation.conversation_id
        )
        is_selected = target.conversation_id == selected_id
        if target.status is ConversationStatus.ARCHIVED:
            if is_selected:
                self._selected_conversation = None
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
        if is_selected:
            return self._archive_selected_conversation(target, entries)
        self._archive_unselected_conversation(target, entries)
        return False

    def _archive_unselected_conversation(
        self,
        target: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> None:
        """Archive one active nonselection with a single catalog mutation.

        The durable and in-memory selection are never changed. A validated
        archived result is rendered using the pre-mutation all-record handle;
        failures follow command-time lease confirmation rules.
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

    def _archive_selected_conversation(
        self,
        target: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> bool:
        """Confirm, archive, and durably select the first active successor.

        No, blank, or interrupt cancels without mutation; EOF requests normal
        detach. After archive, successor/null persistence precedes memory. A
        failed selection write best-effort unarchives and proves both original
        active metadata and original durable selection before recovery is
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
            self._selected_conversation = None
            self._present_conversation_failure(exc)
            return False
        except Exception as exc:
            self._present_conversation_failure(exc)
            return False

        try:
            save_conversation_selection(
                self._workspace,
                session_id,
                (
                    None
                    if successor is None
                    else successor.conversation_id
                ),
            )
        except Exception as exc:
            self._compensate_failed_selected_archive(target, exc)
            return False

        self._selected_conversation = successor
        status = f"Archived {target_handle} | Subject: {archived.subject}"
        if successor is None:
            status += "; no conversation is selected."
        else:
            status += (
                "; selected "
                f"{self._visible_conversation_handle(successor, entries)} | "
                f"Subject: {successor.subject}"
            )
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            status,
        )
        return False

    def _compensate_failed_selected_archive(
        self,
        original: ConversationMetadata,
        selection_failure: Exception,
    ) -> None:
        """Restore and prove the original active selection after save failure.

        Compensation uses only public catalog and selection APIs. It succeeds
        only when unarchive/get both show the original active metadata, the
        durable selection still names it, and the current session lease is
        publicly confirmed active and owned. Otherwise controller bookkeeping
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
            durable_selection = load_conversation_selection(
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
                durable_selection != original.conversation_id
                or not self._attachment_is_safely_retained(session_id)
            ):
                raise WorkspaceFormatError(
                    "selected archive compensation could not be confirmed"
                )
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

        self._selected_conversation = confirmed
        self._terminal.write_error(
            self._classify_conversation_failure(selection_failure)
        )
        self._terminal.write_status(
            TerminalMessageKind.CONVERSATION,
            "Archive did not complete; the original conversation remains selected.",
        )

    def _require_active_selected_conversation(
        self,
    ) -> ConversationMetadata | None:
        """Return the revalidated active selection or render its typed absence.

        Missing and archived records clear only effective in-memory selection,
        leaving the durable stale ID for safe restart behavior. Other failures
        preserve memory and follow command-time lease confirmation rules.
        """

        selected = self._selected_conversation
        if selected is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
            )
            return None
        session_id = self._attached_session_id
        assert session_id is not None
        try:
            current = self._conversation_catalog.get(
                session_id,
                selected.conversation_id,
            )
            current = self._validated_catalog_metadata(
                current,
                session_id,
                expected_conversation_id=selected.conversation_id,
            )
        except ConversationNotFoundError as exc:
            self._selected_conversation = None
            self._present_conversation_failure(exc)
            return None
        except Exception as exc:
            self._present_conversation_failure(exc)
            return None
        if current.status is ConversationStatus.ARCHIVED:
            self._selected_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return None
        self._selected_conversation = current
        return current

    def _reconcile_in_memory_selection(
        self,
        entries: tuple[ConversationMetadata, ...],
    ) -> None:
        """Refresh, drop, and safely report selection against one catalog read.

        The operation changes only effective in-memory metadata. Missing or
        archived durable choices remain untouched so no silent rewrite occurs.
        """

        selected = self._selected_conversation
        if selected is None:
            return
        current = next(
            (
                entry
                for entry in entries
                if entry.conversation_id == selected.conversation_id
            ),
            None,
        )
        if current is None:
            self._selected_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_NOT_FOUND)
            )
        elif current.status is ConversationStatus.ARCHIVED:
            self._selected_conversation = None
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
        else:
            self._selected_conversation = current

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

        Catalog-read failure is reported under command-time lease rules. The
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
        """Require public proof of the same active owned lease after storage failure.

        The provider-free summary read performs no mutation. Missing, foreign,
        inactive, unowned, or unreadable state abandons explicit detach and
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
        proof that the same session lease remains active and owned before the
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
        elif isinstance(failure, (WorkspaceFormatError, OSError)):
            code = InteractiveErrorCode.WORKSPACE_INVALID
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
        return InteractiveError(code)

    def _write_conversation_cancelled_status(self, text: str) -> None:
        """Acknowledge one cancelled conversation action without state mutation."""

        self._terminal.write_status(TerminalMessageKind.CANCELLED, text)

    def _list_story_sessions(self) -> None:
        """Render deterministic public session summaries without mutation.

        Listing performs no provider preflight or lifecycle change. Invalid
        workspace state aborts the process; ordinary typed session failures are
        rendered and return to the current prompt.
        """

        try:
            summaries = self._backend.list_sessions(
                selected_session=self._context.selected_session
            )
        except Exception as exc:
            self._present_session_failure(exc, self._context.selected_session)
            return
        if not summaries:
            self._terminal.write_status(
                TerminalMessageKind.SESSION,
                "No story sessions found.",
            )
            return
        for summary in summaries:
            self._terminal.write_status(
                TerminalMessageKind.SESSION,
                self._format_session_summary(summary),
            )

    @staticmethod
    def _format_session_summary(summary: SessionSummary) -> str:
        """Format one validated summary including visible lease ownership.

        The function has no side effects. Host and PID are included only when
        the public summary supplies them; terminal rendering sanitizes text.
        """

        state = "active" if summary.active else "inactive"
        selection = "selected" if summary.selected else "not selected"
        text = (
            f"{summary.session_id}: {state}; {selection}; "
            f"lease {summary.lease_state.value}."
        )
        if summary.lease_pid is not None and summary.lease_hostname is not None:
            text += (
                f" Owner PID {summary.lease_pid} on "
                f"{summary.lease_hostname}."
            )
        return text

    def _end_attached_session(self) -> bool:
        """Confirm and deactivate the attachment, returning EOF exit intent.

        No/blank/Ctrl-C preserves the attachment. EOF leaves ending untouched
        and asks the outer boundary to detach normally. A failed end remains
        recoverable only when public state confirms active ownership; all
        uncertain outcomes abort with ``cleanup_failed``.
        """

        session_id = self._attached_session_id
        if session_id is None:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.SESSION_INACTIVE)
            )
            return False
        decision = self._terminal.request_confirmation(
            f"End session {session_id}?",
            default=False,
        )
        if decision is ConfirmationResult.EOF:
            return True
        if decision is not ConfirmationResult.YES:
            self._write_session_end_cancelled_status()
            return False
        try:
            self._backend.end_session(session_id)
        except Exception as exc:
            if not self._attachment_is_safely_retained(session_id):
                self._abandon_known_attachment_for_cleanup_abort()
            self._terminal.write_error(self._classify_session_failure(exc))
            return False

        self._attached_session_id = None
        self._selected_conversation = None
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._terminal.write_status(
            TerminalMessageKind.SESSION,
            f"Ended {session_id}; selection retained.",
        )
        self._write_lobby_orientation()
        return False

    def _attachment_is_safely_retained(self, session_id: str) -> bool:
        """Return whether public state proves an active lease is still owned.

        The check performs a provider-free summary read and never mutates the
        session. Any read failure or non-owned/inactive state is uncertainty,
        so callers must close resources and abort instead of claiming recovery.
        """

        try:
            summary = self._backend.get_session_summary(
                session_id,
                selected_session=self._context.selected_session,
            )
        except Exception:
            return False
        return (
            self._summary_matches_requested_session(summary, session_id)
            and summary.active is True
            and summary.lease_state is SessionLeaseState.OWNED
        )

    @staticmethod
    def _summary_matches_requested_session(
        summary: object,
        expected_session: str,
    ) -> bool:
        """Check exact summary identity and route fields without side effects.

        The result is false for foreign or malformed backend values, allowing
        callers to fail closed before lifecycle or selection mutation.
        """

        return (
            type(summary) is SessionSummary
            and summary.session_id == expected_session
            and type(summary.active) is bool
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
        """Return whether raw input begins an exact agent-routing form.

        The original line is inspected only at character zero and its first
        ASCII space/tab boundary. It is not mutated, no failure prose is read,
        and the pure classification owns no resource or cleanup.
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
        return original_line[:first_delimiter] in {"/to", "/agent"}

    def _present_session_failure(
        self,
        failure: Exception,
        session_id: str | None,
        *,
        check_for_inactive_race: bool = False,
    ) -> None:
        """Map a typed operation failure to safe output or a fatal abort.

        Mapping uses exception classes and optional public state only, never
        exception prose. Recoverable session/preflight categories return to the
        prompt; workspace, internal, and cleanup uncertainty abort the run.
        """

        error = self._classify_session_failure(failure)
        if (
            check_for_inactive_race
            and session_id is not None
            and isinstance(failure, BackendProtocolError)
            and not isinstance(
                failure,
                (SessionExistsError, SessionNotFoundError, SessionInUseError),
            )
        ):
            try:
                summary = self._backend.get_session_summary(
                    session_id,
                    selected_session=self._context.selected_session,
                )
            except SessionNotFoundError:
                error = InteractiveError(
                    InteractiveErrorCode.SESSION_NOT_FOUND
                )
            except (WorkspaceSecurityError, WorkspaceFormatError, OSError) as exc:
                raise _ControllerAbort(
                    self._classify_session_failure(exc)
                ) from None
            except Exception:
                pass
            else:
                if (
                    self._summary_matches_requested_session(
                        summary,
                        session_id,
                    )
                    and summary.active is False
                ):
                    error = InteractiveError(
                        InteractiveErrorCode.SESSION_INACTIVE
                    )

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
        elif isinstance(failure, SessionInUseError):
            code = InteractiveErrorCode.SESSION_IN_USE
        elif isinstance(failure, WorkspaceSecurityError):
            code = InteractiveErrorCode.WORKSPACE_UNSAFE
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

    def _write_session_resume_cancelled_status(self) -> None:
        """Acknowledge declined saved-session resume without mutation."""

        self._terminal.write_status(
            TerminalMessageKind.CANCELLED,
            "Session resume cancelled.",
        )

    def _write_session_end_cancelled_status(self) -> None:
        """Acknowledge declined session end while retaining its attachment."""

        self._terminal.write_status(
            TerminalMessageKind.CANCELLED,
            "Session end cancelled.",
        )

    def _write_lobby_orientation(self) -> None:
        """Render workspace-scoped lobby help without acquiring resources."""

        self._terminal.write_lobby_orientation(str(self._workspace.root))

    def _abandon_known_attachment_for_cleanup_abort(self) -> None:
        """Clear uncertain bookkeeping and abort into backend-close cleanup.

        No further explicit detach is attempted because a prior lifecycle
        failure made source/destination ownership ambiguous. Backend ``close``
        remains the best-effort owner cleanup operation.
        """

        self._attached_session_id = None
        self._selected_conversation = None
        raise _ControllerAbort(
            InteractiveError(InteractiveErrorCode.CLEANUP_FAILED)
        )

    def _release_attachment_and_close_backend(
        self,
        exit_code: int,
        *,
        cleanup_error_reported: bool,
    ) -> int:
        """Detach known state, close the backend, and apply cleanup precedence.

        Normal quit/EOF explicitly detaches without deactivation. Either
        detach or close failure forces status 2; backend close is still tried,
        and a single safe ``cleanup_failed`` record is added when needed.
        """

        if self._closed:
            return exit_code
        cleanup_failed = False
        attached_session = self._attached_session_id
        if attached_session is not None:
            try:
                self._backend.detach_session(attached_session)
            except Exception:
                cleanup_failed = True
            finally:
                self._attached_session_id = None
                self._selected_conversation = None
        try:
            self._backend.close()
        except Exception:
            cleanup_failed = True
        finally:
            self._closed = True

        if cleanup_failed and not cleanup_error_reported:
            self._terminal.write_error(
                InteractiveError(InteractiveErrorCode.CLEANUP_FAILED)
            )
        return 2 if cleanup_failed else exit_code


__all__ = ["InteractiveController"]
