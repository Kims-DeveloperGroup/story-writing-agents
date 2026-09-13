"""Runtime lead, management, and reference-inspection public boundaries."""

from __future__ import annotations

import inspect
import multiprocessing
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock

import story_writing_agents
from story_writing_agents import RuntimeSession, StoryRuntime
from story_writing_agents.agent_context.manifest import (
    ContextScope,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.sources import (
    CONTEXT_SOURCE_POLICY,
    create_guarded_writing_artifact_source_adapter,
)
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.errors import WorkspaceSecurityError

from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
    provision_session_resources,
)
from story_writing_agents.writing_artifacts.runtime_references import (
    prepare_guarded_runtime_reference_operation,
    prepare_registered_guarded_runtime_reference_operation,
    prepare_runtime_reference_operation,
    reference_operation_belongs_to_session,
    reference_operation_session_id,
)
from story_writing_agents.writing_artifacts import (
    runtime_references as runtime_references_module,
)
from story_writing_agents.writing_artifacts.authorization import (
    ReferenceAuthorizationErrorCode,
    ReferenceAuthorizationError,
    ReferenceInvocationCorrelation,
    ReferenceInvocationState,
    ReferenceOperationFailure,
    ReferenceOperationState,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)
from story_writing_agents.writing_artifacts.references import (
    ArtifactReference,
    ArtifactReferenceBatch,
)

from tests.helpers import initialized_workspace, pristine_initialized_workspace


REMOVED_RUNTIME_METHODS = (
    "converse",
    "converse_with_retrieval",
    "run_prompt",
    "run_draft",
)


