"""Immutable execution progress events and native status values."""

from __future__ import annotations

from ...lead_schema_groups import LeadGroupPhase
from ...provider_adapter import NativeContextCheck
from ..manifest import (
    ContextScope,
    ContextSubjectKind,
)
from .context_values import safe_identity
from .errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from collections.abc import Callable
from dataclasses import (
    dataclass,
    field,
)
from enum import StrEnum
import inspect


class ContextExecutionEventKind(StrEnum):
    """Content-free lifecycle facts emitted by contextual execution."""

    BOOTSTRAP = "bootstrap"
    DELTA = "delta"
    OMISSION = "omission"
    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    READ_STARTED = "read_started"
    READ_COMPLETED = "read_completed"
    ANSWER_AUTHORIZED = "answer_authorized"
    INVALIDATED = "invalidated"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ACTION_EXHAUSTED = "action_exhausted"
    TAINTED = "tainted"
    CHECKPOINT_ADVANCED = "checkpoint_advanced"
    CHECKPOINT_NOT_ADVANCED = "checkpoint_not_advanced"
    NATIVE_CONTEXT = "native_context"


class ContextNativeDisposition(StrEnum):
    OBSERVED = "observed"
    RESPONSE_REJECTED = "response_rejected"
    CONTROL_ACCEPTED = "control_accepted"
    RESULT_ACCEPTED = "result_accepted"
    RETRY_NOT_STARTED = "retry_not_started"
    RETRY_STARTED = "retry_started"
    RETRY_SUCCEEDED = "retry_succeeded"
    RETRY_FAILED = "retry_failed"


@dataclass(frozen=True, slots=True)
class ContextExecutionEvent:
    """One typed lifecycle event containing exact content-free facts only."""

    kind: ContextExecutionEventKind
    scope: ContextScope
    operation_id: str
    phase: str | None = None
    round_number: int | None = None
    action_index: int | None = None
    selected_source_count: int | None = None
    selected_source_utf8_bytes: int | None = None
    omitted_searchable_source_count: int | None = None
    omitted_searchable_source_utf8_bytes: int | None = None
    search_hit_count: int | None = None
    retrieved_source_count: int | None = None
    retrieved_source_utf8_bytes: int | None = None
    mandatory_reference_count: int | None = None
    mandatory_reference_utf8_bytes: int | None = None
    provider_input_tokens: int | None = None
    history_record_count: int | None = None
    observation_utf8_bytes: int | None = None
    truncated: bool | None = None
    native_context_check: NativeContextCheck | None = field(default=None, kw_only=True)
    native_disposition: ContextNativeDisposition | None = field(default=None, kw_only=True)
    group_phase: LeadGroupPhase | None = field(default=None, kw_only=True)
    attempt_id: str | None = field(default=None, kw_only=True)
    invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ContextExecutionEventKind
            or type(self.scope) is not ContextScope
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        safe_identity(self.operation_id)
        if self.scope.subject_kind is ContextSubjectKind.DEBATE:
            if self.phase is None or self.round_number is None:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            safe_identity(self.phase)
            if type(self.round_number) is not int or self.round_number < 0:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif self.phase is not None or self.round_number is not None:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.action_index is not None and (
            type(self.action_index) is not int or self.action_index < 1
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for value in (
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.omitted_searchable_source_count,
            self.omitted_searchable_source_utf8_bytes,
            self.search_hit_count,
            self.retrieved_source_count,
            self.retrieved_source_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
            self.provider_input_tokens,
            self.history_record_count,
            self.observation_utf8_bytes,
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.truncated is not None and type(self.truncated) is not bool:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        plan_facts = (
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.omitted_searchable_source_count,
            self.omitted_searchable_source_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
        )
        read_facts = (
            self.retrieved_source_count,
            self.retrieved_source_utf8_bytes,
            self.history_record_count,
            self.observation_utf8_bytes,
        )
        all_optional_facts = (
            *plan_facts,
            self.search_hit_count,
            *read_facts,
            self.provider_input_tokens,
            self.truncated,
        )
        native_facts = (self.native_context_check, self.native_disposition,
                        self.group_phase, self.attempt_id, self.invocation_id)
        if self.kind is ContextExecutionEventKind.NATIVE_CONTEXT:
            if (type(self.native_context_check) is not NativeContextCheck
                    or type(self.native_disposition) is not ContextNativeDisposition
                    or self.group_phase is not None and type(self.group_phase) is not LeadGroupPhase
                    or any(value is not None for value in all_optional_facts)):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            for value in (self.attempt_id, self.invocation_id):
                if value is not None:
                    safe_identity(value)
            return
        if any(value is not None for value in native_facts):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.kind in {
            ContextExecutionEventKind.BOOTSTRAP,
            ContextExecutionEventKind.DELTA,
        }:
            if any(value is None for value in plan_facts) or any(
                value is not None
                for value in (
                    self.action_index,
                    self.search_hit_count,
                    *read_facts,
                    self.provider_input_tokens,
                    self.truncated,
                )
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.OMISSION:
            if (
                self.omitted_searchable_source_count is None
                or self.omitted_searchable_source_count < 1
                or self.omitted_searchable_source_utf8_bytes is None
                or any(
                    value is not None
                    for value in (
                        self.action_index,
                        self.selected_source_count,
                        self.selected_source_utf8_bytes,
                        self.search_hit_count,
                        *read_facts,
                        self.mandatory_reference_count,
                        self.mandatory_reference_utf8_bytes,
                        self.provider_input_tokens,
                        self.truncated,
                    )
                )
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind in {
            ContextExecutionEventKind.SEARCH_STARTED,
            ContextExecutionEventKind.READ_STARTED,
            ContextExecutionEventKind.ANSWER_AUTHORIZED,
        }:
            if self.action_index is None or any(
                value is not None for value in all_optional_facts
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.SEARCH_COMPLETED:
            if (
                self.action_index is None
                or self.search_hit_count is None
                or self.observation_utf8_bytes is None
                or self.truncated is None
                or any(
                    value is not None
                    for value in (
                        *plan_facts,
                        self.retrieved_source_count,
                        self.retrieved_source_utf8_bytes,
                        self.provider_input_tokens,
                        self.history_record_count,
                    )
                )
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.READ_COMPLETED:
            if (
                self.action_index is None
                or any(value is None for value in read_facts)
                or any(
                    value is not None
                    for value in (
                        *plan_facts,
                        self.search_hit_count,
                        self.provider_input_tokens,
                        self.truncated,
                    )
                )
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind in {
            ContextExecutionEventKind.CHECKPOINT_ADVANCED,
            ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
        }:
            if self.action_index is not None or any(
                value is not None
                for value in (
                    *plan_facts,
                    self.search_hit_count,
                    *read_facts,
                    self.truncated,
                )
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.action_index is not None or any(
            value is not None for value in all_optional_facts
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


ContextExecutionEventSink = Callable[[ContextExecutionEvent], None]


__all__ = [
    'ContextExecutionEventKind',
    'ContextNativeDisposition',
    'ContextExecutionEvent',
    'ContextExecutionEventSink',
]


def validate_context_event_sink(
    sink: Callable[[ContextExecutionEvent], None] | None,
) -> None:
    if sink is None:
        return
    call = getattr(sink, "__call__", None)
    if (
        not callable(sink)
        or inspect.iscoroutinefunction(sink)
        or inspect.isasyncgenfunction(sink)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise TypeError("context_event_sink must be a synchronous callable")
