"""Public behavior and conformance tests for optional SQLite storage."""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
from story_writing_agents import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationCatalog,
    ConversationEvent,
    ConversationEventKind,
    ConversationEventStore,
    ConversationSearchQuery,
    ConversationSearchScope,
    ReferenceConversationSearch,
    StoryRuntime,
    Workspace,
    WorkspaceFormatError,
    normalize_conversation_subject,
)
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversation_literal_search import (
    CONVERSATION_LITERAL_NORMALIZATION_VERSION,
    normalize_conversation_literal_query,
)
from story_writing_agents.conversations import select_bounded_conversation_context
from story_writing_agents.conversation_storage import (
    ConversationArchiveConflictError,
    ConversationArchiveFormatError,
    ConversationArchiveImportStatus,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageCoordinationError,
    ConversationStorageProvisioningNeededError,
    ConversationStorageServices,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage import sqlite as sqlite_module
from story_writing_agents.conversation_storage import (
    sqlite_search_index as search_index_module,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_APPLICATION_ID,
    SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS,
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION,
    SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION,
    SQLiteConversationArchiveAdapter,
    SQLiteConversationCatalog,
    SQLiteConversationEventStore,
    SQLiteConversationSearch,
    compose_sqlite_conversation_archive,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)
from story_writing_agents.conversation_storage.sqlite_search_index import (
    SQLITE_CONVERSATION_SEARCH_INDEX_PATH,
    SQLiteConversationSearchIndex,
    SQLiteConversationSearchIndexDocument,
    SQLiteConversationSearchIndexError,
)

from tests.conversation_event_store_contract import (
    ConversationEventStoreContractMixin,
)
from tests.conversation_search_contract import ConversationSearchContractMixin
from tests.sqlite_conversation_storage_fixture import (
    database_path,
    initialized_workspace,
    make_append,
    provision,
    provisioned_services,
    raw_connection,
    storage_snapshot,
)
from tests.test_conversation_catalog import _ConversationCatalogContractMixin


def _archive_conversations(archive):  # type: ignore[no-untyped-def]
    """Flatten one canonical archive in its established order."""

    return tuple(
        conversation
        for session in archive.sessions
        for conversation in session.conversations
    )


class SQLiteConversationCatalogContractTests(
    _ConversationCatalogContractMixin,
    unittest.TestCase,
):
    def make_catalog(self) -> ConversationCatalog:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        return compose_sqlite_conversation_storage(self.workspace).catalog

    def test_rename_reaches_sqlite_maximum_then_fails_closed_at_limit(
        self,
    ) -> None:
        maximum = (1 << 63) - 1
        original = self.catalog.create("session_a", "Original subject")
        identity = (original.session_id, original.conversation_id)
        with raw_connection(self.workspace) as connection:
            connection.execute(
                "UPDATE conversations SET subject_revision = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                (maximum - 1, *identity),
            )

        successful_subject = "  Maximum Café revision  "
        maximum_revision = self.catalog.rename(
            original.session_id,
            original.conversation_id,
            successful_subject,
        )
        self.assertEqual(maximum_revision.subject_revision, maximum)
        self.assertEqual(maximum_revision.subject, successful_subject)
        self.assertEqual(
            self.catalog.get(*identity),
            maximum_revision,
        )
        self.assertIs(
            preflight_sqlite_conversation_storage(self.workspace).capability,
            ConversationStorageCapability.READY,
        )

        metadata_sql = (
            "SELECT schema_version, session_id, conversation_id, subject, "
            "normalized_subject, subject_revision, status, created_at, updated_at "
            "FROM conversations WHERE session_id = ? AND conversation_id = ?"
        )
        with raw_connection(self.workspace) as connection:
            logical_before = connection.execute(metadata_sql, identity).fetchone()
        self.assertIsNotNone(logical_before)
        assert logical_before is not None
        self.assertEqual(
            logical_before[3:6],
            (
                successful_subject,
                normalize_conversation_subject(successful_subject),
                maximum,
            ),
        )
        before = storage_snapshot(self.workspace)

        attempted_subject = "overflow sentinel must not persist"
        with self.assertRaises(WorkspaceFormatError) as caught:
            self.catalog.rename(
                original.session_id,
                original.conversation_id,
                attempted_subject,
            )

        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("sentinel", str(caught.exception).casefold())
        self.assertNotIn("too large", str(caught.exception).casefold())
        self.assertEqual(storage_snapshot(self.workspace), before)
        with raw_connection(self.workspace) as connection:
            logical_after = connection.execute(metadata_sql, identity).fetchone()
        self.assertEqual(logical_after, logical_before)
        self.assertEqual(self.catalog.get(*identity), maximum_revision)


class SQLiteConversationEventStoreContractTests(
    ConversationEventStoreContractMixin,
    unittest.TestCase,
):
    def make_store(self) -> ConversationEventStore:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        services = compose_sqlite_conversation_storage(self.workspace)
        self.catalog = services.catalog
        return services.event_store

    def create_conversation(self, session_id: str, subject: str) -> str:
        return self.catalog.create(session_id, subject).conversation_id

    def test_read_context_materializes_only_addressed_pair_for_valid_and_corrupt(
        self,
    ) -> None:
        self.store.append(self.request("interrupted", content="saved unmatched"))
        self.append_turn(
            "complete",
            user_content="complete user",
            reply_content="complete reply",
        )
        transcript = self.store.read(self.session_id, self.conversation_id)
        expected = select_bounded_conversation_context(transcript)

        with (
            mock.patch.object(
                sqlite_module,
                "_event_from_row",
                wraps=sqlite_module._event_from_row,
            ) as materialize,
            mock.patch.object(
                sqlite_module,
                "select_bounded_conversation_context",
                wraps=sqlite_module.select_bounded_conversation_context,
            ) as select_context,
        ):
            actual = self.store.read_context(
                self.session_id,
                self.conversation_id,
            )

        self.assertEqual(actual, expected)
        self.assertEqual(materialize.call_count, len(expected.events))
        select_context.assert_not_called()

        with raw_connection(self.workspace) as connection:
            connection.execute(
                "UPDATE conversation_events SET sender = ? "
                "WHERE session_id = ? AND conversation_id = ? AND sequence = 3",
                ("plot_architect", self.session_id, self.conversation_id),
            )
        before = storage_snapshot(self.workspace)
        with (
            mock.patch.object(
                sqlite_module,
                "_event_from_row",
                wraps=sqlite_module._event_from_row,
            ) as corrupt_materialize,
            mock.patch.object(
                sqlite_module,
                "select_bounded_conversation_context",
                wraps=sqlite_module.select_bounded_conversation_context,
            ) as corrupt_select_context,
        ):
            with self.assertRaises(WorkspaceFormatError) as caught:
                self.store.read_context(
                    self.session_id,
                    self.conversation_id,
                )

        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(corrupt_materialize.call_count, len(expected.events))
        corrupt_select_context.assert_not_called()
        self.assertEqual(storage_snapshot(self.workspace), before)

    def test_read_context_matches_full_selector_with_at_most_25_pairs(self) -> None:
        for index in range(30):
            self.append_turn(
                f"complete_{index:02d}",
                user_content=f"user {index:02d}",
                reply_content=f"reply {index:02d}",
            )
        transcript = self.store.read(self.session_id, self.conversation_id)
        expected = select_bounded_conversation_context(transcript)

        with mock.patch.object(
            sqlite_module,
            "_event_from_row",
            wraps=sqlite_module._event_from_row,
        ) as materialize:
            actual = self.store.read_context(self.session_id, self.conversation_id)

        self.assertEqual(actual, expected)
        self.assertEqual(actual.total_complete_turns, 30)
        self.assertEqual(actual.retained_turns, 24)
        self.assertEqual(actual.omitted_turns, 6)
        self.assertEqual(materialize.call_count, 50)

        with raw_connection(self.workspace) as connection:
            total_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM conversation_events "
                    "WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_slot = 1",
                    (self.session_id, self.conversation_id),
                ).fetchall()
            )
            recent_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + sqlite_module._EVENT_SELECT
                    + " WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_slot = 1 ORDER BY sequence DESC LIMIT ?",
                    (self.session_id, self.conversation_id, 25),
                ).fetchall()
            )
            addressed_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + sqlite_module._EVENT_SELECT_BY_TURN
                    + " WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_slot = 0 AND turn_id IN (?, ?) ORDER BY sequence",
                    (
                        self.session_id,
                        self.conversation_id,
                        "complete_28",
                        "complete_29",
                    ),
                ).fetchall()
            )
        self.assertTrue(
            any("conversation_events_recent_outcomes" in row for row in total_plan)
        )
        self.assertTrue(
            any("conversation_events_recent_outcomes" in row for row in recent_plan)
        )
        self.assertTrue(
            any("conversation_events_turn_slots" in row for row in addressed_plan)
        )

    def test_read_context_orders_reverse_outcomes_by_user_sequence(self) -> None:
        for index in range(25):
            self.store.append(
                self.request(
                    f"reverse_{index:02d}",
                    content=f"user {index:02d}",
                )
            )
        for index in reversed(range(25)):
            self.store.append(
                self.request(
                    f"reverse_{index:02d}",
                    kind=ConversationEventKind.AGENT_REPLY,
                    sender=LEAD_ORCHESTRATOR,
                    content=f"reply {index:02d}",
                )
            )
        transcript = self.store.read(self.session_id, self.conversation_id)
        expected = select_bounded_conversation_context(transcript)

        with mock.patch.object(
            sqlite_module,
            "_event_from_row",
            wraps=sqlite_module._event_from_row,
        ) as materialize:
            actual = self.store.read_context(self.session_id, self.conversation_id)

        self.assertEqual(actual, expected)
        self.assertEqual(actual.total_complete_turns, 25)
        self.assertEqual(actual.retained_turns, 24)
        self.assertEqual(actual.omitted_turns, 1)
        self.assertEqual(materialize.call_count, 50)
        retained_turns = {event.turn_id for event in actual.events}
        self.assertNotIn("reverse_00", retained_turns)
        self.assertEqual(
            retained_turns,
            {f"reverse_{index:02d}" for index in range(1, 25)},
        )

        with raw_connection(self.workspace) as connection:
            recent_user_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + sqlite_module._EVENT_SELECT_RECENT
                    + " WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_slot = 0 AND EXISTS ("
                    "SELECT 1 FROM conversation_events AS outcome_event "
                    "INDEXED BY conversation_events_turn_slots "
                    "WHERE outcome_event.session_id = conversation_events.session_id "
                    "AND outcome_event.conversation_id = "
                    "conversation_events.conversation_id "
                    "AND outcome_event.turn_id = conversation_events.turn_id "
                    "AND outcome_event.turn_slot = 1"
                    ") ORDER BY sequence DESC LIMIT ?",
                    (self.session_id, self.conversation_id, 25),
                ).fetchall()
            )
            placeholders = ", ".join("?" for _ in range(25))
            outcome_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + sqlite_module._EVENT_SELECT_BY_TURN
                    + " WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_slot = 1 "
                    f"AND turn_id IN ({placeholders}) ORDER BY sequence",
                    (
                        self.session_id,
                        self.conversation_id,
                        *(f"reverse_{index:02d}" for index in range(25)),
                    ),
                ).fetchall()
            )
        self.assertTrue(
            any(
                "conversation_events_recent_outcomes" in row for row in recent_user_plan
            )
        )
        self.assertTrue(
            any("conversation_events_turn_slots" in row for row in recent_user_plan)
        )
        self.assertTrue(
            any("conversation_events_turn_slots" in row for row in outcome_plan)
        )

    def test_read_context_matches_unmatched_interleaved_and_oversized_newest(
        self,
    ) -> None:
        self.store.append(self.request("turn_a", content="user a"))
        self.store.append(self.request("turn_b", content="user b"))
        self.store.append(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                sender=LEAD_ORCHESTRATOR,
                content="reply a",
            )
        )
        self.store.append(
            self.request(
                "turn_b",
                kind=ConversationEventKind.AGENT_REPLY,
                sender=LEAD_ORCHESTRATOR,
                content="reply b",
            )
        )
        self.store.append(self.request("unmatched", content="unmatched newest"))
        transcript = self.store.read(self.session_id, self.conversation_id)
        self.assertEqual(
            self.store.read_context(self.session_id, self.conversation_id),
            select_bounded_conversation_context(transcript),
        )

        workspace = initialized_workspace(self)
        provision(workspace)
        services = compose_sqlite_conversation_storage(workspace)
        metadata = services.catalog.create("session_a", "Oversized newest")
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="older",
                content="older user",
            )
        )
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="older",
                kind=ConversationEventKind.AGENT_REPLY,
                content="older reply",
            )
        )
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="oversized",
                content="u",
            )
        )
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="oversized",
                kind=ConversationEventKind.AGENT_REPLY,
                content="x" * 32_768,
            )
        )
        full = services.event_store.read("session_a", metadata.conversation_id)
        expected = select_bounded_conversation_context(full)
        actual = services.event_store.read_context(
            "session_a",
            metadata.conversation_id,
        )
        self.assertEqual(actual, expected)
        self.assertEqual(actual.events, ())
        self.assertEqual(actual.total_complete_turns, 2)
        self.assertEqual(actual.omitted_turns, 2)


