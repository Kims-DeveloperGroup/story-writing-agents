"""Stable writing-artifact reference values and resource limits.

Token discovery and provider-context resolution intentionally belong to later
runtime integration.  This module owns only immutable reference identities.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import MAX_ARTIFACT_PATH_BYTES, canonical_artifact_kind


MAX_REFERENCE_COUNT = 16
MAX_REFERENCE_UTF8_BYTES = 128 * 1024
MAX_REFERENCE_TOTAL_BYTES = 256 * 1024
MAX_VIEWER_UTF8_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactLineRange:
    """One one-based inclusive line selection."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int:
            raise TypeError("line range bounds must be integers")
        if self.start < 1 or self.end < self.start:
            raise ValueError("line range must be one-based, inclusive, and ordered")


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """A canonical artifact identity with an optional exact line range."""

    path: str
    line_range: ArtifactLineRange | None = None

    def __post_init__(self) -> None:
        if canonical_artifact_kind(self.path) is None:
            raise ValueError("reference path is not a canonical writing artifact")
        if self.line_range is not None and type(self.line_range) is not ArtifactLineRange:
            raise TypeError("line_range must be an ArtifactLineRange or None")

    @property
    def token(self) -> str:
        if self.line_range is None:
            return f"${{{self.path}}}"
        return (
            f"${{{self.path}}}#L{self.line_range.start}"
            f"-L{self.line_range.end}"
        )
