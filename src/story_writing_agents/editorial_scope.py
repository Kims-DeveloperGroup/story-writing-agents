"""Pure canonical editorial target coverage, without source or workflow authority."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .writing_artifacts.catalog import canonical_artifact_kind


MAX_EDITORIAL_SCOPE_TARGETS = 32


class EditorialScopeKind(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EditorialScopeTarget:
    path: str
    start_line: int | None = None
    end_line: int | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.path) is not str:
            raise TypeError("editorial target path must be text")
        if canonical_artifact_kind(self.path) is None:
            raise ValueError("editorial target must be a canonical writing artifact")
        if self.start_line is None:
            if self.end_line is not None or self.source_sha256 is not None:
                raise ValueError("whole editorial target cannot assert a range source")
        elif (
            type(self.start_line) is not int or type(self.end_line) is not int
            or self.start_line < 1 or self.end_line < self.start_line
            or type(self.source_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None
        ):
            raise ValueError("editorial range requires exact inclusive bounds and source")


def _target_key(target: EditorialScopeTarget) -> tuple:
    return (target.path, target.start_line or 0, target.end_line or 0, target.source_sha256 or "")


def _normalize(targets: tuple[EditorialScopeTarget, ...]) -> tuple[EditorialScopeTarget, ...]:
    whole_paths = {target.path for target in targets if target.start_line is None}
    normalized: list[EditorialScopeTarget] = []
    for target in sorted(targets, key=lambda item: (item.path, item.source_sha256 or "", item.start_line or 0, item.end_line or 0)):
        if target.path in whole_paths:
            if target.start_line is None:
                normalized.append(target)
            continue
        if normalized:
            prior = normalized[-1]
            if (prior.path, prior.source_sha256) == (target.path, target.source_sha256):
                if target.start_line <= prior.end_line + 1:
                    normalized[-1] = EditorialScopeTarget(
                        prior.path, prior.start_line, max(prior.end_line, target.end_line),
                        prior.source_sha256,
                    )
                    continue
        normalized.append(target)
    return tuple(sorted(normalized, key=_target_key))


@dataclass(frozen=True, slots=True)
class EditorialScope:
    kind: EditorialScopeKind
    targets: tuple[EditorialScopeTarget, ...] = ()
    clarification: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not EditorialScopeKind:
            raise TypeError("editorial scope kind is invalid")
        if type(self.targets) is not tuple or any(
            type(target) is not EditorialScopeTarget for target in self.targets
        ):
            raise TypeError("editorial scope targets must be immutable")
        if len(self.targets) > MAX_EDITORIAL_SCOPE_TARGETS or len(set(self.targets)) != len(self.targets):
            raise ValueError("editorial scope targets repeat or exceed the bound")
        if self.kind is EditorialScopeKind.UNKNOWN:
            if self.targets or type(self.clarification) is not str or not self.clarification.strip():
                raise ValueError("unknown editorial scope requires clarification")
            if len(self.clarification.encode("utf-8")) > 32768:
                raise ValueError("editorial scope clarification exceeds the visible limit")
        elif not self.targets or self.clarification is not None:
            raise ValueError("known editorial scope requires canonical targets")
        object.__setattr__(self, "targets", _normalize(self.targets))

    @classmethod
    def unknown(cls, reason: str) -> EditorialScope:
        return cls(EditorialScopeKind.UNKNOWN, clarification=reason)

    @classmethod
    def whole(cls, path: str) -> EditorialScope:
        return cls(EditorialScopeKind.KNOWN, (EditorialScopeTarget(path),))

    @classmethod
    def selected_range(
        cls, path: str, start_line: int, end_line: int, source_sha256: str
    ) -> EditorialScope:
        return cls(EditorialScopeKind.KNOWN, (
            EditorialScopeTarget(path, start_line, end_line, source_sha256),
        ))


def _intersection(a: EditorialScopeTarget, b: EditorialScopeTarget) -> EditorialScopeTarget | None:
    if a.path != b.path:
        return None
    if a.start_line is None:
        return b
    if b.start_line is None:
        return a
    if a.source_sha256 != b.source_sha256:
        raise ValueError("editorial range anchors are incomparable")
    start, end = max(a.start_line, b.start_line), min(a.end_line, b.end_line)
    return None if end < start else EditorialScopeTarget(a.path, start, end, a.source_sha256)


def intersect_editorial_scopes(
    a: EditorialScope, b: EditorialScope
) -> tuple[EditorialScopeTarget, ...] | None:
    """None means unknown, rather than a fabricated disjoint or overlapping scope."""
    if a.kind is EditorialScopeKind.UNKNOWN or b.kind is EditorialScopeKind.UNKNOWN:
        return None
    found: set[EditorialScopeTarget] = set()
    try:
        for left in a.targets:
            for right in b.targets:
                target = _intersection(left, right)
                if target is not None:
                    found.add(target)
    except ValueError:
        return None
    return tuple(sorted(found, key=_target_key))


def editorial_scope_relation(a: EditorialScope, b: EditorialScope) -> str:
    intersection = intersect_editorial_scopes(a, b)
    if intersection is None:
        return "unknown"
    if not intersection:
        return "disjoint"
    return "same" if (editorial_scope_contains(a, b) is True
                      and editorial_scope_contains(b, a) is True) else "overlap"


def _subtract(
    intervals: list[tuple[int, int | None]], start: int, end: int | None
) -> list[tuple[int, int | None]]:
    remaining = []
    for low, high in intervals:
        if (high is not None and high < start) or (end is not None and end < low):
            remaining.append((low, high))
            continue
        if low < start:
            remaining.append((low, start - 1))
        if end is not None and (high is None or end < high):
            remaining.append((end + 1, high))
    return remaining


def editorial_scope_contains(outer: EditorialScope, inner: EditorialScope) -> bool | None:
    if outer.kind is EditorialScopeKind.UNKNOWN or inner.kind is EditorialScopeKind.UNKNOWN:
        return None
    for target in inner.targets:
        intervals = [(target.start_line or 1, target.end_line)]
        for covering in outer.targets:
            if covering.path != target.path:
                continue
            try:
                intersection = _intersection(target, covering)
            except ValueError:
                return None
            if intersection is not None:
                intervals = _subtract(intervals, intersection.start_line or 1, intersection.end_line)
        if intervals:
            return False
    return True


@dataclass(frozen=True, slots=True)
class EditorialScopeCoverage:
    scope: EditorialScope
    exclusions: tuple[EditorialScopeTarget, ...] = ()

    def __post_init__(self) -> None:
        if type(self.scope) is not EditorialScope or type(self.exclusions) is not tuple:
            raise TypeError("editorial coverage must be immutable scope values")
        if len(self.exclusions) > MAX_EDITORIAL_SCOPE_TARGETS or any(
            type(target) is not EditorialScopeTarget for target in self.exclusions
        ):
            raise ValueError("editorial exclusions are invalid or exceed the bound")
        if len(set(self.exclusions)) != len(self.exclusions):
            raise ValueError("editorial exclusions repeat")
        for target in self.exclusions:
            inner = EditorialScope(EditorialScopeKind.KNOWN, (target,))
            if editorial_scope_contains(self.scope, inner) is not True:
                raise ValueError("editorial exclusion must be exact declared coverage")
        object.__setattr__(self, "exclusions", tuple(sorted(self.exclusions, key=_target_key)))

    @property
    def status(self) -> str:
        if self.scope.kind is EditorialScopeKind.UNKNOWN:
            return "unknown"
        anchors: dict[str, set[str]] = {}
        for target in (*self.scope.targets, *self.exclusions):
            if target.source_sha256 is not None:
                anchors.setdefault(target.path, set()).add(target.source_sha256)
        if any(len(values) > 1 for values in anchors.values()):
            return "unknown"
        for target in self.scope.targets:
            intervals = [(target.start_line or 1, target.end_line)]
            for exclusion in self.exclusions:
                if exclusion.path != target.path:
                    continue
                intersection = _intersection(target, exclusion)
                if intersection is not None:
                    intervals = _subtract(intervals, intersection.start_line or 1, intersection.end_line)
            if intervals:
                return "partial" if self.exclusions else "active"
        return "replaced"

    def replaces(self, scope: EditorialScope) -> EditorialScopeCoverage:
        intersection = intersect_editorial_scopes(self.scope, scope)
        if intersection is None or not intersection:
            raise ValueError("replacement requires exact overlapping declared coverage")
        if self.status == "unknown":
            raise ValueError("replacement requires clarified residual source mapping")
        consumed = []
        for target in intersection:
            intervals = [(target.start_line or 1, target.end_line)]
            for exclusion in self.exclusions:
                overlap = _intersection(target, exclusion)
                if overlap is not None:
                    intervals = _subtract(intervals, overlap.start_line or 1, overlap.end_line)
            if not intervals:
                continue
            if target.start_line is None:
                consumed.append(target)
            else:
                consumed.extend(EditorialScopeTarget(target.path, start, end, target.source_sha256)
                                for start, end in intervals)
        if not consumed:
            raise ValueError("replacement target is already consumed")
        exclusions = tuple(sorted(set(self.exclusions).union(consumed), key=_target_key))
        return EditorialScopeCoverage(self.scope, exclusions)


def _object(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("editorial scope object has invalid current fields")
    return value


def editorial_scope_target_to_dict(target: EditorialScopeTarget) -> dict:
    return {"path": target.path, "start_line": target.start_line,
            "end_line": target.end_line, "source_sha256": target.source_sha256}


def editorial_scope_target_from_dict(value: object) -> EditorialScopeTarget:
    data = _object(value, {"path", "start_line", "end_line", "source_sha256"})
    return EditorialScopeTarget(**data)


def editorial_scope_to_dict(scope: EditorialScope) -> dict:
    return {"kind": scope.kind.value,
            "targets": [editorial_scope_target_to_dict(target) for target in scope.targets],
            "clarification": scope.clarification}


def editorial_scope_from_dict(value: object) -> EditorialScope:
    data = _object(value, {"kind", "targets", "clarification"})
    if type(data["targets"]) is not list:
        raise TypeError("editorial scope targets must be an array")
    return EditorialScope(EditorialScopeKind(data["kind"]),
                          tuple(editorial_scope_target_from_dict(item) for item in data["targets"]),
                          data["clarification"])


def editorial_coverage_to_dict(coverage: EditorialScopeCoverage) -> dict:
    return {"scope": editorial_scope_to_dict(coverage.scope),
            "exclusions": [editorial_scope_target_to_dict(item) for item in coverage.exclusions]}


def editorial_coverage_relation(a: EditorialScopeCoverage, b: EditorialScopeCoverage) -> str:
    """Compare remaining coverage, including anchors on holes in whole targets."""
    if a.status == "unknown" or b.status == "unknown":
        return "unknown"
    remaining = []
    for coverage in (a, b):
        by_path: dict[str, tuple[set[str], list[tuple[int, int | None]]]] = {}
        for target in coverage.scope.targets:
            anchors, intervals = by_path.setdefault(target.path, (set(), []))
            if target.source_sha256 is not None:
                anchors.add(target.source_sha256)
            intervals.append((target.start_line or 1, target.end_line))
        for target in coverage.exclusions:
            anchors, intervals = by_path[target.path]
            if target.source_sha256 is not None:
                anchors.add(target.source_sha256)
            by_path[target.path] = (anchors, _subtract(intervals, target.start_line or 1, target.end_line))
        remaining.append(by_path)
    overlap = False
    for path in remaining[0].keys() & remaining[1].keys():
        left_anchors, left = remaining[0][path]
        right_anchors, right = remaining[1][path]
        if left_anchors and right_anchors and left_anchors != right_anchors:
            return "unknown"
        for low, high in left:
            for start, end in right:
                if (high is None or start <= high) and (end is None or low <= end):
                    overlap = True
    if not overlap:
        return "disjoint"
    return "same" if remaining[0] == remaining[1] else "overlap"


__all__ = (
    "EditorialScopeKind", "EditorialScopeTarget", "EditorialScope", "EditorialScopeCoverage",
    "MAX_EDITORIAL_SCOPE_TARGETS", "intersect_editorial_scopes", "editorial_scope_relation",
    "editorial_scope_contains", "editorial_scope_target_to_dict", "editorial_scope_target_from_dict",
    "editorial_scope_to_dict", "editorial_scope_from_dict", "editorial_coverage_to_dict",
    "editorial_coverage_relation",
)
