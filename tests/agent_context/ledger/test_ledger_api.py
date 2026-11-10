'Existing LedgerSurfaceTests scenarios, moved without new cases.'
from __future__ import annotations
import inspect
import os
import subprocess
import sys
import tempfile
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from story_writing_agents.agent_context import ledger as ledger_module
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.ledger import atomic_files as owner_atomic_files
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import checkpoint_wire as owner_checkpoint_wire
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerSurfaceTests(fixture_ledger_fixture.LedgerTestCase):
    def test_exact_surface_constants_enums_signatures_and_fixed_errors(self) -> None:
        self.assertEqual(tuple(ledger_module.__all__),
            ("CONTEXT_LEDGER_SCHEMA_VERSION",
                "MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES", "MAX_CONTEXT_LEDGER_SOURCES",
                "MAX_CONTEXT_LEDGER_PROVIDER_CALLS", "CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS",
                "ContextLedger", "ContextLedgerKey",
                "ContextLedgerRecordState", "ContextLedgerAttemptOutcome",
                "ContextLedgerAttemptPlan", "ContextLedgerProviderCall",
                "ContextLedgerAttempt", "ContextLedgerReadyCheckpoint",
                "ContextLedgerRecord", "ContextLedgerInspection",
                "ContextLedgerCommitAuthorization", "ContextLedgerError",
                "ContextLedgerErrorCode", "context_checkpoint_proposal_digest",
                "context_ledger_scope_key",),
        )
        self.assertEqual(
            (owner_record_validation.CONTEXT_LEDGER_SCHEMA_VERSION,
                owner_record_validation.MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES, owner_record_validation.MAX_CONTEXT_LEDGER_SOURCES,
                owner_record_validation.MAX_CONTEXT_LEDGER_PROVIDER_CALLS,
                owner_record_validation.CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS,),
            (2, 8 * 1024 * 1024, 4096, 32, 5.0),)
        self.assertEqual(tuple(item.value for item in owner_record_values.ContextLedgerRecordState),
            ("incomplete", "ready", "tainted", "stale"),)
        self.assertEqual(tuple(item.value for item in owner_record_values.ContextLedgerAttemptOutcome),
            ("in_progress",
                "committed", "failed",
                "cancelled", "timed_out",
                "malformed", "reference_invalid",
                "source_invalid", "output_invalid",
                "incomplete",
                "ambiguous",),
        )
        self.assertEqual(tuple(item.value for item in owner_record_validation.ContextLedgerErrorCode),
            ("invalid_value",
                "invalid_transition", "conflict",
                "commit_not_authorized", "limit_exceeded",
                "lock_timeout", "storage_unsupported",
                "storage_unsafe",
                "storage_failure",),
        )
        self.assertEqual(tuple(inspect.signature(owner_checkpoint_ledger.ContextLedger).parameters),
            ("workspace", "clock"),)
        self.assertEqual(tuple(inspect.signature(owner_checkpoint_ledger.ContextLedger.reserve_attempt).parameters),
            ("self", "key", "expected_generation", "plan"),)
        for code in owner_record_validation.ContextLedgerErrorCode:
            with self.subTest(code=code):
                error = owner_record_validation.ContextLedgerError(code)
                self.assertEqual(error.code, code)
                self.assertNotIn("PRIVATE", str(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_values_are_frozen_slotted_exact_and_reject_invalid_shapes(self) -> None:
        call = fixture_ledger_fixture.make_provider_call(1)
        values = (self.key,
            call, self.plan,
            owner_record_values.ContextLedgerAttempt(self.plan,
                (), owner_record_values.ContextLedgerAttemptOutcome.IN_PROGRESS,
                "2026-08-28T00:00:00.000000Z",
                "2026-08-28T00:00:00.000000Z",),
            owner_record_values.ContextLedgerCommitAuthorization(self.plan.attempt_id,
                self.plan.operation_id,
                self.plan.result_correlation_id,
                self.projection.checkpoint_proposal,
                self.plan.proposal_digest, "call-1",
                "thread",
                self.plan.reference_operation_id,
                "output-authorization",),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, TypeError)):
                    value.unexpected = "PRIVATE"  # type: ignore[attr-defined]
                self.assertNotIn(fixture_ledger_fixture.PRIVATE_BODY_CANARIES[0], repr(value))
        invalid = (lambda: owner_record_values.ContextLedgerKey("scope", "provider"),
            lambda: owner_record_values.ContextLedgerKey(self.scope, ""),
            lambda: owner_record_values.ContextLedgerProviderCall(0, "call", fixture_ledger_fixture.make_accounting()),
            lambda: owner_record_values.ContextLedgerProviderCall(1, "", fixture_ledger_fixture.make_accounting()),
            lambda: replace(self.plan, mandatory_reference_count=0),
            lambda: replace(self.plan, proposed_delta_depth=1),
            lambda: owner_record_values.ContextLedgerCommitAuthorization("",
                self.plan.operation_id,
                self.plan.result_correlation_id,
                self.projection.checkpoint_proposal,
                self.plan.proposal_digest, "call",
                "thread",
                self.plan.reference_operation_id,
                "output",),
            lambda: replace(fixture_ledger_fixture.make_authorization(self.plan, self.projection),
                output_action_authorization_id="",),
        )
        for operation in invalid:
            with self.subTest(operation=operation):
                self.assert_ledger_error(
                    operation, owner_record_validation.ContextLedgerErrorCode.INVALID_VALUE)

    def test_scope_and_proposal_digests_are_domain_separated_and_canonical(self,
    ) -> None:
        self.assertEqual(owner_record_values.context_ledger_scope_key(
                owner_record_values.ContextLedgerKey(fixture_ledger_fixture.make_scope(
                        agent_id="lead", workspace_id="workspace",
                        session_id="session",
                        subject_id="subject",),
                    "provider",)
            ),
            "sha256:cc2e59110562cb936b078ada7a6195136046631ddbe6f92b35a8fe4aa196d99d",)
        hostile = owner_record_values.ContextLedgerKey(fixture_ledger_fixture.make_scope(
                workspace_id="../workspace/한🙂", session_id="../../session",
                agent_id="../agent",),
            "provider/../../other",)
        scope_key = owner_record_values.context_ledger_scope_key(hostile)
        self.assertRegex(scope_key, r"\Asha256:[0-9a-f]{64}\Z")
        for private in ("workspace", "session", "agent", "provider", "/", "한"):
            self.assertNotIn(private, scope_key)
        proposal = self.projection.checkpoint_proposal
        reversed_proposal = replace(proposal,
            sources=tuple(reversed(proposal.sources)),)
        self.assertEqual(
            owner_checkpoint_wire.context_checkpoint_proposal_digest(proposal),
            owner_checkpoint_wire.context_checkpoint_proposal_digest(reversed_proposal),)
        self.assertEqual(
            owner_checkpoint_wire.context_checkpoint_proposal_digest(proposal),
            "sha256:365231f25892819500b6bc4507ccd03a83bf0bc4ef2320034bb4006c117a133c",)
        self.assertRegex(owner_checkpoint_wire.context_checkpoint_proposal_digest(proposal),
            r"\Asha256:[0-9a-f]{64}\Z",)

    def test_import_construction_and_missing_inspection_are_mutation_free(self) -> None:
        state_root = (
            self.workspace.root / "agent_states/orchestrator_state/context_ledger")
        self.assertFalse(state_root.exists())
        constructed = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock())
        self.assertFalse(state_root.exists())
        before = {path.relative_to(self.workspace.root).as_posix()
            for path in self.workspace.root.rglob("*")}
        inspection = constructed.inspect(self.key)
        after = {path.relative_to(self.workspace.root).as_posix()
            for path in self.workspace.root.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual(
            (inspection.generation,
                inspection.record,
                inspection.checkpoint_view.state,),
            (0, None, ctx_planning_inputs.ContextCheckpointState.MISSING),)
        with tempfile.TemporaryDirectory() as cwd:
            environment = dict(os.environ,
                PYTHONPATH=str(fixture_ledger_fixture.SOURCE_ROOT), PYTHONDONTWRITEBYTECODE="1",
                PYTHONWARNINGS="error",)
            result = subprocess.run([
                    sys.executable, "-B",
                    "-c",
                    "import story_writing_agents.agent_context.ledger",],
                cwd=cwd, env=environment,
                capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(cwd).iterdir()), [])

    def test_unpublished_v1_directory_is_never_read_rewritten_or_migrated(self) -> None:
        old_root = self.workspace.root / (
            "agent_states/orchestrator_state/context_ledger/v1")
        old_root.mkdir(parents=True)
        os.chmod(old_root.parent, 0o700)
        os.chmod(old_root, 0o700)
        old_record = old_root / "PRIVATE_OLD_RECORD.json"
        old_bytes = b'{"schema_version":1,"private":"PRIVATE_V1_CANARY"}'
        old_record.write_bytes(old_bytes)
        os.chmod(old_record, 0o600)
        before = {path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in old_root.rglob("*")
            if path.is_file()}
        self.assertEqual(owner_atomic_files._LEDGER_PATH,
            ("agent_states", "orchestrator_state", "context_ledger", "v2"),)
        self.assertNotIn("v1", owner_atomic_files._LEDGER_PATH)
        inspection = self.ledger.inspect(self.key)

        self.assertIs(inspection.checkpoint_view.state,
            ctx_planning_inputs.ContextCheckpointState.MISSING,)
        self.assertEqual(inspection.generation, 0)
        self.assertIsNone(inspection.record)
        self.assertFalse(self.workspace.root.joinpath(
                "agent_states/orchestrator_state/context_ledger/v2"
            ).exists())
        after = {path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in old_root.rglob("*")
            if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(old_record.read_bytes(), old_bytes)
