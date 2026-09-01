"""Focused persistence checks for the schema-v2 SQLite collaboration store."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sqlite3
import stat
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from queue import Queue
from typing import Any
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
import story_writing_agents.workspace as workspace_package
from story_writing_agents import (
    WorkspaceFormatError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationStepState,
    ConversationMetadata,
    collaboration_batch_digest,
    decode_collaboration_batch,
    encode_collaboration_batch,
    encode_collaboration_event,
    project_collaboration_history,
)
from story_writing_agents.conversation_storage import sqlite as sqlite_module
from story_writing_agents.conversation_storage import (
    ConversationStorageUnavailableError,
)
from story_writing_agents.conversation_storage import (
    sqlite_database as sqlite_database_module,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLiteCollaborationEventStore,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
)
from story_writing_agents.workspace.core import Workspace

from tests.collaboration_contract_fixture import contract_batches
from tests.sqlite_conversation_storage_fixture import (
    database_path,
    initialized_workspace,
    provision,
    raw_connection,
    storage_snapshot,
)


_SQLITE_EXPORTS = (
    "SQLITE_CONVERSATION_STORAGE_APPLICATION_ID",
    "SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS",
    "SQLITE_CONVERSATION_STORAGE_DATABASE_PATH",
    "SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION",
    "SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION",
    "SQLiteConversationArchiveAdapter",
    "SQLiteConversationCatalog",
    "SQLiteCollaborationEventStore",
    "SQLiteCollaborationHistorySearch",
    "compose_sqlite_conversation_archive",
    "compose_sqlite_conversation_storage",
    "preflight_sqlite_conversation_storage",
    "probe_sqlite_conversation_storage_runtime",
    "provision_sqlite_conversation_storage",
)


def _append_outcome(
    workspace_root: str,
    batch: CollaborationEventBatch,
) -> tuple[object, ...]:
    store = SQLiteCollaborationEventStore(workspace_root)
    try:
        receipt = store.append_batch(batch)
    except CollaborationAppendError as exc:
        return ("error", exc.failure.value)
    return (
        "receipt",
        receipt.status.value,
        receipt.batch_id,
        receipt.batch_digest,
        receipt.first_sequence,
        receipt.last_sequence,
        receipt.timestamps,
    )


def _spawn_append_worker(
    workspace_root: str,
    encoded_batch: bytes,
    start: Any,
    results: Any,
) -> None:
    start.wait()
    try:
        outcome = _append_outcome(
            workspace_root,
            decode_collaboration_batch(encoded_batch),
        )
    except BaseException as exc:
        results.put(("unexpected", type(exc).__name__, str(exc)))
        raise
    results.put(outcome)


class SQLiteCollaborationEventStoreFirstSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace, self.metadata, self.store = self.fresh_store()
        self.batch = contract_batches(self.metadata.conversation_id)["proposal"]

    def fresh_store(
        self,
    ) -> tuple[Workspace, ConversationMetadata, SQLiteCollaborationEventStore]:
        workspace = initialized_workspace(self)
        provision(workspace)
        services = compose_sqlite_conversation_storage(workspace)
        metadata = services.catalog.create("session_a", "Collaboration")
        return workspace, metadata, SQLiteCollaborationEventStore(workspace)

    def database_state(self, workspace: Workspace) -> tuple[object, ...]:
        with raw_connection(workspace) as connection:
            return (
                tuple(
                    connection.execute(
                        "SELECT * FROM conversation_storage_identity"
                    ).fetchall()
                ),
                tuple(
                    connection.execute(
                        "SELECT * FROM conversations ORDER BY session_id, "
                        "conversation_id"
                    ).fetchall()
                ),
                tuple(
                    connection.execute(
                        "SELECT * FROM conversation_events ORDER BY session_id, "
                        "conversation_id, sequence"
                    ).fetchall()
                ),
                tuple(
                    connection.execute(
                        "SELECT * FROM collaboration_events ORDER BY session_id, "
                        "conversation_id, sequence"
                    ).fetchall()
                ),
            )

    def collaboration_rows(
        self,
        workspace: Workspace,
    ) -> tuple[tuple[object, ...], ...]:
        with raw_connection(workspace) as connection:
            return tuple(
                connection.execute(
                    "SELECT * FROM collaboration_events ORDER BY sequence"
                ).fetchall()
            )

    @staticmethod
    def competing_batch(batch: CollaborationEventBatch) -> CollaborationEventBatch:
        return CollaborationEventBatch(
            batch_id="batch_proposal_competitor",
            expected_last_sequence=batch.expected_last_sequence,
            events=tuple(
                replace(event, event_id=f"{event.event_id}_competitor")
                for event in batch.events
            ),
        )

    def run_thread_race(
        self,
        *,
        identical: bool,
    ) -> tuple[list[tuple[object, ...]], Workspace]:
        workspace, metadata, _store = self.fresh_store()
        first = contract_batches(metadata.conversation_id)["proposal"]
        second = first if identical else self.competing_batch(first)
        barrier = threading.Barrier(3)
        results: Queue[tuple[object, ...]] = Queue()

        def append(batch: CollaborationEventBatch) -> None:
            barrier.wait()
            results.put(_append_outcome(str(workspace.root), batch))

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
        return [results.get(timeout=2), results.get(timeout=2)], workspace

    def run_process_race(
        self,
        *,
        identical: bool,
    ) -> tuple[list[tuple[object, ...]], Workspace]:
        workspace, metadata, _store = self.fresh_store()
        first = contract_batches(metadata.conversation_id)["proposal"]
        second = first if identical else self.competing_batch(first)
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = (
            context.Process(
                target=_spawn_append_worker,
                args=(
                    str(workspace.root),
                    encode_collaboration_batch(first),
                    start,
                    results,
                ),
            ),
            context.Process(
                target=_spawn_append_worker,
                args=(
                    str(workspace.root),
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
            for process in processes:
                process.join(15)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            observed = [results.get(timeout=2), results.get(timeout=2)]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            results.close()
            results.join_thread()
        return observed, workspace

    def database_artifact(self, workspace: Workspace) -> tuple[object, ...]:
        target = database_path(workspace)
        with closing(
            sqlite3.connect(
                f"file:{target}?mode=ro",
                uri=True,
                isolation_level=None,
            )
        ) as connection:
            schema = (
                connection.execute("PRAGMA application_id").fetchone(),
                connection.execute("PRAGMA user_version").fetchone(),
                connection.execute("PRAGMA schema_version").fetchone(),
                tuple(
                    connection.execute(
                        "SELECT type, name, sql FROM sqlite_master "
                        "WHERE substr(name, 1, 7) != 'sqlite_' "
                        "ORDER BY type, name"
                    ).fetchall()
                ),
                tuple(
                    connection.execute(
                        "SELECT * FROM conversation_storage_identity"
                    ).fetchall()
                ),
            )
        metadata = target.stat()
        return (
            target.read_bytes(),
            (
                stat.S_IMODE(metadata.st_mode),
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                metadata.st_ino,
                metadata.st_nlink,
            ),
            schema,
        )

    def lexical_generation(self) -> int:
        with raw_connection(self.workspace) as connection:
            row = connection.execute(
                "SELECT lexical_generation FROM conversation_storage_identity "
                "WHERE singleton = 1"
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row[0]

    def test_fresh_direct_append_persists_exact_rows_and_refreshes_lexical_index(
        self,
    ) -> None:
        generation_before = self.lexical_generation()

        receipt = self.store.append_batch(self.batch)
        history = self.store.read("session_a", self.metadata.conversation_id)
        projection = self.store.read_context(
            "session_a",
            self.metadata.conversation_id,
        )

        self.assertIs(receipt.status, CollaborationAppendStatus.APPENDED)
        self.assertEqual(receipt.batch_id, self.batch.batch_id)
        self.assertEqual(receipt.batch_digest, collaboration_batch_digest(self.batch))
        self.assertEqual(receipt.first_sequence, 1)
        self.assertEqual(receipt.last_sequence, len(self.batch.events))
        self.assertEqual(
            receipt.timestamps, tuple(event.timestamp for event in history)
        )
        self.assertEqual(
            tuple(event.sequence for event in history),
            tuple(range(1, len(history) + 1)),
        )
        for timestamp in receipt.timestamps:
            parsed = datetime.fromisoformat(timestamp)
            self.assertIsNotNone(parsed.tzinfo)
            self.assertEqual(parsed.utcoffset(), UTC.utcoffset(parsed))
        self.assertEqual(projection, project_collaboration_history(history))

        with raw_connection(self.workspace) as connection:
            rows = connection.execute(
                "SELECT schema_version, event_id, session_id, conversation_id, "
                "turn_id, step_id, sequence, timestamp, batch_id, batch_ordinal, "
                "batch_size, batch_digest, event_bytes, typeof(event_bytes) "
                "FROM collaboration_events ORDER BY sequence"
            ).fetchall()
        self.assertEqual(len(rows), len(history))
        for row, event in zip(rows, history, strict=True):
            self.assertEqual(
                row,
                (
                    event.schema_version,
                    event.event_id,
                    event.session_id,
                    event.conversation_id,
                    event.turn_id,
                    event.step_id,
                    event.sequence,
                    event.timestamp,
                    event.batch_id,
                    event.batch_ordinal,
                    event.batch_size,
                    event.batch_digest,
                    encode_collaboration_event(event),
                    "blob",
                ),
            )
        self.assertEqual(self.lexical_generation(), generation_before + 1)

    def test_exact_retry_returns_original_receipt_and_one_physical_batch(
        self,
    ) -> None:
        first = self.store.append_batch(self.batch)
        retry = self.store.append_batch(self.batch)

        self.assertIs(first.status, CollaborationAppendStatus.APPENDED)
        self.assertIs(retry.status, CollaborationAppendStatus.ALREADY_COMMITTED)
        self.assertEqual(retry.batch_id, first.batch_id)
        self.assertEqual(retry.batch_digest, first.batch_digest)
        self.assertEqual(retry.first_sequence, first.first_sequence)
        self.assertEqual(retry.last_sequence, first.last_sequence)
        self.assertEqual(retry.timestamps, first.timestamps)
        with raw_connection(self.workspace) as connection:
            counts = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT batch_id) FROM collaboration_events"
            ).fetchone()
        self.assertEqual(counts, (len(self.batch.events), 1))

    def test_direct_only_export_and_v2_composition_are_exact(self) -> None:
        self.assertEqual(tuple(sqlite_module.__all__), _SQLITE_EXPORTS)
        self.assertEqual(len(_SQLITE_EXPORTS), 14)
        self.assertIs(
            sqlite_module.SQLiteCollaborationEventStore,
            SQLiteCollaborationEventStore,
        )
        self.assertEqual(
            SQLiteCollaborationEventStore.__module__,
            sqlite_module.__name__,
        )
        for boundary in (
            story_writing_agents,
            workspace_package,
            storage_package,
        ):
            with self.subTest(boundary=boundary.__name__):
                self.assertNotIn("SQLiteCollaborationEventStore", boundary.__all__)
                self.assertFalse(hasattr(boundary, "SQLiteCollaborationEventStore"))

        services = compose_sqlite_conversation_storage(self.workspace)
        self.assertIsInstance(services.event_store, SQLiteCollaborationEventStore)
        self.assertTrue(callable(services.event_store.append_batch))
        self.assertFalse(hasattr(services.event_store, "append"))

    def test_all_six_batch_families_append_and_every_split_rolls_back(self) -> None:
        completed = contract_batches(self.metadata.conversation_id)
        completed_names = (
            "proposal",
            "confirmation",
            "running",
            "result",
            "completion",
        )
        for name in completed_names:
            receipt = self.store.append_batch(completed[name])
            self.assertIs(receipt.status, CollaborationAppendStatus.APPENDED)
        completed_history = self.store.read(
            "session_a",
            self.metadata.conversation_id,
        )
        self.assertEqual(
            len(completed_history),
            sum(len(completed[name].events) for name in completed_names),
        )
        self.assertEqual(
            len(self.collaboration_rows(self.workspace)),
            len(completed_history),
        )
        self.assertIs(
            self.store.read_context(
                "session_a",
                self.metadata.conversation_id,
            ).current_state,
            CollaborationStepState.COMPLETED,
        )

        unknown_workspace, unknown_metadata, unknown_store = self.fresh_store()
        unknown = contract_batches(unknown_metadata.conversation_id)
        unknown_names = ("proposal", "confirmation", "running", "unknown")
        for name in unknown_names:
            receipt = unknown_store.append_batch(unknown[name])
            self.assertIs(receipt.status, CollaborationAppendStatus.APPENDED)
        unknown_history = unknown_store.read(
            "session_a",
            unknown_metadata.conversation_id,
        )
        self.assertEqual(
            len(unknown_history),
            sum(len(unknown[name].events) for name in unknown_names),
        )
        self.assertEqual(
            len(self.collaboration_rows(unknown_workspace)),
            len(unknown_history),
        )
        self.assertIs(
            unknown_store.read_context(
                "session_a",
                unknown_metadata.conversation_id,
            ).current_state,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )

        split_forms = (
            ((), "proposal", slice(0, 1)),
            (("proposal",), "confirmation", slice(0, 1)),
            (("proposal", "confirmation"), "running", slice(0, -1)),
            (
                ("proposal", "confirmation", "running"),
                "result",
                slice(-1, None),
            ),
            (
                ("proposal", "confirmation", "running", "result"),
                "completion",
                slice(-1, None),
            ),
            (
                ("proposal", "confirmation", "running"),
                "unknown",
                slice(-1, None),
            ),
        )
        for prefix, name, selected in split_forms:
            with self.subTest(split=name):
                workspace, metadata, store = self.fresh_store()
                batches = contract_batches(metadata.conversation_id)
                for prefix_name in prefix:
                    receipt = store.append_batch(batches[prefix_name])
                    self.assertIs(receipt.status, CollaborationAppendStatus.APPENDED)
                before = self.database_state(workspace)
                source = batches[name]
                partial_events = source.events[selected]
                if name == "running":
                    partial_events = tuple(
                        event
                        for event in source.events
                        if event.event_id != "dispatch_assignment_character"
                    )
                partial = CollaborationEventBatch(
                    batch_id=f"partial_{name}",
                    expected_last_sequence=source.expected_last_sequence,
                    events=partial_events,
                )

                with self.assertRaises(CollaborationAppendError) as raised:
                    store.append_batch(partial)

                self.assertIs(
                    raised.exception.failure,
                    CollaborationAppendFailure.INVALID_TRANSITION,
                )
                self.assertEqual(self.database_state(workspace), before)

    def test_late_retry_and_fixed_conflict_precedence_preserve_rows(self) -> None:
        batches = contract_batches(self.metadata.conversation_id)
        original = self.store.append_batch(batches["proposal"])
        self.store.append_batch(batches["confirmation"])
        committed = self.collaboration_rows(self.workspace)

        retry = self.store.append_batch(batches["proposal"])
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
        self.assertEqual(self.collaboration_rows(self.workspace), committed)

        proposal = batches["proposal"]
        batch_id_drift = CollaborationEventBatch(
            batch_id=proposal.batch_id,
            expected_last_sequence=proposal.expected_last_sequence,
            events=(
                replace(proposal.events[0], event_id="proposal_event_drift"),
                proposal.events[1],
            ),
        )
        event_id_reuse = CollaborationEventBatch(
            batch_id="batch_reused_event",
            expected_last_sequence=len(committed),
            events=(proposal.events[0],),
        )
        stale = CollaborationEventBatch(
            batch_id="batch_stale",
            expected_last_sequence=0,
            events=(replace(proposal.events[0], event_id="fresh_stale_event"),),
        )
        confirmation = batches["confirmation"]
        invalid = CollaborationEventBatch(
            batch_id="batch_invalid_transition",
            expected_last_sequence=len(committed),
            events=(
                replace(
                    confirmation.events[0],
                    event_id="late_confirmation",
                ),
            ),
        )
        for candidate, expected in (
            (batch_id_drift, CollaborationAppendFailure.BATCH_ID_CONFLICT),
            (event_id_reuse, CollaborationAppendFailure.EVENT_ID_CONFLICT),
            (stale, CollaborationAppendFailure.STALE_SEQUENCE),
            (invalid, CollaborationAppendFailure.INVALID_TRANSITION),
        ):
            with self.subTest(failure=expected):
                with self.assertRaises(CollaborationAppendError) as raised:
                    self.store.append_batch(candidate)
                self.assertIs(raised.exception.failure, expected)
                self.assertEqual(self.collaboration_rows(self.workspace), committed)

    def test_corrupt_rows_fail_closed_and_database_constraints_reject_duplicates(
        self,
    ) -> None:
        constraint_workspace, constraint_metadata, constraint_store = self.fresh_store()
        constraint_batches = contract_batches(constraint_metadata.conversation_id)
        constraint_store.append_batch(constraint_batches["proposal"])
        constraint_before = self.database_state(constraint_workspace)
        insert_sql = (
            "INSERT INTO collaboration_events VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        with raw_connection(constraint_workspace) as connection:
            rows = connection.execute(
                "SELECT * FROM collaboration_events ORDER BY sequence"
            ).fetchall()
            duplicate_event_id = list(rows[0])
            duplicate_event_id[2] = 3
            duplicate_event_id[8] = "distinct_batch"
            duplicate_event_id[9] = 0
            duplicate_event_id[10] = 1
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(insert_sql, duplicate_event_id)

            duplicate_ordinal = list(rows[0])
            duplicate_ordinal[2] = 3
            duplicate_ordinal[4] = "distinct_event"
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(insert_sql, duplicate_ordinal)

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE collaboration_events SET schema_version = 1 "
                    "WHERE sequence = 1"
                )
        self.assertEqual(
            self.database_state(constraint_workspace),
            constraint_before,
        )

        corruptions = (
            "partial_batch",
            "gapped_sequence",
            "ordinal_drift",
            "size_drift",
            "digest_drift",
            "event_id_drift",
            "batch_id_drift",
            "session_drift",
            "conversation_drift",
            "timestamp_drift",
            "noncanonical_blob",
            "schema_tag",
            "invalid_transition",
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                workspace, metadata, store = self.fresh_store()
                batches = contract_batches(metadata.conversation_id)
                if corruption == "invalid_transition":
                    draft = batches["proposal"].events[0]
                    invalid_batch = CollaborationEventBatch(
                        batch_id="stored_invalid_transition",
                        expected_last_sequence=0,
                        events=(draft,),
                    )
                    invalid_event = CollaborationEvent(
                        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
                        event_id=draft.event_id,
                        session_id=draft.session_id,
                        conversation_id=draft.conversation_id,
                        turn_id=draft.turn_id,
                        step_id=draft.step_id,
                        sequence=1,
                        timestamp="2026-09-01T00:00:00+00:00",
                        batch_id=invalid_batch.batch_id,
                        batch_ordinal=0,
                        batch_size=1,
                        batch_digest=collaboration_batch_digest(invalid_batch),
                        kind=draft.kind,
                        sender=draft.sender,
                        recipient=draft.recipient,
                        payload=draft.payload,
                    )
                    with raw_connection(workspace) as connection:
                        connection.execute(
                            insert_sql,
                            (
                                invalid_event.session_id,
                                invalid_event.conversation_id,
                                invalid_event.sequence,
                                invalid_event.schema_version,
                                invalid_event.event_id,
                                invalid_event.turn_id,
                                invalid_event.step_id,
                                invalid_event.timestamp,
                                invalid_event.batch_id,
                                invalid_event.batch_ordinal,
                                invalid_event.batch_size,
                                invalid_event.batch_digest,
                                encode_collaboration_event(invalid_event),
                            ),
                        )
                    append_candidate = batches["proposal"]
                else:
                    store.append_batch(batches["proposal"])
                    history = store.read("session_a", metadata.conversation_id)
                    with raw_connection(workspace) as connection:
                        if corruption == "partial_batch":
                            connection.execute(
                                "DELETE FROM collaboration_events WHERE sequence = 2"
                            )
                        elif corruption == "gapped_sequence":
                            connection.execute(
                                "UPDATE collaboration_events SET sequence = 3 "
                                "WHERE sequence = 2"
                            )
                        elif corruption == "ordinal_drift":
                            ordinal = replace(history[0], batch_ordinal=1)
                            connection.execute(
                                "UPDATE collaboration_events SET event_bytes = ? "
                                "WHERE sequence = 1",
                                (encode_collaboration_event(ordinal),),
                            )
                        elif corruption == "size_drift":
                            connection.execute(
                                "UPDATE collaboration_events SET batch_size = 3 "
                                "WHERE sequence = 1"
                            )
                        elif corruption == "digest_drift":
                            connection.execute(
                                "UPDATE collaboration_events SET batch_digest = ? "
                                "WHERE sequence = 1",
                                ("sha256:" + "0" * 64,),
                            )
                        elif corruption == "event_id_drift":
                            connection.execute(
                                "UPDATE collaboration_events SET event_id = ? "
                                "WHERE sequence = 1",
                                ("drifted_event",),
                            )
                        elif corruption == "batch_id_drift":
                            connection.execute(
                                "UPDATE collaboration_events SET batch_id = ? "
                                "WHERE sequence = 1",
                                ("drifted_batch",),
                            )
                        elif corruption == "session_drift":
                            connection.execute(
                                "UPDATE collaboration_events SET session_id = ? "
                                "WHERE sequence = 1",
                                ("other_session",),
                            )
                        elif corruption == "conversation_drift":
                            connection.execute(
                                "UPDATE collaboration_events "
                                "SET conversation_id = ? WHERE sequence = 1",
                                ("other_conversation",),
                            )
                        elif corruption == "timestamp_drift":
                            connection.execute(
                                "UPDATE collaboration_events SET timestamp = ? "
                                "WHERE sequence = 1",
                                ("2026-09-01T00:00:01+00:00",),
                            )
                        elif corruption == "noncanonical_blob":
                            connection.execute(
                                "UPDATE collaboration_events SET event_bytes = ? "
                                "WHERE sequence = 1",
                                (b" " + encode_collaboration_event(history[0]),),
                            )
                        else:
                            self.assertEqual(corruption, "schema_tag")
                            schema_one = encode_collaboration_event(history[0]).replace(
                                b'"schema_version":2',
                                b'"schema_version":1',
                            )
                            connection.execute(
                                "UPDATE collaboration_events SET event_bytes = ? "
                                "WHERE sequence = 1",
                                (schema_one,),
                            )
                    append_candidate = batches["confirmation"]

                corrupted = self.database_state(workspace)
                for operation in (
                    lambda: store.read("session_a", metadata.conversation_id),
                    lambda: store.read_context(
                        "session_a",
                        metadata.conversation_id,
                    ),
                ):
                    with self.assertRaises(WorkspaceFormatError):
                        operation()
                    self.assertEqual(self.database_state(workspace), corrupted)
                with self.assertRaises(CollaborationAppendError) as raised:
                    store.append_batch(append_candidate)
                self.assertIs(
                    raised.exception.failure,
                    CollaborationAppendFailure.CORRUPT_HISTORY,
                )
                self.assertEqual(self.database_state(workspace), corrupted)

    def test_transaction_failures_rollback_or_expose_a_retryable_commit(self) -> None:
        insert_workspace, insert_metadata, insert_store = self.fresh_store()
        insert_batch = contract_batches(insert_metadata.conversation_id)["proposal"]
        insert_failure = RuntimeError("injected second insert failure")
        real_insert = sqlite_module._insert_collaboration_event

        def fail_second_insert(
            connection: object,
            event: CollaborationEvent,
        ) -> None:
            if event.batch_ordinal == 1:
                raise insert_failure
            real_insert(connection, event)

        with (
            mock.patch.object(
                sqlite_module,
                "_insert_collaboration_event",
                side_effect=fail_second_insert,
            ),
            self.assertRaises(RuntimeError) as insert_raised,
        ):
            insert_store.append_batch(insert_batch)
        self.assertIs(insert_raised.exception, insert_failure)
        self.assertEqual(self.collaboration_rows(insert_workspace), ())

        commit_workspace, commit_metadata, commit_store = self.fresh_store()
        commit_batch = contract_batches(commit_metadata.conversation_id)["proposal"]
        before_commit_failure = RuntimeError("injected pre-commit failure")
        real_write = sqlite_database_module.SQLiteDatabase.write
        validation_calls: list[int] = []

        def fail_immediately_before_commit(
            database: sqlite_database_module.SQLiteDatabase,
            operation: object,
            *,
            validate_snapshot: object,
            validate_lease: object,
        ) -> object:
            def injected_validation() -> None:
                validation_calls.append(len(validation_calls) + 1)
                if len(validation_calls) == 3:
                    raise before_commit_failure
                validate_lease()  # type: ignore[operator]

            return real_write(
                database,
                operation,  # type: ignore[arg-type]
                validate_snapshot=validate_snapshot,  # type: ignore[arg-type]
                validate_lease=injected_validation,
            )

        with (
            mock.patch.object(
                sqlite_database_module.SQLiteDatabase,
                "write",
                autospec=True,
                side_effect=fail_immediately_before_commit,
            ),
            self.assertRaises(RuntimeError) as commit_raised,
        ):
            commit_store.append_batch(commit_batch)
        self.assertIs(commit_raised.exception, before_commit_failure)
        self.assertEqual(validation_calls, [1, 2, 3])
        self.assertEqual(self.collaboration_rows(commit_workspace), ())

        durable_workspace, durable_metadata, durable_store = self.fresh_store()
        durable_batch = contract_batches(durable_metadata.conversation_id)["proposal"]
        committed_receipts: list[CollaborationAppendReceipt] = []

        def fail_after_durable_commit(
            database: sqlite_database_module.SQLiteDatabase,
            operation: object,
            *,
            validate_snapshot: object,
            validate_lease: object,
        ) -> object:
            result, generation = real_write(
                database,
                operation,  # type: ignore[arg-type]
                validate_snapshot=validate_snapshot,  # type: ignore[arg-type]
                validate_lease=validate_lease,  # type: ignore[arg-type]
            )
            self.assertIsInstance(result, CollaborationAppendReceipt)
            assert isinstance(result, CollaborationAppendReceipt)
            self.assertGreater(generation, 0)
            committed_receipts.append(result)
            raise sqlite_database_module.SQLiteDatabaseUnavailableError()

        with (
            mock.patch.object(
                sqlite_database_module.SQLiteDatabase,
                "write",
                autospec=True,
                side_effect=fail_after_durable_commit,
            ),
            self.assertRaises(ConversationStorageUnavailableError),
        ):
            durable_store.append_batch(durable_batch)
        self.assertEqual(
            len(self.collaboration_rows(durable_workspace)),
            len(durable_batch.events),
        )
        self.assertEqual(len(committed_receipts), 1)

        retry = durable_store.append_batch(durable_batch)
        committed = committed_receipts[0]
        self.assertIs(retry.status, CollaborationAppendStatus.ALREADY_COMMITTED)
        self.assertEqual(
            (
                retry.batch_id,
                retry.batch_digest,
                retry.first_sequence,
                retry.last_sequence,
                retry.timestamps,
            ),
            (
                committed.batch_id,
                committed.batch_digest,
                committed.first_sequence,
                committed.last_sequence,
                committed.timestamps,
            ),
        )

    def test_independent_stores_and_spawned_processes_serialize_races(self) -> None:
        for runner in (self.run_thread_race, self.run_process_race):
            for identical in (False, True):
                with self.subTest(runner=runner.__name__, identical=identical):
                    outcomes, workspace = runner(identical=identical)
                    rows = self.collaboration_rows(workspace)
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(len({row[8] for row in rows}), 1)
                    if identical:
                        receipts = [
                            outcome for outcome in outcomes if outcome[0] == "receipt"
                        ]
                        self.assertEqual(len(receipts), 2, outcomes)
                        self.assertEqual(
                            {receipt[1] for receipt in receipts},
                            {
                                CollaborationAppendStatus.APPENDED.value,
                                CollaborationAppendStatus.ALREADY_COMMITTED.value,
                            },
                        )
                        self.assertEqual(receipts[0][2:], receipts[1][2:])
                    else:
                        receipts = [
                            outcome for outcome in outcomes if outcome[0] == "receipt"
                        ]
                        errors = [
                            outcome for outcome in outcomes if outcome[0] == "error"
                        ]
                        self.assertEqual(len(receipts), 1, outcomes)
                        self.assertEqual(
                            receipts[0][1],
                            CollaborationAppendStatus.APPENDED.value,
                        )
                        self.assertEqual(
                            errors,
                            [
                                (
                                    "error",
                                    CollaborationAppendFailure.STALE_SEQUENCE.value,
                                )
                            ],
                        )

    def test_legacy_database_and_v1_shaped_rows_fail_without_mutation(self) -> None:
        legacy_workspace = initialized_workspace(self)
        target = database_path(legacy_workspace)
        legacy_identity = sqlite_database_module._CREATE_IDENTITY_SQL.replace(
            "schema_version = 2",
            "schema_version = 1",
        )
        self.assertNotEqual(
            legacy_identity,
            sqlite_database_module._CREATE_IDENTITY_SQL,
        )
        legacy_statements = (
            legacy_identity,
            sqlite_database_module._CREATE_CONVERSATIONS_SQL,
            sqlite_database_module._CREATE_EVENTS_SQL,
            sqlite_database_module._CREATE_CONVERSATION_ORDER_INDEX_SQL,
            sqlite_database_module._CREATE_CONVERSATION_STATUS_INDEX_SQL,
            sqlite_database_module._CREATE_TURN_SLOT_INDEX_SQL,
            sqlite_database_module._CREATE_RECENT_OUTCOME_INDEX_SQL,
            sqlite_database_module._CREATE_CONTIGUOUS_TRIGGER_SQL,
            sqlite_database_module._CREATE_USER_BEFORE_OUTCOME_TRIGGER_SQL,
            sqlite_database_module._CREATE_ADDRESSED_REPLY_TRIGGER_SQL,
        )
        normalized = tuple(
            " ".join(statement.split()) for statement in legacy_statements
        )
        fingerprint = hashlib.sha256(
            ("\n".join(sorted(normalized)) + "\n").encode("utf-8")
        ).hexdigest()
        connection = sqlite3.connect(target, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA encoding = 'UTF-8'")
            connection.execute("BEGIN IMMEDIATE")
            for statement in legacy_statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO conversation_storage_identity "
                "(singleton, schema_version, schema_fingerprint, "
                "lexical_generation) VALUES (?, ?, ?, ?)",
                (1, 1, fingerprint, 0),
            )
            connection.execute("PRAGMA user_version = 1")
            connection.execute("PRAGMA schema_version = 1")
            connection.execute(
                "PRAGMA application_id = "
                f"{sqlite_database_module.SQLITE_APPLICATION_ID}"
            )
            connection.execute("COMMIT")
        finally:
            connection.close()

        legacy_store = SQLiteCollaborationEventStore(legacy_workspace)
        with legacy_store._mutation_coordinator.borrow(timeout_seconds=1.0):
            pass
        legacy_artifact = self.database_artifact(legacy_workspace)
        legacy_namespace = storage_snapshot(legacy_workspace)
        legacy_batch = contract_batches("conversation_a")["proposal"]
        for operation in (
            lambda: preflight_sqlite_conversation_storage(legacy_workspace),
            lambda: legacy_store.read("session_a", "conversation_a"),
            lambda: legacy_store.append_batch(legacy_batch),
        ):
            with self.assertRaises(WorkspaceVersionUnsupportedError):
                operation()
            self.assertEqual(
                self.database_artifact(legacy_workspace),
                legacy_artifact,
            )
            self.assertEqual(storage_snapshot(legacy_workspace), legacy_namespace)

        workspace, metadata, store = self.fresh_store()
        batches = contract_batches(metadata.conversation_id)
        v1_bytes = json.dumps(
            {
                "schema_version": 1,
                "session_id": "session_a",
                "conversation_id": metadata.conversation_id,
                "turn_id": "turn_a",
                "sequence": 1,
                "timestamp": "2026-09-01T00:00:00+00:00",
                "sender": "user",
                "recipient": "lead_orchestrator",
                "kind": "user_message",
                "content": "legacy content",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        v1_row = (
            "session_a",
            metadata.conversation_id,
            1,
            1,
            "legacy_event",
            "turn_a",
            "step_a",
            "2026-09-01T00:00:00+00:00",
            "legacy_batch",
            0,
            1,
            "sha256:" + "0" * 64,
            v1_bytes,
        )
        insert_sql = (
            "INSERT INTO collaboration_events VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        before = self.database_state(workspace)
        with raw_connection(workspace) as raw:
            with self.assertRaises(sqlite3.IntegrityError):
                raw.execute(insert_sql, v1_row)
        self.assertEqual(self.database_state(workspace), before)

        v1_shaped_row = (*v1_row[:3], 2, *v1_row[4:])
        with raw_connection(workspace) as raw:
            raw.execute(insert_sql, v1_shaped_row)
        corrupted_state = self.database_state(workspace)
        corrupted_artifact = self.database_artifact(workspace)
        for operation in (
            lambda: store.read("session_a", metadata.conversation_id),
            lambda: store.read_context("session_a", metadata.conversation_id),
        ):
            with self.assertRaises(WorkspaceFormatError):
                operation()
            self.assertEqual(self.database_state(workspace), corrupted_state)
            self.assertEqual(self.database_artifact(workspace), corrupted_artifact)
        with self.assertRaises(CollaborationAppendError) as raised:
            store.append_batch(batches["proposal"])
        self.assertIs(
            raised.exception.failure,
            CollaborationAppendFailure.CORRUPT_HISTORY,
        )
        self.assertEqual(self.database_state(workspace), corrupted_state)
        self.assertEqual(self.database_artifact(workspace), corrupted_artifact)


if __name__ == "__main__":
    unittest.main()