class SQLiteConversationSearchContractTests(
    ConversationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[SQLiteConversationCatalog, SQLiteConversationEventStore]:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        services = compose_sqlite_conversation_storage(self.workspace)
        self.services = services
        if not isinstance(services.catalog, SQLiteConversationCatalog):
            raise AssertionError("SQLite composition returned the wrong catalog")
        if not isinstance(services.event_store, SQLiteConversationEventStore):
            raise AssertionError("SQLite composition returned the wrong event store")
        if not isinstance(services.search, SQLiteConversationSearch):
            raise AssertionError("SQLite composition returned the wrong search")
        return services.catalog, services.event_store

    def make_search(
        self,
        catalog: object,
        store: object,
    ) -> SQLiteConversationSearch:
        del catalog, store
        search = self.services.search
        if not isinstance(search, SQLiteConversationSearch):
            raise AssertionError("SQLite composition returned the wrong search")
        return search

    def test_sqlite_and_reference_results_are_exact_with_or_without_fts_hints(
        self,
    ) -> None:
        scope = ConversationSearchScope(self.session_id)
        reference = ReferenceConversationSearch(self.catalog, self.store)
        queries = (
            ConversationSearchQuery("council vessel"),
            ConversationSearchQuery("when does"),
            ConversationSearchQuery("archived council", include_archived=True),
            ConversationSearchQuery("PRIVATE_DIAGNOSTIC"),
        )
        search = self.make_search(self.catalog, self.store)
        for query in queries:
            with self.subTest(mode="live FTS", query=query.text):
                self.assertEqual(
                    search.search(scope, query),
                    reference.search(scope, query),
                )
            expected = reference.search(scope, query)
            for label, candidate_outcome in (
                ("fallback", SQLiteConversationSearchIndexError()),
                ("false negatives", frozenset()),
                (
                    "irrelevant hints",
                    frozenset({("unrelated_conversation", 999)}),
                ),
            ):
                with (
                    self.subTest(mode=label, query=query.text),
                    mock.patch.object(
                        SQLiteConversationSearchIndex,
                        "candidates",
                        side_effect=(
                            candidate_outcome
                            if isinstance(candidate_outcome, BaseException)
                            else None
                        ),
                        return_value=(
                            candidate_outcome
                            if not isinstance(candidate_outcome, BaseException)
                            else mock.DEFAULT
                        ),
                    ),
                ):
                    self.assertEqual(search.search(scope, query), expected)

        with raw_connection(self.workspace) as connection:
            generation = connection.execute(
                "SELECT lexical_generation FROM conversation_storage_identity"
            ).fetchone()[0]
        index = SQLiteConversationSearchIndex(
            os.fspath(self.workspace.root),
            normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
        )
        hints = index.candidates(
            source_generation=generation,
            session_id=self.session_id,
            conversation_ids=tuple(
                item.conversation_id
                for item in self.catalog.list(
                    self.session_id,
                    include_archived=True,
                )
            ),
            normalized_query=normalize_conversation_literal_query(
                "when does",
                max_characters=200,
            ),
            maximum=10_000,
        )
        self.assertIn(
            (self.records["transcript"].conversation_id, 1),
            hints,
        )


class SQLiteConversationSearchBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace, self.services = provisioned_services(self)
        self.scope = ConversationSearchScope("session_a")

    def generation(self) -> int:
        with raw_connection(self.workspace) as connection:
            row = connection.execute(
                "SELECT lexical_generation FROM conversation_storage_identity"
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row[0]

    def index_path(self) -> Path:
        return self.workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)

    def assert_generations(self, expected: int) -> None:
        self.assertEqual(self.generation(), expected)
        with closing(sqlite3.connect(self.index_path())) as connection, connection:
            companion = connection.execute(
                "SELECT source_generation FROM conversation_search_index_identity"
            ).fetchone()
        self.assertEqual(companion, (expected,))

    def append_turn(
        self,
        conversation_id: str,
        turn_id: str,
        user_content: str,
        outcome_content: str,
        *,
        outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
    ) -> tuple[ConversationEvent, ConversationEvent]:
        user = self.services.event_store.append(
            make_append(
                session_id=self.scope.session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                content=user_content,
            )
        )
        outcome = self.services.event_store.append(
            make_append(
                session_id=self.scope.session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                kind=outcome_kind,
                content=outcome_content,
            )
        )
        return user, outcome

    def test_unicode_literal_and_query_language_results_match_reference(self) -> None:
        exact = self.services.catalog.create("session_a", "Straße")
        prefix = self.services.catalog.create("session_a", "ＳＴＲＡＳＳＥ route")
        transcript = self.services.catalog.create("session_a", "Symbol notes")
        first, _ = self.append_turn(
            transcript.conversation_id,
            "turn_literal",
            "literal council OR vessel; alpha\x00\u200bbeta",
            "council OR vessel appears again",
        )
        runtime = self.services.catalog.create("session_a", "Runtime notes")
        self.append_turn(
            runtime.conversation_id,
            "turn_runtime",
            "ordinary visible question",
            "PRIVATE_RUNTIME_NEEDLE",
            outcome_kind=ConversationEventKind.RUNTIME_ERROR,
        )
        archived = self.services.catalog.create("session_a", "Archived strasse")
        self.services.catalog.archive("session_a", archived.conversation_id)
        self.services.catalog.create("session_other", "Straße")

        reference = ReferenceConversationSearch(
            self.services.catalog,
            self.services.event_store,
        )
        cases = (
            ConversationSearchQuery("  STRASSE  "),
            ConversationSearchQuery("council OR vessel"),
            ConversationSearchQuery("alpha beta"),
            ConversationSearchQuery("strasse", include_archived=True),
            ConversationSearchQuery("PRIVATE_RUNTIME_NEEDLE"),
        )
        for query in cases:
            with self.subTest(query=query):
                expected = reference.search(self.scope, query)
                actual = self.services.search.search(self.scope, query)
                self.assertEqual(actual, expected)
                self.assertTrue(
                    all(
                        hit.citation.session_id == self.scope.session_id
                        for hit in actual.hits
                    )
                )

        literal = self.services.search.search(
            self.scope,
            ConversationSearchQuery("council OR vessel"),
        )
        self.assertEqual(len(literal.hits), 1)
        self.assertEqual(literal.hits[0].citation.sequence, first.sequence)
        self.assertEqual(literal.matched_conversations, 1)
        self.assertEqual(
            tuple(
                hit.citation.conversation_id
                for hit in self.services.search.search(
                    self.scope,
                    ConversationSearchQuery("strasse", include_archived=True),
                ).hits
            ),
            (exact.conversation_id, prefix.conversation_id, archived.conversation_id),
        )

    def test_required_lexical_state_and_best_effort_fts_lifecycle(self) -> None:
        before = storage_snapshot(self.workspace)
        with mock.patch.object(
            sqlite_module,
            "_advance_lexical_generation",
            side_effect=sqlite_module.SQLiteDatabaseFormatError(),
        ):
            with self.assertRaises(WorkspaceFormatError) as caught:
                self.services.catalog.create("session_a", "rollback sentinel")
        self.assertEqual(str(caught.exception), "invalid SQLite conversation storage")
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("sentinel", str(caught.exception))
        self.assertEqual(storage_snapshot(self.workspace), before)
        self.assert_generations(0)
        self.assertEqual(
            self.services.catalog.list("session_a", include_archived=True),
            (),
        )

        metadata = self.services.catalog.create("session_a", "Lifecycle needle")
        self.assert_generations(1)
        renamed = self.services.catalog.rename(
            "session_a",
            metadata.conversation_id,
            "Renamed lifecycle needle",
        )
        self.assert_generations(2)
        archived = self.services.catalog.archive("session_a", metadata.conversation_id)
        self.assert_generations(3)
        self.assertEqual(
            self.services.catalog.archive("session_a", metadata.conversation_id),
            archived,
        )
        self.assert_generations(3)
        active = self.services.catalog.unarchive("session_a", metadata.conversation_id)
        self.assert_generations(4)
        self.assertEqual(
            self.services.catalog.unarchive("session_a", metadata.conversation_id),
            active,
        )
        self.assert_generations(4)
        user = self.services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_generation",
                content="Visible Straße content",
            )
        )
        self.assert_generations(5)
        outcome = self.services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_generation",
                kind=ConversationEventKind.AGENT_REPLY,
                content="Visible outcome",
            )
        )
        self.assert_generations(6)
        with raw_connection(self.workspace) as connection:
            rows = connection.execute(
                "SELECT sequence, searchable_content FROM conversation_events "
                "WHERE session_id = ? AND conversation_id = ? ORDER BY sequence",
                ("session_a", metadata.conversation_id),
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (user.sequence, "visible strasse content"),
                (outcome.sequence, "visible outcome"),
            ],
        )
        with closing(sqlite3.connect(self.index_path())) as connection, connection:
            companion_generation = connection.execute(
                "SELECT source_generation FROM conversation_search_index_identity"
            ).fetchone()
        self.assertEqual(companion_generation, (6,))
        self.assertEqual(renamed.subject_revision, 2)

        source, source_services = provisioned_services(self)
        source_metadata = source_services.catalog.create(
            "session_import",
            "Imported lexical needle",
        )
        source_services.event_store.append(
            make_append(
                session_id="session_import",
                conversation_id=source_metadata.conversation_id,
                turn_id="turn_import",
                content="imported content",
            )
        )
        source_archive = compose_sqlite_conversation_archive(source)
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target = initialized_workspace(self)
        provision(target)
        target_archive = compose_sqlite_conversation_archive(target)
        target_coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            imported = target_archive.import_archive(lease, payload)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        with raw_connection(target) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT lexical_generation FROM conversation_storage_identity"
                ).fetchone(),
                (1,),
            )
        target_services = compose_sqlite_conversation_storage(target)
        imported_search = target_services.search.search(
            ConversationSearchScope("session_import"),
            ConversationSearchQuery("imported content"),
        )
        self.assertEqual(
            tuple(hit.citation.conversation_id for hit in imported_search.hits),
            (source_metadata.conversation_id,),
        )
        with (
            closing(
                sqlite3.connect(target.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH))
            ) as connection,
            connection,
        ):
            self.assertEqual(
                connection.execute(
                    "SELECT source_generation FROM conversation_search_index_identity"
                ).fetchone(),
                (1,),
            )
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            unchanged = target_archive.import_archive(lease, payload)
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        with raw_connection(target) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT lexical_generation FROM conversation_storage_identity"
                ).fetchone(),
                (1,),
            )

        failed_workspace, failed_services = provisioned_services(self)
        failed_index = failed_workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)
        derived_before = failed_index.read_bytes()
        with mock.patch.object(
            SQLiteConversationSearchIndex,
            "rebuild",
            side_effect=SQLiteConversationSearchIndexError(),
        ):
            committed = failed_services.catalog.create(
                "session_a",
                "Committed despite optional index failure",
            )
        self.assertEqual(failed_index.read_bytes(), derived_before)
        with raw_connection(failed_workspace) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT lexical_generation FROM conversation_storage_identity"
                ).fetchone(),
                (1,),
            )
        result = failed_services.search.search(
            ConversationSearchScope("session_a"),
            ConversationSearchQuery("optional index failure"),
        )
        self.assertEqual(
            tuple(hit.citation.conversation_id for hit in result.hits),
            (committed.conversation_id,),
        )
        self.assertEqual(failed_index.read_bytes(), derived_before)

    def test_stale_corrupt_unsafe_unavailable_and_busy_fts_never_repairs(self) -> None:
        def populated() -> tuple[Workspace, ConversationStorageServices, object]:
            workspace, services = provisioned_services(self)
            metadata = services.catalog.create("session_a", "Fallback subject")
            services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id="turn_fallback",
                    content="authoritative fallback needle",
                )
            )
            expected = ReferenceConversationSearch(
                services.catalog,
                services.event_store,
            ).search(
                ConversationSearchScope("session_a"),
                ConversationSearchQuery("fallback needle"),
            )
            return workspace, services, expected

        for case in ("stale", "corrupt", "sidecar", "capability"):
            with self.subTest(case=case):
                workspace, services, expected = populated()
                path = workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)
                patcher = mock.patch.object(
                    search_index_module,
                    "_runtime",
                    side_effect=SQLiteConversationSearchIndexError(),
                )
                if case == "stale":
                    with closing(sqlite3.connect(path)) as connection, connection:
                        connection.execute(
                            "UPDATE conversation_search_index_identity "
                            "SET source_generation = 0"
                        )
                elif case == "corrupt":
                    path.write_bytes(b"corrupt disposable FTS sentinel")
                elif case == "sidecar":
                    Path(f"{path}-wal").write_bytes(b"hostile FTS sidecar sentinel")
                before = storage_snapshot(workspace)
                if case == "capability":
                    with patcher:
                        actual = services.search.search(
                            ConversationSearchScope("session_a"),
                            ConversationSearchQuery("fallback needle"),
                        )
                else:
                    actual = services.search.search(
                        ConversationSearchScope("session_a"),
                        ConversationSearchQuery("fallback needle"),
                    )
                self.assertEqual(actual, expected)
                self.assertEqual(storage_snapshot(workspace), before)

        for case in ("symlink", "hardlink"):
            with self.subTest(case=case):
                workspace, services, expected = populated()
                path = workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)
                outside = workspace.path(f"outside-{case}.sqlite3")
                outside.write_bytes(b"outside FTS sentinel")
                outside.chmod(0o600)
                path.unlink()
                if case == "symlink":
                    os.symlink(outside, path)
                else:
                    os.link(outside, path)
                before = storage_snapshot(workspace)
                outside_before = outside.read_bytes()
                actual = services.search.search(
                    ConversationSearchScope("session_a"),
                    ConversationSearchQuery("fallback needle"),
                )
                self.assertEqual(actual, expected)
                self.assertEqual(storage_snapshot(workspace), before)
                self.assertEqual(outside.read_bytes(), outside_before)

        workspace, services, expected = populated()
        path = workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            before = storage_snapshot(workspace)
            actual = services.search.search(
                ConversationSearchScope("session_a"),
                ConversationSearchQuery("fallback needle"),
            )
            self.assertEqual(actual, expected)
            self.assertEqual(storage_snapshot(workspace), before)
        finally:
            connection.execute("ROLLBACK")
            connection.close()

    def test_incompatible_companion_marker_is_preserved_but_stale_rebuilds(
        self,
    ) -> None:
        def companion_state(path: Path) -> tuple[object, ...]:
            with closing(sqlite3.connect(path)) as connection, connection:
                identity = tuple(
                    connection.execute(
                        "SELECT singleton, schema_version, source_generation, "
                        "normalization_version, tokenizer, runtime_identity "
                        "FROM conversation_search_index_identity"
                    ).fetchall()
                )
                documents = tuple(
                    connection.execute(
                        "SELECT session_id, conversation_id, sequence, "
                        "searchable_content FROM conversation_events_fts "
                        "ORDER BY session_id, conversation_id, sequence"
                    ).fetchall()
                )
                manifest = tuple(
                    connection.execute(
                        "SELECT type, name, sql FROM sqlite_master "
                        "WHERE sql IS NOT NULL AND substr(name, 1, 7) != 'sqlite_' "
                        "ORDER BY type, name"
                    ).fetchall()
                )
                application_id = connection.execute("PRAGMA application_id").fetchone()
                user_version = connection.execute("PRAGMA user_version").fetchone()
            return application_id, user_version, manifest, identity, documents

        metadata = self.services.catalog.create("session_a", "Before marker")
        event = self.services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_marker",
                content="marker fallback event",
            )
        )
        path = self.index_path()
        with closing(sqlite3.connect(path)) as connection, connection:
            current_identity = connection.execute(
                "SELECT runtime_identity FROM conversation_search_index_identity"
            ).fetchone()
            self.assertIsNotNone(current_identity)
            assert current_identity is not None
            incompatible_identity = current_identity[0] + ":incompatible-sentinel"
            connection.execute(
                "UPDATE conversation_search_index_identity "
                "SET runtime_identity = ? WHERE singleton = 1",
                (incompatible_identity,),
            )
        before_state = companion_state(path)
        self.assertEqual(before_state[0], (0x53574654,))
        self.assertEqual(before_state[1], (1,))
        self.assertEqual(before_state[3][0][-1], incompatible_identity)
        before_bytes = path.read_bytes()
        before_namespace = storage_snapshot(self.workspace)
        self.assertFalse(
            any(
                Path(f"{path}{suffix}").exists()
                for suffix in ("-journal", "-wal", "-shm")
            )
        )
        index = SQLiteConversationSearchIndex(
            os.fspath(self.workspace.root),
            normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
        )
        replacement = SQLiteConversationSearchIndexDocument(
            "session_a",
            metadata.conversation_id,
            event.sequence,
            "replacement sentinel must not persist",
        )

        with self.assertRaises(SQLiteConversationSearchIndexError) as caught:
            index.rebuild(
                source_generation=self.generation(),
                documents=(replacement,),
                validate_publication=lambda: None,
            )

        self.assertEqual(
            str(caught.exception),
            "SQLite conversation search index is unavailable",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("sentinel", str(caught.exception).casefold())
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(companion_state(path), before_state)
        self.assertEqual(storage_snapshot(self.workspace), before_namespace)

        generation_before = self.generation()
        renamed = self.services.catalog.rename(
            "session_a",
            metadata.conversation_id,
            "Committed marker fallback needle",
        )
        self.assertEqual(self.generation(), generation_before + 1)
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(companion_state(path), before_state)
        result = self.services.search.search(
            self.scope,
            ConversationSearchQuery("marker fallback needle"),
        )
        expected = ReferenceConversationSearch(
            self.services.catalog,
            self.services.event_store,
        ).search(
            self.scope,
            ConversationSearchQuery("marker fallback needle"),
        )
        self.assertEqual(result, expected)
        self.assertEqual(
            result.hits[0].citation.conversation_id, renamed.conversation_id
        )
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(companion_state(path), before_state)

        stale_workspace, stale_services = provisioned_services(self)
        stale_metadata = stale_services.catalog.create(
            "session_a",
            "Stale generation subject",
        )
        stale_event = stale_services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=stale_metadata.conversation_id,
                turn_id="turn_stale",
                content="recognized stale generation needle",
            )
        )
        stale_path = stale_workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)
        with closing(sqlite3.connect(stale_path)) as connection, connection:
            marker_before = connection.execute(
                "SELECT schema_version, normalization_version, tokenizer, "
                "runtime_identity FROM conversation_search_index_identity"
            ).fetchone()
            connection.execute(
                "UPDATE conversation_search_index_identity "
                "SET source_generation = 0 WHERE singleton = 1"
            )
        stale_services.catalog.rename(
            "session_a",
            stale_metadata.conversation_id,
            "Stale generation renamed",
        )
        with raw_connection(stale_workspace) as connection:
            main_generation = connection.execute(
                "SELECT lexical_generation FROM conversation_storage_identity"
            ).fetchone()
        self.assertEqual(main_generation, (3,))
        with closing(sqlite3.connect(stale_path)) as connection, connection:
            rebuilt_identity = connection.execute(
                "SELECT source_generation, schema_version, normalization_version, "
                "tokenizer, runtime_identity "
                "FROM conversation_search_index_identity"
            ).fetchone()
        self.assertEqual(rebuilt_identity, (3, *marker_before))
        stale_index = SQLiteConversationSearchIndex(
            os.fspath(stale_workspace.root),
            normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
        )
        self.assertEqual(
            stale_index.candidates(
                source_generation=3,
                session_id="session_a",
                conversation_ids=(stale_metadata.conversation_id,),
                normalized_query="recognized stale generation needle",
                maximum=10,
            ),
            frozenset({(stale_metadata.conversation_id, stale_event.sequence)}),
        )

    def test_conversation_scan_uses_exact_256_of_257_stable_rows(self) -> None:
        for count in (256, 257):
            with self.subTest(conversations=count):
                workspace, services = provisioned_services(self)
                rows = [
                    (
                        "session_a",
                        f"conversation_{index:04d}",
                        1,
                        f"Needle subject {index:04d}",
                        f"needle subject {index:04d}",
                        1,
                        "active",
                        "2026-08-25T00:00:00+00:00",
                        "2026-08-25T00:00:00+00:00",
                    )
                    for index in reversed(range(count))
                ]
                rows.append(
                    (
                        "session_other",
                        "conversation_outside",
                        1,
                        "Needle outside scope",
                        "needle outside scope",
                        1,
                        "active",
                        "2026-08-25T00:00:00+00:00",
                        "2026-08-25T00:00:00+00:00",
                    )
                )
                with raw_connection(workspace) as connection:
                    connection.executemany(
                        "INSERT INTO conversations "
                        "(session_id, conversation_id, schema_version, subject, "
                        "normalized_subject, subject_revision, status, created_at, "
                        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                result = services.search.search(
                    ConversationSearchScope("session_a"),
                    ConversationSearchQuery("needle", limit=50),
                )
                expected_scanned = min(count, 256)
                self.assertEqual(result.scanned_conversations, expected_scanned)
                self.assertEqual(result.matched_conversations, expected_scanned)
                self.assertEqual(len(result.hits), 50)
                self.assertEqual(
                    tuple(hit.citation.conversation_id for hit in result.hits),
                    tuple(f"conversation_{index:04d}" for index in range(50)),
                )
                self.assertIs(result.scan_truncated, count == 257)
                self.assertTrue(result.truncated)
                self.assertNotIn(
                    "conversation_0256",
                    {hit.citation.conversation_id for hit in result.hits},
                )

    def test_event_scan_stops_at_10000_and_subject_hit_skips_transcript(self) -> None:
        metadata = self.services.catalog.create("session_a", "Haystack subject")
        rows: list[tuple[object, ...]] = []
        for turn_number in range(1, 5_001):
            user_sequence = turn_number * 2 - 1
            turn_id = f"turn_{turn_number:05d}"
            rows.extend(
                (
                    (
                        "session_a",
                        metadata.conversation_id,
                        user_sequence,
                        1,
                        turn_id,
                        0,
                        "2026-08-25T00:00:00+00:00",
                        "user",
                        "plot_architect",
                        "user_message",
                        "hay",
                        "hay",
                    ),
                    (
                        "session_a",
                        metadata.conversation_id,
                        user_sequence + 1,
                        1,
                        turn_id,
                        1,
                        "2026-08-25T00:00:00+00:00",
                        "runtime",
                        "user",
                        "runtime_error",
                        "runtime diagnostic",
                        "runtime diagnostic",
                    ),
                )
            )
        rows.append(
            (
                "session_a",
                metadata.conversation_id,
                10_001,
                1,
                "turn_tail",
                0,
                "2026-08-25T00:00:00+00:00",
                "user",
                "plot_architect",
                "user_message",
                "needle at event 10001",
                "needle at event 10001",
            )
        )
        with raw_connection(self.workspace) as connection:
            connection.execute("BEGIN")
            connection.executemany(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content, "
                "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            connection.execute("COMMIT")

        bounded = self.services.search.search(
            self.scope,
            ConversationSearchQuery("needle"),
        )
        self.assertEqual(bounded.hits, ())
        self.assertEqual(bounded.scanned_events, 10_000)
        self.assertTrue(bounded.scan_truncated)
        self.assertTrue(bounded.truncated)

        with raw_connection(self.workspace) as connection:
            connection.execute(
                "DELETE FROM conversation_events WHERE session_id = ? "
                "AND conversation_id = ? AND sequence = 10001",
                ("session_a", metadata.conversation_id),
            )
        exact = self.services.search.search(
            self.scope,
            ConversationSearchQuery("needle"),
        )
        self.assertEqual(exact.hits, ())
        self.assertEqual(exact.scanned_events, 10_000)
        self.assertFalse(exact.scan_truncated)
        self.assertFalse(exact.truncated)

        with mock.patch.object(
            sqlite_module,
            "_event_and_searchable_from_row",
            side_effect=AssertionError("subject hit read its transcript"),
        ):
            subject = self.services.search.search(
                self.scope,
                ConversationSearchQuery("haystack subject"),
            )
        self.assertEqual(len(subject.hits), 1)
        self.assertEqual(subject.scanned_events, 0)

    def test_companion_create_only_publication_and_recovery_are_contained(
        self,
    ) -> None:
        path = self.index_path()
        path.unlink()
        index = SQLiteConversationSearchIndex(
            os.fspath(self.workspace.root),
            normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
        )

        def documents() -> tuple[SQLiteConversationSearchIndexDocument, ...]:
            return (
                SQLiteConversationSearchIndexDocument(
                    "session_a",
                    "conversation_a",
                    1,
                    "publication needle",
                ),
            )

        real_link = search_index_module.os.link

        def collide(
            source: str | os.PathLike[str],
            destination: str | os.PathLike[str],
            *,
            follow_symlinks: bool = True,
        ) -> None:
            del source, follow_symlinks
            Path(destination).write_bytes(b"foreign publication sentinel")
            Path(destination).chmod(0o600)
            raise FileExistsError

        with mock.patch.object(search_index_module.os, "link", side_effect=collide):
            with self.assertRaises(SQLiteConversationSearchIndexError):
                index.rebuild(
                    source_generation=0,
                    documents=documents(),
                    validate_publication=lambda: None,
                )
        self.assertEqual(path.read_bytes(), b"foreign publication sentinel")
        self.assertEqual(
            list(path.parent.glob(".conversation_storage_fts5.*.tmp")),
            [],
        )

        path.unlink()
        validation_calls = 0

        def expire_after_link() -> None:
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 3:
                raise RuntimeError("expired publication sentinel")

        with self.assertRaises(SQLiteConversationSearchIndexError) as caught:
            index.rebuild(
                source_generation=0,
                documents=documents(),
                validate_publication=expire_after_link,
            )
        self.assertEqual(
            str(caught.exception), "SQLite conversation search index is unavailable"
        )
        self.assertIsNone(caught.exception.__cause__)
        aliases = list(path.parent.glob(".conversation_storage_fts5.*.tmp"))
        self.assertEqual(len(aliases), 1)
        target_stat = path.stat(follow_symlinks=False)
        alias_stat = aliases[0].stat(follow_symlinks=False)
        self.assertEqual(target_stat.st_nlink, 2)
        self.assertEqual(
            (target_stat.st_dev, target_stat.st_ino),
            (alias_stat.st_dev, alias_stat.st_ino),
        )
        self.assertEqual(path.read_bytes(), aliases[0].read_bytes())

        with mock.patch.object(search_index_module.os, "link", wraps=real_link):
            index.rebuild(
                source_generation=0,
                documents=documents(),
                validate_publication=lambda: None,
            )
        self.assertEqual(
            list(path.parent.glob(".conversation_storage_fts5.*.tmp")),
            [],
        )
        self.assertEqual(path.stat(follow_symlinks=False).st_nlink, 1)
        self.assertEqual(
            index.candidates(
                source_generation=0,
                session_id="session_a",
                conversation_ids=("conversation_a",),
                normalized_query="publication needle",
                maximum=10,
            ),
            frozenset({("conversation_a", 1)}),
        )

    def test_search_uses_one_authoritative_snapshot_during_concurrent_write(
        self,
    ) -> None:
        metadata = self.services.catalog.create("session_a", "Snapshot subject")
        self.append_turn(
            metadata.conversation_id,
            "turn_snapshot",
            "snapshot needle before writer",
            "ordinary reply",
        )
        release_writer = threading.Event()
        updated = threading.Event()
        completed = threading.Event()
        failures: list[BaseException] = []

        def writer() -> None:
            try:
                if not release_writer.wait(timeout=2.0):
                    raise AssertionError("search did not reach the snapshot barrier")
                with raw_connection(self.workspace) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE conversation_events SET content = ?, "
                        "searchable_content = ? WHERE session_id = ? "
                        "AND conversation_id = ? AND sequence = 1",
                        (
                            "changed away by writer",
                            "changed away by writer",
                            "session_a",
                            metadata.conversation_id,
                        ),
                    )
                    updated.set()
                    connection.execute("COMMIT")
            except BaseException as exc:
                failures.append(exc)
                updated.set()
            finally:
                completed.set()

        thread = threading.Thread(target=writer)
        thread.start()

        def barrier_candidates(**_arguments: object) -> frozenset[tuple[str, int]]:
            release_writer.set()
            if not updated.wait(timeout=2.0):
                raise AssertionError("writer did not update before event scan")
            return frozenset()

        try:
            with mock.patch.object(
                SQLiteConversationSearchIndex,
                "candidates",
                side_effect=barrier_candidates,
            ):
                snapshot = self.services.search.search(
                    self.scope,
                    ConversationSearchQuery("snapshot needle"),
                )
        finally:
            release_writer.set()
            thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertTrue(completed.is_set())
        self.assertEqual(failures, [])
        self.assertEqual(len(snapshot.hits), 1)
        self.assertEqual(snapshot.hits[0].citation.sequence, 1)
        after = self.services.search.search(
            self.scope,
            ConversationSearchQuery("snapshot needle"),
        )
        self.assertEqual(after.hits, ())


class SQLiteBoundaryAndCompositionTests(unittest.TestCase):
    def test_constants_exports_and_owning_modules_are_exact(self) -> None:
        expected = (
            "SQLITE_CONVERSATION_STORAGE_APPLICATION_ID",
            "SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS",
            "SQLITE_CONVERSATION_STORAGE_DATABASE_PATH",
            "SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION",
            "SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION",
            "SQLiteConversationArchiveAdapter",
            "SQLiteConversationCatalog",
            "SQLiteConversationEventStore",
            "SQLiteConversationSearch",
            "compose_sqlite_conversation_archive",
            "compose_sqlite_conversation_storage",
            "preflight_sqlite_conversation_storage",
            "probe_sqlite_conversation_storage_runtime",
            "provision_sqlite_conversation_storage",
        )
        self.assertEqual(tuple(sqlite_module.__all__), expected)
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_APPLICATION_ID, 0x53574143)
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS, 5.0)
        self.assertEqual(
            SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
            "discussion_logs/conversation_storage.sqlite3",
        )
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION, (3, 7, 16))
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION, 1)
        self.assertEqual(
            SQLITE_CONVERSATION_SEARCH_INDEX_PATH,
            "discussion_logs/.conversation_storage_fts5.sqlite3",
        )
        self.assertEqual(
            search_index_module.SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION,
            1,
        )
        self.assertEqual(
            tuple(search_index_module.__all__),
            (
                "SQLITE_CONVERSATION_SEARCH_INDEX_PATH",
                "SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION",
                "SQLiteConversationSearchIndex",
                "SQLiteConversationSearchIndexDocument",
                "SQLiteConversationSearchIndexError",
            ),
        )
        for name in expected:
            with self.subTest(name=name):
                self.assertNotIn(name, storage_package.__all__)
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(storage_package, name))
                self.assertFalse(hasattr(story_writing_agents, name))

    def test_optional_modules_import_lazily_without_io_or_sqlite_loading(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        source = repository / "src"
        script = f"""
import pathlib
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {str(source)!r})
before = tuple(pathlib.Path.cwd().iterdir())
import story_writing_agents
import story_writing_agents.conversation_storage as package
assert 'story_writing_agents.conversation_storage.sqlite' not in sys.modules
assert 'sqlite3' not in sys.modules
import story_writing_agents.conversation_storage.sqlite as public
import story_writing_agents.conversation_storage.sqlite_database as database_owner
import story_writing_agents.conversation_storage.sqlite_search_index as index_owner
import story_writing_agents.conversation_literal_search as literal_owner
assert tuple(public.__all__) == {tuple(sqlite_module.__all__)!r}
assert tuple(database_owner.__all__) == (
    'SQLITE_APPLICATION_ID',
    'SQLITE_BUSY_TIMEOUT_SECONDS',
    'SQLITE_DATABASE_PATH',
    'SQLITE_MINIMUM_VERSION',
    'SQLITE_SCHEMA_VERSION',
    'SQLiteDatabase',
    'SQLiteDatabaseFormatError',
    'SQLiteDatabaseMissingError',
    'SQLiteDatabaseSecurityError',
    'SQLiteDatabaseUnavailableError',
    'SQLiteDatabaseVersionError',
    'probe_sqlite_runtime',
)
assert tuple(index_owner.__all__) == (
    'SQLITE_CONVERSATION_SEARCH_INDEX_PATH',
    'SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION',
    'SQLiteConversationSearchIndex',
    'SQLiteConversationSearchIndexDocument',
    'SQLiteConversationSearchIndexError',
)
assert len(literal_owner.__all__) == 6
assert 'sqlite3' not in sys.modules
assert tuple(pathlib.Path.cwd().iterdir()) == before
assert all(name not in story_writing_agents.__all__ for name in public.__all__)
assert all(name not in package.__all__ for name in public.__all__)
assert all(name not in story_writing_agents.__all__ for name in index_owner.__all__)
assert all(name not in package.__all__ for name in index_owner.__all__)
assert all(name not in story_writing_agents.__all__ for name in literal_owner.__all__)
assert all(name not in package.__all__ for name in literal_owner.__all__)
"""
        with tempfile.TemporaryDirectory(prefix="sqlite-import-test-") as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_dependency_edges_are_exact_and_defaults_do_not_select_sqlite(
        self,
    ) -> None:
        public_tree = ast.parse(
            Path(sqlite_module.__file__).read_text(encoding="utf-8")
        )
        dependencies = {
            node.module
            for node in ast.walk(public_tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            dependencies,
            {
                "archive",
                "conversation_search",
                "conversation_literal_search",
                "conversations",
                "errors",
                "workspace",
                "sqlite_database",
                "sqlite_search_index",
                "composition",
                "contracts",
            },
        )
        index_tree = ast.parse(
            Path(search_index_module.__file__).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                node.module
                for node in ast.walk(index_tree)
                if isinstance(node, ast.ImportFrom) and node.level > 0
            },
            set(),
        )

        workspace = initialized_workspace(self)
        before = storage_snapshot(workspace)
        filesystem = compose_filesystem_conversation_storage(workspace)
        self.assertIs(filesystem.backend, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(storage_snapshot(workspace), before)
        self.assertFalse(database_path(workspace).exists())

    def test_composition_returns_one_sqlite_provenance_and_shared_authority(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        self.assertIs(services.backend, ConversationStorageBackend.SQLITE)
        self.assertIsInstance(services.catalog, SQLiteConversationCatalog)
        self.assertIsInstance(services.event_store, SQLiteConversationEventStore)
        self.assertIsInstance(services.search, SQLiteConversationSearch)
        self.assertIs(
            services.catalog_binding.provenance,
            services.event_store_binding.provenance,
        )
        self.assertIs(
            services.catalog_binding.provenance,
            services.search_binding.provenance,
        )
        self.assertEqual(
            services.mutation_coordinator.workspace_key,
            services.provenance.workspace_key,
        )
        self.assertEqual(services.provenance.workspace_key, os.fspath(workspace.root))

    def test_missing_composition_fails_without_provisioning_or_default_change(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = storage_snapshot(workspace)
        with self.assertRaises(ConversationStorageProvisioningNeededError):
            compose_sqlite_conversation_storage(workspace)
        with self.assertRaises(ConversationStorageProvisioningNeededError):
            compose_sqlite_conversation_archive(workspace)
        self.assertEqual(storage_snapshot(workspace), before)
        self.assertFalse(database_path(workspace).exists())

    def test_live_journal_blocks_fresh_composition_but_not_existing_reads(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Committed subject")
        journal = Path(f"{database_path(workspace)}-journal")

        with raw_connection(workspace) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete"
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE conversations SET subject = ?, normalized_subject = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                (
                    "Uncommitted subject",
                    "uncommitted subject",
                    metadata.session_id,
                    metadata.conversation_id,
                ),
            )
            self.assertTrue(journal.is_file())
            self.assertEqual(
                services.catalog.get(
                    metadata.session_id,
                    metadata.conversation_id,
                ).subject,
                "Committed subject",
            )
            before = storage_snapshot(workspace)

            with self.assertRaises(WorkspaceFormatError) as caught:
                compose_sqlite_conversation_storage(workspace)

            self.assertEqual(
                str(caught.exception),
                "invalid SQLite conversation storage",
            )
            self.assertIsNone(caught.exception.__cause__)
            self.assertEqual(storage_snapshot(workspace), before)
            connection.execute("COMMIT")

        self.assertFalse(journal.exists())
        self.assertEqual(
            compose_sqlite_conversation_storage(workspace)
            .catalog.get(metadata.session_id, metadata.conversation_id)
            .subject,
            "Uncommitted subject",
        )

    def test_fresh_composition_and_injected_runtime_observe_persisted_context(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Persistent subject")
        user = services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                content="question with \x00 and 한🙂",
            )
        )
        reply = services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="persisted reply",
            )
        )

        fresh = compose_sqlite_conversation_storage(workspace)
        runtime = StoryRuntime(
            workspace,
            None,
            conversation_event_store=fresh.event_store,
        )
        self.assertIs(runtime.conversation_event_store, fresh.event_store)
        self.assertEqual(
            fresh.event_store.read("session_a", metadata.conversation_id),
            (user, reply),
        )
        self.assertEqual(
            fresh.event_store.read_context(
                "session_a", metadata.conversation_id
            ).events,
            (user, reply),
        )


class SQLitePreflightAndProvisionTests(unittest.TestCase):
    def test_missing_preflight_is_read_only_and_reports_provisioning_needed(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = storage_snapshot(workspace)
        result = preflight_sqlite_conversation_storage(workspace)
        self.assertIs(result.backend, ConversationStorageBackend.SQLITE)
        self.assertIs(
            result.capability,
            ConversationStorageCapability.PROVISIONING_NEEDED,
        )
        self.assertEqual(storage_snapshot(workspace), before)

    def test_provision_requires_an_active_exact_workspace_lease(self) -> None:
        workspace = initialized_workspace(self)
        other = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        before = storage_snapshot(workspace)
        with coordinator.borrow(timeout_seconds=1.0) as wrong:
            with self.assertRaises(ConversationStorageCoordinationError):
                provision_sqlite_conversation_storage(workspace, lease=wrong)
        self.assertEqual(storage_snapshot(workspace), before)

        own = WorkspaceConversationStorageMutationCoordinator(workspace)
        with own.borrow(timeout_seconds=1.0) as released:
            pass
        with self.assertRaises(ConversationStorageCoordinationError):
            provision_sqlite_conversation_storage(workspace, lease=released)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_provision_rejects_a_lease_used_from_another_thread(self) -> None:
        workspace = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        failures: list[BaseException] = []
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            thread = threading.Thread(
                target=lambda: _capture_failure(
                    failures,
                    lambda: provision_sqlite_conversation_storage(
                        workspace,
                        lease=lease,
                    ),
                )
            )
            thread.start()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConversationStorageCoordinationError)
        self.assertFalse(database_path(workspace).exists())

    def test_provision_creates_exact_database_and_is_byte_idempotent(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        target = database_path(workspace)
        first = target.read_bytes()
        self.assertTrue(first.startswith(b"SQLite format 3\x00"))
        self.assertEqual(
            int.from_bytes(first[68:72], "big"),
            SQLITE_CONVERSATION_STORAGE_APPLICATION_ID,
        )
        self.assertEqual(int.from_bytes(first[60:64], "big"), 1)
        self.assertFalse(
            any(
                Path(f"{target}{suffix}").exists()
                for suffix in ("-journal", "-wal", "-shm")
            )
        )
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*.tmp")), []
        )

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            result = provision_sqlite_conversation_storage(workspace, lease=lease)
        self.assertIs(result.capability, ConversationStorageCapability.READY)
        self.assertEqual(target.read_bytes(), first)

    def test_normalized_subject_tamper_is_rejected_without_preflight_repair(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "  Café\u00a0Story  ")
        with raw_connection(workspace) as connection:
            connection.execute(
                "UPDATE conversations SET normalized_subject = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                ("wrong-normalized-value", "session_a", metadata.conversation_id),
            )
        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceFormatError):
            preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_searchable_content_tamper_is_rejected_without_repair_or_leak(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Searchable integrity")
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                content="Café searchable sentinel",
            )
        )
        with raw_connection(workspace) as connection:
            connection.execute(
                "UPDATE conversation_events SET searchable_content = ? "
                "WHERE session_id = ? AND conversation_id = ? AND sequence = 1",
                ("wrong lexical sentinel", "session_a", metadata.conversation_id),
            )
        before = storage_snapshot(workspace)
        for operation in (
            lambda: preflight_sqlite_conversation_storage(workspace),
            lambda: services.search.search(
                ConversationSearchScope("session_a"),
                ConversationSearchQuery("café"),
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(WorkspaceFormatError) as caught:
                    operation()
                self.assertEqual(
                    str(caught.exception),
                    "invalid SQLite conversation storage",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertNotIn("sentinel", str(caught.exception).casefold())
                self.assertEqual(storage_snapshot(workspace), before)

    def test_foreign_key_orphan_is_rejected_without_preflight_repair(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        with raw_connection(workspace) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content, "
                "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "session_a",
                    "missing_conversation",
                    1,
                    1,
                    "turn_a",
                    0,
                    "2026-08-25T00:00:00+00:00",
                    "user",
                    "plot_architect",
                    "user_message",
                    "orphan",
                    "orphan",
                ),
            )
        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceFormatError):
            preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_catalog_create_rejects_orphan_id_collision_before_metadata_insert(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        allocated_hex = "0123456789abcdef0123456789abcdef"
        conversation_id = f"c_{allocated_hex}"
        with raw_connection(workspace) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content, "
                "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "session_a",
                    conversation_id,
                    1,
                    1,
                    "orphan_turn",
                    0,
                    "2026-08-25T00:00:00+00:00",
                    "user",
                    "plot_architect",
                    "user_message",
                    "orphan event",
                    "orphan event",
                ),
            )
        before = storage_snapshot(workspace)
        allocated_uuid = mock.Mock(hex=allocated_hex)

        with (
            mock.patch.object(
                sqlite_module,
                "uuid4",
                return_value=allocated_uuid,
            ) as uuid4,
            mock.patch.object(
                sqlite_module,
                "_insert_metadata",
                wraps=sqlite_module._insert_metadata,
            ) as insert_metadata,
        ):
            with self.assertRaises(WorkspaceFormatError) as caught:
                services.catalog.create("session_a", "Collision witness")

        uuid4.assert_called_once_with()
        insert_metadata.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)
        with raw_connection(workspace) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM conversations "
                    "WHERE session_id = ? AND conversation_id = ?",
                    ("session_a", conversation_id),
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT content FROM conversation_events "
                    "WHERE session_id = ? AND conversation_id = ?",
                    ("session_a", conversation_id),
                ).fetchall(),
                [("orphan event",)],
            )


