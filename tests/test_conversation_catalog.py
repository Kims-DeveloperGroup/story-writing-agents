"""Conversation metadata, catalog contract, and filesystem adapter tests."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints
from unittest import mock
from uuid import UUID

import story_writing_agents
import story_writing_agents.conversations as conversations_module
import story_writing_agents.conversations.catalog as conversation_catalog_module
import story_writing_agents.conversations.context as conversation_context_module
import story_writing_agents.conversations.events as conversation_events_module
import story_writing_agents.workspace as workspace_module
from story_writing_agents import (
    CONVERSATION_METADATA_SCHEMA_VERSION,
    CONVERSATION_SUBJECT_NORMALIZATION_VERSION,
    MAX_CONVERSATION_SUBJECT_LENGTH,
    ConversationCatalog,
    ConversationExistsError,
    ConversationMetadata,
    ConversationNotFoundError,
    ConversationStatus,
    FilesystemConversationCatalog,
    SessionIdError,
    StoryWritingAgentsError,
    Workspace,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    normalize_conversation_subject,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)

from tests.conversation_catalog_contract import (
    CONVERSATION_CATALOG_METHODS,
    ConversationCatalogContractMixin,
)


_CREATED_AT = "2026-08-12T01:02:03.456789+00:00"
_UPDATED_AT = "2026-08-12T02:03:04.567890+00:00"

_CONVERSATION_FACADE_EXPORTS = (
    "CONVERSATION_CONTEXT_POLICY",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CONVERSATION_METADATA_SCHEMA_VERSION",
    "CONVERSATION_SUBJECT_NORMALIZATION_VERSION",
    "MAX_CONVERSATION_SUBJECT_LENGTH",
    "ConversationCatalog",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
    "ConversationEventStore",
    "ConversationMetadata",
    "ConversationStatus",
    "normalize_conversation_subject",
    "select_bounded_conversation_context",
    "validate_conversation_context_window",
)
_CATALOG_EXPORTS = {
    "CONVERSATION_METADATA_SCHEMA_VERSION",
    "CONVERSATION_SUBJECT_NORMALIZATION_VERSION",
    "MAX_CONVERSATION_SUBJECT_LENGTH",
    "ConversationCatalog",
    "ConversationMetadata",
    "ConversationStatus",
    "normalize_conversation_subject",
}
_EVENT_EXPORTS = {
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
}
_CONTEXT_EXPORTS = {
    "CONVERSATION_CONTEXT_POLICY",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEventStore",
    "select_bounded_conversation_context",
    "validate_conversation_context_window",
}
_ROOT_CONVERSATION_EXPORTS = set(_CONVERSATION_FACADE_EXPORTS).difference(
    {
        "select_bounded_conversation_context",
        "validate_conversation_context_window",
    }
)
_PUBLIC_CONVERSATION_OBJECTS = {
    "ConversationCatalog",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
    "ConversationEventStore",
    "ConversationMetadata",
    "ConversationStatus",
    "normalize_conversation_subject",
    "select_bounded_conversation_context",
    "validate_conversation_context_window",
}


def _metadata(**overrides: object) -> ConversationMetadata:
    values: dict[str, object] = {
        "schema_version": CONVERSATION_METADATA_SCHEMA_VERSION,
        "session_id": "session_a",
        "conversation_id": "conversation_a",
        "subject": "Opening scene",
        "subject_revision": 1,
        "status": ConversationStatus.ACTIVE,
        "created_at": _CREATED_AT,
        "updated_at": _UPDATED_AT,
    }
    values.update(overrides)
    return ConversationMetadata(**values)  # type: ignore[arg-type]


def _metadata_path(
    workspace: Workspace,
    session_id: str,
    conversation_id: str,
) -> Path:
    return (
        workspace.root
        / "discussion_logs"
        / session_id
        / "interactive"
        / "conversations"
        / conversation_id
        / "metadata.json"
    )


def _write_payload(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _temporary_artifacts(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.tmp"))


class ConversationMetadataTests(unittest.TestCase):
    def test_schema_constants_status_values_and_exact_serialization(self) -> None:
        self.assertEqual(CONVERSATION_METADATA_SCHEMA_VERSION, 1)
        self.assertEqual(CONVERSATION_SUBJECT_NORMALIZATION_VERSION, 1)
        self.assertEqual(MAX_CONVERSATION_SUBJECT_LENGTH, 256)
        self.assertEqual(
            tuple(ConversationStatus),
            (ConversationStatus.ACTIVE, ConversationStatus.ARCHIVED),
        )
        self.assertEqual(ConversationStatus.ACTIVE.value, "active")
        self.assertEqual(ConversationStatus.ARCHIVED.value, "archived")

        metadata = _metadata()
        self.assertEqual(
            metadata.to_dict(),
            {
                "schema_version": 1,
                "session_id": "session_a",
                "conversation_id": "conversation_a",
                "subject": "Opening scene",
                "subject_revision": 1,
                "status": "active",
                "created_at": _CREATED_AT,
                "updated_at": _UPDATED_AT,
            },
        )
        self.assertFalse(hasattr(metadata, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            metadata.subject = "mutated"  # type: ignore[misc]

    def test_schema_revision_and_status_are_strictly_typed(self) -> None:
        invalid_cases = (
            ("schema boolean", {"schema_version": True}, ValueError),
            ("schema string", {"schema_version": "1"}, ValueError),
            ("schema unsupported", {"schema_version": 2}, ValueError),
            ("revision boolean", {"subject_revision": True}, ValueError),
            ("revision string", {"subject_revision": "1"}, ValueError),
            ("revision zero", {"subject_revision": 0}, ValueError),
            ("revision negative", {"subject_revision": -1}, ValueError),
            ("status string", {"status": "active"}, TypeError),
            ("status null", {"status": None}, TypeError),
        )
        for label, overrides, error in invalid_cases:
            with self.subTest(label=label):
                with self.assertRaises(error):
                    _metadata(**overrides)

    def test_session_and_conversation_identifiers_use_safe_exact_rules(self) -> None:
        valid = (
            "a",
            "A0",
            "session-with-dashes",
            "session_with_underscores",
            "a" * 64,
        )
        for value in valid:
            with self.subTest(valid=value):
                self.assertEqual(_metadata(session_id=value).session_id, value)
                self.assertEqual(
                    _metadata(conversation_id=value).conversation_id,
                    value,
                )

        invalid: tuple[object, ...] = (
            "",
            "-leading",
            "_leading",
            ".hidden",
            "contains.dot",
            "contains space",
            "../escape",
            "a" * 65,
            1,
            None,
        )
        for value in invalid:
            for field in ("session_id", "conversation_id"):
                with self.subTest(field=field, invalid=value):
                    with self.assertRaises(SessionIdError):
                        _metadata(**{field: value})

    def test_subject_validation_accepts_bound_and_rejects_invalid_text(self) -> None:
        bounded = "界" * MAX_CONVERSATION_SUBJECT_LENGTH
        self.assertEqual(_metadata(subject=bounded).subject, bounded)

        invalid_cases: tuple[tuple[object, type[BaseException]], ...] = (
            (None, TypeError),
            (7, TypeError),
            ("", ValueError),
            (" \u2003 ", ValueError),
            ("line\nbreak", ValueError),
            ("tab\tcharacter", ValueError),
            ("delete\x7fcharacter", ValueError),
            ("invalid-surrogate-\ud800", ValueError),
            ("x" * (MAX_CONVERSATION_SUBJECT_LENGTH + 1), ValueError),
        )
        for subject, error in invalid_cases:
            with self.subTest(subject=repr(subject)):
                with self.assertRaises(error):
                    _metadata(subject=subject)
                with self.assertRaises(error):
                    normalize_conversation_subject(subject)  # type: ignore[arg-type]

    def test_subject_normalization_is_stable_and_preserves_display_text(self) -> None:
        display = "  ＣＡＦＥ\u0301\u00a0  Straße\u2003STORY  "
        metadata = _metadata(subject=display)

        self.assertEqual(metadata.subject, display)
        self.assertEqual(
            normalize_conversation_subject(display),
            "café strasse story",
        )
        self.assertEqual(
            normalize_conversation_subject("Café Straße story"),
            normalize_conversation_subject(display),
        )
        self.assertNotIn("subject_key", metadata.to_dict())

    def test_timestamps_require_canonical_utc_and_monotonic_order(self) -> None:
        self.assertEqual(_metadata().created_at, _CREATED_AT)
        invalid_cases = (
            ("not ISO", {"created_at": "not-a-timestamp"}),
            ("not text", {"created_at": 1}),
            ("naive", {"created_at": "2026-08-12T01:02:03"}),
            (
                "non UTC",
                {"created_at": "2026-08-12T10:02:03+09:00"},
            ),
            ("noncanonical Z", {"created_at": "2026-08-12T01:02:03Z"}),
            (
                "noncanonical zero micros",
                {"created_at": "2026-08-12T01:02:03.000000+00:00"},
            ),
            (
                "updated before created",
                {
                    "created_at": "2026-08-12T02:00:00+00:00",
                    "updated_at": "2026-08-12T01:59:59+00:00",
                },
            ),
        )
        for label, overrides in invalid_cases:
            with self.subTest(label=label):
                with self.assertRaises((TypeError, ValueError)):
                    _metadata(**overrides)


class FilesystemConversationCatalogContractTests(
    ConversationCatalogContractMixin,
    unittest.TestCase,
):
    def make_catalog(self) -> ConversationCatalog:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(temporary.name).initialize()
        self.services = compose_filesystem_conversation_storage(self.workspace)
        return self.services.catalog


class FilesystemConversationCatalogPersistenceTests(unittest.TestCase):
    def new_workspace(self, *, initialize: bool = True) -> Workspace:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Workspace(temporary.name)
        return workspace.initialize() if initialize else workspace

    def test_exact_versioned_json_and_fixed_session_scoped_layout(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        metadata = catalog.create("story_session", "Visible subject")
        target = _metadata_path(
            workspace,
            "story_session",
            metadata.conversation_id,
        )

        expected = (
            json.dumps(
                metadata.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(
            target.relative_to(workspace.root).as_posix(),
            "discussion_logs/story_session/interactive/conversations/"
            f"{metadata.conversation_id}/metadata.json",
        )
        self.assertEqual(
            [path.name for path in target.parent.iterdir()],
            ["metadata.json"],
        )

        reloaded = FilesystemConversationCatalog(Workspace(workspace.root))
        self.assertEqual(
            reloaded.get("story_session", metadata.conversation_id),
            metadata,
        )

    def test_absent_catalog_reads_as_empty_without_mutating_legacy_workspace(
        self,
    ) -> None:
        workspace = self.new_workspace(initialize=False)
        legacy = workspace.root / "legacy.jsonl"
        legacy.write_bytes(b"legacy bytes\n")
        before = {
            path.relative_to(workspace.root): (
                None if path.is_dir() else path.read_bytes()
            )
            for path in workspace.root.rglob("*")
        }
        catalog = FilesystemConversationCatalog(workspace)

        self.assertEqual(catalog.list("legacy_session"), ())
        with self.assertRaises(ConversationNotFoundError):
            catalog.get("legacy_session", "missing_conversation")

        after = {
            path.relative_to(workspace.root): (
                None if path.is_dir() else path.read_bytes()
            )
            for path in workspace.root.rglob("*")
        }
        self.assertEqual(after, before)
        self.assertEqual(legacy.read_bytes(), b"legacy bytes\n")

    def test_same_generated_id_is_isolated_between_story_sessions(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        fixed_uuid = UUID("11111111-2222-3333-4444-555555555555")
        with mock.patch.object(workspace_module, "uuid4", return_value=fixed_uuid):
            first = catalog.create("session_a", "First session")
            second = catalog.create("session_b", "Second session")

        self.assertEqual(first.conversation_id, second.conversation_id)
        self.assertEqual(
            catalog.get("session_a", first.conversation_id).subject,
            "First session",
        )
        self.assertEqual(
            catalog.get("session_b", second.conversation_id).subject,
            "Second session",
        )
        catalog.rename("session_a", first.conversation_id, "Only first changed")
        self.assertEqual(
            catalog.get("session_b", second.conversation_id),
            second,
        )
        with self.assertRaises(ConversationNotFoundError):
            catalog.get("session_c", first.conversation_id)

    def test_identifier_collisions_never_overwrite_existing_metadata(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        fixed_uuid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        with mock.patch.object(
            workspace_module,
            "uuid4",
            return_value=fixed_uuid,
        ) as generate:
            existing = catalog.create("session_a", "Original")
            target = _metadata_path(
                workspace,
                "session_a",
                existing.conversation_id,
            )
            before = target.read_bytes()
            with self.assertRaises(ConversationExistsError):
                catalog.create("session_a", "Must not overwrite")

        self.assertGreater(generate.call_count, 1)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(catalog.get("session_a", existing.conversation_id), existing)
        self.assertEqual(catalog.list("session_a"), (existing,))

    def test_failed_create_cleans_temporary_and_empty_conversation_artifacts(
        self,
    ) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        fixed_uuid = UUID("12345678-1234-5678-1234-567812345678")
        conversation_id = f"c_{fixed_uuid.hex}"

        with mock.patch.object(workspace_module, "uuid4", return_value=fixed_uuid):
            with mock.patch.object(
                workspace_module.os,
                "link",
                side_effect=OSError("simulated create link failure"),
            ):
                with self.assertRaisesRegex(OSError, "create link failure"):
                    catalog.create("session_a", "Will fail")

        self.assertFalse(
            _metadata_path(workspace, "session_a", conversation_id).parent.exists()
        )
        self.assertEqual(catalog.list("session_a"), ())
        self.assertEqual(_temporary_artifacts(workspace.root), [])

    def test_failed_replace_preserves_old_record_and_cleans_temporary_file(
        self,
    ) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        original = catalog.create("session_a", "Original")
        target = _metadata_path(
            workspace,
            "session_a",
            original.conversation_id,
        )
        before = target.read_bytes()

        with mock.patch.object(
            workspace_module.os,
            "replace",
            side_effect=OSError("simulated replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "replace failure"):
                catalog.rename(
                    "session_a",
                    original.conversation_id,
                    "Replacement",
                )

        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(catalog.get("session_a", original.conversation_id), original)
        self.assertEqual(_temporary_artifacts(target.parent), [])

    def test_post_replace_failure_leaves_complete_new_record_not_partial_json(
        self,
    ) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        original = catalog.create("session_a", "Original")
        target = _metadata_path(
            workspace,
            "session_a",
            original.conversation_id,
        )
        original_fsync = workspace._fsync_directory

        def fail_metadata_sync(directory: Path) -> None:
            if directory == target.parent:
                raise OSError("simulated metadata directory sync failure")
            original_fsync(directory)

        with mock.patch.object(
            workspace,
            "_fsync_directory",
            side_effect=fail_metadata_sync,
        ):
            with self.assertRaisesRegex(OSError, "metadata directory sync"):
                catalog.rename(
                    "session_a",
                    original.conversation_id,
                    "Complete replacement",
                )

        reloaded = catalog.get("session_a", original.conversation_id)
        self.assertEqual(reloaded.conversation_id, original.conversation_id)
        self.assertEqual(reloaded.created_at, original.created_at)
        self.assertEqual(reloaded.subject, "Complete replacement")
        self.assertEqual(reloaded.subject_revision, 2)
        self.assertEqual(_temporary_artifacts(target.parent), [])

    def test_malformed_utf8_json_and_duplicate_fields_are_rejected(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)

        invalid_utf8 = catalog.create("session_a", "Invalid UTF-8")
        _metadata_path(
            workspace,
            "session_a",
            invalid_utf8.conversation_id,
        ).write_bytes(b"\xff")
        with self.assertRaisesRegex(WorkspaceFormatError, "UTF-8"):
            catalog.get("session_a", invalid_utf8.conversation_id)

        malformed = catalog.create("session_a", "Malformed JSON")
        _metadata_path(
            workspace,
            "session_a",
            malformed.conversation_id,
        ).write_text("{not-json", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid JSON"):
            catalog.get("session_a", malformed.conversation_id)

        duplicate = catalog.create("session_a", "Duplicate field")
        payload = duplicate.to_dict()
        pairs = list(payload.items()) + [("status", "active")]
        duplicate_json = "{" + ",".join(
            f"{json.dumps(key)}:{json.dumps(value)}" for key, value in pairs
        ) + "}"
        _metadata_path(
            workspace,
            "session_a",
            duplicate.conversation_id,
        ).write_text(duplicate_json, encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid JSON"):
            catalog.get("session_a", duplicate.conversation_id)

    def test_missing_extra_and_non_object_json_fields_are_rejected(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        cases = ("missing", "extra", "non-object")
        for label in cases:
            metadata = catalog.create("session_a", f"Schema {label}")
            payload: object = metadata.to_dict()
            if label == "missing":
                assert isinstance(payload, dict)
                payload.pop("subject_revision")
            elif label == "extra":
                assert isinstance(payload, dict)
                payload["derived_subject_key"] = "must-not-be-authoritative"
            else:
                payload = [metadata.to_dict()]
            _write_payload(
                _metadata_path(
                    workspace,
                    "session_a",
                    metadata.conversation_id,
                ),
                payload,
            )
            with self.subTest(label=label):
                with self.assertRaisesRegex(WorkspaceFormatError, "invalid schema"):
                    catalog.get("session_a", metadata.conversation_id)

    def test_wrong_path_owners_are_rejected(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        for field, wrong_owner in (
            ("session_id", "other_session"),
            ("conversation_id", "other_conversation"),
        ):
            metadata = catalog.create("session_a", f"Wrong {field}")
            payload = metadata.to_dict()
            payload[field] = wrong_owner
            _write_payload(
                _metadata_path(
                    workspace,
                    "session_a",
                    metadata.conversation_id,
                ),
                payload,
            )
            with self.subTest(field=field):
                with self.assertRaisesRegex(WorkspaceFormatError, "owning path"):
                    catalog.get("session_a", metadata.conversation_id)

    def test_wrong_persisted_types_are_rejected(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        cases: tuple[tuple[str, object], ...] = (
            ("schema_version", True),
            ("session_id", 1),
            ("conversation_id", None),
            ("subject", ["not", "text"]),
            ("subject_revision", True),
            ("status", 1),
            ("created_at", 1),
            ("updated_at", None),
        )
        for field, invalid in cases:
            metadata = catalog.create("session_a", f"Wrong type {field}")
            payload = metadata.to_dict()
            payload[field] = invalid
            _write_payload(
                _metadata_path(
                    workspace,
                    "session_a",
                    metadata.conversation_id,
                ),
                payload,
            )
            with self.subTest(field=field):
                with self.assertRaisesRegex(WorkspaceFormatError, "invalid value"):
                    catalog.get("session_a", metadata.conversation_id)

    def test_invalid_persisted_values_and_timestamps_are_rejected(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        cases: tuple[tuple[str, str, object], ...] = (
            ("schema", "schema_version", 2),
            ("unsafe session", "session_id", "../escape"),
            ("unsafe conversation", "conversation_id", ".hidden"),
            ("blank subject", "subject", "   "),
            ("control subject", "subject", "bad\nsubject"),
            ("revision", "subject_revision", 0),
            ("status", "status", "deleted"),
            ("malformed timestamp", "created_at", "not-a-timestamp"),
            (
                "naive timestamp",
                "created_at",
                "2026-08-12T01:02:03",
            ),
            (
                "non-UTC timestamp",
                "created_at",
                "2026-08-12T10:02:03+09:00",
            ),
            (
                "noncanonical timestamp",
                "created_at",
                "2026-08-12T01:02:03Z",
            ),
            (
                "updated before created",
                "updated_at",
                "2000-01-01T00:00:00+00:00",
            ),
        )
        for label, field, invalid in cases:
            metadata = catalog.create("session_a", f"Invalid {label}")
            payload = metadata.to_dict()
            payload[field] = invalid
            _write_payload(
                _metadata_path(
                    workspace,
                    "session_a",
                    metadata.conversation_id,
                ),
                payload,
            )
            with self.subTest(label=label):
                with self.assertRaisesRegex(WorkspaceFormatError, "invalid value"):
                    catalog.get("session_a", metadata.conversation_id)

    def test_unsafe_call_identifiers_cannot_escape_the_workspace(self) -> None:
        workspace = self.new_workspace()
        catalog = FilesystemConversationCatalog(workspace)
        outside = workspace.root.parent / "must-not-be-created-by-catalog"
        self.assertFalse(outside.exists())

        for operation in (
            lambda: catalog.create("../outside", "Subject"),
            lambda: catalog.list("/absolute"),
            lambda: catalog.get("session_a", "../outside"),
            lambda: catalog.rename("session_a", ".hidden", "Subject"),
            lambda: catalog.archive("session_a", "contains.dot"),
            lambda: catalog.unarchive("session_a", "contains space"),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(SessionIdError):
                    operation()
        self.assertFalse(outside.exists())

    def test_non_directory_missing_and_unsafe_catalog_artifacts_are_rejected(
        self,
    ) -> None:
        cases = (
            "catalog-file",
            "entry-file",
            "missing-metadata",
            "unsafe-entry",
        )
        for case in cases:
            workspace = self.new_workspace()
            catalog = FilesystemConversationCatalog(workspace)
            catalog_root = (
                workspace.root
                / "discussion_logs"
                / "session_a"
                / "interactive"
                / "conversations"
            )
            if case == "catalog-file":
                catalog_root.parent.mkdir(parents=True)
                catalog_root.write_text("not a directory", encoding="utf-8")
            elif case == "entry-file":
                catalog_root.mkdir(parents=True)
                (catalog_root / "valid_id").write_text(
                    "not a directory",
                    encoding="utf-8",
                )
            elif case == "missing-metadata":
                (catalog_root / "valid_id").mkdir(parents=True)
            else:
                (catalog_root / ".unsafe").mkdir(parents=True)

            with self.subTest(case=case):
                with self.assertRaises(WorkspaceFormatError):
                    catalog.list("session_a", include_archived=True)
                if case == "missing-metadata":
                    with self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "metadata is missing",
                    ):
                        catalog.get("session_a", "valid_id")

    def test_symlink_catalog_conversation_and_metadata_are_rejected(self) -> None:
        for case in ("catalog", "conversation", "metadata"):
            workspace = self.new_workspace()
            catalog = FilesystemConversationCatalog(workspace)
            outside_temporary = tempfile.TemporaryDirectory()
            self.addCleanup(outside_temporary.cleanup)
            outside = Path(outside_temporary.name)
            catalog_root = (
                workspace.root
                / "discussion_logs"
                / "session_a"
                / "interactive"
                / "conversations"
            )

            if case == "catalog":
                catalog_root.parent.mkdir(parents=True)
                catalog_root.symlink_to(outside, target_is_directory=True)
                operation = lambda: catalog.list("session_a")
            elif case == "conversation":
                catalog_root.mkdir(parents=True)
                (catalog_root / "conversation_a").symlink_to(
                    outside,
                    target_is_directory=True,
                )
                operation = lambda: catalog.get("session_a", "conversation_a")
            else:
                metadata = catalog.create("session_a", "Symlink metadata")
                target = _metadata_path(
                    workspace,
                    "session_a",
                    metadata.conversation_id,
                )
                outside_metadata = outside / "metadata.json"
                outside_metadata.write_text(
                    json.dumps(metadata.to_dict()),
                    encoding="utf-8",
                )
                target.unlink()
                target.symlink_to(outside_metadata)
                operation = lambda: catalog.get(
                    "session_a",
                    metadata.conversation_id,
                )

            with self.subTest(case=case):
                with self.assertRaises(WorkspaceSecurityError):
                    operation()


class ConversationPackageBoundaryTests(unittest.TestCase):
    def test_supported_package_exports_are_identity_stable(self) -> None:
        expected_root = _ROOT_CONVERSATION_EXPORTS | {
            "ConversationExistsError",
            "ConversationNotFoundError",
            "FilesystemConversationCatalog",
            "FilesystemConversationEventStore",
        }
        self.assertTrue(expected_root.issubset(set(story_writing_agents.__all__)))
        self.assertEqual(
            tuple(conversations_module.__all__),
            _CONVERSATION_FACADE_EXPORTS,
        )
        self.assertNotIn("__getattr__", vars(conversations_module))

        owner_exports = (
            (conversation_catalog_module, _CATALOG_EXPORTS),
            (conversation_events_module, _EVENT_EXPORTS),
            (conversation_context_module, _CONTEXT_EXPORTS),
        )
        for owner, expected in owner_exports:
            with self.subTest(owner=owner.__name__):
                self.assertEqual(set(owner.__all__), expected)
                self.assertEqual(len(owner.__all__), len(expected))
                for name in expected:
                    self.assertIs(
                        getattr(owner, name),
                        getattr(conversations_module, name),
                    )

        for name in _ROOT_CONVERSATION_EXPORTS:
            with self.subTest(root_export=name):
                self.assertIn(name, story_writing_agents.__all__)
                self.assertIs(
                    getattr(story_writing_agents, name),
                    getattr(conversations_module, name),
                )
        for name in (
            "select_bounded_conversation_context",
            "validate_conversation_context_window",
        ):
            with self.subTest(module_only=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

        for name in _PUBLIC_CONVERSATION_OBJECTS:
            with self.subTest(canonical_module=name):
                self.assertEqual(
                    getattr(conversations_module, name).__module__,
                    "story_writing_agents.conversations",
                )
        self.assertEqual(
            conversations_module.CONVERSATION_CONTEXT_POLICY.__class__.__module__,
            "story_writing_agents.conversations",
        )
        self.assertIs(
            story_writing_agents.FilesystemConversationCatalog,
            workspace_module.FilesystemConversationCatalog,
        )
        self.assertTrue(getattr(ConversationCatalog, "_is_protocol", False))
        self.assertTrue(issubclass(ConversationExistsError, StoryWritingAgentsError))
        self.assertTrue(issubclass(ConversationNotFoundError, StoryWritingAgentsError))

    def test_public_definitions_live_in_their_exact_owner_modules(self) -> None:
        package = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "conversations"
        )
        expected_by_file = {
            "__init__.py": set(),
            "catalog.py": _CATALOG_EXPORTS,
            "events.py": _EVENT_EXPORTS,
            "context.py": _CONTEXT_EXPORTS,
        }
        public_names = set(_CONVERSATION_FACADE_EXPORTS)

        for filename, expected in expected_by_file.items():
            tree = ast.parse(
                (package / filename).read_text(encoding="utf-8"),
                filename=filename,
            )
            definitions: set[str] = set()
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    definitions.add(node.name)
                elif isinstance(node, ast.Assign):
                    definitions.update(
                        target.id
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    )
                elif isinstance(node, ast.AnnAssign) and isinstance(
                    node.target,
                    ast.Name,
                ):
                    definitions.add(node.target.id)
            with self.subTest(filename=filename):
                self.assertEqual(definitions & public_names, expected)

    def test_catalog_signatures_and_type_hints_are_unchanged(self) -> None:
        expected_parameters = {
            "create": ("self", "session_id", "subject"),
            "get": ("self", "session_id", "conversation_id"),
            "list": ("self", "session_id", "include_archived"),
            "rename": ("self", "session_id", "conversation_id", "subject"),
            "archive": ("self", "session_id", "conversation_id"),
            "unarchive": ("self", "session_id", "conversation_id"),
        }
        for name, expected in expected_parameters.items():
            method = getattr(conversation_catalog_module.ConversationCatalog, name)
            with self.subTest(method=name):
                self.assertEqual(tuple(inspect.signature(method).parameters), expected)
                expected_return = (
                    tuple[conversations_module.ConversationMetadata, ...]
                    if name == "list"
                    else conversations_module.ConversationMetadata
                )
                self.assertEqual(
                    get_type_hints(method)["return"],
                    expected_return,
                )

        list_signature = inspect.signature(
            conversation_catalog_module.ConversationCatalog.list
        )
        include_archived = list_signature.parameters["include_archived"]
        self.assertIs(include_archived.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(include_archived.default, False)
        normalize = conversation_catalog_module.normalize_conversation_subject
        self.assertEqual(tuple(inspect.signature(normalize).parameters), ("subject",))
        self.assertEqual(
            get_type_hints(normalize),
            {"subject": str, "return": str},
        )
        self.assertIs(
            get_type_hints(conversation_catalog_module.ConversationMetadata)[
                "status"
            ],
            conversations_module.ConversationStatus,
        )

    def test_package_and_boundary_modules_import_cleanly_in_fresh_interpreter(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source = repository_root / "src"
        code = f"""
import sys
sys.path.insert(0, {str(source)!r})
import story_writing_agents.conversations.context as context
import story_writing_agents.conversations.events as events
import story_writing_agents.conversations.catalog as catalog
import story_writing_agents.conversations as conversations
import story_writing_agents
import story_writing_agents.workspace as workspace
assert story_writing_agents.ConversationCatalog is conversations.ConversationCatalog
assert story_writing_agents.ConversationMetadata is conversations.ConversationMetadata
assert catalog.ConversationCatalog is conversations.ConversationCatalog
assert events.ConversationEvent is conversations.ConversationEvent
assert context.ConversationEventStore is conversations.ConversationEventStore
assert tuple(conversations.__all__) == {_CONVERSATION_FACADE_EXPORTS!r}
assert "__getattr__" not in vars(conversations)
for name in {tuple(sorted(_PUBLIC_CONVERSATION_OBJECTS))!r}:
    assert getattr(conversations, name).__module__ == "story_writing_agents.conversations"
assert (
    story_writing_agents.FilesystemConversationCatalog
    is workspace.FilesystemConversationCatalog
)
assert set({sorted(CONVERSATION_CATALOG_METHODS)!r}) <= set(dir(conversations.ConversationCatalog))
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-I", "-c", code],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
