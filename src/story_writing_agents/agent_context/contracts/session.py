"""Immutable invocation binding, correlation, and read receipt contracts."""

from __future__ import annotations

import base64
import hashlib
from ..actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionErrorCode,
    ContextEvidenceReceipt,
)
from ..manifest import (
    ContextManifest,
    ContextScope,
)
from .context_values import (
    CONTEXT_WIRE_VERSION,
    action_error,
    canonical_bytes,
    safe_identity,
    scope_value,
)
from .planning_inputs import (
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    MandatoryReferenceReservation,
)
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextInvocationBinding:
    """Immutable scope, correlation, manifest, and pending reference facts."""

    scope: ContextScope
    operation_id: str
    result_correlation_id: str
    manifest: ContextManifest
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ContextScope
            or type(self.operation_id) is not str
            or not self.operation_id
            or type(self.result_correlation_id) is not str
            or not self.result_correlation_id
            or type(self.manifest) is not ContextManifest
            or self.manifest.scope != self.scope
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        safe_identity(self.operation_id)
        safe_identity(self.result_correlation_id)
        if self.reference_operation_id is not None:
            safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if tuple(item.ordinal for item in self.mandatory_references) != tuple(
            range(1, len(self.mandatory_references) + 1)
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if sum(item.utf8_bytes for item in self.mandatory_references) > (
            MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReadReceipt:
    """Content-free receipt for one delivered ordered read observation."""

    correlation: ContextActionCorrelation
    evidence: tuple[ContextEvidenceReceipt, ...]
    observation_digest: str
    observation_utf8_bytes: int

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.evidence) is not tuple
            or not self.evidence
            or any(type(item) is not ContextEvidenceReceipt for item in self.evidence)
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if len({item.handle.value for item in self.evidence}) != len(self.evidence):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.observation_digest) is not str
            or not self.observation_digest.startswith("sha256:")
            or len(self.observation_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in self.observation_digest[7:]
            )
            or type(self.observation_utf8_bytes) is not int
            or self.observation_utf8_bytes < 0
            or self.observation_utf8_bytes
            > CONTEXT_ACTION_POLICY.max_observation_utf8_bytes
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)


def action_correlation(
    binding: ContextInvocationBinding,
    action_index: int,
) -> ContextActionCorrelation:
    """Reconstruct the complete exact correlation hidden behind one wire token."""

    return ContextActionCorrelation(
        binding.scope,
        binding.operation_id,
        binding.result_correlation_id,
        action_index,
    )


def correlation_token(correlation: ContextActionCorrelation) -> str:
    """Derive one deterministic URL-safe 256-bit token from full correlation."""

    body = canonical_bytes(
        [
            CONTEXT_WIRE_VERSION,
            scope_value(correlation.scope),
            correlation.operation_id,
            correlation.result_correlation_id,
            correlation.action_index,
        ]
    )
    return (
        base64.urlsafe_b64encode(hashlib.sha256(body).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


__all__ = [
    'ContextInvocationBinding',
    'ContextReadReceipt',
    'action_correlation',
    'correlation_token',
]
