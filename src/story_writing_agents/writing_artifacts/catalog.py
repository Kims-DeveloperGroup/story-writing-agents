"""Immutable catalog values and canonical writing-artifact path grammar."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping


CATALOG_SCHEMA_VERSION = 2
MAX_ARTIFACT_PATH_BYTES = 1024
CORE_ARTIFACT_PATHS: tuple[str, ...] = (
    "core_documents/global_outline.md",
    "core_documents/part_outlines.md",
    "core_documents/character_sheets.md",
    "core_documents/narrative_rules.md",
)
_CORE_ARTIFACT_SET = frozenset(CORE_ARTIFACT_PATHS)
_PART_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_TAGGED_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


class ArtifactKind(StrEnum):
    """Canonical user-visible writing-artifact categories."""

    CORE_DOCUMENT = "core_document"
    MANUSCRIPT = "manuscript"


def validate_artifact_path_syntax(value: object) -> tuple[str, ...]:
    """Validate shared lexical syntax for one artifact-relative POSIX path.

    This package-internal boundary deliberately does not classify either a
    publication identity or a reference source.  Those policies are owned by
    their respective modules and consume only this common lexical result.
    """

    if type(value) is not str:
        raise TypeError("artifact path must be a string")
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("artifact path must be a nonempty POSIX path")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("artifact path must be strict UTF-8") from exc
    if len(encoded) > MAX_ARTIFACT_PATH_BYTES:
        raise ValueError("artifact path is too long")
    parsed = PurePosixPath(value)
    parts = parsed.parts
    if parsed.is_absolute() or not parts or parsed.as_posix() != value:
        raise ValueError("artifact path must be normalized and workspace-relative")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact path contains an unsafe component")
    return parts


def canonical_artifact_kind(path: object) -> ArtifactKind | None:
    """Return the canonical kind for one exact workspace-relative path.

    Syntactically safe non-artifact paths return ``None``. Unsafe or
    non-normalized paths raise before a caller can attempt filesystem access.
    """

    parts = validate_artifact_path_syntax(path)
    assert isinstance(path, str)
    if path in _CORE_ARTIFACT_SET:
        return ArtifactKind.CORE_DOCUMENT
    if len(parts) < 3 or parts[0] != "manuscript":
        return None
    if _PART_ID.fullmatch(parts[1]) is None:
        return None
    if any(_PATH_COMPONENT.fullmatch(part) is None for part in parts[2:]):
        return None
    if not parts[-1].endswith(".md") or parts[-1] == ".md":
        return None
    return ArtifactKind.MANUSCRIPT


def artifact_kind_path_rule(artifact_kind: ArtifactKind) -> dict[str, object]:
    """Declare the exact canonical path language for one artifact kind."""
    if type(artifact_kind) is not ArtifactKind:
        raise TypeError("artifact kind must be an exact ArtifactKind")
    if artifact_kind is ArtifactKind.CORE_DOCUMENT:
        body = "(?:" + "|".join(re.escape(path) for path in CORE_ARTIFACT_PATHS) + ")"
    else:
        component = _PATH_COMPONENT.pattern
        # The former suffix lookahead intersected a 1..128 ASCII component.
        filename = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,124}\.md"
        body = "manuscript/" + _PART_ID.pattern + "/(?:" + component + "/)*" + filename
    return {"type": "string", "maxLength": MAX_ARTIFACT_PATH_BYTES,
            "pattern": "^" + body + r"\z"}


def manuscript_artifact_path(output_path: object) -> str:
    """Translate required ``PART/PATH.md`` output syntax to canonical identity."""

    validate_artifact_path_syntax(output_path)
    assert isinstance(output_path, str)
    artifact_path = f"manuscript/{output_path}"
    if canonical_artifact_kind(artifact_path) is not ArtifactKind.MANUSCRIPT:
        raise ValueError("output path must be PART/PATH.md with safe components")
    return artifact_path


@dataclass(frozen=True, slots=True)
class ArtifactCatalogEntry:
    """Repository-owned provenance for one current applied artifact."""

    path: str
    kind: ArtifactKind
    sha256: str
    application_id: str
    application_sequence: int
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    change_set_digest: str
    relationship_validation_digest: str

    def __post_init__(self) -> None:
        observed_kind = canonical_artifact_kind(self.path)
        if observed_kind is None or observed_kind is not self.kind:
            raise ValueError("catalog entry path and kind must be canonical")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("catalog entry sha256 must be lowercase hexadecimal")
        for name in ("application_id", "candidate_id"):
            if _IDENTIFIER.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"catalog entry {name} is invalid")
        if type(self.application_sequence) is not int or self.application_sequence < 1:
            raise ValueError("catalog entry application_sequence must be positive")
        if type(self.candidate_revision) is not int or self.candidate_revision < 1:
            raise ValueError("catalog entry candidate_revision must be positive")
        if _TAGGED_SHA256.fullmatch(self.candidate_digest) is None:
            raise ValueError("catalog entry candidate_digest is invalid")
        if _SHA256.fullmatch(self.change_set_digest) is None:
            raise ValueError("catalog entry change_set_digest is invalid")
        if _TAGGED_SHA256.fullmatch(self.relationship_validation_digest) is None:
            raise ValueError("catalog entry relationship_validation_digest is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "application_sequence": self.application_sequence,
            "candidate_digest": self.candidate_digest,
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "change_set_digest": self.change_set_digest,
            "kind": self.kind.value,
            "path": self.path,
            "relationship_validation_digest": self.relationship_validation_digest,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArtifactCatalogEntry:
        if type(value) is not dict:
            raise ValueError("catalog entry must be an object")
        expected = {
            "application_id",
            "application_sequence",
            "candidate_digest",
            "candidate_id",
            "candidate_revision",
            "change_set_digest",
            "kind",
            "path",
            "relationship_validation_digest",
            "sha256",
        }
        if set(value) != expected:
            raise ValueError("catalog entry fields are invalid")
        try:
            kind = ArtifactKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("catalog entry kind is invalid") from exc
        return cls(
            path=value["path"],
            kind=kind,
            sha256=value["sha256"],
            application_id=value["application_id"],
            application_sequence=value["application_sequence"],
            candidate_id=value["candidate_id"],
            candidate_revision=value["candidate_revision"],
            candidate_digest=value["candidate_digest"],
            change_set_digest=value["change_set_digest"],
            relationship_validation_digest=value["relationship_validation_digest"],
        )


@dataclass(frozen=True, slots=True)
class ArtifactCatalog:
    """One validated immutable projection of repository provenance."""

    entries: Mapping[str, ArtifactCatalogEntry]
    schema_version: int = CATALOG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != CATALOG_SCHEMA_VERSION
        ):
            raise ValueError("catalog schema version is unsupported")
        if not isinstance(self.entries, Mapping):
            raise TypeError("catalog entries must be a mapping")
        copied = dict(self.entries)
        if any(type(path) is not str for path in copied):
            raise TypeError("catalog keys must be strings")
        for path, entry in copied.items():
            if type(entry) is not ArtifactCatalogEntry or path != entry.path:
                raise ValueError("catalog keys must match exact entry paths")
        object.__setattr__(self, "entries", MappingProxyType(copied))

    def to_dict(self) -> dict[str, object]:
        return {
            "entries": [self.entries[path].to_dict() for path in sorted(self.entries)],
            "schema_version": self.schema_version,
        }

    @classmethod
    def empty(cls) -> ArtifactCatalog:
        return cls({})

    @classmethod
    def from_dict(cls, value: object) -> ArtifactCatalog:
        if type(value) is not dict or set(value) != {"entries", "schema_version"}:
            raise ValueError("artifact catalog fields are invalid")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != CATALOG_SCHEMA_VERSION
        ):
            raise ValueError("catalog schema version is unsupported")
        raw_entries = value["entries"]
        if type(raw_entries) is not list:
            raise ValueError("catalog entries must be an array")
        parsed = [ArtifactCatalogEntry.from_dict(item) for item in raw_entries]
        entries = {entry.path: entry for entry in parsed}
        if len(entries) != len(parsed):
            raise ValueError("catalog entries must have unique paths")
        return cls(entries)
