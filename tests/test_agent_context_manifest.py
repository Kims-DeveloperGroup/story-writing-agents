"""Immutable content-free agent-context manifest contracts."""

from __future__ import annotations

import ast
import inspect
import itertools
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

from story_writing_agents.agent_context import manifest as manifest_module
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextManifestErrorCode,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
    context_manifest_entry_key,
    validate_context_digest,
    validate_context_identity,
)


_DIGEST = "sha256:" + ("1" * 64)
_UNSET = object()
_AUTHORITY_BY_KIND = {
    ContextSourceKind.RUNTIME_INPUT: ContextAuthorityKind.REQUEST,
    ContextSourceKind.AGENT_DEFINITION: ContextAuthorityKind.AGENT_DEFINITION,
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY
    ),
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,
}
_DEFAULT_CLASSIFICATION = {
    ContextSourceKind.RUNTIME_INPUT: (
        ContextSemanticChannel.CURRENT_INPUT,
        ContextPriority.P0_REQUIRED,
    ),
    ContextSourceKind.AGENT_DEFINITION: (
        ContextSemanticChannel.STABLE_BEHAVIOR,
        ContextPriority.P1_STABLE,
    ),
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextSemanticChannel.FOCUSED_EVIDENCE,
        ContextPriority.P2_WORKING,
    ),
    ContextSourceKind.CONVERSATION: (
        ContextSemanticChannel.HISTORY,
        ContextPriority.P2_WORKING,
    ),
    ContextSourceKind.ACTIVE_DEBATE: (
        ContextSemanticChannel.HARD_CONSTRAINT,
        ContextPriority.P2_WORKING,
    ),
    ContextSourceKind.OWN_AGENT_STATE: (
        ContextSemanticChannel.OPTIONAL_BACKGROUND,
        ContextPriority.P3_OPTIONAL,
    ),
}


def _scope(**overrides: object) -> ContextScope:
    values: dict[str, object] = {
        "workspace_id": "workspace",
        "session_id": "session",
        "subject_kind": ContextSubjectKind.CONVERSATION,
        "subject_id": "c_main",
        "agent_id": "lead_orchestrator",
    }
    values.update(overrides)
    return ContextScope(**values)  # type: ignore[arg-type]


def _provenance(
    authority: ContextAuthorityKind = ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
    locator: str = "core_documents/outline.md",
) -> ContextProvenance:
    return ContextProvenance(authority, locator)


def _entry(
    source_id: str = "artifact:outline",
    *,
    source_kind: ContextSourceKind = ContextSourceKind.WRITING_ARTIFACT,
    semantic_channel: ContextSemanticChannel | None = None,
    priority: ContextPriority | None = None,
    ordinal: int = 0,
    revision: str = "revision-1",
    content_digest: str = _DIGEST,
    provenance: ContextProvenance | None = None,
    utf8_bytes: int = 7,
    searchable: object = _UNSET,
) -> ContextManifestEntry:
    default_channel, default_priority = _DEFAULT_CLASSIFICATION[source_kind]
    selected_priority = priority or default_priority
    return ContextManifestEntry(
        source_id,
        source_kind,
        semantic_channel or default_channel,
        selected_priority,
        ordinal,
        revision,
        content_digest,
        provenance or _provenance(_AUTHORITY_BY_KIND[source_kind]),
        utf8_bytes,
        searchable=(
            selected_priority
            in {ContextPriority.P2_WORKING, ContextPriority.P3_OPTIONAL}
            if searchable is _UNSET
            else searchable
        ),  # type: ignore[arg-type]
    )


