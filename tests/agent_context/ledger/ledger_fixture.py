'Owned ledger_fixture.py declarations; mutable objects remain fresh per original call.'
from __future__ import annotations
import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from story_writing_agents.agent_context.accounting import ContextComponentBytes, ContextComponentKind, ProviderCallAccounting, ProviderCallKind, ProviderCallState, ProviderPayloadAccounting, ProviderUsage
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextManifest, ContextManifestEntry, ContextPriority, ContextProvenance, ContextScope, ContextSemanticChannel, ContextSourceKind, ContextSubjectKind
from tests.helpers import pristine_initialized_workspace
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints, planning_inputs as ctx_planning_inputs, projection as ctx_projection
from story_writing_agents.agent_context import planning as planning_module
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import checkpoint_wire as owner_checkpoint_wire
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


SOURCE_ROOT = REPOSITORY_ROOT / "src"


PRIVATE_BODY_CANARIES = ("PRIVATE_CURRENT_INPUT_한🙂",
    "PRIVATE_DEFINITION_BODY", "PRIVATE_ARTIFACT_EXCERPT",
    "PRIVATE_REFERENCE_BODY", "PRIVATE_MODEL_ANSWER",
    "PRIVATE_EXCEPTION_TEXT",)


def digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def make_scope(*,
    workspace_id: str = "workspace", session_id: str = "session",
    subject_kind: ContextSubjectKind = ContextSubjectKind.CONVERSATION, subject_id: str = "subject",
    agent_id: str = "lead_orchestrator",) -> ContextScope:
    return ContextScope(workspace_id,
        session_id, subject_kind,
        subject_id,
        agent_id,
    )


def make_entry(source_id: str,
    body: str, *,
    ordinal: int, source_kind: ContextSourceKind,
    channel: ContextSemanticChannel, priority: ContextPriority,
    authority: ContextAuthorityKind, searchable: bool,
) -> tuple[ContextManifestEntry, ctx_planning_inputs.ContextMaterial]:
    body_utf8 = body.encode("utf-8")
    entry = ContextManifestEntry(source_id,
        source_kind, channel,
        priority, ordinal,
        "revision-1", digest_bytes(body_utf8),
        ContextProvenance(authority, f"source-{ordinal}"), len(body_utf8),
        searchable=searchable,)
    return entry, ctx_planning_inputs.ContextMaterial(entry, body_utf8)


def make_projection(scope: ContextScope,
    provider_id: str = "provider", *,
    checkpoint: ctx_checkpoints.ContextCheckpointView | None = None, active_provider_thread_id: str | None = None,
    mandatory_reference_count: int = 0, definition_fingerprint: str | None = None,
    policy: ctx_planning_inputs.ContextPlanningPolicy | None = None,
    schema_version: int = ctx_planning_inputs.CONTEXT_CHECKPOINT_SCHEMA_VERSION,) -> ctx_projection.ContextProjection:
    selected_policy = policy or ctx_planning_inputs.ContextPlanningPolicy()
    conditions = ctx_planning_inputs.ContextPlanningConditions(scope,
        provider_id, definition_fingerprint or digest_bytes(b"definition"),
        selected_policy.fingerprint,
        schema_version,)
    current, current_material = make_entry("request:current",
        PRIVATE_BODY_CANARIES[0], ordinal=0,
        source_kind=ContextSourceKind.RUNTIME_INPUT, channel=ContextSemanticChannel.CURRENT_INPUT,
        priority=ContextPriority.P0_REQUIRED, authority=ContextAuthorityKind.REQUEST,
        searchable=False,)
    definition, definition_material = make_entry("definition:lead",
        PRIVATE_BODY_CANARIES[1], ordinal=1,
        source_kind=ContextSourceKind.AGENT_DEFINITION,
        channel=ContextSemanticChannel.STABLE_BEHAVIOR, priority=ContextPriority.P1_STABLE,
        authority=ContextAuthorityKind.AGENT_DEFINITION,
        searchable=False,)
    artifact, artifact_material = make_entry("artifact:outline",
        PRIVATE_BODY_CANARIES[2], ordinal=2,
        source_kind=ContextSourceKind.WRITING_ARTIFACT,
        channel=ContextSemanticChannel.FOCUSED_EVIDENCE, priority=ContextPriority.P2_WORKING,
        authority=ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
        searchable=True,
    )
    references = tuple(ctx_planning_inputs.MandatoryReferenceReservation(
            ordinal, f"reference-{ordinal}",
            f"artifact-{ordinal}", "revision-1",
            ContextProvenance(ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                f"reference-{ordinal}",),
            digest_bytes(f"reference-{ordinal}".encode("ascii")),
            9 + ordinal,)
        for ordinal in range(1, mandatory_reference_count + 1))
    return planning_module.plan_context_projection(ContextManifest(scope, (current, definition, artifact)),
        (current_material, definition_material, artifact_material), policy=selected_policy,
        conditions=conditions,
        checkpoint=checkpoint or ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.MISSING),
        active_provider_thread_id=active_provider_thread_id,
        mandatory_references=references,)


def make_plan(projection: ctx_projection.ContextProjection,
    *, attempt_id: str = "attempt-1",
    operation_id: str = "operation-1", result_correlation_id: str = "result-1",
    planned_provider_thread_id: str | None = None, reference_operation_id: str | None = None,
) -> owner_record_values.ContextLedgerAttemptPlan:
    if projection.mode is ctx_planning_inputs.ContextDeliveryMode.DELTA:
        planned_provider_thread_id = planned_provider_thread_id or "thread-1"
    reference_count = projection.accounting.mandatory_reference_count
    if reference_count:
        reference_operation_id = reference_operation_id or "reference-operation-1"
    return owner_record_values.ContextLedgerAttemptPlan(attempt_id,
        operation_id, result_correlation_id,
        owner_checkpoint_wire.context_checkpoint_proposal_digest(projection.checkpoint_proposal), projection.mode,
        projection.bootstrap_reason, projection.checkpoint_proposal.delta_depth,
        reference_count, planned_provider_thread_id,
        reference_operation_id,
        projection.accounting,)


