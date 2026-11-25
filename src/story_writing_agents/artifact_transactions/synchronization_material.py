"""Bind semantic impact inspection to an exact artifact change set."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType

from ..core_document_readiness import AuthoringTarget, DEPENDENCIES
from ..narrative_rule_revision import NarrativeRuleRevisionSelection
from ..synchronization_assessment import (
    NARRATIVE_RULES_PATH,
    SynchronizationAbsence,
    SynchronizationAbsenceOrigin,
    SynchronizationAssessment,
    SynchronizationCensus,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationRead,
)
from ..synchronization_obligations import (
    SynchronizationCondition, SynchronizationObligations, SynchronizationStatus,
)
from ..writing_artifacts.catalog import CORE_ARTIFACT_PATHS, canonical_artifact_kind
from ..writing_artifacts.change_sets import (
    ArtifactChangeSet,
    ArtifactRenameOperation,
    artifact_state_digest,
    project_artifact_change_set,
)


@dataclass(frozen=True, slots=True)
class SynchronizationMaterial:
    """Exact projected identities plus the current paths available to guarded READ."""

    census: SynchronizationCensus
    current_digests: Mapping[str, str] = field(repr=False)
    prior_reads: tuple[SynchronizationRead, ...] = ()
    prior_read_text: Mapping[str, str] = field(default_factory=dict, repr=False)
    current_state_digest: str | None = None
    selected_conditions: tuple[SynchronizationCondition, ...] = ()
    absence_origins: tuple[SynchronizationAbsenceOrigin, ...] = ()

    narrative_impact: NarrativeRuleRevisionSelection | None = None
    narrative_impact_request: SynchronizationEventRef | None = None
    narrative_impact_line_counts: tuple[tuple[str, int], ...] = ()
    narrative_impact_retained_targets: tuple[str, ...] = ()

    @property
    def read_paths(self) -> frozenset[str]:
        if self.current_state_digest is not None and self.narrative_impact is None:
            return frozenset(path for condition in self.selected_conditions
                             for path in (condition.changed_path, condition.target_path)
                             if path in self.current_digests)
        return frozenset(self.current_digests)

    def provider_input(self, obligations: SynchronizationObligations) -> dict[str, object]:
        if self.narrative_impact is not None:
            return self._narrative_impact_input()
        selected = set(self.selected_conditions)
        selected_paths = {path for condition in selected
                          for path in (condition.changed_path, condition.target_path)}
        return {
            "changes": self.census.changes,
            "projected_candidate_source_id": "candidate" if self.current_state_digest is None else None,
            "targets": [
                {
                    "path": item.path,
                    "projected_digest": item.digest,
                    "current_read_available": item.path in self.current_digests,
                    "construction": item.absence is SynchronizationAbsence.INITIAL,
                }
                for item in self.census.documents
                if self.current_state_digest is None or item.path in selected_paths
            ],
            "obligations": [
                {
                    "id": state.condition.condition_id,
                    "changed_path": state.condition.changed_path,
                    "target_path": state.condition.target_path,
                    "domain": state.condition.domain.value,
                    "status": state.status.value,
                    "action": state.condition.action,
                    "resolution": state.condition.resolution,
                }
                for head in obligations.heads for state in head.conditions
                if {state.condition.changed_path, state.condition.target_path}.intersection(self.census.changes)
                and (self.current_state_digest is None or state.condition in selected)
            ],
            "current_review_evidence": [
                {"id": earlier_read_id(item), "path": item.document.path,
                 "start_line": item.start_line, "end_line": item.end_line,
                 "content": self.prior_read_text[earlier_read_id(item)]}
                for item in self.prior_reads
            ],
            "absence_evidence": [
                {"id": origin.receipt_id, "path": origin.path, "condition_id": origin.condition_id}
                for origin in self.absence_origins
            ],
            "instructions": (
                "Inspect the effect of each changed document on the other core documents "
                "and potentially related manuscripts, including those outside the current "
                "document plan. Use the exact projected candidate for changed bytes and the "
                "guarded READ action for existing sources. Identify factual and narrative-rule "
                "effects and the detail appropriate to each target. Report missing or unread "
                "evidence explicitly and record concrete discrepancies and required follow-up."
            ) if self.current_state_digest is None else (
                "Recheck the selected obligations against the exact current physical documents. "
                "Cite guarded READ evidence for each document and the responsible specialist's current review. "
                "A proposed path that was never applied can be considered not applicable when the "
                "absence was verified and its absence evidence ID is supplied. Explain the effect on the present document. "
                "Identify the exact condition addressed by each finding. This review does not change any document."
            ),
        }


    def _narrative_impact_input(self) -> dict[str, object]:
        selection = self.narrative_impact
        assert selection is not None
        counts = dict(self.narrative_impact_line_counts)
        return {
            "changes": self.census.changes,
            "current_state_digest": self.current_state_digest,
            "reviewed_target_paths": self.narrative_impact_retained_targets,
            "approved_narrative_revision": {
                "rules_digest": selection.rules_digest,
                "direction": selection.direction,
                "features": tuple(feature.value for feature in selection.features),
                "preserve": selection.preserve,
                "exceptions": selection.exceptions,
            },
            "targets": [
                {"path": item.path, "current_digest": item.digest, "line_count": counts.get(item.path)}
                for item in self.census.documents
            ],
            "instructions": (
                "Inspect the approved direction against the complete current work inventory. "
                "Prior complete findings still cover the listed reviewed targets on this unchanged basis. "
                "Review the rules and remaining targets, including distant manuscripts. "
                "Use guarded READ over each present source in full, combining exact ranges where needed. "
                "For each rules-to-target pair, cite exact READ IDs and the responsible specialist reply. "
                "Set the responsible domain for every known judgment. Record needs_change, no_change, "
                "or not_applicable with precise reasons and applicable scope. "
                "Report unread and missing targets explicitly. Preserve approved unchanged features and exceptions. "
                "This review grants no permission to edit artifacts or resolve existing obligations."
            ),
        }


def capture_narrative_rule_impact_material(
    current: Mapping[str, bytes],
    selection: NarrativeRuleRevisionSelection,
    request: SynchronizationEventRef,
) -> SynchronizationMaterial:
    """Capture the complete physical inventory for a pre-authoring, review-only direction."""
    state_digest = artifact_state_digest(current)
    digests = {path: sha256(content).hexdigest() for path, content in current.items()}
    if selection.impact is not None or selection.rules_digest != f"sha256:{digests.get(NARRATIVE_RULES_PATH)}":
        raise ValueError("narrative-rule impact review requires the approved current rules source")
    documents = tuple(
        SynchronizationDocument(path, digests[path]) if path in digests
        else SynchronizationDocument(path, absence=SynchronizationAbsence.ABSENT)
        for path in sorted({*CORE_ARTIFACT_PATHS, *digests})
    )
    counts = tuple(
        (path, _physical_line_count(content))
        for path, content in sorted(current.items())
    )
    return SynchronizationMaterial(
        SynchronizationCensus((NARRATIVE_RULES_PATH,), documents, complete=True),
        MappingProxyType(digests),
        current_state_digest=state_digest,
        narrative_impact=selection,
        narrative_impact_request=request,
        narrative_impact_line_counts=counts,
    )


def _physical_line_count(content: bytes) -> int:
    # Use the artifact READ adapter's CRLF, LF and lone CR record boundaries.
    terminated = content.count(b"\r") + content.count(b"\n") - content.count(b"\r\n")
    return terminated + int(bool(content) and not content.endswith((b"\r", b"\n")))


def earlier_read_id(read: SynchronizationRead) -> str:
    return f"earlier_{read.source.event_id}_{read.receipt_id}"


def capture_synchronization_material(
    current: Mapping[str, bytes],
    change_set: ArtifactChangeSet,
    *,
    requested_paths: tuple[str, ...] = (),
    initial_construction: bool = False,
    previously_applied_paths: frozenset[str] = frozenset(),
) -> SynchronizationMaterial:
    """Project the exact reviewed base and cover both identities of a rename."""
    projected = project_artifact_change_set(current, change_set)
    changes = tuple(sorted({
        path for operation in change_set.operations
        for path in (
            (operation.source_path, operation.destination_path)
            if type(operation) is ArtifactRenameOperation else (operation.path,)
        )
    }))
    paths = sorted({*CORE_ARTIFACT_PATHS, *current, *projected, *requested_paths})
    changed_paths = set(changes)
    initial_paths = (
        {path for path in CORE_ARTIFACT_PATHS
         if path not in changes and not current.get(path) and path not in previously_applied_paths
         and changed_paths.issubset(target.value for target in DEPENDENCIES[AuthoringTarget(path)])}
        if initial_construction
        else set()
    )
    documents = []
    current_digests: dict[str, str] = {}
    for path in paths:
        if canonical_artifact_kind(path) is None:
            continue
        before = current.get(path)
        after = projected.get(path)
        if before is not None:
            current_digests[path] = sha256(before).hexdigest()
        if after is not None:
            absence = SynchronizationAbsence.INITIAL if not after and path in initial_paths else None
            document = SynchronizationDocument(path, sha256(after).hexdigest(), absence=absence)
        else:
            absence = (
                SynchronizationAbsence.INITIAL
                if before is None and path in initial_paths
                else SynchronizationAbsence.ABSENT
            )
            document = SynchronizationDocument(path, None, absence=absence)
        documents.append(document)
    return SynchronizationMaterial(
        SynchronizationCensus(
            changes,
            tuple(documents),
            complete=True,
            prior_documents=tuple(
                SynchronizationDocument(path, current_digests[path])
                for path in changes if path in current_digests
            ),
        ),
        MappingProxyType(current_digests),
    )


def current_review_conditions(
    obligations: SynchronizationObligations, requested_paths: tuple[str, ...],
) -> tuple[SynchronizationCondition, ...]:
    """Select pending conditions within the exact confirmed review-only scope."""
    targets = set(requested_paths)
    forked = obligations.forked_conditions
    return tuple(
        state.condition for head in obligations.heads for state in head.conditions
        if state.status in {SynchronizationStatus.OPEN, SynchronizationStatus.IN_PROGRESS,
                            SynchronizationStatus.REVALIDATE}
        and state.condition.condition_id not in forked
        and {state.condition.changed_path, state.condition.target_path}.issubset(targets)
    )


def capture_current_synchronization_material(
    current: Mapping[str, bytes], conditions: tuple[SynchronizationCondition, ...],
) -> SynchronizationMaterial:
    changes = tuple(sorted({condition.changed_path for condition in conditions}))
    paths = sorted({*CORE_ARTIFACT_PATHS, *current, *(condition.target_path for condition in conditions), *changes})
    digests = {path: sha256(content).hexdigest() for path, content in current.items()}
    documents = tuple(
        SynchronizationDocument(path, digests[path]) if path in digests
        else SynchronizationDocument(path, None, absence=SynchronizationAbsence.ABSENT)
        for path in paths
    )
    return SynchronizationMaterial(
        SynchronizationCensus(changes, documents, complete=True), MappingProxyType(digests),
        current_state_digest=artifact_state_digest(current), selected_conditions=conditions,
    )


def matches_projected_documents(
    assessment: SynchronizationAssessment, projected: Mapping[str, bytes],
) -> bool:
    """Check the report covers the exact whole projected repository, including untouched paths."""
    expected = {document.path: document.digest for document in assessment.census.documents
                if document.digest is not None}
    return expected == {path: sha256(content).hexdigest() for path, content in projected.items()}
