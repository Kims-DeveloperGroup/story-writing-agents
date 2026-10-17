"""Call-local runtime coordination for canonical artifact references.

Parsing and provider projection happen once here.  The repository retains
filesystem, snapshot, authorization, revalidation, and durable-evidence
ownership; the backend retains provider-action enforcement.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TypeVar

from ..errors import SessionArtifactGuardFailure, WorkspaceSecurityError
from ..git.repository_lock import current_thread_token
from .authorization import (
    AuthorizedArtifactReference,
    ReferenceAuthorization,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationCorrelation,
    ReferenceOperationFailure,
    ReferenceOperationState,
    validate_reference_identity,
)
from .directives import parse_artifact_references
from .read_actions import (
    ReferenceReadAction,
    ReferenceReadInvocation,
    ReferenceReadResult,
)
from .references import (
    MAX_REFERENCE_COUNT,
    ArtifactReference,
    ArtifactReferenceBatch,
    ArtifactReferenceDirectiveError,
    coalesce_reference_aliases,
)
from .repository import WritingArtifactRepository


_INVOCATION_ID = re.compile(r"ri_[0-9a-f]{32}")
_T = TypeVar("_T")
def _parse_reference_aliases(locators: tuple[str, ...]) -> tuple[ArtifactReference, ...]:
    if type(locators) is not tuple:
        raise TypeError("reference alias locators must be a tuple")
    if len(locators) > MAX_REFERENCE_COUNT:
        raise ArtifactReferenceDirectiveError("reference input exceeds the directive limit")
    aliases: list[ArtifactReference] = []
    for locator in locators:
        if type(locator) is not str:
            raise TypeError("reference alias locators must contain strings")
        batch = parse_artifact_references(locator)
        if len(batch.references) != 1 or batch.references[0].token != locator:
            raise ArtifactReferenceDirectiveError("reference alias must be one exact directive")
        aliases.append(batch.references[0])
    return tuple(aliases)


def reference_alias_source_text(locators: tuple[str, ...]) -> str:
    """Return canonical directive text for a frozen vector of logical aliases."""

    batch, _ = coalesce_reference_aliases(_parse_reference_aliases(locators))
    return batch.source_text


def _operation_id(session_id: str) -> str:
    validate_reference_identity(session_id)
    if len(session_id) > 64:
        raise ValueError("session ID is too long for a reference operation")
    return f"rr_{len(session_id):02x}_{session_id}_{uuid.uuid4().hex}"


def reference_operation_belongs_to_session(
    operation_id: str,
    session_id: str,
) -> bool:
    """Return whether a content-free operation identity embeds this session."""

    try:
        validate_reference_identity(session_id)
        return reference_operation_session_id(operation_id) == session_id
    except (ReferenceAuthorizationError, ValueError):
        return False


def reference_operation_session_id(operation_id: str) -> str:
    """Decode the exact session from one canonical reference-operation ID."""

    try:
        validate_reference_identity(operation_id)
        if not operation_id.startswith("rr_"):
            raise ValueError
        encoded_length, remainder = operation_id[3:].split("_", 1)
        if len(encoded_length) != 2 or any(
            character not in "0123456789abcdef" for character in encoded_length
        ):
            raise ValueError
        session_length = int(encoded_length, 16)
        if not 1 <= session_length <= 64:
            raise ValueError
        session_id = remainder[:session_length]
        suffix = remainder[session_length:]
        if (
            encoded_length != f"{len(session_id):02x}"
            or len(suffix) != 33
            or suffix[0] != "_"
            or any(character not in "0123456789abcdef" for character in suffix[1:])
        ):
            raise ValueError
        validate_reference_identity(session_id)
    except (ReferenceAuthorizationError, TypeError, ValueError):
        raise ValueError("reference operation ID is invalid") from None
    return session_id


def _failure_code(
    failure: BaseException,
    default: ReferenceAuthorizationErrorCode,
) -> ReferenceAuthorizationErrorCode:
    if isinstance(failure, ReferenceOperationFailure):
        return failure.code
    if isinstance(failure, ReferenceAuthorizationError):
        return failure.code
    if isinstance(failure, asyncio.CancelledError):
        return ReferenceAuthorizationErrorCode.CANCELLED
    return default


def _close_descriptor(
    descriptor: int | None,
    *,
    failure: BaseException | None = None,
) -> None:
    if type(descriptor) is not int or descriptor < 0:
        return
    try:
        os.close(descriptor)
    except BaseException as cleanup_failure:
        if failure is not None:
            try:
                failure.add_note(
                    "physical workspace pin cleanup failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            except BaseException:
                pass
        elif not isinstance(cleanup_failure, OSError):
            raise


def _run_reference_cleanups(
    cleanups: tuple[Callable[[], object], ...],
    *,
    failure: BaseException | None,
) -> None:
    cleanup_failure: BaseException | None = None
    for cleanup in cleanups:
        try:
            cleanup()
        except BaseException as exc:
            primary = failure if failure is not None else cleanup_failure
            if primary is None:
                cleanup_failure = exc
            else:
                try:
                    primary.add_note(
                        "runtime reference cleanup failed: "
                        f"{type(exc).__name__}"
                    )
                except BaseException:
                    pass
    if cleanup_failure is not None:
        raise cleanup_failure


def _close_reference_authorization(
    authorization: ReferenceAuthorization,
    *,
    failure: BaseException,
) -> None:
    _run_reference_cleanups((authorization.close,), failure=failure)


def _validate_physical_workspace_pin(
    descriptor: int,
    identity: tuple[int, int],
) -> None:
    if type(descriptor) is not int or descriptor < 0:
        raise TypeError("physical workspace pin is invalid")
    try:
        os.set_inheritable(descriptor, False)
        observed = os.fstat(descriptor)
        inheritable = os.get_inheritable(descriptor)
    except OSError:
        raise WorkspaceSecurityError(
            "physical workspace pin is unavailable"
        ) from None
    if (
        not stat.S_ISDIR(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != identity
        or inheritable
    ):
        raise WorkspaceSecurityError("physical workspace pin identity changed")


class _TrackedReferenceReadInvocation:
    """Notify one operation when its repository coordinator is terminal."""

    __slots__ = (
        "_delegate",
        "_operation",
        "_results",
        "_results_lock",
        "_terminal",
        "request",
    )

    def __init__(
        self,
        operation: RuntimeReferenceOperation,
        delegate: ReferenceReadInvocation,
    ) -> None:
        self._operation = operation
        self._delegate = delegate
        self.request = delegate.request
        self._results: list[ReferenceReadResult] = []
        self._results_lock = threading.Lock()
        self._terminal = False

    def handle_reference_read_action(
        self,
        action: ReferenceReadAction,
    ) -> ReferenceReadResult:
        self._operation._require_operational_affinity()
        try:
            with self._operation._open_repository_access():
                result = self._delegate.handle_reference_read_action(action)
                with self._results_lock:
                    self._results.append(result)
                return result
        except BaseException as exc:
            if self._operation._preserves_guard_failure(exc):
                self._operation._defer_cleanup(failure=exc)
            else:
                self._mark_terminal()
            raise

    def record_result_received(self) -> None:
        self._operation._require_operational_affinity()
        try:
            with self._operation._open_repository_access():
                self._delegate.record_result_received()
        except BaseException as exc:
            if self._operation._preserves_guard_failure(exc):
                self._operation._defer_cleanup(failure=exc)
            raise

    def accept(self) -> None:
        self._operation._require_operational_affinity()
        preserve = False
        try:
            with self._operation._open_repository_access():
                self._delegate.accept()
        except BaseException as exc:
            preserve = self._operation._preserves_guard_failure(exc)
            if preserve:
                self._operation._defer_cleanup(failure=exc)
            raise
        finally:
            if not preserve:
                self._mark_terminal()

    def terminate(self, outcome: ReferenceAuthorizationErrorCode) -> None:
        self._operation._require_operational_affinity()
        if self._terminal:
            return
        preserve = False
        try:
            with self._operation._open_repository_access():
                self._delegate.terminate(outcome)
        except BaseException as exc:
            preserve = self._operation._preserves_guard_failure(exc)
            if preserve:
                self._operation._defer_cleanup(failure=exc)
            raise
        finally:
            if not preserve:
                self._mark_terminal()

    def _mark_terminal(self) -> None:
        if self._terminal:
            return
        self._close_transient_content()
        self._terminal = True
        self._operation._retire_invocation(self)

    def _close_transient_content(
        self,
        *,
        failure: BaseException | None = None,
    ) -> None:
        """Idempotently close every public result retained before guard exit."""

        with self._results_lock:
            results = tuple(self._results)
            self._results.clear()
        _run_reference_cleanups(
            tuple(result.close for result in results),
            failure=failure,
        )


class RuntimeReferenceOperation:
    """One parse-once projection and optional captured reference operation."""

    __slots__ = (
        "_authorization",
        "_cleanup_pending",
        "_creator_pid",
        "_creator_thread_token",
        "_finished",
        "_guarded",
        "_lock",
        "_next_ordinal",
        "_pending",
        "_physical_workspace_identity",
        "_physical_workspace_pin",
        "_repository",
        "_repository_access",
        "_provider_text",
        "_reference_proofs",
        "_source_text",
        "operation_id",
        "session_id",
    )

    def __init__(
        self,
        repository: WritingArtifactRepository,
        *,
        session_id: str,
        batch: ArtifactReferenceBatch | None,
        source_text: str,
        provider_text: str,
        operation_id: str | None,
        authorization: ReferenceAuthorization | None,
        physical_workspace_identity: tuple[int, int] | None = None,
        physical_workspace_pin: int | None = None,
    ) -> None:
        if type(repository) is not WritingArtifactRepository:
            raise TypeError("repository must be a WritingArtifactRepository")
        if batch is not None and type(batch) is not ArtifactReferenceBatch:
            raise TypeError("batch must be an ArtifactReferenceBatch or None")
        if not isinstance(source_text, str) or not isinstance(provider_text, str):
            raise TypeError("reference operation text projections must be strings")
        if batch is not None and (
            batch.source_text != source_text or batch.provider_text != provider_text
        ):
            raise ValueError("reference operation projections do not match its batch")
        if (operation_id is None) != (authorization is None):
            raise ValueError("reference operation identity and authorization differ")
        if bool(batch is not None and batch.references) != (authorization is not None):
            raise ValueError("reference operation capture does not match its batch")
        if physical_workspace_identity is not None and (
            type(physical_workspace_identity) is not tuple
            or len(physical_workspace_identity) != 2
            or any(
                type(value) is not int or value <= 0
                for value in physical_workspace_identity
            )
        ):
            raise TypeError("physical workspace identity is invalid")
        if (physical_workspace_identity is None) != (physical_workspace_pin is None):
            raise ValueError("physical workspace identity and pin differ")
        if physical_workspace_pin is not None:
            if type(physical_workspace_pin) is not int or physical_workspace_pin < 0:
                raise TypeError("physical workspace pin is invalid")
            try:
                pinned = os.fstat(physical_workspace_pin)
            except OSError:
                raise WorkspaceSecurityError(
                    "physical workspace pin is unavailable"
                ) from None
            if (
                not stat.S_ISDIR(pinned.st_mode)
                or (pinned.st_dev, pinned.st_ino) != physical_workspace_identity
            ):
                raise WorkspaceSecurityError(
                    "physical workspace pin identity changed"
                )
        self._creator_pid = os.getpid()
        self._creator_thread_token = current_thread_token()
        self._repository: WritingArtifactRepository | None = repository
        self._repository_access: (
            Callable[[], AbstractContextManager[WritingArtifactRepository]] | None
        ) = None
        self.session_id = session_id
        self._source_text = source_text
        self._provider_text = provider_text
        self.operation_id = operation_id
        self._authorization = authorization
        self._reference_proofs = (
            () if authorization is None else authorization.authorized_references
        )
        self._next_ordinal = 1
        self._pending: dict[str, _TrackedReferenceReadInvocation] = {}
        self._physical_workspace_identity = physical_workspace_identity
        self._physical_workspace_pin = physical_workspace_pin
        self._cleanup_pending = False
        self._finished = False
        self._guarded = False
        self._lock = threading.RLock()

    @property
    def source_text(self) -> str:
        return self._source_text

    @property
    def provider_text(self) -> str:
        return self._provider_text

    @property
    def has_references(self) -> bool:
        return self.operation_id is not None

    @property
    def reference_proofs(self) -> tuple[AuthorizedArtifactReference, ...]:
        """Return the exact ordered content-free capture proofs while open.

        This projection deliberately exposes neither captured bytes nor the
        repository-owned authorization capability.  Context orchestration can
        therefore reserve the same captured references without reaching into
        this operation's private lifecycle state.
        """

        self._require_process_affinity()
        return self._reference_proofs

    def project_alias_proofs(
        self,
        locators: tuple[str, ...],
    ) -> tuple[AuthorizedArtifactReference, ...]:
        """Return exact logical-alias proofs without granting another READ."""

        self._require_process_affinity()
        with self._lock:
            if self._finished or self._authorization is None:
                raise ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.CLOSED)
            return self._authorization.project_alias_proofs(_parse_reference_aliases(locators))

    @property
    def finished(self) -> bool:
        self._require_process_affinity()
        with self._lock:
            return self._finished

    def cleanup_is_pending(self) -> bool:
        """Return whether only content-free durable cleanup remains."""

        self._require_process_affinity()
        with self._lock:
            return self._cleanup_pending

    @property
    def physical_workspace_identity(self) -> tuple[int, int] | None:
        """Return the immutable content-free identity bound before publication."""

        self._require_process_affinity()
        return self._physical_workspace_identity

    def defer_if_workspace_replaced(
        self,
        current_identity: tuple[int, int],
    ) -> bool:
        """Erase transient authority when this process handle belongs elsewhere."""

        if (
            type(current_identity) is not tuple
            or len(current_identity) != 2
            or any(type(value) is not int or value <= 0 for value in current_identity)
        ):
            raise TypeError("physical workspace identity is invalid")
        self._require_process_affinity()
        with self._lock:
            if self._physical_workspace_identity == current_identity:
                return False
            if self._physical_workspace_identity is None:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
            failure: BaseException | None = None
            try:
                self._defer_cleanup_locked()
            except BaseException as exc:
                failure = exc
                raise
            finally:
                self._close_physical_workspace_pin_locked(failure=failure)
            return True

    def begin_invocation(
        self,
        *,
        agent_id: str,
        phase: str,
        round_number: int | None,
    ) -> ReferenceReadInvocation | None:
        """Allocate one fresh, ordinally contiguous provider-read coordinator."""

        if self.operation_id is None:
            return None
        self._require_operational_affinity()
        with self._lock:
            if self._finished or self._cleanup_pending:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CLOSED,
                )
            authorization = self._authorization
            if authorization is None:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CLOSED,
                )
            ordinal = self._next_ordinal
            invocation_id = f"ri_{uuid.uuid4().hex}"
            assert _INVOCATION_ID.fullmatch(invocation_id) is not None
            correlation = ReferenceInvocationCorrelation(
                operation_id=self._required_operation_id(),
                invocation_id=invocation_id,
                invocation_ordinal=ordinal,
                agent_id=agent_id,
                phase=phase,
                round_number=round_number,
                attempt=1,
            )
            tracked: _TrackedReferenceReadInvocation | None = None

            def publish(delegate: ReferenceReadInvocation) -> None:
                nonlocal tracked
                tracked = _TrackedReferenceReadInvocation(self, delegate)
                self._pending[invocation_id] = tracked
                self._next_ordinal += 1

            try:
                delegate, action_failure = self._repository_action(
                    lambda: self._required_repository().begin_runtime_reference_invocation(
                        authorization,
                        correlation,
                        on_created=publish,
                    )
                )
            except BaseException as exc:
                if self._preserves_guard_failure(exc):
                    self._defer_cleanup_locked(failure=exc)
                raise
            if action_failure is not None:
                code = _failure_code(
                    action_failure,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
                failure = self._fail_locked(code)
                raise failure from action_failure
            assert delegate is not None
            if tracked is None:
                failure = self._fail_locked(
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
                )
                raise failure
            return tracked

    def terminate_invocation(
        self,
        invocation: ReferenceReadInvocation | None,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> None:
        """Terminate an allocated coordinator if it has not become terminal."""

        if invocation is None:
            return
        self._require_operational_affinity()
        if type(outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("outcome must be a ReferenceAuthorizationErrorCode")
        if not isinstance(invocation, _TrackedReferenceReadInvocation):
            raise TypeError("invocation does not belong to this runtime operation")
        invocation.terminate(outcome)

    def finish(self) -> None:
        """Accept this operation once after all provider invocations completed."""

        if self.operation_id is None:
            with self._lock:
                self._close_physical_workspace_pin_locked()
            return
        self._require_operational_affinity()
        with self._lock:
            if self._finished:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CLOSED,
                )
            if self._cleanup_pending:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
            if self._pending:
                failure = self._fail_locked(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
                raise failure
            authorization = self._authorization
            if authorization is None:
                self._defer_cleanup_locked()
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CLOSED,
                )
            try:
                _, action_failure = self._repository_action(
                    lambda: self._required_repository().finish_reference_operation(
                        authorization,
                        outcome=None,
                    )
                )
            except BaseException as exc:
                if self._preserves_guard_failure(exc):
                    self._defer_cleanup_locked(failure=exc)
                raise
            if action_failure is not None:
                code = _failure_code(
                    action_failure,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
                if self._durably_terminal_locked():
                    self._mark_finished_locked()
                else:
                    self._defer_cleanup_locked()
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    code,
                ) from action_failure
            self._mark_finished_locked()

    def failure(
        self,
        failure: BaseException,
        *,
        default: ReferenceAuthorizationErrorCode = (
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        ),
    ) -> ReferenceOperationFailure | None:
        """Terminate unfinished state and return one safe structured failure."""

        if self.operation_id is None:
            with self._lock:
                self._close_physical_workspace_pin_locked()
            return None
        self._require_operational_affinity()
        preserve_guard_failure = self._preserves_guard_failure(failure)
        code = _failure_code(failure, default)
        with self._lock:
            if self._finished:
                if preserve_guard_failure:
                    raise failure
                return ReferenceOperationFailure(self._required_operation_id(), code)
            if self._cleanup_pending:
                if preserve_guard_failure:
                    raise failure
                return ReferenceOperationFailure(self._required_operation_id(), code)
            result = self._fail_locked(code)
            if preserve_guard_failure:
                raise failure
            return result

    def _fail_locked(
        self,
        code: ReferenceAuthorizationErrorCode,
    ) -> ReferenceOperationFailure:
        operation_id = self._required_operation_id()
        pending = tuple(self._pending.values())
        for invocation in pending:
            try:
                invocation.terminate(code)
            except BaseException as exc:
                if self._preserves_guard_failure(exc):
                    self._defer_cleanup_locked(failure=exc)
                    raise
                else:
                    code = _failure_code(exc, code)
        entered = False
        try:
            with self._open_repository_access():
                entered = True
                authorization = self._authorization
                if authorization is None:
                    raise ReferenceAuthorizationError(
                        ReferenceAuthorizationErrorCode.CLOSED
                    )
                self._required_repository().finish_reference_operation(
                    authorization,
                    outcome=code,
                )
        except BaseException as exc:
            if self._preserves_guard_failure(exc):
                self._defer_cleanup_locked(failure=exc)
                raise
            else:
                code = _failure_code(exc, code)
        if entered and self._durably_terminal_locked():
            self._mark_finished_locked()
        else:
            self._defer_cleanup_locked()
        return ReferenceOperationFailure(operation_id, code)

    def reconcile_cleanup(
        self,
        repository: WritingArtifactRepository,
        *,
        physical_workspace_identity: tuple[int, int],
    ) -> None:
        """Reconcile through fresh access without accepting its outer postcheck."""

        if type(repository) is not WritingArtifactRepository:
            raise TypeError("repository must be a WritingArtifactRepository")
        if (
            type(physical_workspace_identity) is not tuple
            or len(physical_workspace_identity) != 2
            or any(
                type(value) is not int or value <= 0
                for value in physical_workspace_identity
            )
        ):
            raise TypeError("physical workspace identity is invalid")
        if self.operation_id is None:
            return
        self._require_process_affinity()
        with self._lock:
            if self._finished:
                return
            if self._physical_workspace_identity != physical_workspace_identity:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
            self._reconcile_cleanup_locked(repository)

    def _reconcile_cleanup_locked(
        self,
        repository: WritingArtifactRepository,
    ) -> None:
        self._defer_cleanup_locked()
        terminal = repository.reconcile_reference_operation(
            self.session_id,
            self._required_operation_id(),
        )
        if not terminal:
            raise ReferenceOperationFailure(
                self._required_operation_id(),
                ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
            )

    def complete_reconciled_cleanup(self) -> None:
        """Mark cleanup complete after fresh guarded access passes postchecks."""

        self._require_process_affinity()
        with self._lock:
            if self._finished:
                return
            if not self._cleanup_pending:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION,
                )
            self._cleanup_pending = False
            self._mark_finished_locked()

    def _durably_terminal_locked(self) -> bool:
        try:
            history, failure = self._repository_action(
                lambda: self._required_repository().inspect_reference_operation(
                    self._required_operation_id()
                )
            )
        except BaseException as exc:
            if self._preserves_guard_failure(exc):
                self._defer_cleanup_locked(failure=exc)
            raise
        if failure is not None or history is None:
            return False
        return history.operation_events[-1].operation_state in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }

    def _defer_cleanup(
        self,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._require_process_affinity()
        with self._lock:
            self._defer_cleanup_locked(failure=failure)

    def _defer_cleanup_locked(
        self,
        *,
        failure: BaseException | None = None,
    ) -> None:
        if self._finished or self.operation_id is None:
            return
        pending = tuple(self._pending.values())
        self._pending.clear()
        authorization = self._authorization
        self._authorization = None
        self._repository = None
        self._repository_access = None
        self._cleanup_pending = True
        cleanups = tuple(
            lambda invocation=invocation: invocation._close_transient_content()
            for invocation in pending
        )
        if authorization is not None:
            cleanups += (authorization.close,)
        _run_reference_cleanups(cleanups, failure=failure)

    def _abandon_unretained(
        self,
        *,
        failure: BaseException | None = None,
    ) -> None:
        """Close content and the root pin when no registry owns this operation."""

        self._require_process_affinity()
        with self._lock:
            cleanup_failure: BaseException | None = None
            try:
                self._defer_cleanup_locked(failure=failure)
            except BaseException as exc:
                if failure is not None:
                    try:
                        failure.add_note(
                            "reference cleanup failed: "
                            f"{type(exc).__name__}"
                        )
                    except BaseException:
                        pass
                else:
                    cleanup_failure = exc
            self._close_physical_workspace_pin_locked(
                failure=failure or cleanup_failure,
            )
            if failure is None and cleanup_failure is not None:
                raise cleanup_failure

    def _mark_finished_locked(self) -> None:
        self._finished = True
        self._close_physical_workspace_pin_locked()

    def _close_physical_workspace_pin_locked(
        self,
        *,
        failure: BaseException | None = None,
    ) -> None:
        descriptor = self._physical_workspace_pin
        if descriptor is None:
            return
        self._physical_workspace_pin = None
        _close_descriptor(descriptor, failure=failure)

    def _retire_invocation(
        self,
        invocation: _TrackedReferenceReadInvocation,
    ) -> None:
        invocation_id = invocation.request.correlation.invocation_id
        with self._lock:
            current = self._pending.get(invocation_id)
            if current is invocation:
                self._pending.pop(invocation_id)

    def _required_operation_id(self) -> str:
        assert self.operation_id is not None
        return self.operation_id

    def _belongs_to_current_process(self) -> bool:
        return self._creator_pid == os.getpid()

    def _require_process_affinity(self) -> None:
        if self.operation_id is not None and not self._belongs_to_current_process():
            raise ReferenceOperationFailure(
                self._required_operation_id(),
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            )

    def _require_operational_affinity(self) -> None:
        self._require_process_affinity()
        if (
            self.operation_id is not None
            and self._creator_thread_token is not current_thread_token()
        ):
            raise ReferenceOperationFailure(
                self._required_operation_id(),
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            )

    def _required_repository(self) -> WritingArtifactRepository:
        repository = self._repository
        if repository is None:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CLOSED
            )
        return repository

    def _repository_action(
        self,
        action: Callable[[], _T],
    ) -> tuple[_T | None, BaseException | None]:
        """Run one repository action while preserving guard failures verbatim."""

        with self._open_repository_access():
            try:
                return action(), None
            except BaseException as exc:
                if self._preserves_guard_failure(exc):
                    raise
                return None, exc

    def _preserves_guard_failure(self, failure: BaseException) -> bool:
        """Return whether guarded access requires verbatim failure propagation."""

        return self._guarded and isinstance(
            failure,
            (SessionArtifactGuardFailure, WorkspaceSecurityError),
        )

    @contextmanager
    def _open_repository_access(self) -> Iterator[WritingArtifactRepository]:
        self._require_operational_affinity()
        access = self._repository_access
        if access is None:
            yield self._required_repository()
            return
        with access() as repository:
            if repository is not self._repository:
                raise TypeError("repository access changed its repository owner")
            yield repository


def prepare_runtime_reference_operation(
    repository: WritingArtifactRepository,
    *,
    session_id: str,
    text: str,
) -> RuntimeReferenceOperation:
    """Parse once and capture once only when the current input has references."""

    if type(repository) is not WritingArtifactRepository:
        raise TypeError("repository must be a WritingArtifactRepository")
    return _prepare_runtime_reference_operation(
        repository,
        session_id=session_id,
        text=text,
        repository_access=None,
    )


def prepare_guarded_runtime_reference_operation(
    repository_access: Callable[
        [], AbstractContextManager[WritingArtifactRepository]
    ],
    *,
    session_id: str,
    text: str,
    physical_workspace_identity: tuple[int, int],
    physical_workspace_pin_factory: Callable[[], int],
) -> RuntimeReferenceOperation:
    """Prepare a package-internal operation with short guarded repository calls."""

    if not callable(repository_access):
        raise TypeError("repository_access must be callable")
    if not callable(physical_workspace_pin_factory):
        raise TypeError("physical workspace pin factory must be callable")
    operation: RuntimeReferenceOperation | None = None
    try:
        with repository_access() as repository:
            if type(repository) is not WritingArtifactRepository:
                raise TypeError(
                    "repository access must yield a WritingArtifactRepository"
                )
            operation = _prepare_runtime_reference_operation(
                repository,
                session_id=session_id,
                text=text,
                repository_access=repository_access,
                operation_created=None,
                physical_workspace_identity=physical_workspace_identity,
                physical_workspace_pin_factory=physical_workspace_pin_factory,
            )
            return operation
    except BaseException as failure:
        if operation is not None:
            try:
                operation.failure(failure)
            except BaseException as cleanup_failure:
                failure.add_note(
                    "reference authorization cleanup failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            finally:
                operation._abandon_unretained()
        raise


def prepare_registered_guarded_runtime_reference_operation(
    repository_access: Callable[
        [], AbstractContextManager[WritingArtifactRepository]
    ],
    *,
    session_id: str,
    text: str,
    physical_workspace_identity: tuple[int, int],
    physical_workspace_pin_factory: Callable[[], int],
    register: Callable[[RuntimeReferenceOperation], None],
) -> RuntimeReferenceOperation:
    """Publish captured authority to its runtime owner before guard exit."""

    if not callable(repository_access):
        raise TypeError("repository_access must be callable")
    if not callable(register):
        raise TypeError("register must be callable")
    if not callable(physical_workspace_pin_factory):
        raise TypeError("physical workspace pin factory must be callable")
    operation: RuntimeReferenceOperation | None = None
    published = False

    def publish(created: RuntimeReferenceOperation) -> None:
        nonlocal operation, published
        operation = created
        register(created)
        published = True

    try:
        with repository_access() as repository:
            if type(repository) is not WritingArtifactRepository:
                raise TypeError(
                    "repository access must yield a WritingArtifactRepository"
                )
            prepared = _prepare_runtime_reference_operation(
                repository,
                session_id=session_id,
                text=text,
                repository_access=repository_access,
                operation_created=publish,
                physical_workspace_identity=physical_workspace_identity,
                physical_workspace_pin_factory=physical_workspace_pin_factory,
            )
            if prepared.has_references and (
                operation is not prepared or not published
            ):
                prepared._abandon_unretained()
                raise ReferenceOperationFailure(
                    prepared._required_operation_id(),
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
            return prepared
    except BaseException as failure:
        if operation is not None:
            try:
                operation._defer_cleanup(failure=failure)
            except BaseException as cleanup_failure:
                try:
                    failure.add_note(
                        "reference cleanup failed: "
                        f"{type(cleanup_failure).__name__}"
                    )
                except BaseException:
                    pass
            if not published:
                operation._abandon_unretained(failure=failure)
        raise


def _prepare_runtime_reference_operation(
    repository: WritingArtifactRepository,
    *,
    session_id: str,
    text: str,
    repository_access: Callable[
        [], AbstractContextManager[WritingArtifactRepository]
    ] | None,
    operation_created: Callable[[RuntimeReferenceOperation], None] | None = None,
    physical_workspace_identity: tuple[int, int] | None = None,
    physical_workspace_pin_factory: Callable[[], int] | None = None,
) -> RuntimeReferenceOperation:
    """Implement public and package-internal reference preparation."""

    if not isinstance(text, str):
        raise TypeError("reference operation text must be a string")
    if (physical_workspace_identity is None) != (
        physical_workspace_pin_factory is None
    ):
        raise ValueError("physical workspace identity and pin factory differ")

    physical_workspace_pin: int | None = None

    def create_operation(
        *,
        batch: ArtifactReferenceBatch | None,
        source_text: str,
        provider_text: str,
        operation_id: str | None,
        authorization: ReferenceAuthorization | None,
    ) -> RuntimeReferenceOperation:
        nonlocal physical_workspace_pin
        try:
            operation = RuntimeReferenceOperation(
                repository,
                session_id=session_id,
                batch=batch,
                source_text=source_text,
                provider_text=provider_text,
                operation_id=operation_id,
                authorization=authorization,
                physical_workspace_identity=physical_workspace_identity,
                physical_workspace_pin=physical_workspace_pin,
            )
        except BaseException:
            raise
        physical_workspace_pin = None
        return operation

    if not any(marker in text for marker in ("${", "$$", "\\$")):
        operation = RuntimeReferenceOperation(
            repository,
            session_id=session_id,
            batch=None,
            source_text=text,
            provider_text=text,
            operation_id=None,
            authorization=None,
        )
        operation._repository_access = repository_access
        operation._guarded = repository_access is not None
        return operation
    batch = parse_artifact_references(text)
    if not batch.references:
        operation = RuntimeReferenceOperation(
            repository,
            session_id=session_id,
            batch=batch,
            source_text=batch.source_text,
            provider_text=batch.provider_text,
            operation_id=None,
            authorization=None,
        )
        operation._repository_access = repository_access
        operation._guarded = repository_access is not None
        return operation
    if physical_workspace_pin_factory is not None:
        assert physical_workspace_identity is not None
        try:
            physical_workspace_pin = physical_workspace_pin_factory()
            _validate_physical_workspace_pin(
                physical_workspace_pin,
                physical_workspace_identity,
            )
        except BaseException as failure:
            closing_pin = physical_workspace_pin
            physical_workspace_pin = None
            _close_descriptor(closing_pin, failure=failure)
            raise
    try:
        operation_id = _operation_id(session_id)
    except BaseException as failure:
        closing_pin = physical_workspace_pin
        physical_workspace_pin = None
        _close_descriptor(closing_pin, failure=failure)
        raise
    operation: RuntimeReferenceOperation | None = None
    post_capture_failure: BaseException | None = None
    publication_failure_signal = RuntimeError(
        "runtime reference publication failed"
    )

    def terminalize_unadopted(
        authorization: ReferenceAuthorization,
        failure: BaseException,
    ) -> None:
        try:
            repository.finish_reference_operation(
                authorization,
                outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
            )
        except BaseException as cleanup_failure:
            try:
                failure.add_note(
                    "reference authorization cleanup failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            except BaseException:
                pass
        _close_reference_authorization(authorization, failure=failure)

    def publish(authorization: ReferenceAuthorization) -> None:
        nonlocal operation, post_capture_failure
        try:
            operation = create_operation(
                batch=batch,
                source_text=batch.source_text,
                provider_text=batch.provider_text,
                operation_id=operation_id,
                authorization=authorization,
            )
        except BaseException as failure:
            post_capture_failure = failure
            _close_reference_authorization(authorization, failure=failure)
            raise publication_failure_signal from None
        operation._repository_access = repository_access
        operation._guarded = repository_access is not None
        if operation_created is not None:
            try:
                operation_created(operation)
            except BaseException as failure:
                post_capture_failure = failure
                _close_reference_authorization(authorization, failure=failure)
                raise publication_failure_signal from None

    escaping_failure: BaseException | None = None
    try:
        try:
            if operation_created is None:
                authorization = repository.capture_reference_authorization(
                    operation_id=operation_id,
                    batch=batch,
                )
            else:
                authorization = repository.capture_runtime_reference_authorization(
                    operation_id=operation_id,
                    batch=batch,
                    on_created=publish,
                )
        except ReferenceAuthorizationError as exc:
            raise ReferenceOperationFailure(operation_id, exc.code) from exc
        except BaseException as exc:
            if exc is publication_failure_signal:
                assert post_capture_failure is not None
                raise post_capture_failure.with_traceback(
                    post_capture_failure.__traceback__
                ) from None
            if repository_access is not None and isinstance(
                exc,
                (SessionArtifactGuardFailure, WorkspaceSecurityError),
            ):
                raise
            raise ReferenceOperationFailure(
                operation_id,
                ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
            ) from exc
        if authorization is None:
            raise ReferenceOperationFailure(
                operation_id,
                ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
            )
        if operation is None:
            try:
                operation = create_operation(
                    batch=batch,
                    source_text=batch.source_text,
                    provider_text=batch.provider_text,
                    operation_id=operation_id,
                    authorization=authorization,
                )
            except BaseException as failure:
                terminalize_unadopted(authorization, failure)
                raise
            operation._repository_access = repository_access
            operation._guarded = repository_access is not None
        return operation
    except BaseException as failure:
        escaping_failure = failure
        raise
    finally:
        closing_pin = physical_workspace_pin
        physical_workspace_pin = None
        _close_descriptor(closing_pin, failure=escaping_failure)
