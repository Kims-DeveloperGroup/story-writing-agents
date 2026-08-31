"""Conversation search, citations, and bounded transcript-page tests."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import unittest
import unicodedata
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import story_writing_agents
from story_writing_agents import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_MEMORY_POLICY,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationCitation,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMemoryPolicy,
    ConversationMemoryService,
    ConversationMetadata,
    ConversationSearchHit,
    ConversationSearch,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    ConversationStatus,
    ConversationTranscriptCursor,
    ConversationTranscriptPage,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    ReferenceConversationSearch,
    SessionIdError,
)
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents import conversation_search as search_module
from story_writing_agents import conversation_literal_search as literal_module
from story_writing_agents.conversation_literal_search import (
    CONVERSATION_LITERAL_NORMALIZATION_VERSION,
    build_conversation_literal_snippet,
    classify_conversation_literal_subject,
    conversation_literal_sort_key,
    normalize_conversation_literal_query,
    normalize_conversation_literal_text,
)
from story_writing_agents.conversation_search import (
    COLLABORATION_READABLE_SOURCE_SCHEMA_ID,
    CollaborationHistoryPageCursor,
    CollaborationHistorySearchQuery,
    collaboration_readable_document_id,
    continue_collaboration_history,
    page_collaboration_history,
    resolve_collaboration_citation,
    search_collaboration_history,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import (
    CollaborationEventKind,
    CollaborationMessagePayload,
    CollaborationReadableFieldKey,
    CollaborationStepState,
    project_collaboration_readable_history,
)

from tests.conversation_search_contract import (
    BASE_TIME,
    ConversationSearchContractMixin,
    MemoryConversationCatalog,
    MemoryConversationEventStore,
    append_turn,
)
from tests.collaboration_contract_fixture import (
    RUNTIME,
    USER,
    awaiting_history,
    executed_history,
    make_batch,
    make_draft,
    make_proposal,
    proposal_state,
    store_batch,
)
from tests.helpers import initialized_workspace


def metadata(
    conversation_id: str = "conversation_a",
    *,
    session_id: str = "session_a",
    subject: str = "Subject",
    created_offset: int = 0,
) -> ConversationMetadata:
    timestamp = BASE_TIME.replace(microsecond=created_offset).isoformat()
    return ConversationMetadata(
        schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
        session_id=session_id,
        conversation_id=conversation_id,
        subject=subject,
        subject_revision=1,
        status=ConversationStatus.ACTIVE,
        created_at=timestamp,
        updated_at=timestamp,
    )


def stored_event(
    sequence: int,
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
    turn_id: str | None = None,
    kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
    content: str = "content",
) -> ConversationEvent:
    turn = f"turn_{sequence}" if turn_id is None else turn_id
    if kind is ConversationEventKind.USER_MESSAGE:
        sender, recipient = "user", PLOT_ARCHITECT
    elif kind is ConversationEventKind.AGENT_REPLY:
        sender, recipient = PLOT_ARCHITECT, "user"
    else:
        sender, recipient = "runtime", "user"
    return ConversationEvent(
        schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn,
        sequence=sequence,
        timestamp=BASE_TIME.isoformat(),
        sender=sender,
        recipient=recipient,
        kind=kind,
        content=content,
    )


class InMemoryConversationSearchContractTests(
    ConversationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[MemoryConversationCatalog, MemoryConversationEventStore]:
        catalog = MemoryConversationCatalog()
        return catalog, MemoryConversationEventStore(catalog)


class FilesystemConversationSearchContractTests(
    ConversationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[FilesystemConversationCatalog, FilesystemConversationEventStore]:
        self.workspace = initialized_workspace(self)
        self.services = compose_filesystem_conversation_storage(self.workspace)
        if not isinstance(self.services.catalog, FilesystemConversationCatalog):
            raise AssertionError("filesystem composition returned the wrong catalog")
        if not isinstance(
            self.services.event_store,
            FilesystemConversationEventStore,
        ):
            raise AssertionError(
                "filesystem composition returned the wrong event store"
            )
        return self.services.catalog, self.services.event_store

    def make_search(
        self,
        catalog: object,
        store: object,
    ) -> ReferenceConversationSearch:
        del catalog, store
        if not isinstance(self.services.search, ReferenceConversationSearch):
            raise AssertionError("filesystem composition returned the wrong search")
        return self.services.search


class ConversationSearchValueContractTests(unittest.TestCase):
    def test_literal_search_helpers_are_exact_pure_and_deterministic(self) -> None:
        expected_exports = (
            "CONVERSATION_LITERAL_NORMALIZATION_VERSION",
            "build_conversation_literal_snippet",
            "classify_conversation_literal_subject",
            "conversation_literal_sort_key",
            "normalize_conversation_literal_query",
            "normalize_conversation_literal_text",
        )
        self.assertEqual(tuple(literal_module.__all__), expected_exports)
        self.assertEqual(CONVERSATION_LITERAL_NORMALIZATION_VERSION, 1)
        self.assertEqual(
            normalize_conversation_literal_text(
                "  ＳＴＲＡＳＳＥ\u200b\tStraße\x00한🙂  "
            ),
            "strasse strasse 한🙂",
        )
        self.assertEqual(
            normalize_conversation_literal_query(
                "  Straße   VESSEL ", max_characters=20
            ),
            "strasse vessel",
        )
        for subject, query, expected in (
            ("needle", "needle", "exact_subject"),
            ("needle notes", "needle", "subject_prefix"),
            ("opening needle notes", "needle", "subject_substring"),
            ("unrelated", "needle", None),
        ):
            with self.subTest(subject=subject):
                self.assertEqual(
                    classify_conversation_literal_subject(subject, query),
                    expected,
                )
        self.assertEqual(
            conversation_literal_sort_key(
                "transcript",
                "normalized subject",
                "conversation_a",
            ),
            (3, "normalized subject", "conversation_a"),
        )
        snippet = build_conversation_literal_snippet(
            normalize_conversation_literal_text(
                "p" * 100 + "straße target" + "s" * 300
            ),
            "strasse target",
            max_characters=100,
            max_utf8_bytes=200,
        )
        self.assertIn("strasse target", snippet)
        self.assertLessEqual(len(snippet), 100)
        self.assertLessEqual(len(snippet.encode("utf-8")), 200)

        for value in ("x\x00y", "x\u200by", "   ", "x" * 21, "\ud800"):
            with self.subTest(value=repr(value)):
                with self.assertRaises((TypeError, ValueError)):
                    normalize_conversation_literal_query(value, max_characters=20)
        for call in (
            lambda: normalize_conversation_literal_text(None),  # type: ignore[arg-type]
            lambda: classify_conversation_literal_subject("subject", "\ud800"),
            lambda: conversation_literal_sort_key("unknown", "subject", "id"),
            lambda: build_conversation_literal_snippet(
                "haystack",
                "needle",
                max_characters=10,
                max_utf8_bytes=10,
            ),
        ):
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()

    def test_exact_enums_policy_fields_and_defaults(self) -> None:
        self.assertEqual(
            tuple(ConversationSearchMatchKind),
            (
                ConversationSearchMatchKind.EXACT_SUBJECT,
                ConversationSearchMatchKind.SUBJECT_PREFIX,
                ConversationSearchMatchKind.SUBJECT_SUBSTRING,
                ConversationSearchMatchKind.TRANSCRIPT,
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationSearchMatchKind),
            (
                "exact_subject",
                "subject_prefix",
                "subject_substring",
                "transcript",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationMemoryPolicy)),
            (
                "max_query_characters",
                "default_search_hits",
                "max_search_hits",
                "max_scan_conversations",
                "max_scan_events",
                "max_snippet_characters",
                "max_snippet_utf8_bytes",
                "default_page_events",
                "max_page_events",
                "max_page_utf8_bytes",
            ),
        )
        self.assertEqual(
            CONVERSATION_MEMORY_POLICY,
            ConversationMemoryPolicy(
                200,
                10,
                50,
                256,
                10_000,
                240,
                1_024,
                24,
                100,
                32_768,
            ),
        )

    def test_exact_value_field_order(self) -> None:
        expected = {
            ConversationSearchScope: ("session_id",),
            ConversationSearchQuery: ("text", "limit", "include_archived"),
            ConversationCitation: (
                "session_id",
                "conversation_id",
                "turn_id",
                "sequence",
                "event_kind",
            ),
            ConversationSearchHit: (
                "citation",
                "subject",
                "subject_revision",
                "status",
                "updated_at",
                "match_kind",
                "snippet",
            ),
            ConversationSearchResult: (
                "query",
                "normalized_query",
                "hits",
                "matched_conversations",
                "scanned_conversations",
                "scanned_events",
                "scan_truncated",
                "truncated",
            ),
            ConversationTranscriptCursor: (
                "session_id",
                "conversation_id",
                "next_sequence",
                "end_sequence",
                "snapshot_last_sequence",
                "turn_id",
                "max_events",
                "citation",
            ),
            ConversationTranscriptPage: (
                "session_id",
                "conversation_id",
                "events",
                "requested_start_sequence",
                "requested_end_sequence",
                "snapshot_last_sequence",
                "returned_utf8_bytes",
                "next_cursor",
                "truncated",
            ),
        }
        for value_type, names in expected.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(tuple(field.name for field in fields(value_type)), names)
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertTrue(hasattr(value_type, "__slots__"))

    def test_values_are_frozen_and_query_rejects_malformed_text_and_limits(self) -> None:
        query = ConversationSearchQuery("council")
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            query.limit = 2  # type: ignore[misc]
        invalid = (
            "",
            "   ",
            "x\x00y",
            "x\u200by",
            "x" * 201,
            "\ufb03" * 100,
            "\ud800",
        )
        for text in invalid:
            with self.subTest(text=repr(text)):
                with self.assertRaises((TypeError, ValueError)):
                    ConversationSearchQuery(text)
        for limit in (True, 0, 51, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises((TypeError, ValueError)):
                    ConversationSearchQuery("valid", limit=limit)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ConversationSearchQuery("valid", include_archived=1)  # type: ignore[arg-type]
        self.assertEqual(ConversationSearchQuery("x" * 200).text, "x" * 200)
        normalized_at_limit = "\ufb03" * 66 + "aa"
        self.assertEqual(len(unicodedata.normalize("NFKC", normalized_at_limit)), 200)
        ConversationSearchQuery(normalized_at_limit)

        class StringSubclass(str):
            pass

        class IntegerSubclass(int):
            pass

        with self.assertRaises(TypeError):
            ConversationSearchScope(None)  # type: ignore[arg-type]
        with self.assertRaises(SessionIdError):
            ConversationSearchScope("unsafe/session")
        with self.assertRaises(TypeError):
            ConversationSearchQuery(StringSubclass("valid"))
        with self.assertRaises(TypeError):
            ConversationSearchQuery("valid", limit=IntegerSubclass(1))

    def test_citation_optional_event_fields_are_atomic(self) -> None:
        ConversationCitation("session_a", "conversation_a", None, None, None)
        ConversationCitation(
            "session_a",
            "conversation_a",
            "turn_a",
            1,
            ConversationEventKind.USER_MESSAGE,
        )
        for values in (
            ("turn_a", None, None),
            (None, 1, ConversationEventKind.USER_MESSAGE),
            ("turn_a", 1, None),
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ConversationCitation(
                        "session_a",
                        "conversation_a",
                        *values,
                    )

    def test_result_rejects_more_matches_than_scanned_conversations(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "matched conversations exceed scanned conversations",
        ):
            ConversationSearchResult(
                ConversationSearchQuery("needle"),
                "needle",
                (),
                2,
                1,
                0,
                False,
                True,
            )

        class TupleSubclass(tuple):
            pass

        with self.assertRaises(TypeError):
            ConversationSearchResult(
                ConversationSearchQuery("needle"),
                "needle",
                TupleSubclass(),
                0,
                0,
                0,
                False,
                False,
            )

    def test_page_rejects_an_impossible_empty_cursor(self) -> None:
        cursor = ConversationTranscriptCursor(
            "session_a",
            "conversation_a",
            1,
            5,
            5,
            None,
            1,
            None,
        )
        with self.assertRaisesRegex(ValueError, "empty transcript page"):
            ConversationTranscriptPage(
                "session_a",
                "conversation_a",
                (),
                5,
                5,
                5,
                0,
                cursor,
                True,
            )

    def test_protocol_and_service_signatures_are_stable(self) -> None:
        self.assertTrue(getattr(search_module.ConversationSearch, "_is_protocol", False))
        self.assertEqual(
            tuple(inspect.signature(ReferenceConversationSearch).parameters),
            ("catalog", "event_store"),
        )
        service_parameters = inspect.signature(ConversationMemoryService).parameters
        self.assertEqual(
            tuple(service_parameters),
            (
                "scope",
                "catalog",
                "event_store",
                "search",
                "conversation_storage_services",
            ),
        )
        self.assertIs(
            service_parameters["search"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(service_parameters["search"].default)
        self.assertIs(
            service_parameters["conversation_storage_services"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(
            service_parameters["conversation_storage_services"].default
        )
        self.assertEqual(
            tuple(inspect.signature(ConversationSearch.search).parameters),
            ("self", "scope", "query"),
        )
        self.assertEqual(
            tuple(inspect.signature(ConversationMemoryService.search).parameters),
            ("self", "query"),
        )
        read_signature = inspect.signature(ConversationMemoryService.read_transcript)
        self.assertEqual(
            tuple(read_signature.parameters),
            (
                "self",
                "conversation_id",
                "start_sequence",
                "end_sequence",
                "turn_id",
                "max_events",
                "citation",
            ),
        )
        for name in (
            "start_sequence",
            "end_sequence",
            "turn_id",
            "max_events",
            "citation",
        ):
            self.assertIs(
                read_signature.parameters[name].kind,
                inspect.Parameter.KEYWORD_ONLY,
            )
        self.assertEqual(read_signature.parameters["start_sequence"].default, 1)
        self.assertIsNone(read_signature.parameters["end_sequence"].default)
        self.assertEqual(read_signature.parameters["max_events"].default, 24)
        self.assertEqual(
            tuple(
                inspect.signature(
                    ConversationMemoryService.continue_transcript
                ).parameters
            ),
            ("self", "cursor"),
        )

    def test_service_bundle_identity_conflict_and_legacy_construction(self) -> None:
        workspace = initialized_workspace(self)
        bundle = compose_filesystem_conversation_storage(workspace)
        scope = ConversationSearchScope("session_a")
        bundled = ConversationMemoryService(
            scope,
            conversation_storage_services=bundle,
        )
        self.assertIs(bundled._conversation_storage_services, bundle)
        self.assertIs(bundled._catalog, bundle.catalog)
        self.assertIs(bundled._event_store, bundle.event_store)
        self.assertIs(bundled._search, bundle.search)
        with self.assertRaises(TypeError):
            ConversationMemoryService(
                scope,
                bundle.catalog,
                bundle.event_store,
                conversation_storage_services=bundle,
            )

        legacy = ConversationMemoryService(
            scope,
            bundle.catalog,
            bundle.event_store,
            search=bundle.search,
        )
        self.assertIsNone(legacy._conversation_storage_services)
        self.assertIs(legacy._catalog, bundle.catalog)


class ConversationSearchBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = MemoryConversationCatalog()
        self.store = MemoryConversationEventStore(self.catalog)
        self.scope = ConversationSearchScope("session_a")
        self.service = ConversationMemoryService(
            self.scope,
            self.catalog,
            self.store,
        )

    def test_memory_service_scope_is_exact_immutable_and_io_free(self) -> None:
        reads_before = list(self.store.read_calls)
        appends_before = self.store.append_calls
        self.assertIs(self.service.scope, self.scope)
        self.assertIs(type(self.service.scope), ConversationSearchScope)
        self.assertEqual(self.service.scope.session_id, "session_a")
        self.assertEqual(self.store.read_calls, reads_before)
        self.assertEqual(self.store.append_calls, appends_before)
        with self.assertRaises(AttributeError):
            self.service.scope = ConversationSearchScope("other")  # type: ignore[misc]

    def test_unicode_literal_matching_and_adapter_order_are_deterministic(self) -> None:
        first = self.catalog.create("session_a", "ＣＯＵＮＣＩＬ\u3000Vessel")
        second = self.catalog.create("session_a", "Council vessel notes")
        self.catalog.reverse_list = True
        result = self.service.search(ConversationSearchQuery("council vessel"))
        self.assertEqual(
            tuple(hit.citation.conversation_id for hit in result.hits),
            (first.conversation_id, second.conversation_id),
        )
        self.assertEqual(result.normalized_query, "council vessel")
        self.assertEqual(result.hits[0].snippet, "council vessel")

    def test_same_tier_order_uses_normalized_subject_then_id(self) -> None:
        beta = self.catalog.create("session_a", "Needle beta")
        alpha_first = self.catalog.create("session_a", "Ｎｅｅｄｌｅ alpha")
        alpha_second = self.catalog.create("session_a", "Needle alpha")
        self.catalog.reverse_list = True
        result = self.service.search(ConversationSearchQuery("needle"))
        self.assertEqual(
            tuple(hit.citation.conversation_id for hit in result.hits),
            (
                alpha_first.conversation_id,
                alpha_second.conversation_id,
                beta.conversation_id,
            ),
        )

    def test_search_is_a_literal_phrase_not_a_query_language(self) -> None:
        self.catalog.create("session_a", "Council distant vessel stories")
        for query in ("council vessel", "council.*vessel", "vessels", "council OR vessel"):
            with self.subTest(query=query):
                self.assertEqual(
                    self.service.search(ConversationSearchQuery(query)).hits,
                    (),
                )

    def test_snippet_contains_complete_match_and_removes_control_format_text(self) -> None:
        record = self.catalog.create("session_a", "Other subject")
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=record.conversation_id,
            turn_id="turn_a",
            user_content="p" * 100 + "\u200b TARGET phrase " + "s" * 300,
            outcome_content="reply",
        )
        hit = self.service.search(ConversationSearchQuery("target phrase")).hits[0]
        self.assertIn("target phrase", hit.snippet)
        self.assertTrue(hit.snippet.startswith("…"))
        self.assertTrue(hit.snippet.endswith("…"))
        self.assertLessEqual(len(hit.snippet), 240)
        self.assertLessEqual(len(hit.snippet.encode("utf-8")), 1_024)
        self.assertFalse(
            any(unicodedata.category(char).startswith("C") for char in hit.snippet)
        )

        context_record = self.catalog.create("session_a", "Context subject")
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=context_record.conversation_id,
            turn_id="turn_context",
            user_content="p" * 100 + "needle" + "s" * 300,
            outcome_content="reply",
        )
        context_hit = self.service.search(ConversationSearchQuery("needle")).hits[0]
        self.assertTrue(context_hit.snippet.startswith("…" + "p" * 80 + "needle"))

    def test_hit_limit_reports_full_bounded_match_count(self) -> None:
        for index in range(12):
            self.catalog.create("session_a", f"Needle subject {index:02d}")
        result = self.service.search(ConversationSearchQuery("needle", limit=3))
        self.assertEqual(len(result.hits), 3)
        self.assertEqual(result.matched_conversations, 12)
        self.assertTrue(result.truncated)
        self.assertFalse(result.scan_truncated)

    def test_subject_hit_skips_transcript_read(self) -> None:
        self.catalog.create("session_a", "Exact subject")
        result = self.service.search(ConversationSearchQuery("exact subject"))
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(self.store.read_calls, [])

    def test_conversation_scan_bound_is_applied_after_stable_sort(self) -> None:
        for index in reversed(range(257)):
            item = metadata(
                f"conversation_{index:04d}",
                session_id="session_a",
                subject=f"Needle {index:04d}",
                created_offset=index,
            )
            self.catalog.entries[("session_a", item.conversation_id)] = item
        self.catalog.reverse_list = True
        result = self.service.search(ConversationSearchQuery("needle", limit=50))
        self.assertEqual(result.scanned_conversations, 256)
        self.assertEqual(result.matched_conversations, 256)
        self.assertTrue(result.scan_truncated)
        self.assertNotIn(
            "conversation_0256",
            {hit.citation.conversation_id for hit in result.hits},
        )

    def test_event_scan_stops_before_event_10001_and_counts_runtime_errors(self) -> None:
        item = self.catalog.create("session_a", "Haystack")
        events: list[ConversationEvent] = []
        for turn_number in range(1, 5_001):
            user_sequence = turn_number * 2 - 1
            events.append(
                stored_event(
                    user_sequence,
                    session_id="session_a",
                    conversation_id=item.conversation_id,
                    turn_id=f"turn_{turn_number}",
                    content="hay",
                )
            )
            events.append(
                stored_event(
                    user_sequence + 1,
                    session_id="session_a",
                    conversation_id=item.conversation_id,
                    turn_id=f"turn_{turn_number}",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content="runtime diagnostic",
                )
            )
        corrupt_event_10001 = stored_event(
            10_001,
            session_id="session_other",
            conversation_id=item.conversation_id,
            turn_id="turn_corrupt",
            content="needle",
        )
        self.store.events[("session_a", item.conversation_id)] = (
            *events,
            corrupt_event_10001,
        )
        result = self.service.search(ConversationSearchQuery("needle"))
        self.assertEqual(result.scanned_events, 10_000)
        self.assertTrue(result.scan_truncated)
        self.assertEqual(result.hits, ())

        self.store.events[("session_a", item.conversation_id)] = tuple(events)
        exact = self.service.search(ConversationSearchQuery("needle"))
        self.assertEqual(exact.scanned_events, 10_000)
        self.assertFalse(exact.scan_truncated)

        tail = self.catalog.create("session_a", "Unexamined tail")
        self.store.events[("session_a", tail.conversation_id)] = (
            stored_event(
                1,
                session_id="session_other",
                conversation_id=tail.conversation_id,
                content="needle",
            ),
        )
        self.store.read_calls.clear()
        skipped_tail = self.service.search(ConversationSearchQuery("needle"))
        self.assertTrue(skipped_tail.scan_truncated)
        self.assertNotIn(
            ("session_a", tail.conversation_id),
            self.store.read_calls,
        )

        corrupt_event_10000 = stored_event(
            10_000,
            session_id="session_a",
            conversation_id="conversation_other",
            turn_id="turn_corrupt",
            content="needle",
        )
        self.store.events[("session_a", item.conversation_id)] = (
            *events[:-1],
            corrupt_event_10000,
        )
        with self.assertRaisesRegex(ValueError, "another transcript"):
            self.service.search(ConversationSearchQuery("needle"))

    def test_turn_filtered_pagination_uses_first_eligible_unreturned_sequence(self) -> None:
        item = self.catalog.create("session_a", "Interleaved")
        self.store.append(
            ConversationEventAppend(
                "session_a", item.conversation_id, "turn_a", "user",
                PLOT_ARCHITECT, ConversationEventKind.USER_MESSAGE, "a",
            )
        )
        self.store.append(
            ConversationEventAppend(
                "session_a", item.conversation_id, "turn_b", "user",
                PLOT_ARCHITECT, ConversationEventKind.USER_MESSAGE, "b",
            )
        )
        self.store.append(
            ConversationEventAppend(
                "session_a", item.conversation_id, "turn_a", PLOT_ARCHITECT,
                "user", ConversationEventKind.AGENT_REPLY, "a reply",
            )
        )
        self.store.append(
            ConversationEventAppend(
                "session_a", item.conversation_id, "turn_b", PLOT_ARCHITECT,
                "user", ConversationEventKind.AGENT_REPLY, "b reply",
            )
        )
        page = self.service.read_transcript(
            item.conversation_id,
            turn_id="turn_a",
            max_events=1,
        )
        self.assertEqual(tuple(event.sequence for event in page.events), (1,))
        self.assertEqual(page.next_cursor.next_sequence, 3)
        continued = self.service.continue_transcript(page.next_cursor)
        self.assertEqual(tuple(event.sequence for event in continued.events), (3,))

        outside_range = self.service.read_transcript(
            item.conversation_id,
            start_sequence=2,
            end_sequence=2,
            turn_id="turn_a",
        )
        self.assertEqual(outside_range.events, ())
        self.assertFalse(outside_range.truncated)

        one_past_end = self.service.read_transcript(
            item.conversation_id,
            start_sequence=5,
            turn_id="turn_a",
        )
        self.assertEqual(one_past_end.events, ())
        self.assertFalse(one_past_end.truncated)

        with self.assertRaisesRegex(ValueError, "stale transcript turn"):
            self.service.read_transcript(item.conversation_id, turn_id="missing")

    def test_empty_and_one_past_end_ranges_are_empty_not_truncated(self) -> None:
        item = self.catalog.create("session_a", "Empty")
        empty = self.service.read_transcript(item.conversation_id)
        self.assertEqual(empty.events, ())
        self.assertEqual(empty.requested_end_sequence, 0)
        self.assertFalse(empty.truncated)
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=item.conversation_id,
            turn_id="turn_a",
            user_content="a",
            outcome_content="b",
        )
        at_end = self.service.read_transcript(
            item.conversation_id,
            start_sequence=3,
        )
        self.assertEqual(at_end.events, ())
        with self.assertRaises(ValueError):
            self.service.read_transcript(item.conversation_id, start_sequence=4)

    def test_byte_limit_never_clips_an_event(self) -> None:
        item = self.catalog.create("session_a", "Bytes")
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=item.conversation_id,
            turn_id="turn_a",
            user_content="a",
            outcome_content="x" * 32_769,
        )
        page = self.service.read_transcript(item.conversation_id)
        self.assertEqual(tuple(event.sequence for event in page.events), (1,))
        self.assertEqual(page.next_cursor.next_sequence, 2)
        with self.assertRaisesRegex(ValueError, "exceeds the page UTF-8"):
            self.service.continue_transcript(page.next_cursor)

        exact_item = self.catalog.create("session_a", "Exact bytes")
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=exact_item.conversation_id,
            turn_id="turn_exact",
            user_content="é" * 16_384,
            outcome_content="later reply",
        )
        exact_page = self.service.read_transcript(exact_item.conversation_id)
        self.assertEqual(len(exact_page.events), 1)
        self.assertEqual(exact_page.returned_utf8_bytes, 32_768)
        self.assertEqual(exact_page.events[0].content, "é" * 16_384)

    def test_append_stales_cursor_but_rename_does_not_stale_citation(self) -> None:
        item = self.catalog.create("session_a", "Stable")
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=item.conversation_id,
            turn_id="turn_a",
            user_content="search needle",
            outcome_content="reply",
        )
        hit = self.service.search(ConversationSearchQuery("needle")).hits[0]
        page = self.service.read_transcript(
            item.conversation_id,
            max_events=1,
            citation=hit.citation,
        )
        self.catalog.rename("session_a", item.conversation_id, "Renamed")
        renamed_continuation = self.service.continue_transcript(page.next_cursor)
        self.assertEqual(renamed_continuation.events[0].content, "reply")
        self.assertEqual(
            self.service.read_transcript(
                item.conversation_id,
                citation=hit.citation,
            ).events[0].content,
            "search needle",
        )
        append_turn(
            self.store,
            session_id="session_a",
            conversation_id=item.conversation_id,
            turn_id="turn_b",
            user_content="later",
            outcome_content="later reply",
        )
        self.assertEqual(
            self.service.read_transcript(
                item.conversation_id,
                citation=hit.citation,
            ).events[0].content,
            "search needle",
        )
        with self.assertRaisesRegex(ValueError, "stale transcript cursor"):
            self.service.continue_transcript(page.next_cursor)

    def test_wrong_scope_citation_and_injected_results_fail_closed(self) -> None:
        item = self.catalog.create("session_a", "Needle")
        wrong = ConversationCitation("session_b", item.conversation_id, None, None, None)
        self.store.read_calls.clear()
        with self.assertRaisesRegex(ValueError, "outside the authorized session"):
            self.service.read_transcript(item.conversation_id, citation=wrong)
        with self.assertRaisesRegex(ValueError, "outside the authorized session"):
            self.service.read_transcript("missing_conversation", citation=wrong)
        wrong_conversation = ConversationCitation(
            "session_a",
            "conversation_other",
            None,
            None,
            None,
        )
        with self.assertRaisesRegex(ValueError, "another conversation"):
            self.service.read_transcript(
                item.conversation_id,
                citation=wrong_conversation,
            )
        self.assertEqual(self.store.read_calls, [])

        query = ConversationSearchQuery("needle")
        hit = ConversationSearchHit(
            citation=wrong,
            subject="Needle",
            subject_revision=1,
            status=ConversationStatus.ACTIVE,
            updated_at=BASE_TIME.isoformat(),
            match_kind=ConversationSearchMatchKind.EXACT_SUBJECT,
            snippet="needle",
        )
        result = ConversationSearchResult(query, "needle", (hit,), 1, 1, 0, False, False)

        class Injected:
            def search(self, scope, supplied_query):
                return result

        service = ConversationMemoryService(
            self.scope,
            self.catalog,
            self.store,
            search=Injected(),
        )
        with self.assertRaisesRegex(ValueError, "crossed the authorized session"):
            service.search(query)

        archived_hit = ConversationSearchHit(
            citation=ConversationCitation(
                "session_a",
                item.conversation_id,
                None,
                None,
                None,
            ),
            subject="Needle",
            subject_revision=1,
            status=ConversationStatus.ARCHIVED,
            updated_at=BASE_TIME.isoformat(),
            match_kind=ConversationSearchMatchKind.EXACT_SUBJECT,
            snippet="needle",
        )
        archived_result = ConversationSearchResult(
            query,
            "needle",
            (archived_hit,),
            1,
            1,
            0,
            False,
            False,
        )

        class ArchivedInjected:
            def search(self, scope, supplied_query):
                return archived_result

        archived_service = ConversationMemoryService(
            self.scope,
            self.catalog,
            self.store,
            search=ArchivedInjected(),
        )
        with self.assertRaisesRegex(ValueError, "archived results"):
            archived_service.search(query)

    def test_corrupt_event_pairing_fails_closed(self) -> None:
        item = self.catalog.create("session_a", "Corrupt")
        self.store.events[("session_a", item.conversation_id)] = (
            stored_event(
                1,
                session_id="session_a",
                conversation_id=item.conversation_id,
                turn_id="orphan",
                kind=ConversationEventKind.AGENT_REPLY,
            ),
        )
        with self.assertRaisesRegex(ValueError, "outcome before"):
            self.service.read_transcript(item.conversation_id)


class ConversationSearchFilesystemSafetyTests(unittest.TestCase):
    @staticmethod
    def snapshot(root: Path) -> dict[str, bytes | None]:
        return {
            str(path.relative_to(root)): (
                path.read_bytes() if path.is_file() else None
            )
            for path in root.rglob("*")
        }

    def test_search_and_pages_do_not_mutate_the_workspace(self) -> None:
        workspace = initialized_workspace(self)
        catalog = FilesystemConversationCatalog(workspace)
        store = FilesystemConversationEventStore(workspace, catalog=catalog)
        item = catalog.create("session_a", "Needle subject")
        append_turn(
            store,
            session_id="session_a",
            conversation_id=item.conversation_id,
            turn_id="turn_a",
            user_content="needle content",
            outcome_content="reply",
        )
        service = ConversationMemoryService(
            ConversationSearchScope("session_a"),
            catalog,
            store,
        )
        before = self.snapshot(workspace.root)
        result = service.search(ConversationSearchQuery("needle"))
        page = service.read_transcript(result.hits[0].citation.conversation_id)
        self.assertEqual(before, self.snapshot(workspace.root))
        self.assertEqual(len(page.events), 2)

    def test_absent_catalog_search_is_empty_and_nonmutating(self) -> None:
        workspace = initialized_workspace(self)
        catalog = FilesystemConversationCatalog(workspace)
        store = FilesystemConversationEventStore(workspace, catalog=catalog)
        before = self.snapshot(workspace.root)
        result = ReferenceConversationSearch(catalog, store).search(
            ConversationSearchScope("missing_session"),
            ConversationSearchQuery("needle"),
        )
        self.assertEqual(result.hits, ())
        self.assertEqual(before, self.snapshot(workspace.root))


class CollaborationReadableSearchContractTests(unittest.TestCase):
    def test_normalized_matches_cite_exact_original_codepoint_ranges(self) -> None:
        purpose = "Straße ＡＢＣ e\u0301    vessel"
        history, _ = awaiting_history(make_proposal(purpose=purpose))

        for query, expected in (
            ("STRASSE", "Straße"),
            ("abc", "ＡＢＣ"),
            ("é vessel", "e\u0301    vessel"),
        ):
            with self.subTest(query=query):
                request = CollaborationHistorySearchQuery(query)
                first = search_collaboration_history(history, request)
                self.assertEqual(first, search_collaboration_history(history, request))
                self.assertEqual(len(first.hits), 1)
                hit = first.hits[0]
                self.assertIs(
                    hit.citation.field_key,
                    CollaborationReadableFieldKey.PROPOSAL_PURPOSE,
                )
                self.assertEqual(
                    purpose[
                        hit.citation.start_codepoint : hit.citation.end_codepoint
                    ],
                    expected,
                )
                self.assertEqual(
                    resolve_collaboration_citation(history, hit.citation),
                    expected,
                )

        citation = search_collaboration_history(
            history,
            CollaborationHistorySearchQuery("strasse"),
        ).hits[0].citation
        for stale in (
            replace(citation, event_id="missing_event"),
            replace(citation, sequence=citation.sequence + 1),
            replace(
                citation,
                field_key=(
                    CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_INSTRUCTION
                ),
                assignment_ordinal=99,
            ),
            replace(citation, end_codepoint=len(purpose) + 1),
        ):
            with self.subTest(stale=stale):
                with self.assertRaises(ValueError):
                    resolve_collaboration_citation(history, stale)

    def test_search_order_limits_and_privacy_are_deterministic(self) -> None:
        history, _, _ = executed_history()
        query = CollaborationHistorySearchQuery("physical_calls", limit=2)
        result = search_collaboration_history(history, query)

        self.assertEqual(len(result.hits), 2)
        self.assertGreater(result.matched_fields, len(result.hits))
        self.assertTrue(result.truncated)
        coordinates = tuple(
            (
                hit.citation.sequence,
                hit.citation.field_key.value,
                hit.citation.assignment_ordinal,
                hit.citation.input_ordinal,
            )
            for hit in result.hits
        )
        self.assertEqual(
            coordinates,
            tuple(
                sorted(
                    coordinates,
                    key=lambda value: (
                        value[0],
                        tuple(CollaborationReadableFieldKey).index(
                            CollaborationReadableFieldKey(value[1])
                        ),
                        -1 if value[2] is None else value[2],
                        -1 if value[3] is None else value[3],
                    ),
                )
            ),
        )
        readable_keys = {
            field.key.value
            for record in project_collaboration_readable_history(history)
            for field in record.fields
        }
        for forbidden in (
            "authority",
            "capability",
            "private",
            "provider_session",
            "revision_digest",
        ):
            self.assertFalse(any(forbidden in key for key in readable_keys))

    def test_pages_cover_one_snapshot_and_reject_tail_or_digest_drift(self) -> None:
        history, proposal, _ = executed_history()
        first = page_collaboration_history(history, max_records=3)
        self.assertIsInstance(first.next_cursor, CollaborationHistoryPageCursor)
        assert first.next_cursor is not None

        pages = [first]
        cursor = first.next_cursor
        while cursor is not None:
            page = continue_collaboration_history(history, cursor)
            pages.append(page)
            cursor = page.next_cursor
        self.assertEqual(
            tuple(record.sequence for page in pages for record in page.records),
            tuple(range(1, len(history) + 1)),
        )
        self.assertTrue(all(page.snapshot_last_sequence == len(history) for page in pages))
        self.assertTrue(
            all(
                page.snapshot_tail_batch_digest == history[-1].batch_digest
                for page in pages
            )
        )

        result_event = next(
            event for event in reversed(history) if event.event_id == "execution_result"
        )
        extended = store_batch(
            history,
            make_batch(
                history,
                "batch_search_completed",
                make_draft(
                    "search_completion_notice",
                    CollaborationEventKind.MESSAGE,
                    RUNTIME,
                    USER,
                    CollaborationMessagePayload("Visible completion notice."),
                ),
                make_draft(
                    "search_completed_state",
                    CollaborationEventKind.STEP_STATE,
                    RUNTIME,
                    USER,
                    proposal_state(
                        proposal,
                        CollaborationStepState.COMPLETED,
                        execution_result_event_id=result_event.event_id,
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "stale collaboration page cursor"):
            continue_collaboration_history(extended, first.next_cursor)
        digest_drift = replace(
            first.next_cursor,
            snapshot_tail_batch_digest="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "stale collaboration page cursor"):
            continue_collaboration_history(history, digest_drift)

    def test_document_identity_binds_canonical_event_and_readable_coordinates(
        self,
    ) -> None:
        history, _ = awaiting_history()
        record = project_collaboration_readable_history(history)[0]
        field = record.fields[0]
        base = collaboration_readable_document_id(
            workspace_id="workspace_a",
            metadata_revision=1,
            record=record,
            field=field,
        )
        self.assertEqual(
            base,
            collaboration_readable_document_id(
                workspace_id="workspace_a",
                metadata_revision=1,
                record=record,
                field=field,
            ),
        )
        self.assertTrue(base.startswith("sha256:"))
        self.assertEqual(len(base), 71)
        variants = (
            collaboration_readable_document_id(
                workspace_id="workspace_b",
                metadata_revision=1,
                record=record,
                field=field,
            ),
            collaboration_readable_document_id(
                workspace_id="workspace_a",
                metadata_revision=2,
                record=record,
                field=field,
            ),
            collaboration_readable_document_id(
                workspace_id="workspace_a",
                metadata_revision=1,
                record=replace(record, timestamp="2026-09-01T00:00:00+00:00"),
                field=field,
            ),
            collaboration_readable_document_id(
                workspace_id="workspace_a",
                metadata_revision=1,
                record=record,
                field=record.fields[1],
            ),
        )
        self.assertTrue(all(value != base for value in variants))

        changed_history, _ = awaiting_history(
            make_proposal(purpose=f"{field.text} changed")
        )
        changed_record = project_collaboration_readable_history(changed_history)[0]
        changed = collaboration_readable_document_id(
            workspace_id="workspace_a",
            metadata_revision=1,
            record=changed_record,
            field=changed_record.fields[0],
        )
        self.assertNotEqual(changed, base)
        self.assertEqual(
            COLLABORATION_READABLE_SOURCE_SCHEMA_ID,
            "story-collaboration-readable-event-field-v2",
        )


class ConversationSearchPackageBoundaryTests(unittest.TestCase):
    def test_literal_search_dependency_is_one_way_and_leaf_owned(self) -> None:
        search_tree = ast.parse(
            Path(search_module.__file__).read_text(encoding="utf-8")
        )
        literal_tree = ast.parse(
            Path(literal_module.__file__).read_text(encoding="utf-8")
        )

        def dependencies(tree: ast.AST) -> set[str]:
            return {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.level > 0
                and node.module is not None
            }

        self.assertEqual(
            dependencies(search_tree),
            {"conversation_literal_search", "conversations", "errors"},
        )
        self.assertEqual(dependencies(literal_tree), set())

    def test_module_and_facade_exports_are_identical(self) -> None:
        established = {
            "CONVERSATION_MEMORY_POLICY",
            "ConversationCitation",
            "ConversationMemoryPolicy",
            "ConversationMemoryService",
            "ConversationSearch",
            "ConversationSearchHit",
            "ConversationSearchMatchKind",
            "ConversationSearchQuery",
            "ConversationSearchResult",
            "ConversationSearchScope",
            "ConversationTranscriptCursor",
            "ConversationTranscriptPage",
            "ReferenceConversationSearch",
        }
        collaboration_direct = {
            "COLLABORATION_READABLE_SOURCE_SCHEMA_ID",
            "CollaborationHistoryCitation",
            "CollaborationHistoryPage",
            "CollaborationHistoryPageCursor",
            "CollaborationHistorySearchHit",
            "CollaborationHistorySearchQuery",
            "CollaborationHistorySearchResult",
            "collaboration_readable_document_id",
            "continue_collaboration_history",
            "page_collaboration_history",
            "resolve_collaboration_citation",
            "search_collaboration_history",
        }
        self.assertEqual(set(search_module.__all__), established | collaboration_direct)
        self.assertEqual(len(search_module.__all__), 25)
        self.assertEqual(len(set(search_module.__all__)), 25)
        self.assertTrue(established.issubset(story_writing_agents.__all__))
        self.assertTrue(collaboration_direct.isdisjoint(story_writing_agents.__all__))
        for name in established:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(story_writing_agents, name),
                    getattr(search_module, name),
                )

    def test_clean_process_import_uses_only_the_source_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import sys; sys.path.insert(0, r'"
                    + str(root / "src")
                    + "'); import story_writing_agents; "
                    "import story_writing_agents.conversation_search as m; "
                    "import story_writing_agents.conversation_literal_search as l; "
                    "assert len(m.__all__) == 25; assert len(l.__all__) == 6; "
                    "assert 'CollaborationHistoryCitation' not in "
                    "story_writing_agents.__all__; "
                    "assert 'sqlite3' not in sys.modules"
                ),
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
