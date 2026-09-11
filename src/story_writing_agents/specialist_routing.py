"""Pure specialist ownership for explicit, versioned story-authoring facts.

These facts describe work, not evidence or execution authority. Callers own
current-task correlation, candidate availability, reads, and confirmation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath


AUTHORING_SCOPE_SCHEMA_VERSION = 1
REVIEW_BASIS_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class ArtifactReviewDomain(StrEnum):
    """Canonical ownership domains shared by authoring and artifact review."""

    PLOT_STRUCTURE = "plot_structure"
    CHARACTER_WORLD = "character_world"
    TONE_EXECUTION = "tone_execution"


class AuthoringActivity(StrEnum):
    """The requested activity, independently of evidence acquisition."""

    WORKFLOW = "workflow"
    DRAFT = "draft"
    REVISE = "revise"
    REVIEW = "review"


class SpecialistAssignmentMode(StrEnum):
    """Whether a specialist produces material or assesses concrete material."""

    DRAFT = "draft"
    REVIEW = "review"


_SPECIALIST_BY_DOMAIN = {
    ArtifactReviewDomain.PLOT_STRUCTURE: "plot_architect",
    ArtifactReviewDomain.CHARACTER_WORLD: "character_custodian",
    ArtifactReviewDomain.TONE_EXECUTION: "execution_specialist",
}
_CORE_DOMAIN_BY_PATH = {
    "core_documents/global_outline.md": ArtifactReviewDomain.PLOT_STRUCTURE,
    "core_documents/part_outlines.md": ArtifactReviewDomain.PLOT_STRUCTURE,
    "core_documents/character_sheets.md": ArtifactReviewDomain.CHARACTER_WORLD,
    "core_documents/narrative_rules.md": ArtifactReviewDomain.TONE_EXECUTION,
}


def artifact_domain(path: str) -> ArtifactReviewDomain | None:
    """Classify an artifact identity without inspecting its existence or content.

    Safe unrelated paths return ``None``; malformed paths raise ``TypeError``
    or ``ValueError``. Manuscript reference paths may be broader than publishable
    artifact paths. Classification grants neither read nor publication access.
    """

    if type(path) is not str:
        raise TypeError("routing artifact path must be exact text")
    if not path or "\\" in path or "\x00" in path:
        raise ValueError("routing artifact path must be a nonempty POSIX path")
    if len(path.encode("utf-8", errors="strict")) > 1_024:
        raise ValueError("routing artifact path is too long")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError("routing artifact path must be normalized and relative")
    if path in _CORE_DOMAIN_BY_PATH:
        return _CORE_DOMAIN_BY_PATH[path]
    if (
        len(parsed.parts) >= 2
        and parsed.parts[0] == "manuscript"
        and parsed.parts[-1].endswith(".md")
        and parsed.parts[-1] != ".md"
        and not any(
            character in {"}", "\u2028", "\u2029"}
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in path
        )
    ):
        return ArtifactReviewDomain.TONE_EXECUTION
    return None


@dataclass(frozen=True, slots=True)
class AuthoringScope:
    """An explicit interpretation of work, bound to caller-owned task revision.

    Targets may be absent or empty on disk. A task without an exact target must
    disclose its semantic domains. ``task_id`` and ``task_revision`` correlate
    existing runtime work; this value neither creates nor advances a task.
    """

    activity: AuthoringActivity
    target_paths: tuple[str, ...]
    domains: tuple[ArtifactReviewDomain, ...]
    interpretation: str = field(repr=False)
    task_id: str
    task_revision: int
    schema_version: int = AUTHORING_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version, AUTHORING_SCOPE_SCHEMA_VERSION)
        if type(self.activity) is not AuthoringActivity:
            raise TypeError("authoring activity must be an AuthoringActivity")
        _path_domains(self.target_paths)
        _domain_tuple(self.domains)
        _text(self.interpretation, label="authoring interpretation")
        _identifier(self.task_id)
        if type(self.task_revision) is not int or self.task_revision < 1:
            raise ValueError("authoring task revision must be a positive integer")
        required = set(self.domains) | _path_domains(self.target_paths)
        if self.activity is AuthoringActivity.WORKFLOW:
            if required:
                raise ValueError("workflow scope cannot carry story domains or targets")
        elif not required:
            raise ValueError("story authoring requires explicit domains or targets")


@dataclass(frozen=True, slots=True)
class ReviewBasis:
    """Concrete draft/candidate input aliases plus disclosed review criteria.

    An authoring brief alone is not a candidate. Runtime must verify that each
    alias contains concrete material, its availability, and its current revision.
    """

    candidate_input_ids: tuple[str, ...]
    criteria: str = field(repr=False)
    schema_version: int = REVIEW_BASIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version, REVIEW_BASIS_SCHEMA_VERSION)
        _input_ids(self.candidate_input_ids)
        if not self.candidate_input_ids:
            raise ValueError("review basis requires concrete candidate inputs")
        _text(self.criteria, label="review criteria")


def specialists_for_domains(
    domains: tuple[ArtifactReviewDomain, ...],
) -> tuple[str, ...]:
    """Return the canonical specialist order for an exact domain tuple."""

    _domain_tuple(domains)
    return tuple(_SPECIALIST_BY_DOMAIN[domain] for domain in domains)


def required_authoring_domains(
    scope: AuthoringScope,
    *,
    affected_paths: tuple[str, ...] = (),
) -> tuple[ArtifactReviewDomain, ...]:
    """Union semantic targets with caller-resolved artifact facts.

    Affected paths may come from references, statuses, evidence, or current
    work. Their presence here proves no read. Their absence from input aliases
    cannot remove the domains already stated by the scope.
    """

    if type(scope) is not AuthoringScope:
        raise TypeError("authoring scope must be an AuthoringScope")
    domains = set(scope.domains)
    domains.update(_path_domains(scope.target_paths))
    domains.update(_path_domains(affected_paths))
    if scope.activity is AuthoringActivity.WORKFLOW and domains:
        raise ValueError("workflow scope cannot carry affected story artifacts")
    return tuple(domain for domain in ArtifactReviewDomain if domain in domains)


def required_authoring_specialists(
    scope: AuthoringScope,
    *,
    affected_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Derive mandatory specialists independently of attached input aliases."""

    return specialists_for_domains(
        required_authoring_domains(scope, affected_paths=affected_paths)
    )


