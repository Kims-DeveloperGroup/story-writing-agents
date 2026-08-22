"""Provider-neutral contracts for one mandatory artifact-read prelude."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, Protocol

from .authorization import (
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationCorrelation,
)
from .references import (
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
)


class ReferenceReadActionKind(StrEnum):
    """The only provider action allowed by an artifact-read prelude."""

    READ_WRITING_ARTIFACTS = "read_writing_artifacts"


def _validate_references(
    references: tuple[ArtifactReference, ...],
) -> tuple[ArtifactReference, ...]:
    """Validate one nonempty ordered reference batch without reading content."""

    if type(references) is not tuple or not references:
        raise ValueError("reference read must contain an ArtifactReference tuple")
    if any(type(item) is not ArtifactReference for item in references):
        raise TypeError("reference read must contain ArtifactReference values")
    ArtifactReferenceBatch("", "", references)
    return references


@dataclass(frozen=True, slots=True)
class ReferenceReadRequest:
    """Content-free identity and exact ordered batch exposed to a provider."""

    correlation: ReferenceInvocationCorrelation
    references: tuple[ArtifactReference, ...]

    def __post_init__(self) -> None:
        if type(self.correlation) is not ReferenceInvocationCorrelation:
            raise TypeError("correlation must be a ReferenceInvocationCorrelation")
        _validate_references(self.references)


@dataclass(frozen=True, slots=True)
class ReferenceReadAction:
    """One provider-requested read of the complete authorized batch."""

    kind: ReferenceReadActionKind
    correlation: ReferenceInvocationCorrelation
    references: tuple[ArtifactReference, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not ReferenceReadActionKind:
            raise TypeError("reference read action kind is invalid")
        if self.kind is not ReferenceReadActionKind.READ_WRITING_ARTIFACTS:
            raise ValueError("reference read action kind is unsupported")
        if type(self.correlation) is not ReferenceInvocationCorrelation:
            raise TypeError("correlation must be a ReferenceInvocationCorrelation")
        _validate_references(self.references)


class ReferenceReadResult:
    """Closeable, nonserializable carrier for one transient ordered read."""

    __slots__ = ("_contents", "_request")

    def __init__(
        self,
        request: ReferenceReadRequest,
        contents: tuple[bytes, ...],
    ) -> None:
        if type(request) is not ReferenceReadRequest:
            raise TypeError("request must be a ReferenceReadRequest")
        if type(contents) is not tuple or len(contents) != len(request.references):
            raise ValueError("reference read contents do not match the request")
        if any(type(item) is not bytes for item in contents):
            raise TypeError("reference read contents must contain bytes")
        self._request = request
        self._contents = contents

    @property
    def request(self) -> ReferenceReadRequest:
        """Return the immutable content-free identity for this result."""

        return self._request

    @property
    def contents(self) -> tuple[bytes, ...]:
        """Return transient selected bytes until this result is closed."""

        if not self._contents and self.request.references:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CLOSED
            )
        return self._contents

    def close(self) -> None:
        """Release this result's references to selected artifact bytes."""

        self._contents = ()

    def __enter__(self) -> ReferenceReadResult:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __repr__(self) -> str:
        return "ReferenceReadResult(<content hidden>)"

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        raise TypeError("ReferenceReadResult cannot be serialized")


class ReferenceReadInvocation(Protocol):
    """Synchronous runtime coordinator for one authorized read invocation."""

    request: ReferenceReadRequest

    def handle_reference_read_action(
        self,
        action: ReferenceReadAction,
    ) -> ReferenceReadResult:
        """Consume the exact provider action and return transient selected bytes."""

    def record_result_received(self) -> None:
        """Record that the substantive provider result was received."""

    def accept(self) -> None:
        """Revalidate and accept the completed invocation."""

    def terminate(self, outcome: ReferenceAuthorizationErrorCode) -> None:
        """Terminate the invocation with one content-free outcome."""
