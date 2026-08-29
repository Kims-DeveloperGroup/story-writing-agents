"""Immutable bounded agent-exchange contract and facade tests."""

from __future__ import annotations

import dataclasses
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import story_writing_agents
from story_writing_agents import agent_exchanges
from story_writing_agents.agent_exchanges import (
    AGENT_EXCHANGE_CONTENT_MAX_BYTES,
    AGENT_EXCHANGE_SCHEMA_VERSION,
    AGENT_EXCHANGE_TOTAL_MAX_BYTES,
    AgentExchangeAppendReceipt,
    AgentExchangeFailure,
    AgentExchangeInventoryItem,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
    AgentExchangeSelection,
    AgentExchangeSnapshot,
    AgentExchangeStorageError,
    WorkspaceAgentExchangeStore,
)
from story_writing_agents.agent_exchanges import contracts
from story_writing_agents.agents import ROSTER
from story_writing_agents.errors import StoryWritingAgentsError


ROOT = Path(__file__).resolve().parents[1]
_TIMESTAMP = "2026-08-29T01:02:03.456+00:00"
_DIGEST = "sha256:" + "0" * 64


def _selection() -> AgentExchangeSelection:
    return AgentExchangeSelection(
        session_id="session_1",
        scope_kind=AgentExchangeScopeKind.CONVERSATION,
        scope_id="conversation_1",
        agent_id=ROSTER[0],
    )


