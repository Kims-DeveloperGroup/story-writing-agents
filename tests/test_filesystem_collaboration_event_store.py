"""Runtime verification for the v2 filesystem collaboration-event store."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import threading
import unittest
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as conversation_storage
import story_writing_agents.conversation_storage.filesystem as filesystem_module
import story_writing_agents.workspace as workspace_facade
from story_writing_agents.conversation_storage.composition import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
    FilesystemConversationCatalog,
)
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendStatus,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationStepState,
    ConversationEvent,
    ConversationEventKind,
    collaboration_batch_digest,
    decode_collaboration_batch,
    encode_collaboration_batch,
    encode_collaboration_event,
    project_collaboration_history,
)
from story_writing_agents.errors import WorkspaceFormatError, WorkspaceSecurityError
from story_writing_agents.workspace.core import Workspace

from tests.collaboration_contract_fixture import (
    SESSION_ID,
    contract_batches,
    measured_batch,
)
from tests.helpers import pristine_initialized_workspace


def _object_effect_patch(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)

@dataclass(slots=True)
class _Harness:
    workspace: Workspace
    catalog: FilesystemConversationCatalog
    conversation_id: str
    store: FilesystemCollaborationEventStore
    history_path: Path
    lock_path: Path


@contextmanager
def _harness(test_case: unittest.TestCase):
    workspace = pristine_initialized_workspace(test_case)
    catalog = FilesystemConversationCatalog(workspace)
    metadata = catalog.create(SESSION_ID, "Filesystem v2 contract")
    directory = (
        workspace.root
        / "discussion_logs"
        / SESSION_ID
        / "interactive"
        / "conversations"
        / metadata.conversation_id
    )
    yield _Harness(
        workspace=workspace,
        catalog=catalog,
        conversation_id=metadata.conversation_id,
        store=FilesystemCollaborationEventStore(workspace, catalog=catalog),
        history_path=directory / "collaboration-history.jsonl",
        lock_path=directory / ".collaboration-history.lock",
    )


def _encode_history(events: tuple[CollaborationEvent, ...]) -> bytes:
    return b"".join(encode_collaboration_event(event) + b"\n" for event in events)


def _append_prefix(
    store: FilesystemCollaborationEventStore,
    batches: dict[str, CollaborationEventBatch],
    names: tuple[str, ...],
) -> None:
    for name in names:
        receipt = store.append_batch(batches[name])
        if receipt.status is not CollaborationAppendStatus.APPENDED:
            raise AssertionError(f"prefix batch was not appended: {name}")


def _process_append_worker(root: str, payload: bytes, start, results) -> None:
    """Append in one spawned process and return content-free outcome fields."""

    batch = decode_collaboration_batch(payload)
    start.wait()
    try:
        receipt = FilesystemCollaborationEventStore(root).append_batch(batch)
    except WorkspaceFormatError as exc:
        results.put(("workspace_rejected", str(exc)))
    except CollaborationAppendError as exc:
        results.put(("failure", exc.failure.value))
    except BaseException as exc:  # pragma: no cover - diagnostic branch
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(
            (
                "receipt",
                receipt.status.value,
                receipt.batch_id,
                receipt.batch_digest,
                receipt.first_sequence,
                receipt.last_sequence,
                receipt.timestamps,
            )
        )


class FilesystemCollaborationEventStorePersistenceTests(unittest.TestCase):
    def test_construction_absent_read_and_first_append_are_exact_and_isolated(
        self,
    ) -> None:
        with _harness(self) as value:
            before = tuple(
                sorted(
                    path.relative_to(value.workspace.root)
                    for path in value.workspace.root.rglob("*")
                )
            )
            stores = (
                FilesystemCollaborationEventStore(value.workspace.root),
                FilesystemCollaborationEventStore(os.fspath(value.workspace.root)),
                FilesystemCollaborationEventStore(value.workspace),
                FilesystemCollaborationEventStore(copy(value.workspace)),
            )
            self.assertEqual(
                before,
                tuple(
                    sorted(
                        path.relative_to(value.workspace.root)
                        for path in value.workspace.root.rglob("*")
                    )
                ),
            )
            for store in stores:
                self.assertEqual(store.read(SESSION_ID, value.conversation_id), ())
                self.assertEqual(
                    store.read_context(SESSION_ID, value.conversation_id),
                    project_collaboration_history(()),
                )
            self.assertFalse(value.history_path.exists())
            self.assertFalse(value.lock_path.exists())

            batch = measured_batch(contract_batches(value.conversation_id)["proposal"])
            receipt = value.store.append_batch(batch)
            events = value.store.read(SESSION_ID, value.conversation_id)

            self.assertIs(receipt.status, CollaborationAppendStatus.APPENDED)
            self.assertEqual(
                tuple(event.sequence for event in events),
                tuple(range(1, len(batch.events) + 1)),
            )
            self.assertEqual(
                tuple(event.batch_ordinal for event in events),
                tuple(range(len(batch.events))),
            )
            self.assertTrue(
                all(event.batch_size == len(batch.events) for event in events)
            )
            digest = collaboration_batch_digest(batch)
            self.assertTrue(all(event.batch_digest == digest for event in events))
            for event in events:
                parsed = datetime.fromisoformat(event.timestamp)
                self.assertEqual(parsed.utcoffset(), timedelta(0))
                self.assertTrue(event.timestamp.endswith("+00:00"))
            self.assertEqual(
                (
                    receipt.batch_id,
                    receipt.batch_digest,
                    receipt.first_sequence,
                    receipt.last_sequence,
                    receipt.timestamps,
                ),
                (
                    batch.batch_id,
                    digest,
                    1,
                    len(events),
                    tuple(event.timestamp for event in events),
                ),
            )
            self.assertEqual(value.history_path.read_bytes(), _encode_history(events))
            self.assertTrue(value.lock_path.is_file())
            self.assertFalse(value.history_path.with_name("transcript.jsonl").exists())
            self.assertEqual(
                value.store.read_context(SESSION_ID, value.conversation_id),
                project_collaboration_history(events),
            )
            self.assertEqual(
                FilesystemCollaborationEventStore(copy(value.workspace)).read(
                    SESSION_ID,
                    value.conversation_id,
                ),
                events,
            )

    def test_catalog_failures_and_wrong_identity_do_not_create_history(self) -> None:
        with _harness(self) as value:
            batch = contract_batches(value.conversation_id)["proposal"]
            wrong = SimpleNamespace(
                session_id=SESSION_ID,
                conversation_id="different_conversation",
            )
            store = FilesystemCollaborationEventStore(
                value.workspace,
                catalog=mock.Mock(get=mock.Mock(return_value=wrong)),
            )
            with self.assertRaises(WorkspaceFormatError):
                store.append_batch(batch)
            self.assertFalse(value.history_path.exists())
            self.assertFalse(value.lock_path.exists())

            failure = RuntimeError("catalog unavailable")
            store = FilesystemCollaborationEventStore(
                value.workspace,
                catalog=mock.Mock(get=mock.Mock(side_effect=failure)),
            )
            with self.assertRaises(RuntimeError) as raised:
                store.read(SESSION_ID, value.conversation_id)
            self.assertIs(raised.exception, failure)
            self.assertFalse(value.history_path.exists())

    def test_retry_precedes_tail_cas_and_semantic_failures_preserve_bytes(
        self,
    ) -> None:
        with _harness(self) as value:
            batches = contract_batches(value.conversation_id)
            original = value.store.append_batch(batches["proposal"])
            value.store.append_batch(batches["confirmation"])
            committed = value.history_path.read_bytes()

            retry = value.store.append_batch(batches["proposal"])
            self.assertIs(retry.status, CollaborationAppendStatus.ALREADY_COMMITTED)
            self.assertEqual(
                (
                    retry.batch_digest,
                    retry.first_sequence,
                    retry.last_sequence,
                    retry.timestamps,
                ),
                (
                    original.batch_digest,
                    original.first_sequence,
                    original.last_sequence,
                    original.timestamps,
                ),
            )

            proposal = batches["proposal"]
            drift = CollaborationEventBatch(
                proposal.batch_id,
                proposal.expected_last_sequence,
                (
                    replace(proposal.events[0], event_id="proposal_event_drift"),
                    proposal.events[1],
                ),
            )
            reused = CollaborationEventBatch(
                "batch_reused_event",
                len(value.store.read(SESSION_ID, value.conversation_id)),
                (proposal.events[0],),
            )
            stale = CollaborationEventBatch(
                "batch_stale",
                0,
                (replace(proposal.events[0], event_id="fresh_stale_event"),),
            )
            confirmation = batches["confirmation"]
            invalid = CollaborationEventBatch(
                "batch_invalid_transition",
                confirmation.expected_last_sequence + len(confirmation.events),
                (replace(confirmation.events[0], event_id="late_confirmation"),),
            )
            for candidate, expected in (
                (drift, CollaborationAppendFailure.BATCH_ID_CONFLICT),
                (reused, CollaborationAppendFailure.EVENT_ID_CONFLICT),
                (stale, CollaborationAppendFailure.STALE_SEQUENCE),
                (invalid, CollaborationAppendFailure.INVALID_TRANSITION),
            ):
                with self.subTest(failure=expected):
                    with self.assertRaises(CollaborationAppendError) as raised:
                        value.store.append_batch(candidate)
                    self.assertIs(raised.exception.failure, expected)
                    self.assertEqual(value.history_path.read_bytes(), committed)

    def test_all_required_co_batches_append_and_split_forms_are_atomic(
        self,
    ) -> None:
        with _harness(self) as value:
            batches = contract_batches(value.conversation_id)
            _append_prefix(
                value.store,
                batches,
                (
                    "proposal",
                    "confirmation",
                    "running",
                    "calls",
                    "result",
                    "completion",
                ),
            )
            self.assertIs(
                value.store.read_context(
                    SESSION_ID, value.conversation_id
                ).current_state,
                CollaborationStepState.COMPLETED,
            )

        with _harness(self) as value:
            batches = contract_batches(value.conversation_id)
            _append_prefix(
                value.store,
                batches,
                ("proposal", "confirmation", "running", "calls", "unknown"),
            )
            self.assertIs(
                value.store.read_context(
                    SESSION_ID, value.conversation_id
                ).current_state,
                CollaborationStepState.OUTCOME_UNKNOWN,
            )

        split_cases = (
            ((), "proposal", slice(0, 1)),
            (("proposal",), "confirmation", slice(0, 1)),
            (("proposal", "confirmation"), "running", slice(0, -1)),
            (
                ("proposal", "confirmation", "running", "calls"),
                "result",
                slice(-1, None),
            ),
            (
                ("proposal", "confirmation", "running", "calls", "result"),
                "completion",
                slice(-1, None),
            ),
            (
                ("proposal", "confirmation", "running", "calls"),
                "unknown",
                slice(-1, None),
            ),
        )
        for prefix, name, selected in split_cases:
            with self.subTest(batch=name), _harness(self) as value:
                batches = contract_batches(value.conversation_id)
                _append_prefix(value.store, batches, prefix)
                before = (
                    value.history_path.read_bytes()
                    if value.history_path.exists()
                    else None
                )
                source = batches[name]
                partial_events = source.events[selected]
                if name == "proposal":
                    partial_events = tuple(
                        event
                        for event in source.events
                        if event.event_id == "proposal_event"
                    )
                if name == "running":
                    partial_events = tuple(
                        event
                        for event in source.events
                        if event.event_id != "dispatch_assignment_character"
                    )
                partial = CollaborationEventBatch(
                    f"partial_{name}",
                    source.expected_last_sequence,
                    partial_events,
                )
                with self.assertRaises(CollaborationAppendError) as raised:
                    value.store.append_batch(partial)
                self.assertIs(
                    raised.exception.failure,
                    CollaborationAppendFailure.INVALID_TRANSITION,
                )
                after = (
                    value.history_path.read_bytes()
                    if value.history_path.exists()
                    else None
                )
                self.assertEqual(after, before)
                self.assertEqual(list(value.history_path.parent.glob("*.tmp")), [])


class FilesystemCollaborationEventStoreCorruptionTests(unittest.TestCase):
    def test_malformed_and_metadata_corruption_fail_closed_without_rewrite(
        self,
    ) -> None:
        for name in (
            "empty",
            "malformed",
            "duplicate_fields",
            "missing_final_lf",
            "blank_record",
            "invalid_utf8",
            "schema_v1",
            "partial_batch",
            "sequence_gap",
            "duplicate_ordinal",
            "size_drift",
            "digest_drift",
            "event_reuse",
            "batch_drift",
            "wrong_identity",
        ):
            with self.subTest(corruption=name), _harness(self) as value:
                batch = contract_batches(value.conversation_id)["proposal"]
                value.store.append_batch(batch)
                history = value.store.read(SESSION_ID, value.conversation_id)
                first, second = history[:2]
                v1 = ConversationEvent(
                    schema_version=1,
                    session_id=SESSION_ID,
                    conversation_id=value.conversation_id,
                    turn_id="legacy_turn",
                    sequence=1,
                    timestamp="2026-09-01T00:00:00+00:00",
                    sender="user",
                    recipient="lead_orchestrator",
                    kind=ConversationEventKind.USER_MESSAGE,
                    content="Legacy transcript content.",
                )
                corruptions = {
                    "empty": b"",
                    "malformed": b"{not-json}\n",
                    "duplicate_fields": (b'{"schema_version":2,"schema_version":2}\n'),
                    "missing_final_lf": encode_collaboration_event(first),
                    "blank_record": encode_collaboration_event(first) + b"\n\n",
                    "invalid_utf8": b"\xff\n",
                    "schema_v1": (
                        json.dumps(
                            v1.to_dict(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                        + b"\n"
                    ),
                    "partial_batch": _encode_history((first,)),
                    "sequence_gap": _encode_history(
                        (replace(first, sequence=2), *history[1:])
                    ),
                    "duplicate_ordinal": _encode_history(
                        (first, replace(second, batch_ordinal=0), *history[2:])
                    ),
                    "size_drift": _encode_history(
                        (replace(first, batch_size=3), *history[1:])
                    ),
                    "digest_drift": _encode_history(
                        (
                            replace(
                                first,
                                batch_digest="sha256:" + "0" * 64,
                            ),
                            *history[1:],
                        )
                    ),
                    "event_reuse": _encode_history(
                        (
                            first,
                            replace(second, event_id=first.event_id),
                            *history[2:],
                        )
                    ),
                    "batch_drift": _encode_history(
                        (
                            first,
                            replace(second, batch_id="different_batch"),
                            *history[2:],
                        )
                    ),
                    "wrong_identity": _encode_history(
                        (
                            replace(
                                first,
                                conversation_id="different_conversation",
                            ),
                            *history[1:],
                        )
                    ),
                }
                payload = corruptions[name]
                value.history_path.write_bytes(payload)
                before = value.history_path.read_bytes()
                with self.assertRaises(WorkspaceFormatError):
                    value.store.read(SESSION_ID, value.conversation_id)
                with self.assertRaises(WorkspaceFormatError):
                    value.store.read_context(SESSION_ID, value.conversation_id)
                with self.assertRaises(CollaborationAppendError) as raised:
                    value.store.append_batch(batch)
                self.assertIs(
                    raised.exception.failure,
                    CollaborationAppendFailure.CORRUPT_HISTORY,
                )
                self.assertEqual(value.history_path.read_bytes(), before)
                self.assertEqual(list(value.history_path.parent.glob("*.tmp")), [])


class FilesystemCollaborationEventStoreConcurrencyTests(unittest.TestCase):
    @staticmethod
    def _different_batch(batch: CollaborationEventBatch) -> CollaborationEventBatch:
        return CollaborationEventBatch(
            "batch_proposal_competitor",
            batch.expected_last_sequence,
            tuple(
                replace(event, event_id=f"{event.event_id}_competitor")
                for event in batch.events
            ),
        )

    def _run_thread_race(self, *, identical: bool) -> list[object]:
        with _harness(self) as value:
            first = contract_batches(value.conversation_id)["proposal"]
            second = first if identical else self._different_batch(first)
            barrier = threading.Barrier(3)
            results: Queue[object] = Queue()

            def append(batch: CollaborationEventBatch) -> None:
                barrier.wait()
                try:
                    results.put(
                        FilesystemCollaborationEventStore(value.workspace).append_batch(
                            batch
                        )
                    )
                except BaseException as exc:
                    results.put(exc)

            threads = (
                threading.Thread(target=append, args=(first,)),
                threading.Thread(target=append, args=(second,)),
            )
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(10)
                self.assertFalse(thread.is_alive())
            observed = [results.get(timeout=2), results.get(timeout=2)]
            self.assertEqual(list(value.history_path.parent.glob("*.tmp")), [])
            self.assertEqual(
                tuple(
                    event.sequence
                    for event in value.store.read(SESSION_ID, value.conversation_id)
                ),
                tuple(range(1, len(first.events) + 1)),
            )
            return observed

    def test_two_store_threads_serialize_different_and_identical_candidates(
        self,
    ) -> None:
        different = self._run_thread_race(identical=False)
        outcomes = []
        for item in different:
            if isinstance(item, CollaborationAppendError):
                outcomes.append(item.failure.value)
            else:
                outcomes.append(item.status.value)  # type: ignore[union-attr]
        self.assertEqual(sorted(outcomes), ["APPENDED", "stale_sequence"])

        identical = self._run_thread_race(identical=True)
        receipts = [item for item in identical if not isinstance(item, BaseException)]
        self.assertEqual(
            sorted(receipt.status.value for receipt in receipts),
            ["ALREADY_COMMITTED", "APPENDED"],
        )
        self.assertEqual(receipts[0].timestamps, receipts[1].timestamps)

    def _run_process_race(self, *, identical: bool) -> list[tuple[object, ...]]:
        with _harness(self) as value:
            first = contract_batches(value.conversation_id)["proposal"]
            second = first if identical else self._different_batch(first)
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            results = context.Queue()
            processes = (
                context.Process(
                    target=_process_append_worker,
                    args=(
                        os.fspath(value.workspace.root),
                        encode_collaboration_batch(first),
                        start,
                        results,
                    ),
                ),
                context.Process(
                    target=_process_append_worker,
                    args=(
                        os.fspath(value.workspace.root),
                        encode_collaboration_batch(second),
                        start,
                        results,
                    ),
                ),
            )
            try:
                for process in processes:
                    process.start()
                start.set()
                observed = [results.get(timeout=20), results.get(timeout=20)]
                for process in processes:
                    process.join(20)
                    self.assertFalse(process.is_alive())
                    self.assertEqual(process.exitcode, 0)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
                results.close()
                results.join_thread()
            self.assertEqual(
                tuple(
                    event.sequence
                    for event in value.store.read(SESSION_ID, value.conversation_id)
                ),
                tuple(range(1, len(first.events) + 1)),
            )
            self.assertEqual(list(value.history_path.parent.glob("*.tmp")), [])
            return observed

    def test_spawned_path_only_stores_serialize_without_workspace_rejection(
        self,
    ) -> None:
        different = self._run_process_race(identical=False)
        self.assertEqual(sorted(item[1] for item in different), ["APPENDED", "stale_sequence"])


class FilesystemCollaborationEventStoreFailureTests(unittest.TestCase):
    def test_file_fsync_and_replace_failures_preserve_old_history_and_clean_temp(
        self,
    ) -> None:
        for failure in ("file_fsync", "replace"):
            with self.subTest(failure=failure), _harness(self) as value:
                batches = contract_batches(value.conversation_id)
                value.store.append_batch(batches["proposal"])
                before = value.history_path.read_bytes()
                if failure == "file_fsync":
                    real_fsync = filesystem_module.os.fsync

                    def fail_regular(descriptor: int) -> None:
                        if stat.S_ISREG(os.fstat(descriptor).st_mode):
                            raise OSError("file fsync failed")
                        real_fsync(descriptor)

                    patcher = _object_effect_patch(filesystem_module.os, "fsync", fail_regular)
                else:
                    patcher = _object_effect_patch(filesystem_module.os, "replace", OSError("replace failed"))
                with patcher, self.assertRaises(OSError):
                    value.store.append_batch(batches["confirmation"])
                self.assertEqual(value.history_path.read_bytes(), before)
                self.assertEqual(list(value.history_path.parent.glob("*.tmp")), [])

    def test_directory_fsync_failure_is_uncertain_and_retry_resolves_original(
        self,
    ) -> None:
        with _harness(self) as value:
            batch = contract_batches(value.conversation_id)["proposal"]
            real_fsync = filesystem_module.os.fsync

            def fail_directory(descriptor: int) -> None:
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("directory fsync failed")
                real_fsync(descriptor)

            with (
                _object_effect_patch(filesystem_module.os, "fsync", fail_directory),
                self.assertRaises(OSError),
            ):
                value.store.append_batch(batch)
            committed = value.store.read(SESSION_ID, value.conversation_id)
            self.assertEqual(len(committed), len(batch.events))
            retry = value.store.append_batch(batch)
            self.assertIs(retry.status, CollaborationAppendStatus.ALREADY_COMMITTED)
            self.assertEqual(
                retry.timestamps,
                tuple(event.timestamp for event in committed),
            )

    def test_lock_unsupported_unsafe_acquire_and_unlock_fail_closed_or_retry(
        self,
    ) -> None:
        with _harness(self) as value:
            batch = contract_batches(value.conversation_id)["proposal"]
            with mock.patch.object(filesystem_module, "_fcntl", None):
                with self.assertRaises(OSError):
                    value.store.append_batch(batch)
            self.assertFalse(value.history_path.exists())
            self.assertFalse(value.lock_path.exists())

        with _harness(self) as value:
            batch = contract_batches(value.conversation_id)["proposal"]
            outside = value.workspace.root / "outside-lock-target"
            outside.write_text("untouched", encoding="utf-8")
            value.lock_path.symlink_to(outside)
            with self.assertRaises(WorkspaceSecurityError):
                value.store.append_batch(batch)
            self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
            self.assertFalse(value.history_path.exists())

        with _harness(self) as value:
            batch = contract_batches(value.conversation_id)["proposal"]
            assert filesystem_module._fcntl is not None
            real_flock = filesystem_module._fcntl.flock

            def fail_acquire(descriptor: int, operation: int) -> None:
                if operation == filesystem_module._fcntl.LOCK_EX:
                    raise OSError("lock acquisition failed")
                real_flock(descriptor, operation)

            with (
                _object_effect_patch(filesystem_module._fcntl, "flock", fail_acquire),
                self.assertRaises(OSError),
            ):
                value.store.append_batch(batch)
            self.assertFalse(value.history_path.exists())

        with _harness(self) as value:
            batch = contract_batches(value.conversation_id)["proposal"]
            assert filesystem_module._fcntl is not None
            real_flock = filesystem_module._fcntl.flock

            def fail_unlock(descriptor: int, operation: int) -> None:
                if operation == filesystem_module._fcntl.LOCK_UN:
                    raise OSError("lock release failed")
                real_flock(descriptor, operation)

            with (
                _object_effect_patch(filesystem_module._fcntl, "flock", fail_unlock),
                self.assertRaises(OSError),
            ):
                value.store.append_batch(batch)
            retry = value.store.append_batch(batch)
            self.assertIs(retry.status, CollaborationAppendStatus.ALREADY_COMMITTED)


class FilesystemCollaborationEventStoreBoundaryTests(unittest.TestCase):
    def test_direct_module_export_only_and_v2_composition_are_exact(self) -> None:
        self.assertEqual(
            filesystem_module.__all__,
            [
                "FilesystemConversationCatalog",
                "FilesystemCollaborationEventStore",
            ],
        )
        self.assertEqual(
            FilesystemCollaborationEventStore.__module__,
            filesystem_module.__name__,
        )
        self.assertFalse(
            hasattr(story_writing_agents, "FilesystemCollaborationEventStore")
        )
        self.assertFalse(hasattr(workspace_facade, "FilesystemCollaborationEventStore"))
        self.assertFalse(
            hasattr(conversation_storage, "FilesystemCollaborationEventStore")
        )
        self.assertFalse(
            hasattr(story_writing_agents, "FilesystemConversationEventStore")
        )
        self.assertFalse(hasattr(workspace_facade, "FilesystemConversationEventStore"))

        with _harness(self) as value:
            services = compose_filesystem_conversation_storage(value.workspace)
            self.assertIsInstance(
                services.event_store,
                FilesystemCollaborationEventStore,
            )
            self.assertTrue(callable(services.event_store.append_batch))
            self.assertFalse(hasattr(services.event_store, "append"))

    def test_schema_version_and_module_have_no_dynamic_forwarder(self) -> None:
        self.assertEqual(COLLABORATION_EVENT_SCHEMA_VERSION, 2)
        self.assertNotIn("__getattr__", filesystem_module.__dict__)


if __name__ == "__main__":
    unittest.main()