class ManifestSurfaceTests(unittest.TestCase):
    """Verify the complete direct-module surface and stable vocabularies."""

    def test_exact_direct_module_surface_and_enum_values(self) -> None:
        self.assertEqual(
            tuple(manifest_module.__all__),
            (
                "ContextAuthorityKind",
                "ContextManifest",
                "ContextManifestEntry",
                "ContextManifestError",
                "ContextManifestErrorCode",
                "ContextPriority",
                "ContextProvenance",
                "ContextScope",
                "ContextContinuationIdentity",
                "validate_context_history_coverage",
                "ContextSemanticChannel",
                "ContextSourceKind",
                "ContextSubjectKind",
                "context_manifest_entry_key",
                "validate_context_digest",
                "validate_context_identity",
            ),
        )
        self.assertEqual( tuple(item.value for item in ContextSubjectKind), ("conversation", "debate", "session"), )
        self.assertEqual(
            tuple(item.value for item in ContextAuthorityKind),
            (
                "request",
                "agent_definition",
                "writing_artifact_repository",
                "conversation_storage",
                "active_debate",
                "own_agent_state",
                "reference_authorization",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ContextSourceKind),
            (
                "runtime_input",
                "agent_definition",
                "writing_artifact",
                "conversation",
                "active_debate",
                "own_agent_state",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ContextPriority),
            ("p0_required", "p1_stable", "p2_working", "p3_optional"),
        )
        self.assertEqual(
            tuple(item.value for item in ContextSemanticChannel),
            (
                "current_input",
                "candidate",
                "stable_behavior",
                "hard_constraint",
                "focused_evidence",
                "retrieved_evidence",
                "history",
                "optional_background",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ContextManifestErrorCode),
            ("invalid_value", "invalid_matrix", "duplicate_source_id"),
        )

    def test_dataclass_fields_are_exact_and_searchable_is_required_keyword_only(
        self,
    ) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(ContextScope)),
            (
                "workspace_id",
                "session_id",
                "subject_kind",
                "subject_id",
                "agent_id",
            ),
        )
        self.assertEqual( tuple(field.name for field in fields(ContextProvenance)), ("authority_kind", "locator"), )
        self.assertEqual(
            tuple(field.name for field in fields(ContextManifestEntry)),
            (
                "source_id",
                "source_kind",
                "semantic_channel",
                "priority",
                "ordinal",
                "revision",
                "content_digest",
                "provenance",
                "utf8_bytes",
                "searchable",
            ),
        )
        searchable = inspect.signature(ContextManifestEntry).parameters["searchable"]
        self.assertIs(searchable.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(searchable.default, inspect.Parameter.empty)
        valid = _entry()
        positional = tuple(getattr(valid, field.name) for field in fields(valid))
        with self.assertRaises(TypeError):
            ContextManifestEntry(*positional)  # type: ignore[misc]
        with self.assertRaises(TypeError):
            ContextManifestEntry(*positional[:-1])  # type: ignore[misc]


class ManifestIdentityTests(unittest.TestCase):
    """Verify bounded identities, subject grammar, digests, and provenance."""

    def assert_manifest_error(
        self,
        callable_: object,
        code: ContextManifestErrorCode,
    ) -> ContextManifestError:
        with self.assertRaises(ContextManifestError) as caught:
            callable_()  # type: ignore[operator]
        self.assertIs(caught.exception.code, code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        return caught.exception

    def test_identity_exact_utf8_boundaries_and_unsafe_text(self) -> None:
        ascii_boundary = "a" * 1024
        unicode_boundary = ("한" * 341) + "a"
        self.assertEqual(len(unicode_boundary.encode("utf-8")), 1024)
        self.assertEqual(validate_context_identity(ascii_boundary), ascii_boundary)
        self.assertEqual(validate_context_identity(unicode_boundary), unicode_boundary)
        invalid = (
            "",
            "a" * 1025,
            unicode_boundary + "é",
            "line\nfeed",
            "tab\tvalue",
            "zero\u200bwidth",
            "private\ud800surrogate",
            "line\u2028separator",
            "paragraph\u2029separator",
            1,
            True,
            None,
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                error = self.assert_manifest_error(
                    lambda value=value: validate_context_identity(value),
                    ContextManifestErrorCode.INVALID_VALUE,
                )
                self.assertEqual( str(error), "context manifest contains an invalid value", )
                self.assertNotIn("private", str(error).lower())
                self.assertNotIn("private", repr(error).lower())

    def test_all_generic_identity_fields_use_the_safe_grammar(self) -> None:
        scope = _scope()
        provenance = _provenance()
        entry = _entry()
        cases = (
            lambda: replace(scope, workspace_id="unsafe\nworkspace"),
            lambda: replace(scope, session_id="unsafe\nsession"),
            lambda: replace(scope, agent_id="unsafe\nagent"),
            lambda: replace(provenance, locator="unsafe\nlocator"),
            lambda: replace(entry, source_id="unsafe\nsource"),
            lambda: replace(entry, revision="unsafe\nrevision"),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assert_manifest_error( case, ContextManifestErrorCode.INVALID_VALUE, )

    def test_subject_kind_and_id_exact_grammar(self) -> None:
        for subject_id in ("A", "a" * 64, "A0_-z"):
            with self.subTest(subject_id=subject_id):
                self.assertEqual(_scope(subject_id=subject_id).subject_id, subject_id)
        for subject_id in (
            "",
            "a" * 65,
            "_leading",
            "-leading",
            "space value",
            "colon:value",
            "한글",
            "line\nbreak",
            1,
            True,
            None,
        ):
            with self.subTest(subject_id=subject_id):
                self.assert_manifest_error(
                    lambda subject_id=subject_id: _scope(subject_id=subject_id),
                    ContextManifestErrorCode.INVALID_VALUE,
                )
        for subject_kind in ("conversation", 1, True, None):
            with self.subTest(subject_kind=subject_kind):
                self.assert_manifest_error(
                    lambda subject_kind=subject_kind: _scope( subject_kind=subject_kind ),
                    ContextManifestErrorCode.INVALID_VALUE,
                )

    def test_digest_is_exact_lowercase_sha256(self) -> None:
        valid = "sha256:" + ("0123456789abcdef" * 4)
        self.assertEqual(validate_context_digest(valid), valid)
        invalid = (
            "",
            "sha256:" + ("a" * 63),
            "sha256:" + ("a" * 65),
            "sha256:" + ("A" * 64),
            "sha256:" + ("g" * 64),
            "sha1:" + ("a" * 64),
            1,
            True,
            None,
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assert_manifest_error(
                    lambda value=value: validate_context_digest(value),
                    ContextManifestErrorCode.INVALID_VALUE,
                )

    def test_provenance_rejects_absolute_posix_and_windows_locators(self) -> None:
        valid = (
            "core_documents/outline.md",
            "manuscript/part_01/chapter.md#L4-L8",
            "conversation:c_main:event:4",
            "C:relative.txt",
        )
        for locator in valid:
            with self.subTest(locator=locator):
                self.assertEqual(_provenance(locator=locator).locator, locator)
        invalid = (
            "/private/outline.md",
            "//server/share/file.md",
            "\\rooted\\file.md",
            "\\\\server\\share\\file.md",
            "C:\\private\\file.md",
            "D:/private/file.md",
        )
        for locator in invalid:
            with self.subTest(locator=locator):
                error = self.assert_manifest_error(
                    lambda locator=locator: _provenance(locator=locator),
                    ContextManifestErrorCode.INVALID_VALUE,
                )
                self.assertNotIn(locator, str(error))
                self.assertNotIn(locator, repr(error))
        for authority in ("writing_artifact_repository", 1, True, None):
            with self.subTest(authority=authority):
                self.assert_manifest_error(
                    lambda authority=authority: ContextProvenance(authority, "relative/path"),  # type: ignore[arg-type]
                    ContextManifestErrorCode.INVALID_VALUE,
                )

    def test_exact_nonboolean_integer_counts(self) -> None:
        self.assertEqual(_entry(ordinal=0, utf8_bytes=0).ordinal, 0)
        for field_name, value in itertools.product( ("ordinal", "utf8_bytes"), (-1, True, 1.0, "1", None), ):
            with self.subTest(field_name=field_name, value=value):
                self.assert_manifest_error(
                    lambda field_name=field_name, value=value: _entry(
                        **{field_name: value}  # type: ignore[arg-type]
                    ),
                    ContextManifestErrorCode.INVALID_VALUE,
                )


class ManifestClassificationTests(unittest.TestCase):
    """Verify exhaustive source classification and authority matrices."""

    def test_kind_channel_priority_matrix_is_exhaustive(self) -> None:
        expected: set[
            tuple[ContextSourceKind, ContextSemanticChannel, ContextPriority]
        ] = {
            (
                ContextSourceKind.RUNTIME_INPUT,
                ContextSemanticChannel.CURRENT_INPUT,
                ContextPriority.P0_REQUIRED,
            ),
            (
                ContextSourceKind.RUNTIME_INPUT,
                ContextSemanticChannel.CANDIDATE,
                ContextPriority.P0_REQUIRED,
            ),
            (
                ContextSourceKind.AGENT_DEFINITION,
                ContextSemanticChannel.STABLE_BEHAVIOR,
                ContextPriority.P1_STABLE,
            ),
        }
        for kind in (
            ContextSourceKind.WRITING_ARTIFACT,
            ContextSourceKind.CONVERSATION,
            ContextSourceKind.ACTIVE_DEBATE,
            ContextSourceKind.OWN_AGENT_STATE,
        ):
            for channel in (
                ContextSemanticChannel.HARD_CONSTRAINT,
                ContextSemanticChannel.FOCUSED_EVIDENCE,
                ContextSemanticChannel.RETRIEVED_EVIDENCE,
                ContextSemanticChannel.HISTORY,
            ):
                expected.add((kind, channel, ContextPriority.P2_WORKING))
            expected.add( ( kind, ContextSemanticChannel.OPTIONAL_BACKGROUND, ContextPriority.P3_OPTIONAL, ) )
        self.assertEqual(len(expected), 23)
        for kind, channel, priority in itertools.product( ContextSourceKind, ContextSemanticChannel, ContextPriority, ):
            with self.subTest(kind=kind, channel=channel, priority=priority):
                if (kind, channel, priority) in expected:
                    entry = _entry(source_kind=kind, semantic_channel=channel, priority=priority)
                    self.assertIs(entry.source_kind, kind)
                else:
                    with self.assertRaises(ContextManifestError) as caught:
                        _entry(source_kind=kind, semantic_channel=channel, priority=priority)
                    self.assertIs( caught.exception.code, ContextManifestErrorCode.INVALID_MATRIX, )

    def test_source_kind_has_exactly_one_authority(self) -> None:
        for kind, authority in itertools.product( ContextSourceKind, ContextAuthorityKind, ):
            with self.subTest(kind=kind, authority=authority):
                if authority is _AUTHORITY_BY_KIND[kind]:
                    self.assertIs(
                        _entry(source_kind=kind, provenance=_provenance(authority)).provenance.authority_kind,
                        authority,
                    )
                else:
                    with self.assertRaises(ContextManifestError) as caught:
                        _entry( source_kind=kind, provenance=_provenance(authority), )
                    self.assertIs( caught.exception.code, ContextManifestErrorCode.INVALID_MATRIX, )

    def test_searchability_is_exactly_priority_owned(self) -> None:
        valid_cases = (
            (ContextSourceKind.RUNTIME_INPUT, False),
            (ContextSourceKind.AGENT_DEFINITION, False),
            (ContextSourceKind.WRITING_ARTIFACT, True),
            (ContextSourceKind.OWN_AGENT_STATE, True),
        )
        for kind, searchable in valid_cases:
            with self.subTest(kind=kind):
                self.assertIs( _entry(source_kind=kind, searchable=searchable).searchable, searchable, )
                with self.assertRaises(ContextManifestError) as caught:
                    _entry(source_kind=kind, searchable=not searchable)
                self.assertIs( caught.exception.code, ContextManifestErrorCode.INVALID_MATRIX, )
        for searchable in (0, 1, "true", None):
            with self.subTest(searchable=searchable):
                with self.assertRaises(ContextManifestError) as caught:
                    _entry(searchable=searchable)  # type: ignore[arg-type]
                self.assertIs( caught.exception.code, ContextManifestErrorCode.INVALID_VALUE, )


class ManifestCanonicalizationTests(unittest.TestCase):
    """Verify immutable canonical snapshots and content-free diagnostics."""

    def test_manifest_rejects_duplicate_source_identity_across_revisions(self) -> None:
        first = _entry(revision="r1")
        second = _entry(revision="r2", provenance=_provenance(locator="other"))
        with self.assertRaises(ContextManifestError) as caught:
            ContextManifest(_scope(), (first, second))
        self.assertIs( caught.exception.code, ContextManifestErrorCode.DUPLICATE_SOURCE_ID, )

    def test_canonical_order_is_permutation_locale_and_hash_independent(self) -> None:
        source_ids = ("a", "e\u0301", "é", "한", "🙂")
        entries = tuple( _entry(source_id, ordinal=3, revision="same") for source_id in source_ids )
        expected_ids = tuple( sorted(source_ids, key=lambda value: value.encode("utf-8")) )
        for permutation in itertools.permutations(entries):
            manifest = ContextManifest(_scope(), permutation)
            self.assertEqual( tuple(entry.source_id for entry in manifest.entries), expected_ids, )
        unordered = {entry.source_id: entry for entry in reversed(entries)}
        self.assertEqual(
            tuple(
                entry.source_id
                for entry in ContextManifest( _scope(), tuple(unordered.values()), ).entries
            ),
            expected_ids,
        )

    def test_priority_channel_ordinal_and_metadata_key_are_exact(self) -> None:
        entries = (
            _entry("optional", source_kind=ContextSourceKind.OWN_AGENT_STATE, ordinal=9),
            _entry("required", source_kind=ContextSourceKind.RUNTIME_INPUT, ordinal=5),
            _entry("stable", source_kind=ContextSourceKind.AGENT_DEFINITION, ordinal=2),
            _entry("working-later", ordinal=8),
            _entry("working-first", ordinal=1),
        )
        manifest = ContextManifest(_scope(), tuple(reversed(entries)))
        self.assertEqual(
            tuple(entry.source_id for entry in manifest.entries),
            (
                "required",
                "stable",
                "working-first",
                "working-later",
                "optional",
            ),
        )
        key = context_manifest_entry_key(_entry())
        self.assertEqual(len(key), 9)
        self.assertTrue(all(isinstance(value, (int, bytes)) for value in key))
        with self.assertRaises(ContextManifestError):
            context_manifest_entry_key(object())  # type: ignore[arg-type]

    def test_values_are_frozen_slotted_and_repr_contains_no_body(self) -> None:
        secret = "PRIVATE_STORY_BODY_한🙂"
        values = (
            _scope(),
            _provenance(),
            _entry(),
            ContextManifest(_scope(), (_entry(),)),
        )
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertNotIn(secret, repr(value))
                with self.assertRaises( (FrozenInstanceError, AttributeError, TypeError) ):
                    value.__setattr__("private_body", secret)

    def test_module_is_a_standard_library_leaf_and_compiles(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "agent_context"
            / "manifest.py"
        )
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        compile(source, str(path), "exec")
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module != "__future__"
        }
        imports.update( alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names )
        self.assertEqual(imports, {"re", "unicodedata", "dataclasses", "enum"})


if __name__ == "__main__":
    unittest.main()
