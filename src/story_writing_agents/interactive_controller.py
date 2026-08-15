"""Synchronous story-session lobby transitions for interactive CLI entry."""

from __future__ import annotations

from .agents import LEAD_ORCHESTRATOR
from .backend import AgentBackend
from .cli_context import CliContext, select_session
from .errors import (
    BackendProtocolError,
    ConfigurationError,
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
from .models import SessionLeaseState, SessionSummary
from .workspace import Workspace


_UNAVAILABLE_SUMMARY = "This command is not available in the session lobby."
_LOBBY_COMMAND_HINT = (
    "Use /help, /session list, /session new NAME, /session resume [NAME], "
    "/session end, or /quit."
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


class InteractiveController:
    """Own one interactive process's session attachment and lobby loop.

    Construction borrows collaborators without I/O. ``run_session_lobby`` is
    single-use; once it starts, this controller closes the backend on every
    exit and releases any attachment it can still identify safely.
    """

    __slots__ = (
        "_attached_session_id",
        "_backend",
        "_closed",
        "_context",
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
    ) -> None:
        """Bind validated collaborators without reading or mutating resources."""

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
        self._attached_session_id: str | None = None
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._started = False
        self._closed = False

    def run_session_lobby(
        self,
        *,
        requested_session: str | None = None,
    ) -> int:
        """Run one lobby/attachment lifecycle and return shell status 0 or 2.

        ``requested_session`` selects explicit create/attach/resume behavior;
        ``None`` uses saved selection. The method mutates only session lifecycle
        and CLI selection state, closes its borrowed backend on every started
        exit, and rejects a second run with ``RuntimeError``.
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
        if self._attached_session_id is None:
            self._attach_selected_session(summary.session_id, attachment)
            return
        self._switch_attached_session(summary.session_id, attachment)

    def _attach_selected_session(
        self,
        session_id: str,
        attachment: AttachmentKind,
    ) -> None:
        """Persist an initial destination and commit it as process attachment.

        Selection is written only after lease acquisition. A selection failure
        releases the destination before it is reported; failed release makes
        the remaining lease state fatal and delegates cleanup to backend close.
        """

        next_context = self._persist_destination_selection(session_id)
        if next_context is None:
            return
        self._commit_attached_session(next_context, session_id, attachment)

    def _switch_attached_session(
        self,
        destination_session: str,
        attachment: AttachmentKind,
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
    ) -> None:
        """Commit process state, reset routing to lead, and render orientation.

        The destination lease and selection are already durable. This method
        performs no backend lifecycle operation and deliberately omits history
        because conversation selection belongs to a later feature.
        """

        self._context = context
        self._attached_session_id = session_id
        self._target_agent_id = LEAD_ORCHESTRATOR
        self._terminal.write_session_orientation(
            str(self._workspace.root),
            session_id,
            attachment,
            self._target_agent_id,
            history_count=None,
        )

    def _read_session_lobby_commands(self) -> int:
        """Read and dispatch the session-only command loop until normal exit.

        Main Ctrl-C clears only the current input operation. Main EOF, quit, or
        EOF during end confirmation returns 0; fatal transition uncertainty is
        raised to the outer cleanup boundary.
        """

        while True:
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
            action = parse_interactive_line(terminal_action.text)
            if self._dispatch_session_lobby_action(action):
                return 0

    def _dispatch_session_lobby_action(self, action: InteractiveAction) -> bool:
        """Execute one parser action and return whether the loop should exit.

        Only help and story-session navigation mutate or inspect state in this
        leaf. All other valid actions receive a stable unavailable error, and
        parser errors may surface only their parser-owned safe suggestion.
        """

        if action.kind is ActionKind.IGNORED:
            return False
        if action.kind is ActionKind.HELP:
            self._terminal.write_help(action.argument)
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
            self._write_parser_error(action)
            return False
        self._terminal.write_error(
            InteractiveError(
                InteractiveErrorCode.COMMAND_INVALID,
                summary=_UNAVAILABLE_SUMMARY,
                hint=_LOBBY_COMMAND_HINT,
            )
        )
        return False

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

    def _write_parser_error(self, action: InteractiveAction) -> None:
        """Render one parser-selected command error and optional suggestion.

        Parser text is trusted only after ``PARSE_ERROR`` selection. No
        exception text is inspected, and the operation performs no mutation.
        """

        assert action.kind is ActionKind.PARSE_ERROR
        assert action.error is not None
        hint = (
            f"Try {action.suggestion}, or type /help."
            if action.suggestion is not None
            else None
        )
        self._terminal.write_error(
            InteractiveError(
                InteractiveErrorCode.COMMAND_INVALID,
                summary=action.error,
                hint=hint,
            )
        )

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
