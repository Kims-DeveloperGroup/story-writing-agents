"""Interactive directives, history, status, and inspection contracts."""

from __future__ import annotations

import socket
import subprocess
import unittest
from dataclasses import replace
from unittest import mock

from story_writing_agents.agents import (
    AGENT_SPECS,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
)
from story_writing_agents.cli_context import (
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
)
from story_writing_agents.conversations.events import ConversationEventAppend
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_terminal import InteractiveErrorCode
from story_writing_agents.interactive_terminal._render import render_plain
from story_writing_agents.models import (
    DebateContextMode,
    DebateMessage,
    DebateResult,
    DebateStatus,
    InputKind,
)
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    Workspace,
)

from tests.helpers import core_snapshot
from tests.interactive_controller_fixture import (
    REAL_RUNTIME as _REAL_RUNTIME,
    InteractiveControllerTestCase as InteractiveConversationTestCase,
    error_record as _error_record,
)

# Retained only by the non-collected schema-v1 history examples below.
FilesystemConversationEventStore = object


_TIMESTAMP = "2026-08-16T00:00:00+00:00"


def _durable_file_snapshot(workspace: Workspace) -> dict[str, tuple[bytes, int]]:
    """Capture domain state while excluding leases and diagnostic artifacts."""

    return {
        path.relative_to(workspace.root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file()
        and not path.name.endswith(".access.json")
        and "debug_logs" not in path.relative_to(workspace.root).parts
    }


def _append_visible_event(
    store: FilesystemConversationEventStore,
    metadata: ConversationMetadata,
    *,
    turn_id: str,
    kind: ConversationEventKind,
    content: str,
) -> None:
    """Append one visible event using the canonical actors for its kind."""

    if kind is ConversationEventKind.USER_MESSAGE:
        sender, recipient = "user", LEAD_ORCHESTRATOR
    elif kind is ConversationEventKind.AGENT_REPLY:
        sender, recipient = LEAD_ORCHESTRATOR, "user"
    else:
        sender, recipient = "runtime", "user"
    store.append(
        ConversationEventAppend(
            session_id=metadata.session_id,
            conversation_id=metadata.conversation_id,
            turn_id=turn_id,
            sender=sender,
            recipient=recipient,
            kind=kind,
            content=content,
        )
    )


def _persist_terminal_debate(
    workspace: Workspace,
    *,
    session_id: str,
    debate_id: str,
    status: DebateStatus | str,
    secret: str,
) -> None:
    """Persist one inspectable terminal debate containing non-display secrets."""

    token = f"owner_{debate_id}"
    workspace.reserve_debate(
        session_id,
        debate_id,
        input_kind=InputKind.PROMPT.value,
        owner_token=token,
    )
    workspace.append_debate_message(
        session_id,
        debate_id,
        DebateMessage(
            sender=LEAD_ORCHESTRATOR,
            phase="audit",
            round=1,
            revision="r1",
            kind="secret",
            content=f"{secret}_MESSAGE",
            timestamp=_TIMESTAMP,
        ),
    )
    status_value = status.value if isinstance(status, DebateStatus) else status
    workspace.transition_debate(
        session_id,
        debate_id,
        owner_token=token,
        status=status_value,
        terminal=True,
        revision_id="r1",
        rounds_completed=1,
        error=(f"{secret}_STATE_ERROR" if status_value == "failed" else None),
    )
    if not isinstance(status, DebateStatus):
        return
    workspace.save_debate_result(
        DebateResult(
            session_id=session_id,
            debate_id=debate_id,
            input_kind=InputKind.PROMPT,
            status=status,
            revision_id="r1",
            rounds_completed=1,
            final_candidate=(
                f"{secret}_FINAL" if status is DebateStatus.CONSENSUS else None
            ),
            latest_candidate=f"{secret}_LATEST",
            manuscript_path=(
                "manuscript/part-01/safe-result.md"
                if status is DebateStatus.CONSENSUS
                else None
            ),
            core_updates=(),
            error=(f"{secret}_RESULT_ERROR" if status is DebateStatus.FAILED else None),
            action_required=(
                f"{secret}_ACTION" if status is DebateStatus.DEADLOCK else None
            ),
        )
    )


class InteractiveManagementGateAndDirectiveTests(InteractiveConversationTestCase):
    """Verify global gates and exact existing directive persistence."""

    def test_global_help_status_and_attachment_gates_are_provider_free(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/? status",
            "/help override",
            "/help language",
            "/help persona",
            "/help reset",
            "/status",
            "/language EN",
            "/persona plot default_kim_eun_hee",
            "/reset",
            "/history",
            "/inspect",
            "/quit",
            runtime=_REAL_RUNTIME,
        )
        before = _durable_file_snapshot(workspace)
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
            mock.patch.object(controller_core_module.asyncio, "run") as async_run,
            mock.patch.object(
                controller._conversation_storage_services.event_store,
                "read",
            ) as transcript_read,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        async_run.assert_not_called()
        transcript_read.assert_not_called()
        self.assertEqual(_durable_file_snapshot(workspace), before)
        rendered = "".join(callbacks.records)
        self.assertIn("Syntax: /status", rendered)
        self.assertEqual(
            rendered.count(
                "Syntax: /override language LANG | persona AGENT NAME | reset"
            ),
            4,
        )
        self.assertIn(
            "[session]\n"
            "  Session: none\n"
            "  Durable state: absent\n"
            "  Process access: claimable\n"
            "  Default session: none\n"
            "  Current attachment: none\n",
            rendered,
        )
        self.assertIn(
            "  Default conversation: none\n"
            "  Current focus: none\n"
            "  Provider readiness: no\n"
            "  Next action: Create a session.\n",
            rendered,
        )
        self.assertEqual(rendered.count("[error session_not_attached]"), 5)
        execution_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "Execution profiles"
        ]
        self.assertEqual(len(execution_views), 1)
        execution_view = execution_views[0]
        self.assertEqual(execution_view.total_count, len(ROSTER))
        self.assertEqual(
            tuple(record.identity for record in execution_view.items),
            ROSTER,
        )
        for record, agent_id in zip(execution_view.items, ROSTER, strict=True):
            with self.subTest(agent_id=agent_id):
                self.assertEqual(record.title, AGENT_SPECS[agent_id].display_name)
                self.assertEqual(
                    tuple((field.label, field.value) for field in record.fields),
                    (
                        ("Provider", "Codex"),
                        ("Provider source", "built in default"),
                        ("Model", "provider default"),
                        ("Model source", "provider default"),
                        ("Effort", "provider default"),
                        ("Effort source", "provider default"),
                        ("Confirmation", "pending"),
                        ("Thread", "not observed"),
                        ("Route generation", "0"),
                    ),
                )

        attached = self.make_workspace()
        self.seed_session(attached, "session")
        save_default_session(attached, "session")
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            attached,
            "/status",
            "/history",
            "/inspect",
            "/inspect last",
            "/quit",
            runtime=_REAL_RUNTIME,
        )
        with mock.patch.object(
            controller._conversation_storage_services.event_store,
            "read",
        ) as transcript_read:
            self.assertEqual(controller.run_session_lobby(), 0)
        transcript_read.assert_not_called()
        rendered = "".join(callbacks.records)
        self.assertIn(
            "  Default session: session\n"
            "  Current attachment: session\n"
            "  Default conversation: none\n"
            "  Current focus: none\n",
            rendered,
        )
        self.assertIn("Conversation focus: none | Context: unavailable.", rendered)
        self.assertEqual(rendered.count("[error conversation_required]"), 1)
        self.assertIn("Session session: 0 persisted debates.", rendered)

    def test_directives_use_exact_forms_and_persist_only_validated_success(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")

        def verify_language_then_set_persona(_prompt: str) -> str:
            """Verify language durability before returning the persona command."""

            self.assertEqual(
                workspace.load_session_overrides("session"),
                {"language": "EN", "persona": None, "agents": {}},
            )
            return "/override persona plot default_kim_eun_hee"

        def verify_persona_then_reset(_prompt: str) -> str:
            """Verify canonical agent persistence before returning reset."""

            self.assertEqual(
                workspace.load_session_overrides("session"),
                {
                    "language": "EN",
                    "persona": None,
                    "agents": {
                        PLOT_ARCHITECT: {
                            "language": None,
                            "persona": "default_kim_eun_hee",
                        }
                    },
                },
            )
            return "/override reset"

        def verify_reset_then_quit(_prompt: str) -> str:
            """Verify reset clears only runtime overrides before quitting."""

            self.assertEqual(
                workspace.load_session_overrides("session"),
                {"language": None, "persona": None, "agents": {}},
            )
            return "/quit"

        controller, backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/override language EN",
            verify_language_then_set_persona,
            verify_persona_then_reset,
            verify_reset_then_quit,
            runtime=_REAL_RUNTIME,
        )
        system_before = workspace.read_text("config/system_defaults.json")
        agents_before = workspace.read_text("config/agent_configs.json")
        with (
            mock.patch.object(
                controller._runtime,
                "apply_directive",
                wraps=controller._runtime.apply_directive,
            ) as apply_directive,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                subprocess,
                "Popen",
                wraps=subprocess.Popen,
            ) as popen,
            mock.patch.object(socket, "create_connection") as connect,
            mock.patch.object(
                controller._conversation_storage_services.event_store,
                "append_batch",
            ) as append,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            apply_directive.call_args_list,
            [
                mock.call("session", "[SYS: LANG=EN]"),
                mock.call(
                    "session",
                    "[OVERRIDE: plot_architect Persona=default_kim_eun_hee]",
                ),
                mock.call("session", "[RESET: ALL]"),
            ],
        )
        bind.assert_not_called()
        self.assertTrue(popen.called)
        self.assertTrue(
            all(call.args[0][0] == "git" for call in popen.call_args_list)
        )
        connect.assert_not_called()
        append.assert_not_called()
        self.assertIsNone(load_default_conversation(workspace, "session"))
        self.assertEqual(
            workspace.read_text("config/system_defaults.json"),
            system_before,
        )
        self.assertEqual(
            workspace.read_text("config/agent_configs.json"),
            agents_before,
        )
        self.assertEqual(workspace.inspect_session("session")["debates"], [])
        self.assertEqual(
            [path.name for path in workspace.path("manuscript").iterdir()],
            [".gitkeep"],
        )
        rendered = "".join(callbacks.records)
        self.assertIn("Language override set to EN.", rendered)
        self.assertIn(
            "Persona override for plot_architect set to default_kim_eun_hee.",
            rendered,
        )
        self.assertIn("Runtime overrides reset.", rendered)

    def test_invalid_directives_preserve_existing_override_bytes(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        workspace.save_session_overrides(
            "session",
            {"language": "EN", "persona": None, "agents": {}},
        )
        state = workspace.path("agent_states/orchestrator_state/sessions/session.json")
        before = (state.read_bytes(), state.stat().st_mtime_ns)
        controller, backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/override language 한국어.v2",
            "/override persona plot missing_persona",
            "/quit",
            runtime=_REAL_RUNTIME,
        )
        with (
            mock.patch.object(
                controller._runtime,
                "apply_directive",
                wraps=controller._runtime.apply_directive,
            ) as apply_directive,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                subprocess,
                "Popen",
                wraps=subprocess.Popen,
            ) as popen,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            apply_directive.call_args_list,
            [
                mock.call("session", "[SYS: LANG=한국어.v2]"),
                mock.call(
                    "session",
                    "[OVERRIDE: plot_architect Persona=missing_persona]",
                ),
            ],
        )
        bind.assert_not_called()
        self.assertTrue(popen.called)
        self.assertTrue(
            all(call.args[0][0] == "git" for call in popen.call_args_list)
        )
        connect.assert_not_called()
        self.assertEqual((state.read_bytes(), state.stat().st_mtime_ns), before)
        self.assertEqual(
            "".join(callbacks.records).count("[error configuration_invalid]"),
            2,
        )


