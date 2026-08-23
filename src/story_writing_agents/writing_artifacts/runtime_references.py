"""Call-local runtime coordination for canonical artifact references.

Parsing and provider projection happen once here.  The repository retains
filesystem, snapshot, authorization, revalidation, and durable-evidence
ownership; the backend retains provider-action enforcement.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid

from .authorization import (
    ReferenceAuthorization,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationCorrelation,
    ReferenceOperationFailure,
    validate_reference_identity,
)
from .directives import parse_artifact_references
from .read_actions import (
    ReferenceReadAction,
    ReferenceReadInvocation,
    ReferenceReadRequest,
    ReferenceReadResult,
)
from .references import ArtifactReferenceBatch
from .repository import WritingArtifactRepository


_INVOCATION_ID = re.compile(r"ri_[0-9a-f]{32}")


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
        validate_reference_identity(operation_id)
        validate_reference_identity(session_id)
    except ReferenceAuthorizationError:
        return False
    if len(session_id) > 64:
        return False
    prefix = f"rr_{len(session_id):02x}_{session_id}_"
    suffix = operation_id[len(prefix):] if operation_id.startswith(prefix) else ""
    return len(suffix) == 32 and all(
        character in "0123456789abcdef" for character in suffix
    )


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


class _TrackedReferenceReadInvocation:
    """Notify one operation when its repository coordinator is terminal."""

    __slots__ = ("_delegate", "_operation", "_terminal", "request")

    def __init__(
        self,
        operation: RuntimeReferenceOperation,
        delegate: ReferenceReadInvocation,
    ) -> None:
        self._operation = operation
        self._delegate = delegate
        self.request = delegate.request
        self._terminal = False

    def handle_reference_read_action(
        self,
        action: ReferenceReadAction,
    ) -> ReferenceReadResult:
        try:
            return self._delegate.handle_reference_read_action(action)
        except BaseException:
            self._mark_terminal()
            raise

    def record_result_received(self) -> None:
        self._delegate.record_result_received()

    def accept(self) -> None:
        try:
            self._delegate.accept()
        finally:
            self._mark_terminal()

    def terminate(self, outcome: ReferenceAuthorizationErrorCode) -> None:
        if self._terminal:
            return
        try:
            self._delegate.terminate(outcome)
        finally:
            self._mark_terminal()

    def _mark_terminal(self) -> None:
        if self._terminal:
            return
        self._terminal = True
        self._operation._retire_invocation(self)


class RuntimeReferenceOperation:
    """One parse-once projection and optional captured reference operation."""

    __slots__ = (
        "_authorization",
        "_finished",
        "_lock",
        "_next_ordinal",
        "_pending",
        "_repository",
        "_provider_text",
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
        self._repository = repository
        self.session_id = session_id
        self._source_text = source_text
        self._provider_text = provider_text
        self.operation_id = operation_id
        self._authorization = authorization
        self._next_ordinal = 1
        self._pending: dict[str, _TrackedReferenceReadInvocation] = {}
        self._finished = False
        self._lock = threading.RLock()

    @property
    def source_text(self) -> str:
        return self._source_text

    @property
    def provider_text(self) -> str:
        return self._provider_text

    @property
    def has_references(self) -> bool:
        return self._authorization is not None

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    def begin_invocation(
        self,
        *,
        agent_id: str,
        phase: str,
        round_number: int | None,
    ) -> ReferenceReadInvocation | None:
        """Allocate one fresh, ordinally contiguous provider-read coordinator."""

        authorization = self._authorization
        if authorization is None:
            return None
        with self._lock:
            if self._finished:
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
            try:
                delegate = self._repository.begin_reference_invocation(
                    authorization,
                    correlation,
                )
            except BaseException as exc:
                code = _failure_code(
                    exc,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
                failure = self._fail_locked(code)
                raise failure from exc
            tracked = _TrackedReferenceReadInvocation(self, delegate)
            self._pending[invocation_id] = tracked
            self._next_ordinal += 1
            return tracked

    def terminate_invocation(
        self,
        invocation: ReferenceReadInvocation | None,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> None:
        """Terminate an allocated coordinator if it has not become terminal."""

        if invocation is None:
            return
        if type(outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("outcome must be a ReferenceAuthorizationErrorCode")
        if not isinstance(invocation, _TrackedReferenceReadInvocation):
            raise TypeError("invocation does not belong to this runtime operation")
        invocation.terminate(outcome)

    def finish(self) -> None:
        """Accept this operation once after all provider invocations completed."""

        if self._authorization is None:
            return
        with self._lock:
            if self._finished:
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    ReferenceAuthorizationErrorCode.CLOSED,
                )
            if self._pending:
                failure = self._fail_locked(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
                raise failure
            try:
                self._repository.finish_reference_operation(
                    self._authorization,
                    outcome=None,
                )
            except BaseException as exc:
                self._finished = True
                code = _failure_code(
                    exc,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
                raise ReferenceOperationFailure(
                    self._required_operation_id(),
                    code,
                ) from exc
            self._finished = True

    def failure(
        self,
        failure: BaseException,
        *,
        default: ReferenceAuthorizationErrorCode = (
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        ),
    ) -> ReferenceOperationFailure | None:
        """Terminate unfinished state and return one safe structured failure."""

        if self._authorization is None:
            return None
        code = _failure_code(failure, default)
        with self._lock:
            if self._finished:
                return ReferenceOperationFailure(self._required_operation_id(), code)
            return self._fail_locked(code)

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
                code = _failure_code(exc, code)
        try:
            self._repository.finish_reference_operation(
                self._authorization,
                outcome=code,
            )
        except BaseException as exc:
            code = _failure_code(exc, code)
        self._finished = True
        return ReferenceOperationFailure(operation_id, code)

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


def prepare_runtime_reference_operation(
    repository: WritingArtifactRepository,
    *,
    session_id: str,
    text: str,
) -> RuntimeReferenceOperation:
    """Parse once and capture once only when the current input has references."""

    if type(repository) is not WritingArtifactRepository:
        raise TypeError("repository must be a WritingArtifactRepository")
    if not isinstance(text, str):
        raise TypeError("reference operation text must be a string")
    if not any(marker in text for marker in ("${", "$$", "\\$")):
        return RuntimeReferenceOperation(
            repository,
            session_id=session_id,
            batch=None,
            source_text=text,
            provider_text=text,
            operation_id=None,
            authorization=None,
        )
    batch = parse_artifact_references(text)
    if not batch.references:
        return RuntimeReferenceOperation(
            repository,
            session_id=session_id,
            batch=batch,
            source_text=batch.source_text,
            provider_text=batch.provider_text,
            operation_id=None,
            authorization=None,
        )
    operation_id = _operation_id(session_id)
    try:
        authorization = repository.capture_reference_authorization(
            operation_id=operation_id,
            batch=batch,
        )
    except ReferenceAuthorizationError as exc:
        raise ReferenceOperationFailure(operation_id, exc.code) from exc
    except BaseException as exc:
        raise ReferenceOperationFailure(
            operation_id,
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        ) from exc
    if authorization is None:
        raise ReferenceOperationFailure(
            operation_id,
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )
    return RuntimeReferenceOperation(
        repository,
        session_id=session_id,
        batch=batch,
        source_text=batch.source_text,
        provider_text=batch.provider_text,
        operation_id=operation_id,
        authorization=authorization,
    )