def make_accounting(*,
    call_kind: ProviderCallKind = ProviderCallKind.CONVERSATION,
    state: ProviderCallState = ProviderCallState.SUBMITTED,
    usage: ProviderUsage | None = None,) -> ProviderCallAccounting:
    return ProviderCallAccounting(payload=ProviderPayloadAccounting(
            call_kind, 2,
            5, (
                ContextComponentBytes(ContextComponentKind.OUTPUT_SCHEMA, 2),
                ContextComponentBytes(ContextComponentKind.CONTROL, 5),),
        ), binding_components=(
            ContextComponentBytes(ContextComponentKind.ROLE_SYSTEM_PROMPT, 3),),
        state=state, actual_transport_utf8_bytes=11
        if state is ProviderCallState.SUBMITTED else None,
        usage=usage,)


def make_provider_call(ordinal: int,
    *, call_id: str | None = None,
    call_kind: ProviderCallKind = ProviderCallKind.CONVERSATION,
    state: ProviderCallState = ProviderCallState.SUBMITTED,
    usage: ProviderUsage | None = None,) -> owner_record_values.ContextLedgerProviderCall:
    return owner_record_values.ContextLedgerProviderCall(ordinal,
        call_id or f"call-{ordinal}",
        make_accounting(call_kind=call_kind, state=state, usage=usage),)


def make_authorization(plan: owner_record_values.ContextLedgerAttemptPlan,
    projection: ctx_projection.ContextProjection,
    *, terminal_call_id: str = "call-1",
    final_provider_thread_id: str = "thread-final",
    output_action_authorization_id: str = "output-authorization-1",
) -> owner_record_values.ContextLedgerCommitAuthorization:
    return owner_record_values.ContextLedgerCommitAuthorization(plan.attempt_id,
        plan.operation_id, plan.result_correlation_id,
        projection.checkpoint_proposal,
        owner_checkpoint_wire.context_checkpoint_proposal_digest(projection.checkpoint_proposal), terminal_call_id,
        final_provider_thread_id, plan.reference_operation_id,
        output_action_authorization_id,)


class LedgerClock:
    def __init__(self) -> None:
        self._next = datetime(2026, 8, 28, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        return value


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        self.scope = make_scope()
        self.key = owner_record_values.ContextLedgerKey(self.scope, "provider")
        self.projection = make_projection(self.scope, mandatory_reference_count=1)
        self.plan = make_plan(self.projection)
        self.ledger = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=LedgerClock())

    def record_path(self, key: owner_record_values.ContextLedgerKey | None = None) -> Path:
        selected = key or self.key
        return self.workspace.root.joinpath("agent_states/orchestrator_state/context_ledger/v2",
            f"{owner_record_values.context_ledger_scope_key(selected)[len('sha256:') :]}.json",)

    def lock_path(self, key: owner_record_values.ContextLedgerKey | None = None) -> Path:
        selected = key or self.key
        return self.record_path(selected).with_suffix(".lock")

    def assert_ledger_error(self,
        operation: object, code: owner_record_validation.ContextLedgerErrorCode,
    ) -> owner_record_validation.ContextLedgerError:
        with self.assertRaises(owner_record_validation.ContextLedgerError) as caught:
            operation()  # type: ignore[operator]
        self.assertIs(caught.exception.code, code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        for canary in PRIVATE_BODY_CANARIES:
            self.assertNotIn(canary, str(caught.exception))
            self.assertNotIn(canary, repr(caught.exception))
        return caught.exception

    def reserve(self,
        *, key: owner_record_values.ContextLedgerKey | None = None,
        projection: ctx_projection.ContextProjection | None = None,
        plan: owner_record_values.ContextLedgerAttemptPlan | None = None, expected_generation: int = 0,
    ) -> tuple[ctx_projection.ContextProjection, owner_record_values.ContextLedgerAttemptPlan, owner_record_values.ContextLedgerInspection]:
        selected_key = key or self.key
        selected_projection = projection or self.projection
        selected_plan = plan or self.plan
        inspection = self.ledger.reserve_attempt(selected_key,
            expected_generation=expected_generation,
            plan=selected_plan,)
        return selected_projection, selected_plan, inspection

    def ready(self,
        *, usage: ProviderUsage | None = None,
        key: owner_record_values.ContextLedgerKey | None = None, projection: ctx_projection.ContextProjection | None = None,
        plan: owner_record_values.ContextLedgerAttemptPlan | None = None,) -> owner_record_values.ContextLedgerInspection:
        selected_key = key or self.key
        selected_projection, selected_plan, reserved = self.reserve(key=selected_key,
            projection=projection,
            plan=plan,)
        call = make_provider_call(1, usage=usage)
        appended = self.ledger.append_provider_call(selected_key,
            expected_generation=reserved.generation, attempt_id=selected_plan.attempt_id,
            provider_call=call,)
        return self.ledger.commit_attempt(selected_key,
            expected_generation=appended.generation,
            authorization=make_authorization(selected_plan,
                selected_projection, final_provider_thread_id=(
                    selected_plan.planned_provider_thread_id or "thread-final"),
            ),)
