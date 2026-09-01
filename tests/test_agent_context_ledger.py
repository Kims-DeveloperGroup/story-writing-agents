"""Durable content-free agent-context ledger verification."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import agent_context, writing_artifacts
from story_writing_agents.agent_context import ledger as ledger_module
from story_writing_agents.agent_context.accounting import (
    ContextComponentBytes,
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderPayloadAccounting,
    ProviderUsage,
)
from story_writing_agents.agent_context.ledger import (
    CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS,
    CONTEXT_LEDGER_SCHEMA_VERSION,
    MAX_CONTEXT_LEDGER_PROVIDER_CALLS,
    MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES,
    MAX_CONTEXT_LEDGER_SOURCES,
    ContextLedger,
    ContextLedgerAttempt,
    ContextLedgerAttemptOutcome,
    ContextLedgerAttemptPlan,
    ContextLedgerCommitAuthorization,
    ContextLedgerError,
    ContextLedgerErrorCode,
    ContextLedgerInspection,
    ContextLedgerKey,
    ContextLedgerProviderCall,
    ContextLedgerReadyCheckpoint,
    ContextLedgerRecord,
    ContextLedgerRecordState,
    context_checkpoint_proposal_digest,
    context_ledger_scope_key,
)
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.planning import (
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    ContextBootstrapReason,
    ContextCheckpointProposal,
    ContextCheckpointSource,
    ContextCheckpointState,
    ContextCheckpointView,
    ContextDeliveryMode,
    ContextMaterial,
    ContextPlanningConditions,
    ContextPlanningPolicy,
    ContextProjection,
    MandatoryReferenceReservation,
    plan_context_projection,
)
from story_writing_agents.workspace import Workspace
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_PRIVATE_BODY_CANARIES = (
    "PRIVATE_CURRENT_INPUT_한🙂",
    "PRIVATE_DEFINITION_BODY",
    "PRIVATE_ARTIFACT_EXCERPT",
    "PRIVATE_REFERENCE_BODY",
    "PRIVATE_MODEL_ANSWER",
    "PRIVATE_EXCEPTION_TEXT",
)


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _scope(
    *,
    workspace_id: str = "workspace",
    session_id: str = "session",
    subject_kind: ContextSubjectKind = ContextSubjectKind.CONVERSATION,
    subject_id: str = "subject",
    agent_id: str = "lead_orchestrator",
) -> ContextScope:
    return ContextScope(
        workspace_id,
        session_id,
        subject_kind,
        subject_id,
        agent_id,
    )


def _entry(
    source_id: str,
    body: str,
    *,
    ordinal: int,
    source_kind: ContextSourceKind,
    channel: ContextSemanticChannel,
    priority: ContextPriority,
    authority: ContextAuthorityKind,
    searchable: bool,
) -> tuple[ContextManifestEntry, ContextMaterial]:
    body_utf8 = body.encode("utf-8")
    entry = ContextManifestEntry(
        source_id,
        source_kind,
        channel,
        priority,
        ordinal,
        "revision-1",
        _digest(body_utf8),
        ContextProvenance(authority, f"source-{ordinal}"),
        len(body_utf8),
        searchable=searchable,
    )
    return entry, ContextMaterial(entry, body_utf8)


def _projection(
    scope: ContextScope,
    provider_id: str = "provider",
    *,
    checkpoint: ContextCheckpointView | None = None,
    active_provider_thread_id: str | None = None,
    mandatory_reference_count: int = 0,
    definition_fingerprint: str | None = None,
    policy: ContextPlanningPolicy | None = None,
    schema_version: int = CONTEXT_CHECKPOINT_SCHEMA_VERSION,
) -> ContextProjection:
    selected_policy = policy or ContextPlanningPolicy()
    conditions = ContextPlanningConditions(
        scope,
        provider_id,
        definition_fingerprint or _digest(b"definition"),
        selected_policy.fingerprint,
        schema_version,
    )
    current, current_material = _entry(
        "request:current",
        _PRIVATE_BODY_CANARIES[0],
        ordinal=0,
        source_kind=ContextSourceKind.RUNTIME_INPUT,
        channel=ContextSemanticChannel.CURRENT_INPUT,
        priority=ContextPriority.P0_REQUIRED,
        authority=ContextAuthorityKind.REQUEST,
        searchable=False,
    )
    definition, definition_material = _entry(
        "definition:lead",
        _PRIVATE_BODY_CANARIES[1],
        ordinal=1,
        source_kind=ContextSourceKind.AGENT_DEFINITION,
        channel=ContextSemanticChannel.STABLE_BEHAVIOR,
        priority=ContextPriority.P1_STABLE,
        authority=ContextAuthorityKind.AGENT_DEFINITION,
        searchable=False,
    )
    artifact, artifact_material = _entry(
        "artifact:outline",
        _PRIVATE_BODY_CANARIES[2],
        ordinal=2,
        source_kind=ContextSourceKind.WRITING_ARTIFACT,
        channel=ContextSemanticChannel.FOCUSED_EVIDENCE,
        priority=ContextPriority.P2_WORKING,
        authority=ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
        searchable=True,
    )
    references = tuple(
        MandatoryReferenceReservation(
            ordinal,
            f"reference-{ordinal}",
            f"artifact-{ordinal}",
            "revision-1",
            ContextProvenance(
                ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                f"reference-{ordinal}",
            ),
            _digest(f"reference-{ordinal}".encode("ascii")),
            9 + ordinal,
        )
        for ordinal in range(1, mandatory_reference_count + 1)
    )
    return plan_context_projection(
        ContextManifest(scope, (current, definition, artifact)),
        (current_material, definition_material, artifact_material),
        policy=selected_policy,
        conditions=conditions,
        checkpoint=checkpoint or ContextCheckpointView(ContextCheckpointState.MISSING),
        active_provider_thread_id=active_provider_thread_id,
        mandatory_references=references,
    )


def _plan(
    projection: ContextProjection,
    *,
    attempt_id: str = "attempt-1",
    operation_id: str = "operation-1",
    result_correlation_id: str = "result-1",
    planned_provider_thread_id: str | None = None,
    reference_operation_id: str | None = None,
) -> ContextLedgerAttemptPlan:
    if projection.mode is ContextDeliveryMode.DELTA:
        planned_provider_thread_id = planned_provider_thread_id or "thread-1"
    reference_count = projection.accounting.mandatory_reference_count
    if reference_count:
        reference_operation_id = reference_operation_id or "reference-operation-1"
    return ContextLedgerAttemptPlan(
        attempt_id,
        operation_id,
        result_correlation_id,
        context_checkpoint_proposal_digest(projection.checkpoint_proposal),
        projection.mode,
        projection.bootstrap_reason,
        projection.checkpoint_proposal.delta_depth,
        reference_count,
        planned_provider_thread_id,
        reference_operation_id,
        projection.accounting,
    )


def _accounting(
    *,
    call_kind: ProviderCallKind = ProviderCallKind.CONVERSATION,
    state: ProviderCallState = ProviderCallState.SUBMITTED,
    usage: ProviderUsage | None = None,
) -> ProviderCallAccounting:
    return ProviderCallAccounting(
        payload=ProviderPayloadAccounting(
            call_kind,
            2,
            5,
            (
                ContextComponentBytes(ContextComponentKind.OUTPUT_SCHEMA, 2),
                ContextComponentBytes(ContextComponentKind.CONTROL, 5),
            ),
        ),
        binding_components=(
            ContextComponentBytes(ContextComponentKind.ROLE_SYSTEM_PROMPT, 3),
        ),
        state=state,
        actual_transport_utf8_bytes=11
        if state is ProviderCallState.SUBMITTED
        else None,
        usage=usage,
    )


def _provider_call(
    ordinal: int,
    *,
    call_id: str | None = None,
    call_kind: ProviderCallKind = ProviderCallKind.CONVERSATION,
    state: ProviderCallState = ProviderCallState.SUBMITTED,
    usage: ProviderUsage | None = None,
) -> ContextLedgerProviderCall:
    return ContextLedgerProviderCall(
        ordinal,
        call_id or f"call-{ordinal}",
        _accounting(call_kind=call_kind, state=state, usage=usage),
    )


def _authorization(
    plan: ContextLedgerAttemptPlan,
    projection: ContextProjection,
    *,
    terminal_call_id: str = "call-1",
    final_provider_thread_id: str = "thread-final",
    output_action_authorization_id: str = "output-authorization-1",
) -> ContextLedgerCommitAuthorization:
    return ContextLedgerCommitAuthorization(
        plan.attempt_id,
        plan.operation_id,
        plan.result_correlation_id,
        projection.checkpoint_proposal,
        context_checkpoint_proposal_digest(projection.checkpoint_proposal),
        terminal_call_id,
        final_provider_thread_id,
        plan.reference_operation_id,
        output_action_authorization_id,
    )


class _Clock:
    def __init__(self) -> None:
        self._next = datetime(2026, 8, 28, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        return value


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name) / "workspace").initialize()
        self.scope = _scope()
        self.key = ContextLedgerKey(self.scope, "provider")
        self.projection = _projection(self.scope, mandatory_reference_count=1)
        self.plan = _plan(self.projection)
        self.ledger = ContextLedger(self.workspace, clock=_Clock())

    def record_path(self, key: ContextLedgerKey | None = None) -> Path:
        selected = key or self.key
        return self.workspace.root.joinpath(
            "agent_states/orchestrator_state/context_ledger/v2",
            f"{context_ledger_scope_key(selected)[len('sha256:') :]}.json",
        )

    def lock_path(self, key: ContextLedgerKey | None = None) -> Path:
        selected = key or self.key
        return self.record_path(selected).with_suffix(".lock")

    def assert_ledger_error(
        self,
        operation: object,
        code: ContextLedgerErrorCode,
    ) -> ContextLedgerError:
        with self.assertRaises(ContextLedgerError) as caught:
            operation()  # type: ignore[operator]
        self.assertIs(caught.exception.code, code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        for canary in _PRIVATE_BODY_CANARIES:
            self.assertNotIn(canary, str(caught.exception))
            self.assertNotIn(canary, repr(caught.exception))
        return caught.exception

    def reserve(
        self,
        *,
        key: ContextLedgerKey | None = None,
        projection: ContextProjection | None = None,
        plan: ContextLedgerAttemptPlan | None = None,
        expected_generation: int = 0,
    ) -> tuple[ContextProjection, ContextLedgerAttemptPlan, ContextLedgerInspection]:
        selected_key = key or self.key
        selected_projection = projection or self.projection
        selected_plan = plan or self.plan
        inspection = self.ledger.reserve_attempt(
            selected_key,
            expected_generation=expected_generation,
            plan=selected_plan,
        )
        return selected_projection, selected_plan, inspection

    def ready(
        self,
        *,
        usage: ProviderUsage | None = None,
        key: ContextLedgerKey | None = None,
        projection: ContextProjection | None = None,
        plan: ContextLedgerAttemptPlan | None = None,
    ) -> ContextLedgerInspection:
        selected_key = key or self.key
        selected_projection, selected_plan, reserved = self.reserve(
            key=selected_key,
            projection=projection,
            plan=plan,
        )
        call = _provider_call(1, usage=usage)
        appended = self.ledger.append_provider_call(
            selected_key,
            expected_generation=reserved.generation,
            attempt_id=selected_plan.attempt_id,
            provider_call=call,
        )
        return self.ledger.commit_attempt(
            selected_key,
            expected_generation=appended.generation,
            authorization=_authorization(
                selected_plan,
                selected_projection,
                final_provider_thread_id=(
                    selected_plan.planned_provider_thread_id or "thread-final"
                ),
            ),
        )


class LedgerSurfaceTests(LedgerTestCase):
    def test_exact_surface_constants_enums_signatures_and_fixed_errors(self) -> None:
        self.assertEqual(
            tuple(ledger_module.__all__),
            (
                "CONTEXT_LEDGER_SCHEMA_VERSION",
                "MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES",
                "MAX_CONTEXT_LEDGER_SOURCES",
                "MAX_CONTEXT_LEDGER_PROVIDER_CALLS",
                "CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS",
                "ContextLedger",
                "ContextLedgerKey",
                "ContextLedgerRecordState",
                "ContextLedgerAttemptOutcome",
                "ContextLedgerAttemptPlan",
                "ContextLedgerProviderCall",
                "ContextLedgerAttempt",
                "ContextLedgerReadyCheckpoint",
                "ContextLedgerRecord",
                "ContextLedgerInspection",
                "ContextLedgerCommitAuthorization",
                "ContextLedgerError",
                "ContextLedgerErrorCode",
                "context_checkpoint_proposal_digest",
                "context_ledger_scope_key",
            ),
        )
        self.assertEqual(
            (
                CONTEXT_LEDGER_SCHEMA_VERSION,
                MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES,
                MAX_CONTEXT_LEDGER_SOURCES,
                MAX_CONTEXT_LEDGER_PROVIDER_CALLS,
                CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS,
            ),
            (2, 8 * 1024 * 1024, 4096, 32, 5.0),
        )
        self.assertEqual(
            tuple(item.value for item in ContextLedgerRecordState),
            ("incomplete", "ready", "tainted", "stale"),
        )
        self.assertEqual(
            tuple(item.value for item in ContextLedgerAttemptOutcome),
            (
                "in_progress",
                "committed",
                "failed",
                "cancelled",
                "timed_out",
                "malformed",
                "reference_invalid",
                "source_invalid",
                "output_invalid",
                "incomplete",
                "ambiguous",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ContextLedgerErrorCode),
            (
                "invalid_value",
                "invalid_transition",
                "conflict",
                "commit_not_authorized",
                "limit_exceeded",
                "lock_timeout",
                "storage_unsupported",
                "storage_unsafe",
                "storage_failure",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(ContextLedger).parameters),
            ("workspace", "clock"),
        )
        self.assertEqual(
            tuple(inspect.signature(ContextLedger.reserve_attempt).parameters),
            ("self", "key", "expected_generation", "plan"),
        )
        for code in ContextLedgerErrorCode:
            with self.subTest(code=code):
                error = ContextLedgerError(code)
                self.assertEqual(error.code, code)
                self.assertNotIn("PRIVATE", str(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_values_are_frozen_slotted_exact_and_reject_invalid_shapes(self) -> None:
        call = _provider_call(1)
        values = (
            self.key,
            call,
            self.plan,
            ContextLedgerAttempt(
                self.plan,
                (),
                ContextLedgerAttemptOutcome.IN_PROGRESS,
                "2026-08-28T00:00:00.000000Z",
                "2026-08-28T00:00:00.000000Z",
            ),
            ContextLedgerCommitAuthorization(
                self.plan.attempt_id,
                self.plan.operation_id,
                self.plan.result_correlation_id,
                self.projection.checkpoint_proposal,
                self.plan.proposal_digest,
                "call-1",
                "thread",
                self.plan.reference_operation_id,
                "output-authorization",
            ),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, TypeError)):
                    value.unexpected = "PRIVATE"  # type: ignore[attr-defined]
                self.assertNotIn(_PRIVATE_BODY_CANARIES[0], repr(value))
        invalid = (
            lambda: ContextLedgerKey("scope", "provider"),
            lambda: ContextLedgerKey(self.scope, ""),
            lambda: ContextLedgerProviderCall(0, "call", _accounting()),
            lambda: ContextLedgerProviderCall(1, "", _accounting()),
            lambda: replace(self.plan, mandatory_reference_count=0),
            lambda: replace(self.plan, proposed_delta_depth=1),
            lambda: ContextLedgerCommitAuthorization(
                "",
                self.plan.operation_id,
                self.plan.result_correlation_id,
                self.projection.checkpoint_proposal,
                self.plan.proposal_digest,
                "call",
                "thread",
                self.plan.reference_operation_id,
                "output",
            ),
            lambda: replace(
                _authorization(self.plan, self.projection),
                output_action_authorization_id="",
            ),
        )
        for operation in invalid:
            with self.subTest(operation=operation):
                self.assert_ledger_error(
                    operation, ContextLedgerErrorCode.INVALID_VALUE
                )

    def test_scope_and_proposal_digests_are_domain_separated_and_canonical(
        self,
    ) -> None:
        self.assertEqual(
            context_ledger_scope_key(
                ContextLedgerKey(
                    _scope(
                        agent_id="lead",
                        workspace_id="workspace",
                        session_id="session",
                        subject_id="subject",
                    ),
                    "provider",
                )
            ),
            "sha256:cc2e59110562cb936b078ada7a6195136046631ddbe6f92b35a8fe4aa196d99d",
        )
        hostile = ContextLedgerKey(
            _scope(
                workspace_id="../workspace/한🙂",
                session_id="../../session",
                agent_id="../agent",
            ),
            "provider/../../other",
        )
        scope_key = context_ledger_scope_key(hostile)
        self.assertRegex(scope_key, r"\Asha256:[0-9a-f]{64}\Z")
        for private in ("workspace", "session", "agent", "provider", "/", "한"):
            self.assertNotIn(private, scope_key)
        proposal = self.projection.checkpoint_proposal
        reversed_proposal = replace(
            proposal,
            sources=tuple(reversed(proposal.sources)),
        )
        self.assertEqual(
            context_checkpoint_proposal_digest(proposal),
            context_checkpoint_proposal_digest(reversed_proposal),
        )
        self.assertEqual(
            context_checkpoint_proposal_digest(proposal),
            "sha256:365231f25892819500b6bc4507ccd03a83bf0bc4ef2320034bb4006c117a133c",
        )
        self.assertRegex(
            context_checkpoint_proposal_digest(proposal),
            r"\Asha256:[0-9a-f]{64}\Z",
        )

    def test_import_construction_and_missing_inspection_are_mutation_free(self) -> None:
        state_root = (
            self.workspace.root / "agent_states/orchestrator_state/context_ledger"
        )
        self.assertFalse(state_root.exists())
        constructed = ContextLedger(self.workspace, clock=_Clock())
        self.assertFalse(state_root.exists())
        before = {
            path.relative_to(self.workspace.root).as_posix()
            for path in self.workspace.root.rglob("*")
        }
        inspection = constructed.inspect(self.key)
        after = {
            path.relative_to(self.workspace.root).as_posix()
            for path in self.workspace.root.rglob("*")
        }
        self.assertEqual(before, after)
        self.assertEqual(
            (
                inspection.generation,
                inspection.record,
                inspection.checkpoint_view.state,
            ),
            (0, None, ContextCheckpointState.MISSING),
        )
        with tempfile.TemporaryDirectory() as cwd:
            environment = dict(
                os.environ,
                PYTHONPATH=str(_SOURCE_ROOT),
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONWARNINGS="error",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import story_writing_agents.agent_context.ledger",
                ],
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(cwd).iterdir()), [])

    def test_unpublished_v1_directory_is_never_read_rewritten_or_migrated(self) -> None:
        old_root = self.workspace.root / (
            "agent_states/orchestrator_state/context_ledger/v1"
        )
        old_root.mkdir(parents=True)
        os.chmod(old_root.parent, 0o700)
        os.chmod(old_root, 0o700)
        old_record = old_root / "PRIVATE_OLD_RECORD.json"
        old_bytes = b'{"schema_version":1,"private":"PRIVATE_V1_CANARY"}'
        old_record.write_bytes(old_bytes)
        os.chmod(old_record, 0o600)
        before = {
            path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in old_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            ledger_module._LEDGER_PATH,
            ("agent_states", "orchestrator_state", "context_ledger", "v2"),
        )
        self.assertNotIn("v1", ledger_module._LEDGER_PATH)
        inspection = self.ledger.inspect(self.key)

        self.assertIs(
            inspection.checkpoint_view.state,
            ContextCheckpointState.MISSING,
        )
        self.assertEqual(inspection.generation, 0)
        self.assertIsNone(inspection.record)
        self.assertFalse(
            self.workspace.root.joinpath(
                "agent_states/orchestrator_state/context_ledger/v2"
            ).exists()
        )
        after = {
            path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in old_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(old_record.read_bytes(), old_bytes)


class LedgerLifecycleTests(LedgerTestCase):
    def test_persisted_values_have_exact_fields_are_frozen_and_self_validate(
        self,
    ) -> None:
        ready = self.ready()
        assert ready.record is not None
        assert ready.record.attempt is not None
        assert ready.record.ready_checkpoint is not None
        exact_fields = {
            ContextLedgerKey: ("scope", "provider_id"),
            ContextLedgerProviderCall: ("ordinal", "call_id", "accounting"),
            ContextLedgerAttemptPlan: (
                "attempt_id",
                "operation_id",
                "result_correlation_id",
                "proposal_digest",
                "delivery_mode",
                "bootstrap_reason",
                "proposed_delta_depth",
                "mandatory_reference_count",
                "planned_provider_thread_id",
                "reference_operation_id",
                "projection_accounting",
            ),
            ContextLedgerAttempt: (
                "plan",
                "provider_calls",
                "outcome",
                "started_at",
                "updated_at",
                "completed_at",
                "commit_authorization_digest",
            ),
            ContextLedgerReadyCheckpoint: (
                "conditions",
                "provider_thread_id",
                "sources",
                "delta_depth",
                "proposal_digest",
                "committed_at",
                "catalog_sources",
                "source_alias_high_watermark",
            ),
            ContextLedgerRecord: (
                "key",
                "generation",
                "state",
                "created_at",
                "updated_at",
                "attempt",
                "ready_checkpoint",
                "schema_version",
            ),
            ContextLedgerInspection: (
                "key",
                "generation",
                "checkpoint_view",
                "record",
            ),
            ContextLedgerCommitAuthorization: (
                "attempt_id",
                "operation_id",
                "result_correlation_id",
                "proposal",
                "revalidated_proposal_digest",
                "terminal_call_id",
                "final_provider_thread_id",
                "completed_reference_operation_id",
                "output_action_authorization_id",
            ),
        }
        for value in (
            ready.record.key,
            ready.record.attempt.plan,
            ready.record.attempt.provider_calls[0],
            ready.record.attempt,
            ready.record.ready_checkpoint,
            ready.record,
            ready,
            _authorization(self.plan, self.projection),
        ):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(type(value))),
                    exact_fields[type(value)],
                )
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, TypeError)):
                    value.unexpected = 1  # type: ignore[attr-defined]
        self.assert_ledger_error(
            lambda: replace(
                ready.record.ready_checkpoint,
                proposal_digest=_digest(b"mismatch"),
            ),
            ContextLedgerErrorCode.INVALID_VALUE,
        )
        self.assert_ledger_error(
            lambda: replace(
                ready.record,
                state=ContextLedgerRecordState.STALE,
            ),
            ContextLedgerErrorCode.INVALID_VALUE,
        )
        self.assert_ledger_error(
            lambda: replace(ready, generation=ready.generation + 1),
            ContextLedgerErrorCode.INVALID_VALUE,
        )

    def test_complete_lifecycle_restart_idempotency_invalidation_and_modes(
        self,
    ) -> None:
        self.assertIs(
            self.ledger.inspect(self.key).checkpoint_view.state,
            ContextCheckpointState.MISSING,
        )
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,
        )
        self.assertEqual(
            (
                reserved.generation,
                reserved.record.state if reserved.record else None,
                reserved.checkpoint_view.state,
            ),
            (1, ContextLedgerRecordState.INCOMPLETE, ContextCheckpointState.TAINTED),
        )
        self.assertIsNone(reserved.record.ready_checkpoint if reserved.record else None)
        self.assertEqual(
            self.ledger.reserve_attempt(
                self.key,
                expected_generation=0,
                plan=self.plan,
            ),
            reserved,
        )
        reference_call = _provider_call(
            1,
            call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        )
        first = self.ledger.append_provider_call(
            self.key,
            expected_generation=1,
            attempt_id=self.plan.attempt_id,
            provider_call=reference_call,
        )
        self.assertEqual(first.generation, 2)
        self.assertEqual(
            self.ledger.append_provider_call(
                self.key,
                expected_generation=1,
                attempt_id=self.plan.attempt_id,
                provider_call=reference_call,
            ),
            first,
        )
        substantive = _provider_call(
            2,
            usage=ProviderUsage(input_tokens=17, output_tokens=5),
        )
        second = self.ledger.append_provider_call(
            self.key,
            expected_generation=2,
            attempt_id=self.plan.attempt_id,
            provider_call=substantive,
        )
        authorization = _authorization(
            self.plan,
            self.projection,
            terminal_call_id="call-2",
        )
        ready = self.ledger.commit_attempt(
            self.key,
            expected_generation=second.generation,
            authorization=authorization,
        )
        self.assertEqual(ready.generation, 4)
        self.assertIs(ready.record.state, ContextLedgerRecordState.READY)
        self.assertIs(ready.checkpoint_view.state, ContextCheckpointState.READY)
        self.assertEqual(
            (
                ready.checkpoint_view.conditions,
                ready.checkpoint_view.provider_thread_id,
                ready.checkpoint_view.sources,
                ready.checkpoint_view.delta_depth,
            ),
            (
                self.projection.checkpoint_proposal.conditions,
                "thread-final",
                self.projection.checkpoint_proposal.sources,
                0,
            ),
        )
        self.assertEqual(
            self.ledger.commit_attempt(
                self.key,
                expected_generation=0,
                authorization=authorization,
            ),
            ready,
        )
        restarted = ContextLedger(self.workspace, clock=_Clock()).inspect(self.key)
        self.assertEqual(restarted, ready)
        stale = self.ledger.invalidate(self.key, expected_generation=ready.generation)
        self.assertEqual(
            (stale.generation, stale.record.state, stale.checkpoint_view.state),
            (5, ContextLedgerRecordState.STALE, ContextCheckpointState.STALE),
        )
        self.assertIsNone(stale.record.ready_checkpoint)
        self.assertEqual(
            self.ledger.invalidate(self.key, expected_generation=0),
            stale,
        )
        directory = self.record_path().parent
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(directory.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.record_path().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.lock_path().stat().st_mode), 0o600)
        self.assertEqual(
            {path.name for path in directory.iterdir()},
            {self.record_path().name, self.lock_path().name},
        )

    def test_optimistic_generations_ordered_calls_and_conflicting_replays(self) -> None:
        self.assert_ledger_error(
            lambda: self.ledger.reserve_attempt(
                self.key,
                expected_generation=1,
                plan=self.plan,
            ),
            ContextLedgerErrorCode.CONFLICT,
        )
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,
        )
        self.assert_ledger_error(
            lambda: self.ledger.reserve_attempt(
                self.key,
                expected_generation=reserved.generation,
                plan=replace(self.plan, attempt_id="attempt-2"),
            ),
            ContextLedgerErrorCode.INVALID_TRANSITION,
        )
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=reserved.generation,
                attempt_id="other-attempt",
                provider_call=_provider_call(1),
            ),
            ContextLedgerErrorCode.INVALID_TRANSITION,
        )
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=reserved.generation,
                attempt_id=self.plan.attempt_id,
                provider_call=_provider_call(2),
            ),
            ContextLedgerErrorCode.INVALID_TRANSITION,
        )
        call = _provider_call(1)
        appended = self.ledger.append_provider_call(
            self.key,
            expected_generation=reserved.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=call,
        )
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=appended.generation,
                attempt_id=self.plan.attempt_id,
                provider_call=replace(
                    call,
                    accounting=_accounting(usage=ProviderUsage(input_tokens=1)),
                ),
            ),
            ContextLedgerErrorCode.CONFLICT,
        )
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=1,
                attempt_id=self.plan.attempt_id,
                provider_call=_provider_call(2),
            ),
            ContextLedgerErrorCode.CONFLICT,
        )

    def test_old_ready_is_removed_before_new_provider_work_and_delta_thread_is_exact(
        self,
    ) -> None:
        first_ready = self.ready()
        delta_projection = _projection(
            self.scope,
            checkpoint=first_ready.checkpoint_view,
            active_provider_thread_id="thread-final",
        )
        self.assertIs(delta_projection.mode, ContextDeliveryMode.DELTA)
        delta_plan = _plan(
            delta_projection,
            attempt_id="attempt-delta",
            operation_id="operation-delta",
            result_correlation_id="result-delta",
            planned_provider_thread_id="thread-final",
        )
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=first_ready.generation,
            plan=delta_plan,
        )
        self.assertIs(reserved.record.state, ContextLedgerRecordState.INCOMPLETE)
        self.assertIsNone(reserved.record.ready_checkpoint)
        self.assertIs(reserved.checkpoint_view.state, ContextCheckpointState.TAINTED)
        appended = self.ledger.append_provider_call(
            self.key,
            expected_generation=reserved.generation,
            attempt_id=delta_plan.attempt_id,
            provider_call=_provider_call(1),
        )
        wrong_thread = _authorization(
            delta_plan,
            delta_projection,
            final_provider_thread_id="other-thread",
        )
        self.assert_ledger_error(
            lambda: self.ledger.commit_attempt(
                self.key,
                expected_generation=appended.generation,
                authorization=wrong_thread,
            ),
            ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
        )
        ready = self.ledger.commit_attempt(
            self.key,
            expected_generation=appended.generation,
            authorization=_authorization(
                delta_plan,
                delta_projection,
                final_provider_thread_id="thread-final",
            ),
        )
        self.assertEqual(ready.checkpoint_view.delta_depth, 1)


class LedgerAuthorizationTests(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,
        )
        first = self.ledger.append_provider_call(
            self.key,
            expected_generation=reserved.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=_provider_call(
                1,
                call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
            ),
        )
        self.appended = self.ledger.append_provider_call(
            self.key,
            expected_generation=first.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=_provider_call(2),
        )
        self.authorization = _authorization(
            self.plan,
            self.projection,
            terminal_call_id="call-2",
        )

    def test_commit_rejects_every_uncorrelated_or_unverified_dimension(self) -> None:
        changed_conditions = replace(
            self.projection.checkpoint_proposal.conditions,
            definition_fingerprint=_digest(b"changed-definition"),
        )
        changed_proposal = replace(
            self.projection.checkpoint_proposal,
            conditions=changed_conditions,
        )
        changed_scope_conditions = replace(
            self.projection.checkpoint_proposal.conditions,
            scope=_scope(session_id="other-session"),
        )
        changed_scope_proposal = replace(
            self.projection.checkpoint_proposal,
            conditions=changed_scope_conditions,
        )
        changed_provider_conditions = replace(
            self.projection.checkpoint_proposal.conditions,
            provider_id="other-provider",
        )
        changed_provider_proposal = replace(
            self.projection.checkpoint_proposal,
            conditions=changed_provider_conditions,
        )
        cases = (
            (
                replace(self.authorization, attempt_id="wrong-attempt"),
                ContextLedgerErrorCode.INVALID_TRANSITION,
            ),
            (
                replace(self.authorization, operation_id="wrong-operation"),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(self.authorization, result_correlation_id="wrong-result"),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(self.authorization, proposal=changed_proposal),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(self.authorization, proposal=changed_scope_proposal),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(self.authorization, proposal=changed_provider_proposal),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(
                    self.authorization,
                    revalidated_proposal_digest=_digest(b"wrong-proposal"),
                ),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(self.authorization, terminal_call_id="call-1"),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
            (
                replace(self.authorization, completed_reference_operation_id=None),
                ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
            ),
        )
        for authorization, code in cases:
            with self.subTest(field=authorization):
                before = self.record_path().read_bytes()
                self.assert_ledger_error(
                    lambda authorization=authorization: self.ledger.commit_attempt(
                        self.key,
                        expected_generation=self.appended.generation,
                        authorization=authorization,
                    ),
                    code,
                )
                self.assertEqual(self.record_path().read_bytes(), before)

    def test_only_submitted_substantive_terminal_call_can_commit(self) -> None:
        for index, (kind, state) in enumerate(
            (
                (
                    ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
                    ProviderCallState.SUBMITTED,
                ),
                (ProviderCallKind.UNCLASSIFIED, ProviderCallState.SUBMITTED),
                (ProviderCallKind.CONVERSATION, ProviderCallState.PREPARED),
            ),
            start=1,
        ):
            with self.subTest(kind=kind, state=state):
                scope = _scope(subject_id=f"terminal-{index}")
                key = ContextLedgerKey(scope, "provider")
                projection = _projection(scope)
                plan = _plan(
                    projection,
                    attempt_id=f"attempt-terminal-{index}",
                    operation_id=f"operation-terminal-{index}",
                    result_correlation_id=f"result-terminal-{index}",
                )
                reserved = self.ledger.reserve_attempt(
                    key,
                    expected_generation=0,
                    plan=plan,
                )
                appended = self.ledger.append_provider_call(
                    key,
                    expected_generation=reserved.generation,
                    attempt_id=plan.attempt_id,
                    provider_call=_provider_call(1, call_kind=kind, state=state),
                )
                self.assert_ledger_error(
                    lambda: self.ledger.commit_attempt(
                        key,
                        expected_generation=appended.generation,
                        authorization=_authorization(plan, projection),
                    ),
                    ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,
                )


class LedgerUnsafeOutcomeAndUsageTests(LedgerTestCase):
    def test_every_unsafe_outcome_is_restart_safe_tainted_and_idempotent(self) -> None:
        unsafe = tuple(
            outcome
            for outcome in ContextLedgerAttemptOutcome
            if outcome
            not in {
                ContextLedgerAttemptOutcome.IN_PROGRESS,
                ContextLedgerAttemptOutcome.COMMITTED,
            }
        )
        self.assertEqual(len(unsafe), 9)
        for index, outcome in enumerate(unsafe, start=1):
            with self.subTest(outcome=outcome):
                scope = _scope(subject_id=f"unsafe-{index}")
                key = ContextLedgerKey(scope, "provider")
                projection = _projection(scope)
                plan = _plan(
                    projection,
                    attempt_id=f"attempt-unsafe-{index}",
                    operation_id=f"operation-unsafe-{index}",
                    result_correlation_id=f"result-unsafe-{index}",
                )
                reserved = self.ledger.reserve_attempt(
                    key,
                    expected_generation=0,
                    plan=plan,
                )
                tainted = self.ledger.taint_attempt(
                    key,
                    expected_generation=reserved.generation,
                    attempt_id=plan.attempt_id,
                    outcome=outcome,
                )
                self.assertEqual(
                    (
                        tainted.record.state,
                        tainted.record.attempt.outcome,
                        tainted.checkpoint_view.state,
                    ),
                    (
                        ContextLedgerRecordState.TAINTED,
                        outcome,
                        ContextCheckpointState.TAINTED,
                    ),
                )
                restarted = ContextLedger(self.workspace, clock=_Clock()).inspect(key)
                self.assertEqual(restarted, tainted)
                self.assertEqual(
                    self.ledger.taint_attempt(
                        key,
                        expected_generation=0,
                        attempt_id=plan.attempt_id,
                        outcome=outcome,
                    ),
                    tainted,
                )

    def test_incomplete_restart_maps_to_tainted_and_never_authorizes_delta(
        self,
    ) -> None:
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,
        )
        restarted = ContextLedger(self.workspace, clock=_Clock()).inspect(self.key)
        self.assertEqual(restarted, reserved)
        self.assertIs(restarted.checkpoint_view.state, ContextCheckpointState.TAINTED)
        fresh_projection = _projection(
            self.scope,
            checkpoint=restarted.checkpoint_view,
            active_provider_thread_id="thread",
        )
        self.assertIs(fresh_projection.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(
            fresh_projection.bootstrap_reason,
            ContextBootstrapReason.CHECKPOINT_TAINTED,
        )

    def test_usage_unavailable_zero_partial_and_full_remains_exact_per_call(
        self,
    ) -> None:
        usages = (
            None,
            ProviderUsage(input_tokens=0),
            ProviderUsage(output_tokens=7),
            ProviderUsage(input_tokens=11, output_tokens=5, cached_input_tokens=3),
        )
        for index, usage in enumerate(usages, start=1):
            with self.subTest(usage=usage):
                scope = _scope(subject_id=f"usage-{index}")
                key = ContextLedgerKey(scope, "provider")
                projection = _projection(scope)
                plan = _plan(
                    projection,
                    attempt_id=f"attempt-usage-{index}",
                    operation_id=f"operation-usage-{index}",
                    result_correlation_id=f"result-usage-{index}",
                )
                ready = self.ready(
                    usage=usage,
                    key=key,
                    projection=projection,
                    plan=plan,
                )
                persisted = ready.record.attempt.provider_calls[0].accounting.usage
                self.assertEqual(persisted, usage)
                raw = json.loads(self.record_path(key).read_bytes())
                raw_usage = raw["attempt"]["provider_calls"][0]["accounting"]["usage"]
                if usage is None:
                    self.assertIsNone(raw_usage)
                else:
                    self.assertEqual(
                        raw_usage,
                        {
                            "cached_input_tokens": usage.cached_input_tokens,
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                        },
                    )
        scope = _scope(subject_id="usage-multiple")
        key = ContextLedgerKey(scope, "provider")
        projection = _projection(scope)
        plan = _plan(
            projection,
            attempt_id="attempt-usage-multiple",
            operation_id="operation-usage-multiple",
            result_correlation_id="result-usage-multiple",
        )
        reserved = self.ledger.reserve_attempt(key, expected_generation=0, plan=plan)
        first = self.ledger.append_provider_call(
            key,
            expected_generation=reserved.generation,
            attempt_id=plan.attempt_id,
            provider_call=_provider_call(1, usage=ProviderUsage(input_tokens=5)),
        )
        second = self.ledger.append_provider_call(
            key,
            expected_generation=first.generation,
            attempt_id=plan.attempt_id,
            provider_call=_provider_call(2, usage=ProviderUsage(input_tokens=7)),
        )
        self.assertEqual(
            tuple(
                call.accounting.usage.input_tokens
                for call in second.record.attempt.provider_calls
            ),
            (5, 7),
        )


class LedgerScopeAndBootstrapTests(LedgerTestCase):
    def test_exact_scope_key_dimensions_are_isolated_without_cross_scope_reads(
        self,
    ) -> None:
        ready = self.ready()
        cases = (
            ContextLedgerKey(_scope(workspace_id="other-workspace"), "provider"),
            ContextLedgerKey(_scope(session_id="other-session"), "provider"),
            ContextLedgerKey(
                _scope(subject_kind=ContextSubjectKind.DEBATE),
                "provider",
            ),
            ContextLedgerKey(_scope(subject_id="other-subject"), "provider"),
            ContextLedgerKey(_scope(agent_id="plot_architect"), "provider"),
            ContextLedgerKey(self.scope, "other-provider"),
        )
        self.assertIs(ready.checkpoint_view.state, ContextCheckpointState.READY)
        observed_keys = {context_ledger_scope_key(self.key)}
        for key in cases:
            with self.subTest(key=key):
                self.assertNotIn(context_ledger_scope_key(key), observed_keys)
                observed_keys.add(context_ledger_scope_key(key))
                inspection = self.ledger.inspect(key)
                self.assertEqual(
                    (
                        inspection.generation,
                        inspection.record,
                        inspection.checkpoint_view.state,
                    ),
                    (0, None, ContextCheckpointState.MISSING),
                )
                self.assertFalse(self.record_path(key).exists())

    def test_persisted_ready_checkpoint_requests_exact_bootstrap_reasons(self) -> None:
        ready = self.ready()
        checkpoint = ready.checkpoint_view
        cases = (
            (
                _projection(
                    _scope(workspace_id="other-workspace"),
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                ),
                ContextBootstrapReason.SCOPE_MISMATCH,
            ),
            (
                _projection(
                    self.scope,
                    "other-provider",
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                ),
                ContextBootstrapReason.PROVIDER_MISMATCH,
            ),
            (
                _projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id=None,
                ),
                ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                _projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="other-thread",
                ),
                ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                _projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                    definition_fingerprint=_digest(b"other-definition"),
                ),
                ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH,
            ),
            (
                _projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                    policy=ContextPlanningPolicy(max_delta_depth=17),
                ),
                ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH,
            ),
            (
                _projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                    schema_version=2,
                ),
                ContextBootstrapReason.SCHEMA_MISMATCH,
            ),
        )
        for projection, expected in cases:
            with self.subTest(expected=expected):
                self.assertIs(projection.mode, ContextDeliveryMode.BOOTSTRAP)
                self.assertIs(projection.bootstrap_reason, expected)

    def test_persisted_maximum_delta_depth_forces_bootstrap(self) -> None:
        conditions = self.projection.checkpoint_proposal.conditions
        depth_fifteen = ContextCheckpointView(
            ContextCheckpointState.READY,
            conditions,
            "thread-depth",
            self.projection.checkpoint_proposal.sources,
            15,
            self.projection.checkpoint_proposal.catalog_sources,
            self.projection.checkpoint_proposal.source_alias_high_watermark,
        )
        delta = _projection(
            self.scope,
            checkpoint=depth_fifteen,
            active_provider_thread_id="thread-depth",
        )
        self.assertEqual(delta.checkpoint_proposal.delta_depth, 16)
        plan = _plan(
            delta,
            attempt_id="attempt-depth",
            operation_id="operation-depth",
            result_correlation_id="result-depth",
            planned_provider_thread_id="thread-depth",
        )
        ready = self.ready(projection=delta, plan=plan)
        self.assertEqual(ready.checkpoint_view.delta_depth, 16)
        restarted = ContextLedger(self.workspace, clock=_Clock()).inspect(self.key)
        projected = _projection(
            self.scope,
            checkpoint=restarted.checkpoint_view,
            active_provider_thread_id="thread-depth",
        )
        self.assertIs(projected.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(
            projected.bootstrap_reason, ContextBootstrapReason.MAX_DELTA_DEPTH
        )


class LedgerCodecPrivacyAndLimitTests(LedgerTestCase):
    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def test_canonical_record_is_content_free_and_retains_exact_numeric_evidence(
        self,
    ) -> None:
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,
        )
        reference = self.ledger.append_provider_call(
            self.key,
            expected_generation=reserved.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=_provider_call(
                1,
                call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
            ),
        )
        substantive = self.ledger.append_provider_call(
            self.key,
            expected_generation=reference.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=_provider_call(
                2,
                usage=ProviderUsage(
                    input_tokens=19,
                    output_tokens=6,
                    cached_input_tokens=4,
                ),
            ),
        )
        ready = self.ledger.commit_attempt(
            self.key,
            expected_generation=substantive.generation,
            authorization=_authorization(
                self.plan,
                self.projection,
                terminal_call_id="call-2",
            ),
        )
        raw = self.record_path().read_bytes()
        decoded = json.loads(raw)
        self.assertEqual(raw, self._canonical(decoded))
        self.assertEqual(
            set(decoded),
            {
                "attempt",
                "created_at",
                "generation",
                "key",
                "ready_checkpoint",
                "schema_version",
                "state",
                "updated_at",
            },
        )
        self.assertEqual(decoded["schema_version"], 2)
        persisted_accounting = decoded["attempt"]["plan"]["projection_accounting"]
        self.assertEqual(
            (
                persisted_accounting["logical_source_count"],
                persisted_accounting["logical_source_utf8_bytes"],
                persisted_accounting["projected_unique_utf8_bytes"],
                persisted_accounting["mandatory_reference_count"],
                persisted_accounting["mandatory_reference_utf8_bytes"],
            ),
            (
                self.projection.accounting.logical_source_count,
                self.projection.accounting.logical_source_utf8_bytes,
                self.projection.accounting.projected_unique_utf8_bytes,
                self.projection.accounting.mandatory_reference_count,
                self.projection.accounting.mandatory_reference_utf8_bytes,
            ),
        )
        self.assertEqual(
            decoded["attempt"]["provider_calls"][1]["accounting"]["usage"],
            {
                "cached_input_tokens": 4,
                "input_tokens": 19,
                "output_tokens": 6,
            },
        )
        self.assertEqual(
            tuple(
                item["entry"]["source_id"]
                for item in decoded["ready_checkpoint"]["sources"]
            ),
            tuple(
                source.entry.source_id
                for source in self.projection.checkpoint_proposal.sources
            ),
        )
        prohibited_keys = {
            "body",
            "body_utf8",
            "prompt",
            "excerpt",
            "candidate",
            "answer",
            "summary",
            "projected_units",
            "reference_path",
            "line_range",
            "exception",
        }

        def visit(value: object) -> None:
            if isinstance(value, dict):
                self.assertTrue(prohibited_keys.isdisjoint(value))
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(decoded)
        text = raw.decode("utf-8")
        for canary in _PRIVATE_BODY_CANARIES:
            self.assertNotIn(canary, text)
            self.assertNotIn(canary, repr(ready.record))
        self.assertNotIn("PRIVATE_REFERENCE_BODY", text)
        self.assertEqual(
            decoded["attempt"]["plan"]["mandatory_reference_count"],
            1,
        )
        self.assertGreater(
            decoded["attempt"]["plan"]["projection_accounting"][
                "mandatory_reference_utf8_bytes"
            ],
            0,
        )
        self.assertEqual(
            set(
                decoded["attempt"]["plan"]["projection_accounting"]["budgets"][
                    "mandatory_references"
                ]
            ),
            {
                "configured_utf8_bytes",
                "remaining_utf8_bytes",
                "used_utf8_bytes",
            },
        )

    def test_strict_corruption_matrix_is_nonmutating_and_maps_to_corrupt(self) -> None:
        self.ready(usage=ProviderUsage(input_tokens=0))
        path = self.record_path()
        baseline = path.read_bytes()
        value = json.loads(baseline)

        def changed(mutator: object) -> bytes:
            candidate = json.loads(baseline)
            mutator(candidate)  # type: ignore[operator]
            return self._canonical(candidate)

        cases: tuple[tuple[str, bytes], ...] = (
            ("empty", b""),
            ("truncated", baseline[:-1]),
            ("non_utf8", b"\xff"),
            ("bom", b"\xef\xbb\xbf" + baseline),
            ("pretty", json.dumps(value, indent=2).encode("utf-8")),
            (
                "duplicate_key",
                baseline.replace(
                    b'"generation":3',
                    b'"generation":3,"generation":3',
                    1,
                ),
            ),
            (
                "unknown_top_level",
                changed(lambda item: item.__setitem__("unknown", 1)),
            ),
            (
                "missing_top_level",
                changed(lambda item: item.pop("updated_at")),
            ),
            (
                "unknown_nested",
                changed(lambda item: item["attempt"]["plan"].__setitem__("unknown", 1)),
            ),
            (
                "missing_nested",
                changed(lambda item: item["attempt"]["plan"].pop("operation_id")),
            ),
            (
                "unknown_schema",
                changed(lambda item: item.__setitem__("schema_version", 3)),
            ),
            (
                "bool_integer",
                changed(lambda item: item.__setitem__("generation", True)),
            ),
            (
                "invalid_state",
                changed(lambda item: item.__setitem__("state", "retired_ready")),
            ),
            (
                "bad_scope_digest",
                changed(
                    lambda item: item["key"].__setitem__(
                        "scope_key", _digest(b"wrong-scope")
                    )
                ),
            ),
            (
                "bad_proposal_digest",
                changed(
                    lambda item: item["attempt"]["plan"].__setitem__(
                        "proposal_digest", _digest(b"wrong-proposal")
                    )
                ),
            ),
            (
                "noncanonical_sources",
                changed(
                    lambda item: item["ready_checkpoint"].__setitem__(
                        "sources",
                        list(reversed(item["ready_checkpoint"]["sources"])),
                    )
                ),
            ),
            (
                "invalid_accounting",
                changed(
                    lambda item: item["attempt"]["plan"][
                        "projection_accounting"
                    ].__setitem__("logical_source_count", True)
                ),
            ),
            (
                "invalid_usage",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0][
                        "accounting"
                    ].__setitem__(
                        "usage",
                        {
                            "cached_input_tokens": None,
                            "input_tokens": None,
                            "output_tokens": None,
                        },
                    )
                ),
            ),
            (
                "negative_usage",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0]["accounting"][
                        "usage"
                    ].__setitem__("input_tokens", -1)
                ),
            ),
            (
                "boolean_usage",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0]["accounting"][
                        "usage"
                    ].__setitem__("input_tokens", True)
                ),
            ),
            (
                "unknown_usage_field",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0]["accounting"][
                        "usage"
                    ].__setitem__("estimated", 1)
                ),
            ),
        )
        for label, payload in cases:
            with self.subTest(label=label):
                path.write_bytes(payload)
                os.chmod(path, 0o600)
                before = path.read_bytes()
                inspection = self.ledger.inspect(self.key)
                self.assertEqual(
                    (
                        inspection.generation,
                        inspection.record,
                        inspection.checkpoint_view.state,
                    ),
                    (0, None, ContextCheckpointState.CORRUPT),
                )
                self.assertEqual(path.read_bytes(), before)
        path.write_bytes(baseline)
        os.chmod(path, 0o600)
        self.assertIs(
            self.ledger.inspect(self.key).checkpoint_view.state,
            ContextCheckpointState.READY,
        )

    def test_record_source_call_generation_and_size_limits_fail_closed(self) -> None:
        calls = tuple(
            _provider_call(ordinal)
            for ordinal in range(1, MAX_CONTEXT_LEDGER_PROVIDER_CALLS + 2)
        )
        self.assert_ledger_error(
            lambda: ContextLedgerAttempt(
                self.plan,
                calls,
                ContextLedgerAttemptOutcome.IN_PROGRESS,
                "2026-08-28T00:00:00.000000Z",
                "2026-08-28T00:00:00.000000Z",
            ),
            ContextLedgerErrorCode.LIMIT_EXCEEDED,
        )
        sources = []
        for ordinal in range(MAX_CONTEXT_LEDGER_SOURCES + 1):
            entry, _material = _entry(
                f"artifact:{ordinal}",
                "x",
                ordinal=ordinal,
                source_kind=ContextSourceKind.AGENT_DEFINITION,
                channel=ContextSemanticChannel.STABLE_BEHAVIOR,
                priority=ContextPriority.P1_STABLE,
                authority=ContextAuthorityKind.AGENT_DEFINITION,
                searchable=False,
            )
            sources.append(ContextCheckpointSource(entry))
        oversized_proposal = ContextCheckpointProposal(
            self.projection.checkpoint_proposal.conditions,
            tuple(sources),
            0,
        )
        self.assert_ledger_error(
            lambda: context_checkpoint_proposal_digest(oversized_proposal),
            ContextLedgerErrorCode.LIMIT_EXCEEDED,
        )
        ready = self.ready()
        path = self.record_path()
        with path.open("r+b") as handle:
            handle.truncate(MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES + 1)
        inspection = self.ledger.inspect(self.key)
        self.assertIs(inspection.checkpoint_view.state, ContextCheckpointState.CORRUPT)
        self.assertIsNone(inspection.record)
        self.assertEqual(path.stat().st_size, MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES + 1)
        self.assertIs(ready.record.state, ContextLedgerRecordState.READY)

    def test_exact_provider_call_limit_and_max_generation_do_not_partially_advance(
        self,
    ) -> None:
        projection = _projection(self.scope)
        plan = _plan(projection)
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=plan,
        )
        current = reserved
        for ordinal in range(1, MAX_CONTEXT_LEDGER_PROVIDER_CALLS + 1):
            current = self.ledger.append_provider_call(
                self.key,
                expected_generation=current.generation,
                attempt_id=plan.attempt_id,
                provider_call=_provider_call(ordinal),
            )
        before = self.record_path().read_bytes()
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=current.generation,
                attempt_id=plan.attempt_id,
                provider_call=_provider_call(MAX_CONTEXT_LEDGER_PROVIDER_CALLS + 1),
            ),
            ContextLedgerErrorCode.LIMIT_EXCEEDED,
        )
        self.assertEqual(self.record_path().read_bytes(), before)
        self.ledger.taint_attempt(
            self.key,
            expected_generation=current.generation,
            attempt_id=plan.attempt_id,
            outcome=ContextLedgerAttemptOutcome.INCOMPLETE,
        )
        self.ledger.inspect(self.key)
        maximum_generation = (1 << 63) - 1
        maximum_value = json.loads(self.record_path().read_bytes())
        maximum_value["generation"] = maximum_generation
        self.record_path().write_bytes(self._canonical(maximum_value))
        os.chmod(self.record_path(), 0o600)
        maximum_observed = self.ledger.inspect(self.key)
        self.assertEqual(maximum_observed.generation, maximum_generation)
        self.assert_ledger_error(
            lambda: self.ledger.reserve_attempt(
                self.key,
                expected_generation=maximum_generation,
                plan=replace(plan, attempt_id="attempt-after-maximum"),
            ),
            ContextLedgerErrorCode.LIMIT_EXCEEDED,
        )


class LedgerStorageSecurityAndAtomicityTests(LedgerTestCase):
    def test_real_ledger_is_excluded_from_canonical_artifact_discovery(self) -> None:
        self.ledger.invalidate(self.key, expected_generation=0)
        paths = WritingArtifactRepository(self.workspace.root).list_paths()
        relative_record = self.record_path().relative_to(self.workspace.root).as_posix()
        relative_lock = self.lock_path().relative_to(self.workspace.root).as_posix()
        self.assertTrue(self.record_path().is_file())
        self.assertTrue(self.lock_path().is_file())
        self.assertNotIn(relative_record, paths)
        self.assertNotIn(relative_lock, paths)
        self.assertFalse(any("context_ledger" in path for path in paths))

    def test_symlink_hardlink_nonregular_and_unsafe_modes_fail_without_outside_mutation(
        self,
    ) -> None:
        for kind in (
            "record_symlink",
            "record_hardlink",
            "record_directory",
            "record_mode",
            "lock_symlink",
            "lock_directory",
            "lock_mode",
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                workspace = Workspace(Path(temporary) / "workspace").initialize()
                ledger = ContextLedger(workspace, clock=_Clock())
                key = ContextLedgerKey(self.scope, "provider")
                ledger.invalidate(key, expected_generation=0)
                digest = context_ledger_scope_key(key)[len("sha256:") :]
                directory = (
                    workspace.root / "agent_states/orchestrator_state/context_ledger/v2"
                )
                record = directory / f"{digest}.json"
                lock = directory / f"{digest}.lock"
                outside = Path(temporary) / "outside"
                outside.write_bytes(b"outside-safe")
                os.chmod(outside, 0o600)
                target = record if kind.startswith("record") else lock
                target.unlink()
                if kind.endswith("symlink"):
                    target.symlink_to(outside)
                elif kind.endswith("hardlink"):
                    os.link(outside, target)
                elif kind.endswith("directory"):
                    target.mkdir(mode=0o700)
                else:
                    target.write_bytes(b"unsafe-mode")
                    os.chmod(target, 0o644)
                if kind.startswith("record"):

                    def callable_operation():
                        return ledger.inspect(key)
                else:

                    def callable_operation():
                        return ledger.invalidate(
                            key,
                            expected_generation=1,
                        )

                with self.assertRaises(ContextLedgerError) as caught:
                    callable_operation()
                self.assertIs(
                    caught.exception.code, ContextLedgerErrorCode.STORAGE_UNSAFE
                )
                self.assertEqual(outside.read_bytes(), b"outside-safe")

    def test_workspace_root_identity_replacement_is_rejected_without_new_state(
        self,
    ) -> None:
        workspace = Workspace(
            Path(self.temporary.name) / "identity-workspace"
        ).initialize()
        ledger = ContextLedger(workspace, clock=_Clock())
        original = workspace.root.with_name("identity-workspace-original")
        workspace.root.rename(original)
        workspace.root.mkdir()
        try:
            with self.assertRaises(ContextLedgerError) as caught:
                ledger.inspect(self.key)
            self.assertIs(caught.exception.code, ContextLedgerErrorCode.STORAGE_UNSAFE)
            self.assertEqual(list(workspace.root.iterdir()), [])
        finally:
            workspace.root.rmdir()
            original.rename(workspace.root)

    def test_precommit_write_fsync_and_replace_failures_preserve_old_record_and_retry(
        self,
    ) -> None:
        injections = ("partial_write", "fsync", "replace")
        for index, injection in enumerate(injections, start=1):
            with self.subTest(injection=injection):
                scope = _scope(subject_id=f"atomic-{index}")
                key = ContextLedgerKey(scope, "provider")
                projection = _projection(scope)
                plan = _plan(
                    projection,
                    attempt_id=f"attempt-atomic-{index}",
                    operation_id=f"operation-atomic-{index}",
                    result_correlation_id=f"result-atomic-{index}",
                )
                stale = self.ledger.invalidate(key, expected_generation=0)
                path = self.record_path(key)
                before = path.read_bytes()
                real_write = ledger_module.os.write

                def partial_write(descriptor: int, payload: object) -> int:
                    value = bytes(payload)  # type: ignore[arg-type]
                    real_write(descriptor, value[:7])
                    raise OSError("PRIVATE partial write")

                patcher = (
                    mock.patch.object(
                        ledger_module.os, "write", side_effect=partial_write
                    )
                    if injection == "partial_write"
                    else mock.patch.object(
                        ledger_module.os,
                        injection,
                        side_effect=OSError(f"PRIVATE {injection}"),
                    )
                )
                with patcher:
                    self.assert_ledger_error(
                        lambda: self.ledger.reserve_attempt(
                            key,
                            expected_generation=stale.generation,
                            plan=plan,
                        ),
                        ContextLedgerErrorCode.STORAGE_FAILURE,
                    )
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(
                    any(item.name.startswith(".tmp-") for item in path.parent.iterdir())
                )
                recovered = self.ledger.reserve_attempt(
                    key,
                    expected_generation=stale.generation,
                    plan=plan,
                )
                self.assertIs(
                    recovered.record.state, ContextLedgerRecordState.INCOMPLETE
                )

    def test_post_replace_durability_failure_leaves_complete_idempotent_record(
        self,
    ) -> None:
        stale = self.ledger.invalidate(self.key, expected_generation=0)
        real_fsync = ledger_module.os.fsync
        failed = False

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal failed
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
                failed = True
                raise OSError("PRIVATE directory fsync")
            real_fsync(descriptor)

        with mock.patch.object(
            ledger_module.os,
            "fsync",
            side_effect=fail_directory_fsync,
        ):
            self.assert_ledger_error(
                lambda: self.ledger.reserve_attempt(
                    self.key,
                    expected_generation=stale.generation,
                    plan=self.plan,
                ),
                ContextLedgerErrorCode.STORAGE_FAILURE,
            )
        observed = self.ledger.inspect(self.key)
        self.assertEqual(
            (observed.generation, observed.record.state),
            (2, ContextLedgerRecordState.INCOMPLETE),
        )
        self.assertEqual(
            self.ledger.reserve_attempt(
                self.key,
                expected_generation=stale.generation,
                plan=self.plan,
            ),
            observed,
        )

    def test_control_exceptions_cleanup_temporary_and_writer_lock_for_retry(
        self,
    ) -> None:
        for index, control in enumerate(
            (KeyboardInterrupt("PRIVATE"), SystemExit(7)), start=1
        ):
            with self.subTest(control=type(control).__name__):
                scope = _scope(subject_id=f"control-{index}")
                key = ContextLedgerKey(scope, "provider")
                projection = _projection(scope)
                plan = _plan(
                    projection,
                    attempt_id=f"attempt-control-{index}",
                    operation_id=f"operation-control-{index}",
                    result_correlation_id=f"result-control-{index}",
                )
                stale = self.ledger.invalidate(key, expected_generation=0)
                before = self.record_path(key).read_bytes()
                with (
                    mock.patch.object(ledger_module.os, "replace", side_effect=control),
                    self.assertRaises(type(control)) as caught,
                ):
                    self.ledger.reserve_attempt(
                        key,
                        expected_generation=stale.generation,
                        plan=plan,
                    )
                self.assertIs(caught.exception, control)
                self.assertEqual(self.record_path(key).read_bytes(), before)
                self.assertFalse(
                    any(
                        item.name.startswith(".tmp-")
                        for item in self.record_path(key).parent.iterdir()
                    )
                )
                recovered = self.ledger.reserve_attempt(
                    key,
                    expected_generation=stale.generation,
                    plan=plan,
                )
                self.assertIs(
                    recovered.record.state, ContextLedgerRecordState.INCOMPLETE
                )

    def test_unsupported_storage_primitives_fail_before_mutation(self) -> None:
        state_root = (
            self.workspace.root / "agent_states/orchestrator_state/context_ledger"
        )
        with mock.patch.object(ledger_module, "_fcntl", None):
            self.assert_ledger_error(
                lambda: self.ledger.inspect(self.key),
                ContextLedgerErrorCode.STORAGE_UNSUPPORTED,
            )
        self.assertFalse(state_root.exists())


class LedgerConcurrencyTests(LedgerTestCase):
    def test_two_independent_writers_serialize_without_lost_update_or_deadlock(
        self,
    ) -> None:
        ledger_a = ContextLedger(self.workspace, clock=_Clock())
        ledger_b = ContextLedger(self.workspace, clock=_Clock())
        plan_a = self.plan
        plan_b = replace(self.plan, attempt_id="attempt-competing")
        ready = threading.Barrier(3)
        results: list[tuple[str, object]] = []

        def reserve(
            name: str, ledger: ContextLedger, plan: ContextLedgerAttemptPlan
        ) -> None:
            ready.wait(timeout=5)
            try:
                results.append(
                    (
                        name,
                        ledger.reserve_attempt(
                            self.key,
                            expected_generation=0,
                            plan=plan,
                        ),
                    )
                )
            except ContextLedgerError as error:
                results.append((name, error.code))

        threads = (
            threading.Thread(target=reserve, args=("a", ledger_a, plan_a)),
            threading.Thread(target=reserve, args=("b", ledger_b, plan_b)),
        )
        for thread in threads:
            thread.start()
        ready.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        successes = [
            value
            for _name, value in results
            if isinstance(value, ContextLedgerInspection)
        ]
        failures = [
            value
            for _name, value in results
            if isinstance(value, ContextLedgerErrorCode)
        ]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(failures, [ContextLedgerErrorCode.INVALID_TRANSITION])
        observed = self.ledger.inspect(self.key)
        self.assertEqual(observed, successes[0])
        self.assertIn(observed.record.attempt.plan, {plan_a, plan_b})

    def test_lock_free_reader_over_atomic_replace_returns_complete_successor(
        self,
    ) -> None:
        stale = self.ledger.invalidate(self.key, expected_generation=0)
        read_started = threading.Event()
        release_read = threading.Event()
        results: list[object] = []
        real_read = ledger_module.os.read

        def controlled_read(descriptor: int, size: int) -> bytes:
            chunk = real_read(descriptor, size)
            if (
                threading.current_thread().name == "ledger-reader"
                and not read_started.is_set()
            ):
                read_started.set()
                if not release_read.wait(timeout=5):
                    raise AssertionError("reader release timed out")
            return chunk

        def inspect() -> None:
            try:
                results.append(self.ledger.inspect(self.key))
            except BaseException as error:
                results.append(error)

        with mock.patch.object(ledger_module.os, "read", side_effect=controlled_read):
            reader = threading.Thread(target=inspect, name="ledger-reader")
            reader.start()
            self.assertTrue(read_started.wait(timeout=5))
            successor = self.ledger.reserve_attempt(
                self.key,
                expected_generation=stale.generation,
                plan=self.plan,
            )
            release_read.set()
            reader.join(timeout=5)
            self.assertFalse(reader.is_alive())
        self.assertEqual(results, [successor])
        self.assertIs(results[0].record.state, ContextLedgerRecordState.INCOMPLETE)

    def test_repeated_atomic_replacement_retries_are_bounded_at_eight(self) -> None:
        observed = self.ledger.invalidate(self.key, expected_generation=0)
        payload = self.record_path().read_bytes()
        with mock.patch.object(
            ledger_module,
            "_read_payload_once",
            return_value=(True, payload, True),
        ) as read_once:
            replay = self.ledger.inspect(self.key)
        self.assertEqual(replay, observed)
        self.assertEqual(read_once.call_count, 8)

    def test_in_place_mutation_during_read_is_storage_unsafe(self) -> None:
        self.ledger.invalidate(self.key, expected_generation=0)
        path = self.record_path()
        real_read = ledger_module.os.read
        mutated = False

        def mutate_after_read(descriptor: int, size: int) -> bytes:
            nonlocal mutated
            chunk = real_read(descriptor, size)
            if not mutated and chunk:
                mutated = True
                target = os.open(path, os.O_RDWR)
                try:
                    os.pwrite(target, b" ", path.stat().st_size - 1)
                    os.fsync(target)
                finally:
                    os.close(target)
            return chunk

        with mock.patch.object(
            ledger_module.os,
            "read",
            side_effect=mutate_after_read,
        ):
            self.assert_ledger_error(
                lambda: self.ledger.inspect(self.key),
                ContextLedgerErrorCode.STORAGE_UNSAFE,
            )
        self.assertTrue(mutated)

    @unittest.skipUnless(ledger_module._fcntl is not None, "requires POSIX flock")
    def test_cross_process_lock_contention_times_out_then_recovers(self) -> None:
        stale = self.ledger.invalidate(self.key, expected_generation=0)
        script = (
            "import fcntl, os, sys; "
            "fd=os.open(sys.argv[1], os.O_RDWR); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); "
            "sys.stdin.read(1); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)"
        )
        process = subprocess.Popen(
            [sys.executable, "-B", "-W", "error", "-c", script, str(self.lock_path())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup() -> None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(cleanup)
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline(), "locked\n")
        with mock.patch.object(
            ledger_module,
            "CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS",
            0.05,
        ):
            self.assert_ledger_error(
                lambda: self.ledger.reserve_attempt(
                    self.key,
                    expected_generation=stale.generation,
                    plan=self.plan,
                ),
                ContextLedgerErrorCode.LOCK_TIMEOUT,
            )
        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()
        process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr is not None else ""
        self.assertEqual(process.returncode, 0, stderr)
        recovered = self.ledger.reserve_attempt(
            self.key,
            expected_generation=stale.generation,
            plan=self.plan,
        )
        self.assertIs(recovered.record.state, ContextLedgerRecordState.INCOMPLETE)


class LedgerBoundaryTests(LedgerTestCase):
    def test_facades_import_direction_compile_and_graph_contract_are_exact(
        self,
    ) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 104)
        self.assertEqual(len(agent_context.__all__), 11)
        self.assertEqual(len(writing_artifacts.__all__), 62)
        self.assertTrue(
            set(ledger_module.__all__).isdisjoint(story_writing_agents.__all__)
        )
        self.assertTrue(set(ledger_module.__all__).isdisjoint(agent_context.__all__))
        source_path = _SOURCE_ROOT / "story_writing_agents/agent_context/ledger.py"
        source = source_path.read_text(encoding="utf-8")
        compile(source, os.fspath(source_path), "exec")
        tree = ast.parse(source)
        relative_dependencies = {
            (node.level, node.module)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        }
        self.assertEqual(
            relative_dependencies,
            {
                (1, "accounting"),
                (1, "manifest"),
                (1, "planning"),
                (2, "workspace"),
            },
        )
        self.assertFalse(
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                and any(
                    alias.name.startswith(("requests", "httpx", "subprocess", "socket"))
                    for alias in node.names
                )
            ]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
