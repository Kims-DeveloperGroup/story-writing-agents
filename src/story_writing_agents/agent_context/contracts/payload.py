"""The read-only session surface used by context payload renderers."""

from __future__ import annotations

from typing import Protocol

from ..actions import ContextSourceStatusReceipt
from ..manifest import ContextSourceKind, ContextSubjectKind
from .call_offers import ContextInvocationKind, ContextJsonObjectResultContract
from .planning_inputs import ContextMaterial


class ContextPayloadSession(Protocol):
    """Read the existing call-local offers without owning execution state."""

    @property
    def core_statuses(self) -> tuple[ContextSourceStatusReceipt, ...]:
        ...

    @property
    def allowed_search_source_kinds(self) -> tuple[ContextSourceKind, ...]:
        ...

    def action_limits(self) -> tuple[tuple[str, int], ...]:
        ...


class ContextPayloadRequest(Protocol):
    """Read the six immutable request facts needed by payload projections."""

    @property
    def invocation_kind(self) -> ContextInvocationKind | None:
        ...

    @property
    def result_contract(self) -> ContextJsonObjectResultContract:
        ...

    @property
    def subject_kind(self) -> ContextSubjectKind:
        ...

    @property
    def allowed_search_source_kinds(self) -> tuple[ContextSourceKind, ...]:
        ...

    @property
    def operation_id(self) -> str:
        ...

    @property
    def materials(self) -> tuple[ContextMaterial, ...]:
        ...


__all__ = ["ContextPayloadSession", "ContextPayloadRequest"]
