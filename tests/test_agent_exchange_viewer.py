"""Pure projection and lazy optional agent-exchange viewer contracts."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents.agent_exchanges import (
    AgentExchangeFailure,
    AgentExchangeInventoryItem,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
    AgentExchangeSelection,
    AgentExchangeStorageError,
    WorkspaceAgentExchangeStore,
)
from story_writing_agents.agents import AGENT_SPECS, ROSTER
from story_writing_agents.interactive_controller import (
    _exchange_policy as policy_module,
)
from story_writing_agents.interactive_controller import (
    _exchange_viewer as viewer_module,
)
from story_writing_agents.interactive_controller._exchange_policy import (
    AgentExchangeProjectionError,
    DEBATE_SUBJECT_UNAVAILABLE,
    ExchangeAgentChoice,
    ExchangeScopeChoice,
    debate_subject_from_messages,
    project_exchange_agents,
    project_exchange_scopes,
)
from story_writing_agents.interactive_controller._exchange_viewer import (
    AgentExchangeViewerError,
    prepare_agent_exchange_view,
)
from story_writing_agents.models import DebateMessage
from tests.test_interactive_controller import ControllerTestCase
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_terminal._external_view import (
    ExternalViewUnavailableError, ExternalTerminalIO,
)
from story_writing_agents.interactive_controller import _core as controller_core_module


ROOT = Path(__file__).resolve().parents[1]
VIEWER_SOURCE = ROOT / "vendor" / "jsonl-viewer" / "src"
VIEWER_ERROR_MESSAGES = {
    "installation_required": (
        "Required agent exchange viewer is unavailable. Repair or reinstall "
        "Story and retry /exchanges."
    ),
    "incompatible": (
        "Required agent exchange viewer is incompatible with Story. Repair or "
        "reinstall Story and retry /exchanges."
    ),
    "view_failed": "Agent exchange viewer failed.",
}


def _selection(
    scope_kind: AgentExchangeScopeKind,
    scope_id: str,
    agent_id: str,
    *,
    session_id: str = "session_1",
) -> AgentExchangeSelection:
    return AgentExchangeSelection(session_id, scope_kind, scope_id, agent_id)


def _inventory(
    scope_kind: AgentExchangeScopeKind,
    scope_id: str,
    agent_id: str,
    *,
    first: int,
    last: int,
    count: int = 1,
    session_id: str = "session_1",
) -> AgentExchangeInventoryItem:
    return AgentExchangeInventoryItem(
        _selection(scope_kind, scope_id, agent_id, session_id=session_id),
        count,
        first,
        last,
        1,
        count,
    )


def _message(content: str, **changes: object) -> DebateMessage:
    values: dict[str, object] = {
        "sender": "user",
        "phase": "initiation",
        "round": 0,
        "revision": "r0",
        "kind": "prompt",
        "content": content,
        "timestamp": "2026-08-30T00:00:00+00:00",
    }
    values.update(changes)
    return DebateMessage(**values)  # type: ignore[arg-type]


class _ExactViewerSpec:
    def __init__(
        self,
        session_id,
        conversation_id,
        agent_id,
        date_time_field="timestamp",
        request_type_field="request_type",
        content_field="content",
        title="JSONL Viewer",
        conversation_label="Conversation",
        conversation_subject=None,
        input_protocol="semantic",
    ):
        self.session_id, self.conversation_id, self.agent_id = session_id, conversation_id, agent_id
        self.date_time_field, self.request_type_field, self.content_field = date_time_field, request_type_field, content_field
        self.title, self.conversation_label, self.conversation_subject = title, conversation_label, conversation_subject
        self.input_protocol = input_protocol

    @property
    def primary_fields(self):
        return (
            self.date_time_field,
            self.request_type_field,
            self.content_field,
        )


class _ExactViewerHost:
    def terminal_size(self):
        raise NotImplementedError

    def color_enabled(self):
        raise NotImplementedError

    def present(self, frame):
        raise NotImplementedError

    def read_event(self):
        raise NotImplementedError

    def close_view(self):
        raise NotImplementedError


class _ExactViewerTerminal:
    def __init__(self, input_stream, output_stream, read_character=None, no_color=False):
        self.input_stream, self.output_stream = input_stream, output_stream
        self.read_character, self.no_color = read_character, no_color


def _transport():
    return ExternalTerminalIO(io.StringIO(), io.StringIO(), lambda before_read: "", True)


def _viewer_module(view_jsonl=mock.DEFAULT) -> types.ModuleType:
    module = types.ModuleType("jsonl_viewer")
    module.__all__ = ["ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl"]
    module.ViewerHost = _ExactViewerHost
    module.ViewerSpec = _ExactViewerSpec
    module.ViewerTerminal = _ExactViewerTerminal

    if view_jsonl is mock.DEFAULT:

        def view_jsonl(source, spec, host=None, *, terminal=None, transient_only=False):
            return None

    module.view_jsonl = view_jsonl
    return module


class ExchangeProjectionTests(unittest.TestCase):
    def test_debate_subject_uses_only_exact_first_initiation_and_bounds_preview(
        self,
    ) -> None:
        self.assertEqual(
            debate_subject_from_messages((_message("  Alpha\n\tBeta  "),)),
            "Alpha Beta",
        )
        long = "x" * 520
        self.assertEqual(
            debate_subject_from_messages((_message(long, kind="draft"),)),
            "x" * 511 + "…",
        )
        self.assertEqual(len(debate_subject_from_messages((_message(long),))), 512)
        invalid_first = (
            (),
            [],
            (_message("later", sender="plot_architect"),),
            (_message("later", phase="draft"),),
            (_message("later", round=1),),
            (_message("later", revision="r1"),),
            (_message("later", kind="critique"),),
            (_message(" \n\t "),),
            (object(),),
            (_message("wrong", sender="plot_architect"), _message("right")),
        )
        for messages in invalid_first:
            with self.subTest(messages=messages):
                self.assertEqual(
                    debate_subject_from_messages(messages),
                    DEBATE_SUBJECT_UNAVAILABLE,
                )

    def test_scopes_are_inventory_only_deduplicated_latest_first_and_kind_exact(
        self,
    ) -> None:
        conversation_id = "same_full_scope_id"
        inventory = (
            _inventory(
                AgentExchangeScopeKind.CONVERSATION,
                conversation_id,
                ROSTER[0],
                first=1,
                last=4,
                count=2,
            ),
            _inventory(
                AgentExchangeScopeKind.DEBATE,
                conversation_id,
                ROSTER[1],
                first=2,
                last=6,
            ),
            _inventory(
                AgentExchangeScopeKind.CONVERSATION,
                conversation_id,
                ROSTER[2],
                first=3,
                last=8,
                count=3,
            ),
            _inventory(
                AgentExchangeScopeKind.CONVERSATION,
                "archived_conversation_full_id",
                ROSTER[0],
                first=5,
                last=7,
            ),
        )
        choices = project_exchange_scopes(
            "session_1",
            inventory,
            conversation_subjects={
                conversation_id: "Current subject",
                "archived_conversation_full_id": "Archived subject",
                "not_in_inventory": "Must not appear",
            },
            debate_messages={conversation_id: (_message("Debate subject"),)},
        )
        self.assertEqual(
            choices,
            (
                ExchangeScopeChoice(
                    AgentExchangeScopeKind.CONVERSATION,
                    conversation_id,
                    "Current subject",
                    5,
                    8,
                ),
                ExchangeScopeChoice(
                    AgentExchangeScopeKind.CONVERSATION,
                    "archived_conversation_full_id",
                    "Archived subject",
                    1,
                    7,
                ),
                ExchangeScopeChoice(
                    AgentExchangeScopeKind.DEBATE,
                    conversation_id,
                    "Debate subject",
                    1,
                    6,
                ),
            ),
        )
        self.assertEqual(
            [choice.identity for choice in choices],
            [
                f"conversation:{conversation_id}",
                "conversation:archived_conversation_full_id",
                f"debate:{conversation_id}",
            ],
        )
        self.assertEqual(
            choices[0].label,
            f"Conversation {conversation_id} — Current subject",
        )
        self.assertNotIn("not_in_inventory", repr(choices))

    def test_scopes_and_agents_fail_closed_on_cross_session_or_duplicates(self) -> None:
        item = _inventory(
            AgentExchangeScopeKind.CONVERSATION,
            "conversation_1",
            ROSTER[0],
            first=1,
            last=1,
        )
        crossed = _inventory(
            AgentExchangeScopeKind.CONVERSATION,
            "conversation_1",
            ROSTER[1],
            first=2,
            last=2,
            session_id="session_2",
        )
        with self.assertRaises(AgentExchangeProjectionError):
            project_exchange_scopes(
                "session_1",
                (crossed,),
                conversation_subjects={"conversation_1": "Subject"},
                debate_messages={},
            )
        with self.assertRaises(AgentExchangeProjectionError):
            project_exchange_scopes(
                "session_1",
                (item, item),
                conversation_subjects={"conversation_1": "Subject"},
                debate_messages={},
            )
        with self.assertRaises(AgentExchangeProjectionError):
            project_exchange_scopes(
                "session_1",
                (item,),
                conversation_subjects={},
                debate_messages={},
            )
        with self.assertRaises(AgentExchangeProjectionError):
            project_exchange_agents(
                "session_1",
                AgentExchangeScopeKind.DEBATE,
                "missing",
                (item,),
            )

    def test_agent_projection_always_contains_exact_roster_and_empty_streams(
        self,
    ) -> None:
        item = _inventory(
            AgentExchangeScopeKind.DEBATE,
            "debate_1",
            ROSTER[2],
            first=7,
            last=9,
            count=3,
        )
        choices = project_exchange_agents(
            "session_1",
            AgentExchangeScopeKind.DEBATE,
            "debate_1",
            (item,),
        )
        self.assertEqual(tuple(choice.agent_id for choice in choices), ROSTER)
        self.assertEqual(
            tuple(choice.display_name for choice in choices),
            tuple(AGENT_SPECS[agent_id].display_name for agent_id in ROSTER),
        )
        for choice in choices:
            if choice.agent_id == ROSTER[2]:
                self.assertEqual(choice.selection, item.selection)
                self.assertEqual(
                    (choice.record_count, choice.last_workspace_sequence), (3, 9)
                )
                self.assertTrue(choice.has_records)
            else:
                self.assertEqual(
                    choice,
                    ExchangeAgentChoice(
                        choice.agent_id,
                        choice.display_name,
                        None,
                        0,
                        None,
                    ),
                )
                self.assertFalse(choice.has_records)

    def test_policy_surface_is_internal_exact_and_dependency_direction_is_closed(
        self,
    ) -> None:
        self.assertEqual(
            tuple(policy_module.__all__),
            (
                "AgentExchangeProjectionError",
                "DEBATE_SUBJECT_UNAVAILABLE",
                "ExchangeAgentChoice",
                "ExchangeScopeChoice",
                "debate_subject_from_messages",
                "project_exchange_agents",
                "project_exchange_scopes",
            ),
        )
        source = Path(policy_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("jsonl_viewer", source)
        self.assertNotIn("interactive_terminal", source)


class LazyViewerAdapterTests(unittest.TestCase):
    def test_exact_full_text_spec_bytes_and_single_invocation(
        self,
    ) -> None:
        calls: list[tuple[object, object, object]] = []

        def view_jsonl(source, spec, host=None, *, terminal=None, transient_only=False):
            self.assertIsNone(host); self.assertIs(transient_only, True)
            calls.append((source, spec, terminal))

        module = _viewer_module(view_jsonl)
        source = b'{"content":"PRIVATE","provider":"codex"}\n'
        with mock.patch.object(
            viewer_module.importlib, "import_module", return_value=module
        ) as imported:
            operation = prepare_agent_exchange_view(
                source,
                session_id="session_1",
                scope_kind=AgentExchangeScopeKind.DEBATE,
                scope_id="debate_full_id",
                scope_subject="Subject",
                agent_id=ROSTER[2],
            )
        imported.assert_called_once_with("jsonl_viewer")
        self.assertEqual(calls, [])
        host = _transport()
        operation(host)
        self.assertEqual(len(calls), 1)
        observed_source, spec, observed_host = calls[0]
        self.assertIs(observed_source, source)
        self.assertIs(type(observed_source), bytes)
        self.assertIs(observed_host.input_stream, host.input_stream)
        self.assertIs(observed_host.output_stream, host.output_stream)
        self.assertIs(observed_host.read_character, host.read_character)
        self.assertIs(observed_host.no_color, host.no_color)
        self.assertEqual(
            (
                spec.session_id,
                spec.conversation_id,
                spec.agent_id,
                spec.primary_fields,
                spec.title,
                spec.conversation_label,
                spec.conversation_subject,
                spec.input_protocol,
            ),
            (
                "session_1",
                "debate_full_id",
                ROSTER[2],
                ("timestamp", "request_type", "content"),
                "Agent Exchanges",
                "Debate",
                "Subject",
                "keys",
            ),
        )
        self.assertFalse(hasattr(spec, "searchable_fields"))
        self.assertEqual(source, b'{"content":"PRIVATE","provider":"codex"}\n')

    def test_missing_nested_import_and_bad_public_surface_are_fixed_preterminal_failures(
        self,
    ) -> None:
        missing = ModuleNotFoundError("missing", name="jsonl_viewer")
        nested = ModuleNotFoundError("nested", name="jsonl_viewer.engine")
        bad_modules = (
            types.SimpleNamespace(__all__=("ViewerHost", "ViewerSpec", "view_jsonl")),
            types.SimpleNamespace(
                __all__=["ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl"],
                ViewerHost=_ExactViewerHost,
                ViewerSpec=object,
                view_jsonl=lambda source, spec, host: None,
            ),
        )
        cases: tuple[tuple[object, str], ...] = (
            (missing, "installation_required"),
            (nested, "incompatible"),
            (ImportError("PRIVATE external import"), "incompatible"),
            *((module, "incompatible") for module in bad_modules),
        )
        for imported, expected_code in cases:
            with self.subTest(imported=imported):
                side_effect = imported if isinstance(imported, BaseException) else None
                return_value = imported if side_effect is None else mock.DEFAULT
                with (
                    mock.patch.object(
                        viewer_module.importlib,
                        "import_module",
                        side_effect=side_effect,
                        return_value=return_value,
                    ),
                    self.assertRaises(AgentExchangeViewerError) as raised,
                ):
                    prepare_agent_exchange_view(
                        b"",
                        session_id="session_1",
                        scope_kind=AgentExchangeScopeKind.CONVERSATION,
                        scope_id="conversation_1",
                        scope_subject="Subject",
                        agent_id=ROSTER[0],
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(
                    str(raised.exception),
                    VIEWER_ERROR_MESSAGES[expected_code],
                )
                self.assertLessEqual(len(str(raised.exception).encode("utf-8")), 160)
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertNotIn("[viewer]", str(raised.exception))
                self.assertNotIn("optional", str(raised.exception).lower())
                self.assertFalse(
                    any(ord(character) < 32 for character in str(raised.exception))
                )

    def test_every_repaired_api_shape_mismatch_fails_before_operation_creation(
        self,
    ) -> None:
        def exact_view_jsonl(source, spec, host=None, *, terminal=None, transient_only=False):
            raise AssertionError("view must not run during preparation")

        class Legacy02Spec(_ExactViewerSpec):
            def __init__(
                self, session_id, conversation_id, agent_id, searchable_fields,
                date_time_field="timestamp", request_type_field="request_type",
                content_field="content", title="JSONL Viewer",
                conversation_label="Conversation", conversation_subject=None,
                input_protocol="semantic",
            ):
                raise AssertionError("the legacy constructor must be rejected before invocation")

        class BadProjection(_ExactViewerSpec):
            @property
            def primary_fields(self):
                return ("content", "timestamp", "request_type")

        class BadTerminal(_ExactViewerTerminal):
            def __init__(self, input_stream, output_stream, read_character=None, no_color=False, extra=None):
                raise NotImplementedError

        def bad_view_jsonl(source, spec, host=None, terminal=None, transient_only=False):
            raise AssertionError("bad view must not run")

        variants = []
        for name, value in (
            ("ViewerSpec", Legacy02Spec),
            ("ViewerSpec", BadProjection),
            ("ViewerTerminal", BadTerminal),
            ("view_jsonl", bad_view_jsonl),
            ("view_jsonl", object()),
        ):
            module = _viewer_module(exact_view_jsonl)
            setattr(module, name, value)
            variants.append(module)
        for module in variants:
            with (
                self.subTest(module=module),
                mock.patch.object(
                    viewer_module.importlib,
                    "import_module",
                    return_value=module,
                ),
                self.assertRaises(AgentExchangeViewerError) as raised,
            ):
                prepare_agent_exchange_view(
                    b"",
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Subject",
                    agent_id=ROSTER[0],
                )
            self.assertEqual(raised.exception.code, "incompatible")
            self.assertEqual(
                str(raised.exception),
                VIEWER_ERROR_MESSAGES["incompatible"],
            )

    def test_invalid_source_and_runtime_view_failure_preserve_fixed_boundary(
        self,
    ) -> None:
        module = _viewer_module()
        with mock.patch.object(
            viewer_module.importlib, "import_module", return_value=module
        ):
            with self.assertRaisesRegex(TypeError, "immutable bytes"):
                prepare_agent_exchange_view(  # type: ignore[arg-type]
                    bytearray(b"mutable"),
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Subject",
                    agent_id=ROSTER[0],
                )

        private = RuntimeError("PRIVATE provider response /tmp/private")

        def failing_view_jsonl(source, spec, host=None, *, terminal=None, transient_only=False):
            raise private

        with mock.patch.object(
            viewer_module.importlib,
            "import_module",
            return_value=_viewer_module(failing_view_jsonl),
        ):
            operation = prepare_agent_exchange_view(
                b"PRIVATE bytes\n",
                session_id="session_1",
                scope_kind=AgentExchangeScopeKind.CONVERSATION,
                scope_id="conversation_1",
                scope_subject="Subject",
                agent_id=ROSTER[0],
            )
        with self.assertRaises(AgentExchangeViewerError) as raised:
            operation(_transport())
        self.assertEqual(raised.exception.code, "view_failed")
        self.assertEqual(str(raised.exception), VIEWER_ERROR_MESSAGES["view_failed"])
        self.assertIs(raised.exception.__cause__, private)

    @unittest.skipUnless(VIEWER_SOURCE.is_dir(), "pinned jsonl-viewer is unavailable")
    def test_pinned_real_viewer_hidden_field_search_promotes_verbose_and_is_transient(self) -> None:
        script = textwrap.dedent("""
            import io, inspect, json, jsonl_viewer
            from types import SimpleNamespace
            from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
            from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
            from story_writing_agents.interactive_terminal._external_view import ExternalTerminalIO
            source = (json.dumps({"content": "body", "provider": "codex codex"}) + "\\n").encode(); original = bytes(source)
            frames, closed = [], []; events = iter(("search\\tCODEX", "next_match", "previous_match", "clear_search", "close"))
            borrowed = SimpleNamespace(terminal_size=lambda: (240, 28), color_enabled=lambda: False, present=frames.append, read_event=lambda: next(events, None), close_view=lambda: closed.append(True))
            actual = jsonl_viewer.view_jsonl
            assert tuple(inspect.signature(actual).parameters) == ("source", "spec", "host", "terminal", "transient_only")
            def owned(source, spec, host=None, *, terminal=None, transient_only=False):
                assert host is None and transient_only is True and isinstance(terminal, jsonl_viewer.ViewerTerminal)
                actual(source, spec, borrowed)
            jsonl_viewer.view_jsonl = owned
            operation = prepare_agent_exchange_view(source, session_id="session_1", scope_kind=AgentExchangeScopeKind.DEBATE, scope_id="debate_1", scope_subject="Subject", agent_id="character_custodian")
            transport = ExternalTerminalIO(io.StringIO(), io.StringIO(), lambda before_read: "", True); operation(transport)
            searched = [frame for frame in frames if "Search all text='CODEX'" in frame]
            assert closed == [True] and "SIMPLE" in frames[0] and all("VERBOSE" in frame for frame in searched)
            assert ["1/2" in frame for frame in searched] == [True, False, True] and "Search all text=" not in frames[-1]
            frames.clear(); events = iter(("close",)); operation(transport)
            assert "SIMPLE" in frames[0] and "Search all text=" not in frames[0] and source == original
        """)
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(VIEWER_SOURCE))))
        completed = subprocess.run([sys.executable, "-W", "error", "-c", script], cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")

    def test_adapter_surface_is_internal_and_only_runtime_import_is_lazy(self) -> None:
        self.assertEqual(
            viewer_module.__all__,
            ["AgentExchangeViewerError", "prepare_agent_exchange_view"],
        )
        source = Path(viewer_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import jsonl_viewer", source)
        self.assertIn('import_module("jsonl_viewer")', source)


class AgentExchangeControllerIntegrationTests(ControllerTestCase):
    def _append(
        self,
        workspace,
        *,
        scope_kind: AgentExchangeScopeKind,
        scope_id: str,
        agent_id: str = ROSTER[0],
        content: str = "PRIVATE provider response",
        exchange_id: str = "exchange_1",
    ) -> bytes:
        store = WorkspaceAgentExchangeStore(workspace)
        store.append(
            AgentExchangeRecordInput(
                session_id="session_1",
                scope_kind=scope_kind,
                scope_id=scope_id,
                agent_id=agent_id,
                provider=AgentExchangeProvider.CODEX,
                exchange_id=exchange_id,
                request_type=AgentExchangeRequestType.REQUEST,
                content=content,
            )
        )
        return store.snapshot(
            AgentExchangeSelection(
                "session_1",
                scope_kind,
                scope_id,
                agent_id,
            )
        ).jsonl_bytes

    @staticmethod
    def _commit(value: str) -> terminal_module.ControlOutcome:
        return terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text=value,
        )

    def test_attached_command_uses_authoritative_archived_scope_and_exact_snapshot(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        conversation = controller._conversation_catalog.create(
            "session_1",
            "Archived subject",
        )
        controller._conversation_catalog.archive(
            "session_1",
            conversation.conversation_id,
        )
        expected_source = self._append(
            workspace,
            scope_kind=AgentExchangeScopeKind.CONVERSATION,
            scope_id=conversation.conversation_id,
        )
        controller._attached_session_id = "session_1"
        controller._focused_conversation = conversation
        original_focus = controller._focused_conversation
        original_context = controller._context
        source_path = workspace.path(
            f"debug_logs/agent_exchanges/session_1/{ROSTER[0]}.jsonl"
        )
        before_bytes = source_path.read_bytes()
        prepared: list[dict[str, object]] = []
        operation = mock.Mock(name="operation")

        def prepare(source: bytes, **keywords: object):
            prepared.append({"source": source, **keywords})
            return operation

        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(
                    self._commit(f"conversation:{conversation.conversation_id}"),
                    self._commit(ROSTER[0]),
                ),
            ) as picker,
            mock.patch.object(
                controller_core_module,
                "prepare_agent_exchange_view",
                side_effect=prepare,
            ) as prepare_view,
            mock.patch.object(
                terminal_module.InteractiveExternalViewer,
                "open_owned",
                autospec=True,
            ) as open_view,
        ):
            controller._view_attached_agent_exchanges()

        prepare_view.assert_called_once()
        open_view.assert_called_once_with(
            controller._external_viewer, operation
        )
        self.assertEqual(len(picker.call_args_list), 2)
        scope_prompt, scope_choices = picker.call_args_list[0].args[1:3]
        agent_prompt, agent_choices = picker.call_args_list[1].args[1:3]
        self.assertEqual(scope_prompt.name, "View agent exchanges")
        self.assertEqual(agent_prompt.name, "View agent exchanges")
        self.assertTrue(picker.call_args_list[0].kwargs["transient"])
        self.assertTrue(picker.call_args_list[1].kwargs["transient"])
        self.assertEqual(
            tuple(choice.label for choice in scope_choices),
            (f"Conversation {conversation.conversation_id} — Archived subject",),
        )
        self.assertEqual(tuple(choice.identity for choice in agent_choices), ROSTER)
        self.assertEqual(agent_choices[0].description, "1 recorded exchange")
        self.assertTrue(
            all(
                choice.description == "No recorded exchanges"
                for choice in agent_choices[1:]
            )
        )
        self.assertEqual(
            prepared,
            [
                {
                    "source": expected_source,
                    "session_id": "session_1",
                    "scope_kind": AgentExchangeScopeKind.CONVERSATION,
                    "scope_id": conversation.conversation_id,
                    "scope_subject": "Archived subject",
                    "agent_id": ROSTER[0],
                }
            ],
        )
        self.assertIs(type(prepared[0]["source"]), bytes)
        self.assertEqual(source_path.read_bytes(), before_bytes)
        self.assertIs(controller._focused_conversation, original_focus)
        self.assertIs(controller._context, original_context)
        self.assertEqual(callbacks.records, [])

    def test_attached_command_reports_unavailable_transient_display_without_content(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        conversation = controller._conversation_catalog.create(
            "session_1", "Subject"
        )
        self._append(
            workspace,
            scope_kind=AgentExchangeScopeKind.CONVERSATION,
            scope_id=conversation.conversation_id,
            content="PRIVATE_VIEWER_CONTENT",
        )
        controller._attached_session_id = "session_1"
        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(
                    self._commit(f"conversation:{conversation.conversation_id}"),
                    self._commit(ROSTER[0]),
                ),
            ),
            mock.patch.object(
                terminal_module.InteractiveExternalViewer,
                "open_owned",
                autospec=True,
                side_effect=ExternalViewUnavailableError("PRIVATE_VIEWER_CONTENT"),
            ),
        ):
            controller._view_attached_agent_exchanges()

        rendered = "".join(callbacks.records)
        self.assertIn(
            "terminalwithtransientdisplaysupport", "".join(rendered.split())
        )
        self.assertNotIn("PRIVATE_VIEWER_CONTENT", rendered)

    def test_latest_first_same_id_kind_separation_debate_subject_and_empty_agent(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        conversation = controller._conversation_catalog.create(
            "session_1",
            "Conversation subject",
        )
        shared_id = conversation.conversation_id
        self._append(
            workspace,
            scope_kind=AgentExchangeScopeKind.CONVERSATION,
            scope_id=shared_id,
            exchange_id="conversation_exchange",
        )
        self._append(
            workspace,
            scope_kind=AgentExchangeScopeKind.DEBATE,
            scope_id=shared_id,
            agent_id=ROSTER[1],
            exchange_id="debate_exchange",
        )
        workspace.append_debate_message(
            "session_1",
            shared_id,
            _message("  Exact\n debate\t subject  ", kind="draft"),
        )
        controller._attached_session_id = "session_1"
        captured_sources: list[bytes] = []

        def prepare(source: bytes, **_keywords: object):
            captured_sources.append(source)
            return lambda _host: None

        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(
                    self._commit(f"debate:{shared_id}"),
                    self._commit(ROSTER[-1]),
                ),
            ) as picker,
            mock.patch.object(
                controller_core_module,
                "prepare_agent_exchange_view",
                side_effect=prepare,
            ) as prepare_view,
            mock.patch.object(
                terminal_module.InteractiveExternalViewer,
                "open_owned",
                autospec=True,
            ) as open_view,
        ):
            controller._view_attached_agent_exchanges()

        scope_choices = picker.call_args_list[0].args[2]
        self.assertEqual(
            tuple(choice.identity for choice in scope_choices),
            (f"debate:{shared_id}", f"conversation:{shared_id}"),
        )
        self.assertEqual(
            scope_choices[0].label,
            f"Debate {shared_id} — Exact debate subject",
        )
        prepare_view.assert_called_once()
        self.assertEqual(captured_sources, [b""])
        open_view.assert_called_once()

    def test_session_gate_empty_inventory_and_both_picker_cancellations_are_inert(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        controller._view_attached_agent_exchanges()
        self.assertIn("[error session_not_attached]", "".join(callbacks.records))

        callbacks.records.clear()
        controller._attached_session_id = "session_1"
        before = {
            path.relative_to(workspace.root): path.read_bytes()
            for path in workspace.root.rglob("*")
            if path.is_file()
        }
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
        ) as picker:
            controller._view_attached_agent_exchanges()
        picker.assert_not_called()
        self.assertIn(
            "No agent exchanges are recorded for session session_1.",
            "".join(callbacks.records),
        )
        after = {
            path.relative_to(workspace.root): path.read_bytes()
            for path in workspace.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

        conversation = controller._conversation_catalog.create("session_1", "Subject")
        self._append(
            workspace,
            scope_kind=AgentExchangeScopeKind.CONVERSATION,
            scope_id=conversation.conversation_id,
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        for outcomes in (
            (cancel,),
            (self._commit(f"conversation:{conversation.conversation_id}"), cancel),
        ):
            with (
                self.subTest(outcomes=outcomes),
                mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    side_effect=outcomes,
                ) as picker,
                mock.patch.object(
                    controller_core_module,
                    "prepare_agent_exchange_view",
                ) as prepare_view,
            ):
                controller._view_attached_agent_exchanges()
            self.assertEqual(picker.call_count, len(outcomes))
            prepare_view.assert_not_called()

    def test_disappearing_snapshot_refreshes_once_and_never_substitutes_empty(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        controller._attached_session_id = "session_1"
        selection = _selection(
            AgentExchangeScopeKind.CONVERSATION,
            "conversation_1",
            ROSTER[0],
        )
        item = AgentExchangeInventoryItem(selection, 1, 1, 1, 1, 1)
        store = mock.Mock()
        store.inventory.side_effect = ((item,), ())
        store.snapshot.side_effect = AgentExchangeStorageError(
            AgentExchangeFailure.NOT_FOUND
        )
        with (
            mock.patch.object(
                controller_core_module,
                "WorkspaceAgentExchangeStore",
                return_value=store,
            ),
            mock.patch.object(
                controller_core_module.InteractiveController,
                "_load_agent_exchange_scope_choices",
                autospec=True,
                return_value=(
                    ExchangeScopeChoice(
                        AgentExchangeScopeKind.CONVERSATION,
                        "conversation_1",
                        "Subject",
                        1,
                        1,
                    ),
                ),
            ),
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(
                    self._commit("conversation:conversation_1"),
                    self._commit(ROSTER[0]),
                ),
            ),
            mock.patch.object(
                controller_core_module,
                "prepare_agent_exchange_view",
            ) as prepare_view,
        ):
            controller._view_attached_agent_exchanges()
        self.assertEqual(store.inventory.call_count, 2)
        store.snapshot.assert_called_once_with(selection)
        prepare_view.assert_not_called()
        self.assertIn(
            "selected agent exchanges are no longer available",
            "".join(callbacks.records),
        )

    def test_storage_projection_and_viewer_errors_are_content_free_in_output_and_debug(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        controller._attached_session_id = "session_1"
        session_state = workspace.path(
            "agent_states/orchestrator_state/sessions/session_1.json"
        )
        session_state.parent.mkdir(parents=True, exist_ok=True)
        session_state.write_text("{}\n", encoding="utf-8")
        private = "PRIVATE_RESPONSE /tmp/private TOKEN=secret"

        failures = (
            AgentExchangeStorageError(AgentExchangeFailure.CORRUPT_STORAGE),
            AgentExchangeProjectionError(private),
        )
        controller._present_agent_exchange_storage_failure(failures[0])  # type: ignore[arg-type]
        controller._present_agent_exchange_projection_failure(failures[1])  # type: ignore[arg-type]
        rendered = "".join(callbacks.records)
        self.assertNotIn(private, rendered)
        self.assertIn("could not be read safely", rendered)
        compact_rendered = " ".join(rendered.split())
        self.assertIn("could not be correlated", compact_rendered)
        debug = workspace.path("debug_logs/runtime.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(private, debug)
        self.assertNotIn(str(workspace.root), debug)

    def test_all_viewer_failures_have_fixed_required_installation_guidance(
        self,
    ) -> None:
        cases = {
            "installation_required": (
                "The required agent exchange viewer is unavailable.",
                "Repair or reinstall the standard story-writing-agents "
                "installation and retry /exchanges.",
            ),
            "incompatible": (
                "The required agent exchange viewer is incompatible with Story.",
                "Repair or reinstall the standard story-writing-agents "
                "installation and retry /exchanges.",
            ),
            "view_failed": (
                "The agent exchange viewer failed.",
                "Inspect the sanitized diagnostic before retrying /exchanges.",
            ),
        }
        for code, (summary, hint) in cases.items():
            with self.subTest(code=code):
                workspace = self.make_workspace()
                controller, _backend, callbacks = self.make_controller(workspace)
                controller._attached_session_id = "session_1"
                session_state = workspace.path(
                    "agent_states/orchestrator_state/sessions/session_1.json"
                )
                session_state.parent.mkdir(parents=True, exist_ok=True)
                session_state.write_text("{}\n", encoding="utf-8")
                controller._present_agent_exchange_viewer_failure(
                    AgentExchangeViewerError(code)
                )
                rendered = "".join(callbacks.records)
                compact = "".join(rendered.split())
                self.assertIn("".join(summary.split()), compact)
                self.assertIn("".join(hint.split()), compact)
                self.assertNotIn("optional", rendered.lower())
                self.assertNotIn("[viewer]", rendered)
                self.assertNotIn("PRIVATE", rendered)
                self.assertNotIn("/tmp/", rendered)
                self.assertLessEqual(len(rendered.encode("utf-8")), 512)
                debug = workspace.path("debug_logs/runtime.jsonl").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("PRIVATE", debug)
                self.assertNotIn(str(workspace.root), debug)


if __name__ == "__main__":
    unittest.main()
