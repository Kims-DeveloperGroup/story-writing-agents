"""Check whole-work rule revision authority against the current artifact inventory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256

from .synchronization_material import capture_narrative_rule_impact_material
from ..conversations.reducers.narrative_rule_revision_history import (
    narrative_rule_revision_task,
    narrative_rule_revision_task_for_origin,
)
from ..conversations.reducers.synchronization_repair_history import (
    project_synchronization_repair_work,
    require_synchronization_repair_scope,
    synchronization_repair_selection_for_task,
)
from ..conversations.session_context import SessionHistorySnapshot
from ..core_document_readiness import AuthoringTarget
from ..narrative_rule_revision import NarrativeRuleRevisionSelection
from ..specialist_routing import ArtifactReviewDomain, AuthoringActivity, AuthoringIntent, AuthoringScope
from ..synchronization_assessment import (
    SynchronizationAssessment,
    SynchronizationEventRef,
    SynchronizationJudgment,
    narrative_rule_impact_complete,
)
from ..synchronization_repair import SynchronizationRepairSelection
from ..writing_artifacts.change_sets import ArtifactChangeSet, ArtifactRenameOperation


RULES_PATH = AuthoringTarget.NARRATIVE_RULES.value


def established_rules_digest(current: Mapping[str, bytes]) -> str | None:
    rules = current.get(RULES_PATH)
    return "sha256:" + sha256(rules).hexdigest() if rules and rules.strip() else None


def current_narrative_impacts(
    snapshot: SessionHistorySnapshot, current: Mapping[str, bytes], *, complete_only: bool = False,
) -> tuple[tuple[SynchronizationEventRef, SynchronizationAssessment], ...]:
    history = snapshot.synchronization()
    if not history.complete:
        return ()
    rules_digest = established_rules_digest(current)
    events = snapshot.events()
    verified = []
    for ref, assessment in history.assessments:
        owner = assessment.basis.narrative_rule_revision
        if owner is None or (complete_only and not narrative_rule_impact_complete(assessment)):
            continue
        binding = narrative_rule_revision_task_for_origin(events, owner)
        if binding is None or binding.selection.rules_digest != rules_digest or binding.selection.impact is not None:
            continue
        material = capture_narrative_rule_impact_material(current, binding.selection, owner)
        if (assessment.census == material.census
                and assessment.basis.result_state_digest == material.current_state_digest
                and assessment.basis.narrative_rule_line_counts == material.narrative_impact_line_counts):
            verified.append((ref, assessment))
    return tuple(verified)


def narrative_rule_revision_choices(
    snapshot: SessionHistorySnapshot, current: Mapping[str, bytes],
) -> tuple[NarrativeRuleRevisionSelection, ...]:
    choices = []
    events = snapshot.events()
    for ref, assessment in current_narrative_impacts(snapshot, current, complete_only=True):
        origin = assessment.basis.narrative_rule_revision
        assert origin is not None
        binding = narrative_rule_revision_task_for_origin(events, origin)
        assert binding is not None
        choices.append(replace(binding.selection, impact=ref))
    return tuple(choices)


def validate_narrative_rule_selection(
    snapshot: SessionHistorySnapshot, current: Mapping[str, bytes], selection: NarrativeRuleRevisionSelection,
) -> None:
    if selection.rules_digest != established_rules_digest(current):
        raise ValueError("The selected narrative rules changed. Review the current revision before selecting a direction.")
    if selection.impact is None:
        return
    assessment = dict(current_narrative_impacts(snapshot, current, complete_only=True)).get(selection.impact)
    if assessment is None or assessment.basis.narrative_rule_revision is None:
        raise ValueError("The narrative-rule revision requires complete current whole-work impact evidence.")
    origin = narrative_rule_revision_task_for_origin(snapshot.events(), assessment.basis.narrative_rule_revision)
    if origin is None or origin.selection != replace(selection, impact=None):
        raise ValueError("The approved rule direction or its scope differs from the recorded impact review.")


def changed_artifact_paths(change_set: ArtifactChangeSet) -> set[str]:
    return {
        path for item in change_set.operations
        for path in ((item.source_path, item.destination_path) if type(item) is ArtifactRenameOperation else (item.path,))
    }


def validate_narrative_rule_scope(
    selection: NarrativeRuleRevisionSelection, scope: AuthoringScope, paths: tuple[str, ...],
) -> None:
    if scope.schema_version != 3 or set(scope.target_paths) != set(paths) or ArtifactReviewDomain.TONE_EXECUTION not in scope.domains:
        raise ValueError("Narrative-rule work must retain its exact reviewed scope and Execution ownership.")
    if selection.impact is None and (scope.intent is not AuthoringIntent.REVIEW_ONLY or scope.activity is not AuthoringActivity.REVIEW):
        raise ValueError("Whole-work rule impact review has read-only authority.")
    if selection.impact is not None and scope.intent is not AuthoringIntent.AUTHORING:
        raise ValueError("The approved rule revision must use its exact rule-authoring scope.")


def retained_narrative_rule_repair_direction(
    snapshot: SessionHistorySnapshot, current: Mapping[str, bytes], selection: SynchronizationRepairSelection,
    *, active_application_decision: SynchronizationEventRef | None = None,
) -> NarrativeRuleRevisionSelection | None:
    if selection.repair_path != RULES_PATH or established_rules_digest(current) is None:
        return None
    digests = {path: sha256(content).hexdigest() for path, content in current.items()}
    history = snapshot.synchronization().with_current_artifact_digests(digests)
    events = snapshot.events()
    work = require_synchronization_repair_scope(selection, project_synchronization_repair_work(
        events, history, active_application_decision=active_application_decision,
    ))
    source = narrative_rule_revision_task(
        events, selection.application.conversation_id, work.task_id, task_revision=work.task_revision,
    )
    if source is None or source.selection.impact is None:
        return None
    return source.selection if any(
        assessment.basis.application is not None
        and assessment.basis.application.result == selection.application
        and any(document.path == RULES_PATH and document.digest == digests[RULES_PATH]
                for document in assessment.census.documents)
        for _, assessment in history.assessments
    ) else None


def require_narrative_rule_candidate_authority(
    snapshot: SessionHistorySnapshot, current: Mapping[str, bytes], conversation_id: str,
    scope: AuthoringScope | None, change_set: ArtifactChangeSet,
    *, active_application_decision: SynchronizationEventRef | None = None,
) -> None:
    paths = changed_artifact_paths(change_set)
    if RULES_PATH not in paths or established_rules_digest(current) is None:
        return
    if paths != {RULES_PATH} or scope is None or scope.target_paths != (RULES_PATH,):
        raise ValueError("A revision to established narrative rules needs its own single-document candidate.")
    events = snapshot.events()
    binding = narrative_rule_revision_task(events, conversation_id, scope.task_id, task_revision=scope.task_revision)
    if binding is not None and binding.selection.impact is not None:
        validate_narrative_rule_scope(binding.selection, scope, (RULES_PATH,))
        validate_narrative_rule_selection(snapshot, current, binding.selection)
        return
    owner_tail = max((event.sequence for event in events if event.conversation_id == conversation_id), default=0)
    repair = synchronization_repair_selection_for_task(
        events, conversation_id, scope.task_id, before_sequence=owner_tail + 1,
    )
    if repair is not None and retained_narrative_rule_repair_direction(
        snapshot, current, repair, active_application_decision=active_application_decision,
    ) is not None:
        return
    raise ValueError("Changing established narrative rules requires the exact user-selected direction and whole-work impact review.")


def validate_provisional_rule_use(
    snapshot: SessionHistorySnapshot, current: Mapping[str, bytes], source_conversation: str,
    source_scope: AuthoringScope | None, change_set: ArtifactChangeSet, target_path: str,
) -> None:
    if established_rules_digest(current) is None:
        return
    require_narrative_rule_candidate_authority(snapshot, current, source_conversation, source_scope, change_set)
    assert source_scope is not None
    source = narrative_rule_revision_task(
        snapshot.events(), source_conversation, source_scope.task_id, task_revision=source_scope.task_revision,
    )
    impact = None if source is None else dict(current_narrative_impacts(snapshot, current, complete_only=True)).get(source.selection.impact)
    if impact is None or not any(
        finding.target_path == target_path and finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
        for finding in impact.findings
    ):
        raise ValueError("Provisional rule material is available only for an affected document in its verified plan.")
