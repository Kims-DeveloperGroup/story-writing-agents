from __future__ import annotations
import hashlib
import unittest
from story_writing_agents.agent_context import planning as planning_module
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextManifest, ContextManifestEntry, ContextPriority, ContextProvenance, ContextScope, ContextSemanticChannel, ContextSourceKind, ContextSubjectKind
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from story_writing_agents.agent_context.contracts import projection_accounting as ctx_projection_accounting


UNSET = object()


AUTHORITY_BY_KIND = {ContextSourceKind.RUNTIME_INPUT: ContextAuthorityKind.REQUEST,
    ContextSourceKind.AGENT_DEFINITION: ContextAuthorityKind.AGENT_DEFINITION,
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY
    ),
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,
}


DEFAULT_CLASSIFICATION = {ContextSourceKind.RUNTIME_INPUT: (
        ContextSemanticChannel.CURRENT_INPUT,
        ContextPriority.P0_REQUIRED,
    ),
    ContextSourceKind.AGENT_DEFINITION: (ContextSemanticChannel.STABLE_BEHAVIOR,
        ContextPriority.P1_STABLE,),
    ContextSourceKind.WRITING_ARTIFACT: (ContextSemanticChannel.FOCUSED_EVIDENCE,
        ContextPriority.P2_WORKING,),
    ContextSourceKind.CONVERSATION: (ContextSemanticChannel.HISTORY,
        ContextPriority.P2_WORKING,),
    ContextSourceKind.ACTIVE_DEBATE: (ContextSemanticChannel.HARD_CONSTRAINT,
        ContextPriority.P2_WORKING,),
    ContextSourceKind.OWN_AGENT_STATE: (ContextSemanticChannel.OPTIONAL_BACKGROUND,
        ContextPriority.P3_OPTIONAL,),
}


def digest(body_utf8: bytes) -> str:
    return f"sha256:{hashlib.sha256(body_utf8).hexdigest()}"


def scope(
    workspace_id: str = "workspace",
    session_id: str = "session",
    subject_kind: ContextSubjectKind = ContextSubjectKind.CONVERSATION, subject_id: str = "c_main",
    agent_id: str = "lead_orchestrator",
) -> ContextScope:
    return ContextScope(
        workspace_id,
        session_id,
        subject_kind,
        subject_id,
        agent_id,
    )


def entry(
    source_id: str,
    body_utf8: bytes, *,
    source_kind: ContextSourceKind = ContextSourceKind.WRITING_ARTIFACT,
    semantic_channel: ContextSemanticChannel | None = None, priority: ContextPriority | None = None,
    ordinal: int = 0, revision: str = "revision-1",
    provenance: ContextProvenance | None = None, content_digest: object = UNSET,
    utf8_bytes: object = UNSET, searchable: object = UNSET,
) -> ContextManifestEntry:
    default_channel, default_priority = DEFAULT_CLASSIFICATION[source_kind]
    selected_priority = priority or default_priority
    return ContextManifestEntry(source_id,
        source_kind, semantic_channel or default_channel,
        selected_priority, ordinal,
        revision, digest(body_utf8) if content_digest is UNSET else content_digest,
        provenance or ContextProvenance(
            AUTHORITY_BY_KIND[source_kind],
            f"sources/{source_id}",),
        len(body_utf8) if utf8_bytes is UNSET else utf8_bytes, searchable=(
            selected_priority in {ContextPriority.P2_WORKING, ContextPriority.P3_OPTIONAL}
            if searchable is UNSET
            else searchable),
    )  # type: ignore[arg-type]


def source(source_id: str,
    body_utf8: bytes = b"x", **entry_overrides: object,
) -> tuple[ContextManifestEntry, ctx_planning_inputs.ContextMaterial]:
    entry = entry(source_id, body_utf8, **entry_overrides)  # type: ignore[arg-type]
    return entry, ctx_planning_inputs.ContextMaterial(entry, body_utf8)


def record(record_id: str,
    body_utf8: bytes, ordinal: int,
) -> ctx_planning_inputs.ContextRecordMaterial:
    return ctx_planning_inputs.ContextRecordMaterial(ctx_planning_inputs.ContextRecordCursor(
            record_id, "records",
            ordinal, ordinal,
            None, None,
            digest(body_utf8),
            len(body_utf8),),
        body_utf8,)


def checkpoint_from_projection(projection: ctx_projection.ContextProjection,
    *, provider_thread_id: str = "thread",
) -> ctx_checkpoints.ContextCheckpointView:
    proposal = projection.checkpoint_proposal
    return ctx_checkpoints.ContextCheckpointView(
        ctx_planning_inputs.ContextCheckpointState.READY, proposal.conditions,
        provider_thread_id, proposal.sources,
        proposal.delta_depth, proposal.catalog_sources,
        proposal.source_alias_high_watermark,)


def policy(**overrides: int) -> ctx_planning_inputs.ContextPlanningPolicy:
    values = {"total_source_utf8_bytes": 64,
        "p0_source_utf8_bytes": 16, "p1_source_utf8_bytes": 8,
        "p2_source_utf8_bytes": 24, "p3_source_utf8_bytes": 16,
        "mandatory_reference_utf8_bytes": 64,
        "max_delta_depth": 16,}
    values.update(overrides)
    return ctx_planning_inputs.ContextPlanningPolicy(**values)