class _RetiredSchemaV1InteractiveHistoryAndStatusExamples:
    """Verify one-snapshot history and status context accounting."""

    def selected_controller(
        self,
        *reads: object,
    ) -> tuple[
        Workspace,
        FilesystemConversationCatalog,
        ConversationMetadata,
        object,
        object,
        object,
        FilesystemConversationEventStore,
    ]:
        """Create one selected real-runtime controller and expose its store."""

        (
            workspace,
            catalog,
            entries,
            controller,
            backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            *reads,
            runtime=_REAL_RUNTIME,
        )
        return (
            workspace,
            catalog,
            entries[0],
            controller,
            backend,
            callbacks,
            controller._conversation_storage_services.event_store,
        )

    @staticmethod
    def seed_mixed_history(
        store: FilesystemConversationEventStore,
        metadata: ConversationMetadata,
    ) -> None:
        """Seed two complete turns and one unmatched visible user event."""

        for turn_id, kind, content in (
            ("turn_reply", ConversationEventKind.USER_MESSAGE, "question"),
            (
                "turn_reply",
                ConversationEventKind.AGENT_REPLY,
                '{"answer":"reply","items":[1,true]}',
            ),
            ("turn_error", ConversationEventKind.USER_MESSAGE, "bad request"),
            ("turn_error", ConversationEventKind.RUNTIME_ERROR, "safe failure"),
            ("turn_open", ConversationEventKind.USER_MESSAGE, "unmatched"),
        ):
            _append_visible_event(
                store,
                metadata,
                turn_id=turn_id,
                kind=kind,
                content=content,
            )

    def test_history_uses_one_fixed_snapshot_and_renders_every_visible_kind(
        self,
    ) -> None:
        (
            workspace,
            _catalog,
            metadata,
            controller,
            backend,
            callbacks,
            store,
        ) = self.selected_controller("/history", "/quit")
        self.assertFalse(controller._show_message_details)
        self.seed_mixed_history(store, metadata)
        external_store = FilesystemConversationEventStore(
            workspace,
            catalog=controller._conversation_catalog,
        )
        core_before = core_snapshot(workspace)
        selection_before = load_default_conversation(workspace, "session")

        def capture_then_append(
            session_id: str,
            conversation_id: str,
        ) -> tuple[object, ...]:
            """Append externally only after capturing the command snapshot."""

            snapshot = external_store.read(session_id, conversation_id)
            _append_visible_event(
                external_store,
                metadata,
                turn_id="turn_late",
                kind=ConversationEventKind.USER_MESSAGE,
                content="LATE_SNAPSHOT_SECRET",
            )
            return snapshot

        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(store, "read", side_effect=capture_then_append) as read,
            mock.patch.object(store, "read_context") as read_context,
            mock.patch.object(store, "append") as append,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(socket, "create_connection") as connect,
            mock.patch.object(
                controller._conversation_catalog,
                "create",
            ) as create,
            mock.patch.object(
                controller._conversation_catalog,
                "rename",
            ) as rename,
            mock.patch.object(
                controller._conversation_catalog,
                "archive",
            ) as archive,
            mock.patch.object(
                controller._conversation_catalog,
                "unarchive",
            ) as unarchive,
            mock.patch.object(
                controller_core_module,
                "save_default_conversation",
            ) as save_selection,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        read.assert_called_once_with("session", metadata.conversation_id)
        read_context.assert_not_called()
        append.assert_not_called()
        bind.assert_not_called()
        popen.assert_not_called()
        connect.assert_not_called()
        create.assert_not_called()
        rename.assert_not_called()
        archive.assert_not_called()
        unarchive.assert_not_called()
        save_selection.assert_not_called()
        self.assertEqual(core_snapshot(workspace), core_before)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            selection_before,
        )
        self.assertEqual(
            [path.name for path in workspace.path("manuscript").iterdir()],
            [".gitkeep"],
        )
        self.assertEqual(
            len(external_store.read("session", metadata.conversation_id)),
            6,
        )
        rendered = "".join(callbacks.records)
        history_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "History"
        ]
        formatted_reply = (
            '{\n  "answer": "reply",\n  "items": [\n    1,\n    true\n  ]\n}'
        )
        expected_events = (
            (
                1,
                "You → Lead Orchestrator",
                "question",
                terminal_module.SemanticRole.DEFAULT,
                terminal_module.SpeakerAccent.MAGENTA,
            ),
            (
                2,
                "Lead Orchestrator → You",
                formatted_reply,
                terminal_module.SemanticRole.AGENT,
                terminal_module.SpeakerAccent.CYAN,
            ),
            (
                3,
                "You → Lead Orchestrator",
                "bad request",
                terminal_module.SemanticRole.DEFAULT,
                terminal_module.SpeakerAccent.MAGENTA,
            ),
            (
                4,
                "Runtime → You",
                "safe failure",
                terminal_module.SemanticRole.DEFAULT,
                terminal_module.SpeakerAccent.CYAN,
            ),
            (
                5,
                "You → Lead Orchestrator",
                "unmatched",
                terminal_module.SemanticRole.DEFAULT,
                terminal_module.SpeakerAccent.MAGENTA,
            ),
        )
        expected_history = [
            terminal_module.ListView(
                "History",
                (
                    terminal_module.Message(
                        sender,
                        content=content,
                        role=role,
                        state=terminal_module.ViewState.COMPLETED,
                        presentation=(terminal_module.MessagePresentation.CHAT_BLOCK),
                        metadata=f"Sequence: {sequence}",
                        accent=accent,
                    ),
                ),
                total_count=5,
                position=sequence,
                state=terminal_module.ViewState.COMPLETED,
            )
            for sequence, sender, content, role, accent in expected_events
        ]
        self.assertEqual(history_views, expected_history)
        self.assertTrue(
            all(
                type(view.items[0]) is terminal_module.Message
                and view.items[0].metadata == f"Sequence: {view.position}"
                for view in history_views
            )
        )
        self.assertEqual(rendered.count("You → Lead Orchestrator"), 3)
        self.assertEqual(rendered.count("Lead Orchestrator → You"), 1)
        self.assertEqual(rendered.count("Runtime → You"), 1)
        self.assertIn('│   "answer": "reply",\n', rendered)
        self.assertIn("│     true\n", rendered)
        self.assertIn("│ unmatched", rendered)
        for hidden in (
            "Turn:",
            "Kind:",
            "IDs:",
            "Provider:",
            "Model:",
            "Effort:",
            "Confirmation:",
            "Thread:",
        ):
            self.assertNotIn(hidden, rendered)
        self.assertNotIn("LATE_SNAPSHOT_SECRET", rendered)
        retained_bytes = len(
            (
                'question{"answer":"reply","items":[1,true]}bad requestsafe failure'
            ).encode("utf-8")
        )
        expected_context = terminal_module.Record(
            "context",
            fields=(
                terminal_module.Field("History displayed", "5/5 events"),
                terminal_module.Field("Complete turns", "2"),
                terminal_module.Field(
                    "Context retained",
                    "2 turns, 4 events",
                ),
                terminal_module.Field("Omitted turns", "0"),
                terminal_module.Field(
                    "UTF-8 bytes",
                    f"{retained_bytes}/32768",
                ),
                terminal_module.Field("Turn cap", "24"),
                terminal_module.Field("Truncated", "no."),
            ),
            state=terminal_module.ViewState.COMPLETED,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.identity == "context"
            ],
            [expected_context],
        )
        self.assertIn(
            f"{render_plain(expected_context, width=240).text}\n",
            callbacks.records,
        )

    def test_history_newest_tail_is_chronological_and_empty_is_normal(self) -> None:
        (
            _workspace,
            _catalog,
            metadata,
            controller,
            _backend,
            callbacks,
            store,
        ) = self.selected_controller("/history 2", "/quit")
        for turn_number in range(1, 26):
            _append_visible_event(
                store,
                metadata,
                turn_id=f"turn_{turn_number}",
                kind=ConversationEventKind.USER_MESSAGE,
                content="q",
            )
            _append_visible_event(
                store,
                metadata,
                turn_id=f"turn_{turn_number}",
                kind=ConversationEventKind.AGENT_REPLY,
                content="r",
            )
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(store, "read", wraps=store.read) as read,
            mock.patch.object(store, "read_context") as read_context,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        read.assert_called_once_with("session", metadata.conversation_id)
        read_context.assert_not_called()
        rendered = "".join(callbacks.records)
        sequence_forty_nine = rendered.index("Sequence: 49")
        sequence_fifty = rendered.index("Sequence: 50")
        self.assertLess(sequence_forty_nine, sequence_fifty)
        self.assertNotIn("Sequence: 1", rendered)
        self.assertNotIn("Sequence: 2", rendered)
        self.assertNotIn("Sequence: 48", rendered)
        tail_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "History"
        ]
        self.assertEqual(
            [(view.position, view.total_count) for view in tail_views],
            [(1, 2), (2, 2)],
        )
        expected_context = terminal_module.Record(
            "context",
            fields=(
                terminal_module.Field("History displayed", "2/50 events"),
                terminal_module.Field("Complete turns", "25"),
                terminal_module.Field(
                    "Context retained",
                    "24 turns, 48 events",
                ),
                terminal_module.Field("Omitted turns", "1"),
                terminal_module.Field("UTF-8 bytes", "48/32768"),
                terminal_module.Field("Turn cap", "24"),
                terminal_module.Field("Truncated", "yes."),
            ),
            state=terminal_module.ViewState.COMPLETED,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.identity == "context"
            ],
            [expected_context],
        )
        self.assertIn(
            f"{render_plain(expected_context, width=240).text}\n",
            callbacks.records,
        )

        (
            _workspace,
            _catalog,
            metadata,
            controller,
            _backend,
            callbacks,
            store,
        ) = self.selected_controller("/history", "/quit")
        components = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_empty_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with mock.patch.object(
            terminal_module.InteractiveSemanticOutput,
            "write_component",
            autospec=True,
            side_effect=capture_empty_component,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertFalse(
            any(record.startswith("History (") for record in callbacks.records)
        )
        empty_context = terminal_module.Record(
            "context",
            fields=(
                terminal_module.Field("History displayed", "0/0 events"),
                terminal_module.Field("Complete turns", "0"),
                terminal_module.Field(
                    "Context retained",
                    "0 turns, 0 events",
                ),
                terminal_module.Field("Omitted turns", "0"),
                terminal_module.Field("UTF-8 bytes", "0/32768"),
                terminal_module.Field("Turn cap", "24"),
                terminal_module.Field("Truncated", "no."),
            ),
            state=terminal_module.ViewState.EMPTY,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.identity == "context"
            ],
            [empty_context],
        )
        self.assertIn(
            f"{render_plain(empty_context, width=240).text}\n",
            callbacks.records,
        )
        self.assertFalse(
            store.workspace.path(
                "discussion_logs/session/interactive/conversations/"
                f"{metadata.conversation_id}/transcript.jsonl"
            ).exists()
        )

    def test_status_refreshes_selected_metadata_and_uses_one_snapshot(self) -> None:
        holder: dict[str, object] = {}

        def rename_after_scope(prompt: str) -> str:
            """Rename after prompt scope so status must refresh metadata."""

            self.assertEqual(prompt, "story[session]> ")
            catalog = holder["catalog"]
            metadata = holder["metadata"]
            assert isinstance(catalog, FilesystemConversationCatalog)
            assert isinstance(metadata, ConversationMetadata)
            catalog.rename("session", metadata.conversation_id, "Revised subject")
            return "/status"

        (
            _workspace,
            catalog,
            metadata,
            controller,
            _backend,
            callbacks,
            store,
        ) = self.selected_controller(rename_after_scope, "/quit")
        holder.update(catalog=catalog, metadata=metadata)
        for turn_id, kind, content in (
            ("turn_done", ConversationEventKind.USER_MESSAGE, "question"),
            ("turn_done", ConversationEventKind.AGENT_REPLY, "reply"),
            ("turn_open", ConversationEventKind.USER_MESSAGE, "unmatched"),
        ):
            _append_visible_event(
                store,
                metadata,
                turn_id=turn_id,
                kind=kind,
                content=content,
            )
        with (
            mock.patch.object(store, "read", wraps=store.read) as read,
            mock.patch.object(store, "read_context") as read_context,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        read.assert_called_once_with("session", metadata.conversation_id)
        read_context.assert_not_called()
        rendered = "".join(callbacks.records)
        self.assertIn(
            "[conversation]\n"
            f"  Current focus: {metadata.conversation_id}\n"
            "  Subject: Revised subject\n"
            "  Status: active\n"
            "  Revision: 2.\n",
            rendered,
        )
        self.assertIn("History displayed: 3/3 events", rendered)
        self.assertIn("Context retained: 1 turns, 2 events", rendered)
        self.assertIn(
            "[result]\n  Search: available, active-only\n",
            rendered,
        )

    def test_selector_snapshot_failures_are_safe_and_non_mutating(self) -> None:
        class TranscriptTuple(tuple):
            """Represent an exact-type violation returned by the store."""

        for command in ("/history", "/status"):
            for failure_kind in ("tuple_subclass", "noncontiguous_sequence"):
                with self.subTest(command=command, failure=failure_kind):
                    continuation_state: list[tuple[object, object]] = []

                    def verify_failure_then_quit(prompt: str) -> str:
                        """Observe retained command state before normal cleanup."""

                        self.assertEqual(prompt, "story[session]> ")
                        continuation_state.append(controller._focused_conversation)
                        return "/quit"

                    (
                        workspace,
                        _catalog,
                        metadata,
                        controller,
                        backend,
                        callbacks,
                        store,
                    ) = self.selected_controller(
                        command,
                        verify_failure_then_quit,
                    )
                    sentinel = (
                        f"RAW_{command.removeprefix('/').upper()}_"
                        f"{failure_kind.upper()}_SELECTOR_SECRET"
                    )
                    if failure_kind == "tuple_subclass":
                        snapshot: object = TranscriptTuple((sentinel,))
                    else:
                        stored = store.append(
                            ConversationEventAppend(
                                session_id="session",
                                conversation_id=metadata.conversation_id,
                                turn_id="turn_invalid",
                                sender="user",
                                recipient=LEAD_ORCHESTRATOR,
                                kind=ConversationEventKind.USER_MESSAGE,
                                content="safe persisted content",
                            )
                        )
                        snapshot = (replace(stored, sequence=2, content=sentinel),)

                    durable_before = _durable_file_snapshot(workspace)
                    with (
                        mock.patch.object(
                            store,
                            "read",
                            return_value=snapshot,
                        ) as read,
                        mock.patch.multiple(
                            store,
                            read_context=mock.DEFAULT,
                            append=mock.DEFAULT,
                        ) as store_calls,
                        mock.patch.object(backend, "bind") as bind,
                        mock.patch.object(
                            controller_core_module.asyncio,
                            "run",
                        ) as async_run,
                    ):
                        self.assertEqual(controller.run_session_lobby(), 0)

                    read.assert_called_once_with(
                        "session",
                        metadata.conversation_id,
                    )
                    for collaborator in (
                        *store_calls.values(),
                        bind,
                        async_run,
                    ):
                        collaborator.assert_not_called()
                    self.assertEqual(callbacks.reads, [])
                    self.assertEqual(len(callbacks.prompts), 2)
                    self.assertEqual(
                        continuation_state,
                        [metadata],
                    )
                    # Covers catalog, selection, transcript, run, and artifacts.
                    self.assertEqual(
                        _durable_file_snapshot(workspace),
                        durable_before,
                    )
                    rendered = "".join(callbacks.records)
                    self.assertEqual(rendered.count("[error "), 1)
                    self.assertEqual(
                        rendered.count(
                            _error_record(
                                InteractiveErrorCode.TRANSCRIPT_INVALID,
                            )
                        ),
                        1,
                    )
                    self.assertNotIn(
                        _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                        rendered,
                    )
                    self.assertNotIn(sentinel, rendered)


class InteractiveInspectionTests(InteractiveConversationTestCase):
    """Verify safe persisted and process-local debate inspection."""

    def test_persisted_lifecycle_and_context_inspection_redact_raw_content(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        selected = catalog.create("session", "Inspection subject")
        save_default_conversation(workspace, "session", selected.conversation_id)
        context = ConversationContextWindow(
            events=(
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    "session",
                    selected.conversation_id,
                    "secret_turn",
                    1,
                    _TIMESTAMP,
                    "user",
                    LEAD_ORCHESTRATOR,
                    ConversationEventKind.USER_MESSAGE,
                    "RAW_CONTEXT_USER_SECRET",
                ),
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    "session",
                    selected.conversation_id,
                    "secret_turn",
                    2,
                    _TIMESTAMP,
                    LEAD_ORCHESTRATOR,
                    "user",
                    ConversationEventKind.AGENT_REPLY,
                    "RAW_CONTEXT_REPLY_SECRET",
                ),
            ),
            total_complete_turns=1,
            retained_turns=1,
            omitted_turns=0,
            retained_utf8_bytes=len(
                "RAW_CONTEXT_USER_SECRETRAW_CONTEXT_REPLY_SECRET".encode()
            ),
            truncated=False,
        )

        cases = (
            ("debate_consensus", DebateStatus.CONSENSUS, "CONSENSUS_SECRET"),
            ("debate_deadlock", DebateStatus.DEADLOCK, "DEADLOCK_SECRET"),
            ("debate_failed", DebateStatus.FAILED, "FAILED_SECRET"),
            ("debate_interrupted", "interrupted", "INTERRUPTED_SECRET"),
        )
        for debate_id, status, secret in cases:
            _persist_terminal_debate(
                workspace,
                session_id="session",
                debate_id=debate_id,
                status=status,
                secret=secret,
            )
        workspace.save_debate_context(
            "session",
            "debate_consensus",
            conversation_id=selected.conversation_id,
            mode=DebateContextMode.INCLUDED,
            snapshot=context,
        )
        commands = (
            "/inspect",
            "/inspect debate_consensus",
            "/inspect debate_deadlock",
            "/inspect debate_failed",
            "/inspect debate_interrupted",
            "/inspect debate_missing",
            "/inspect last",
            "/quit",
        )
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            *commands,
            catalog=catalog,
            runtime=_REAL_RUNTIME,
        )
        before = _durable_file_snapshot(workspace)
        with (
            mock.patch.object(
                subprocess,
                "Popen",
                wraps=subprocess.Popen,
            ) as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
            mock.patch.object(controller_core_module.asyncio, "run") as async_run,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertTrue(popen.called)
        self.assertTrue(
            all(call.args[0][0] == "git" for call in popen.call_args_list)
        )
        run.assert_not_called()
        connect.assert_not_called()
        async_run.assert_not_called()
        self.assertEqual(_durable_file_snapshot(workspace), before)
        rendered = "".join(callbacks.records)
        self.assertIn("Session session: 4 persisted debates.", rendered)
        for debate_id, status, _secret in cases:
            expected_status = (
                status.value if isinstance(status, DebateStatus) else status
            )
            self.assertIn(
                f"[run {debate_id}]"
                + (
                    " [success]"
                    if expected_status == "consensus"
                    else " [cancelled] [warning]"
                    if expected_status == "interrupted"
                    else " [error]"
                )
                + "\n  Session: session\n"
                + f"  Status: {expected_status}\n",
                rendered,
            )
        self.assertIn(
            f"[context] conversation {selected.conversation_id}\n"
            "  Mode: included\n"
            "  Events: 2\n"
            "  Complete turns: 1\n"
            "  Retained turns: 1\n"
            "  Omitted turns: 0\n",
            rendered,
        )
        self.assertIn(
            "[run debate_missing] [empty]\n  Session: session\n  Status: not_found\n",
            rendered,
        )
        for secret in (
            "CONSENSUS_SECRET",
            "DEADLOCK_SECRET",
            "FAILED_SECRET",
            "INTERRUPTED_SECRET",
            "RAW_CONTEXT_USER_SECRET",
            "RAW_CONTEXT_REPLY_SECRET",
        ):
            self.assertNotIn(secret, rendered)

    def test_malformed_inspection_and_context_fail_closed_without_echo(self) -> None:
        for case in ("debate", "context"):
            with self.subTest(case=case):
                workspace = self.make_workspace()
                self.seed_session(workspace, "session")
                save_default_session(workspace, "session")
                catalog = FilesystemConversationCatalog(workspace)
                selected = catalog.create("session", "Tamper target")
                save_default_conversation(
                    workspace,
                    "session",
                    selected.conversation_id,
                )
                _persist_terminal_debate(
                    workspace,
                    session_id="session",
                    debate_id="debate_target",
                    status=DebateStatus.FAILED,
                    secret="PERSISTED_SECRET",
                )
                controller, _backend, callbacks, _store, _factory = (
                    self.make_controller(
                        workspace,
                        "/inspect debate_target",
                        catalog=catalog,
                        runtime=_REAL_RUNTIME,
                    )
                )
                secret = f"RAW_{case.upper()}_TAMPER_SECRET"
                if case == "debate":
                    patches = (
                        mock.patch.object(
                            controller._runtime,
                            "inspect_debate",
                            return_value={
                                "session_id": "foreign",
                                "debate_id": "debate_target",
                                "status": "not_found",
                                "terminal": False,
                                "state": None,
                                "result": None,
                                "messages": [{"content": secret}],
                            },
                        ),
                    )
                else:
                    patches = (
                        mock.patch.object(
                            controller._runtime,
                            "inspect_debate_context",
                            return_value={
                                "schema_version": 1,
                                "session_id": "session",
                                "debate_id": "debate_target",
                                "conversation_id": selected.conversation_id,
                                "mode": "included",
                                "snapshot": {
                                    "events": [{"content": secret}],
                                    "total_complete_turns": 1,
                                    "retained_turns": 1,
                                    "omitted_turns": 0,
                                    "retained_utf8_bytes": 1,
                                    "truncated": False,
                                },
                            },
                        ),
                    )
                with patches[0]:
                    self.assertEqual(controller.run_session_lobby(), 2)
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                    rendered,
                )
                self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
