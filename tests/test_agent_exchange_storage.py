"""Secure durable bounded agent-exchange storage tests."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import threading
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from story_writing_agents.agent_exchanges import (
    AGENT_EXCHANGE_CONTENT_MAX_BYTES,
    AgentExchangeFailure,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
    AgentExchangeSelection,
    AgentExchangeStorageError,
    WorkspaceAgentExchangeStore,
)
from story_writing_agents.agent_exchanges import storage as storage_module
from story_writing_agents.agents import ROSTER
from story_writing_agents.workspace import Workspace

from tests.helpers import initialized_workspace


_RECORD_KEYS = {
    "schema_version",
    "workspace_sequence",
    "sequence",
    "timestamp",
    "request_type",
    "session_id",
    "scope_kind",
    "scope_id",
    "agent_id",
    "provider",
    "exchange_id",
    "content",
    "content_full_utf8_bytes",
    "content_retained_utf8_bytes",
    "content_full_characters",
    "content_retained_characters",
    "content_truncated",
    "content_sha256",}


def _input(
    *,
    session_id: str = "session_1",
    scope_kind: AgentExchangeScopeKind = AgentExchangeScopeKind.CONVERSATION,
    scope_id: str = "conversation_1",
    agent_id: str = ROSTER[0],
    provider: AgentExchangeProvider = AgentExchangeProvider.CODEX,
    exchange_id: str = "exchange_1",
    request_type: AgentExchangeRequestType = AgentExchangeRequestType.REQUEST,
    content: str = "provider body",
) -> AgentExchangeRecordInput:
    return AgentExchangeRecordInput(
        session_id=session_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        agent_id=agent_id,
        provider=provider,
        exchange_id=exchange_id,
        request_type=request_type,
        content=content,
    )


def _selection(
    *,
    session_id: str = "session_1",
    scope_kind: AgentExchangeScopeKind = AgentExchangeScopeKind.CONVERSATION,
    scope_id: str = "conversation_1",
    agent_id: str = ROSTER[0],
) -> AgentExchangeSelection:
    return AgentExchangeSelection(session_id, scope_kind, scope_id, agent_id)


def _decoded_snapshot(
    store: WorkspaceAgentExchangeStore,
    selection: AgentExchangeSelection | None = None,
) -> list[dict[str, object]]:
    snapshot = store.snapshot(selection or _selection())
    return [json.loads(line) for line in snapshot.jsonl_bytes.splitlines()]


def _store_directory(workspace: Workspace) -> Path:
    return workspace.path("debug_logs/agent_exchanges")


def _active_path(
    workspace: Workspace,
    *,
    session_id: str = "session_1",
    agent_id: str = ROSTER[0],
) -> Path:
    return _store_directory(workspace) / session_id / f"{agent_id}.jsonl"


def _assert_storage_failure(
    test_case: unittest.TestCase,
    failure: AgentExchangeFailure,
    callback: object,
) -> AgentExchangeStorageError:
    if not callable(callback):
        raise AssertionError("callback must be callable")
    with test_case.assertRaises(AgentExchangeStorageError) as raised:
        callback()
    test_case.assertIs(raised.exception.failure, failure)
    test_case.assertEqual(
        str(raised.exception),
        f"agent exchange storage failure: {failure.value}",)
    return raised.exception


def _canonical_line(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


class AgentExchangeStorageBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.store = WorkspaceAgentExchangeStore(self.workspace)

    def test_construction_absent_inventory_and_snapshot_are_noncreating(self) -> None:
        before = {
            path.relative_to(self.workspace.root): path.read_bytes()
            for path in self.workspace.root.rglob("*")
            if path.is_file()
        }
        second = WorkspaceAgentExchangeStore(self.workspace)
        self.assertEqual(second.inventory("session_1"), ())
        self.assertFalse(_store_directory(self.workspace).exists())
        error = _assert_storage_failure(
            self,
            AgentExchangeFailure.NOT_FOUND,
            lambda: second.snapshot(_selection()),
        )
        self.assertNotIn(str(self.workspace.root), str(error))
        after = {
            path.relative_to(self.workspace.root): path.read_bytes()
            for path in self.workspace.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        with self.assertRaises(TypeError):
            WorkspaceAgentExchangeStore(self.workspace.root)  # type: ignore[arg-type]

    def test_exact_canonical_schema_key_set_and_request_response_correlation(
        self,
    ) -> None:
        receipts = []
        for request_type, content, provider in (
            (
                AgentExchangeRequestType.REQUEST,
                "request 한글🙂",
                AgentExchangeProvider.CODEX,),
            (
                AgentExchangeRequestType.RESPONSE,
                "response",
                AgentExchangeProvider.ANTIGRAVITY,
            ),
            (
                AgentExchangeRequestType.NO_RESPONSE,
                "",
                AgentExchangeProvider.ANTIGRAVITY,
            ),
            (
                AgentExchangeRequestType.OUTCOME,
                '{"code":"accepted","outcome":"accepted"}',
                AgentExchangeProvider.ANTIGRAVITY,
            ),
        ):
            receipts.append(
                self.store.append(
                    _input(
                        exchange_id="shared_exchange",
                        request_type=request_type,
                        content=content,
                        provider=provider,
                    )
                )
            )
        snapshot = self.store.snapshot(_selection())
        records = [json.loads(line) for line in snapshot.jsonl_bytes.splitlines()]
        self.assertEqual([item.workspace_sequence for item in receipts], [1, 2, 3, 4])
        self.assertEqual([item.sequence for item in receipts], [1, 2, 3, 4])
        self.assertEqual(snapshot.record_count, 4)
        self.assertEqual((snapshot.first_sequence, snapshot.last_sequence), (1, 4))
        self.assertEqual(
            [record["request_type"] for record in records],
            ["request", "response", "no_response", "outcome"],
        )
        self.assertEqual(
            [record["provider"] for record in records],
            ["codex", "antigravity", "antigravity", "antigravity"],
        )
        self.assertEqual( {record["exchange_id"] for record in records}, {"shared_exchange"} )
        for line, record in zip( snapshot.jsonl_bytes.splitlines(keepends=True), records ):
            self.assertEqual(set(record), _RECORD_KEYS)
            self.assertEqual(list(record), sorted(_RECORD_KEYS))
            self.assertEqual(line, _canonical_line(record))
            self.assertEqual(record["schema_version"], 1)
            self.assertNotIn("stderr", record)
            self.assertNotIn("transport", record)
            self.assertNotIn("authentication", record)
            self.assertNotIn("environment", record)
            self.assertNotIn("credentials", record)
        self.assertIs(type(snapshot.jsonl_bytes), bytes)
        self.assertFalse(hasattr(snapshot, "path"))
        self.assertFalse(hasattr(snapshot, "handle"))

    def test_content_bounds_complete_utf8_prefix_counts_and_full_digest(self) -> None:
        exact = "x" * AGENT_EXCHANGE_CONTENT_MAX_BYTES
        exact_receipt = self.store.append(_input(content=exact, exchange_id="exact"))
        self.assertFalse(exact_receipt.content_truncated)
        self.assertEqual( exact_receipt.content_retained_utf8_bytes, AGENT_EXCHANGE_CONTENT_MAX_BYTES, )
        self.assertEqual(exact_receipt.content_full_characters, len(exact))
        self.assertEqual(exact_receipt.content_retained_characters, len(exact))

        unicode_content = "한" * 87_382
        full = unicode_content.encode("utf-8")
        receipt = self.store.append( _input(content=unicode_content, exchange_id="unicode") )
        self.assertEqual(receipt.content_full_utf8_bytes, len(full))
        self.assertEqual(receipt.content_retained_utf8_bytes, 262_143)
        self.assertEqual(receipt.content_full_characters, len(unicode_content))
        self.assertEqual(receipt.content_retained_characters, 87_381)
        self.assertTrue(receipt.content_truncated)
        self.assertEqual( receipt.content_sha256, "sha256:" + hashlib.sha256(full).hexdigest(), )
        records = _decoded_snapshot(self.store)
        retained = records[-1]["content"]
        self.assertIsInstance(retained, str)
        assert isinstance(retained, str)
        self.assertEqual(len(retained.encode("utf-8")), 262_143)
        self.assertEqual(records[-1]["content_full_utf8_bytes"], len(full))
        self.assertEqual(records[-1]["content_retained_utf8_bytes"], 262_143)
        self.assertEqual(records[-1]["content_full_characters"], len(unicode_content))
        self.assertEqual(records[-1]["content_retained_characters"], len(retained))
        self.assertTrue(records[-1]["content_truncated"])

    def test_global_and_per_stream_sequences_survive_new_store_instances(self) -> None:
        first = self.store.append(_input(exchange_id="e1"))
        second = WorkspaceAgentExchangeStore(self.workspace).append( _input(agent_id=ROSTER[1], exchange_id="e2") )
        third = WorkspaceAgentExchangeStore(self.workspace).append( _input(session_id="session_2", exchange_id="e3") )
        fourth = WorkspaceAgentExchangeStore(self.workspace).append(
            _input(exchange_id="e4", request_type=AgentExchangeRequestType.RESPONSE))
        self.assertEqual( [item.workspace_sequence for item in (first, second, third, fourth)], [1, 2, 3, 4], )
        self.assertEqual( [item.sequence for item in (first, second, third, fourth)], [1, 1, 1, 2] )

    def test_inventory_is_content_free_ordered_and_snapshot_filters_exact_scope(
        self,
    ) -> None:
        values = (
            _input(content="PRIVATE one", exchange_id="e1"),
            _input(
                scope_kind=AgentExchangeScopeKind.DEBATE,
                scope_id="debate_1",
                content="PRIVATE two",
                exchange_id="e2",),
            _input( agent_id=ROSTER[1], content="PRIVATE three", exchange_id="e3", ),
            _input( session_id="session_2", content="PRIVATE four", exchange_id="e4", ),
        )
        for value in values:
            self.store.append(value)
        inventory = self.store.inventory("session_1")
        self.assertEqual( [item.first_workspace_sequence for item in inventory], [1, 2, 3] )
        self.assertEqual(
            [item.selection for item in inventory],
            [
                _selection(),
                _selection( scope_kind=AgentExchangeScopeKind.DEBATE, scope_id="debate_1", ),
                _selection(agent_id=ROSTER[1]),
            ],
        )
        self.assertNotIn("PRIVATE", repr(inventory))
        for item in inventory:
            self.assertFalse(hasattr(item, "content"))
        self.assertEqual(len(_decoded_snapshot(self.store)), 1)
        self.assertEqual(
            len(
                _decoded_snapshot(
                    self.store,
                    _selection( scope_kind=AgentExchangeScopeKind.DEBATE, scope_id="debate_1", ),
                )
            ),
            1,
        )
        _assert_storage_failure(
            self,
            AgentExchangeFailure.NOT_FOUND,
            lambda: self.store.snapshot( _selection(scope_kind=AgentExchangeScopeKind.DEBATE) ),
        )

    def test_failed_append_reservations_leave_gaps_without_reuse(self) -> None:
        with mock.patch.object(
            storage_module,
            "_append_segment_line",
            side_effect=OSError(errno.EIO, "PRIVATE write failure"),
        ):
            error = _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(_input(exchange_id="failed")),
            )
        self.assertNotIn("PRIVATE", str(error))
        receipt = self.store.append(_input(exchange_id="succeeded"))
        self.assertEqual((receipt.workspace_sequence, receipt.sequence), (2, 2))
        records = _decoded_snapshot(self.store)
        self.assertEqual( [(record["workspace_sequence"], record["sequence"]) for record in records], [(2, 2)], )

    def test_rotation_keeps_exactly_seven_backups_and_complete_lines(self) -> None:
        with mock.patch.object(storage_module, "_SEGMENT_MAX_BYTES", 1_100):
            for index in range(10):
                self.store.append(_input(exchange_id=f"e{index}", content="x" * 500))
            session = _store_directory(self.workspace) / "session_1"
            segments = sorted(path.name for path in session.glob(f"{ROSTER[0]}.jsonl*"))
            self.assertEqual(
                segments,
                [
                    f"{ROSTER[0]}.jsonl",
                    *(f"{ROSTER[0]}.jsonl.{index}" for index in range(1, 8)),
                ],
            )
            for filename in segments:
                payload = (session / filename).read_bytes()
                self.assertTrue(payload.endswith(b"\n"))
                for line in payload.splitlines(keepends=True):
                    self.assertTrue(line.endswith(b"\n"))
                    json.loads(line)
            snapshot = self.store.snapshot(_selection())
            retained = [json.loads(line) for line in snapshot.jsonl_bytes.splitlines()]
            self.assertEqual( [record["workspace_sequence"] for record in retained], list(range(3, 11)), )

    def test_global_pruning_replaces_split_segment_with_newest_exact_suffix(
        self,
    ) -> None:
        replacements: list[str] = []
        original_replace = storage_module._atomic_replace_leaf

        def recording_replace(*args: object, **kwargs: object) -> None:
            pending = kwargs.get("pending_filename")
            if isinstance(pending, str) and pending.endswith(".segment.pending"):
                replacements.append(pending)
            original_replace(*args, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.object(storage_module, "_SEGMENT_MAX_BYTES", 2_100),
            mock.patch.object( storage_module, "AGENT_EXCHANGE_TOTAL_MAX_BYTES", 3_500, ),
            mock.patch.object( storage_module, "_atomic_replace_leaf", side_effect=recording_replace, ),
        ):
            for index in range(8):
                self.store.append(_input(exchange_id=f"e{index}", content="y" * 500))
            snapshot = self.store.snapshot(_selection())
            records = [json.loads(line) for line in snapshot.jsonl_bytes.splitlines()]
            total = sum(
                path.stat().st_size
                for path in (_store_directory(self.workspace) / "session_1").glob( "*.jsonl*" )
            )
            self.assertLessEqual(total, 3_500)
            sequences = [record["workspace_sequence"] for record in records]
            self.assertEqual(sequences, list(range(sequences[0], 9)))
            self.assertTrue(replacements)


class AgentExchangeConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    def test_threads_serialize_one_agent_and_keep_agent_streams_independent(
        self,
    ) -> None:
        barrier = threading.Barrier(16)

        def append(index: int) -> tuple[int, int, str]:
            barrier.wait(timeout=3)
            agent_id = ROSTER[index % 2]
            receipt = WorkspaceAgentExchangeStore(self.workspace).append(
                _input(agent_id=agent_id, exchange_id=f"thread_{index}"))
            return receipt.workspace_sequence, receipt.sequence, agent_id

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(append, range(16)))
        self.assertEqual(sorted(item[0] for item in results), list(range(1, 17)))
        for agent_id in ROSTER[:2]:
            sequences = sorted(item[1] for item in results if item[2] == agent_id)
            self.assertEqual(sequences, list(range(1, 9)))
            records = _decoded_snapshot(
                WorkspaceAgentExchangeStore(self.workspace),
                _selection(agent_id=agent_id),
            )
            self.assertEqual(
                [record["sequence"] for record in records],
                list(range(1, 9)),
            )



class AgentExchangeStorageSecurityTests(unittest.TestCase):
    def _seed(self) -> tuple[Workspace, WorkspaceAgentExchangeStore]:
        workspace = initialized_workspace(self)
        store = WorkspaceAgentExchangeStore(workspace)
        store.append(_input())
        return workspace, store

    def test_unsafe_caller_ids_and_unsupported_primitives_fail_safely(self) -> None:
        workspace = initialized_workspace(self)
        store = WorkspaceAgentExchangeStore(workspace)
        for value in ("../PRIVATE", "space value", b"session", ""):
            with self.subTest(value=value):
                error = _assert_storage_failure(
                    self,
                    AgentExchangeFailure.UNSAFE_STORAGE,
                    lambda value=value: store.inventory(value),
                )
                self.assertNotIn("PRIVATE", str(error))
        self.assertFalse(_store_directory(workspace).exists())
        with (
            mock.patch.object(storage_module, "_fcntl", None),
            mock.patch.object(storage_module, "_msvcrt", None),
        ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.UNSUPPORTED_PLATFORM,
                lambda: store.inventory("session_1"),
            )

    def test_process_lock_contention_is_bounded_and_released(self) -> None:
        workspace = initialized_workspace(self)
        store = WorkspaceAgentExchangeStore(workspace)
        process_lock = storage_module._process_lock_for(workspace)
        process_lock.lock.acquire()
        try:
            _assert_storage_failure( self, AgentExchangeFailure.CONTENTION, lambda: store.append(_input()), )
        finally:
            process_lock.lock.release()
        self.assertEqual(store.append(_input()).workspace_sequence, 1)

    def test_symlink_hardlink_fifo_modes_and_unexpected_names_are_rejected(
        self,
    ) -> None:
        def symlink_case(
            workspace: Workspace, _store: WorkspaceAgentExchangeStore
        ) -> None:
            active = _active_path(workspace)
            outside = workspace.root.parent / f"{workspace.root.name}-outside"
            outside.write_bytes(b"PRIVATE")
            self.addCleanup(lambda: outside.unlink(missing_ok=True))
            active.unlink()
            active.symlink_to(outside)

        def hardlink_case(
            workspace: Workspace, _store: WorkspaceAgentExchangeStore
        ) -> None:
            outside = workspace.root.parent / f"{workspace.root.name}-link"
            os.link(_active_path(workspace), outside)
            self.addCleanup(lambda: outside.unlink(missing_ok=True))

        def fifo_case(
            workspace: Workspace, _store: WorkspaceAgentExchangeStore
        ) -> None:
            active = _active_path(workspace)
            active.unlink()
            os.mkfifo(active, 0o600)

        def leaf_mode_case(
            workspace: Workspace, _store: WorkspaceAgentExchangeStore
        ) -> None:
            _active_path(workspace).chmod(0o644)

        def session_mode_case(
            workspace: Workspace, _store: WorkspaceAgentExchangeStore
        ) -> None:
            (_store_directory(workspace) / "session_1").chmod(0o755)

        def unexpected_case(
            workspace: Workspace, _store: WorkspaceAgentExchangeStore
        ) -> None:
            (_store_directory(workspace) / "session_1" / "PRIVATE.txt").write_text( "PRIVATE", encoding="utf-8" )

        for name, mutation in (
            ("symlink", symlink_case),
            ("hardlink", hardlink_case),
            ("fifo", fifo_case),
            ("leaf_mode", leaf_mode_case),
            ("session_mode", session_mode_case),
            ("unexpected", unexpected_case),
        ):
            with self.subTest(name=name):
                workspace, store = self._seed()
                try:
                    mutation(workspace, store)
                except (AttributeError, NotImplementedError, PermissionError) as exc:
                    self.skipTest(f"{name} unsupported: {exc}")
                error = _assert_storage_failure(
                    self,
                    AgentExchangeFailure.UNSAFE_STORAGE,
                    lambda: store.inventory("session_1"),
                )
                self.assertNotIn("PRIVATE", str(error))

    def test_root_debug_store_and_session_alias_swaps_are_rejected(self) -> None:
        for target in ("root", "debug", "store", "session"):
            with self.subTest(target=target):
                workspace, store = self._seed()
                if target == "root":
                    canonical = workspace.root
                elif target == "debug":
                    canonical = workspace.path("debug_logs")
                elif target == "store":
                    canonical = _store_directory(workspace)
                else:
                    canonical = _store_directory(workspace) / "session_1"
                parked = canonical.parent / f"{canonical.name}.parked"
                outside = canonical.parent / f"{canonical.name}.outside"
                outside.mkdir()
                (outside / "PRIVATE").write_bytes(b"PRIVATE")
                os.rename(canonical, parked)
                try:
                    canonical.symlink_to(outside, target_is_directory=True)
                except (NotImplementedError, PermissionError) as exc:
                    os.rename(parked, canonical)
                    self.skipTest(f"directory symlink unsupported: {exc}")
                try:
                    _assert_storage_failure(
                        self,
                        AgentExchangeFailure.UNSAFE_STORAGE,
                        lambda: store.inventory("session_1"),
                    )
                    self.assertEqual((outside / "PRIVATE").read_bytes(), b"PRIVATE")
                finally:
                    canonical.unlink()
                    os.rename(parked, canonical)
                    for child in outside.iterdir():
                        child.unlink()
                    outside.rmdir()

    def test_malformed_records_states_sequences_and_metadata_fail_closed(self) -> None:
        def rewrite_record(workspace: Workspace, mutate: object) -> None:
            path = _active_path(workspace)
            value = json.loads(path.read_bytes())
            assert callable(mutate)
            mutate(value)
            path.write_bytes(_canonical_line(value))

        mutations = (
            ("extra", lambda value: value.__setitem__("PRIVATE", "PRIVATE")),
            ("schema", lambda value: value.__setitem__("schema_version", 2)),
            (
                "digest",
                lambda value: value.__setitem__("content_sha256", "sha256:" + "f" * 64),
            ),
            (
                "count",
                lambda value: value.__setitem__("content_retained_utf8_bytes", 999),
            ),
            (
                "full-characters-negative",
                lambda value: value.__setitem__("content_full_characters", -1),
            ),
            (
                "full-characters-boolean",
                lambda value: value.__setitem__("content_full_characters", True),
            ),
            (
                "retained-characters-negative",
                lambda value: value.__setitem__("content_retained_characters", -1),
            ),
            (
                "retained-characters-boolean",
                lambda value: value.__setitem__("content_retained_characters", False),
            ),
            (
                "retained-characters-over-full",
                lambda value: value.__setitem__(
                    "content_retained_characters",
                    int(value["content_full_characters"]) + 1,
                ),
            ),
            (
                "wrong-retained-character-count",
                lambda value: value.__setitem__(
                    "content_retained_characters",
                    int(value["content_retained_characters"]) - 1,
                ),
            ),
            (
                "wrong-untruncated-full-character-count",
                lambda value: value.__setitem__(
                    "content_full_characters",
                    int(value["content_full_characters"]) + 1,
                ),
            ),
            (
                "wrong-character-truncation-fact",
                lambda value: value.__setitem__("content_truncated", True),
            ),
            ("timestamp", lambda value: value.__setitem__("timestamp", "2026-01-01")),
            (
                "request_type",
                lambda value: value.__setitem__("request_type", "PRIVATE"),
            ),
            ("session", lambda value: value.__setitem__("session_id", "other")),
        )
        for name, mutation in mutations:
            with self.subTest(name=name):
                workspace, store = self._seed()
                rewrite_record(workspace, mutation)
                error = _assert_storage_failure(
                    self,
                    AgentExchangeFailure.CORRUPT_STORAGE,
                    lambda: store.inventory("session_1"),
                )
                self.assertNotIn("PRIVATE", str(error))

        workspace = initialized_workspace(self)
        store = WorkspaceAgentExchangeStore(workspace)
        store.append(_input(content="한" * 87_382))
        rewrite_record(
            workspace,
            lambda value: value.__setitem__( "content_full_characters", value["content_retained_characters"] ),
        )
        _assert_storage_failure( self, AgentExchangeFailure.CORRUPT_STORAGE, lambda: store.inventory("session_1"), )

        for name, payload in (
            ("malformed", b"{\n"),
            (
                "duplicate",
                b'{"agent_id":"lead_orchestrator","agent_id":"lead_orchestrator"}\n',),
            ("noncanonical", b'{ "schema_version": 1 }\n'),
        ):
            with self.subTest(name=name):
                workspace, store = self._seed()
                _active_path(workspace).write_bytes(payload)
                _assert_storage_failure(
                    self,
                    AgentExchangeFailure.CORRUPT_STORAGE,
                    lambda: store.inventory("session_1"),
                )

    def test_state_high_water_below_logs_and_pending_state_are_corrupt(self) -> None:
        cases: tuple[tuple[str, Callable[[Workspace], int]], ...] = (
            (
                "global-low",
                lambda workspace: (
                    _store_directory(workspace) / ".state.json"
                ).write_bytes(storage_module._global_state_payload(0)),
            ),
            (
                "stream-low",
                lambda workspace: (
                    _store_directory(workspace)
                    / "session_1"
                    / f"{ROSTER[0]}.sequence.json"
                ).write_bytes(storage_module._stream_state_payload(0)),
            ),
            (
                "pending-global",
                lambda workspace: (
                    _store_directory(workspace) / ".state.pending"
                ).write_bytes(storage_module._global_state_payload(2)),
            ),
            (
                "pending-stream",
                lambda workspace: (
                    _store_directory(workspace)
                    / "session_1"
                    / f".{ROSTER[0]}.sequence.pending"
                ).write_bytes(storage_module._stream_state_payload(2)),
            ),
        )
        for name, mutation in cases:
            with self.subTest(name=name):
                workspace, store = self._seed()
                mutation(workspace)
                _assert_storage_failure(
                    self,
                    AgentExchangeFailure.CORRUPT_STORAGE,
                    lambda: store.inventory("session_1"),
                )

        targets: tuple[tuple[str, Callable[[Workspace], Path]], ...] = (
            ("global", lambda workspace: _store_directory(workspace) / ".state.json"),
            (
                "stream",
                lambda workspace: _store_directory(workspace)
                / "session_1"
                / f"{ROSTER[0]}.sequence.json",
            ),
        )
        for name, target in targets:
            with self.subTest(name=f"missing-{name}"):
                workspace, store = self._seed()
                target(workspace).unlink()
                _assert_storage_failure(
                    self,
                    AgentExchangeFailure.CORRUPT_STORAGE,
                    lambda: store.inventory("session_1"),
                )

    def test_duplicate_or_decreasing_sequences_are_rejected(self) -> None:
        for field in ("workspace_sequence", "sequence"):
            workspace, store = self._seed()
            store.append(_input(exchange_id="second"))
            path = _active_path(workspace)
            lines = path.read_bytes().splitlines(keepends=True)
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            second[field] = first[field]
            path.write_bytes(lines[0] + _canonical_line(second))
            with self.subTest(field=field):
                _assert_storage_failure(
                    self,
                    AgentExchangeFailure.CORRUPT_STORAGE,
                    lambda: store.inventory("session_1"),
                )

    def test_oversized_state_segment_and_managed_namespace_are_bounded(self) -> None:
        workspace, store = self._seed()
        state = _store_directory(workspace) / ".state.json"
        state.write_bytes(b"x" * 513)
        _assert_storage_failure( self, AgentExchangeFailure.CAPACITY_EXCEEDED, lambda: store.inventory("session_1"), )

        workspace, store = self._seed()
        with _active_path(workspace).open("r+b") as handle:
            handle.truncate(2_097_153)
        _assert_storage_failure( self, AgentExchangeFailure.CAPACITY_EXCEEDED, lambda: store.inventory("session_1"), )

        workspace, store = self._seed()
        session = _store_directory(workspace) / "session_1"
        for index in range(4_097):
            (session / f"x{index}").touch(mode=0o600)
        _assert_storage_failure( self, AgentExchangeFailure.CAPACITY_EXCEEDED, lambda: store.inventory("session_1"), )

    def test_sibling_debug_logs_are_never_scanned_modified_or_counted(self) -> None:
        workspace = initialized_workspace(self)
        debug = workspace.path("debug_logs")
        provider = debug / "provider_error_responses"
        provider.mkdir()
        (provider / "legitimate.json").write_bytes(b'{"body":"PRIVATE"}\n')
        (provider / "malformed").write_bytes(b"\xffPRIVATE")
        runtime = debug / "runtime.jsonl"
        runtime.write_bytes(b"not json\nPRIVATE\n")
        huge = provider / "huge.bin"
        with huge.open("wb") as handle:
            handle.truncate(17_000_000)
        before = {
            path.relative_to(debug): (path.stat().st_mode, path.stat().st_size)
            for path in debug.rglob("*")
        }
        store = WorkspaceAgentExchangeStore(workspace)
        store.append(_input())
        self.assertEqual(len(_decoded_snapshot(store)), 1)
        after = {
            path.relative_to(debug): (path.stat().st_mode, path.stat().st_size)
            for path in debug.rglob("*")
            if "agent_exchanges" not in path.parts
        }
        expected = {
            key: value
            for key, value in before.items()
            if "agent_exchanges" not in key.parts
        }
        self.assertEqual(after, expected)
        self.assertEqual(runtime.read_bytes(), b"not json\nPRIVATE\n")
        self.assertEqual((provider / "malformed").read_bytes(), b"\xffPRIVATE")


class AgentExchangeStorageFaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.store = WorkspaceAgentExchangeStore(self.workspace)
        self.store.append(_input(exchange_id="first"))

    def test_partial_segment_write_rolls_back_and_releases_every_lock(self) -> None:
        before = _active_path(self.workspace).read_bytes()
        original = storage_module._write_all
        calls = 0

        def partial_then_fail(descriptor: int, payload: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                os.write(descriptor, payload[:17])
                raise OSError(errno.EIO, "PRIVATE partial write")
            original(descriptor, payload)

        with mock.patch.object( storage_module, "_write_all", side_effect=partial_then_fail, ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(_input(exchange_id="failed")),
            )
        self.assertEqual(_active_path(self.workspace).read_bytes(), before)
        receipt = self.store.append(_input(exchange_id="after"))
        self.assertEqual((receipt.workspace_sequence, receipt.sequence), (3, 3))
        self.assertEqual( [record["sequence"] for record in _decoded_snapshot(self.store)], [1, 3], )

    def test_primary_capacity_failure_precedes_cleanup_io_failure(self) -> None:
        original_release = storage_module._release_advisory_lock

        def release_then_fail(descriptor: int) -> None:
            original_release(descriptor)
            raise OSError(errno.EIO, "PRIVATE cleanup failure")

        with (
            mock.patch.object(
                storage_module,
                "_append_segment_line",
                side_effect=OSError(errno.ENOSPC, "PRIVATE capacity failure"),
            ),
            mock.patch.object( storage_module, "_release_advisory_lock", side_effect=release_then_fail, ),
        ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.CAPACITY_EXCEEDED,
                lambda: self.store.append(_input(exchange_id="failed")),
            )
        self.assertEqual(self.store.append(_input(exchange_id="after")).sequence, 3)

    def test_cleanup_only_failure_is_fixed_and_persisted_record_remains(self) -> None:
        original_release = storage_module._release_advisory_lock

        def release_then_fail(descriptor: int) -> None:
            original_release(descriptor)
            raise OSError(errno.EIO, "PRIVATE cleanup failure")

        with mock.patch.object( storage_module, "_release_advisory_lock", side_effect=release_then_fail, ):
            error = _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(_input(exchange_id="persisted")),
            )
        self.assertNotIn("PRIVATE", str(error))
        self.assertEqual(
            [record["exchange_id"] for record in _decoded_snapshot(self.store)],
            ["first", "persisted"],
        )
        self.assertEqual(self.store.append(_input(exchange_id="after")).sequence, 3)

    def test_control_exception_precedes_cleanup_failure_and_resources_release(
        self,
    ) -> None:
        original_release = storage_module._release_advisory_lock

        def release_then_fail(descriptor: int) -> None:
            original_release(descriptor)
            raise OSError(errno.EIO, "PRIVATE cleanup failure")

        with (
            mock.patch.object(
                storage_module,
                "_append_under_guard",
                side_effect=KeyboardInterrupt("PRIVATE control"),
            ),
            mock.patch.object( storage_module, "_release_advisory_lock", side_effect=release_then_fail, ),
            self.assertRaisesRegex(KeyboardInterrupt, "PRIVATE control"),
        ):
            self.store.append(_input(exchange_id="interrupted"))
        self.assertEqual(self.store.append(_input(exchange_id="after")).sequence, 2)

    def test_segment_fsync_failure_rolls_back_and_preserves_prior_record(self) -> None:
        before = _active_path(self.workspace).read_bytes()
        original = os.fsync
        calls = 0

        def fail_fifth(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 5:
                raise OSError(errno.EIO, "PRIVATE fsync")
            original(descriptor)

        with mock.patch.object(os, "fsync", side_effect=fail_fifth):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(_input(exchange_id="failed")),
            )
        self.assertEqual(_active_path(self.workspace).read_bytes(), before)
        self.assertEqual(self.store.append(_input(exchange_id="after")).sequence, 3)

    def test_stream_state_and_rotation_failures_leave_permitted_gaps(self) -> None:
        original_replace = storage_module._atomic_replace_leaf

        def fail_stream_state(*args: object, **kwargs: object) -> None:
            if str(kwargs.get("filename", "")).endswith(".sequence.json"):
                raise OSError(errno.EIO, "PRIVATE state")
            original_replace(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object( storage_module, "_atomic_replace_leaf", side_effect=fail_stream_state, ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(_input(exchange_id="state_failed")),
            )
        after_state = self.store.append(_input(exchange_id="state_after"))
        self.assertEqual((after_state.workspace_sequence, after_state.sequence), (3, 2))

        with (
            mock.patch.object(storage_module, "_SEGMENT_MAX_BYTES", 1_100),
            mock.patch.object( storage_module, "_rotate_stream", side_effect=OSError(errno.EIO, "PRIVATE rotate"), ),
        ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append( _input(exchange_id="rotation_failed", content="z" * 500) ),
            )
        with mock.patch.object(storage_module, "_SEGMENT_MAX_BYTES", 1_100):
            recovered = self.store.append( _input(exchange_id="rotation_after", content="z" * 500) )
            self.assertGreater( recovered.workspace_sequence, after_state.workspace_sequence )
            sequences = [record["sequence"] for record in _decoded_snapshot(self.store)]
            self.assertEqual(sequences, sorted(set(sequences)))

    def test_prune_and_retained_suffix_replace_failures_recover_next_append(
        self,
    ) -> None:
        original_prune = storage_module._prune_to_total

        def fail_over_capacity(
            directories: object,
            snapshot: object,
        ) -> None:
            if snapshot.total_jsonl_bytes > 1_400:  # type: ignore[attr-defined]
                raise OSError(errno.EIO, "PRIVATE prune")
            original_prune(directories, snapshot)  # type: ignore[arg-type]

        with (
            mock.patch.object( storage_module, "AGENT_EXCHANGE_TOTAL_MAX_BYTES", 1_400, ),
            mock.patch.object( storage_module, "_prune_to_total", side_effect=fail_over_capacity, ),
        ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(
                    _input(exchange_id="prune_failed", content="p" * 500)
                ),
            )
        raw = _active_path(self.workspace).read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(len(raw.splitlines()), 2)
        with mock.patch.object( storage_module, "AGENT_EXCHANGE_TOTAL_MAX_BYTES", 1_400, ):
            self.store.append(_input(exchange_id="prune_after", content="p" * 500))

        original_replace = storage_module._atomic_replace_leaf

        def fail_suffix(*args: object, **kwargs: object) -> None:
            if str(kwargs.get("pending_filename", "")).endswith(".segment.pending"):
                raise OSError(errno.EIO, "PRIVATE replace")
            original_replace(*args, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.object(storage_module, "_SEGMENT_MAX_BYTES", 4_000),
            mock.patch.object( storage_module, "AGENT_EXCHANGE_TOTAL_MAX_BYTES", 1_400, ),
            mock.patch.object( storage_module, "_atomic_replace_leaf", side_effect=fail_suffix, ),
        ):
            _assert_storage_failure(
                self,
                AgentExchangeFailure.IO_FAILED,
                lambda: self.store.append(
                    _input(exchange_id="replace_failed", content="q" * 500)
                ),
            )
        with (
            mock.patch.object(storage_module, "_SEGMENT_MAX_BYTES", 4_000),
            mock.patch.object( storage_module, "AGENT_EXCHANGE_TOTAL_MAX_BYTES", 1_400, ),
        ):
            self.store.append(_input(exchange_id="replace_after", content="q" * 500))


if __name__ == "__main__":
    unittest.main()
