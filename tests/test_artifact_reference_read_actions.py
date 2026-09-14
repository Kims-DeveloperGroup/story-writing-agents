"""Provider-free contracts and repository coordination for reference reads."""

from __future__ import annotations

import ast
import pickle
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.writing_artifacts import (
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationCorrelation,
    ReferenceInvocationState,
    ReferenceOperationState,
    ReferenceReadAction,
    ReferenceReadActionKind,
    ReferenceReadRequest,
)
from story_writing_agents import writing_artifacts as artifact_facade
from story_writing_agents.writing_artifacts import read_actions as action_module
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadInvocation,
    ReferenceReadResult,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import pristine_initialized_workspace


_STATE = Path("agent_states/orchestrator_state/writing_artifacts")
_SNAPSHOTS = _STATE / "input_snapshots"
_MARKERS = {
    "core_documents/global_outline.md": b"GLOBAL-READ-MARKER\n",
    "core_documents/part_outlines.md": b"PART-READ-MARKER\n",
    "core_documents/character_sheets.md": (
        b"CHARACTER-OMITTED\nCHARACTER-RANGE-MARKER\nCHARACTER-OMITTED\n"
    ),
    "core_documents/narrative_rules.md": b"RULES-READ-MARKER\n",
    "manuscript/part-01/reference-marker.md": b"MANUSCRIPT-READ-MARKER\n",
}


def _correlation(
    operation_id: str,
    invocation_id: str,
    ordinal: int,
    *,
    agent_id: str = PLOT_ARCHITECT,
    phase: str = "draft",
    round_number: int | None = 0,
    attempt: int = 1,
) -> ReferenceInvocationCorrelation:
    return ReferenceInvocationCorrelation(
        operation_id=operation_id,
        invocation_id=invocation_id,
        invocation_ordinal=ordinal,
        agent_id=agent_id,
        phase=phase,
        round_number=round_number,
        attempt=attempt,
    )


def _action(request: ReferenceReadRequest) -> ReferenceReadAction:
    return ReferenceReadAction(
        ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
        request.correlation,
        request.references,
    )


class ReferenceReadActionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.references = (
            ArtifactReference("core_documents/global_outline.md"),
            ArtifactReference(
                "core_documents/character_sheets.md",
                ArtifactLineRange(2, 2),
            ),
        )
        self.correlation = _correlation("operation", "invocation", 1)
        self.request = ReferenceReadRequest(self.correlation, self.references)

    def test_public_values_are_frozen_slotted_hash_safe_and_content_free(self) -> None:
        action = _action(self.request)
        self.assertEqual(
            tuple(ReferenceReadActionKind),
            (ReferenceReadActionKind.READ_WRITING_ARTIFACTS,),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ReferenceReadRequest)),
            ("correlation", "references"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ReferenceReadAction)),
            ("kind", "correlation", "references"),
        )
        for value in (self.request, action):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertIsInstance(hash(value), int)
                with self.assertRaises(FrozenInstanceError):
                    value.references = ()  # type: ignore[misc]
                rendered = repr(value)
                for marker in _MARKERS.values():
                    self.assertNotIn(marker.decode().strip(), rendered)
        self.assertNotRegex(repr(action), r"[0-9a-f]{32}")

    def test_request_and_action_enforce_exact_types_order_and_correlation(self) -> None:
        self.assertEqual(_action(self.request).references, self.references)
        for value, error in (
            ([], ValueError),
            ((), ValueError),
            (("bad",), TypeError),
        ):
            with self.subTest(references=value), self.assertRaises(error):
                ReferenceReadRequest(  # type: ignore[arg-type]
                    self.correlation,
                    value,
                )
        with self.assertRaises(TypeError):
            ReferenceReadRequest("bad", self.references)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ReferenceReadAction(
                "read_writing_artifacts",  # type: ignore[arg-type]
                self.correlation,
                self.references,
            )
        with self.assertRaises(ValueError):
            ReferenceReadAction(
                ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                self.correlation,
                self.references + (self.references[0],),
            )

    def test_result_is_closeable_hidden_and_nonserializable(self) -> None:
        contents = (b"one", b"two")
        result = ReferenceReadResult(self.request, contents)
        self.assertEqual(result.request, self.request)
        self.assertEqual(result.contents, contents)
        self.assertEqual(repr(result), "ReferenceReadResult(<content hidden>)")
        self.assertNotIn("one", repr(result))
        with self.assertRaisesRegex(TypeError, "cannot be serialized"):
            pickle.dumps(result)
        with result as entered:
            self.assertIs(entered, result)
        with self.assertRaises(ReferenceAuthorizationError) as raised:
            result.contents
        self.assertIs(raised.exception.code, ReferenceAuthorizationErrorCode.CLOSED)
        result.close()
        for bad in ((b"one",), (b"one", "two")):
            with self.subTest(contents=bad), self.assertRaises((TypeError, ValueError)):
                ReferenceReadResult(self.request, bad)  # type: ignore[arg-type]

    def test_protocol_is_module_only_and_facades_are_exact(self) -> None:
        self.assertTrue(ReferenceReadInvocation._is_protocol)
        self.assertEqual(
            tuple(
                name
                for name in action_module.__dict__
                if name.startswith("ReferenceRead")
            ),
            (
                "ReferenceReadActionKind",
                "ReferenceReadRequest",
                "ReferenceReadAction",
                "ReferenceReadResult",
                "ReferenceReadInvocation",
            ),
        )
        public = {
            "ReferenceReadActionKind",
            "ReferenceReadRequest",
            "ReferenceReadAction",
        }
        self.assertTrue(public <= set(artifact_facade.__all__))
        self.assertFalse(
            {"ReferenceReadResult", "ReferenceReadInvocation"}
            & set(artifact_facade.__all__)
        )
        self.assertTrue(
            all(name not in story_writing_agents.__all__ for name in public)
        )
        self.assertEqual(len(story_writing_agents.__all__), 106)

    def test_contract_module_has_clean_accepted_dependency_direction(self) -> None:
        source = Path(action_module.__file__)
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden = {
            "backend",
            "conversation_retrieval",
            "models",
            "orchestrator",
            "workspace",
        }
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertTrue(forbidden.isdisjoint(imported))


class ReferenceReadRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pristine_initialized_workspace(self).root
        self.repository = WritingArtifactRepository(self.root)
        for path, content in _MARKERS.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.references = (
            ArtifactReference("core_documents/global_outline.md"),
            ArtifactReference("core_documents/part_outlines.md"),
            ArtifactReference(
                "core_documents/character_sheets.md",
                ArtifactLineRange(2, 2),
            ),
            ArtifactReference("core_documents/narrative_rules.md"),
            ArtifactReference("manuscript/part-01/reference-marker.md"),
        )
        self.expected = (
            _MARKERS["core_documents/global_outline.md"],
            _MARKERS["core_documents/part_outlines.md"],
            b"CHARACTER-RANGE-MARKER\n",
            _MARKERS["core_documents/narrative_rules.md"],
            _MARKERS["manuscript/part-01/reference-marker.md"],
        )

    def authorization(self, operation_id: str):
        result = self.repository.capture_reference_authorization(
            operation_id=operation_id,
            batch=ArtifactReferenceBatch(
                "reference request",
                "reference request",
                self.references,
            ),
        )
        assert result is not None
        self.addCleanup(result.close)
        return result

    def invocation(self, operation_id: str, invocation_id: str, ordinal: int):
        authorization = self.authorization(operation_id)
        coordinator = self.repository.begin_reference_invocation(
            authorization,
            _correlation(operation_id, invocation_id, ordinal),
        )
        return authorization, coordinator

    def test_five_artifact_batch_delivers_once_in_order_and_accepts_exactly(
        self,
    ) -> None:
        authorization, invocation = self.invocation("complete", "invoke", 1)
        capability_id = invocation._capability.capability_id
        self.assertNotIn(capability_id, repr(invocation.request))
        result = invocation.handle_reference_read_action(_action(invocation.request))
        self.assertEqual(result.contents, self.expected)
        invocation.record_result_received()
        with self.assertRaises(ReferenceAuthorizationError):
            result.contents
        invocation.accept()
        self.repository.finish_reference_operation(authorization, outcome=None)

        history = self.repository.inspect_reference_operation("complete")
        assert history is not None
        self.assertEqual(
            tuple(event.operation_state for event in history.operation_events),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.ACCEPTED,
            ),
        )
        self.assertEqual(
            tuple(event.invocation_state for event in history.invocation_events[0]),
            (
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.RESULT_RECEIVED,
                ReferenceInvocationState.ACCEPTED,
            ),
        )
        evidence = b"".join(
            path.read_bytes()
            for path in (self.root / _SNAPSHOTS / "complete").rglob("*.json")
        )
        for marker in _MARKERS.values():
            self.assertNotIn(marker.strip(), evidence)

    def test_prior_delivery_and_one_use_transitions_are_exact(self) -> None:
        authorization, invocation = self.invocation("transitions", "invoke", 1)
        for method in (invocation.record_result_received, invocation.accept):
            with (
                self.subTest(method=method.__name__),
                self.assertRaises(ReferenceAuthorizationError) as raised,
            ):
                method()
            self.assertIs(
                raised.exception.code,
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION,
            )
        result = invocation.handle_reference_read_action(_action(invocation.request))
        with self.assertRaises(ReferenceAuthorizationError) as raised:
            invocation.handle_reference_read_action(_action(invocation.request))
        self.assertIs(
            raised.exception.code,
            ReferenceAuthorizationErrorCode.INVALID_TRANSITION,
        )
        with self.assertRaises(ReferenceAuthorizationError):
            result.contents
        history = self.repository.inspect_reference_operation("transitions")
        assert history is not None
        terminal = history.invocation_events[0][-1]
        self.assertIs(
            terminal.invocation_state,
            ReferenceInvocationState.TERMINATED,
        )
        self.assertIs(
            terminal.outcome,
            ReferenceAuthorizationErrorCode.INVALID_TRANSITION,
        )
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )

    def test_wrong_partial_reordered_broadened_and_foreign_actions_terminate(
        self,
    ) -> None:
        variants = (
            ("partial", self.references[:-1], None),
            ("reordered", tuple(reversed(self.references)), None),
            (
                "broadened",
                self.references
                + (
                    ArtifactReference(
                        "core_documents/character_sheets.md",
                        ArtifactLineRange(1, 1),
                    ),
                ),
                None,
            ),
            (
                "foreign",
                self.references,
                _correlation("foreign-operation", "invoke", 1),
            ),
        )
        for name, references, correlation in variants:
            with self.subTest(name=name):
                authorization, invocation = self.invocation(name, "invoke", 1)
                action = ReferenceReadAction(
                    ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                    invocation.request.correlation
                    if correlation is None
                    else correlation,
                    references,
                )
                with self.assertRaises(ReferenceAuthorizationError) as raised:
                    invocation.handle_reference_read_action(action)
                self.assertIs(
                    raised.exception.code,
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
                history = self.repository.inspect_reference_operation(name)
                assert history is not None
                terminal = history.invocation_events[0][-1]
                self.assertIs(
                    terminal.invocation_state,
                    ReferenceInvocationState.TERMINATED,
                )
                self.assertIs(
                    terminal.outcome,
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
                self.repository.finish_reference_operation(
                    authorization,
                    outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )

    def test_drift_cancel_and_fresh_retry_use_new_private_capability(self) -> None:
        authorization = self.authorization("retry")
        first = self.repository.begin_reference_invocation(
            authorization,
            _correlation("retry", "first", 1, attempt=1),
        )
        first_id = first._capability.capability_id
        first.terminate(ReferenceAuthorizationErrorCode.CANCELLED)
        second = self.repository.begin_reference_invocation(
            authorization,
            _correlation("retry", "second", 2, attempt=2),
        )
        second_id = second._capability.capability_id
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(
            second.handle_reference_read_action(_action(second.request)).contents,
            self.expected,
        )
        second.record_result_received()
        second.accept()
        self.repository.finish_reference_operation(authorization, outcome=None)

        drift_authorization, drift = self.invocation("drift", "invoke", 1)
        target = self.root / "core_documents/global_outline.md"
        target.write_bytes(b"CHANGED")
        with self.assertRaises(ReferenceAuthorizationError) as raised:
            drift.handle_reference_read_action(_action(drift.request))
        self.assertIs(raised.exception.code, ReferenceAuthorizationErrorCode.DRIFT)
        history = self.repository.inspect_reference_operation("drift")
        assert history is not None
        terminal = history.invocation_events[0][-1]
        self.assertIs(
            terminal.invocation_state,
            ReferenceInvocationState.TERMINATED,
        )
        self.assertIs(
            terminal.outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )
        self.repository.finish_reference_operation(
            drift_authorization,
            outcome=ReferenceAuthorizationErrorCode.DRIFT,
        )

    def test_unexpected_delivery_failure_terminates_with_workflow_failure(
        self,
    ) -> None:
        authorization, invocation = self.invocation("unexpected", "invoke", 1)
        unexpected = OSError("revalidation failed")
        with mock.patch.object(
            self.repository,
            "_revalidate_reference_authorization",
            side_effect=unexpected,
        ):
            with self.assertRaises(OSError) as raised:
                invocation.handle_reference_read_action(_action(invocation.request))
        self.assertIs(raised.exception, unexpected)

        history = self.repository.inspect_reference_operation("unexpected")
        assert history is not None
        terminal = history.invocation_events[0][-1]
        self.assertIs(
            terminal.invocation_state,
            ReferenceInvocationState.TERMINATED,
        )
        self.assertIs(
            terminal.outcome,
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )

    def test_acceptance_failures_terminate_with_exact_durable_outcome(self) -> None:
        for case, expected_outcome in (
            ("unexpected", ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE),
            ("drift", ReferenceAuthorizationErrorCode.DRIFT),
        ):
            with self.subTest(case=case):
                operation_id = f"accept-{case}"
                authorization, invocation = self.invocation(
                    operation_id,
                    "invoke",
                    1,
                )
                invocation.handle_reference_read_action(_action(invocation.request))
                invocation.record_result_received()

                if case == "unexpected":
                    unexpected = OSError("acceptance revalidation failed")
                    with mock.patch.object(
                        self.repository,
                        "_revalidate_reference_authorization",
                        side_effect=unexpected,
                    ):
                        with self.assertRaises(OSError) as raised:
                            invocation.accept()
                    self.assertIs(raised.exception, unexpected)
                else:
                    self.root.joinpath("core_documents/global_outline.md").write_bytes(
                        b"CHANGED"
                    )
                    with self.assertRaises(ReferenceAuthorizationError) as raised:
                        invocation.accept()
                    self.assertIs(
                        raised.exception.code,
                        ReferenceAuthorizationErrorCode.DRIFT,
                    )

                history = self.repository.inspect_reference_operation(operation_id)
                assert history is not None
                self.assertEqual(
                    tuple(
                        event.invocation_state for event in history.invocation_events[0]
                    ),
                    (
                        ReferenceInvocationState.AUTHORIZED,
                        ReferenceInvocationState.DELIVERED,
                        ReferenceInvocationState.RESULT_RECEIVED,
                        ReferenceInvocationState.TERMINATED,
                    ),
                )
                self.assertIs(
                    history.invocation_events[0][-1].outcome,
                    expected_outcome,
                )
                self.repository.finish_reference_operation(
                    authorization,
                    outcome=expected_outcome,
                )

    def test_cleanup_failure_never_replaces_primary_failure(self) -> None:
        authorization, invocation = self.invocation("cleanup", "invoke", 1)
        wrong = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references[:-1],
        )
        with mock.patch.object(
            self.repository,
            "terminate_reference_invocation",
            side_effect=RuntimeError("cleanup failed"),
        ):
            with self.assertRaises(ReferenceAuthorizationError) as raised:
                invocation.handle_reference_read_action(wrong)
        self.assertIs(
            raised.exception.code,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
        )
        authorization.close()

        authorization, invocation = self.invocation(
            "cleanup-unexpected",
            "invoke",
            1,
        )
        unexpected = OSError("revalidation failed")
        with (
            mock.patch.object(
                self.repository,
                "_revalidate_reference_authorization",
                side_effect=unexpected,
            ),
            mock.patch.object(
                self.repository,
                "terminate_reference_invocation",
                side_effect=RuntimeError("cleanup failed"),
            ),
        ):
            with self.assertRaises(OSError) as raised:
                invocation.handle_reference_read_action(_action(invocation.request))
        self.assertIs(raised.exception, unexpected)
        authorization.close()

        authorization, invocation = self.invocation(
            "cleanup-acceptance",
            "invoke",
            1,
        )
        invocation.handle_reference_read_action(_action(invocation.request))
        invocation.record_result_received()
        unexpected = OSError("acceptance revalidation failed")
        with (
            mock.patch.object(
                self.repository,
                "_revalidate_reference_authorization",
                side_effect=unexpected,
            ),
            mock.patch.object(
                self.repository,
                "terminate_reference_invocation",
                side_effect=RuntimeError("cleanup failed"),
            ),
        ):
            with self.assertRaises(OSError) as raised:
                invocation.accept()
        self.assertIs(raised.exception, unexpected)
        authorization.close()


if __name__ == "__main__":
    unittest.main()