class RuntimeExecutionRetirementTests(unittest.TestCase):
    @staticmethod
    def _reference_batch(
        reference: ArtifactReference,
    ) -> ArtifactReferenceBatch:
        return ArtifactReferenceBatch(
            "Read one guarded reference.",
            "Read one guarded reference.",
            (reference,),
        )

    @staticmethod
    def _reference_correlation(
        operation_id: str,
        invocation_id: str,
        ordinal: int,
    ) -> ReferenceInvocationCorrelation:
        return ReferenceInvocationCorrelation(
            operation_id=operation_id,
            invocation_id=invocation_id,
            invocation_ordinal=ordinal,
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
            attempt=1,
        )

    def _assert_content_free_cleanup_pending(self, operation) -> None:
        self.assertFalse(operation.finished)
        self.assertTrue(operation.cleanup_is_pending())
        self.assertEqual(operation._pending, {})
        self.assertIsNone(operation._authorization)
        self.assertIsNone(operation._repository)
        self.assertIsNone(operation._repository_access)

    @staticmethod
    def _physical_pin_factory(binding):
        def duplicate() -> int:
            with binding.open_ready_resources() as access:
                return access.duplicate_worktree_descriptor()

        return duplicate

    def _assert_physical_pin_open(self, operation) -> int:
        descriptor = operation._physical_workspace_pin
        self.assertIs(type(descriptor), int)
        os.fstat(descriptor)
        return descriptor

    def test_descriptor_and_owner_pin_cleanup_preserve_active_baseexception(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        repository = WritingArtifactRepository(workspace.root)
        direct_descriptor = os.open(workspace.root, os.O_RDONLY)
        owner_descriptor = os.open(workspace.root, os.O_RDONLY)
        identity = os.fstat(owner_descriptor)
        operation = runtime_references_module.RuntimeReferenceOperation(
            repository,
            session_id="novel",
            batch=None,
            source_text="plain text",
            provider_text="plain text",
            operation_id=None,
            authorization=None,
            physical_workspace_identity=(identity.st_dev, identity.st_ino),
            physical_workspace_pin=owner_descriptor,
        )
        original_close = os.close
        close_attempts = []

        def close_then_fail(descriptor):
            close_attempts.append(descriptor)
            original_close(descriptor)
            raise SystemExit("physical pin close interrupted")

        direct_failure = RuntimeError("direct descriptor owner failed")
        owner_failure = KeyboardInterrupt("operation cleanup interrupted")
        with mock.patch.object(
            runtime_references_module.os,
            "close",
            side_effect=close_then_fail,
        ):
            runtime_references_module._close_descriptor(
                direct_descriptor,
                failure=direct_failure,
            )
            with mock.patch.object(
                runtime_references_module.RuntimeReferenceOperation,
                "_defer_cleanup_locked",
                autospec=True,
                side_effect=owner_failure,
            ), self.assertRaises(KeyboardInterrupt) as caught:
                operation._abandon_unretained()
        self.assertIs(caught.exception, owner_failure)
        self.assertEqual(close_attempts, [direct_descriptor, owner_descriptor])
        self.assertIsNone(operation._physical_workspace_pin)
        for failure in (direct_failure, owner_failure):
            self.assertIn(
                "physical workspace pin cleanup failed: SystemExit",
                failure.__notes__,
            )
        for descriptor in close_attempts:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def _assert_descriptor_closed(self, descriptor: int) -> None:
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    @staticmethod
    def _reference_snapshot_names(binding):
        with binding.open_ready_resources() as access:
            evidence = access.repository._reference_evidence
            try:
                return evidence._storage.list_names(
                    evidence._snapshots_directory
                )
            except FileNotFoundError:
                return ()

    @staticmethod
    def _reference_history(binding, operation_id):
        with binding.open_ready_resources() as access:
            return access.repository._reference_evidence.inspect(operation_id)

    def _complete_fresh_cleanup(self, binding, operation) -> None:
        with binding.open_ready_resources() as access:
            operation.reconcile_cleanup(
                access.repository,
                physical_workspace_identity=binding.worktree_identity,
            )
        operation.complete_reconciled_cleanup()
        self.assertTrue(operation.finished)
        self.assertFalse(operation.cleanup_is_pending())

    def _cleanup_runtime_registry(self, workspace_root: str) -> None:
        with StoryRuntime._reference_operation_registry_lock:
            for key in tuple(StoryRuntime._reference_operation_registry):
                if key[0] == workspace_root:
                    StoryRuntime._reference_operation_registry.pop(key)

    def test_runtime_methods_aliases_and_forwarding_are_absent(self) -> None:
        for runtime_type in (StoryRuntime, RuntimeSession):
            for name in REMOVED_RUNTIME_METHODS:
                with self.subTest(runtime_type=runtime_type.__name__, name=name):
                    self.assertFalse(hasattr(runtime_type, name))
                    self.assertNotIn(name, runtime_type.__dict__)
        for alias in ("Runtime", "Session"):
            self.assertNotIn(alias, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, alias))
            self.assertFalse(hasattr(orchestrator_module, alias))

    def test_management_directive_configuration_and_inspection_remain(self) -> None:
        workspace = initialized_workspace(self)
        runtime = StoryRuntime(workspace)
        session = runtime.session("managed")
        self.assertIs(type(session), RuntimeSession)
        self.assertIs(session.runtime, runtime)
        self.assertEqual(session.session_id, "managed")

        expected_runtime = (
            "session",
            "lead_turn",
            "propose_specialist_advisory",
            "decide_lead_step",
            "apply_directive",
            "set_runtime_overrides",
            "prepare_runtime_configuration",
            "commit_runtime_configuration",
            "discard_runtime_configuration",
            "inspect_session",
            "inspect_debate",
            "inspect_debate_context",
            "inspect_reference_operation",
        )
        expected_session = (
            "lead_turn",
            "propose_specialist_advisory",
            "decide_lead_step",
            "apply_directive",
            "set_overrides",
            "inspect",
            "inspect_debate",
            "inspect_debate_context",
            "inspect_reference_operation",
        )
        for name in expected_runtime:
            self.assertTrue(callable(getattr(runtime, name)))
        for name in expected_session:
            self.assertTrue(callable(getattr(session, name)))

        self.assertEqual(
            session.apply_directive("[SYS: LANG=KO]"),
            {"language": "KO", "persona": None, "agents": {}},
        )
        self.assertEqual(session.inspect()["session_id"], "managed")

    def test_management_construction_and_inspection_do_not_bind_or_call_provider(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        backend = mock.Mock()
        backend.backend_overrides = {}
        backend.provider_route_generations.return_value = {}
        with mock.patch.object(
            orchestrator_module,
            "backend_configuration_snapshot",
            return_value=None,
        ):
            runtime = StoryRuntime(workspace, backend)  # type: ignore[arg-type]
        runtime.inspect_session("managed")
        for name in ("bind", "converse", "converse_with_retrieval", "run"):
            candidate = getattr(backend, name)
            candidate.assert_not_called()

    def test_runtime_signatures_do_not_hide_removed_keyword_paths(self) -> None:
        constructor = inspect.signature(StoryRuntime)
        self.assertNotIn("progress_sink", constructor.parameters)
        self.assertNotIn("context_execution_sink", constructor.parameters)
        session_constructor = inspect.signature(RuntimeSession)
        self.assertEqual(
            tuple(session_constructor.parameters), ("runtime", "session_id")
        )
        expected = {
            StoryRuntime.lead_turn: (
                "self",
                "session_id",
                "conversation_id",
                "text",
                "context_event_sink",
                "action_progress_sink",
            ),
            StoryRuntime.decide_lead_step: ("self", "decision", "action_progress_sink"),
            StoryRuntime.decide_artifact_application: ("self", "decision"),
            StoryRuntime.decide_story_commit: ("self", "decision"),
            StoryRuntime.propose_specialist_advisory: (
                "self",
                "session_id",
                "conversation_id",
                "specialist_id",
                "text",
                "context_event_sink",
                "action_progress_sink",
                "authoring_scope",
                "review_basis",
            ),
            RuntimeSession.lead_turn: (
                "self",
                "conversation_id",
                "text",
                "context_event_sink",
                "action_progress_sink",
            ),
            RuntimeSession.decide_lead_step: ("self", "decision", "action_progress_sink"),
            RuntimeSession.decide_artifact_application: ("self", "decision"),
            RuntimeSession.decide_story_commit: ("self", "decision"),
            RuntimeSession.propose_specialist_advisory: (
                "self",
                "conversation_id",
                "specialist_id",
                "text",
                "context_event_sink",
                "action_progress_sink",
                "authoring_scope",
                "review_basis",
            ),
        }
        for function, parameters in expected.items():
            with self.subTest(function=function.__qualname__):
                self.assertTrue(inspect.iscoroutinefunction(function))
                signature = inspect.signature(function)
                self.assertEqual(tuple(signature.parameters), parameters)
                self.assertNotIn("agent_id", signature.parameters)
                if function in (
                    StoryRuntime.propose_specialist_advisory,
                    RuntimeSession.propose_specialist_advisory,
                ):
                    self.assertEqual(
                        tuple(name for name, parameter in signature.parameters.items()
                              if parameter.kind is inspect.Parameter.KEYWORD_ONLY),
                        ("action_progress_sink", "authoring_scope", "review_basis"),
                    )

    def test_session_reference_identity_and_persisted_ledger_fail_closed(self) -> None:
        workspace = pristine_initialized_workspace(self)
        ready = provision_session_resources(workspace.root, "novel")
        source = ready.identity.worktree_root / "core_documents/global_outline.md"
        source.write_text("Session-only reference.\n", encoding="utf-8")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        with binding.open_ready_resources() as access:
            operation = prepare_runtime_reference_operation(
                access.repository,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
            )
        operation_id = operation.operation_id
        assert operation_id is not None

        self.assertEqual(reference_operation_session_id(operation_id), "novel")
        self.assertTrue(
            reference_operation_belongs_to_session(operation_id, "novel")
        )
        runtime = StoryRuntime(workspace)
        history = runtime.inspect_reference_operation(operation_id)
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(history.manifest.operation_id, operation_id)
        restarted = StoryRuntime(workspace)
        self.assertEqual(
            restarted.session("novel").inspect_reference_operation(operation_id),
            history,
        )

        foreign_ready = provision_session_resources(workspace.root, "foreign")
        foreign_source = (
            foreign_ready.identity.worktree_root
            / "core_documents/global_outline.md"
        )
        foreign_source.write_text("Foreign reference.\n", encoding="utf-8")
        foreign_binding = compose_session_artifact_binding(
            workspace.root,
            "foreign",
        )
        with foreign_binding.open_ready_resources() as access:
            foreign = prepare_runtime_reference_operation(
                access.repository,
                session_id="foreign",
                text="Read ${core_documents/global_outline.md}.",
            ).operation_id
        assert foreign is not None
        with self.assertRaisesRegex(ValueError, "does not belong"):
            restarted.session("novel").inspect_reference_operation(foreign)

        malformed = (
            "rr_00__" + "0" * 32,
            "rr_05_novel_" + "g" * 32,
            operation_id + "0",
            "not_an_operation",
        )
        for invalid in malformed:
            with self.subTest(operation_id=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "reference operation ID is invalid",
                ):
                    reference_operation_session_id(invalid)
                self.assertFalse(
                    reference_operation_belongs_to_session(invalid, "novel")
                )
                with self.assertRaises(ValueError):
                    restarted.inspect_reference_operation(invalid)

    def test_guarded_security_failure_propagates_while_public_reference_api_converts(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        failure = WorkspaceSecurityError("guarded root identity changed")
        with mock.patch.object(
            WritingArtifactRepository,
            "capture_reference_authorization",
            side_effect=failure,
        ):
            with self.assertRaises(WorkspaceSecurityError) as guarded:
                prepare_guarded_runtime_reference_operation(
                    repository_access,
                    session_id="novel",
                    text="Read ${core_documents/global_outline.md}.",
                    physical_workspace_identity=binding.worktree_identity,
                    physical_workspace_pin_factory=self._physical_pin_factory(
                        binding
                    ),
                )
            self.assertIs(guarded.exception, failure)

            with binding.open_ready_resources() as access:
                with self.assertRaises(ReferenceOperationFailure) as public:
                    prepare_runtime_reference_operation(
                        access.repository,
                        session_id="novel",
                        text="Read ${core_documents/global_outline.md}.",
                    )
            self.assertIs(
                public.exception.code,
                ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
            )

    def test_guarded_physical_pin_is_bounded_to_reference_lifecycle(self) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        no_reference_supplier = mock.Mock(
            side_effect=AssertionError("plain text must not acquire a root pin")
        )
        plain = prepare_guarded_runtime_reference_operation(
            repository_access,
            session_id="novel",
            text="No writing artifact reference.",
            physical_workspace_identity=binding.worktree_identity,
            physical_workspace_pin_factory=no_reference_supplier,
        )
        no_reference_supplier.assert_not_called()
        self.assertIsNone(plain._physical_workspace_pin)
        plain.finish()

        successful = prepare_guarded_runtime_reference_operation(
            repository_access,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
            physical_workspace_identity=binding.worktree_identity,
            physical_workspace_pin_factory=self._physical_pin_factory(binding),
        )
        successful_pin = self._assert_physical_pin_open(successful)
        invocation = successful.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        assert invocation is not None
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references,
        )
        result = invocation.handle_reference_read_action(action)
        self.assertEqual(result.contents, (b"",))
        invocation.record_result_received()
        invocation.accept()
        successful.finish()
        self.assertTrue(successful.finished)
        self._assert_descriptor_closed(successful_pin)

        failed = prepare_guarded_runtime_reference_operation(
            repository_access,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
            physical_workspace_identity=binding.worktree_identity,
            physical_workspace_pin_factory=self._physical_pin_factory(binding),
        )
        failed_pin = self._assert_physical_pin_open(failed)
        terminal = failed.failure(RuntimeError("provider stopped"))
        self.assertIsNotNone(terminal)
        self.assertTrue(failed.finished)
        self._assert_descriptor_closed(failed_pin)

    def test_guarded_physical_pin_closes_on_preparation_failures(self) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        before = self._reference_snapshot_names(binding)
        supplier_marker = RuntimeError("pin supplier rejected")
        with mock.patch.object(
            WritingArtifactRepository,
            "capture_reference_authorization",
            side_effect=AssertionError("capture must follow pin validation"),
        ) as capture, self.assertRaises(RuntimeError) as supplier_failure:
            prepare_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=mock.Mock(
                    side_effect=supplier_marker
                ),
            )
        self.assertIs(supplier_failure.exception, supplier_marker)
        capture.assert_not_called()
        self.assertEqual(self._reference_snapshot_names(binding), before)

        for invalid_pin in (-1, os.open(workspace.root, os.O_RDONLY)):
            with self.subTest(invalid_pin=invalid_pin), mock.patch.object(
                WritingArtifactRepository,
                "capture_reference_authorization",
                side_effect=AssertionError("capture must follow pin validation"),
            ) as capture, self.assertRaises((TypeError, WorkspaceSecurityError)):
                prepare_guarded_runtime_reference_operation(
                    repository_access,
                    session_id="novel",
                    text="Read ${core_documents/global_outline.md}.",
                    physical_workspace_identity=binding.worktree_identity,
                    physical_workspace_pin_factory=lambda: invalid_pin,
                )
            capture.assert_not_called()
            if invalid_pin >= 0:
                self._assert_descriptor_closed(invalid_pin)
            self.assertEqual(self._reference_snapshot_names(binding), before)

        postcheck_marker = RuntimeError("guard postcheck rejected")
        postcheck_pins: list[int] = []

        @contextmanager
        def rejected_repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository
            raise postcheck_marker

        def capture_pin() -> int:
            descriptor = self._physical_pin_factory(binding)()
            postcheck_pins.append(descriptor)
            return descriptor

        with self.assertRaises(RuntimeError) as postcheck_failure:
            prepare_guarded_runtime_reference_operation(
                rejected_repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=capture_pin,
            )
        self.assertIs(postcheck_failure.exception, postcheck_marker)
        self.assertEqual(len(postcheck_pins), 1)
        self._assert_descriptor_closed(postcheck_pins[0])

        registered: list[object] = []
        authorizations = []
        register_marker = RuntimeError("registry rejected")

        def reject_registration(operation) -> None:
            registered.append(operation)
            authorizations.append(operation._authorization)
            raise register_marker

        histories_before_callback = self._reference_snapshot_names(binding)
        with self.assertRaises(RuntimeError) as registration_failure:
            prepare_registered_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=self._physical_pin_factory(binding),
                register=reject_registration,
            )
        self.assertIs(registration_failure.exception, register_marker)
        self.assertEqual(len(registered), 1)
        abandoned = registered[0]
        self._assert_content_free_cleanup_pending(abandoned)
        self.assertIsNone(abandoned._physical_workspace_pin)
        self.assertEqual(len(authorizations), 1)
        self.assertTrue(authorizations[0]._closed)
        self.assertEqual(authorizations[0]._captured, ())
        callback_names = tuple(
            item
            for item in self._reference_snapshot_names(binding)
            if item not in histories_before_callback
        )
        self.assertEqual(callback_names, (abandoned.operation_id,))
        callback_history = self._reference_history(
            binding,
            abandoned.operation_id,
        )
        self.assertIsNotNone(callback_history)
        self.assertEqual(
            tuple(
                event.operation_state
                for event in callback_history.operation_events
            ),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
            ),
        )

    def test_unregistered_constructor_failure_terminalizes_captured_authority(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        pins: list[int] = []
        authorizations = []

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        def capture_pin() -> int:
            descriptor = self._physical_pin_factory(binding)()
            pins.append(descriptor)
            return descriptor

        original_capture = WritingArtifactRepository.capture_reference_authorization

        def capture_authorization(repository, **kwargs):
            authorization = original_capture(repository, **kwargs)
            authorizations.append(authorization)
            return authorization

        marker = RuntimeError("operation construction rejected")
        histories_before = self._reference_snapshot_names(binding)
        with mock.patch.object(
            WritingArtifactRepository,
            "capture_reference_authorization",
            new=capture_authorization,
        ), mock.patch.object(
            runtime_references_module,
            "RuntimeReferenceOperation",
            side_effect=marker,
        ), self.assertRaises(RuntimeError) as caught:
            prepare_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=capture_pin,
            )

        self.assertIs(caught.exception, marker)
        self.assertEqual(len(pins), 1)
        self._assert_descriptor_closed(pins[0])
        self.assertEqual(len(authorizations), 1)
        self.assertTrue(authorizations[0]._closed)
        self.assertEqual(authorizations[0]._captured, ())
        operation_id = authorizations[0].manifest.operation_id
        history_names = tuple(
            item
            for item in self._reference_snapshot_names(binding)
            if item not in histories_before
        )
        self.assertEqual(history_names, (operation_id,))
        history = self._reference_history(binding, operation_id)
        self.assertIsNotNone(history)
        self.assertEqual(
            tuple(
                (event.operation_state, event.outcome)
                for event in history.operation_events
            ),
            (
                (ReferenceOperationState.REQUESTED, None),
                (ReferenceOperationState.CAPTURED, None),
                (
                    ReferenceOperationState.TERMINATED,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                ),
            ),
        )

    def test_guarded_binding_failure_identity_survives_every_reference_phase(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        ready = provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        blocked: list[BaseException | None] = [None]

        @contextmanager
        def repository_access():
            if blocked[0] is not None:
                raise blocked[0]
            with binding.open_ready_resources() as access:
                yield access.repository

        def operation():
            return prepare_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=self._physical_pin_factory(binding),
            )

        preparation_marker = SessionArtifactBindingError("preparation marker")
        with mock.patch.object(
            WritingArtifactRepository,
            "capture_reference_authorization",
            side_effect=preparation_marker,
        ), self.assertRaises(SessionArtifactBindingError) as preparation:
            operation()
        self.assertIs(preparation.exception, preparation_marker)

        with binding.open_ready_resources() as access:
            with mock.patch.object(
                WritingArtifactRepository,
                "capture_reference_authorization",
                side_effect=preparation_marker,
            ), self.assertRaises(ReferenceOperationFailure) as public_prepare:
                prepare_runtime_reference_operation(
                    access.repository,
                    session_id="novel",
                    text="Read ${core_documents/global_outline.md}.",
                )
        self.assertIs(
            public_prepare.exception.code,
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )
        self.assertIs(public_prepare.exception.__cause__, preparation_marker)

        begin_operation = operation()
        begin_marker = SessionArtifactBindingError("begin marker")
        blocked[0] = begin_marker
        with self.assertRaises(SessionArtifactBindingError) as begin:
            begin_operation.begin_invocation(
                agent_id=PLOT_ARCHITECT,
                phase="draft",
                round_number=None,
            )
        self.assertIs(begin.exception, begin_marker)
        self._assert_content_free_cleanup_pending(begin_operation)
        supplied_marker = SessionArtifactBindingError("supplied marker")
        with self.assertRaises(SessionArtifactBindingError) as supplied:
            begin_operation.failure(supplied_marker)
        self.assertIs(supplied.exception, supplied_marker)
        blocked[0] = None
        self._complete_fresh_cleanup(binding, begin_operation)

        action_operation = operation()
        invocation = action_operation.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        assert invocation is not None
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references,
        )
        action_marker = SessionArtifactBindingError("action marker")
        blocked[0] = action_marker
        with self.assertRaises(SessionArtifactBindingError) as action_failure:
            invocation.handle_reference_read_action(action)
        self.assertIs(action_failure.exception, action_marker)
        self._assert_content_free_cleanup_pending(action_operation)
        with self.assertRaises(SessionArtifactBindingError) as action_cleanup:
            action_operation.failure(action_marker)
        self.assertIs(action_cleanup.exception, action_marker)
        blocked[0] = None
        self._complete_fresh_cleanup(binding, action_operation)

        terminal_operation = operation()
        terminal_marker = SessionArtifactBindingError("terminal marker")
        blocked[0] = terminal_marker
        with self.assertRaises(SessionArtifactBindingError) as terminal:
            terminal_operation.finish()
        self.assertIs(terminal.exception, terminal_marker)
        self._assert_content_free_cleanup_pending(terminal_operation)
        with self.assertRaises(SessionArtifactBindingError) as terminal_cleanup:
            terminal_operation.failure(terminal_marker)
        self.assertIs(terminal_cleanup.exception, terminal_marker)
        blocked[0] = None
        self._complete_fresh_cleanup(binding, terminal_operation)

        deferred_operation = operation()
        deferred_invocation = deferred_operation.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        assert deferred_invocation is not None
        deferred_marker = SessionArtifactBindingError("deferred cleanup marker")
        blocked[0] = deferred_marker
        with self.assertRaises(SessionArtifactBindingError) as deferred:
            deferred_operation.failure(RuntimeError("provider failed"))
        self.assertIs(deferred.exception, deferred_marker)
        self._assert_content_free_cleanup_pending(deferred_operation)
        blocked[0] = None
        self._complete_fresh_cleanup(binding, deferred_operation)

        public_repository = WritingArtifactRepository(ready.identity.worktree_root)
        public_operation = prepare_runtime_reference_operation(
            public_repository,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
        )
        public_failure = public_operation.failure(
            SessionArtifactBindingError("public marker")
        )
        self.assertIs(type(public_failure), ReferenceOperationFailure)
        assert public_failure is not None
        self.assertIs(
            public_failure.code,
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )

    def test_retained_guarded_repository_delivery_rejection_preserves_retry(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        reference = ArtifactReference("core_documents/global_outline.md")
        with binding.open_ready_resources() as access:
            repository = access.repository
            authorization = repository.capture_reference_authorization(
                operation_id="guarded_delivery_retry",
                batch=self._reference_batch(reference),
            )
            assert authorization is not None
            capability = repository.authorize_reference_invocation(
                authorization,
                self._reference_correlation(
                    "guarded_delivery_retry",
                    "invoke",
                    1,
                ),
            )

        with self.assertRaises(SessionArtifactBindingError):
            repository.deliver_authorized_references(capability, (reference,))

        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                "guarded_delivery_retry"
            )
            assert history is not None
            self.assertEqual(
                tuple(
                    event.invocation_state
                    for event in history.invocation_events[0]
                ),
                (ReferenceInvocationState.AUTHORIZED,),
            )
            delivery = access.repository.deliver_authorized_references(
                capability,
                (reference,),
            )
            self.assertEqual(delivery.contents, (b"",))
            delivery.close()
            access.repository.terminate_reference_invocation(
                capability,
                ReferenceAuthorizationErrorCode.CANCELLED,
            )
            access.repository.finish_reference_operation(
                authorization,
                outcome=ReferenceAuthorizationErrorCode.CANCELLED,
            )

    def test_retained_guarded_repository_termination_rejection_preserves_cleanup(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        reference = ArtifactReference("core_documents/global_outline.md")
        with binding.open_ready_resources() as access:
            repository = access.repository
            authorization = repository.capture_reference_authorization(
                operation_id="guarded_termination_retry",
                batch=self._reference_batch(reference),
            )
            assert authorization is not None
            capability = repository.authorize_reference_invocation(
                authorization,
                self._reference_correlation(
                    "guarded_termination_retry",
                    "invoke",
                    1,
                ),
            )

        with self.assertRaises(SessionArtifactBindingError):
            repository.terminate_reference_invocation(
                capability,
                ReferenceAuthorizationErrorCode.CANCELLED,
            )

        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                "guarded_termination_retry"
            )
            assert history is not None
            self.assertEqual(
                tuple(
                    event.invocation_state
                    for event in history.invocation_events[0]
                ),
                (ReferenceInvocationState.AUTHORIZED,),
            )
            access.repository.terminate_reference_invocation(
                capability,
                ReferenceAuthorizationErrorCode.CANCELLED,
            )
            access.repository.finish_reference_operation(
                authorization,
                outcome=ReferenceAuthorizationErrorCode.CANCELLED,
            )

    def test_foreign_thread_cannot_consume_or_retire_retained_guarded_authority(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        reference = ArtifactReference("core_documents/global_outline.md")
        with binding.open_ready_resources() as access:
            repository = access.repository
            authorization = repository.capture_reference_authorization(
                operation_id="foreign_guarded_authority",
                batch=self._reference_batch(reference),
            )
            assert authorization is not None
            deliver_capability = repository.authorize_reference_invocation(
                authorization,
                self._reference_correlation(
                    "foreign_guarded_authority",
                    "deliver",
                    1,
                ),
            )
            terminate_capability = repository.authorize_reference_invocation(
                authorization,
                self._reference_correlation(
                    "foreign_guarded_authority",
                    "terminate",
                    2,
                ),
            )
            failures: list[BaseException] = []

            def misuse_retained_authority() -> None:
                for action in (
                    lambda: repository.deliver_authorized_references(
                        deliver_capability,
                        (reference,),
                    ),
                    lambda: repository.terminate_reference_invocation(
                        terminate_capability,
                        ReferenceAuthorizationErrorCode.CANCELLED,
                    ),
                ):
                    try:
                        action()
                    except BaseException as exc:
                        failures.append(exc)

            thread = threading.Thread(target=misuse_retained_authority)
            thread.start()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(failures), 2)
            self.assertTrue(
                all(type(failure) is SessionArtifactBindingError for failure in failures)
            )
            history = repository.inspect_reference_operation(
                "foreign_guarded_authority"
            )
            assert history is not None
            self.assertEqual(
                tuple(
                    tuple(event.invocation_state for event in events)
                    for events in history.invocation_events
                ),
                (
                    (ReferenceInvocationState.AUTHORIZED,),
                    (ReferenceInvocationState.AUTHORIZED,),
                ),
            )
            repository.terminate_reference_invocation(
                deliver_capability,
                ReferenceAuthorizationErrorCode.CANCELLED,
            )
            repository.terminate_reference_invocation(
                terminate_capability,
                ReferenceAuthorizationErrorCode.CANCELLED,
            )
            repository.finish_reference_operation(
                authorization,
                outcome=ReferenceAuthorizationErrorCode.CANCELLED,
            )

    def _runtime_reference_affinity_case(self, runtime_type=StoryRuntime):
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        runtime = runtime_type(workspace)
        self.addCleanup(self._cleanup_runtime_registry, str(workspace.root))

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        def public_unregistered():
            return prepare_runtime_reference_operation(
                WritingArtifactRepository(binding.resources.identity.worktree_root),
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
            )

        def guarded_unregistered():
            return prepare_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=self._physical_pin_factory(binding),
            )

        def guarded_registered():
            return runtime._prepare_session_reference_operation(
                repository_access,
                artifact_binding=binding,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                reconcile_existing=False,
            )

        return (
            binding,
            runtime,
            repository_access,
            (
                ("public_unregistered", public_unregistered, False),
                ("guarded_unregistered", guarded_unregistered, False),
                ("guarded_registered", guarded_registered, True),
            ),
        )

    def _reference_operation_snapshot(self, binding, operation):
        history = self._reference_history(binding, operation.operation_id)
        assert history is not None
        return (
            tuple(
                (event.operation_state, event.outcome)
                for event in history.operation_events
            ),
            tuple(
                tuple(
                    (event.invocation_state, event.outcome)
                    for event in invocation
                )
                for invocation in history.invocation_events
            ),
            operation.finished,
            operation.cleanup_is_pending(),
            tuple(operation._pending),
        )

    def _complete_reference_affinity_case(
        self,
        *,
        runtime,
        operation,
        registered,
        invocation=None,
    ) -> None:
        if invocation is None:
            invocation = operation.begin_invocation(
                agent_id=PLOT_ARCHITECT,
                phase="draft",
                round_number=None,
            )
            assert invocation is not None
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references,
        )
        with invocation.handle_reference_read_action(action) as result:
            self.assertEqual(result.contents, (b"",))
        invocation.record_result_received()
        invocation.accept()
        if registered:
            runtime._finish_reference_operation(operation)
        else:
            operation.finish()
        self.assertTrue(operation.finished)

    def test_runtime_reference_authority_rejects_foreign_thread_across_factories(
        self,
    ) -> None:
        binding, runtime, _repository_access, factories = (
            self._runtime_reference_affinity_case()
        )
        cases = []
        for label, factory, registered in factories:
            begin = factory()
            finish = factory()
            tracked = factory()
            invocation = tracked.begin_invocation(
                agent_id=PLOT_ARCHITECT,
                phase="draft",
                round_number=None,
            )
            assert invocation is not None
            action = ReferenceReadAction(
                ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                invocation.request.correlation,
                invocation.request.references,
            )
            cases.extend(
                (
                    (label, "begin", begin, registered, None, lambda operation=begin: operation.begin_invocation(
                        agent_id=PLOT_ARCHITECT,
                        phase="draft",
                        round_number=None,
                    )),
                    (label, "finish", finish, registered, None, finish.finish),
                    (label, "tracked_read", tracked, registered, invocation,
                     lambda current=invocation, request=action:
                     current.handle_reference_read_action(request)),
                    (label, "tracked_result", tracked, registered, invocation,
                     invocation.record_result_received),
                    (label, "tracked_accept", tracked, registered, invocation,
                     invocation.accept),
                    (label, "tracked_terminate", tracked, registered, invocation,
                     lambda current=invocation: current.terminate(
                         ReferenceAuthorizationErrorCode.CANCELLED
                     )),
                )
            )

        before = {
            id(operation): self._reference_operation_snapshot(binding, operation)
            for _, _, operation, _, _, _ in cases
        }

        def foreign_calls():
            failures = []
            for label, action_name, operation, _registered, _invocation, action in cases:
                try:
                    result = action()
                    if result is not None and callable(getattr(result, "close", None)):
                        result.close()
                except BaseException as failure:
                    failures.append((label, action_name, failure))
            return tuple(failures)

        with ThreadPoolExecutor(max_workers=1) as executor:
            failures = executor.submit(foreign_calls).result(timeout=20)
        self.assertEqual(len(failures), len(cases))
        for label, action_name, failure in failures:
            with self.subTest(factory=label, action=action_name):
                self.assertIs(type(failure), ReferenceOperationFailure)
                self.assertIs(
                    failure.code,
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
                self.assertIsNone(failure.__cause__)
        after = {
            id(operation): self._reference_operation_snapshot(binding, operation)
            for _, _, operation, _, _, _ in cases
        }
        self.assertEqual(after, before)

        completed = set()
        for _label, action_name, operation, registered, invocation, _action in cases:
            if id(operation) in completed:
                continue
            self._complete_reference_affinity_case(
                runtime=runtime,
                operation=operation,
                registered=registered,
                invocation=invocation if action_name.startswith("tracked_") else None,
            )
            completed.add(id(operation))

    def test_runtime_reference_authority_rejects_fork_across_factories(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        binding, runtime, _repository_access, factories = (
            self._runtime_reference_affinity_case()
        )
        cases = []
        for label, factory, registered in factories:
            begin = factory()
            finish = factory()
            tracked = factory()
            invocation = tracked.begin_invocation(
                agent_id=PLOT_ARCHITECT,
                phase="draft",
                round_number=None,
            )
            assert invocation is not None
            action = ReferenceReadAction(
                ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                invocation.request.correlation,
                invocation.request.references,
            )
            cases.extend(
                (
                    (label, "begin", begin, registered, None, lambda operation=begin: operation.begin_invocation(
                        agent_id=PLOT_ARCHITECT,
                        phase="draft",
                        round_number=None,
                    )),
                    (label, "finish", finish, registered, None, finish.finish),
                    (label, "tracked_read", tracked, registered, invocation,
                     lambda current=invocation, request=action:
                     current.handle_reference_read_action(request)),
                    (label, "tracked_result", tracked, registered, invocation,
                     invocation.record_result_received),
                    (label, "tracked_accept", tracked, registered, invocation,
                     invocation.accept),
                    (label, "tracked_terminate", tracked, registered, invocation,
                     lambda current=invocation: current.terminate(
                         ReferenceAuthorizationErrorCode.CANCELLED
                     )),
                )
            )
        before = {
            id(operation): self._reference_operation_snapshot(binding, operation)
            for _, _, operation, _, _, _ in cases
        }
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def foreign_calls() -> None:
            failures = []
            for label, action_name, _operation, _registered, _invocation, action in cases:
                try:
                    result = action()
                    if result is not None and callable(getattr(result, "close", None)):
                        result.close()
                    failures.append((label, action_name, "success", None, None))
                except BaseException as failure:
                    failures.append(
                        (
                            label,
                            action_name,
                            type(failure).__name__,
                            getattr(getattr(failure, "code", None), "value", None),
                            failure.__cause__ is None,
                        )
                    )
            child_results.put(tuple(failures))

        process = context.Process(target=foreign_calls)
        try:
            process.start()
            process.join(timeout=20)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            failures = child_results.get(timeout=3)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        self.assertEqual(len(failures), len(cases))
        for label, action_name, failure_type, code, no_cause in failures:
            with self.subTest(factory=label, action=action_name):
                self.assertEqual(failure_type, "ReferenceOperationFailure")
                self.assertEqual(
                    code,
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH.value,
                )
                self.assertTrue(no_cause)
        after = {
            id(operation): self._reference_operation_snapshot(binding, operation)
            for _, _, operation, _, _, _ in cases
        }
        self.assertEqual(after, before)

        completed = set()
        for _label, action_name, operation, registered, invocation, _action in cases:
            if id(operation) in completed:
                continue
            self._complete_reference_affinity_case(
                runtime=runtime,
                operation=operation,
                registered=registered,
                invocation=invocation if action_name.startswith("tracked_") else None,
            )
            completed.add(id(operation))

    def test_forked_reference_registry_purges_inherited_handles(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        binding, runtime, _repository_access, factories = (
            self._runtime_reference_affinity_case()
        )
        _label, factory, registered = factories[-1]
        self.assertTrue(registered)
        operation = factory()
        assert operation.operation_id is not None
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel",
                binding.worktree_identity,
            ),
            frozenset((operation.operation_id,)),
        )
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def inspect_in_child() -> None:
            registered_ids = runtime._registered_reference_operation_ids(
                "novel",
                binding.worktree_identity,
            )
            child_results.put(
                (
                    StoryRuntime._reference_operation_registry_pid == os.getpid(),
                    tuple(registered_ids),
                    runtime._has_cleanup_pending_reference_operation("novel"),
                )
            )

        process = context.Process(target=inspect_in_child)
        try:
            process.start()
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            child = child_results.get(timeout=3)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        self.assertEqual(child, (True, (), False))
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel",
                binding.worktree_identity,
            ),
            frozenset((operation.operation_id,)),
        )
        self._complete_reference_affinity_case(
            runtime=runtime,
            operation=operation,
            registered=True,
        )
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel",
                binding.worktree_identity,
            ),
            frozenset(),
        )

    def test_forked_runtime_subclass_resets_base_registry_before_locking(
        self,
    ) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")

        class ChildStoryRuntime(StoryRuntime):
            pass

        binding, runtime, _repository_access, factories = (
            self._runtime_reference_affinity_case(ChildStoryRuntime)
        )
        _label, factory, registered = factories[-1]
        self.assertTrue(registered)
        operation = factory()
        assert operation.operation_id is not None
        parent_registry = dict(StoryRuntime._reference_operation_registry)

        class InheritedPoisonLock:
            def __enter__(self):
                raise AssertionError("inherited registry lock was used")

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

        inherited_lock = InheritedPoisonLock()
        inherited_lock_id = id(inherited_lock)
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def inspect_in_child() -> None:
            try:
                registered_ids = runtime._registered_reference_operation_ids(
                    "novel",
                    binding.worktree_identity,
                )
                child_results.put(
                    (
                        StoryRuntime._reference_operation_registry_pid,
                        tuple(registered_ids),
                        id(StoryRuntime._reference_operation_registry_lock),
                        None,
                    )
                )
            except BaseException as failure:
                child_results.put((None, (), None, repr(failure)))

        with mock.patch.object(
            StoryRuntime,
            "_reference_operation_registry_lock",
            inherited_lock,
        ):
            process = context.Process(target=inspect_in_child)
            try:
                process.start()
                process.join(timeout=10)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
                child_pid, child_ids, child_lock_id, child_failure = (
                    child_results.get(timeout=3)
                )
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            self.assertEqual(
                StoryRuntime._reference_operation_registry,
                parent_registry,
            )
        self.assertIsNone(child_failure)
        self.assertEqual(child_pid, process.pid)
        self.assertEqual(child_ids, ())
        self.assertNotEqual(child_lock_id, inherited_lock_id)
        self._complete_reference_affinity_case(
            runtime=runtime,
            operation=operation,
            registered=True,
        )

    def test_two_first_forked_consumers_keep_both_child_registrations(
        self,
    ) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        binding, runtime, _repository_access, factories = (
            self._runtime_reference_affinity_case()
        )
        _label, factory, registered = factories[-1]
        self.assertTrue(registered)
        parent_operation = factory()
        assert parent_operation.operation_id is not None
        parent_registry = dict(StoryRuntime._reference_operation_registry)

        class InheritedPoisonLock:
            def __enter__(self):
                raise AssertionError("inherited registry lock was used")

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

        inherited_lock = InheritedPoisonLock()
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def register_in_child() -> None:
            barrier = threading.Barrier(3)
            operation_ids: list[str] = []
            failures: list[str] = []

            def consume() -> None:
                try:
                    barrier.wait(timeout=5)
                    operation = factory()
                    assert operation.operation_id is not None
                    operation_ids.append(operation.operation_id)
                except BaseException as failure:
                    failures.append(repr(failure))

            consumers = tuple(
                threading.Thread(target=consume) for _ in range(2)
            )
            for consumer in consumers:
                consumer.start()
            barrier.wait(timeout=5)
            for consumer in consumers:
                consumer.join(timeout=20)
            child_results.put(
                (
                    tuple(consumer.is_alive() for consumer in consumers),
                    tuple(sorted(operation_ids)),
                    tuple(sorted(runtime._registered_reference_operation_ids(
                        "novel",
                        binding.worktree_identity,
                    ))),
                    tuple(failures),
                    StoryRuntime._reference_operation_registry_lock
                    is inherited_lock,
                )
            )

        with mock.patch.object(
            StoryRuntime,
            "_reference_operation_registry_lock",
            inherited_lock,
        ):
            process = context.Process(target=register_in_child)
            try:
                process.start()
                process.join(timeout=45)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
                child = child_results.get(timeout=3)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            self.assertEqual(
                StoryRuntime._reference_operation_registry,
                parent_registry,
            )
        live_threads, operation_ids, registered_ids, failures, used_poison = child
        self.assertEqual(live_threads, (False, False))
        self.assertEqual(failures, ())
        self.assertEqual(len(operation_ids), 2)
        self.assertEqual(len(set(operation_ids)), 2)
        self.assertEqual(registered_ids, operation_ids)
        self.assertFalse(used_poison)
        self._complete_reference_affinity_case(
            runtime=runtime,
            operation=parent_operation,
            registered=True,
        )

    def test_registered_cleanup_can_finish_from_another_thread_in_same_process(
        self,
    ) -> None:
        binding, runtime, repository_access, factories = (
            self._runtime_reference_affinity_case()
        )
        _label, factory, registered = factories[-1]
        self.assertTrue(registered)
        operation = factory()
        assert operation.operation_id is not None
        operation._defer_cleanup()
        self.assertTrue(operation.cleanup_is_pending())
        failures = []

        def reconcile() -> None:
            try:
                runtime._reconcile_reference_operations_at_boundary(
                    session_id="novel",
                    physical_workspace_identity=binding.worktree_identity,
                    repository_access=repository_access,
                )
            except BaseException as failure:
                failures.append(failure)

        thread = threading.Thread(target=reconcile)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(operation.finished)
        self.assertFalse(operation.cleanup_is_pending())
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel",
                binding.worktree_identity,
            ),
            frozenset(),
        )
        history = self._reference_history(binding, operation.operation_id)
        assert history is not None
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

    def test_nested_guard_rejection_discards_content_and_defers_fresh_cleanup(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        operation = prepare_guarded_runtime_reference_operation(
            repository_access,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
            physical_workspace_identity=binding.worktree_identity,
            physical_workspace_pin_factory=self._physical_pin_factory(binding),
        )
        invocation = operation.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        assert invocation is not None
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references,
        )
        repository = operation._repository
        original_operation_access = repository._operation_access
        reject_inner = [True]

        @contextmanager
        def inner_operation_access():
            if reject_inner[0]:
                raise SessionArtifactBindingError("inner operation rejected")
            assert original_operation_access is not None
            with original_operation_access():
                yield

        repository._operation_access = inner_operation_access
        try:
            with self.assertRaises(SessionArtifactBindingError):
                invocation.handle_reference_read_action(action)
            self._assert_content_free_cleanup_pending(operation)
        finally:
            repository._operation_access = original_operation_access

        reject_inner[0] = False
        self._complete_fresh_cleanup(binding, operation)

        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert history is not None
        self.assertEqual(
            tuple(event.invocation_state for event in history.invocation_events[0]),
            (
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.TERMINATED,
            ),
        )
        self.assertNotIn(
            ReferenceInvocationState.DELIVERED,
            tuple(event.invocation_state for event in history.invocation_events[0]),
        )
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

    def test_guard_postcheck_failure_closes_result_and_prevents_provider_replay(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        postcheck_failure: list[BaseException | None] = [None]

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository
                if postcheck_failure[0] is not None:
                    raise postcheck_failure[0]

        operation = prepare_guarded_runtime_reference_operation(
            repository_access,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
            physical_workspace_identity=binding.worktree_identity,
            physical_workspace_pin_factory=self._physical_pin_factory(binding),
        )
        invocation = operation.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        assert invocation is not None
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references,
        )
        marker = SessionArtifactBindingError("postcheck rejected")
        postcheck_failure[0] = marker
        produced = []
        original_read = invocation._delegate.handle_reference_read_action

        def capture_result(requested_action):
            result = original_read(requested_action)
            produced.append(result)
            return result

        with mock.patch.object(
            operation._repository,
            "deliver_authorized_references",
            wraps=operation._repository.deliver_authorized_references,
        ) as deliver, mock.patch.object(
            type(invocation._delegate),
            "handle_reference_read_action",
            autospec=True,
            side_effect=lambda _delegate, requested_action: capture_result(
                requested_action
            ),
        ):
            with self.assertRaises(SessionArtifactBindingError) as rejected:
                invocation.handle_reference_read_action(action)
            self.assertIs(rejected.exception, marker)
            self.assertEqual(len(produced), 1)
            with self.assertRaises(ReferenceAuthorizationError) as closed:
                produced[0].contents
            self.assertIs(
                closed.exception.code,
                ReferenceAuthorizationErrorCode.CLOSED,
            )
            self._assert_content_free_cleanup_pending(operation)
            with self.assertRaises(SessionArtifactBindingError) as cleanup:
                operation.failure(marker)
            self.assertIs(cleanup.exception, marker)
            with self.assertRaises(ReferenceAuthorizationError) as replay:
                invocation.handle_reference_read_action(action)
            self.assertIs(
                replay.exception.code,
                ReferenceAuthorizationErrorCode.CLOSED,
            )
            self.assertEqual(deliver.call_count, 1)

        postcheck_failure[0] = None
        self._complete_fresh_cleanup(binding, operation)

        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert history is not None
        self.assertEqual(
            tuple(event.invocation_state for event in history.invocation_events[0]),
            (
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.TERMINATED,
            ),
        )
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

    def test_registered_ordinary_postcheck_retains_owner_until_fresh_terminalization(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        runtime = StoryRuntime(workspace)
        workspace_root = str(workspace.root)
        self.addCleanup(self._cleanup_runtime_registry, workspace_root)
        marker = RuntimeError("ordinary capture postcheck rejected")
        reject_postcheck = [True]
        published = []

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository
            if reject_postcheck[0]:
                raise marker

        def register(operation):
            runtime._register_reference_operation(operation)
            published.append(operation)

        with self.assertRaises(RuntimeError) as rejected:
            prepare_registered_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=self._physical_pin_factory(binding),
                register=register,
            )
        self.assertIs(rejected.exception, marker)
        self.assertEqual(len(published), 1)
        operation = published[0]
        self._assert_content_free_cleanup_pending(operation)
        retained_pin = self._assert_physical_pin_open(operation)
        assert operation.operation_id is not None
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", binding.worktree_identity
            ),
            frozenset((operation.operation_id,)),
        )
        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert history is not None
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.CAPTURED,
        )

        reject_postcheck[0] = False
        fresh_binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def fresh_repository_access():
            with fresh_binding.open_ready_resources() as access:
                yield access.repository

        runtime._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=fresh_binding.worktree_identity,
            repository_access=fresh_repository_access,
        )
        self.assertTrue(operation.finished)
        self._assert_descriptor_closed(retained_pin)
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", fresh_binding.worktree_identity
            ),
            frozenset(),
        )
        with fresh_binding.open_ready_resources() as access:
            terminal = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert terminal is not None
        self.assertIs(
            terminal.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

    def test_registered_workspace_security_postcheck_defers_content_free_cleanup(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        runtime = StoryRuntime(workspace)
        workspace_root = str(workspace.root)
        self.addCleanup(self._cleanup_runtime_registry, workspace_root)
        marker = WorkspaceSecurityError("capture root security changed")
        published = []

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository
            raise marker

        def register(operation):
            runtime._register_reference_operation(operation)
            published.append(operation)

        with self.assertRaises(WorkspaceSecurityError) as rejected:
            prepare_registered_guarded_runtime_reference_operation(
                repository_access,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
                physical_workspace_identity=binding.worktree_identity,
                physical_workspace_pin_factory=self._physical_pin_factory(binding),
                register=register,
            )
        self.assertIs(rejected.exception, marker)
        self.assertEqual(len(published), 1)
        operation = published[0]
        self._assert_content_free_cleanup_pending(operation)
        retained_pin = self._assert_physical_pin_open(operation)

        @contextmanager
        def fresh_repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        runtime._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=binding.worktree_identity,
            repository_access=fresh_repository_access,
        )
        self.assertTrue(operation.finished)
        self._assert_descriptor_closed(retained_pin)
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", binding.worktree_identity
            ),
            frozenset(),
        )

    def test_registered_authorized_postcheck_is_published_before_cleanup_defers(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        runtime = StoryRuntime(workspace)
        workspace_root = str(workspace.root)
        self.addCleanup(self._cleanup_runtime_registry, workspace_root)

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        operation = runtime._prepare_session_reference_operation(
            repository_access,
            artifact_binding=binding,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
        )
        marker = SessionArtifactBindingError("authorization postcheck rejected")

        @contextmanager
        def rejected_repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository
            raise marker

        operation._repository_access = rejected_repository_access
        with self.assertRaises(SessionArtifactBindingError) as rejected:
            operation.begin_invocation(
                agent_id=PLOT_ARCHITECT,
                phase="draft",
                round_number=None,
            )
        self.assertIs(rejected.exception, marker)
        self._assert_content_free_cleanup_pending(operation)
        assert operation.operation_id is not None
        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert history is not None
        self.assertEqual(
            tuple(
                event.invocation_state
                for event in history.invocation_events[0]
            ),
            (ReferenceInvocationState.AUTHORIZED,),
        )
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", binding.worktree_identity
            ),
            frozenset((operation.operation_id,)),
        )

        fresh_binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def fresh_repository_access():
            with fresh_binding.open_ready_resources() as access:
                yield access.repository

        runtime._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=fresh_binding.worktree_identity,
            repository_access=fresh_repository_access,
        )
        self.assertTrue(operation.finished)
        with fresh_binding.open_ready_resources() as access:
            terminal = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert terminal is not None
        self.assertEqual(
            tuple(
                event.invocation_state
                for event in terminal.invocation_events[0]
            ),
            (
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.TERMINATED,
            ),
        )

    def test_restart_reconciles_every_nonterminal_reference_evidence_state(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        operations = {}
        reference = ArtifactReference("core_documents/global_outline.md")
        action = None

        with binding.open_ready_resources() as access:
            for state in (
                ReferenceOperationState.CAPTURED,
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.RESULT_RECEIVED,
            ):
                operation = prepare_runtime_reference_operation(
                    access.repository,
                    session_id="novel",
                    text="Read ${core_documents/global_outline.md}.",
                )
                operations[state] = operation.operation_id
                if state is ReferenceOperationState.CAPTURED:
                    continue
                invocation = operation.begin_invocation(
                    agent_id=PLOT_ARCHITECT,
                    phase="draft",
                    round_number=None,
                )
                assert invocation is not None
                if state is ReferenceInvocationState.AUTHORIZED:
                    continue
                action = ReferenceReadAction(
                    ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                    invocation.request.correlation,
                    (reference,),
                )
                result = invocation.handle_reference_read_action(action)
                result.close()
                if state is ReferenceInvocationState.RESULT_RECEIVED:
                    invocation.record_result_received()

        restarted = StoryRuntime(workspace)
        fresh_binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def repository_access():
            with fresh_binding.open_ready_resources() as access:
                yield access.repository

        restarted._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=fresh_binding.worktree_identity,
            repository_access=repository_access,
        )
        with fresh_binding.open_ready_resources() as access:
            for state, operation_id in operations.items():
                with self.subTest(state=state):
                    assert operation_id is not None
                    history = access.repository.inspect_reference_operation(operation_id)
                    assert history is not None
                    self.assertIs(
                        history.operation_events[-1].operation_state,
                        ReferenceOperationState.TERMINATED,
                    )
                    if history.invocation_events:
                        self.assertIs(
                            history.invocation_events[0][-1].invocation_state,
                            ReferenceInvocationState.TERMINATED,
                        )

    def test_interrupted_reconciliation_preserves_prefix_and_resumes_idempotently(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        with binding.open_ready_resources() as access:
            operation = prepare_runtime_reference_operation(
                access.repository,
                session_id="novel",
                text="Read ${core_documents/global_outline.md}.",
            )
            for _ in range(2):
                invocation = operation.begin_invocation(
                    agent_id=PLOT_ARCHITECT,
                    phase="draft",
                    round_number=None,
                )
                assert invocation is not None
            operation_id = operation.operation_id
            assert operation_id is not None
            evidence = access.repository._reference_evidence
            original_append = evidence.append_invocation_event
            termination_calls = [0]

            def interrupt_second_termination(*args, **kwargs):
                if args[2] is ReferenceInvocationState.TERMINATED:
                    termination_calls[0] += 1
                    if termination_calls[0] == 2:
                        raise OSError("injected reconciliation interruption")
                return original_append(*args, **kwargs)

            with mock.patch.object(
                evidence,
                "append_invocation_event",
                side_effect=interrupt_second_termination,
            ), self.assertRaisesRegex(OSError, "interruption"):
                access.repository.reconcile_reference_operation(
                    "novel",
                    operation_id,
                )
            prefix = access.repository.inspect_reference_operation(operation_id)
            assert prefix is not None
            self.assertEqual(
                tuple(events[-1].invocation_state for events in prefix.invocation_events),
                (
                    ReferenceInvocationState.TERMINATED,
                    ReferenceInvocationState.AUTHORIZED,
                ),
            )

            self.assertTrue(
                access.repository.reconcile_reference_operation("novel", operation_id)
            )
            resumed = access.repository.inspect_reference_operation(operation_id)
            assert resumed is not None
            self.assertEqual(
                tuple(events[-1].invocation_state for events in resumed.invocation_events),
                (
                    ReferenceInvocationState.TERMINATED,
                    ReferenceInvocationState.TERMINATED,
                ),
            )
            self.assertIs(
                resumed.operation_events[-1].operation_state,
                ReferenceOperationState.TERMINATED,
            )
            self.assertTrue(
                access.repository.reconcile_reference_operation("novel", operation_id)
            )
            self.assertEqual(
                access.repository.inspect_reference_operation(operation_id),
                resumed,
            )

    def test_live_operation_is_excluded_across_story_runtime_instances(self) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        first = StoryRuntime(workspace)
        second = StoryRuntime(workspace)
        workspace_root = str(workspace.root)
        self.addCleanup(self._cleanup_runtime_registry, workspace_root)

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        operation = first._prepare_session_reference_operation(
            repository_access,
            artifact_binding=binding,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
        )
        assert operation.operation_id is not None
        second._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=binding.worktree_identity,
            repository_access=repository_access,
        )
        with binding.open_ready_resources() as access:
            active = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert active is not None
        self.assertIs(
            active.operation_events[-1].operation_state,
            ReferenceOperationState.CAPTURED,
        )
        self.assertFalse(operation.finished)
        first._fail_reference_operation(operation, RuntimeError("owner stopped"))
        self.assertTrue(operation.finished)
        self.assertEqual(
            first._registered_reference_operation_ids(
                "novel", binding.worktree_identity
            ),
            frozenset(),
        )

    def test_replaced_workspace_drops_stale_authority_without_cross_root_cleanup(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        runtime = StoryRuntime(workspace)
        workspace_root = str(workspace.root)
        self.addCleanup(self._cleanup_runtime_registry, workspace_root)

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        operation = runtime._prepare_session_reference_operation(
            repository_access,
            artifact_binding=binding,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
        )
        assert operation.operation_id is not None
        stale_pin = self._assert_physical_pin_open(operation)
        replacement_identity = (
            binding.worktree_identity[0],
            binding.worktree_identity[1] + 1,
        )

        class ReplacementRepository:
            excluded: frozenset[str] | None = None

            def reconcile_incomplete_reference_operations(
                self,
                session_id,
                *,
                excluded_ids_supplier,
            ):
                self.excluded = excluded_ids_supplier()

        replacement = ReplacementRepository()

        @contextmanager
        def replacement_access():
            yield replacement

        runtime._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=replacement_identity,
            repository_access=replacement_access,
        )

        self._assert_content_free_cleanup_pending(operation)
        self.assertIsNone(operation._physical_workspace_pin)
        self._assert_descriptor_closed(stale_pin)
        self.assertEqual(replacement.excluded, frozenset())
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", binding.worktree_identity
            ),
            frozenset(),
        )
        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert history is not None
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.CAPTURED,
        )

    def test_terminal_postcheck_reconciles_before_runtime_marks_finished(self) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "novel")
        binding = compose_session_artifact_binding(workspace.root, "novel")
        runtime = StoryRuntime(workspace)
        workspace_root = str(workspace.root)
        self.addCleanup(self._cleanup_runtime_registry, workspace_root)

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        operation = runtime._prepare_session_reference_operation(
            repository_access,
            artifact_binding=binding,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
        )
        invocation = operation.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        assert invocation is not None
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            invocation.request.correlation,
            invocation.request.references,
        )
        result = invocation.handle_reference_read_action(action)
        self.assertEqual(result.contents, (b"",))
        invocation.record_result_received()
        invocation.accept()
        with self.assertRaises(ReferenceAuthorizationError) as closed:
            result.contents
        self.assertIs(closed.exception.code, ReferenceAuthorizationErrorCode.CLOSED)

        marker = SessionArtifactBindingError("terminal postcheck rejected")

        @contextmanager
        def rejected_repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository
            raise marker

        operation._repository_access = rejected_repository_access
        with self.assertRaises(SessionArtifactBindingError) as rejected:
            runtime._finish_reference_operation(operation)
        self.assertIs(rejected.exception, marker)
        self._assert_content_free_cleanup_pending(operation)
        assert operation.operation_id is not None
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", binding.worktree_identity
            ),
            frozenset((operation.operation_id,)),
        )
        with binding.open_ready_resources() as access:
            durable = access.repository.inspect_reference_operation(
                operation.operation_id
            )
        assert durable is not None
        self.assertIs(
            durable.operation_events[-1].operation_state,
            ReferenceOperationState.ACCEPTED,
        )

        fresh_binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def fresh_repository_access():
            with fresh_binding.open_ready_resources() as access:
                yield access.repository

        runtime._reconcile_reference_operations_at_boundary(
            session_id="novel",
            physical_workspace_identity=fresh_binding.worktree_identity,
            repository_access=fresh_repository_access,
        )
        self.assertTrue(operation.finished)
        self.assertEqual(
            runtime._registered_reference_operation_ids(
                "novel", fresh_binding.worktree_identity
            ),
            frozenset(),
        )

    def test_long_lived_reference_and_source_adapter_reacquire_exact_access(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        ready = provision_session_resources(workspace.root, "novel")
        ready.identity.worktree_root.joinpath(
            "core_documents/global_outline.md"
        ).write_text("Session-only guarded source.\n", encoding="utf-8")
        binding = compose_session_artifact_binding(workspace.root, "novel")

        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        with binding.open_ready_resources() as access:
            retained_repository = access.repository
        with self.assertRaises(SessionArtifactBindingError):
            retained_repository.list_paths()

        operation = prepare_guarded_runtime_reference_operation(
            repository_access,
            session_id="novel",
            text="Read ${core_documents/global_outline.md}.",
            physical_workspace_identity=binding.worktree_identity,
            physical_workspace_pin_factory=self._physical_pin_factory(binding),
        )
        invocation = operation.begin_invocation(
            agent_id=PLOT_ARCHITECT,
            phase="draft",
            round_number=None,
        )
        self.assertIsNotNone(invocation)
        operation.terminate_invocation(
            invocation,
            ReferenceAuthorizationErrorCode.CANCELLED,
        )

        adapter = create_guarded_writing_artifact_source_adapter(
            repository_access,
            ContextScope(
                "workspace",
                "novel",
                ContextSubjectKind.CONVERSATION,
                "conversation",
                PLOT_ARCHITECT,
            ),
        )
        captured = adapter.capture(
            "core_documents/global_outline.md",
            CONTEXT_SOURCE_POLICY,
        )
        self.assertEqual(captured.content, b"Session-only guarded source.\n")


if __name__ == "__main__":
    unittest.main()