class SQLiteArchiveTests(unittest.TestCase):
    @staticmethod
    def _insert_orphan(
        workspace: Workspace,
        *,
        session_id: str,
        conversation_id: str,
    ) -> None:
        with raw_connection(workspace) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content, "
                "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    conversation_id,
                    1,
                    1,
                    "orphan_turn",
                    0,
                    "2026-08-25T00:00:00+00:00",
                    "user",
                    "plot_architect",
                    "user_message",
                    "orphan event",
                    "orphan event",
                ),
            )

    def _populated_archive(
        self,
    ) -> tuple[
        Workspace,
        ConversationStorageServices,
        SQLiteConversationArchiveAdapter,
    ]:
        workspace, services = provisioned_services(self)
        first = services.catalog.create("session_b", "Second subject")
        second = services.catalog.create("session_a", "  First subject  ")
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=second.conversation_id,
                turn_id="turn_a",
                content="exact \x00 한🙂 content",
            )
        )
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=second.conversation_id,
                turn_id="turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="reply",
            )
        )
        services.catalog.archive("session_b", first.conversation_id)
        return workspace, services, compose_sqlite_conversation_archive(workspace)

    def test_export_is_exact_canonical_and_deterministic(self) -> None:
        workspace, services, archive = self._populated_archive()
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            first = archive.export_archive(lease)
            second = archive.export_archive(lease)
        self.assertEqual(first, second)
        conversations = _archive_conversations(first)
        identities = tuple(
            (conversation.session_id, conversation.conversation_id)
            for conversation in conversations
        )
        self.assertEqual(
            identities,
            tuple(
                (item.session_id, item.conversation_id)
                for item in sorted(
                    services.catalog.list("session_a", include_archived=True)
                    + services.catalog.list("session_b", include_archived=True),
                    key=lambda item: (
                        item.session_id,
                        item.created_at,
                        item.conversation_id,
                    ),
                )
            ),
        )
        event_contents = tuple(
            event.content
            for conversation in conversations
            for event in conversation.events
        )
        self.assertIn("exact \x00 한🙂 content", event_contents)

    def test_archive_round_trip_and_identical_reimport_are_exact(self) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target = initialized_workspace(self)
        provision(target)
        target_archive = compose_sqlite_conversation_archive(target)
        target_coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            imported = target_archive.import_archive(lease, payload)
            exact = target_archive.export_archive(lease)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(exact, payload)

        before = storage_snapshot(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            unchanged = target_archive.import_archive(lease, payload)
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(storage_snapshot(target), before)

    def test_archive_import_subject_revision_sqlite_bounds_are_exact(self) -> None:
        maximum = (1 << 63) - 1
        source, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Archive bound witness")
        source_archive = compose_sqlite_conversation_archive(source)
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)
        conversations = _archive_conversations(payload)
        self.assertEqual(len(conversations), 1)
        payload_metadata = conversations[0]
        self.assertEqual(
            (payload_metadata.session_id, payload_metadata.conversation_id),
            (metadata.session_id, metadata.conversation_id),
        )

        object.__setattr__(payload_metadata, "subject_revision", maximum)
        maximum_target = initialized_workspace(self)
        provision(maximum_target)
        maximum_archive = compose_sqlite_conversation_archive(maximum_target)
        maximum_coordinator = WorkspaceConversationStorageMutationCoordinator(
            maximum_target
        )
        with maximum_coordinator.borrow(timeout_seconds=2.0) as lease:
            imported = maximum_archive.import_archive(lease, payload)
            exact = maximum_archive.export_archive(lease)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(exact, payload)
        maximum_services = compose_sqlite_conversation_storage(maximum_target)
        self.assertEqual(
            maximum_services.catalog.get(
                metadata.session_id,
                metadata.conversation_id,
            ).subject_revision,
            maximum,
        )

        object.__setattr__(payload_metadata, "subject_revision", maximum + 1)
        overflow_target = initialized_workspace(self)
        provision(overflow_target)
        overflow_archive = compose_sqlite_conversation_archive(overflow_target)
        overflow_coordinator = WorkspaceConversationStorageMutationCoordinator(
            overflow_target
        )
        with raw_connection(overflow_target) as connection:
            logical_before = (
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
            )
        self.assertEqual(logical_before, ((), ()))
        before = storage_snapshot(overflow_target)
        with (
            mock.patch.object(
                sqlite_module,
                "_insert_metadata",
                wraps=sqlite_module._insert_metadata,
            ) as insert_metadata,
            overflow_coordinator.borrow(timeout_seconds=2.0) as lease,
        ):
            with self.assertRaises(WorkspaceFormatError) as caught:
                overflow_archive.import_archive(lease, payload)

        insert_metadata.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("overflow", str(caught.exception).casefold())
        self.assertNotIn("too large", str(caught.exception).casefold())
        self.assertNotIn(payload_metadata.subject, str(caught.exception))
        self.assertEqual(storage_snapshot(overflow_target), before)
        with raw_connection(overflow_target) as connection:
            logical_after = (
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
            )
        self.assertEqual(logical_after, logical_before)
        overflow_services = compose_sqlite_conversation_storage(overflow_target)
        self.assertEqual(
            overflow_services.catalog.list("session_a", include_archived=True),
            (),
        )

    def test_archive_canonicalizes_integer_subtypes_before_sqlite_sinks(
        self,
    ) -> None:
        class IntegerSubtype(int):
            """One non-exact integer accepted by domain value validation."""

        def logical_rows(
            workspace: Workspace,
        ) -> tuple[tuple[object, ...], tuple[object, ...]]:
            with raw_connection(workspace) as connection:
                return (
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
                )

        source, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Subtype payload subject")
        event = services.event_store.append(
            make_append(
                session_id=metadata.session_id,
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                content="subtype payload event content",
            )
        )
        source_archive = compose_sqlite_conversation_archive(source)
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)
        conversations = _archive_conversations(payload)
        self.assertEqual(len(conversations), 1)
        payload_metadata = conversations[0]
        self.assertEqual(len(payload_metadata.events), 1)
        payload_event = payload_metadata.events[0]
        cases = (
            ("metadata schema version", payload_metadata, "schema_version", True),
            ("subject revision", payload_metadata, "subject_revision", False),
            ("event schema version", payload_event, "schema_version", True),
            ("event sequence", payload_event, "sequence", False),
        )

        for label, owner, field, rejected in cases:
            with self.subTest(field=label):
                original = getattr(owner, field)
                object.__setattr__(owner, field, IntegerSubtype(1))
                try:
                    target = initialized_workspace(self)
                    provision(target)
                    archive = compose_sqlite_conversation_archive(target)
                    coordinator = WorkspaceConversationStorageMutationCoordinator(
                        target
                    )
                    logical_before = logical_rows(target)
                    self.assertEqual(logical_before, ((), ()))
                    before = storage_snapshot(target)
                    with (
                        mock.patch.object(
                            sqlite_module,
                            "_insert_metadata",
                            wraps=sqlite_module._insert_metadata,
                        ) as insert_metadata,
                        mock.patch.object(
                            sqlite_module,
                            "_insert_event",
                            wraps=sqlite_module._insert_event,
                        ) as insert_event,
                        coordinator.borrow(timeout_seconds=2.0) as lease,
                    ):
                        if rejected:
                            with self.assertRaises(
                                ConversationArchiveFormatError
                            ) as caught:
                                archive.import_archive(lease, payload)
                        else:
                            receipt = archive.import_archive(lease, payload)
                            exact = archive.export_archive(lease)

                    if rejected:
                        insert_metadata.assert_not_called()
                        insert_event.assert_not_called()
                        self.assertEqual(
                            str(caught.exception),
                            "conversation archive format is invalid",
                        )
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertNotIn(
                            "integersubtype",
                            str(caught.exception).casefold(),
                        )
                        self.assertNotIn(metadata.subject, str(caught.exception))
                        self.assertNotIn(event.content, str(caught.exception))
                        self.assertEqual(storage_snapshot(target), before)
                        self.assertEqual(logical_rows(target), logical_before)
                    else:
                        self.assertIs(
                            receipt.status,
                            ConversationArchiveImportStatus.IMPORTED,
                        )
                        insert_metadata.assert_called_once()
                        insert_event.assert_called_once()
                        exact_conversation = _archive_conversations(exact)[0]
                        self.assertIs(type(exact_conversation.subject_revision), int)
                        self.assertIs(type(exact_conversation.events[0].sequence), int)
                finally:
                    object.__setattr__(owner, field, original)

    def test_event_insert_signed_maximum_is_inclusive_at_sink(self) -> None:
        maximum = (1 << 63) - 1

        def event_with_sequence(sequence: int) -> ConversationEvent:
            return ConversationEvent(
                schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
                session_id="session_a",
                conversation_id="conversation_a",
                turn_id="turn_a",
                sequence=sequence,
                timestamp="2026-08-25T00:00:00+00:00",
                sender="user",
                recipient="plot_architect",
                kind=ConversationEventKind.USER_MESSAGE,
                content="sink boundary event",
            )

        connection = mock.Mock()
        sqlite_module._insert_event(connection, event_with_sequence(maximum))
        connection.execute.assert_called_once()
        sql, parameters = connection.execute.call_args.args
        self.assertIn("INSERT INTO conversation_events", sql)
        self.assertEqual(parameters[2], maximum)
        self.assertIs(type(parameters[2]), int)

        connection.reset_mock()
        with self.assertRaises(sqlite_module.SQLiteDatabaseFormatError) as caught:
            sqlite_module._insert_event(
                connection,
                event_with_sequence(maximum + 1),
            )
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__cause__)
        connection.execute.assert_not_called()

    def test_archive_divergence_rolls_back_earlier_imports(self) -> None:
        source, _, source_archive = self._populated_archive()
        coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)
        conversations = _archive_conversations(payload)
        self.assertGreaterEqual(len(conversations), 2)

        target = initialized_workspace(self)
        provision(target)
        conflict = conversations[-1]
        with raw_connection(target) as connection:
            connection.execute(
                "INSERT INTO conversations "
                "(session_id, conversation_id, schema_version, subject, "
                "normalized_subject, subject_revision, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conflict.session_id,
                    conflict.conversation_id,
                    conflict.schema_version,
                    "divergent subject",
                    normalize_conversation_subject("divergent subject"),
                    conflict.subject_revision,
                    conflict.status.value,
                    conflict.created_at,
                    conflict.updated_at,
                ),
            )
        before = storage_snapshot(target)
        target_archive = compose_sqlite_conversation_archive(target)
        target_coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationArchiveConflictError):
                target_archive.import_archive(lease, payload)
        self.assertEqual(storage_snapshot(target), before)
        target_services = compose_sqlite_conversation_storage(target)
        self.assertEqual(
            sum(
                len(target_services.catalog.list(session, include_archived=True))
                for session in ("session_a", "session_b")
            ),
            1,
        )

    def test_archive_export_rejects_any_global_orphan_without_change(self) -> None:
        workspace, _, archive = self._populated_archive()
        self._insert_orphan(
            workspace,
            session_id="unrelated_session",
            conversation_id="unrelated_conversation",
        )
        before = storage_snapshot(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(WorkspaceFormatError) as caught:
                archive.export_archive(lease)
        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_archive_import_preflight_rejects_same_and_unrelated_orphans(
        self,
    ) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)
        same_identity = next(
            (
                conversation.session_id,
                conversation.conversation_id,
            )
            for conversation in _archive_conversations(payload)
            if conversation.events
        )

        for label, identity in (
            ("same identity", same_identity),
            ("unrelated identity", ("orphan_session", "orphan_conversation")),
        ):
            with self.subTest(case=label):
                target = initialized_workspace(self)
                provision(target)
                target_archive = compose_sqlite_conversation_archive(target)
                self._insert_orphan(
                    target,
                    session_id=identity[0],
                    conversation_id=identity[1],
                )
                before = storage_snapshot(target)
                with raw_connection(target) as connection:
                    logical_before = tuple(
                        connection.execute(
                            "SELECT * FROM conversation_events "
                            "ORDER BY session_id, conversation_id, sequence"
                        ).fetchall()
                    )
                coordinator = WorkspaceConversationStorageMutationCoordinator(target)
                with (
                    mock.patch.object(
                        sqlite_module,
                        "_insert_metadata",
                        wraps=sqlite_module._insert_metadata,
                    ) as insert_metadata,
                    coordinator.borrow(timeout_seconds=2.0) as lease,
                ):
                    with self.assertRaises(WorkspaceFormatError):
                        target_archive.import_archive(lease, payload)
                insert_metadata.assert_not_called()
                self.assertEqual(storage_snapshot(target), before)
                with raw_connection(target) as connection:
                    logical_after = tuple(
                        connection.execute(
                            "SELECT * FROM conversation_events "
                            "ORDER BY session_id, conversation_id, sequence"
                        ).fetchall()
                    )
                self.assertEqual(logical_after, logical_before)

    def test_archive_postvalidation_corruption_rolls_back_every_import(self) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target = initialized_workspace(self)
        provision(target)
        target_archive = compose_sqlite_conversation_archive(target)
        coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        before = storage_snapshot(target)
        real_validator = sqlite_module._validate_database_snapshot
        validation_calls = 0

        def corrupt_before_second_validation(connection: object) -> None:
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 2:
                connection.execute(  # type: ignore[attr-defined]
                    "UPDATE conversations "
                    "SET normalized_subject = 'post-validation-corruption' "
                    "WHERE rowid = (SELECT MIN(rowid) FROM conversations)"
                )
            real_validator(connection)

        with (
            mock.patch.object(
                sqlite_module,
                "_validate_database_snapshot",
                corrupt_before_second_validation,
            ),
            coordinator.borrow(timeout_seconds=2.0) as lease,
        ):
            with self.assertRaises(WorkspaceFormatError):
                target_archive.import_archive(lease, payload)
        self.assertEqual(validation_calls, 2)
        self.assertEqual(storage_snapshot(target), before)
        services = compose_sqlite_conversation_storage(target)
        self.assertEqual(services.catalog.list("session_a", include_archived=True), ())
        self.assertEqual(services.catalog.list("session_b", include_archived=True), ())

    def test_archive_import_preserves_unrelated_exact_destination_records(
        self,
    ) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target, services = provisioned_services(self)
        extra_metadata = services.catalog.create("extra_session", "Extra subject")
        extra_event = services.event_store.append(
            make_append(
                session_id="extra_session",
                conversation_id=extra_metadata.conversation_id,
                turn_id="extra_turn",
                content="preserve exact extra event",
            )
        )
        target_archive = compose_sqlite_conversation_archive(target)
        coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            imported = target_archive.import_archive(lease, payload)
            combined = target_archive.export_archive(lease)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(
            services.catalog.get("extra_session", extra_metadata.conversation_id),
            extra_metadata,
        )
        self.assertEqual(
            services.event_store.read("extra_session", extra_metadata.conversation_id),
            (extra_event,),
        )
        payload_conversations = _archive_conversations(payload)
        combined_conversations = _archive_conversations(combined)
        self.assertTrue(
            all(
                conversation in combined_conversations
                for conversation in payload_conversations
            )
        )
        self.assertEqual(len(combined_conversations), len(payload_conversations) + 1)

        before = storage_snapshot(target)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            unchanged = target_archive.import_archive(lease, payload)
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(storage_snapshot(target), before)

    def test_archive_rejects_wrong_released_and_malformed_evidence_without_change(
        self,
    ) -> None:
        workspace, _, archive = self._populated_archive()
        other = initialized_workspace(self)
        other_coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        before = storage_snapshot(workspace)
        with other_coordinator.borrow(timeout_seconds=2.0) as wrong:
            with self.assertRaises(ConversationStorageCoordinationError):
                archive.export_archive(wrong)
        self.assertEqual(storage_snapshot(workspace), before)

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as released:
            payload = archive.export_archive(released)
        with self.assertRaises(ConversationStorageCoordinationError):
            archive.export_archive(released)
        object.__setattr__(payload, "sessions", list(payload.sessions))
        before = storage_snapshot(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationArchiveFormatError):
                archive.import_archive(lease, payload)
        self.assertEqual(storage_snapshot(workspace), before)