def conditions(policy: ctx_planning_inputs.ContextPlanningPolicy,
    *, scope: ContextScope | None = None,
    provider_id: str = "provider", definition_fingerprint: str | None = None,
    policy_fingerprint: str | None = None, schema_version: int = ctx_planning_inputs.CONTEXT_CHECKPOINT_SCHEMA_VERSION,
) -> ctx_planning_inputs.ContextPlanningConditions:
    return ctx_planning_inputs.ContextPlanningConditions(scope or scope(),
        provider_id,
        definition_fingerprint or digest(b"definition"),
        policy_fingerprint or policy.fingerprint,
        schema_version,)


def ready_checkpoint(conditions: ctx_planning_inputs.ContextPlanningConditions,
    entries: tuple[ContextManifestEntry, ...] = (), *,
    provider_thread_id: str | None = "thread", delta_depth: int = 0,
) -> ctx_checkpoints.ContextCheckpointView:
    catalog_entries = tuple(entry
        for entry in sorted(entries, key=lambda value: value.ordinal) if entry.source_kind
        in {ContextSourceKind.WRITING_ARTIFACT,
            ContextSourceKind.CONVERSATION, ContextSourceKind.ACTIVE_DEBATE,
            ContextSourceKind.OWN_AGENT_STATE,}
    )
    catalog = tuple(ctx_checkpoints.ContextCatalogCursor(entry, f"s{ordinal}")
        for ordinal, entry in enumerate(catalog_entries, start=1))
    return ctx_checkpoints.ContextCheckpointView(
        ctx_planning_inputs.ContextCheckpointState.READY, conditions,
        provider_thread_id, tuple(ctx_checkpoints.ContextCheckpointSource(entry) for entry in entries),
        delta_depth, catalog,
        len(catalog),)


def project(entries: tuple[ContextManifestEntry, ...],
    materials: tuple[ctx_planning_inputs.ContextMaterial, ...], *,
    policy: ctx_planning_inputs.ContextPlanningPolicy | None = None,
    conditions: ctx_planning_inputs.ContextPlanningConditions | None = None,
    checkpoint: ctx_checkpoints.ContextCheckpointView | None = None,
    active_provider_thread_id: str | None = None,
    mandatory_references: tuple[ctx_planning_inputs.MandatoryReferenceReservation, ...] = (), fresh: bool = False,
) -> ctx_projection.ContextProjection:
    selected_policy = policy or policy()
    selected_conditions = conditions or conditions(selected_policy)
    return planning_module.plan_context_projection(ContextManifest(selected_conditions.scope, entries),
        materials, policy=selected_policy,
        conditions=selected_conditions,
        checkpoint=checkpoint or ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.MISSING),
        active_provider_thread_id=active_provider_thread_id,
        mandatory_references=mandatory_references,
        fresh=fresh,)


def reference(ordinal: int,
    *, reference_id: str | None = None,
    source_id: str | None = None, locator: str | None = None,
    utf8_bytes: int = 1, content_digest: str | None = None,
) -> ctx_planning_inputs.MandatoryReferenceReservation:
    return ctx_planning_inputs.MandatoryReferenceReservation(ordinal,
        reference_id or f"reference-{ordinal}", source_id or f"artifact-{ordinal}",
        "revision-1", ContextProvenance(
            ContextAuthorityKind.REFERENCE_AUTHORIZATION,
            locator or f"references/item-{ordinal}.md",),
        content_digest or digest(f"reference-{ordinal}".encode("ascii")),
        utf8_bytes,)


def decision_map(projection: ctx_projection.ContextProjection) -> dict[str, ctx_projection_accounting.ContextSourceDecision]:
    return {decision.entry.source_id: decision for decision in projection.decisions}


def decision_allowed(priority: ContextPriority,
    channel: ContextSemanticChannel, decision: ctx_planning_inputs.ContextDecisionKind,
) -> bool:
    if priority is ContextPriority.P0_REQUIRED:
        return (channel
            in {ContextSemanticChannel.CURRENT_INPUT,
                ContextSemanticChannel.CANDIDATE,}
            and decision is ctx_planning_inputs.ContextDecisionKind.SELECTED)
    if priority is ContextPriority.P1_STABLE:
        return channel is ContextSemanticChannel.STABLE_BEHAVIOR and decision in {
            ctx_planning_inputs.ContextDecisionKind.SELECTED,
            ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,}
    if priority is ContextPriority.P2_WORKING:
        if channel not in {ContextSemanticChannel.HARD_CONSTRAINT,
            ContextSemanticChannel.FOCUSED_EVIDENCE, ContextSemanticChannel.RETRIEVED_EVIDENCE,
            ContextSemanticChannel.HISTORY,}:
            return False
        return not (channel is ContextSemanticChannel.HARD_CONSTRAINT
            and decision in {
                ctx_planning_inputs.ContextDecisionKind.OMITTED_CLASS_BUDGET,
                ctx_planning_inputs.ContextDecisionKind.OMITTED_MATERIAL,}
        )
    return priority is ContextPriority.P3_OPTIONAL and channel in {
        ContextSemanticChannel.OPTIONAL_BACKGROUND, ContextSemanticChannel.RETRIEVED_EVIDENCE,
        ContextSemanticChannel.HISTORY,}


class PlanningTestCase(unittest.TestCase):
    """Common fixed-error assertion for planning contract tests."""

    def assert_planning_error(self,
        callable_: object, code: ctx_planning_inputs.ContextPlanningErrorCode,
    ) -> ctx_planning_inputs.ContextPlanningError:
        with self.assertRaises(ctx_planning_inputs.ContextPlanningError) as caught:
            callable_()  # type: ignore[operator]
        self.assertIs(caught.exception.code, code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        return caught.exception