class AgentExchangeContractTests(unittest.TestCase):
    def test_exact_constants_enums_and_supported_surfaces(self) -> None:
        self.assertEqual(AGENT_EXCHANGE_SCHEMA_VERSION, 1)
        self.assertEqual(AGENT_EXCHANGE_CONTENT_MAX_BYTES, 262_144)
        self.assertEqual(AGENT_EXCHANGE_TOTAL_MAX_BYTES, 16_777_216)
        self.assertEqual(
            tuple(AgentExchangeProvider),
            (
                AgentExchangeProvider.CODEX,
                AgentExchangeProvider.ANTIGRAVITY,
            ),
        )
        self.assertEqual(
            tuple(AgentExchangeRequestType),
            (
                AgentExchangeRequestType.REQUEST,
                AgentExchangeRequestType.RESPONSE,
                AgentExchangeRequestType.NO_RESPONSE,
            ),
        )
        self.assertEqual(
            tuple(AgentExchangeScopeKind),
            (
                AgentExchangeScopeKind.CONVERSATION,
                AgentExchangeScopeKind.DEBATE,
            ),
        )
        self.assertEqual(
            tuple(item.value for item in AgentExchangeFailure),
            (
                "unsupported_platform",
                "contention",
                "unsafe_storage",
                "corrupt_storage",
                "capacity_exceeded",
                "io_failed",
                "not_found",
            ),
        )
        contract_names = [
            "AGENT_EXCHANGE_CONTENT_MAX_BYTES",
            "AGENT_EXCHANGE_SCHEMA_VERSION",
            "AGENT_EXCHANGE_TOTAL_MAX_BYTES",
            "AgentExchangeAppendReceipt",
            "AgentExchangeFailure",
            "AgentExchangeInventoryItem",
            "AgentExchangeProvider",
            "AgentExchangeRecordInput",
            "AgentExchangeRequestType",
            "AgentExchangeScopeKind",
            "AgentExchangeSelection",
            "AgentExchangeSnapshot",
            "AgentExchangeStorageError",
        ]
        self.assertEqual(contracts.__all__, contract_names)
        self.assertEqual(
            agent_exchanges.__all__,
            [*contract_names, "WorkspaceAgentExchangeStore"],
        )
        for name in agent_exchanges.__all__:
            self.assertIs(
                getattr(agent_exchanges, name),
                getattr(contracts, name, WorkspaceAgentExchangeStore),
            )
            self.assertNotIn(name, story_writing_agents.__all__)
        self.assertEqual(len(story_writing_agents.__all__), 120)

    def test_input_schema_is_exact_frozen_slotted_and_content_hidden(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(AgentExchangeRecordInput).parameters),
            (
                "session_id",
                "scope_kind",
                "scope_id",
                "agent_id",
                "provider",
                "exchange_id",
                "request_type",
                "content",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(AgentExchangeRecordInput)),
            tuple(inspect.signature(AgentExchangeRecordInput).parameters),
        )
        value = AgentExchangeRecordInput(
            "s" + "a" * 63,
            AgentExchangeScopeKind.DEBATE,
            "d" + "_" * 63,
            ROSTER[0],
            AgentExchangeProvider.CODEX,
            "e" + "-" * 127,
            AgentExchangeRequestType.REQUEST,
            "PRIVATE request body",
        )
        self.assertNotIn("PRIVATE", repr(value))
        self.assertFalse(hasattr(value, "__dict__"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.content = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            AgentExchangeRecordInput(  # type: ignore[call-arg]
                session_id="s",
                scope_kind=AgentExchangeScopeKind.CONVERSATION,
                scope_id="c",
                agent_id=ROSTER[0],
                provider=AgentExchangeProvider.CODEX,
                exchange_id="e",
                request_type=AgentExchangeRequestType.REQUEST,
                content="body",
                transport="PRIVATE transport",
            )

    def test_input_rejects_unsafe_identity_enum_content_and_surrogates(self) -> None:
        base: dict[str, object] = {
            "session_id": "session",
            "scope_kind": AgentExchangeScopeKind.CONVERSATION,
            "scope_id": "conversation",
            "agent_id": ROSTER[0],
            "provider": AgentExchangeProvider.CODEX,
            "exchange_id": "exchange",
            "request_type": AgentExchangeRequestType.REQUEST,
            "content": "body",
        }
        cases = (
            ({"session_id": "../PRIVATE_PATH"}, ValueError),
            ({"scope_id": "space value"}, ValueError),
            ({"exchange_id": "x" * 129}, ValueError),
            ({"agent_id": "unknown_agent"}, ValueError),
            ({"session_id": b"session"}, TypeError),
            ({"scope_kind": "conversation"}, TypeError),
            ({"provider": "codex"}, TypeError),
            ({"request_type": "request"}, TypeError),
            ({"content": b"PRIVATE_CONTENT"}, TypeError),
            ({"content": "bad\ud800content"}, ValueError),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                arguments = dict(base)
                arguments.update(overrides)
                with self.assertRaises(expected) as raised:
                    AgentExchangeRecordInput(**arguments)  # type: ignore[arg-type]
                self.assertNotIn("PRIVATE", str(raised.exception))

    def test_receipt_selection_inventory_and_snapshot_validate_exact_facts(
        self,
    ) -> None:
        self.assertEqual(
            tuple(inspect.signature(AgentExchangeAppendReceipt).parameters),
            (
                "session_id",
                "agent_id",
                "exchange_id",
                "workspace_sequence",
                "sequence",
                "timestamp",
                "content_full_utf8_bytes",
                "content_retained_utf8_bytes",
                "content_full_characters",
                "content_retained_characters",
                "content_truncated",
                "content_sha256",
            ),
        )
        self.assertEqual(
            tuple(
                field.name for field in dataclasses.fields(AgentExchangeAppendReceipt)
            ),
            tuple(inspect.signature(AgentExchangeAppendReceipt).parameters),
        )
        receipt = AgentExchangeAppendReceipt(
            session_id="session_1",
            agent_id=ROSTER[0],
            exchange_id="exchange_1",
            workspace_sequence=1,
            sequence=2,
            timestamp=_TIMESTAMP,
            content_full_utf8_bytes=4,
            content_retained_utf8_bytes=3,
            content_full_characters=4,
            content_retained_characters=3,
            content_truncated=True,
            content_sha256=_DIGEST,
        )
        selection = _selection()
        inventory = AgentExchangeInventoryItem(selection, 2, 1, 3, 2, 4)
        snapshot = AgentExchangeSnapshot(selection, b"{}\n{}\n", 2, 1, 3, 2, 4)
        for value, attribute in (
            (receipt, "sequence"),
            (selection, "scope_id"),
            (inventory, "record_count"),
            (snapshot, "record_count"),
        ):
            self.assertFalse(hasattr(value, "__dict__"))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(value, attribute, getattr(value, attribute))
        self.assertNotIn("{}", repr(snapshot))
        invalid_receipts = (
            {"workspace_sequence": 0},
            {"timestamp": "2026-08-29T01:02:03+00:00"},
            {"content_retained_utf8_bytes": 5},
            {"content_full_characters": -1},
            {"content_retained_characters": -1},
            {"content_full_characters": True},
            {"content_retained_characters": False},
            {"content_retained_characters": 5},
            {"content_full_characters": 3, "content_retained_characters": 3},
            {"content_truncated": False},
            {"content_sha256": "PRIVATE digest"},
        )
        for overrides in invalid_receipts:
            values: dict[str, object] = {
                "session_id": "session_1",
                "agent_id": ROSTER[0],
                "exchange_id": "exchange_1",
                "workspace_sequence": 1,
                "sequence": 2,
                "timestamp": _TIMESTAMP,
                "content_full_utf8_bytes": 4,
                "content_retained_utf8_bytes": 3,
                "content_full_characters": 4,
                "content_retained_characters": 3,
                "content_truncated": True,
                "content_sha256": _DIGEST,
            }
            values.update(overrides)
            with (
                self.subTest(overrides=overrides),
                self.assertRaises((TypeError, ValueError)),
            ):
                AgentExchangeAppendReceipt(**values)  # type: ignore[arg-type]
        for arguments in (
            (selection, 0, 1, 1, 1, 1),
            (selection, 1, 2, 1, 1, 1),
            (selection, 1, 1, 1, 2, 1),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                AgentExchangeInventoryItem(*arguments)
        for payload, count in ((b"", 0), (b"{}", 1), (b"\xff\n", 1), (b"{}\n", 2)):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                AgentExchangeSnapshot(selection, payload, count, 1, 1, 1, 1)

    def test_storage_errors_are_exact_content_free_classifications(self) -> None:
        for failure in AgentExchangeFailure:
            error = AgentExchangeStorageError(failure)
            self.assertIsInstance(error, StoryWritingAgentsError)
            self.assertIs(error.failure, failure)
            self.assertEqual(
                str(error),
                f"agent exchange storage failure: {failure.value}",
            )
            self.assertFalse(hasattr(error, "path"))
            self.assertNotIn("PRIVATE", str(error))
        with self.assertRaises(TypeError):
            AgentExchangeStorageError("PRIVATE")  # type: ignore[arg-type]

    def test_clean_import_and_store_construction_are_io_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = (
                "import pathlib,sys; "
                f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
                "before=tuple(pathlib.Path('.').iterdir()); "
                "import story_writing_agents.agent_exchanges as a; "
                "assert len(a.__all__)==14; "
                "assert tuple(pathlib.Path('.').iterdir())==before"
            )
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
