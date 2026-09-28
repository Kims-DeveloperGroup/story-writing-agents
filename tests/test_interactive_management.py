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








if __name__ == "__main__":
    unittest.main()
