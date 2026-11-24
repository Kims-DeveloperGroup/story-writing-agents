"""Causal state and decisions for persistent semantic synchronization obligations."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from .specialist_routing import ArtifactReviewDomain
from .synchronization_assessment import (
    SynchronizationAssessment,
    SynchronizationCensus,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationFinding,
    SynchronizationJudgment,
    require_synchronization_digest,
    require_synchronization_identifier,
    require_synchronization_text,
    require_synchronization_tuple,
)
from .writing_artifacts.catalog import canonical_artifact_kind


class SynchronizationStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    REVALIDATE = "revalidate"
    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"


_TERMINAL_JUDGMENT = {
    SynchronizationStatus.RESOLVED: SynchronizationJudgment.NO_CHANGE,
    SynchronizationStatus.NOT_APPLICABLE: SynchronizationJudgment.NOT_APPLICABLE,
}


@dataclass(frozen=True, slots=True)
class SynchronizationCondition:
    """One immutable obligation, tied to the exact original semantic finding."""

    condition_id: str
    detected_by: SynchronizationEventRef
    changed_path: str
    target_path: str
    domain: ArtifactReviewDomain
    discrepancy: str
    action: str
    resolution: str

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.condition_id)
        if type(self.detected_by) is not SynchronizationEventRef:
            raise TypeError("synchronization condition needs an exact detection reference")
        if type(self.domain) is not ArtifactReviewDomain:
            raise TypeError("synchronization condition needs an exact specialist domain")
        if self.changed_path == self.target_path or any(
            canonical_artifact_kind(path) is None for path in (self.changed_path, self.target_path)
        ):
            raise ValueError("synchronization condition needs two distinct canonical paths")
        for explanation in (self.discrepancy, self.action, self.resolution):
            require_synchronization_text(explanation)

    @classmethod
    def from_finding(
        cls, condition_id: str, detected_by: SynchronizationEventRef, finding: SynchronizationFinding,
    ) -> SynchronizationCondition:
        if finding.judgment is not SynchronizationJudgment.NEEDS_CHANGE:
            raise ValueError("only an observed semantic discrepancy creates a repair condition")
        assert finding.domain is not None and finding.action is not None and finding.resolution is not None
        return cls(
            condition_id, detected_by, finding.changed_path, finding.target_path,
            finding.domain, finding.reason, finding.action, finding.resolution,
        )


@dataclass(frozen=True, slots=True)
class SynchronizationConditionState:
    condition: SynchronizationCondition
    status: SynchronizationStatus
    reason: str
    evidence: SynchronizationEventRef | None = None

    def __post_init__(self) -> None:
        if type(self.condition) is not SynchronizationCondition:
            raise TypeError("synchronization condition state needs an exact condition")
        if type(self.status) is not SynchronizationStatus:
            raise TypeError("synchronization condition state needs an exact status")
        require_synchronization_text(self.reason)
        if self.status in _TERMINAL_JUDGMENT:
            if type(self.evidence) is not SynchronizationEventRef:
                raise ValueError("a completed condition needs an exact source of current repair evidence")
        elif self.evidence is not None:
            raise ValueError("a pending condition cannot retain completed repair evidence")


@dataclass(frozen=True, slots=True)
class SynchronizationTransition:
    """A version links exact causal parents and preserves all their repair conditions."""

    obligation_id: str
    version: int
    parents: tuple[SynchronizationEventRef, ...]
    conditions: tuple[SynchronizationConditionState, ...]
    reason: str

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.obligation_id)
        if type(self.version) is not int or self.version < 1:
            raise ValueError("synchronization transition version must be a positive integer")
        require_synchronization_tuple(self.parents, SynchronizationEventRef)
        require_synchronization_tuple(self.conditions, SynchronizationConditionState)
        if not self.conditions or len({state.condition.condition_id for state in self.conditions}) != len(self.conditions):
            raise ValueError("synchronization transition needs distinct repair condition identities")
        require_synchronization_text(self.reason)

    @property
    def condition_ids(self) -> frozenset[str]:
        return frozenset(state.condition.condition_id for state in self.conditions)


@dataclass(frozen=True, slots=True)
class SynchronizationHead:
    ref: SynchronizationEventRef
    transition: SynchronizationTransition
    current_conditions: tuple[SynchronizationConditionState, ...] | None = None

    @property
    def conditions(self) -> tuple[SynchronizationConditionState, ...]:
        if self.current_conditions is None:
            return self.transition.conditions
        return self.current_conditions


@dataclass(frozen=True, slots=True)
class SynchronizationObligations:
    heads: tuple[SynchronizationHead, ...]

    @property
    def forked_conditions(self) -> frozenset[str]:
        seen: set[str] = set()
        forked: set[str] = set()
        by_id: dict[str, frozenset[str]] = {}
        for head in self.heads:
            transition = head.transition
            ids = transition.condition_ids
            forked.update(ids.intersection(seen))
            seen.update(ids)
            previous = by_id.get(transition.obligation_id)
            if previous is not None:
                forked.update(previous | ids)
            by_id[transition.obligation_id] = ids | (previous or frozenset())
        return frozenset(forked)

    @property
    def blockers(self) -> tuple[str, ...]:
        forked = self.forked_conditions
        blockers = [f"{condition_id}: concurrent synchronization states" for condition_id in sorted(forked)]
        blockers.extend(
            f"{state.condition.condition_id}: {state.status.value}"
            for head in self.heads
            for state in head.conditions
            if state.condition.condition_id not in forked and state.status not in _TERMINAL_JUDGMENT
        )
        return tuple(blockers)

    def require_parents(self, transition: SynchronizationTransition) -> None:
        """Live admission binds the full observed frontier, including overlapping replacements."""
        related = {
            head.ref for head in self.heads
            if head.transition.obligation_id == transition.obligation_id
            or head.transition.condition_ids.intersection(transition.condition_ids)
        }
        if related != set(transition.parents):
            raise ValueError("synchronization transition must include every currently observed related head")

    def blockers_after(self, assessment: SynchronizationAssessment) -> tuple[str, ...]:
        """Evaluate pending conditions for the exact candidate, without changing durable state."""
        forked = self.forked_conditions
        blockers = [f"{condition_id}: concurrent synchronization states" for condition_id in sorted(forked)]
        for head in self.heads:
            for state in head.conditions:
                condition = state.condition
                if condition.condition_id in forked or state.status in _TERMINAL_JUDGMENT:
                    continue
                if not any(
                    finding.judgment in _TERMINAL_JUDGMENT.values() and _addresses(condition, finding)
                    for finding in assessment.findings
                ):
                    blockers.append(f"{condition.condition_id}: candidate does not satisfy the condition")
        return tuple(blockers)


def _addresses(condition: SynchronizationCondition, finding: SynchronizationFinding) -> bool:
    return (
        condition.condition_id in finding.resolves
        and {condition.changed_path, condition.target_path} == set(finding.pair)
        and condition.domain is finding.domain
    )


def _require_detection(
    condition: SynchronizationCondition,
    assessments: Mapping[SynchronizationEventRef, SynchronizationAssessment],
) -> None:
    detected = assessments.get(condition.detected_by)
    if detected is not None:
        for finding in detected.findings:
            if (
                finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
                and finding.pair == (condition.changed_path, condition.target_path)
                and SynchronizationCondition.from_finding(
                    condition.condition_id, condition.detected_by, finding,
                ) == condition
            ):
                return
    raise ValueError("synchronization condition does not match its original evidence")


def _require_resolution(
    state: SynchronizationConditionState,
    assessments: Mapping[SynchronizationEventRef, SynchronizationAssessment],
) -> None:
    assert state.evidence is not None
    assessment = assessments.get(state.evidence)
    if assessment is None or assessment.basis.projected or not any(
        finding.judgment is _TERMINAL_JUDGMENT[state.status] and _addresses(state.condition, finding)
        for finding in assessment.findings
    ):
        raise ValueError("durable synchronization resolution needs matching actual semantic evidence")


def _is_ancestor(
    sought: SynchronizationEventRef, descendants: set[SynchronizationEventRef],
    history: Mapping[SynchronizationEventRef, SynchronizationTransition],
) -> bool:
    visited = set(descendants)
    frontier = list(descendants)
    while frontier:
        for parent in history[frontier.pop()].parents:
            if parent == sought:
                return True
            if parent not in visited:
                visited.add(parent)
                frontier.append(parent)
    return False


def _validate_version(
    transition: SynchronizationTransition,
    history: Mapping[SynchronizationEventRef, SynchronizationTransition],
    assessments: Mapping[SynchronizationEventRef, SynchronizationAssessment],
    known: dict[str, SynchronizationCondition],
) -> None:
    parents = [history[parent] for parent in transition.parents]
    if transition.version != 1 + max((parent.version for parent in parents), default=0):
        raise ValueError("synchronization version does not follow its causal parents")
    if len(parents) > 1:
        parent_refs = set(transition.parents)
        if any(_is_ancestor(ref, parent_refs - {ref}, history) for ref in parent_refs):
            raise ValueError("synchronization merge parents must be distinct live branches")

    inherited: dict[str, set[SynchronizationConditionState]] = defaultdict(set)
    for parent in parents:
        for state in parent.conditions:
            inherited[state.condition.condition_id].add(state)
    if set(inherited) - transition.condition_ids:
        raise ValueError("synchronization replacement discarded inherited repair conditions")

    for state in transition.conditions:
        condition = state.condition
        prior = inherited.get(condition.condition_id, set())
        if not prior:
            if state.status is not SynchronizationStatus.OPEN:
                raise ValueError("a synchronization condition must begin open")
            if known.get(condition.condition_id) != condition:
                if condition.condition_id in known:
                    raise ValueError("synchronization version changed an original condition")
                _require_detection(condition, assessments)
                known[condition.condition_id] = condition
            continue
        if any(item.condition != condition for item in prior):
            raise ValueError("synchronization version changed an inherited condition")
        if state in prior and len(prior) == 1:
            continue
        if state.status in _TERMINAL_JUDGMENT:
            if any(state.evidence == previous.evidence for previous in prior):
                raise ValueError("synchronization closure needs independent current evidence")
            _require_resolution(state, assessments)
        elif state.status is not SynchronizationStatus.REVALIDATE and any(
            previous.status in _TERMINAL_JUDGMENT for previous in prior
        ):
            raise ValueError("a closed condition must be revalidated before it is reopened")


def project_synchronization_obligations(
    transitions: Mapping[SynchronizationEventRef, SynchronizationTransition],
    assessments: Mapping[SynchronizationEventRef, SynchronizationAssessment],
) -> SynchronizationObligations:
    """Fold an exact causal graph without using conversation order or timestamps."""
    followers: dict[SynchronizationEventRef, list[SynchronizationEventRef]] = defaultdict(list)
    waiting: dict[SynchronizationEventRef, int] = {}
    event_ids: set[tuple[str, str]] = set()
    for ref, transition in transitions.items():
        if type(ref) is not SynchronizationEventRef or type(transition) is not SynchronizationTransition:
            raise TypeError("synchronization history needs exact typed transitions")
        identity = (ref.conversation_id, ref.event_id)
        if identity in event_ids:
            raise ValueError("synchronization transition has multiple digests for one event")
        event_ids.add(identity)
        for parent in transition.parents:
            if parent not in transitions or parent == ref:
                raise ValueError("synchronization transition has an unknown or self-referencing parent")
            followers[parent].append(ref)
        waiting[ref] = len(transition.parents)
    ready = deque(ref for ref, count in waiting.items() if count == 0)
    known: dict[str, SynchronizationCondition] = {}
    accepted = 0
    while ready:
        ref = ready.popleft()
        _validate_version(transitions[ref], transitions, assessments, known)
        accepted += 1
        for follower in followers.get(ref, ()):
            waiting[follower] -= 1
            if waiting[follower] == 0:
                ready.append(follower)
    if accepted != len(transitions):
        raise ValueError("synchronization transition history has a causal cycle")

    heads = sorted(
        (ref for ref in transitions if ref not in followers),
        key=lambda ref: (ref.conversation_id, ref.event_id, ref.digest),
    )
    return SynchronizationObligations(tuple(SynchronizationHead(ref, transitions[ref]) for ref in heads))


def _document_map(census: SynchronizationCensus) -> dict[str, SynchronizationDocument]:
    return {item.path: item for item in census.documents}


def revalidate_synchronization_obligations(
    obligations: SynchronizationObligations,
    assessments: Mapping[SynchronizationEventRef, SynchronizationAssessment],
    census: SynchronizationCensus,
    conditions_digest: str | None,
    rules_digest: str,
    task_id: str | None = None,
) -> SynchronizationObligations:
    """Derive a current view; a changed source or semantic condition needs fresh review."""
    if conditions_digest is not None:
        require_synchronization_digest(conditions_digest)
    require_synchronization_digest(rules_digest)
    current = _document_map(census)
    heads: list[SynchronizationHead] = []
    for head in obligations.heads:
        states = []
        for state in head.transition.conditions:
            proof = assessments.get(state.evidence) if state.evidence is not None else None
            if proof is not None:
                documents = _document_map(proof.census)
                paths = (state.condition.changed_path, state.condition.target_path)
                if (
                    not census.complete
                    or any(path not in current or current[path] != documents.get(path) for path in paths)
                or (conditions_digest is not None
                    and (task_id is None or proof.basis.task_id == task_id)
                    and proof.basis.conditions_digest != conditions_digest)
                    or proof.basis.rules_digest != rules_digest
                ):
                    state = replace(
                        state, status=SynchronizationStatus.REVALIDATE, evidence=None,
                        reason="Document revision, applicable conditions, or rules have changed.",
                    )
            states.append(state)
        heads.append(replace(head, current_conditions=tuple(states)))
    return SynchronizationObligations(tuple(heads))
