"""Capture and validate exact editorial scope facts through live operations."""
from __future__ import annotations

from ..specialist_routing import AuthoringScope
from collections.abc import Callable
from ..conversations import CollaborationConfirmationDecision
from ..conversations import CollaborationConfirmationPayload
from ..conversations import CollaborationProposal
from ..editorial_state import EditorialRecordRef
from ..editorial_scope import EditorialScope
from ..editorial_scope import EditorialScopeKind
from ..editorial_state import EditorialStateIntegrityError
from ..conversations.session_context import SessionHistorySnapshot

__all__ = ('editorial_refs_for_scope', 'editorial_context_for_scope', 'validate_editorial_refs',)


def editorial_refs_for_scope(
    session_id: str,
    scope: AuthoringScope | None,
    snapshot: SessionHistorySnapshot | None = None,
    *,
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
) -> tuple[EditorialRecordRef, ...]:
    if scope is None or scope.schema_version != 3:
        return ()
    snapshot = capture_editorial_snapshot(session_id) if snapshot is None else snapshot
    events = snapshot.events()
    confirmed = {(event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)
        for event in events if type(event.payload) is CollaborationConfirmationPayload
        and event.payload.decision is CollaborationConfirmationDecision.CONFIRM}
    sources = tuple(event.payload for event in events if type(event.payload) is CollaborationProposal
        and event.payload.authoring_scope is not None
        and (event.payload.authoring_scope.task_id, event.payload.authoring_scope.task_revision) == (scope.task_id, scope.task_revision)
        and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest) in confirmed)
    if sources:
        refs = sources[0].basis.editorial_refs
        if any(item.basis.editorial_refs != refs for item in sources):
            raise EditorialStateIntegrityError("confirmed task revision changed its editorial capture")
        return refs
    refs = snapshot.editorial_refs()
    if not scope.target_paths:
        return refs
    state = snapshot.editorial_state()
    records = {record.ref: record for record in state.current_records}
    return tuple(ref for ref in refs if (
        records[ref].draft.scope.kind is EditorialScopeKind.UNKNOWN
        or any(target.path in scope.target_paths
               for target in records[ref].draft.scope.targets)
    ))


def editorial_context_for_scope(
    session_id: str,
    scope: AuthoringScope | None,
    snapshot: SessionHistorySnapshot | None = None,
    *,
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
    qualify_editorial_scopes: Callable[[str, tuple[EditorialScope, ...]], None],
) -> tuple:
    snapshot = capture_editorial_snapshot(session_id) if snapshot is None else snapshot
    refs = editorial_refs_for_scope(session_id, scope, snapshot, capture_editorial_snapshot=capture_editorial_snapshot)
    if not refs:
        return (), None
    confirmed = {(event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)
                 for event in snapshot.events() if type(event.payload) is CollaborationConfirmationPayload
                 and event.payload.decision is CollaborationConfirmationDecision.CONFIRM}
    sources = tuple(event.payload for event in snapshot.events() if type(event.payload) is CollaborationProposal
                    and event.payload.authoring_scope is not None and scope is not None
                    and (event.payload.authoring_scope.task_id, event.payload.authoring_scope.task_revision)
                    == (scope.task_id, scope.task_revision)
                    and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)
                    in confirmed)
    if sources:
        digest = sources[0].basis.editorial_context_digest
        if digest is None or any(item.basis.editorial_context_digest != digest for item in sources):
            raise EditorialStateIntegrityError("confirmed task lost its original editorial topology pin")
    else:
        digest = snapshot.editorial_state().context_digest(refs)
    validate_editorial_refs(session_id, refs, digest, snapshot=snapshot, scope=scope, capture_editorial_snapshot=capture_editorial_snapshot, qualify_editorial_scopes=qualify_editorial_scopes)
    return refs, digest


def validate_editorial_refs(
    session_id: str,
    refs: tuple[EditorialRecordRef, ...],
    expected_context_digest: str | None,
    *,
    snapshot: SessionHistorySnapshot | None = None,
    scope: AuthoringScope | None = None,
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
    qualify_editorial_scopes: Callable[[str, tuple[EditorialScope, ...]], None],
) -> None:
    if not refs:
        if expected_context_digest is not None:
            raise ValueError("empty editorial capture cannot acquire a topology pin")
        return
    snapshot = capture_editorial_snapshot(session_id) if snapshot is None else snapshot
    state = snapshot.editorial_state()
    selected = {record.ref: record for record in state.selected_records}
    if any(ref not in selected for ref in refs):
        raise ValueError("editorial capture is no longer an exact selected USER revision")
    if expected_context_digest is None or state.context_digest(refs) != expected_context_digest:
        raise ValueError("editorial effective coverage or relationships changed; fresh planning is required")
    relevant = tuple(selected[ref] for ref in refs if scope is None or not scope.target_paths
                     or selected[ref].draft.scope.kind is EditorialScopeKind.UNKNOWN
                     or any(target.path in scope.target_paths for target in selected[ref].draft.scope.targets))
    for record in relevant:
        scope_state = state.scope_state(record.ref)
        coverage = scope_state.coverage
        if coverage.status == "unknown" and coverage.scope.kind is EditorialScopeKind.KNOWN:
            raise ValueError("editorial residual mapping needs explicit scope clarification")
        qualification = [coverage.scope]
        if coverage.exclusions:
            qualification.append(EditorialScope(EditorialScopeKind.KNOWN, coverage.exclusions))
        for fact in scope_state.relations:
            if fact.decision.scope is not None:
                qualification.append(fact.decision.scope)
            for ref in (fact.origin_ref, fact.decision.other_ref):
                source = next(item for item in state.revisions if item.ref == ref)
                qualification.append(source.draft.scope)
        qualify_editorial_scopes(session_id, tuple(qualification))
    if scope is not None and any(state.scope_state(record.ref).explicit_conflicts for record in relevant):
        raise ValueError("an exact USER editorial conflict needs explicit relation resolution")
