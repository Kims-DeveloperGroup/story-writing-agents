"""Define live-composer input, delivery, and interruption contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass
from enum import StrEnum
from typing import TypeAlias

from ._model import LineInputState


class LiveOperationBoundary(StrEnum):
    """Authoritative durable boundary observed when live input is accepted."""

    PRECOMMIT = "precommit"
    POSTCOMMIT = "postcommit"


class LiveComposerInputKind(StrEnum):
    """Distinct terminal events accepted by a live composer."""

    SUBMIT = "submit"
    INTERRUPT = "interrupt"
    EOF = "eof"


class LiveComposerDeliveryKind(StrEnum):
    """Closed outcomes returned to the runtime-owned workflow."""

    OPERATION_COMPLETED = "operation_completed"
    REPLAN = "replan"
    NEXT_TURN = "next_turn"
    INTERRUPTED = "interrupted"
    EOF = "eof"
    RECOVERY_REQUIRED = "recovery_required"


class LiveComposerJoinStatus(StrEnum):
    """Whether precommit cancellation ended with a proved worker join."""

    JOINED = "joined"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class LiveComposerInput:
    """One exact immutable editor state and its terminal event."""

    kind: LiveComposerInputKind
    state: LineInputState

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LiveComposerInputKind):
            raise TypeError("kind must be a LiveComposerInputKind")
        if type(self.state) is not LineInputState:
            raise TypeError("state must be a LineInputState")

    @property
    def text(self) -> str | None:
        """Return literal submitted text without interpreting slash prefixes."""

        if self.kind is LiveComposerInputKind.SUBMIT:
            return self.state.buffer
        return None


@dataclass(frozen=True, slots=True)
class LiveComposerResult:
    """One terminal delivery with exact preserved editor state."""

    kind: LiveComposerDeliveryKind
    state: LineInputState
    _: KW_ONLY
    text: str | None = None
    operation_result: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LiveComposerDeliveryKind):
            raise TypeError("kind must be a LiveComposerDeliveryKind")
        if type(self.state) is not LineInputState:
            raise TypeError("state must be a LineInputState")
        if self.text is not None and type(self.text) is not str:
            raise TypeError("text must be a string or None")
        text_kinds = {
            LiveComposerDeliveryKind.REPLAN,
            LiveComposerDeliveryKind.NEXT_TURN,
        }
        if self.kind in text_kinds:
            if self.text != self.state.buffer:
                raise ValueError("delivered text must equal the exact editor buffer")
        elif self.text is not None:
            raise ValueError(f"{self.kind.value} does not carry submitted text")
        if (
            self.kind is not LiveComposerDeliveryKind.OPERATION_COMPLETED
            and self.operation_result is not None
        ):
            raise ValueError("only operation completion carries an operation result")


BoundaryProvider: TypeAlias = Callable[[], LiveOperationBoundary]
CancelAndJoin: TypeAlias = Callable[
    [Awaitable[object]], Awaitable[LiveComposerJoinStatus]
]


async def deliver_live_composer_input(
    accepted: LiveComposerInput,
    operation: Awaitable[object],
    boundary_provider: BoundaryProvider,
    cancel_and_join: CancelAndJoin,
) -> LiveComposerResult:
    """Resolve accepted input against runtime-owned durable authority.

    The caller supplies the authoritative boundary and owns cancellation.  This
    module neither infers durable completion nor cancels an operation directly.
    """

    if type(accepted) is not LiveComposerInput:
        raise TypeError("accepted must be a LiveComposerInput")
    if not hasattr(operation, "__await__"):
        raise TypeError("operation must be awaitable")
    if not callable(boundary_provider):
        raise TypeError("boundary_provider must be callable")
    if not callable(cancel_and_join):
        raise TypeError("cancel_and_join must be callable")
    boundary = boundary_provider()
    if not isinstance(boundary, LiveOperationBoundary):
        raise TypeError("boundary_provider must return a LiveOperationBoundary")

    if boundary is LiveOperationBoundary.POSTCOMMIT:
        if accepted.kind is LiveComposerInputKind.SUBMIT:
            return LiveComposerResult(
                LiveComposerDeliveryKind.NEXT_TURN,
                accepted.state,
                text=accepted.text,
            )
        return LiveComposerResult(
            _non_submit_delivery(accepted.kind),
            accepted.state,
        )

    try:
        join_status = await cancel_and_join(operation)
    except Exception:
        return LiveComposerResult(
            LiveComposerDeliveryKind.RECOVERY_REQUIRED,
            accepted.state,
        )
    if not isinstance(join_status, LiveComposerJoinStatus):
        return LiveComposerResult(
            LiveComposerDeliveryKind.RECOVERY_REQUIRED,
            accepted.state,
        )
    if join_status is LiveComposerJoinStatus.UNCERTAIN:
        return LiveComposerResult(
            LiveComposerDeliveryKind.RECOVERY_REQUIRED,
            accepted.state,
        )
    if accepted.kind is LiveComposerInputKind.SUBMIT:
        return LiveComposerResult(
            LiveComposerDeliveryKind.REPLAN,
            accepted.state,
            text=accepted.text,
        )
    return LiveComposerResult(
        _non_submit_delivery(accepted.kind),
        accepted.state,
    )


def _non_submit_delivery(kind: LiveComposerInputKind) -> LiveComposerDeliveryKind:
    if kind is LiveComposerInputKind.INTERRUPT:
        return LiveComposerDeliveryKind.INTERRUPTED
    if kind is LiveComposerInputKind.EOF:
        return LiveComposerDeliveryKind.EOF
    raise ValueError("submission requires an explicit delivery classification")


__all__ = [
    "BoundaryProvider",
    "CancelAndJoin",
    "LiveComposerDeliveryKind",
    "LiveComposerInput",
    "LiveComposerInputKind",
    "LiveComposerJoinStatus",
    "LiveComposerResult",
    "LiveOperationBoundary",
    "deliver_live_composer_input",
]
