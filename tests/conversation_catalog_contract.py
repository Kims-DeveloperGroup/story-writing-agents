"""Reusable contract fixtures for conversation-catalog adapters."""

from __future__ import annotations

from story_writing_agents import (
    ConversationCatalog,
    ConversationNotFoundError,
    ConversationStatus,
    normalize_conversation_subject,
)


CONVERSATION_CATALOG_METHODS = {
    "archive",
    "create",
    "get",
    "list",
    "rename",
    "unarchive",
}


class ConversationCatalogContractMixin:
    """Reusable public behavior contract for every catalog adapter."""

    catalog: ConversationCatalog

    def make_catalog(self) -> ConversationCatalog:
        raise NotImplementedError

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.catalog = self.make_catalog()

    def test_adapter_exposes_the_complete_narrow_protocol(self) -> None:
        protocol_methods = {
            name
            for name, member in vars(ConversationCatalog).items()
            if not name.startswith("_") and callable(member)
        }
        self.assertEqual(protocol_methods, CONVERSATION_CATALOG_METHODS)
        for name in protocol_methods:
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(self.catalog, name, None)))

    def test_create_get_and_deterministic_list_contract(self) -> None:
        first = self.catalog.create("session_a", "First subject")
        second = self.catalog.create("session_a", "Second subject")

        self.assertNotEqual(first.conversation_id, second.conversation_id)
        self.assertRegex(first.conversation_id, r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
        self.assertEqual(first.session_id, "session_a")
        self.assertEqual(first.subject, "First subject")
        self.assertEqual(first.subject_revision, 1)
        self.assertIs(first.status, ConversationStatus.ACTIVE)
        self.assertEqual(first.created_at, first.updated_at)
        self.assertEqual(self.catalog.get("session_a", first.conversation_id), first)
        self.assertEqual(self.catalog.list("session_a"), (first, second))
        self.assertIsInstance(self.catalog.list("session_a"), tuple)

    def test_duplicate_display_subjects_are_independent(self) -> None:
        display = "  Café Story  "
        first = self.catalog.create("session_a", display)
        second = self.catalog.create("session_a", "ＣＡＦＥ́\u00a0story")

        self.assertNotEqual(first.conversation_id, second.conversation_id)
        self.assertEqual(first.subject, display)
        self.assertEqual(
            normalize_conversation_subject(first.subject),
            normalize_conversation_subject(second.subject),
        )
        self.assertEqual(self.catalog.list("session_a"), (first, second))

    def test_rename_preserves_identity_and_increments_every_success(self) -> None:
        original = self.catalog.create("session_a", "Original")
        renamed = self.catalog.rename(
            "session_a",
            original.conversation_id,
            "  Renamed display  ",
        )

        self.assertEqual(renamed.session_id, original.session_id)
        self.assertEqual(renamed.conversation_id, original.conversation_id)
        self.assertEqual(renamed.schema_version, original.schema_version)
        self.assertEqual(renamed.created_at, original.created_at)
        self.assertEqual(renamed.subject, "  Renamed display  ")
        self.assertEqual(renamed.subject_revision, 2)
        self.assertIs(renamed.status, ConversationStatus.ACTIVE)
        self.assertGreaterEqual(renamed.updated_at, original.updated_at)

        repeated = self.catalog.rename(
            "session_a",
            original.conversation_id,
            renamed.subject,
        )
        self.assertEqual(repeated.conversation_id, original.conversation_id)
        self.assertEqual(repeated.subject_revision, 3)
        self.assertEqual(
            self.catalog.get("session_a", original.conversation_id),
            repeated,
        )

    def test_archive_and_unarchive_are_reversible_and_idempotent(self) -> None:
        original = self.catalog.create("session_a", "Lifecycle")
        archived = self.catalog.archive("session_a", original.conversation_id)

        self.assertEqual(archived.conversation_id, original.conversation_id)
        self.assertEqual(archived.subject, original.subject)
        self.assertEqual(archived.subject_revision, original.subject_revision)
        self.assertEqual(archived.created_at, original.created_at)
        self.assertIs(archived.status, ConversationStatus.ARCHIVED)
        self.assertEqual(self.catalog.list("session_a"), ())
        self.assertEqual(
            self.catalog.list("session_a", include_archived=True),
            (archived,),
        )

        archived_again = self.catalog.archive(
            "session_a",
            original.conversation_id,
        )
        self.assertEqual(archived_again, archived)

        active = self.catalog.unarchive("session_a", original.conversation_id)
        self.assertIs(active.status, ConversationStatus.ACTIVE)
        self.assertEqual(active.conversation_id, original.conversation_id)
        self.assertEqual(active.subject_revision, original.subject_revision)
        self.assertEqual(self.catalog.list("session_a"), (active,))
        self.assertEqual(
            self.catalog.unarchive("session_a", original.conversation_id),
            active,
        )

    def test_missing_records_and_filter_types_have_stable_errors(self) -> None:
        with self.assertRaises(ConversationNotFoundError):
            self.catalog.get("session_a", "missing_conversation")
        with self.assertRaises(ConversationNotFoundError):
            self.catalog.rename(
                "session_a",
                "missing_conversation",
                "New subject",
            )
        with self.assertRaises(ConversationNotFoundError):
            self.catalog.archive("session_a", "missing_conversation")
        with self.assertRaises(ConversationNotFoundError):
            self.catalog.unarchive("session_a", "missing_conversation")
        for invalid in (None, 0, 1, "false"):
            with self.subTest(include_archived=invalid):
                with self.assertRaises(TypeError):
                    self.catalog.list(  # type: ignore[arg-type]
                        "session_a",
                        include_archived=invalid,
                    )
