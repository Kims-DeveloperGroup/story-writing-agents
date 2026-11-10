'Existing LedgerAuthorizationTests scenarios, moved without new cases.'
from __future__ import annotations
from dataclasses import replace
from story_writing_agents.agent_context.accounting import ProviderCallKind, ProviderCallState
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerAuthorizationTests(fixture_ledger_fixture.LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,)
        first = self.ledger.append_provider_call(self.key,
            expected_generation=reserved.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(
                1,
                call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,),
        )
        self.appended = self.ledger.append_provider_call(self.key,
            expected_generation=first.generation, attempt_id=self.plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(2),)
        self.authorization = fixture_ledger_fixture.make_authorization(self.plan,
            self.projection,
            terminal_call_id="call-2",)

    def test_commit_rejects_every_uncorrelated_or_unverified_dimension(self) -> None:
        changed_conditions = replace(self.projection.checkpoint_proposal.conditions,
            definition_fingerprint=fixture_ledger_fixture.digest_bytes(b"changed-definition"),)
        changed_proposal = replace(self.projection.checkpoint_proposal,
            conditions=changed_conditions,)
        changed_scope_conditions = replace(self.projection.checkpoint_proposal.conditions,
            scope=fixture_ledger_fixture.make_scope(session_id="other-session"),)
        changed_scope_proposal = replace(self.projection.checkpoint_proposal,
            conditions=changed_scope_conditions,)
        changed_provider_conditions = replace(self.projection.checkpoint_proposal.conditions,
            provider_id="other-provider",)
        changed_provider_proposal = replace(self.projection.checkpoint_proposal,
            conditions=changed_provider_conditions,)
        cases = (
            (
                replace(self.authorization, attempt_id="wrong-attempt"),
                owner_record_validation.ContextLedgerErrorCode.INVALID_TRANSITION,),
            (replace(self.authorization, operation_id="wrong-operation"),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(self.authorization, result_correlation_id="wrong-result"),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(self.authorization, proposal=changed_proposal),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(self.authorization, proposal=changed_scope_proposal),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(self.authorization, proposal=changed_provider_proposal),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(
                    self.authorization,
                    revalidated_proposal_digest=fixture_ledger_fixture.digest_bytes(b"wrong-proposal"),),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(self.authorization, terminal_call_id="call-1"),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
            (replace(self.authorization, completed_reference_operation_id=None),
                owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,),
        )
        for authorization, code in cases:
            with self.subTest(field=authorization):
                before = self.record_path().read_bytes()
                self.assert_ledger_error(
                    lambda authorization=authorization: self.ledger.commit_attempt(self.key,
                        expected_generation=self.appended.generation,
                        authorization=authorization,),
                    code,)
                self.assertEqual(self.record_path().read_bytes(), before)

    def test_only_submitted_substantive_terminal_call_can_commit(self) -> None:
        for index, (kind, state) in enumerate(
            (
                (
                    ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
                    ProviderCallState.SUBMITTED,),
                (ProviderCallKind.UNCLASSIFIED, ProviderCallState.SUBMITTED),
                (ProviderCallKind.CONVERSATION, ProviderCallState.PREPARED),),
            start=1,):
            with self.subTest(kind=kind, state=state):
                scope = fixture_ledger_fixture.make_scope(subject_id=f"terminal-{index}")
                key = owner_record_values.ContextLedgerKey(scope, "provider")
                projection = fixture_ledger_fixture.make_projection(scope)
                plan = fixture_ledger_fixture.make_plan(
                    projection, attempt_id=f"attempt-terminal-{index}",
                    operation_id=f"operation-terminal-{index}",
                    result_correlation_id=f"result-terminal-{index}",)
                reserved = self.ledger.reserve_attempt(
                    key,
                    expected_generation=0,
                    plan=plan,)
                appended = self.ledger.append_provider_call(key,
                    expected_generation=reserved.generation,
                    attempt_id=plan.attempt_id,
                    provider_call=fixture_ledger_fixture.make_provider_call(1, call_kind=kind, state=state),)
                self.assert_ledger_error(lambda: self.ledger.commit_attempt(
                        key, expected_generation=appended.generation,
                        authorization=fixture_ledger_fixture.make_authorization(plan, projection),),
                    owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,)
