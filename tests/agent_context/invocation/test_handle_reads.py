'Existing HandleAndReadLifecycleTests scenarios, moved without new cases.'
from __future__ import annotations
import unittest
from dataclasses import replace
from unittest import mock
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextActionError, ContextActionErrorCode, ContextAnswerAction, ContextReadAction, ContextSourceHandle
from story_writing_agents.agent_context.manifest import ContextManifest, ContextManifestEntry
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers
from story_writing_agents.agent_context.invocation import invocation_session as owner_invocation_session
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from tests.agent_context.invocation import session_fixture as fixture_session_fixture


class HandleAndReadLifecycleTests(unittest.TestCase):
    """Verify opaque handles and atomic ordered READ behavior."""

    def setUp(self) -> None:
        self.manifest, self.snapshots = fixture_session_fixture.manifest_fixture(); self.binding = fixture_session_fixture.binding(self.manifest)

    def test_initial_read_is_impossible_and_search_handles_are_canonical_opaque(self, ) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        with self.assertRaises(ContextActionError) as initial_read:
            session.read( ContextReadAction(fixture_session_fixture.correlation(self.binding, 1),
                    (ContextSourceHandle("ctxh_v1_" + "0" * 64),), ) )
        self.assertEqual(initial_read.exception.code, ContextActionErrorCode.HANDLE_INVALID, )
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)

        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        pairs = fixture_session_fixture.mint_handles(session, registry, self.snapshots)
        self.assertEqual(tuple(entry for entry, _ in pairs), self.manifest.entries)
        self.assertEqual(len({handle.value for _, handle in pairs}), len(pairs))
        for entry, handle in pairs:
            self.assertRegex(handle.value, r"\Actxh_v1_[0-9a-f]{64}\Z"); self.assertNotIn(handle.value, repr(handle))
            self.assertNotIn(entry.provenance.locator, repr(handle))

    def test_handles_are_invocation_bound_unforgeable_and_terminal_on_reuse(self, ) -> None:
        first_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); second_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        first = fixture_session_fixture.session(self.binding, first_registry, secret=b"a" * 32)
        second = fixture_session_fixture.session(self.binding, second_registry, secret=b"b" * 32)
        first_handle = fixture_session_fixture.mint_handles(first, first_registry, self.snapshots)[0][1]
        second_handle = fixture_session_fixture.mint_handles(second, second_registry, self.snapshots)[0][1]
        self.assertNotEqual(first_handle, second_handle)
        with self.assertRaises(ContextActionError) as crossed:
            second.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (first_handle,)) )
        self.assertEqual(crossed.exception.code, ContextActionErrorCode.HANDLE_INVALID)
        self.assertEqual(second.state, ctx_call_offers.ContextInvocationState.FAILED)
        forged = ContextSourceHandle( first_handle.value[:-1] + ("0" if first_handle.value[-1] != "0" else "1") )
        third_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        third = fixture_session_fixture.session(self.binding, third_registry, secret=b"a" * 32)
        fixture_session_fixture.mint_handles(third, third_registry, self.snapshots)
        with self.assertRaises(ContextActionError):
            third.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (forged,)))
        fourth_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); fourth = fixture_session_fixture.session(self.binding, fourth_registry)
        handle = fixture_session_fixture.mint_handles(fourth, fourth_registry, self.snapshots)[0][1]
        _, receipt = fourth.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (handle,)) )
        fourth.complete_read(receipt)
        with self.assertRaises(ContextActionError) as reused:
            fourth.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 3), (handle,)))
        self.assertEqual(reused.exception.code, ContextActionErrorCode.HANDLE_INVALID)

    def test_release_handle_matrix_preserves_failure_order_and_privacy(self) -> None:
        private_values = (fixture_session_fixture.PRIVATE_BODY_A, fixture_session_fixture.PRIVATE_BODY_B, "core_documents/global_outline.md", )

        def assert_failure(session: owner_invocation_session.ContextInvocationSession,
            action: ContextReadAction, expected: ContextActionErrorCode, *, next_action_index: int,
            extra_private: tuple[str, ...] = (),) -> ContextActionError:
            with self.assertRaises(ContextActionError) as caught: session.read(action)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)
            self.assertEqual(session.next_action_index, next_action_index); encoded = repr(caught.exception)
            for value in (*private_values, *extra_private): self.assertNotIn(value, encoded)
            return caught.exception

        fresh_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); fresh = fixture_session_fixture.session(self.binding, fresh_registry)
        fresh_handle = fixture_session_fixture.mint_handles(fresh, fresh_registry, self.snapshots, )[0][1]
        _observation, receipt = fresh.read(ContextReadAction( fixture_session_fixture.correlation(self.binding, 2), (fresh_handle,), ) )
        fresh.complete_read(receipt)
        authorization = fresh.authorize_answer(ContextAnswerAction( fixture_session_fixture.correlation(self.binding, 3), fixture_session_fixture.PRIVATE_ANSWER,
                (fresh_handle,), ) )
        self.assertEqual( tuple(item.handle for item in authorization.used_evidence), (fresh_handle,), )
        self.assertEqual(fresh.state, ctx_call_offers.ContextInvocationState.ANSWER_AUTHORIZED)

        guessed = ContextSourceHandle("ctxh_v1_" + "0" * 64)
        guessed_session = fixture_session_fixture.session(self.binding, fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots))
        assert_failure(guessed_session, ContextReadAction(fixture_session_fixture.correlation(self.binding, 1), (guessed,), ),
            ContextActionErrorCode.HANDLE_INVALID, next_action_index=1, extra_private=(guessed.value,), )

        foreign_scopes = (("stale_operation", fixture_session_fixture.SCOPE, "stale_operation", "stale_result"), ("cross_scope",
                replace(fixture_session_fixture.SCOPE, session_id="other_session"), "operation", "result", ), (
                "cross_agent", replace(fixture_session_fixture.SCOPE, agent_id="plot_architect"), "operation", "result", ), )
        for label, foreign_scope, operation_id, result_id in foreign_scopes:
            with self.subTest(handle_origin=label):
                foreign_manifest, foreign_snapshots = fixture_session_fixture.manifest_fixture(foreign_scope)
                foreign_binding = fixture_session_fixture.binding(foreign_manifest, operation_id=operation_id, result_id=result_id)
                foreign_registry = fixture_session_fixture.registry(foreign_scope, foreign_snapshots)
                foreign = fixture_session_fixture.session(foreign_binding, foreign_registry, secret=b'z' * 32)
                foreign_handle = fixture_session_fixture.mint_handles(foreign, foreign_registry, foreign_snapshots, )[0][1]
                current = fixture_session_fixture.session(self.binding, fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots), secret=b'z' * 32)
                assert_failure( current, ContextReadAction( fixture_session_fixture.correlation(self.binding, 1), (foreign_handle,), ),
                    ContextActionErrorCode.HANDLE_INVALID, next_action_index=1, extra_private=(foreign_handle.value,), )

        for label, correlation in (( "cross_scope_correlation", replace( fixture_session_fixture.correlation(self.binding, 2),
                    scope=replace(fixture_session_fixture.SCOPE, session_id="other_session"), ), ), ( "cross_agent_correlation", replace(
                    fixture_session_fixture.correlation(self.binding, 2), scope=replace(fixture_session_fixture.SCOPE, agent_id="plot_architect"), ),), ):
            with self.subTest(failure_order=label):
                registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); current = fixture_session_fixture.session(self.binding, registry)
                valid_handle = fixture_session_fixture.mint_handles(current, registry, self.snapshots, )[0][1]
                assert_failure( current, ContextReadAction(correlation, (valid_handle, guessed)),
                    ContextActionErrorCode.CORRELATION_MISMATCH, next_action_index=2,
                    extra_private=(valid_handle.value, guessed.value), )
                registry.capture.assert_not_called()

        consumed_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); consumed = fixture_session_fixture.session(self.binding, consumed_registry)
        consumed_handle = fixture_session_fixture.mint_handles(consumed, consumed_registry, self.snapshots, )[0][1]
        _observation, receipt = consumed.read( ContextReadAction( fixture_session_fixture.correlation(self.binding, 2), (consumed_handle,), ) )
        consumed.complete_read(receipt)
        assert_failure(consumed, ContextReadAction(fixture_session_fixture.correlation(self.binding, 3), (consumed_handle,),),
            ContextActionErrorCode.HANDLE_INVALID, next_action_index=3, extra_private=(consumed_handle.value,),)

        drift_entry = self.manifest.entries[0]; stable = self.snapshots[drift_entry.source_id]
        drifted = fixture_session_fixture.snapshot( fixture_session_fixture.SCOPE, drift_entry.provenance.locator, "changed private body needle\n",
            revision_seed=b"changed-release-revision", )
        drift_registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); drift = fixture_session_fixture.session(self.binding, drift_registry)
        drift_handle = fixture_session_fixture.mint_handles(drift, drift_registry, self.snapshots, )[0][1]
        drift_registry.capture = mock.Mock(side_effect=(stable, drifted))
        assert_failure(drift, ContextReadAction( fixture_session_fixture.correlation(self.binding, 2), (drift_handle,),),
            ContextActionErrorCode.HANDLE_INVALID, next_action_index=3,
            extra_private=(drift_handle.value, "changed private body"),)

        terminal = fixture_session_fixture.session( self.binding, fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots), )
        terminal.authorize_answer( ContextAnswerAction( fixture_session_fixture.correlation(self.binding, 1), fixture_session_fixture.PRIVATE_ANSWER, ) )
        assert_failure(terminal, ContextReadAction( fixture_session_fixture.correlation(self.binding, 2), (guessed,),),
            ContextActionErrorCode.INVALID_TRANSITION, next_action_index=2, extra_private=(guessed.value,),)

    def test_read_captures_canonical_sources_twice_but_returns_requested_order(self, ) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        seen: list[str] = []

        def capture( _manifest: ContextManifest, entry: ContextManifestEntry, _policy: owner_capture_values.ContextSourcePolicy,
        ) -> owner_capture_values.ContextSourceSnapshot:
            seen.append(entry.source_id)
            return self.snapshots[entry.source_id]

        registry.capture = mock.Mock(side_effect=capture); session = fixture_session_fixture.session(self.binding, registry)
        handles = { entry.source_id: handle for entry, handle in fixture_session_fixture.mint_handles(session, registry, self.snapshots) }
        requested_ids = tuple((entry.source_id for entry in reversed(self.manifest.entries)))
        observation, receipt = session.read( ContextReadAction( fixture_session_fixture.correlation(self.binding, 2),
                tuple(handles[source_id] for source_id in requested_ids), ) )
        canonical = [entry.source_id for entry in self.manifest.entries]; self.assertEqual(seen, canonical + canonical)
        self.assertEqual( tuple(item.receipt.source_id for item in observation.evidence), requested_ids, )
        self.assertEqual(tuple((item.source_id for item in receipt.evidence)), requested_ids)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.READING)
        self.assertNotIn(fixture_session_fixture.PRIVATE_BODY_A, repr(receipt)); session.complete_read(receipt)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.OPEN)

    def test_second_source_failure_is_atomic_executed_and_clears_every_handle(self, ) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); canonical = tuple(self.manifest.entries)
        registry.capture = mock.Mock( side_effect=( self.snapshots[canonical[0].source_id],
                owner_capture_values.ContextSourceError(owner_capture_values.ContextSourceErrorCode.SOURCE_MISSING), ) )
        session = fixture_session_fixture.session(self.binding, registry)
        handles = tuple( handle for _, handle in reversed(fixture_session_fixture.mint_handles(session, registry, self.snapshots)) )
        with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
            session.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), handles))
        self.assertEqual(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_MISSING)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED); self.assertEqual(session.next_action_index, 3)

    def test_preflight_failure_is_not_charged_but_executed_byte_failure_is_charged(self, ) -> None:
        preflight_policy = replace( CONTEXT_ACTION_POLICY, max_handles_per_read=1, )
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        session = fixture_session_fixture.session(self.binding, registry, action_policy=preflight_policy)
        handles = tuple( handle for _, handle in fixture_session_fixture.mint_handles(session, registry, self.snapshots) )
        with self.assertRaises(ContextActionError) as caught:
            session.read( ContextReadAction( fixture_session_fixture.correlation(self.binding, 2), handles, ) )
        self.assertEqual(caught.exception.code, ContextActionErrorCode.READ_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 2); registry.capture.assert_not_called()
        byte_policy = replace( CONTEXT_ACTION_POLICY, max_snippet_utf8_bytes=10, max_excerpt_utf8_bytes=40,
            max_source_read_utf8_bytes=40, max_read_utf8_bytes=40, )
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        session = fixture_session_fixture.session(self.binding, registry, action_policy=byte_policy)
        handles = tuple( handle for _, handle in fixture_session_fixture.mint_handles(session, registry, self.snapshots) )
        with self.assertRaises(ContextActionError) as executed:
            session.read( ContextReadAction( fixture_session_fixture.correlation(self.binding, 2), handles, ) )
        self.assertEqual(executed.exception.code, ContextActionErrorCode.READ_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 3); self.assertGreater(registry.capture.call_count, 0)

    def test_post_selection_drift_and_reentrant_failure_cannot_resurrect_session(self, ) -> None:
        entry = self.manifest.entries[0]; stable = self.snapshots[entry.source_id]
        drifted = fixture_session_fixture.snapshot( fixture_session_fixture.SCOPE, entry.provenance.locator, "changed needle\n", revision_seed=b"changed", )
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); registry.capture = mock.Mock(side_effect=(stable, drifted))
        session = fixture_session_fixture.session(self.binding, registry)
        handle = dict(fixture_session_fixture.mint_handles(session, registry, self.snapshots))[entry]
        with self.assertRaises(ContextActionError) as caught:
            session.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (handle,)))
        self.assertEqual(caught.exception.code, ContextActionErrorCode.HANDLE_INVALID)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        handle = fixture_session_fixture.mint_handles(session, registry, self.snapshots)[0][1]

        def reentrant(*_args: object) -> owner_capture_values.ContextSourceSnapshot:
            session.fail()
            return self.snapshots[session.binding.manifest.entries[0].source_id]

        registry.capture = mock.Mock(side_effect=reentrant)
        with self.assertRaises(ContextActionError) as reentered:
            session.read(ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (handle,)))
        self.assertEqual(reentered.exception.code, ContextActionErrorCode.INVALID_TRANSITION)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)

    def test_outstanding_read_requires_exact_receipt_and_blocks_answer(self) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        handle = fixture_session_fixture.mint_handles(session, registry, self.snapshots)[0][1]
        _, receipt = session.read( ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (handle,)) )
        with self.assertRaises(ContextActionError) as blocked:
            session.authorize_answer( ContextAnswerAction( fixture_session_fixture.correlation(self.binding, 3), fixture_session_fixture.PRIVATE_ANSWER, (handle,) ) )
        self.assertEqual(blocked.exception.code, ContextActionErrorCode.OUTSTANDING_READ)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        handle = fixture_session_fixture.mint_handles(session, registry, self.snapshots)[0][1]
        _, receipt = session.read( ContextReadAction(fixture_session_fixture.correlation(self.binding, 2), (handle,)) )
        wrong = replace(receipt, observation_digest="sha256:" + "f" * 64)
        with self.assertRaises(ContextActionError) as mismatch: session.complete_read(wrong)
        self.assertEqual(mismatch.exception.code, ContextActionErrorCode.OUTSTANDING_READ)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)
