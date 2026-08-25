"""Real-filesystem journeys for interactive conversation search and reads."""

from __future__ import annotations

import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock
from uuid import UUID

from story_writing_agents import workspace as workspace_module
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    load_cli_context,
    save_default_session,
)
from story_writing_agents.conversation_search import (
    ConversationCitation,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
)
from story_writing_agents.conversations import ConversationEventKind
from story_writing_agents.interactive_commands import parse_interactive_line
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_terminal._render import render_plain
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)

from tests.conversation_search_contract import append_turn
from tests.helpers import core_snapshot, initialized_workspace


_SESSION = "session"
_A = "c_0123456789abcdef0123456789abcdef"
_B = "c_fedcba9876543210fedcba9876543210"
_COLLIDING_A = "c_0123456789" + ("a" * 22)
_COLLIDING_B = "c_0123456789" + ("b" * 22)


class ScriptedTerminalCallbacks:
    """Supply deterministic input while retaining exact terminal records."""

    def __init__(self, *reads: object) -> None:
        """Store scripted inputs and initialize empty observable traffic."""

        self.reads = list(reads)
        self.prompts: list[str] = []
        self.records: list[str] = []

    def read_line(self, prompt: str) -> str:
        """Record a prompt and resolve the next line, event, or callback."""

        self.prompts.append(prompt)
        if not self.reads:
            raise AssertionError(f"unexpected input request: {prompt}")
        value = self.reads.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(prompt)
        return value  # type: ignore[return-value]

    def write_text(self, text: str) -> None:
        """Retain one terminal-owned callback record without transformation."""

        self.records.append(text)


def _error_record(code: InteractiveErrorCode) -> str:
    """Return the exact default record for one typed interactive error."""

    spec = INTERACTIVE_ERROR_SPECS[code]
    component = terminal_module.Panel(
        "Interactive error",
        (
            terminal_module.Notice(
                spec.summary,
                label=f"error {code.value}",
                role=terminal_module.SemanticRole.ERROR,
                state=terminal_module.ViewState.ERROR,
            ),
            terminal_module.Notice(spec.hint, label="hint"),
            terminal_module.Notice(
                "If available, workspace-relative: debug_logs/runtime.jsonl",
                label="debug log",
            ),
        ),
        state=terminal_module.ViewState.ERROR,
    )
    return f"{render_plain(component, width=240).text}\n"


