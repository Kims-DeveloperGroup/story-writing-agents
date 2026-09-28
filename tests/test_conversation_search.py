"""Schema-v2 collaboration search, citation, and bounded-page tests."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import story_writing_agents
from story_writing_agents import conversation_literal_search as literal_module
from story_writing_agents import conversation_search as search_module
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
    CONVERSATION_MEMORY_POLICY,
    CollaborationHistoryCitation,
    CollaborationHistoryPage,
    CollaborationHistoryPageCursor,
    CollaborationHistorySearch,
    CollaborationHistorySearchHit,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    CollaborationHistorySearchScope,
    CollaborationHistoryService,
    ConversationMemoryPolicy,
    ReferenceCollaborationHistorySearch,
    collaboration_readable_document_id,
    continue_collaboration_history,
    page_collaboration_history,
    resolve_collaboration_citation,
    search_collaboration_history,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
    FilesystemConversationCatalog,
)
from story_writing_agents.errors import SessionIdError
from story_writing_agents.conversations import (
    CollaborationEventKind,
    CollaborationMessagePayload,
    CollaborationReadableFieldKey,
    CollaborationStepState,
    project_collaboration_readable_history,
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
from tests.conversation_search_contract import (
    CollaborationSearchContractMixin,
    MemoryCollaborationEventStore,
    MemoryConversationCatalog,
)
from tests.helpers import initialized_workspace


class InMemoryCollaborationSearchContractTests(
    CollaborationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[MemoryConversationCatalog, MemoryCollaborationEventStore]:
        catalog = MemoryConversationCatalog()
        return catalog, MemoryCollaborationEventStore(catalog)


class FilesystemCollaborationSearchContractTests(
    CollaborationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[FilesystemConversationCatalog, FilesystemCollaborationEventStore]:
        self.workspace = initialized_workspace(self)
        self.services = compose_filesystem_conversation_storage(self.workspace)
        self.assertIsInstance(self.services.catalog, FilesystemConversationCatalog)
        self.assertIsInstance(
            self.services.event_store,
            FilesystemCollaborationEventStore,
        )
        return self.services.catalog, self.services.event_store

    def make_search(
        self,
        catalog: object,
        store: object,
    ) -> CollaborationHistorySearch:
        del catalog, store
        self.assertIsInstance(
            self.services.search,
            ReferenceCollaborationHistorySearch,
        )
        return self.services.search


class CollaborationSearchValueContractTests(unittest.TestCase):
    def test_literal_helpers_remain_exact_pure_and_deterministic(self) -> None:
        self.assertEqual(CONVERSATION_LITERAL_NORMALIZATION_VERSION, 1)
        self.assertEqual(
            normalize_conversation_literal_text(
                "  ＳＴＲＡＳＳＥ\u200b\tStraße\x00한🙂  "
            ),
            "strasse strasse 한🙂",
        )
        self.assertEqual(
            normalize_conversation_literal_query(" Straße  VESSEL ", max_characters=20),
            "strasse vessel",
        )
        self.assertEqual(
            classify_conversation_literal_subject("needle notes", "needle"),
            "subject_prefix",
        )
        self.assertEqual(
            conversation_literal_sort_key("transcript", "subject", "conversation_a"),
            (3, "subject", "conversation_a"),
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
        self.assertLessEqual(len(snippet.encode()), 200)

    def test_policy_and_v2_value_field_order_are_exact(self) -> None:
        self.assertEqual(
            CONVERSATION_MEMORY_POLICY,
            ConversationMemoryPolicy(
                200, 10, 50, 256, 10_000, 240, 1_024, 24, 100, 32_768
            ),
        )
        expected = {
            CollaborationHistoryCitation: (
                "session_id",
                "conversation_id",
                "event_id",
                "sequence",
                "field_key",
                "start_codepoint",
                "end_codepoint",
                "assignment_ordinal",
                "input_ordinal",
            ),
            CollaborationHistorySearchQuery: ("text", "limit"),
            CollaborationHistorySearchHit: ("citation", "snippet"),
            CollaborationHistorySearchResult: (
                "query",
                "normalized_query",
                "hits",
                "matched_fields",
                "scanned_records",
                "scanned_fields",
                "scan_truncated",
                "truncated",
            ),
            CollaborationHistoryPageCursor: (
                "session_id",
                "conversation_id",
                "next_sequence",
                "end_sequence",
                "snapshot_last_sequence",
                "snapshot_tail_batch_digest",
                "max_records",
                "max_utf8_bytes",
            ),
            CollaborationHistoryPage: (
                "session_id",
                "conversation_id",
                "records",
                "requested_start_sequence",
                "requested_end_sequence",
                "snapshot_last_sequence",
                "snapshot_tail_batch_digest",
                "returned_utf8_bytes",
                "next_cursor",
                "truncated",
            ),
            CollaborationHistorySearchScope: ("session_id", "include_archived"),
        }
        for value_type, names in expected.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(value_type)), names
                )
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertTrue(hasattr(value_type, "__slots__"))

    def test_queries_scopes_and_values_reject_malformed_inputs(self) -> None:
        query = CollaborationHistorySearchQuery("council")
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            query.limit = 2  # type: ignore[misc]
        for text in ("", "   ", "x\x00y", "x\u200by", "x" * 201, "\ud800"):
            with self.subTest(text=repr(text)):
                with self.assertRaises((TypeError, ValueError)):
                    CollaborationHistorySearchQuery(text)
        for limit in (True, 0, 51, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises((TypeError, ValueError)):
                    CollaborationHistorySearchQuery("valid", limit=limit)  # type: ignore[arg-type]
        with self.assertRaises(SessionIdError):
            CollaborationHistorySearchScope("unsafe/session")
        with self.assertRaises(TypeError):
            CollaborationHistorySearchScope("session_a", include_archived=1)  # type: ignore[arg-type]

    def test_protocol_service_and_bundle_signatures_are_stable(self) -> None:
        self.assertTrue(getattr(CollaborationHistorySearch, "_is_protocol", False))
        self.assertEqual(
            tuple(inspect.signature(ReferenceCollaborationHistorySearch).parameters),
            ("catalog", "event_store"),
        )
        self.assertEqual(
            tuple(inspect.signature(CollaborationHistorySearch.search).parameters),
            ("self", "scope", "query"),
        )
        workspace = initialized_workspace(self)
        bundle = compose_filesystem_conversation_storage(workspace)
        service = CollaborationHistoryService(
            CollaborationHistorySearchScope("session_a"),
            conversation_storage_services=bundle,
        )
        self.assertIs(service._catalog, bundle.catalog)
        self.assertIs(service._event_store, bundle.event_store)
        self.assertIs(service._search, bundle.search)
        with self.assertRaises(TypeError):
            CollaborationHistoryService(
                CollaborationHistorySearchScope("session_a"),
                bundle.catalog,
                bundle.event_store,
                conversation_storage_services=bundle,
            )


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
                result = search_collaboration_history(
                    history,
                    CollaborationHistorySearchQuery(query),
                )
                self.assertEqual(len(result.hits), 1)
                hit = result.hits[0]
                self.assertIs(
                    hit.citation.field_key,
                    CollaborationReadableFieldKey.PROPOSAL_PURPOSE,
                )
                self.assertEqual(
                    purpose[hit.citation.start_codepoint : hit.citation.end_codepoint],
                    expected,
                )
                self.assertEqual(
                    resolve_collaboration_citation(history, hit.citation), expected
                )
        citation = (
            search_collaboration_history(
                history,
                CollaborationHistorySearchQuery("strasse"),
            )
            .hits[0]
            .citation
        )
        for stale in (
            replace(citation, event_id="missing_event"),
            replace(citation, sequence=citation.sequence + 1),
            replace(citation, end_codepoint=len(purpose) + 1),
        ):
            with self.subTest(stale=stale):
                with self.assertRaises(ValueError):
                    resolve_collaboration_citation(history, stale)

    def test_search_order_limits_and_privacy_are_deterministic(self) -> None:
        history, _, _ = executed_history()
        query = CollaborationHistorySearchQuery("physical_calls", limit=2)
        result = search_collaboration_history(history, query)
        self.assertEqual(result, search_collaboration_history(history, query))
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
            tuple(item[0] for item in coordinates),
            tuple(sorted(item[0] for item in coordinates)),
        )
        readable_keys = {
            field.key.value
            for record in project_collaboration_readable_history(history)
            for field in record.fields
        }
        for forbidden in ("authority", "capability", "private", "provider_session"):
            self.assertFalse(any(forbidden in key for key in readable_keys))

    def test_pages_cover_snapshot_and_reject_tail_or_digest_drift(self) -> None:
        history, proposal, _ = executed_history()
        first = page_collaboration_history(history, max_records=3)
        self.assertIsNotNone(first.next_cursor)
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
        with self.assertRaisesRegex(ValueError, "stale collaboration page cursor"):
            continue_collaboration_history(
                history,
                replace(
                    first.next_cursor,
                    snapshot_tail_batch_digest="sha256:" + "0" * 64,
                ),
            )

    def test_document_identity_binds_event_field_and_metadata_revision(self) -> None:
        history, _ = awaiting_history()
        record = next(
            item
            for item in project_collaboration_readable_history(history)
            if item.fields
        )
        field = record.fields[0]
        base = collaboration_readable_document_id(
            workspace_id="workspace_a",
            metadata_revision=1,
            record=record,
            field=field,
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
                record=record,
                field=record.fields[1],
            ),
        )
        self.assertTrue(all(value != base for value in variants))
        self.assertEqual(
            COLLABORATION_READABLE_SOURCE_SCHEMA_ID,
            "story-collaboration-readable-event-field-v2",
        )


class ConversationSearchPackageBoundaryTests(unittest.TestCase):
    def test_literal_search_dependency_is_one_way_and_leaf_owned(self) -> None:
        def dependencies(module: object) -> set[str]:
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            return {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.level > 0
                and node.module is not None
            }

        self.assertEqual(
            dependencies(search_module),
            {"conversation_literal_search", "conversations", "errors"},
        )
        self.assertEqual(dependencies(literal_module), set())

    def test_module_and_root_facade_exports_are_exact(self) -> None:
        expected = {
            "COLLABORATION_READABLE_SOURCE_SCHEMA_ID",
            "CONVERSATION_MEMORY_POLICY",
            "CollaborationHistoryCitation",
            "CollaborationHistoryPage",
            "CollaborationHistoryPageCursor",
            "CollaborationHistorySearch",
            "CollaborationHistorySearchHit",
            "CollaborationHistorySearchQuery",
            "CollaborationHistorySearchResult",
            "CollaborationHistorySearchScope",
            "CollaborationHistoryService",
            "ConversationMemoryPolicy",
            "ReferenceCollaborationHistorySearch",
            "collaboration_readable_document_id",
            "continue_collaboration_history",
            "page_collaboration_history",
            "resolve_collaboration_citation",
            "search_collaboration_history",
        }
        self.assertEqual(set(search_module.__all__), expected)
        self.assertEqual(len(search_module.__all__), 18)
        self.assertEqual(
            expected & set(story_writing_agents.__all__),
            {"CONVERSATION_MEMORY_POLICY", "ConversationMemoryPolicy"},
        )
        for old_name in (
            "ConversationCitation",
            "ConversationMemoryService",
            "ConversationSearch",
            "ConversationSearchQuery",
            "ReferenceConversationSearch",
        ):
            self.assertNotIn(old_name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, old_name))

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
                    + "'); import story_writing_agents as root; "
                    "import story_writing_agents.conversation_search as search; "
                    "assert len(root.__all__) == 106; assert len(search.__all__) == 18; "
                    "assert not hasattr(root, 'ConversationMemoryService'); "
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
