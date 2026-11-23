"""Receipt and failure value contracts for collaboration event append."""

from __future__ import annotations

from .event_vocabulary import (
    CollaborationAppendFailure,
    CollaborationAppendStatus,
)
from dataclasses import dataclass
from story_writing_agents.conversations.event_value_validation import (
    require_enum as _require_enum,
    validate_digest as _validate_digest,
    validate_identifier as _validate_identifier,
    validate_positive_integer as _validate_positive_integer,
    validate_utc_timestamp as _validate_utc_timestamp,
)


@dataclass(frozen=True, slots=True)
class CollaborationAppendReceipt:
    """Stable append result, including original ordering and timestamps on retry."""

    status: CollaborationAppendStatus
    batch_id: str
    batch_digest: str
    first_sequence: int
    last_sequence: int
    timestamps: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_enum(self.status, CollaborationAppendStatus, "append status")
        _validate_identifier(self.batch_id, label="append receipt batch ID")
        _validate_digest(self.batch_digest, label="append receipt batch digest")
        _validate_positive_integer(self.first_sequence, label="first appended sequence")
        _validate_positive_integer(self.last_sequence, label="last appended sequence")
        if self.last_sequence < self.first_sequence:
            raise ValueError("append receipt sequence range must be ordered")
        if type(self.timestamps) is not tuple:
            raise TypeError("append receipt timestamps must be an exact tuple")
        expected = self.last_sequence - self.first_sequence + 1
        if len(self.timestamps) != expected:
            raise ValueError("append receipt timestamps must cover its sequence range")
        for timestamp in self.timestamps:
            _validate_utc_timestamp(timestamp, label="append receipt timestamp")


class CollaborationAppendError(Exception):
    """Fixed semantic collaboration-store failure with no diagnostic authority."""

    def __init__(self, failure: CollaborationAppendFailure) -> None:
        _require_enum(failure, CollaborationAppendFailure, "append failure")
        self.failure = failure
        super().__init__(failure.value)
