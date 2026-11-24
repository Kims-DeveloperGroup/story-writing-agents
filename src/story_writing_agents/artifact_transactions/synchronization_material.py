"""Bind semantic impact inspection to an exact artifact change set."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType

from ..core_document_readiness import AuthoringTarget, DEPENDENCIES
from ..synchronization_assessment import (
    SynchronizationAbsence,
    SynchronizationAbsenceOrigin,
    SynchronizationAssessment,
    SynchronizationCensus,
    SynchronizationDocument,
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

    @property
    def read_paths(self) -> frozenset[str]:
        if self.current_state_digest is not None:
            return frozenset(path for condition in self.selected_conditions
                             for path in (condition.changed_path, condition.target_path)
                             if path in self.current_digests)
        return frozenset(self.current_digests)

    def provider_input(self, obligations: SynchronizationObligations) -> dict[str, object]:
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
