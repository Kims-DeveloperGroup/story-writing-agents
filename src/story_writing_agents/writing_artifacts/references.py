"""Stable writing-artifact reference values and resource limits.

Provider-context resolution intentionally belongs to later runtime
integration.  This module owns only immutable reference and parse-result
identities.
"""

from __future__ import annotations

from dataclasses import dataclass

import hashlib

from .catalog import (
    MAX_ARTIFACT_PATH_BYTES,
    ArtifactKind,
    canonical_artifact_kind,
)


MAX_REFERENCE_COUNT = 16
MAX_REFERENCE_UTF8_BYTES = 128 * 1024
MAX_REFERENCE_TOTAL_BYTES = 256 * 1024
MAX_VIEWER_UTF8_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactSourceIdentity:
    """Content-free identity for one stable canonical filesystem source."""

    path: str
    kind: ArtifactKind
    source_sha256: str
    source_utf8_bytes: int
    device_id: int
    inode: int
    modified_ns: int
    changed_ns: int

    def __post_init__(self) -> None:
        if canonical_artifact_kind(self.path) is not self.kind:
            raise ValueError("artifact source path and kind must be canonical")
        if (
            type(self.source_sha256) is not str
            or len(self.source_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_sha256
            )
        ):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        if type(self.source_utf8_bytes) is not int or self.source_utf8_bytes < 0:
            raise ValueError("source_utf8_bytes must be nonnegative")
        for name in ("device_id", "inode"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("modified_ns", "changed_ns"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class ArtifactContentSnapshot:
    """Exact bytes for one stable canonical filesystem source."""

    source: ArtifactSourceIdentity
    content: bytes

    def __post_init__(self) -> None:
        if type(self.source) is not ArtifactSourceIdentity:
            raise TypeError("source must be an ArtifactSourceIdentity")
        if type(self.content) is not bytes:
            raise TypeError("artifact snapshot content must be bytes")
        if len(self.content) != self.source.source_utf8_bytes:
            raise ValueError("artifact snapshot size does not match its source")
        if (
            hashlib.sha256(self.content).hexdigest()
            != self.source.source_sha256
        ):
            raise ValueError(
                "artifact snapshot content does not match its source"
            )
        try:
            decoded = self.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("artifact snapshot is not valid UTF-8") from exc
        if "\x00" in decoded:
            raise ValueError("artifact snapshot contains NUL")

    @property
    def path(self) -> str:
        return self.source.path

    def read_text(self) -> str:
        """Decode the immutable verified bytes as strict UTF-8."""

        return self.content.decode("utf-8", errors="strict")


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


class ArtifactReferenceDirectiveError(ValueError):
    """A deterministic, content-free directive validation failure."""


@dataclass(frozen=True, slots=True)
class ArtifactReferenceBatch:
    """One immutable ordered directive parse result.

    ``source_text`` preserves the caller's strict-UTF-8 input exactly, while
    ``provider_text`` differs only by the documented escaped-dollar
    projection.  References contain identities and optional ranges, never
    artifact bytes.
    """

    source_text: str
    provider_text: str
    references: tuple[ArtifactReference, ...]

    def __post_init__(self) -> None:
        for name in ("source_text", "provider_text"):
            value = getattr(self, name)
            if type(value) is not str:
                raise TypeError(f"{name} must be a string")
            if "\x00" in value:
                raise ArtifactReferenceDirectiveError(
                    "reference input must not contain NUL"
                )
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise ArtifactReferenceDirectiveError(
                    "reference input must be strict UTF-8"
                ) from None

        if type(self.references) is not tuple:
            raise TypeError("references must be a tuple")
        if any(
            type(reference) is not ArtifactReference
            for reference in self.references
        ):
            raise TypeError("references must contain ArtifactReference values")
        if len(self.references) > MAX_REFERENCE_COUNT:
            raise ArtifactReferenceDirectiveError(
                "reference input exceeds the directive limit"
            )

        observed: dict[str, list[ArtifactLineRange | None]] = {}
        for reference in self.references:
            ranges = observed.setdefault(reference.path, [])
            if reference.line_range in ranges:
                raise ArtifactReferenceDirectiveError(
                    "reference input contains an exact duplicate"
                )
            if reference.line_range is None or any(
                line_range is None for line_range in ranges
            ):
                if ranges:
                    raise ArtifactReferenceDirectiveError(
                        "reference input mixes whole and ranged directives"
                    )
            else:
                for line_range in ranges:
                    assert line_range is not None
                    if (
                        reference.line_range.start <= line_range.end
                        and line_range.start <= reference.line_range.end
                    ):
                        raise ArtifactReferenceDirectiveError(
                            "reference input contains overlapping ranges"
                        )
            ranges.append(reference.line_range)
