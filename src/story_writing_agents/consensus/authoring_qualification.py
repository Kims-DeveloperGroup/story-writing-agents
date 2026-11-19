"""Qualify exact Consensus lineage and current USER/material source values."""

import hashlib
from dataclasses import asdict

from ..authoring_admission import (
    AdmissionSource,
    AdmissionSourceKind,
    AdmissionStatus,
    AuthoringAssessment,
    AuthoringAdmissionBasis,
    AuthoringAdmissionNeeded,
    admission_digest,
    qualify_authoring_assessment,
)
from ..core_document_readiness import AuthoringTarget
from ..writing_artifacts.references import ArtifactContentSnapshot
from .contracts import ConsensusExecutionIdentity, ConsensusMaterialHandle
from .serialization import ConsensusInputKind, ConsensusStoredWorkflow


def consensus_lineage_changed(original: ConsensusStoredWorkflow, material: ConsensusMaterialHandle, direction_material: ConsensusMaterialHandle | None) -> bool:
    return (
        original.submission.input_material != material or original.submission.authoring_direction != direction_material
    )

def record_consensus_lineage_hop(original: ConsensusStoredWorkflow, seen: set[str]) -> None:
    if original.submission.workflow_id in seen or original.submission.continuation is None:
        raise ValueError("consensus input lineage is invalid")
    seen.add(original.submission.workflow_id)

def consensus_admission_inputs(input_kind: ConsensusInputKind, material: ConsensusMaterialHandle, direction_material: ConsensusMaterialHandle | None, ruling_material: ConsensusMaterialHandle | None) -> tuple[tuple[str, ConsensusMaterialHandle | None, AdmissionSourceKind], ...]:
    return (
        ("user_direction", direction_material, AdmissionSourceKind.USER),
        ("ruling_input", ruling_material, AdmissionSourceKind.USER),
        ("user_input" if input_kind is ConsensusInputKind.PROMPT and material.locator.startswith("candidate:") else "input_material", material,
         AdmissionSourceKind.USER if input_kind is ConsensusInputKind.PROMPT and material.locator.startswith("candidate:") else AdmissionSourceKind.MATERIAL),
    )

def manuscript_admission_source(handle: ConsensusMaterialHandle, snapshots: tuple[ArtifactContentSnapshot, ...], facts: list[AdmissionSource]) -> tuple[str, str] | AuthoringAdmissionNeeded:
    if len(snapshots) != 1 or snapshots[0].source.source_sha256 != handle.digest:
        return AuthoringAdmissionNeeded(AdmissionStatus.NEEDS_INFORMATION,
            ("Select the current manuscript bytes before assessing authoring direction.",), tuple(facts))
    text = snapshots[0].read_text()
    revision_digest = admission_digest(asdict(snapshots[0].source))
    return text, revision_digest

def consensus_admission_source_fact(alias: str, kind: AdmissionSourceKind, handle: ConsensusMaterialHandle, text: str, revision_digest: str) -> AdmissionSource:
    encoded = text.encode("utf-8", "strict")
    if hashlib.sha256(encoded).hexdigest() != handle.digest:
        raise ValueError("consensus admission material changed")
    return AdmissionSource(alias, kind, handle.locator, revision_digest,
        "literal_user" if kind is AdmissionSourceKind.USER else "input_only", len(encoded))

def qualify_consensus_authoring_sources(identity: ConsensusExecutionIdentity, workflow_id: str, material: ConsensusMaterialHandle, assessment: AuthoringAssessment | None, *, input_kind: ConsensusInputKind, direction_material: ConsensusMaterialHandle | None, ruling_material: ConsensusMaterialHandle | None, source_revision_id: str, authoring_target: AuthoringTarget | None, facts: list[AdmissionSource]) -> AuthoringAdmissionBasis | AuthoringAdmissionNeeded:
    if (ruling_material is not None and assessment is not None and assessment.status is AdmissionStatus.SUFFICIENT
        and (assessment.direction is None or assessment.creative_scope is None
             or "ruling_input" not in assessment.direction.source_ids
             or "ruling_input" not in assessment.creative_scope.source_ids)):
        return AuthoringAdmissionNeeded(AdmissionStatus.NEEDS_INFORMATION, (
            "Assess the new USER ruling using ruling_input while retaining the original material and direction.",), tuple(facts))
    request = {"input": material.digest, "direction": None if direction_material is None else direction_material.digest,
               "ruling": None if ruling_material is None else ruling_material.digest}
    scope = {"session_id": identity.session_id, "conversation_id": identity.conversation_id,
             "workflow_id": workflow_id, "input_kind": input_kind.value, "authoring_target": None if authoring_target is None else authoring_target.value, "input_locator": material.locator,
             "direction_locator": None if direction_material is None else direction_material.locator,
             "ruling_locator": None if ruling_material is None else ruling_material.locator,
             "source_revision_id": source_revision_id, "intent": "authoring"}
    return qualify_authoring_assessment(assessment, sources=tuple(facts), request_digest=admission_digest(request),
        task_id=workflow_id, task_revision=int(source_revision_id.removeprefix("r")) + (2 if ruling_material is not None else 1),
        scope_digest=admission_digest(scope))


__all__ = (
    "consensus_lineage_changed",
    "record_consensus_lineage_hop",
    "consensus_admission_inputs",
    "manuscript_admission_source",
    "consensus_admission_source_fact",
    "qualify_consensus_authoring_sources",
)