def _persistent_file_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Capture domain file state without leases or diagnostic artifacts."""

    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.name.endswith(".access.json")
        and "debug_logs" not in path.relative_to(root).parts
    }


class InteractiveSearchFilesystemTests(unittest.TestCase):
    """Exercise controller/service integration through real workspace adapters."""

    def make_workspace(self) -> Workspace:
        """Create one initialized workspace owned by this test case."""

        return initialized_workspace(self)

    def seed_default_ready_session(self, workspace: Workspace) -> None:
        """Persist one default ready session without retaining process access."""

        backend = AgentBackend(workspace, executable=sys.executable)
        try:
            backend.create_session_with_access(_SESSION)
        finally:
            backend.close()
        save_default_session(workspace, _SESSION)

    def make_controller(
        self,
        workspace: Workspace,
        catalog: FilesystemConversationCatalog,
        *reads: object,
    ) -> tuple[
        InteractiveController,
        AgentBackend,
        ScriptedTerminalCallbacks,
    ]:
        """Bind a real controller to one catalog and scripted terminal."""

        backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(backend.close)
        callbacks = ScriptedTerminalCallbacks(*reads)
        terminal = InteractiveTerminal(
            callbacks.read_line,
            callbacks.write_text,
            width=240,
        )
        controller = InteractiveController(
            workspace,
            load_cli_context(workspace),
            backend,
            terminal,
            conversation_catalog=catalog,
        )
        return controller, backend, callbacks

    def test_search_and_transcript_journey_is_provider_free_and_read_only(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_default_ready_session(workspace)
        catalog = FilesystemConversationCatalog(workspace)
        identifiers = (
            UUID(hex=_A[2:]),
            UUID(hex=_B[2:]),
            UUID(hex="a" * 32),
        )
        with mock.patch.object(
            workspace_module,
            "uuid4",
            side_effect=identifiers,
        ):
            exact = catalog.create(_SESSION, "Council Vessel")
            transcript = catalog.create(_SESSION, "Harbor timing")
            archived = catalog.create(_SESSION, "Archived council vessel")
        self.assertEqual(exact.conversation_id, _A)
        self.assertEqual(transcript.conversation_id, _B)
        store = FilesystemConversationEventStore(workspace, catalog=catalog)
        append_turn(
            store,
            session_id=_SESSION,
            conversation_id=transcript.conversation_id,
            turn_id="turn_search",
            user_content="The council vessel enters at dusk.",
            outcome_content="Observed from the harbor.",
        )
        catalog.archive(_SESSION, archived.conversation_id)
        controller, backend, callbacks = self.make_controller(
            workspace,
            catalog,
            "/search council vessel",
            f"/transcript {transcript.conversation_id} 1",
            f"/transcript {transcript.conversation_id[:12]} 1 --from 2",
            f"/transcript {archived.conversation_id}",
            f"/transcript {exact.conversation_id} 101",
            "/quit",
        )
        before = _persistent_file_snapshot(workspace.root)
        core_before = core_snapshot(workspace)

        with (
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
            mock.patch.object(controller_core_module.asyncio, "run") as async_run,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                controller_core_module,
                "save_default_session",
                wraps=save_default_session,
            ) as persist_session,
            mock.patch.object(
                controller_core_module,
                "save_default_conversation",
            ) as persist_conversation,
            mock.patch.object(
                controller._runtime,
                "converse",
                wraps=controller._runtime.converse,
            ) as converse,
            mock.patch.object(
                controller._conversation_event_store,
                "append",
                wraps=controller._conversation_event_store.append,
            ) as append,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        async_run.assert_not_called()
        bind.assert_not_called()
        persist_session.assert_not_called()
        persist_conversation.assert_not_called()
        converse.assert_not_called()
        append.assert_not_called()
        self.assertEqual(_persistent_file_snapshot(workspace.root), before)
        self.assertEqual(core_snapshot(workspace), core_before)
        self.assertFalse(
            workspace.path(
                "agent_states/orchestrator_state/conversation_defaults/"
                f"{_SESSION}.json"
            ).exists()
        )
        self.assertEqual(
            [path.name for path in workspace.path("manuscript").iterdir()],
            [".gitkeep"],
        )

        rendered = "".join(callbacks.records)
        self.assertIn(
            "[context] Search request [completed]\n"
            "  Query: council vessel\n"
            "  Scope: active\n",
            rendered,
        )
        self.assertIn(
            "[context] Transcript request [completed]\n"
            f"  Conversation: {transcript.conversation_id[:12]}\n"
            "  Start: 2\n"
            "  Count: 1\n",
            rendered,
        )
        self.assertIn(
            "[context] Transcript request [completed]\n"
            f"  Conversation: {archived.conversation_id[:12]}\n"
            "  Start: 1\n"
            "  Count: 24\n",
            rendered,
        )
        exact_row = (
            "Search results (1/2) [completed]\n"
            f"  [search {exact.conversation_id[:12]}] [completed]\n"
            "    Status: active\n"
            "    Match: exact_subject\n"
            "    Subject: Council Vessel\n"
            f"    Citation: conversation {exact.conversation_id[:12]}\n"
            "    Snippet: council vessel\n"
        )
        transcript_row = (
            "Search results (2/2) [completed]\n"
            f"  [search {transcript.conversation_id[:12]}] [completed]\n"
            "    Status: active\n"
            "    Match: transcript\n"
            "    Subject: Harbor timing\n"
            f"    Citation: conversation {transcript.conversation_id[:12]}, "
            "turn turn_search, sequence 1, kind user_message\n"
            "    Snippet: the council vessel enters at dusk.\n"
        )
        self.assertIn(exact_row, rendered)
        self.assertIn(transcript_row, rendered)
        self.assertLess(rendered.index(exact_row), rendered.index(transcript_row))
        self.assertNotIn("Archived council vessel", rendered)
        self.assertIn(
            "[result] Search returned 2 of 2 matching conversations.\n",
            rendered,
        )
        self.assertIn(
            f"[hint] Continue with /transcript "
            f"{transcript.conversation_id[:12]} 1 --from 2.\n",
            rendered,
        )
        self.assertIn("[result] Transcript page returned 0 events.\n", rendered)
        self.assertIn(_error_record(InteractiveErrorCode.TRANSCRIPT_INVALID), rendered)
        self.assertNotIn("[error conversation_required]", rendered)

    def test_search_rejects_tampered_partial_event_citation_before_output(
        self,
    ) -> None:
        workspace = self.make_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        with mock.patch.object(
            workspace_module,
            "uuid4",
            return_value=UUID(hex=_A[2:]),
        ):
            metadata = catalog.create(_SESSION, "Council Vessel")
        query = ConversationSearchQuery("council")
        citation = ConversationCitation(
            _SESSION,
            metadata.conversation_id,
            "turn_1",
            1,
            ConversationEventKind.USER_MESSAGE,
        )
        hit = ConversationSearchHit(
            citation,
            metadata.subject,
            metadata.subject_revision,
            metadata.status,
            metadata.updated_at,
            ConversationSearchMatchKind.TRANSCRIPT,
            "council vessel",
        )
        result = ConversationSearchResult(
            query,
            "council",
            (hit,),
            1,
            1,
            1,
            False,
            False,
        )
        self.assertIs(type(result), ConversationSearchResult)
        self.assertIs(type(result.hits[0]), ConversationSearchHit)
        self.assertIs(type(result.hits[0].citation), ConversationCitation)
        object.__setattr__(citation, "sequence", None)

        controller, _backend, callbacks = self.make_controller(
            workspace,
            catalog,
        )
        memory = mock.Mock()
        memory.search.return_value = result
        controller._attached_session_id = _SESSION
        controller._conversation_memory_service = memory

        with self.assertRaisesRegex(
            ValueError,
            "^citation turn ID, sequence, and kind must be present together$",
        ):
            controller._search_attached_conversations(
                parse_interactive_line("/search council"),
            )

        memory.search.assert_called_once_with(query)
        accepted_context = terminal_module.Record(
            "context",
            title="Search request",
            fields=(
                terminal_module.Field("Query", "council"),
                terminal_module.Field("Scope", "active"),
            ),
            state=terminal_module.ViewState.COMPLETED,
        )
        self.assertEqual(
            callbacks.records,
            [f"{render_plain(accepted_context, width=240).text}\n"],
        )

    def test_resolution_is_case_sensitive_and_includes_archived_collisions(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_default_ready_session(workspace)
        catalog = FilesystemConversationCatalog(workspace)
        identifiers = (
            UUID(hex=_COLLIDING_A[2:]),
            UUID(hex=_COLLIDING_B[2:]),
        )
        with mock.patch.object(
            workspace_module,
            "uuid4",
            side_effect=identifiers,
        ):
            active = catalog.create(_SESSION, "Active collision")
            archived = catalog.create(_SESSION, "Archived collision")
        catalog.archive(_SESSION, archived.conversation_id)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            catalog,
            f"/transcript {active.conversation_id}",
            f"/transcript {active.conversation_id[:12]}",
            f"/transcript {active.conversation_id[:11]}",
            f"/transcript {active.conversation_id.upper()}",
            f"/transcript {archived.conversation_id}",
            "/quit",
        )

        with mock.patch.object(
            controller._conversation_event_store,
            "read",
            wraps=controller._conversation_event_store.read,
        ) as read:
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            read.call_args_list,
            [
                mock.call(_SESSION, active.conversation_id),
                mock.call(_SESSION, archived.conversation_id),
            ],
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count("[result] Transcript page returned 0 events.\n"),
            2,
        )
        self.assertEqual(
            rendered.count("[error conversation_ambiguous]"),
            1,
        )
        self.assertEqual(
            rendered.count("[error conversation_invalid]"),
            1,
        )
        self.assertEqual(
            rendered.count("[error conversation_not_found]"),
            1,
        )
        self.assertNotIn("[error conversation_archived]", rendered)

    def test_each_command_reads_a_fresh_snapshot_and_renders_whitespace_content(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_default_ready_session(workspace)
        catalog = FilesystemConversationCatalog(workspace)
        with mock.patch.object(
            workspace_module,
            "uuid4",
            return_value=UUID(hex=_A[2:]),
        ):
            metadata = catalog.create(_SESSION, "Fresh snapshot")
        store = FilesystemConversationEventStore(workspace, catalog=catalog)
        append_turn(
            store,
            session_id=_SESSION,
            conversation_id=metadata.conversation_id,
            turn_id="turn_1",
            user_content="first question",
            outcome_content="first reply",
        )

        def append_new_turn_then_continue(prompt: str) -> str:
            """Append a turn between commands and request its fresh range."""

            self.assertEqual(prompt, f"story[{_SESSION}:lead]> ")
            append_turn(
                store,
                session_id=_SESSION,
                conversation_id=metadata.conversation_id,
                turn_id="turn_2",
                user_content=" \t",
                outcome_content="new reply",
            )
            return f"/transcript {metadata.conversation_id[:12]} 2 --from 3"

        controller, _backend, callbacks = self.make_controller(
            workspace,
            catalog,
            f"/transcript {metadata.conversation_id} 2",
            append_new_turn_then_continue,
            "/quit",
        )
        with mock.patch.object(
            controller._conversation_event_store,
            "read",
            wraps=controller._conversation_event_store.read,
        ) as read:
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(read.call_count, 2)
        events = store.read(_SESSION, metadata.conversation_id)
        self.assertEqual(tuple(event.sequence for event in events), (1, 2, 3, 4))
        rendered = "".join(callbacks.records)
        handle = metadata.conversation_id[:12]
        self.assertIn(
            "Transcript (1/2) [completed]\n"
            f"  [transcript {handle}] [completed]\n"
            "    Sequence: 3\n"
            "    Turn: turn_2\n"
            "    Kind: user_message\n"
            "    Sender: user\n"
            "    Recipient: plot_architect\n"
            "    Content:\n"
            "     \\x09\n",
            rendered,
        )
        self.assertIn(
            "Transcript (2/2) [completed]\n"
            f"  [transcript {handle}] [completed]\n"
            "    Sequence: 4\n"
            "    Turn: turn_2\n"
            "    Kind: agent_reply\n"
            "    Sender: plot_architect\n"
            "    Recipient: user\n"
            "    Content:\n"
            "    new reply\n",
            rendered,
        )
        self.assertEqual(
            rendered.count("[result] Transcript page returned 2 events.\n"),
            2,
        )
        self.assertNotIn("[error transcript_invalid]", rendered)


if __name__ == "__main__":
    unittest.main()
