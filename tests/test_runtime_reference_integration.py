"""Runtime lead, management, and reference-inspection public boundaries."""

from __future__ import annotations

import inspect
import unittest
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
    prepare_runtime_reference_operation,
    reference_operation_belongs_to_session,
    reference_operation_session_id,
)
from story_writing_agents.writing_artifacts.authorization import (
    ReferenceAuthorizationErrorCode,
    ReferenceOperationFailure,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import initialized_workspace, pristine_initialized_workspace


REMOVED_RUNTIME_METHODS = (
    "converse",
    "converse_with_retrieval",
    "run_prompt",
    "run_draft",
)


class RuntimeExecutionRetirementTests(unittest.TestCase):
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
        blocked[0] = None
        supplied_marker = SessionArtifactBindingError("supplied marker")
        with self.assertRaises(SessionArtifactBindingError) as supplied:
            begin_operation.failure(supplied_marker)
        self.assertIs(supplied.exception, supplied_marker)
        self.assertTrue(begin_operation.finished)

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
        blocked[0] = None
        with self.assertRaises(SessionArtifactBindingError) as action_cleanup:
            action_operation.failure(action_marker)
        self.assertIs(action_cleanup.exception, action_marker)
        self.assertTrue(action_operation.finished)

        terminal_operation = operation()
        terminal_marker = SessionArtifactBindingError("terminal marker")
        blocked[0] = terminal_marker
        with self.assertRaises(SessionArtifactBindingError) as terminal:
            terminal_operation.finish()
        self.assertIs(terminal.exception, terminal_marker)
        self.assertFalse(terminal_operation.finished)
        blocked[0] = None
        with self.assertRaises(SessionArtifactBindingError) as terminal_cleanup:
            terminal_operation.failure(terminal_marker)
        self.assertIs(terminal_cleanup.exception, terminal_marker)
        self.assertTrue(terminal_operation.finished)

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
        self.assertTrue(deferred_operation.finished)
        self.assertEqual(deferred_operation._pending, {})
        blocked[0] = None

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
