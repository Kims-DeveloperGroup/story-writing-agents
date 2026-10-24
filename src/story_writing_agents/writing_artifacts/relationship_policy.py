"""Pure canonical relationship policy for exact writing-artifact changes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ..specialist_routing import ArtifactReviewDomain, artifact_domain
from .catalog import CORE_ARTIFACT_PATHS, ArtifactKind, canonical_artifact_kind
from .change_sets import (
    ArtifactChangeSet,
    ArtifactChangeOperationKind,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    project_artifact_change_set,
)


ARTIFACT_RELATIONSHIP_SCHEMA_VERSION = 1
ARTIFACT_RELATIONSHIP_COMPANION_RULES: tuple[()] = ()
MAX_RELATIONSHIP_ARTIFACTS = 4_096
MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES = 8 * 1024 * 1024
MAX_RELATIONSHIP_STATE_UTF8_BYTES = 64 * 1024 * 1024
MAX_RELATIONSHIP_EDGES = 65_536

_SHA256 = re.compile(r"[0-9a-f]{64}")
_CORE_PATH_SET = frozenset(CORE_ARTIFACT_PATHS)


class ArtifactRelationshipErrorCode(StrEnum):
    """Closed content-free failures for relationship validation."""

    INVALID_SCHEMA = "invalid_schema"
    INVALID_ATTACHED_CORE_PATHS = "invalid_attached_core_paths"
    PROTECTED_CORE_OPERATION = "protected_core_operation"
    PATH_ESCAPE = "path_escape"
    ARTIFACT_COUNT_LIMIT = "artifact_count_limit"
    ARTIFACT_SIZE_LIMIT = "artifact_size_limit"
    STATE_SIZE_LIMIT = "state_size_limit"
    EDGE_COUNT_LIMIT = "edge_count_limit"


_ERROR_MESSAGES = {
    ArtifactRelationshipErrorCode.INVALID_SCHEMA: (
        "artifact relationship input schema is invalid"
    ),
    ArtifactRelationshipErrorCode.INVALID_ATTACHED_CORE_PATHS: (
        "attached core artifact paths are invalid"
    ),
    ArtifactRelationshipErrorCode.PROTECTED_CORE_OPERATION: (
        "a permanent core artifact cannot be renamed or deleted"
    ),
    ArtifactRelationshipErrorCode.PATH_ESCAPE: (
        "an artifact relationship target escapes the workspace"
    ),
    ArtifactRelationshipErrorCode.ARTIFACT_COUNT_LIMIT: (
        "artifact relationship state exceeds the artifact-count limit"
    ),
    ArtifactRelationshipErrorCode.ARTIFACT_SIZE_LIMIT: (
        "artifact relationship state exceeds the per-artifact byte limit"
    ),
    ArtifactRelationshipErrorCode.STATE_SIZE_LIMIT: (
        "artifact relationship state exceeds the total byte limit"
    ),
    ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT: (
        "artifact relationship state exceeds the edge-count limit"
    ),
}


class ArtifactRelationshipError(RuntimeError):
    """One deterministic relationship failure without artifact content."""

    def __init__(
        self,
        code: ArtifactRelationshipErrorCode,
        *,
        operation_index: int | None = None,
        artifact_index: int | None = None,
        link_index: int | None = None,
    ) -> None:
        if type(code) is not ArtifactRelationshipErrorCode:
            raise TypeError("code must be an ArtifactRelationshipErrorCode")
        for name, value in (
            ("operation_index", operation_index),
            ("artifact_index", artifact_index),
            ("link_index", link_index),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise TypeError(f"{name} must be a nonnegative integer or None")
        self.code = code
        self.operation_index = operation_index
        self.artifact_index = artifact_index
        self.link_index = link_index
        super().__init__(_ERROR_MESSAGES[code])


class ArtifactRelationshipKind(StrEnum):
    """Closed canonical artifact relationship kinds."""

    INLINE_MARKDOWN_LINK = "inline_markdown_link"


class ArtifactReviewReason(StrEnum):
    """Closed reasons why an artifact review domain is required."""

    TOUCHED_CORE = "touched_core"
    MANUSCRIPT_OPERATION = "manuscript_operation"
    ATTACHED_CORE_EVIDENCE = "attached_core_evidence"
    RELATIONSHIP_CORE_EVIDENCE = "relationship_core_evidence"


@dataclass(frozen=True, slots=True)
class ArtifactRelationshipEdge:
    """One exact content-free inline-link fact."""

    source_path: str
    target_path: str
    destination_start_utf8_byte: int
    destination_end_utf8_byte: int
    fragment: str | None = field(repr=False)
    relationship_kind: ArtifactRelationshipKind

    def __post_init__(self) -> None:
        _require_canonical_path(self.source_path)
        _require_canonical_path(self.target_path)
        if (
            type(self.destination_start_utf8_byte) is not int
            or type(self.destination_end_utf8_byte) is not int
            or self.destination_start_utf8_byte < 0
            or self.destination_end_utf8_byte <= self.destination_start_utf8_byte
        ):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        if type(self.relationship_kind) is not ArtifactRelationshipKind:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        if self.fragment is not None:
            _require_fragment(self.fragment)


@dataclass(frozen=True, slots=True)
class ArtifactReviewFact:
    """One deterministic artifact-domain review requirement."""

    domain: ArtifactReviewDomain
    reason: ArtifactReviewReason
    artifact_path: str
    operation_index: int | None

    def __post_init__(self) -> None:
        if (
            type(self.domain) is not ArtifactReviewDomain
            or type(self.reason) is not ArtifactReviewReason
        ):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        kind = _require_canonical_path(self.artifact_path)
        if self.operation_index is not None and (
            type(self.operation_index) is not int or self.operation_index < 0
        ):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        if self.reason is ArtifactReviewReason.ATTACHED_CORE_EVIDENCE:
            if self.operation_index is not None:
                raise ArtifactRelationshipError(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA
                )
        elif self.operation_index is None:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        if self.reason is ArtifactReviewReason.MANUSCRIPT_OPERATION:
            if (
                kind is not ArtifactKind.MANUSCRIPT
                or self.domain is not ArtifactReviewDomain.TONE_EXECUTION
            ):
                raise ArtifactRelationshipError(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA
                )
        elif (
            self.artifact_path not in _CORE_PATH_SET
            or self.domain is not _core_review_domain(self.artifact_path)
        ):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )


@dataclass(frozen=True, slots=True)
class ArtifactRelationshipValidation:
    """Complete exact relationship delta and review facts for one change set."""

    schema_version: int
    base_state_digest: str
    projected_state_digest: str
    change_set_digest: str
    added_edges: tuple[ArtifactRelationshipEdge, ...]
    removed_edges: tuple[ArtifactRelationshipEdge, ...]
    retained_edges: tuple[ArtifactRelationshipEdge, ...]
    projected_dangling_edges: tuple[ArtifactRelationshipEdge, ...]
    review_facts: tuple[ArtifactReviewFact, ...]

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != ARTIFACT_RELATIONSHIP_SCHEMA_VERSION
        ):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        for digest in (
            self.base_state_digest,
            self.projected_state_digest,
            self.change_set_digest,
        ):
            _require_digest(digest)
        edge_groups = (
            self.added_edges,
            self.removed_edges,
            self.retained_edges,
            self.projected_dangling_edges,
        )
        for group in edge_groups:
            _require_exact_tuple(group, ArtifactRelationshipEdge)
            if len(set(group)) != len(group) or group != tuple(
                sorted(group, key=_edge_key)
            ):
                raise ArtifactRelationshipError(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA
                )
        _require_exact_tuple(self.review_facts, ArtifactReviewFact)
        if len(set(self.review_facts)) != len(
            self.review_facts
        ) or self.review_facts != tuple(sorted(self.review_facts, key=_review_key)):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        added = set(self.added_edges)
        removed = set(self.removed_edges)
        retained = set(self.retained_edges)
        if (
            len(added) != len(self.added_edges)
            or len(removed) != len(self.removed_edges)
            or len(retained) != len(self.retained_edges)
            or added & removed
            or added & retained
            or removed & retained
            or not set(self.projected_dangling_edges) <= added | retained
        ):
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )

    @property
    def is_valid(self) -> bool:
        """Return whether the projected graph contains no dangling edge."""

        return not self.projected_dangling_edges


def validate_artifact_relationships(
    base_state: Mapping[str, bytes],
    change_set: ArtifactChangeSet,
    *,
    attached_core_paths: tuple[str, ...],
) -> ArtifactRelationshipValidation:
    """Validate the complete projected canonical relationship graph."""

    normalized_base = _bounded_state(base_state)
    attached = _require_attached_core_paths(attached_core_paths)
    projected = project_artifact_change_set(normalized_base, change_set)
    _reject_protected_core_operations(change_set)
    normalized_projected = _bounded_state(projected)

    base_edges = _parse_state_relationships(normalized_base)
    projected_edges = _parse_state_relationships(normalized_projected)
    base_set = set(base_edges)
    projected_set = set(projected_edges)
    added = tuple(sorted(projected_set - base_set, key=_edge_key))
    removed = tuple(sorted(base_set - projected_set, key=_edge_key))
    retained = tuple(sorted(base_set & projected_set, key=_edge_key))
    dangling = tuple(
        edge for edge in projected_edges if edge.target_path not in normalized_projected
    )
    review_facts = _derive_review_facts(
        change_set,
        attached_core_paths=attached,
        base_edges=base_edges,
        projected_edges=projected_edges,
    )
    return ArtifactRelationshipValidation(
        schema_version=ARTIFACT_RELATIONSHIP_SCHEMA_VERSION,
        base_state_digest=change_set.base_state_digest,
        projected_state_digest=change_set.projected_state_digest,
        change_set_digest=change_set.change_set_digest,
        added_edges=added,
        removed_edges=removed,
        retained_edges=retained,
        projected_dangling_edges=dangling,
        review_facts=review_facts,
    )


def _require_digest(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    return value


def _require_canonical_path(value: object) -> ArtifactKind:
    invalid = False
    try:
        kind = canonical_artifact_kind(value)
    except (TypeError, ValueError):
        invalid = True
        kind = None
    if invalid or kind is None:
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    return kind


def _require_fragment(value: object) -> str:
    if type(value) is not str or not value:
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    invalid = False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        invalid = True
        encoded = b""
    if invalid or any(_is_ascii_space_or_control(item) for item in encoded):
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    if any(item in b"\\()?%#" for item in encoded):
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    return value


def _require_exact_tuple(value: object, item_type: type[object]) -> tuple[object, ...]:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    return value


def _bounded_state(state: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(state, Mapping):
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    invalid = False
    try:
        items = tuple(state.items())
    except Exception:
        invalid = True
        items = ()
    if invalid:
        raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)
    if len(items) > MAX_RELATIONSHIP_ARTIFACTS:
        raise ArtifactRelationshipError(
            ArtifactRelationshipErrorCode.ARTIFACT_COUNT_LIMIT
        )
    normalized: dict[str, bytes] = {}
    for path, content in items:
        _require_canonical_path(path)
        if type(content) is not bytes:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        invalid_utf8 = False
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            invalid_utf8 = True
        if invalid_utf8:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_SCHEMA
            )
        normalized[path] = content
    ordered = dict(sorted(normalized.items(), key=lambda item: item[0].encode("utf-8")))
    total = 0
    for artifact_index, content in enumerate(ordered.values()):
        if len(content) > MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.ARTIFACT_SIZE_LIMIT,
                artifact_index=artifact_index,
            )
        total += len(content)
        if total > MAX_RELATIONSHIP_STATE_UTF8_BYTES:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.STATE_SIZE_LIMIT,
                artifact_index=artifact_index,
            )
    return ordered


def _require_attached_core_paths(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ArtifactRelationshipError(
            ArtifactRelationshipErrorCode.INVALID_ATTACHED_CORE_PATHS
        )
    observed: set[str] = set()
    for path in value:
        if type(path) is not str or path not in _CORE_PATH_SET or path in observed:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.INVALID_ATTACHED_CORE_PATHS
            )
        observed.add(path)
    ordered = tuple(sorted(observed, key=lambda path: path.encode("utf-8")))
    if value != ordered:
        raise ArtifactRelationshipError(
            ArtifactRelationshipErrorCode.INVALID_ATTACHED_CORE_PATHS
        )
    return value


def artifact_source_operation_rule(
    operation_kind: ArtifactChangeOperationKind,
    source_path: str,
) -> tuple[bool, ArtifactKind | None]:
    """Return source permission and the required rename destination kind."""

    if type(operation_kind) is not ArtifactChangeOperationKind or type(source_path) is not str:
        raise TypeError("artifact operation rule requires exact kind and source text")
    source_kind = canonical_artifact_kind(source_path)
    allowed = source_kind is not None and not (
        operation_kind in (ArtifactChangeOperationKind.RENAME, ArtifactChangeOperationKind.DELETE)
        and source_path in _CORE_PATH_SET
    )
    destination_kind = source_kind if operation_kind is ArtifactChangeOperationKind.RENAME else None
    return allowed, destination_kind


def _reject_protected_core_operations(
    change_set: ArtifactChangeSet,
    *,
    _source_rule=artifact_source_operation_rule,
) -> None:
    for operation_index, operation in enumerate(change_set.operations):
        source_path = (
            operation.source_path if type(operation) is ArtifactRenameOperation else operation.path
        )
        if not _source_rule(operation.operation_kind, source_path)[0]:
            raise ArtifactRelationshipError(
                ArtifactRelationshipErrorCode.PROTECTED_CORE_OPERATION,
                operation_index=operation_index,
            )

def _parse_state_relationships(
    state: Mapping[str, bytes],
) -> tuple[ArtifactRelationshipEdge, ...]:
    edges: list[ArtifactRelationshipEdge] = []
    for artifact_index, (source_path, content) in enumerate(state.items()):
        for link_index, parsed in enumerate(_scan_links(content)):
            raw_path, fragment, start, end = parsed
            target = _resolve_target(
                source_path,
                raw_path,
                artifact_index=artifact_index,
                link_index=link_index,
            )
            if target is None:
                continue
            edge = ArtifactRelationshipEdge(
                source_path=source_path,
                target_path=target,
                destination_start_utf8_byte=start,
                destination_end_utf8_byte=end,
                fragment=fragment,
                relationship_kind=ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            )
            edges.append(edge)
            if len(edges) > MAX_RELATIONSHIP_EDGES:
                raise ArtifactRelationshipError(
                    ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT,
                    artifact_index=artifact_index,
                    link_index=link_index,
                )
    return tuple(sorted(edges, key=_edge_key))


def _scan_links(content: bytes) -> tuple[tuple[bytes, str | None, int, int], ...]:
    excluded = _fenced_code_intervals(content)
    interval_index = 0
    parsed: list[tuple[bytes, str | None, int, int]] = []
    index = 0
    while index < len(content):
        while interval_index < len(excluded) and index >= excluded[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(excluded)
            and excluded[interval_index][0] <= index < excluded[interval_index][1]
        ):
            index = excluded[interval_index][1]
            continue
        if content[index] == 0x60:
            run_end = _same_byte_run_end(content, index, 0x60)
            closing = _find_inline_code_close(
                content,
                run_end,
                run_end - index,
                excluded,
                interval_index,
            )
            index = closing if closing is not None else run_end
            continue
        if content[index] != 0x5B or _is_escaped(content, index):
            index += 1
            continue
        if (
            index > 0
            and content[index - 1] == 0x21
            and not _is_escaped(content, index - 1)
        ):
            index += 1
            continue
        candidate = _parse_link_candidate(content, index)
        if candidate is None:
            index += 1
            continue
        parsed.append(candidate[:-1])
        index = candidate[-1]
    return tuple(parsed)


def _parse_link_candidate(
    content: bytes,
    opening: int,
) -> tuple[bytes, str | None, int, int, int] | None:
    label_end = opening + 1
    if label_end >= len(content):
        return None
    while label_end < len(content):
        value = content[label_end]
        if value == 0x5D:
            break
        if value in {0x0A, 0x0D, 0x5B, 0x5C} or _is_ascii_control(value):
            return None
        label_end += 1
    if label_end == opening + 1 or label_end + 1 >= len(content):
        return None
    if content[label_end : label_end + 2] != b"](":
        return None
    destination_start = label_end + 2
    closing = destination_start
    while closing < len(content) and content[closing] != 0x29:
        value = content[closing]
        if value in {0x0A, 0x0D, 0x28}:
            return None
        closing += 1
    if closing >= len(content):
        return None
    destination = content[destination_start:closing]
    normalized = _parse_destination(destination)
    if normalized is None:
        return None
    raw_path, fragment, path_length = normalized
    return (
        raw_path,
        fragment,
        destination_start,
        destination_start + path_length,
        closing + 1,
    )


def _parse_destination(
    value: bytes,
) -> tuple[bytes, str | None, int] | None:
    if not value or value.startswith(b"//"):
        return None
    if any(_is_ascii_space_or_control(item) for item in value):
        return None
    if any(item in b"\\()?%" for item in value):
        return None
    if value.count(b"#") > 1 or b"?" in value:
        return None
    path, marker, raw_fragment = value.partition(b"#")
    if not path or (marker and not raw_fragment):
        return None
    if _looks_like_scheme(path):
        return None
    invalid = False
    try:
        path.decode("utf-8", errors="strict")
        fragment = raw_fragment.decode("utf-8", errors="strict") if marker else None
    except UnicodeDecodeError:
        invalid = True
        fragment = None
    if invalid:
        return None
    if fragment is not None:
        try:
            _require_fragment(fragment)
        except ArtifactRelationshipError:
            return None
    return path, fragment, len(path)


def _resolve_target(
    source_path: str,
    raw_path: bytes,
    *,
    artifact_index: int,
    link_index: int,
) -> str | None:
    raw = raw_path.decode("utf-8", errors="strict")
    if raw.startswith("/"):
        components: list[str] = []
        parts = raw[1:].split("/")
    else:
        components = source_path.split("/")[:-1]
        parts = raw.split("/")
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not components:
                raise ArtifactRelationshipError(
                    ArtifactRelationshipErrorCode.PATH_ESCAPE,
                    artifact_index=artifact_index,
                    link_index=link_index,
                )
            components.pop()
            continue
        components.append(part)
    target = "/".join(components)
    invalid = False
    try:
        kind = canonical_artifact_kind(target)
    except (TypeError, ValueError):
        invalid = True
        kind = None
    if invalid or kind is None:
        return None
    return target


def _fenced_code_intervals(content: bytes) -> tuple[tuple[int, int], ...]:
    intervals: list[tuple[int, int]] = []
    fence_start: int | None = None
    fence_byte: int | None = None
    fence_length = 0
    line_start = 0
    while line_start < len(content):
        newline = content.find(b"\n", line_start)
        line_end = len(content) if newline < 0 else newline + 1
        line = content[line_start:line_end].rstrip(b"\r\n")
        indent = 0
        while indent < min(3, len(line)) and line[indent] == 0x20:
            indent += 1
        marker = line[indent] if indent < len(line) else None
        run_end = (
            _same_byte_run_end(line, indent, marker)
            if marker in {0x60, 0x7E}
            else indent
        )
        run_length = run_end - indent
        if fence_start is None:
            if run_length >= 3:
                fence_start = line_start
                fence_byte = marker
                fence_length = run_length
        elif marker == fence_byte and run_length >= fence_length:
            if not line[run_end:].strip(b" \t"):
                intervals.append((fence_start, line_end))
                fence_start = None
                fence_byte = None
                fence_length = 0
        line_start = line_end
    if fence_start is not None:
        intervals.append((fence_start, len(content)))
    return tuple(intervals)


def _find_inline_code_close(
    content: bytes,
    start: int,
    run_length: int,
    excluded: tuple[tuple[int, int], ...],
    interval_index: int,
) -> int | None:
    index = start
    current_interval = interval_index
    while index < len(content):
        while (
            current_interval < len(excluded) and index >= excluded[current_interval][1]
        ):
            current_interval += 1
        if current_interval < len(excluded) and index >= excluded[current_interval][0]:
            return None
        found = content.find(b"`", index)
        if found < 0:
            return None
        end = _same_byte_run_end(content, found, 0x60)
        if end - found == run_length:
            return end
        index = end
    return None


def _same_byte_run_end(content: bytes, start: int, value: int | None) -> int:
    end = start
    while value is not None and end < len(content) and content[end] == value:
        end += 1
    return end


def _is_escaped(content: bytes, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and content[index] == 0x5C:
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _looks_like_scheme(path: bytes) -> bool:
    colon = path.find(b":")
    slash = path.find(b"/")
    if colon <= 0 or (slash >= 0 and slash < colon):
        return False
    prefix = path[:colon]
    return (0x41 <= prefix[0] <= 0x5A or 0x61 <= prefix[0] <= 0x7A) and all(
        0x41 <= item <= 0x5A
        or 0x61 <= item <= 0x7A
        or 0x30 <= item <= 0x39
        or item in b"+-."
        for item in prefix[1:]
    )


def _is_ascii_control(value: int) -> bool:
    return value < 0x20 or value == 0x7F


def _is_ascii_space_or_control(value: int) -> bool:
    return value <= 0x20 or value == 0x7F


def _edge_key(edge: ArtifactRelationshipEdge) -> tuple[bytes, int, int, bytes, bytes]:
    return (
        edge.source_path.encode("utf-8"),
        edge.destination_start_utf8_byte,
        edge.destination_end_utf8_byte,
        edge.target_path.encode("utf-8"),
        (edge.fragment or "").encode("utf-8"),
    )


def _core_review_domain(path: str) -> ArtifactReviewDomain:
    if path in _CORE_PATH_SET:
        domain = artifact_domain(path)
        if domain is not None:
            return domain
    raise ArtifactRelationshipError(ArtifactRelationshipErrorCode.INVALID_SCHEMA)


def _operation_primary_path(operation: object) -> str:
    if type(operation) is ArtifactRenameOperation:
        return operation.source_path
    return operation.path


def _operation_participating_paths(operation: object) -> tuple[str, ...]:
    if type(operation) is ArtifactRenameOperation:
        return operation.source_path, operation.destination_path
    return (operation.path,)


def _derive_review_facts(
    change_set: ArtifactChangeSet,
    *,
    attached_core_paths: tuple[str, ...],
    base_edges: tuple[ArtifactRelationshipEdge, ...],
    projected_edges: tuple[ArtifactRelationshipEdge, ...],
) -> tuple[ArtifactReviewFact, ...]:
    facts: set[ArtifactReviewFact] = set()
    for path in attached_core_paths:
        facts.add(
            ArtifactReviewFact(
                domain=_core_review_domain(path),
                reason=ArtifactReviewReason.ATTACHED_CORE_EVIDENCE,
                artifact_path=path,
                operation_index=None,
            )
        )
    all_edges = set(base_edges) | set(projected_edges)
    for operation_index, operation in enumerate(change_set.operations):
        primary_path = _operation_primary_path(operation)
        if operation.artifact_kind is ArtifactKind.MANUSCRIPT:
            facts.add(
                ArtifactReviewFact(
                    domain=ArtifactReviewDomain.TONE_EXECUTION,
                    reason=ArtifactReviewReason.MANUSCRIPT_OPERATION,
                    artifact_path=primary_path,
                    operation_index=operation_index,
                )
            )
        elif type(operation) in {
            ArtifactCreateOperation,
            ArtifactPatchOperation,
            ArtifactReplaceOperation,
        }:
            facts.add(
                ArtifactReviewFact(
                    domain=_core_review_domain(primary_path),
                    reason=ArtifactReviewReason.TOUCHED_CORE,
                    artifact_path=primary_path,
                    operation_index=operation_index,
                )
            )
        participating = set(_operation_participating_paths(operation))
        for edge in all_edges:
            if (
                edge.source_path not in participating
                and edge.target_path not in participating
            ):
                continue
            for endpoint in (edge.source_path, edge.target_path):
                if endpoint in _CORE_PATH_SET:
                    facts.add(
                        ArtifactReviewFact(
                            domain=_core_review_domain(endpoint),
                            reason=ArtifactReviewReason.RELATIONSHIP_CORE_EVIDENCE,
                            artifact_path=endpoint,
                            operation_index=operation_index,
                        )
                    )
    return tuple(sorted(facts, key=_review_key))


def _review_key(
    fact: ArtifactReviewFact,
) -> tuple[str, str, bytes, int]:
    return (
        fact.domain.value,
        fact.reason.value,
        fact.artifact_path.encode("utf-8"),
        -1 if fact.operation_index is None else fact.operation_index,
    )


__all__ = (
    "ARTIFACT_RELATIONSHIP_COMPANION_RULES",
    "ARTIFACT_RELATIONSHIP_SCHEMA_VERSION",
    "MAX_RELATIONSHIP_ARTIFACTS",
    "MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES",
    "MAX_RELATIONSHIP_EDGES",
    "MAX_RELATIONSHIP_STATE_UTF8_BYTES",
    "ArtifactRelationshipEdge",
    "ArtifactRelationshipError",
    "ArtifactRelationshipErrorCode",
    "ArtifactRelationshipKind",
    "ArtifactRelationshipValidation",
    "ArtifactReviewDomain",
    "ArtifactReviewFact",
    "ArtifactReviewReason",
    "artifact_source_operation_rule",
    "validate_artifact_relationships",
)