class SQLiteConcurrencyTests(unittest.TestCase):
    def test_threaded_sequence_and_turn_slot_races_are_atomic(self) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Thread races")
        workers = 6
        barrier = threading.Barrier(workers)

        def append_user(index: int):  # type: ignore[no-untyped-def]
            barrier.wait(timeout=3.0)
            return services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id=f"turn_{index}",
                    content=f"question-{index}",
                )
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            users = tuple(executor.map(append_user, range(workers)))
        for index in range(workers):
            services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id=f"turn_{index}",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=f"reply-{index}",
                )
            )
        events = services.event_store.read("session_a", metadata.conversation_id)
        self.assertEqual(tuple(event.sequence for event in events), tuple(range(1, 13)))
        self.assertEqual({event.sequence for event in users}, set(range(1, 7)))

        race_barrier = threading.Barrier(2)

        def same_turn_user():  # type: ignore[no-untyped-def]
            race_barrier.wait(timeout=3.0)
            return services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id="same_turn",
                )
            )

        successes, failures = _run_two_way_race(same_turn_user)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)

    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_spawned_process_appends_have_one_contiguous_history(self) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Process sequencing")
        processes = [
            _spawn_worker(
                "append",
                workspace,
                "session_a",
                metadata.conversation_id,
                f"process_turn_{index}",
            )
            for index in range(3)
        ]
        self.addCleanup(lambda: _cleanup_processes(processes))
        for process in processes:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline(), "ready\n")
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("x")
            process.stdin.flush()
        child_sequences: list[int] = []
        for process in processes:
            assert process.stdout is not None
            output = process.stdout.readline()
            process.wait(timeout=10.0)
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.assertEqual(process.returncode, 0, stderr)
            child_sequences.extend(json.loads(output))

        events = services.event_store.read("session_a", metadata.conversation_id)
        self.assertEqual(tuple(event.sequence for event in events), tuple(range(1, 7)))
        self.assertEqual(sorted(child_sequences), list(range(1, 7)))

    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_spawned_process_provisioners_install_one_complete_database(self) -> None:
        workspace = initialized_workspace(self)
        processes = [_spawn_worker("provision", workspace) for _ in range(2)]
        self.addCleanup(lambda: _cleanup_processes(processes))
        for process in processes:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline(), "ready\n")
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("x")
            process.stdin.flush()
        for process in processes:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline(), "provisioned\n")
            process.wait(timeout=10.0)
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.assertEqual(process.returncode, 0, stderr)
        self.assertIs(
            preflight_sqlite_conversation_storage(workspace).capability,
            ConversationStorageCapability.READY,
        )
        target = database_path(workspace)
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*.tmp")), []
        )


def _capture_failure(failures: list[BaseException], operation) -> None:  # type: ignore[no-untyped-def]
    try:
        operation()
    except BaseException as exc:
        failures.append(exc)


def _run_two_way_race(operation):  # type: ignore[no-untyped-def]
    successes: list[object] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            successes.append(operation())
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise AssertionError("race worker did not terminate")
    return successes, failures


def _spawn_worker(
    mode: str,
    workspace,
    session: str = "session_a",
    conversation: str = "conversation_a",
    turn: str = "turn_a",
):  # type: ignore[no-untyped-def]
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(repository / "src"), str(repository))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.sqlite_conversation_storage_fixture",
            mode,
            os.fspath(workspace.root),
            session,
            conversation,
            turn,
        ],
        cwd=repository,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _cleanup_processes(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


if __name__ == "__main__":
    unittest.main()