def validate_specialist_routing(
    scope: AuthoringScope,
    specialist_ids: tuple[str, ...],
    *,
    affected_paths: tuple[str, ...] = (),
) -> None:
    """Reject noncanonical participants or omitted mandatory specialists."""

    if type(specialist_ids) is not tuple or any(
        type(item) is not str or item not in _SPECIALIST_BY_DOMAIN.values()
        for item in specialist_ids
    ):
        raise TypeError("routing participants must be canonical specialist IDs")
    if len(set(specialist_ids)) != len(specialist_ids):
        raise ValueError("routing participants must be unique")
    if specialist_ids != tuple(
        item for item in _SPECIALIST_BY_DOMAIN.values() if item in specialist_ids
    ):
        raise ValueError("routing participants must be in canonical order")
    required = required_authoring_specialists(scope, affected_paths=affected_paths)
    if not set(required).issubset(specialist_ids):
        raise ValueError("routing omits a required story specialist")


def validate_specialist_assignment(
    mode: SpecialistAssignmentMode,
    input_ids: tuple[str, ...],
    review_basis: ReviewBasis | None,
) -> None:
    """Validate explicit drafting or review against disclosed input aliases."""

    if type(mode) is not SpecialistAssignmentMode:
        raise TypeError("assignment mode must be a SpecialistAssignmentMode")
    _input_ids(input_ids)
    if mode is SpecialistAssignmentMode.DRAFT:
        if review_basis is not None:
            raise ValueError("draft assignment cannot carry a review basis")
    else:
        if type(review_basis) is not ReviewBasis:
            raise TypeError("review assignment requires a ReviewBasis")
        if not set(review_basis.candidate_input_ids).issubset(input_ids):
            raise ValueError("review candidates must be disclosed assignment inputs")


def _version(value: object, expected: int) -> None:
    if type(value) is not int or value != expected:
        raise ValueError("specialist routing schema version is unsupported")


def _identifier(value: object) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("specialist routing identifier is invalid")


def _text(value: object, *, label: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be exact text")
    if (
        not value.strip()
        or "\x00" in value
        or len(value.encode("utf-8", errors="strict")) > 32_768
    ):
        raise ValueError(f"{label} must be nonempty bounded text")


def _input_ids(value: object) -> None:
    if type(value) is not tuple:
        raise TypeError("specialist input IDs must be an exact tuple")
    for item in value:
        _identifier(item)
    if len(set(value)) != len(value):
        raise ValueError("specialist input IDs must be unique")


def _domain_tuple(value: object) -> None:
    if type(value) is not tuple or any(
        type(item) is not ArtifactReviewDomain for item in value
    ):
        raise TypeError("routing domains must be an exact domain tuple")
    if value != tuple(domain for domain in ArtifactReviewDomain if domain in value):
        raise ValueError("routing domains must be unique and in canonical order")


def _path_domains(paths: tuple[str, ...]) -> set[ArtifactReviewDomain]:
    if type(paths) is not tuple:
        raise TypeError("routing artifact paths must be an exact tuple")
    result: set[ArtifactReviewDomain] = set()
    for path in paths:
        domain = artifact_domain(path)
        if domain is None:
            raise ValueError("routing target must be a canonical artifact identity")
        result.add(domain)
    if len(set(paths)) != len(paths):
        raise ValueError("routing artifact paths must be unique")
    return result


__all__ = [
    "AUTHORING_SCOPE_SCHEMA_VERSION",
    "REVIEW_BASIS_SCHEMA_VERSION",
    "ArtifactReviewDomain",
    "AuthoringActivity",
    "AuthoringScope",
    "ReviewBasis",
    "SpecialistAssignmentMode",
    "artifact_domain",
    "required_authoring_domains",
    "required_authoring_specialists",
    "specialists_for_domains",
    "validate_specialist_assignment",
    "validate_specialist_routing",
]
