"""Immutable context call eligibility and result declarations."""

from __future__ import annotations

import hashlib
import json
from ...structured_output_validation import SchemaRules
from ..actions import (
    ContextCallReadFact,
    ContextSourceStatusReceipt,
)
from ..manifest import (
    ContextScope,
    ContextSourceKind,
)
from .context_values import (
    safe_digest,
    safe_identity,
    status_value,
)
from .errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from .result_schema import validated_result_schema_bytes
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)
from enum import StrEnum
from types import MappingProxyType


class ContextInvocationState(StrEnum):
    """Observable lifecycle of one call-local context action session."""

    OPEN = "open"
    SEARCHING = "searching"
    READING = "reading"
    ANSWER_AUTHORIZED = "answer_authorized"
    FAILED = "failed"


class ContextInvocationKind(StrEnum):
    """Optional provider-neutral semantic phase for a contextual invocation."""

    LEAD = "lead"
    SPECIALIST_ASSIGNMENT = "specialist_assignment"
    LEAD_SYNTHESIS = "lead_synthesis"
    SPECIALIST_ADVISORY = "specialist_advisory"


@dataclass(frozen=True, slots=True)
class ContextJsonObjectResultContract:
    """Carry one immutable owner-supplied JSON-object result schema."""

    contract_id: str
    schema: Mapping[str, object] = field(repr=False, compare=False)
    wire_format: bool = field(default=False, kw_only=True)
    _schema_bytes: bytes = field(init=False, repr=False)
    rule_graph: SchemaRules = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.wire_format) is not bool:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        safe_identity(self.contract_id)
        encoded = validated_result_schema_bytes(self.schema)
        decoded = json.loads(encoded)
        assert type(decoded) is dict
        object.__setattr__(self, "schema", MappingProxyType(decoded))
        object.__setattr__(self, "_schema_bytes", encoded)
        object.__setattr__(self, "rule_graph", SchemaRules(encoded))

    def schema_value(self) -> dict[str, object]:
        """Return a detached exact schema for one physical provider call."""

        return self.rule_graph.provider_schema_value()


@dataclass(frozen=True, slots=True)
class ContextCallOffers:
    """One owner-locked physical-call snapshot, without source content."""
    call_id: str
    scope: ContextScope
    action_index: int
    allowed_actions: tuple[str, ...]
    action_limits: tuple[tuple[str, int], ...]
    search_source_kinds: tuple[ContextSourceKind, ...]
    issued_handle_aliases: tuple[str, ...]
    completed_read_facts: tuple[ContextCallReadFact, ...]
    delivery_read_facts: tuple[ContextCallReadFact, ...]
    core_status_facts: tuple[ContextSourceStatusReceipt, ...]
    reference_read_digest: str | None = field(default=None, init=False)
    offer_digest: str = field(init=False)

    remaining_provider_calls: int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.remaining_provider_calls is not None and (type(self.remaining_provider_calls) is not int or self.remaining_provider_calls < 0):
            raise ValueError("current physical remaining is invalid")
        safe_identity(self.call_id)
        if (type(self.scope) is not ContextScope or type(self.action_index) is not int or self.action_index < 1
                or type(self.allowed_actions) is not tuple or not self.allowed_actions
                or len(set(self.allowed_actions)) != len(self.allowed_actions)
                or any(item not in {"search", "read", "answer"} for item in self.allowed_actions)
                or type(self.action_limits) is not tuple or any(
                    type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                    or type(item[1]) is not int or item[1] < 0 for item in self.action_limits)
                or len({item[0] for item in self.action_limits}) != len(self.action_limits)
                or type(self.search_source_kinds) is not tuple
                or any(type(item) is not ContextSourceKind for item in self.search_source_kinds)
                or type(self.issued_handle_aliases) is not tuple
                or any(type(item) is not str for item in self.issued_handle_aliases)
                or len(set(self.issued_handle_aliases)) != len(self.issued_handle_aliases)
                or any(type(values) is not tuple or any(type(item) is not ContextCallReadFact for item in values)
                       for values in (self.completed_read_facts, self.delivery_read_facts))):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (type(self.core_status_facts) is not tuple
                or any(type(item) is not ContextSourceStatusReceipt
                       for item in self.core_status_facts)
                or len({item.evidence_id for item in self.core_status_facts})
                != len(self.core_status_facts)):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.reference_read_digest is not None:
            safe_digest(self.reference_read_digest)
        encoded = json.dumps(self.snapshot_value(), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8", "strict")
        object.__setattr__(self, "offer_digest", hashlib.sha256(encoded).hexdigest())

    def snapshot_value(self) -> dict[str, object]:
        """Return the full detached source/eligibility facts of this physical offer."""
        value = {"call_id": self.call_id, "scope": repr(self.scope), "action_index": self.action_index,
            "actions": self.allowed_actions, "limits": self.action_limits,
            "search_kinds": [kind.value for kind in self.search_source_kinds],
            "handles": self.issued_handle_aliases,
            "completed": [item.snapshot_value() for item in self.completed_read_facts],
            "delivery": [item.snapshot_value() for item in self.delivery_read_facts],
            "core_statuses": [status_value(item) for item in self.core_status_facts],
        }
        if self.reference_read_digest is not None:
            value["reference_read_digest"] = self.reference_read_digest
        if self.remaining_provider_calls is not None:
            value["remaining_provider_calls"] = self.remaining_provider_calls
        return value


__all__ = [
    'ContextInvocationState',
    'ContextInvocationKind',
    'ContextJsonObjectResultContract',
    'ContextCallOffers',
]
