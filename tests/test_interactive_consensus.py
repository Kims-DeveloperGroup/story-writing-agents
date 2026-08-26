"""Interactive consensus routing, safety, and retained-runtime contracts."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, replace
from typing import Any
from unittest import mock

from story_writing_agents import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    ConversationReply,
    ConversationRequest,
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    DebateContextMode,
    DebateFailureKind,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateResult,
    DebateStatus,
    InputKind,
    Phase,
    ProgressObserverError,
    SessionAccessState,
    SessionDurableState,
    SessionNotFoundError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.cli_context import (
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversations import ConversationMetadata
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.interactive_terminal import InteractiveErrorCode
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)

from tests.helpers import all_manuscripts
from tests.test_interactive_conversation import (
    InteractiveConversationTestCase,
    _REAL_RUNTIME,
    _capture_semantic_method,
    _error_record,
    _transcript_relative,
)


@dataclass(frozen=True, slots=True)
class ConsensusOutcome:
    """Describe one deterministic terminal outcome or identity tamper."""

    status: DebateStatus = DebateStatus.CONSENSUS
    failure_kind: DebateFailureKind | None = None
    tampered_progress: bool = False
    tampered_result: bool = False
    reference_operation_id: str | None = None


class ScriptedConsensusRuntime:
    """Emit canonical progress while recording retained event-loop calls."""

    def __init__(self, *outcomes: ConsensusOutcome | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.output_paths: list[str] = []
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.tasks: list[asyncio.Task[object] | None] = []
        self.on_call: Any = None

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        output_path,
        conversation_context,
        progress_sink,
    ) -> DebateResult:
        """Record and complete one prompt consensus action."""

        return await self._complete(
            session_id,
            prompt,
            InputKind.PROMPT,
            output_path,
            conversation_context,
            progress_sink,
        )

    async def run_draft(
        self,
        session_id: str,
        draft: str,
        *,
        output_path,
        conversation_context,
        progress_sink,
    ) -> DebateResult:
        """Record and complete one supplied-draft consensus action."""

        return await self._complete(
            session_id,
            draft,
            InputKind.DRAFT,
            output_path,
            conversation_context,
            progress_sink,
        )

    @staticmethod
    def _emit(progress_sink, event: DebateProgressEvent) -> None:
        """Match the runtime observer wrapper for controller-focused tests."""

        try:
            progress_sink(event)
        except Exception as exc:
            raise ProgressObserverError() from exc

    async def _complete(
        self,
        session_id: str,
        payload: str,
        input_kind: InputKind,
        output_path: str,
        conversation_context,
        progress_sink,
    ) -> DebateResult:
        """Emit one legal lifecycle and return its correlated typed result."""

        loop = asyncio.get_running_loop()
        self.loops.append(loop)
        self.tasks.append(asyncio.current_task())
        outcome = (
            self.outcomes.pop(0)
            if self.outcomes
            else ConsensusOutcome()
        )
        self.calls.append(
            {
                "session_id": session_id,
                "payload": payload,
                "input_kind": input_kind,
                "conversation_context": conversation_context,
            }
        )
        self.output_paths.append(output_path)
        if self.on_call is not None:
            self.on_call()
        if isinstance(outcome, BaseException):
            raise outcome
        debate_id = f"debate_{len(self.calls)}"
        if outcome.tampered_progress:
            self._emit(
                progress_sink,
                DebateProgressEvent(
                    session_id="foreign_session",
                    debate_id=debate_id,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.STARTED,
                    context_mode=conversation_context.mode,
                ),
            )
            raise AssertionError("tampered progress was accepted")

        common = {
            "session_id": session_id,
            "debate_id": debate_id,
            "input_kind": input_kind,
        }
        self._emit(
            progress_sink,
            DebateProgressEvent(
                **common,
                phase=DebateProgressPhase.STARTED,
                context_mode=conversation_context.mode,
            ),
        )
        self._emit(
            progress_sink,
            DebateProgressEvent(
                **common,
                phase=DebateProgressPhase.DRAFTED,
                revision_id="r0",
            ),
        )
        self._emit(
            progress_sink,
            DebateProgressEvent(
                **common,
                phase=DebateProgressPhase.AUDIT_STARTED,
                revision_id="r0",
            ),
        )
        self._emit(
            progress_sink,
            DebateProgressEvent(
                **common,
                phase=DebateProgressPhase.AUDIT_COMPLETED,
                revision_id="r0",
            ),
        )

        manuscript_path = None
        final_candidate = None
        failure_kind = outcome.failure_kind
        if outcome.status is DebateStatus.CONSENSUS:
            manuscript_path = f"manuscript/{output_path}"
            final_candidate = "trusted candidate"
            self._emit(
                progress_sink,
                DebateProgressEvent(
                    **common,
                    phase=DebateProgressPhase.ARTIFACTS_COMMITTED,
                    revision_id="r0",
                    manuscript_path=manuscript_path,
                    core_updates=("global_outline.md",),
                ),
            )
            terminal_phase = DebateProgressPhase.CONSENSUS
        elif outcome.status is DebateStatus.DEADLOCK:
            terminal_phase = DebateProgressPhase.DEADLOCK
        else:
            terminal_phase = DebateProgressPhase.FAILED
            failure_kind = failure_kind or DebateFailureKind.INTERNAL
        self._emit(
            progress_sink,
            DebateProgressEvent(
                **common,
                phase=terminal_phase,
                revision_id="r0",
                failure_kind=(
                    failure_kind
                    if terminal_phase is DebateProgressPhase.FAILED
                    else None
                ),
            ),
        )
        result = DebateResult(
            session_id=session_id,
            debate_id=debate_id,
            input_kind=input_kind,
            status=outcome.status,
            revision_id="r0",
            rounds_completed=0,
            final_candidate=final_candidate,
            latest_candidate="RAW_RESULT_SECRET",
            manuscript_path=manuscript_path,
            core_updates=(
                ("global_outline.md",)
                if outcome.status is DebateStatus.CONSENSUS
                else ()
            ),
            error=(
                "RAW_RESULT_SECRET\x1b"
                if outcome.status is DebateStatus.FAILED
                else None
            ),
            action_required=(
                "RAW_RESULT_SECRET\x1b"
                if outcome.status is DebateStatus.DEADLOCK
                else None
            ),
            reference_operation_id=outcome.reference_operation_id,
        )
        return (
            replace(result, session_id="foreign_session")
            if outcome.tampered_result
            else result
        )


def file_bytes(workspace: Workspace) -> dict[str, bytes]:
    """Snapshot domain files while ignoring diagnostic artifacts."""

    return {
        path.relative_to(workspace.root).as_posix(): path.read_bytes()
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file()
        and "debug_logs" not in path.relative_to(workspace.root).parts
    }


class InteractiveConsensusGateTests(InteractiveConversationTestCase):
    """Verify attachment and active-selection gates precede every side effect."""

    def test_unattached_unselected_and_archived_actions_are_provider_free(
        self,
    ) -> None:
        runtime = ScriptedConsensusRuntime()
        workspace = self.make_workspace()
        controller, backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/run prompt",
            "/run --fresh prompt",
            "/draft-file missing.md",
            "/quit",
            runtime=runtime,  # type: ignore[arg-type]
        )
        accepted_components: list[object] = []
        real_run = asyncio.run
        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            mock.patch.object(controller_core_module, "read_draft_file") as loader,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
                side_effect=real_run,
            ) as run_boundary,
            mock.patch.object(backend, "bind") as bind,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(accepted_components, [])
        run_boundary.assert_not_called()
        loader.assert_not_called()
        bind.assert_not_called()
        self.assertEqual(
            "".join(callbacks.records).count("[error session_not_attached]"),
            3,
        )

        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        runtime = ScriptedConsensusRuntime()
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/run prompt",
            "/run --fresh prompt",
            "/draft-file missing.md",
            "/quit",
            catalog=catalog,
            runtime=runtime,  # type: ignore[arg-type]
        )
        accepted_components = []
        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            mock.patch.object(controller_core_module, "read_draft_file") as loader,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(accepted_components, [])
        loader.assert_not_called()
        self.assertEqual(
            "".join(callbacks.records).count("[error conversation_required]"),
            3,
        )

        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        selected = catalog.create("session", "Selected")
        save_default_conversation(
            workspace,
            "session",
            selected.conversation_id,
        )
        selection_path = workspace.path(
            "agent_states/orchestrator_state/conversation_defaults/session.json"
        )
        selection_before = selection_path.read_bytes()

        def archive_before_dispatch(_prompt: str) -> str:
            catalog.archive("session", selected.conversation_id)
            return "/run stale"

        runtime = ScriptedConsensusRuntime()
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            archive_before_dispatch,
            "/quit",
            catalog=catalog,
            runtime=runtime,  # type: ignore[arg-type]
        )
        accepted_components = []
        with _capture_semantic_method(
            "write_accepted_input",
            accepted_components,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(accepted_components, [])
        self.assertEqual(selection_path.read_bytes(), selection_before)
        self.assertIn("[error conversation_archived]", "".join(callbacks.records))


class InteractiveConsensusDispatchTests(InteractiveConversationTestCase):
    """Verify run routing, composition, shared loading, and nonmutation."""

    def test_run_fresh_draft_and_bare_send_share_one_runner_loop(
        self,
    ) -> None:
        runtime = ScriptedConsensusRuntime()
        (
            workspace,
            _catalog,
            entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            "/run inline ${core_documents/global_outline.md}",
            "/run --fresh isolated ${core_documents/character_sheets.md}",
            "/draft-file literal.md",
            "/run",
            "composed line one",
            "/say /send",
            "/send",
            "/quit",
            runtime=runtime,  # type: ignore[arg-type]
        )
        controller._show_message_details = True
        before = file_bytes(workspace)
        attachment_facts: list[tuple[object, object, object, object]] = []
        accepted_components: list[object] = []
        semantic_components: list[object] = []
        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            _capture_semantic_method(
                "write_component",
                semantic_components,
            ),
            mock.patch.object(
                controller_core_module,
                "read_draft_file",
                return_value="exact loaded draft\r\n\x00",
            ) as loader,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
            ) as run_boundary,
        ):
            projection_counts_at_runtime: list[tuple[int, int]] = []

            def observe_runtime_entry() -> None:
                attachment_facts.append(
                    (
                        controller._attached_session_id,
                        controller._focused_conversation,
                        controller._target_agent_id,
                        controller._conversation_memory_service,
                    )
                )
                projection_counts_at_runtime.append(
                    (
                        len(accepted_components),
                        sum(
                            type(component) is terminal_module.Record
                            and component.title == "Consensus request"
                            for component in semantic_components
                        ),
                    )
                )

            runtime.on_call = observe_runtime_entry
            self.assertEqual(controller.run_session_lobby(), 0)

        run_boundary.assert_not_called()
        loader.assert_called_once_with("literal.md")
        self.assertEqual(
            [call["payload"] for call in runtime.calls],
            [
                "inline ${core_documents/global_outline.md}",
                "isolated ${core_documents/character_sheets.md}",
                "exact loaded draft\r\n\x00",
                "composed line one\n/send",
            ],
        )
        self.assertEqual(
            [call["input_kind"] for call in runtime.calls],
            [InputKind.PROMPT, InputKind.PROMPT, InputKind.DRAFT, InputKind.PROMPT],
        )
        self.assertEqual(
            projection_counts_at_runtime,
            [(1, 1), (2, 2), (2, 3), (3, 4)],
        )
        self.assertEqual(
            accepted_components,
            [
                terminal_module.Message(
                    "You → Consensus",
                    "inline ${core_documents/global_outline.md}",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
                terminal_module.Message(
                    "You → Consensus",
                    "isolated ${core_documents/character_sheets.md}",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
                terminal_module.Message(
                    "You → Consensus",
                    "composed line one\n/send",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
            ],
        )
        request_contexts = [
            component
            for component in semantic_components
            if type(component) is terminal_module.Record
            and component.title == "Consensus request"
        ]
        self.assertEqual(
            request_contexts,
            [
                terminal_module.Record(
                    "context",
                    title="Consensus request",
                    fields=(terminal_module.Field("Output", output_path),),
                    state=terminal_module.ViewState.COMPLETED,
                )
                for output_path in runtime.output_paths[:2]
            ]
            + [
                terminal_module.Record(
                    "context",
                    title="Consensus request",
                    fields=(
                        terminal_module.Field("Output", runtime.output_paths[2]),
                        terminal_module.Field("Source", "literal.md"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
                terminal_module.Record(
                    "context",
                    title="Consensus request",
                    fields=(
                        terminal_module.Field("Output", runtime.output_paths[3]),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
            ],
        )
        self.assertEqual(
            [call["conversation_context"].mode for call in runtime.calls],
            [
                DebateContextMode.INCLUDED,
                DebateContextMode.FRESH,
                DebateContextMode.INCLUDED,
                DebateContextMode.INCLUDED,
            ],
        )
        self.assertTrue(
            all(
                call["conversation_context"].conversation_id
                == entries[0].conversation_id
                for call in runtime.calls
            )
        )
        self.assertEqual(len({id(loop) for loop in runtime.loops}), 1)
        self.assertTrue(all(loop.is_closed() for loop in runtime.loops))
        self.assertTrue(all(task is not None and task.done() for task in runtime.tasks))
        self.assertEqual(file_bytes(workspace), before)
        self.assertFalse(
            workspace.path(
                _transcript_relative("session", entries[0].conversation_id)
            ).exists()
        )
        self.assertEqual(len(attachment_facts), 4)
        self.assertTrue(
            all(facts[0] == "session" for facts in attachment_facts)
        )
        self.assertTrue(
            all(
                isinstance(facts[1], ConversationMetadata)
                and facts[1].conversation_id == entries[0].conversation_id
                for facts in attachment_facts
            )
        )
        self.assertEqual(len({id(facts[3]) for facts in attachment_facts}), 1)
        rendered = "".join(callbacks.records)
        self.assertIn("[compose] Consensus run", rendered)
        self.assertNotIn("You → Consensus", rendered)
        self.assertNotIn("exact loaded draft", rendered)
        self.assertNotIn("RAW_RESULT_SECRET", rendered)

    def test_bare_run_cancel_interrupt_and_eof_never_dispatch(self) -> None:
        cases = (
            ("cancel", ("/run", "discarded", "/cancel", "/quit"), 0),
            ("interrupt", ("/run", KeyboardInterrupt(), "/quit"), 0),
            ("eof", ("/run", EOFError()), 0),
        )
        for label, reads, expected_exit in cases:
            with self.subTest(label=label):
                runtime = ScriptedConsensusRuntime()
                (
                    workspace,
                    _catalog,
                    entries,
                    controller,
                    _backend,
                    _callbacks,
                    _store,
                ) = self.prepared_selected_controller(
                    *reads,
                    runtime=runtime,  # type: ignore[arg-type]
                )
                before = file_bytes(workspace)
                accepted_components: list[object] = []
                semantic_components: list[object] = []
                with (
                    _capture_semantic_method(
                        "write_accepted_input",
                        accepted_components,
                    ),
                    _capture_semantic_method(
                        "write_component",
                        semantic_components,
                    ),
                ):
                    self.assertEqual(
                        controller.run_session_lobby(),
                        expected_exit,
                    )
                self.assertEqual(runtime.calls, [])
                self.assertEqual(accepted_components, [])
                self.assertFalse(
                    any(
                        component.title == "Consensus request"
                        for component in semantic_components
                        if type(component) is terminal_module.Record
                    )
                )
                self.assertEqual(file_bytes(workspace), before)
                self.assertEqual(
                    load_default_conversation(workspace, "session"),
                    entries[0].conversation_id,
                )


class InteractiveConsensusResultTests(InteractiveConversationTestCase):
    """Verify safe terminal mappings, recovery, and tamper rejection."""

    def test_valid_reference_operation_identity_is_not_presented(self) -> None:
        operation_id = "rr_07_session_" + "a" * 32
        runtime = ScriptedConsensusRuntime(
            ConsensusOutcome(reference_operation_id=operation_id)
        )
        (_workspace, _catalog, _entries, controller, _backend, callbacks, _store) = (
            self.prepared_selected_controller(
                "/run referenced",
                "/quit",
                runtime=runtime,  # type: ignore[arg-type]
            )
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        self.assertIn("Consensus [completed]", rendered)
        self.assertNotIn(operation_id, rendered)

    def test_all_terminal_mappings_are_safe_and_recover_to_the_prompt(self) -> None:
        failure_kinds = tuple(DebateFailureKind)
        runtime = ScriptedConsensusRuntime(
            ConsensusOutcome(DebateStatus.CONSENSUS),
            ConsensusOutcome(DebateStatus.DEADLOCK),
            *(ConsensusOutcome(DebateStatus.FAILED, kind) for kind in failure_kinds),
        )
        commands = tuple(
            f"/run outcome {index}" for index in range(2 + len(failure_kinds))
        )
        (
            workspace,
            _catalog,
            entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            *commands,
            "/quit",
            runtime=runtime,  # type: ignore[arg-type]
        )
        before = file_bytes(workspace)
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(len(runtime.calls), len(commands))
        self.assertEqual(file_bytes(workspace), before)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            entries[0].conversation_id,
        )

        rendered = "".join(callbacks.records)
        expected_codes = (
            InteractiveErrorCode.CONSENSUS_DEADLOCK,
            InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
            InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR,
            InteractiveErrorCode.TRANSCRIPT_INVALID,
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.ARTIFACT_CONFLICT,
            InteractiveErrorCode.CONSENSUS_FAILED,
            InteractiveErrorCode.CONSENSUS_FAILED,
        )
        for code in expected_codes:
            with self.subTest(code=code):
                self.assertIn(_error_record(code), rendered)
        self.assertIn("debate_1", rendered)
        self.assertIn("manuscript/part-01/interactive-2.md", rendered)
        self.assertIn("global_outline.md", rendered)
        self.assertIn("r0", rendered)
        self.assertNotIn("RAW_RESULT_SECRET", rendered)
        self.assertNotIn("\x1b", rendered)

    def test_no_progress_input_errors_use_only_trusted_draft_facts(self) -> None:
        runtime = ScriptedConsensusRuntime(
            TypeError("empty-type-secret"),
            ValueError("empty-value-secret"),
            TypeError("context-type-secret"),
            ValueError("context-value-secret"),
            ConsensusOutcome(),
        )
        (
            _workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            "/draft-file empty-type.md",
            "/draft-file empty-value.md",
            "/draft-file context-type.md",
            "/draft-file context-value.md",
            "/run recovered",
            "/quit",
            runtime=runtime,  # type: ignore[arg-type]
        )
        with mock.patch.object(
            controller_core_module,
            "read_draft_file",
            side_effect=("", "", "nonempty draft", "nonempty draft"),
        ) as loader:
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(loader.call_count, 4)
        self.assertEqual(len(runtime.calls), 5)
        self.assertEqual(
            [call["payload"] for call in runtime.calls],
            ["", "", "nonempty draft", "nonempty draft", "recovered"],
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count(_error_record(InteractiveErrorCode.DRAFT_INPUT_INVALID)),
            2,
        )
        self.assertEqual(
            rendered.count(_error_record(InteractiveErrorCode.TRANSCRIPT_INVALID)),
            2,
        )
        self.assertIn("debate_5", rendered)
        for secret in (
            "empty-type-secret",
            "empty-value-secret",
            "context-type-secret",
            "context-value-secret",
        ):
            self.assertNotIn(secret, rendered)

    def test_workspace_failures_recover_only_with_active_owned_proof(self) -> None:
        runtime = ScriptedConsensusRuntime(
            WorkspaceSecurityError("security-secret"),
            OSError("io-secret"),
            ConsensusOutcome(
                DebateStatus.FAILED,
                DebateFailureKind.WORKSPACE,
            ),
            ConsensusOutcome(),
        )
        (
            _workspace,
            _catalog,
            _entries,
            controller,
            backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            "/run security failure",
            "/run io failure",
            "/run durable workspace failure",
            "/run recovered",
            "/quit",
            runtime=runtime,  # type: ignore[arg-type]
        )
        original_summary = backend.get_session_lifecycle
        retained_summaries: list[object] = []

        def record_owned_summary(
            session_id: str,
        ) -> object:
            """Record every public summary used to prove safe recovery."""

            summary = original_summary(session_id)
            retained_summaries.append(summary)
            return summary

        with (
            mock.patch.object(
                backend,
                "get_session_lifecycle",
                side_effect=record_owned_summary,
            ),
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(len(runtime.calls), 4)
        held_proofs = [
            summary
            for summary in retained_summaries
            if summary.access_state is SessionAccessState.HELD_HERE
        ]
        self.assertGreaterEqual(len(held_proofs), 3)
        self.assertTrue(
            all(
                summary.durable_state is SessionDurableState.READY
                for summary in held_proofs
            )
        )
        detach.assert_called_once_with("session")
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count(_error_record(InteractiveErrorCode.WORKSPACE_INVALID)),
            3,
        )
        self.assertIn("debate_4", rendered)
        self.assertNotIn("security-secret", rendered)
        self.assertNotIn("io-secret", rendered)
        self.assertNotIn("RAW_RESULT_SECRET", rendered)

    def test_unproven_workspace_recovery_aborts_without_detach_or_next_run(
        self,
    ) -> None:
        def missing_summary(_summary: object) -> object:
            """Model a proof read whose attached session disappeared."""

            raise SessionNotFoundError("missing-proof-secret")

        cases = (
            (
                "pre_result_unowned",
                OSError("pre-unowned-secret"),
                lambda summary: replace(
                    summary,
                    access_state=SessionAccessState.CLAIMABLE,
                ),
            ),
            (
                "pre_result_format_unowned",
                WorkspaceFormatError("pre-format-unowned-secret"),
                lambda summary: replace(
                    summary,
                    access_state=SessionAccessState.CLAIMABLE,
                ),
            ),
            (
                "pre_result_missing",
                WorkspaceSecurityError("pre-missing-secret"),
                missing_summary,
            ),
            (
                "durable_malformed",
                ConsensusOutcome(
                    DebateStatus.FAILED,
                    DebateFailureKind.WORKSPACE,
                ),
                lambda _summary: object(),
            ),
        )
        for label, first_outcome, alter_summary in cases:
            with self.subTest(case=label):
                runtime = ScriptedConsensusRuntime(
                    first_outcome,
                    ConsensusOutcome(),
                )
                (
                    _workspace,
                    _catalog,
                    _entries,
                    controller,
                    backend,
                    callbacks,
                    _store,
                ) = self.prepared_selected_controller(
                    "/run failing",
                    "/run must not execute",
                    "/quit",
                    runtime=runtime,  # type: ignore[arg-type]
                )
                original_summary = backend.get_session_lifecycle
                summary_calls = 0
                altered_calls = 0

                def change_recovery_summary(
                    session_id: str,
                ) -> object:
                    """Keep startup valid, then alter only recovery proof."""

                    nonlocal summary_calls
                    nonlocal altered_calls
                    summary_calls += 1
                    summary = original_summary(session_id)
                    if not runtime.calls:
                        return summary
                    altered_calls += 1
                    return alter_summary(summary)

                with (
                    mock.patch.object(
                        backend,
                        "get_session_lifecycle",
                        side_effect=change_recovery_summary,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        wraps=backend.release_session_access,
                    ) as detach,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                self.assertGreaterEqual(summary_calls, 4)
                self.assertEqual(altered_calls, 1)
                self.assertEqual(len(runtime.calls), 1)
                detach.assert_not_called()
                self.assertIsNone(controller._attached_session_id)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    rendered.count(_error_record(InteractiveErrorCode.CLEANUP_FAILED)),
                    1,
                )
                self.assertNotIn(
                    _error_record(InteractiveErrorCode.WORKSPACE_INVALID),
                    rendered,
                )
                for secret in (
                    "pre-unowned-secret",
                    "pre-format-unowned-secret",
                    "pre-missing-secret",
                    "missing-proof-secret",
                    "RAW_RESULT_SECRET",
                ):
                    self.assertNotIn(secret, rendered)

    def test_tampered_progress_or_result_is_fatal_and_never_echoed(self) -> None:
        cases = (
            ConsensusOutcome(tampered_progress=True),
            ConsensusOutcome(tampered_result=True),
            ConsensusOutcome(
                reference_operation_id=(
                    "rr_11_different_session_" + "a" * 32
                )
            ),
            ConsensusOutcome(reference_operation_id="rr_malformed"),
        )
        for outcome in cases:
            with self.subTest(outcome=outcome):
                runtime = ScriptedConsensusRuntime(outcome)
                (
                    _workspace,
                    _catalog,
                    _entries,
                    controller,
                    backend,
                    callbacks,
                    _store,
                ) = self.prepared_selected_controller(
                    "/run tampered",
                    "/quit",
                    runtime=runtime,  # type: ignore[arg-type]
                )
                with mock.patch.object(
                    backend,
                    "release_session_access",
                    wraps=backend.release_session_access,
                ) as detach:
                    self.assertEqual(controller.run_session_lobby(), 2)
                detach.assert_called_once_with("session")
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                    rendered,
                )
                self.assertNotIn("foreign_session", rendered)
                self.assertNotIn("rr_", rendered)
                self.assertNotIn("RAW_RESULT_SECRET", rendered)


class RecordingMixedBoundAgent:
    """Serve real retained chat and consensus calls without a provider."""

    def __init__(
        self,
        definition: AgentDefinition,
        conversation_requests: list[ConversationRetrievalRequest],
        debate_requests: list[AgentRequest],
        loops: list[asyncio.AbstractEventLoop],
    ) -> None:
        self.definition = definition
        self.conversation_requests = conversation_requests
        self.debate_requests = debate_requests
        self.loops = loops

    async def converse(self, request: ConversationRequest) -> ConversationReply:
        """Return one correlated visible advisory reply."""

        self.conversation_requests.append(request)  # type: ignore[arg-type]
        self.loops.append(asyncio.get_running_loop())
        return ConversationReply(
            request.session_id,
            request.conversation_id,
            request.turn_id,
            f"reply:{request.content}",
        )

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        """Resolve one direct answer through the runtime retrieval handler."""

        self.conversation_requests.append(request)
        self.loops.append(asyncio.get_running_loop())
        outcome = handler.handle_conversation_retrieval_action(
            ConversationRetrievalAnswerAction(
                kind=ConversationRetrievalActionKind.ANSWER,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                action_index=1,
                content=f"reply:{request.content}",
                citation_ids=(),
            )
        )
        if type(outcome) is not ConversationRetrievalReply:
            raise AssertionError("direct answer did not produce a retrieval reply")
        return outcome

    async def invoke(self, request: AgentRequest) -> AgentReply:
        """Approve one debate immediately while retaining request evidence."""

        if request.agent_id != self.definition.agent_id:
            raise AssertionError("request crossed a bound-agent definition")
        self.debate_requests.append(request)
        self.loops.append(asyncio.get_running_loop())
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, "generated candidate")
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


class RealRetainedConsensusRuntimeTests(InteractiveConversationTestCase):
    """Exercise chat and consensus on one runtime and retained Runner loop."""

    def test_malformed_system_defaults_after_construction_keep_active_snapshot(
        self,
    ) -> None:
        """Keep one constructed runtime active despite later default corruption."""

        run_command_returned = False

        def corrupt_defaults_before_run(_prompt: str) -> str:
            """Corrupt defaults only after startup has safely attached."""

            nonlocal run_command_returned
            workspace.path("config/system_defaults.json").write_bytes(b"{invalid")
            run_command_returned = True
            return "/run malformed defaults"

        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            corrupt_defaults_before_run,
            "/quit",
            runtime=_REAL_RUNTIME,
        )
        original_summary = backend.get_session_lifecycle
        summary_observations: list[tuple[bool, Any]] = []

        def record_summary_phase(
            session_id: str,
        ) -> object:
            """Distinguish startup lookup from active run lifecycle reads."""

            summary = original_summary(session_id)
            summary_observations.append((run_command_returned, summary))
            return summary

        with (
            mock.patch.object(
                backend,
                "get_session_lifecycle",
                side_effect=record_summary_phase,
            ),
            mock.patch.object(
                type(controller),
                "_confirm_attachment_after_command_failure",
                autospec=True,
                side_effect=(
                    type(controller)._confirm_attachment_after_command_failure
                ),
            ) as confirm_attachment,
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
            mock.patch.object(
                workspace,
                "reserve_debate",
                wraps=workspace.reserve_debate,
            ) as reserve,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertTrue(any(not phase for phase, _summary in summary_observations))
        self.assertTrue(any(phase for phase, _summary in summary_observations))
        post_dispatch_proofs = [
            summary
            for after_dispatch, summary in summary_observations
            if (
                after_dispatch
                and summary.access_state is SessionAccessState.HELD_HERE
            )
        ]
        self.assertEqual(len(post_dispatch_proofs), 4)
        proof = post_dispatch_proofs[0]
        self.assertIs(proof.durable_state, SessionDurableState.READY)
        self.assertIs(proof.access_state, SessionAccessState.HELD_HERE)
        confirm_attachment.assert_not_called()
        reserve.assert_called_once()
        reserve_call = reserve.call_args
        self.assertEqual(reserve_call.args[0], "session")
        self.assertRegex(reserve_call.args[1], r"^d[0-9]{8}T[0-9]{6}_[0-9a-f]{8}$")
        self.assertEqual(reserve_call.kwargs["input_kind"], "prompt")
        self.assertRegex(reserve_call.kwargs["owner_token"], r"^[0-9a-f]{32}$")
        detach.assert_called_once_with("session")
        self.assertEqual(callbacks.reads, [])
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error "), 1)
        self.assertEqual(
            rendered.count(_error_record(InteractiveErrorCode.WORKSPACE_INVALID)),
            0,
        )
        self.assertEqual(
            rendered.count(
                _error_record(InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR)
            ),
            1,
        )
        self.assertEqual(rendered.count("Started prompt consensus."), 1)

    def test_malformed_selected_transcript_is_not_a_workspace_failure(
        self,
    ) -> None:
        """Normalize a selected-transcript format error without lease proof."""

        run_command_returned = False

        def request_malformed_transcript_run(_prompt: str) -> str:
            """Mark the exact point after startup and before run dispatch."""

            nonlocal run_command_returned
            run_command_returned = True
            return "/run malformed transcript"

        (
            workspace,
            _catalog,
            entries,
            controller,
            backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            request_malformed_transcript_run,
            "/quit",
            runtime=_REAL_RUNTIME,
        )
        transcript_path = workspace.path(
            _transcript_relative("session", entries[0].conversation_id)
        )
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_bytes(b"{invalid")
        original_summary = backend.get_session_lifecycle
        summary_observations: list[tuple[bool, Any]] = []

        def record_summary_phase(
            session_id: str,
        ) -> object:
            """Record whether any summary read occurs after run dispatch."""

            summary = original_summary(session_id)
            summary_observations.append((run_command_returned, summary))
            return summary

        with (
            mock.patch.object(
                backend,
                "get_session_lifecycle",
                side_effect=record_summary_phase,
            ),
            mock.patch.object(
                type(controller),
                "_confirm_attachment_after_command_failure",
                autospec=True,
                side_effect=(
                    type(controller)._confirm_attachment_after_command_failure
                ),
            ) as confirm_attachment,
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
            mock.patch.object(
                workspace,
                "reserve_debate",
                wraps=workspace.reserve_debate,
            ) as reserve,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertTrue(any(not phase for phase, _summary in summary_observations))
        confirm_attachment.assert_not_called()
        reserve.assert_not_called()
        detach.assert_called_once_with("session")
        self.assertEqual(callbacks.reads, [])
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error "), 1)
        self.assertEqual(
            rendered.count(_error_record(InteractiveErrorCode.TRANSCRIPT_INVALID)),
            1,
        )
        self.assertNotIn("Started prompt consensus.", rendered)

    def test_mixed_chat_included_fresh_and_draft_runs_remain_loop_safe(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        selected = catalog.create("session", "Selected")
        save_default_conversation(
            workspace,
            "session",
            selected.conversation_id,
        )
        draft_path = workspace.path("draft-input.md")
        draft_path.write_text("supplied draft", encoding="utf-8")
        backend = self.make_backend(workspace)
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "chat question",
            "/run included prompt",
            "/run --fresh fresh prompt",
            f"/draft-file {draft_path}",
            "/quit",
            backend=backend,
            catalog=catalog,
            runtime=_REAL_RUNTIME,
        )
        conversation_requests: list[ConversationRetrievalRequest] = []
        debate_requests: list[AgentRequest] = []
        loops: list[asyncio.AbstractEventLoop] = []
        definitions: list[AgentDefinition] = []

        async def bind(definition: AgentDefinition) -> RecordingMixedBoundAgent:
            definitions.append(definition)
            return RecordingMixedBoundAgent(
                definition,
                conversation_requests,
                debate_requests,
                loops,
            )

        store = controller._conversation_event_store
        with (
            mock.patch.object(backend, "bind", new=bind),
            mock.patch.object(
                store,
                "read_context",
                wraps=store.read_context,
            ) as read_context,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
            ) as run_boundary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        run_boundary.assert_not_called()
        self.assertEqual(read_context.call_count, 3)
        self.assertEqual(len(conversation_requests), 1)
        transcript = FilesystemConversationEventStore(
            workspace,
            catalog=catalog,
        ).read("session", selected.conversation_id)
        self.assertEqual(
            tuple(event.content for event in transcript),
            ("chat question", "reply:chat question"),
        )

        grouped: dict[str, list[AgentRequest]] = {}
        for request in debate_requests:
            grouped.setdefault(request.debate_id, []).append(request)
        self.assertEqual(len(grouped), 3)
        by_payload = {
            requests[0].user_payload: requests for requests in grouped.values()
        }
        included = by_payload["included prompt"]
        fresh = by_payload["fresh prompt"]
        supplied = by_payload["supplied draft"]
        expected_context = ("chat question", "reply:chat question")
        for requests in (included, supplied):
            self.assertTrue(
                all(
                    tuple(
                        event.content
                        for event in request.conversation_context.events
                    )
                    == expected_context
                    for request in requests
                )
            )
        self.assertTrue(
            all(request.conversation_context is None for request in fresh)
        )

        action_loops = [loops[0]]
        action_loops.extend(
            next(
                loop
                for request, loop in zip(debate_requests, loops[1:], strict=True)
                if request.debate_id == debate_id
            )
            for debate_id in grouped
        )
        self.assertEqual(len({id(loop) for loop in action_loops}), 1)
        self.assertTrue(all(loop.is_closed() for loop in action_loops))
        self.assertTrue(definitions)

        for debate_id, requests in grouped.items():
            sidecar = workspace.inspect_debate_context("session", debate_id)
            assert sidecar is not None
            expected_mode = (
                "fresh"
                if requests[0].user_payload == "fresh prompt"
                else "included"
            )
            self.assertEqual(sidecar["mode"], expected_mode)
            if expected_mode == "fresh":
                self.assertIsNone(sidecar["snapshot"])
            else:
                self.assertIsNotNone(sidecar["snapshot"])
        self.assertEqual(len(all_manuscripts(workspace)), 3)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            selected.conversation_id,
        )
        self.assertNotIn("loop", "".join(callbacks.records).casefold())


if __name__ == "__main__":
    unittest.main()
