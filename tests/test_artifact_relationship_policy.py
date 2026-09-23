"""Exact relationship-policy and story-workflow verification for Issue #446."""

from __future__ import annotations

import ast
import hashlib
import inspect
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import (
    CORE_ARTIFACT_PATHS,
    ArtifactChangeSetError,
    ArtifactChangeSetErrorCode,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
)
from story_writing_agents.writing_artifacts import (
    relationship_policy as relationship_module,
)
from story_writing_agents.writing_artifacts.relationship_policy import (
    ARTIFACT_RELATIONSHIP_COMPANION_RULES,
    ARTIFACT_RELATIONSHIP_SCHEMA_VERSION,
    MAX_RELATIONSHIP_ARTIFACTS,
    MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
    MAX_RELATIONSHIP_EDGES,
    MAX_RELATIONSHIP_STATE_UTF8_BYTES,
    ArtifactRelationshipEdge,
    ArtifactRelationshipError,
    ArtifactRelationshipErrorCode,
    ArtifactRelationshipKind,
    ArtifactRelationshipValidation,
    ArtifactReviewDomain,
    ArtifactReviewFact,
    ArtifactReviewReason,
    validate_artifact_relationships,
)


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _ROOT / "src" / "story_writing_agents"
_CORE_DOMAINS = {
    "core_documents/global_outline.md": ArtifactReviewDomain.PLOT_STRUCTURE,
    "core_documents/part_outlines.md": ArtifactReviewDomain.PLOT_STRUCTURE,
    "core_documents/character_sheets.md": ArtifactReviewDomain.CHARACTER_WORLD,
    "core_documents/narrative_rules.md": ArtifactReviewDomain.TONE_EXECUTION,
}
_ERROR_MESSAGES = {
    ArtifactRelationshipErrorCode.INVALID_SCHEMA: (
        "artifact relationship input schema is invalid"
    ),
    ArtifactRelationshipErrorCode.INVALID_ATTACHED_CORE_PATHS: (
        "attached core artifact paths are invalid"
    ),
    ArtifactRelationshipErrorCode.PROTECTED_CORE_OPERATION: (
        "a permanent core artifact cannot be renamed or deleted"
    ),
    ArtifactRelationshipErrorCode.PATH_ESCAPE: (
        "an artifact relationship target escapes the workspace"
    ),
    ArtifactRelationshipErrorCode.ARTIFACT_COUNT_LIMIT: (
        "artifact relationship state exceeds the artifact-count limit"
    ),
    ArtifactRelationshipErrorCode.ARTIFACT_SIZE_LIMIT: (
        "artifact relationship state exceeds the per-artifact byte limit"
    ),
    ArtifactRelationshipErrorCode.STATE_SIZE_LIMIT: (
        "artifact relationship state exceeds the total byte limit"
    ),
    ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT: (
        "artifact relationship state exceeds the edge-count limit"
    ),
}
_MODULE_EXPORTS = (
    "ARTIFACT_RELATIONSHIP_COMPANION_RULES",
    "ARTIFACT_RELATIONSHIP_SCHEMA_VERSION",
    "MAX_RELATIONSHIP_ARTIFACTS",
    "MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES",
    "MAX_RELATIONSHIP_EDGES",
    "MAX_RELATIONSHIP_STATE_UTF8_BYTES",
    "ArtifactRelationshipEdge",
    "ArtifactRelationshipError",
    "ArtifactRelationshipErrorCode",
    "ArtifactRelationshipKind",
    "ArtifactRelationshipValidation",
    "ArtifactReviewDomain",
    "ArtifactReviewFact",
    "ArtifactReviewReason",
    "validate_artifact_relationships",
)
_MANUSCRIPT = "manuscript/part-01/opening.md"
_SECOND_MANUSCRIPT = "manuscript/part-01/index.md"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _create(
    path: str,
    content: bytes,
    *,
    kind: ArtifactKind = ArtifactKind.MANUSCRIPT,
) -> ArtifactCreateOperation:
    return ArtifactCreateOperation(path, kind, content, _sha256(content))


def _replace(
    path: str,
    before: bytes,
    after: bytes,
    *,
    kind: ArtifactKind = ArtifactKind.MANUSCRIPT,
) -> ArtifactReplaceOperation:
    return ArtifactReplaceOperation(
        path,
        kind,
        _sha256(before),
        after,
        _sha256(after),
    )


def _patch(
    path: str,
    before: bytes,
    after: bytes,
    *,
    kind: ArtifactKind = ArtifactKind.MANUSCRIPT,
) -> ArtifactPatchOperation:
    digest = _sha256(before)
    return ArtifactPatchOperation(
        path,
        kind,
        digest,
        (ArtifactTextHunk(digest, 0, len(before), before, after),),
        _sha256(after),
    )


def _rename(source: str, destination: str, content: bytes) -> ArtifactRenameOperation:
    digest = _sha256(content)
    return ArtifactRenameOperation(
        source,
        destination,
        ArtifactKind.MANUSCRIPT,
        digest,
        digest,
    )


def _delete(
    path: str,
    content: bytes,
    *,
    kind: ArtifactKind = ArtifactKind.MANUSCRIPT,
) -> ArtifactDeleteOperation:
    return ArtifactDeleteOperation(path, kind, _sha256(content))


def _validation_for_replace(
    before: bytes,
    after: bytes,
    *,
    extra_state: Mapping[str, bytes] | None = None,
    attached_core_paths: tuple[str, ...] = (),
) -> ArtifactRelationshipValidation:
    base = {_MANUSCRIPT: before, **dict(extra_state or {})}
    change_set = build_artifact_change_set(
        base,
        (_replace(_MANUSCRIPT, before, after),),
    )
    return validate_artifact_relationships(
        base,
        change_set,
        attached_core_paths=attached_core_paths,
    )


def _edge(
    source: str = _MANUSCRIPT,
    target: str = _SECOND_MANUSCRIPT,
    *,
    start: int = 1,
    end: int = 2,
    fragment: str | None = None,
) -> ArtifactRelationshipEdge:
    return ArtifactRelationshipEdge(
        source,
        target,
        start,
        end,
        fragment,
        ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
    )


def _fact(
    domain: ArtifactReviewDomain = ArtifactReviewDomain.TONE_EXECUTION,
    reason: ArtifactReviewReason = ArtifactReviewReason.MANUSCRIPT_OPERATION,
    path: str = _MANUSCRIPT,
    operation_index: int | None = 0,
) -> ArtifactReviewFact:
    return ArtifactReviewFact(domain, reason, path, operation_index)


class _ExplodingMapping(Mapping[str, bytes]):
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def __getitem__(self, key: str) -> bytes:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self):
        raise RuntimeError(self.secret)


class RelationshipTestCase(unittest.TestCase):
    def assert_relationship_error(
        self,
        code: ArtifactRelationshipErrorCode,
        callback,
        *,
        operation_index: int | None = None,
        artifact_index: int | None = None,
        link_index: int | None = None,
    ) -> ArtifactRelationshipError:
        with self.assertRaises(ArtifactRelationshipError) as raised:
            callback()
        error = raised.exception
        self.assertIs(error.code, code)
        self.assertEqual(str(error), _ERROR_MESSAGES[code])
        self.assertEqual(error.args, (_ERROR_MESSAGES[code],))
        self.assertEqual(error.operation_index, operation_index)
        self.assertEqual(error.artifact_index, artifact_index)
        self.assertEqual(error.link_index, link_index)
        return error

    def assert_exception_graph_excludes(
        self,
        error: BaseException,
        secrets: tuple[str, ...],
    ) -> None:
        pending: list[BaseException] = [error]
        seen: set[int] = set()
        rendered: list[str] = []
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            rendered.extend((str(current), repr(current), repr(current.args)))
            if hasattr(current, "__dict__"):
                rendered.append(repr(vars(current)))
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
        combined = "\n".join(rendered)
        for secret in secrets:
            self.assertNotIn(secret, combined)


class ArtifactRelationshipSurfaceTests(RelationshipTestCase):
    def test_exact_public_surface_constants_enums_signature_and_facades(self) -> None:
        self.assertEqual(ARTIFACT_RELATIONSHIP_SCHEMA_VERSION, 1)
        self.assertEqual(ARTIFACT_RELATIONSHIP_COMPANION_RULES, ())
        self.assertEqual(MAX_RELATIONSHIP_ARTIFACTS, 4_096)
        self.assertEqual(MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES, 8 * 1024 * 1024)
        self.assertEqual(MAX_RELATIONSHIP_STATE_UTF8_BYTES, 64 * 1024 * 1024)
        self.assertEqual(MAX_RELATIONSHIP_EDGES, 65_536)
        self.assertEqual(tuple(relationship_module.__all__), _MODULE_EXPORTS)
        self.assertEqual(len(writing_artifacts.__all__), 78)
        for name in _MODULE_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(writing_artifacts, name),
                    getattr(relationship_module, name),
                )
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))
        self.assertEqual(len(story_writing_agents.__all__), 106)
        self.assertEqual(
            tuple(code.value for code in ArtifactRelationshipErrorCode),
            tuple(code.value for code in _ERROR_MESSAGES),
        )
        self.assertEqual(
            tuple(kind.value for kind in ArtifactRelationshipKind),
            ("inline_markdown_link",),
        )
        self.assertEqual(
            tuple(domain.value for domain in ArtifactReviewDomain),
            ("plot_structure", "character_world", "tone_execution"),
        )
        self.assertEqual(
            tuple(reason.value for reason in ArtifactReviewReason),
            (
                "touched_core",
                "manuscript_operation",
                "attached_core_evidence",
                "relationship_core_evidence",
            ),
        )
        signature = inspect.signature(validate_artifact_relationships)
        self.assertEqual(
            tuple(signature.parameters),
            ("base_state", "change_set", "attached_core_paths"),
        )
        self.assertIs(
            signature.parameters["attached_core_paths"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )

    def test_value_schemas_are_exact_frozen_slotted_and_content_free(self) -> None:
        expected_fields = {
            ArtifactRelationshipEdge: (
                "source_path",
                "target_path",
                "destination_start_utf8_byte",
                "destination_end_utf8_byte",
                "fragment",
                "relationship_kind",
            ),
            ArtifactReviewFact: (
                "domain",
                "reason",
                "artifact_path",
                "operation_index",
            ),
            ArtifactRelationshipValidation: (
                "schema_version",
                "base_state_digest",
                "projected_state_digest",
                "change_set_digest",
                "added_edges",
                "removed_edges",
                "retained_edges",
                "projected_dangling_edges",
                "review_facts",
            ),
        }
        validation = _validation_for_replace(
            b"[secret label](index.md#secret-fragment)",
            b"[secret label](index.md#secret-fragment)\n",
            extra_state={_SECOND_MANUSCRIPT: b"destination"},
        )
        values = (validation.retained_edges[0], validation.review_facts[0], validation)
        for value_type, expected in expected_fields.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(value_type)), expected
                )
                self.assertEqual(
                    tuple(inspect.signature(value_type).parameters), expected
                )
                self.assertTrue(value_type.__dataclass_params__.frozen)
                value = next(item for item in values if type(item) is value_type)
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, expected[0], getattr(value, expected[0]))
        rendered = repr(validation)
        self.assertNotIn("secret label", rendered)
        self.assertNotIn("secret-fragment", rendered)

    def test_edge_manual_invariants_reject_invalid_values(self) -> None:
        invalid = (
            lambda: ArtifactRelationshipEdge(
                "notes/private.md",
                _SECOND_MANUSCRIPT,
                0,
                1,
                None,
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                "agent_states/secret.md",
                0,
                1,
                None,
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                _SECOND_MANUSCRIPT,
                -1,
                1,
                None,
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                _SECOND_MANUSCRIPT,
                1,
                1,
                None,
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                _SECOND_MANUSCRIPT,
                True,
                2,
                None,
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                _SECOND_MANUSCRIPT,
                0,
                1,
                "bad fragment",
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                _SECOND_MANUSCRIPT,
                0,
                1,
                None,
                "inline_markdown_link",  # type: ignore[arg-type]
            ),
        )
        for callback in invalid:
            with self.subTest(callback=callback):
                self.assert_relationship_error(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA,
                    callback,
                )

    def test_review_fact_manual_invariants_reject_invalid_combinations(self) -> None:
        outline = CORE_ARTIFACT_PATHS[0]
        invalid = (
            lambda: ArtifactReviewFact(
                "plot_structure",  # type: ignore[arg-type]
                ArtifactReviewReason.TOUCHED_CORE,
                outline,
                0,
            ),
            lambda: ArtifactReviewFact(
                ArtifactReviewDomain.PLOT_STRUCTURE,
                ArtifactReviewReason.TOUCHED_CORE,
                outline,
                None,
            ),
            lambda: ArtifactReviewFact(
                ArtifactReviewDomain.TONE_EXECUTION,
                ArtifactReviewReason.MANUSCRIPT_OPERATION,
                outline,
                0,
            ),
            lambda: ArtifactReviewFact(
                ArtifactReviewDomain.PLOT_STRUCTURE,
                ArtifactReviewReason.ATTACHED_CORE_EVIDENCE,
                outline,
                0,
            ),
            lambda: ArtifactReviewFact(
                ArtifactReviewDomain.CHARACTER_WORLD,
                ArtifactReviewReason.TOUCHED_CORE,
                outline,
                0,
            ),
            lambda: ArtifactReviewFact(
                ArtifactReviewDomain.TONE_EXECUTION,
                ArtifactReviewReason.MANUSCRIPT_OPERATION,
                _MANUSCRIPT,
                -1,
            ),
        )
        for callback in invalid:
            with self.subTest(callback=callback):
                self.assert_relationship_error(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA,
                    callback,
                )

    def test_validation_manual_invariants_and_is_valid_are_exact(self) -> None:
        first = _edge(start=1, end=2)
        second = _edge(start=3, end=4)
        fact = _fact()
        valid = ArtifactRelationshipValidation(
            1,
            "0" * 64,
            "1" * 64,
            "2" * 64,
            (first,),
            (),
            (second,),
            (),
            (fact,),
        )
        self.assertTrue(valid.is_valid)
        dangling = ArtifactRelationshipValidation(
            1,
            "0" * 64,
            "1" * 64,
            "2" * 64,
            (first,),
            (),
            (),
            (first,),
            (fact,),
        )
        self.assertFalse(dangling.is_valid)
        invalid = (
            lambda: ArtifactRelationshipValidation(
                2, "0" * 64, "1" * 64, "2" * 64, (), (), (), (), ()
            ),
            lambda: ArtifactRelationshipValidation(
                1, "BAD", "1" * 64, "2" * 64, (), (), (), (), ()
            ),
            lambda: ArtifactRelationshipValidation(
                1,
                "0" * 64,
                "1" * 64,
                "2" * 64,
                [first],
                (),
                (),
                (),
                (),  # type: ignore[arg-type]
            ),
            lambda: ArtifactRelationshipValidation(
                1, "0" * 64, "1" * 64, "2" * 64, (second, first), (), (), (), ()
            ),
            lambda: ArtifactRelationshipValidation(
                1, "0" * 64, "1" * 64, "2" * 64, (first,), (first,), (), (), ()
            ),
            lambda: ArtifactRelationshipValidation(
                1, "0" * 64, "1" * 64, "2" * 64, (), (), (), (first,), ()
            ),
            lambda: ArtifactRelationshipValidation(
                1, "0" * 64, "1" * 64, "2" * 64, (), (), (), (), (fact, fact)
            ),
        )
        for callback in invalid:
            with self.subTest(callback=callback):
                self.assert_relationship_error(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA,
                    callback,
                )


class ArtifactRelationshipGrammarTests(RelationshipTestCase):
    def test_exact_inline_grammar_excludes_every_unsupported_marker(self) -> None:
        valid = b"[Valid](index.md)"
        unsupported = b"\n".join(
            (
                b"![image](index.md)",
                b"[reference][index]",
                b"[index]: index.md",
                b"<index.md>",
                b'<a href="index.md">html</a>',
                b'[title](index.md "caption")',
                b"[query](index.md?view=1)",
                b"[scheme](https://example.invalid/index.md)",
                b"[authority](//example.invalid/index.md)",
                b"[percent](index%2Emd)",
                b"[space](index .md)",
                b"[backslash](sub\\index.md)",
                b"\\[escaped](index.md)",
                b"[nested](sub(index).md)",
                b"`[inline code](index.md)`",
                b"```markdown\n[fenced](index.md)\n```",
                b"~~~\n[fenced tilde](index.md)\n~~~",
                b"[empty]()",
                b"[empty fragment](index.md#)",
                b"[double fragment](index.md#a#b)",
            )
        )
        content = valid + b"\n" + unsupported
        validation = _validation_for_replace(
            content,
            content + b"\nordinary prose",
            extra_state={_SECOND_MANUSCRIPT: b"target"},
        )
        self.assertEqual(len(validation.retained_edges), 1)
        self.assertEqual(validation.retained_edges[0].target_path, _SECOND_MANUSCRIPT)

    def test_relative_root_and_dot_segments_resolve_from_source_identity(self) -> None:
        source = "manuscript/part-01/scenes/source.md"
        relative = "manuscript/part-01/target.md"
        root_core = "core_documents/narrative_rules.md"
        before = (
            b"[Relative](./../target.md)\n"
            b"[Root](/core_documents/narrative_rules.md#voice)"
        )
        after = before + b"\nrevised"
        base = {source: before, relative: b"target", root_core: b"rules"}
        change_set = build_artifact_change_set(
            base,
            (_replace(source, before, after),),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertEqual(
            tuple(edge.target_path for edge in validation.retained_edges),
            (relative, root_core),
        )
        self.assertEqual(
            tuple(edge.fragment for edge in validation.retained_edges),
            (None, "voice"),
        )

    def test_escape_above_workspace_fails_with_exact_sorted_indices(self) -> None:
        first = "manuscript/part-01/a.md"
        second = "manuscript/part-01/b.md"
        secret = "SECRET_ESCAPE_LABEL"
        base = {
            second: f"[{secret}](../../../outside.md)".encode(),
            first: b"safe",
        }
        change_set = build_artifact_change_set(
            base,
            (_replace(first, b"safe", b"safer"),),
        )
        error = self.assert_relationship_error(
            ArtifactRelationshipErrorCode.PATH_ESCAPE,
            lambda: validate_artifact_relationships(
                base,
                change_set,
                attached_core_paths=(),
            ),
            artifact_index=1,
            link_index=0,
        )
        self.assert_exception_graph_excludes(error, (secret, "outside.md"))

    def test_utf8_destination_span_and_unicode_label_fragment_are_exact(self) -> None:
        label = "바다의 🌙"
        fragment = "장면🌙"
        target = "index.md"
        before = f"서문 {label}: [{label}]({target}#{fragment}) 끝".encode()
        validation = _validation_for_replace(
            before,
            before + "\n수정".encode(),
            extra_state={_SECOND_MANUSCRIPT: b"target"},
        )
        edge = validation.retained_edges[0]
        expected_start = before.index(target.encode())
        self.assertEqual(edge.destination_start_utf8_byte, expected_start)
        self.assertEqual(edge.destination_end_utf8_byte, expected_start + len(target))
        self.assertEqual(edge.fragment, fragment)
        self.assertNotIn(label, repr(edge))
        self.assertNotIn(fragment, repr(edge))

    def test_noncanonical_internal_and_unicode_targets_never_enter_graph(self) -> None:
        content = b"\n".join(
            (
                "[Unicode](장면.md)".encode(),
                b"[Transcript](/transcripts/session.md)",
                b"[Discussion](/discussion_logs/debate.md)",
                b"[Agent](/agent_states/lead.md)",
                b"[Notes](../../notes/private.md)",
            )
        )
        validation = _validation_for_replace(content, content + b"\nchanged")
        self.assertEqual(validation.added_edges, ())
        self.assertEqual(validation.removed_edges, ())
        self.assertEqual(validation.retained_edges, ())
        self.assertEqual(validation.projected_dangling_edges, ())

    def test_noncanonical_destination_is_prose_but_unsafe_escape_is_error(self) -> None:
        safe = b"[ordinary](../../notes/private.md)"
        validation = _validation_for_replace(safe, safe + b"\nchanged")
        self.assertTrue(validation.is_valid)
        unsafe = b"[unsafe](../../../notes/private.md)"
        base = {_MANUSCRIPT: unsafe}
        change_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, unsafe, unsafe + b"\nchanged"),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.PATH_ESCAPE,
            lambda: validate_artifact_relationships(
                base,
                change_set,
                attached_core_paths=(),
            ),
            artifact_index=0,
            link_index=0,
        )


class ArtifactRelationshipWorkflowTests(RelationshipTestCase):
    def test_scenario_create_each_missing_fixed_core_identity(self) -> None:
        self.assertEqual(tuple(_CORE_DOMAINS), CORE_ARTIFACT_PATHS)
        for path, domain in _CORE_DOMAINS.items():
            with self.subTest(path=path):
                operation = _create(path, b"new core", kind=ArtifactKind.CORE_DOCUMENT)
                change_set = build_artifact_change_set({}, (operation,))
                validation = validate_artifact_relationships(
                    {},
                    change_set,
                    attached_core_paths=(),
                )
                self.assertTrue(validation.is_valid)
                self.assertEqual(
                    validation.review_facts,
                    (
                        ArtifactReviewFact(
                            domain,
                            ArtifactReviewReason.TOUCHED_CORE,
                            path,
                            0,
                        ),
                    ),
                )

    def test_scenario_patch_and_replace_each_present_core_identity(self) -> None:
        for path, domain in _CORE_DOMAINS.items():
            for operation_factory in (_patch, _replace):
                with self.subTest(path=path, operation=operation_factory.__name__):
                    base = {path: b"before"}
                    operation = operation_factory(
                        path,
                        b"before",
                        b"after",
                        kind=ArtifactKind.CORE_DOCUMENT,
                    )
                    change_set = build_artifact_change_set(base, (operation,))
                    validation = validate_artifact_relationships(
                        base,
                        change_set,
                        attached_core_paths=(),
                    )
                    self.assertTrue(validation.is_valid)
                    self.assertEqual(validation.review_facts[0].domain, domain)
                    self.assertIs(
                        validation.review_facts[0].reason,
                        ArtifactReviewReason.TOUCHED_CORE,
                    )

    def test_scenario_reject_every_fixed_core_rename_and_delete(self) -> None:
        for path in CORE_ARTIFACT_PATHS:
            alternatives = tuple(item for item in CORE_ARTIFACT_PATHS if item != path)
            for operation in (
                ArtifactRenameOperation(
                    path,
                    alternatives[0],
                    ArtifactKind.CORE_DOCUMENT,
                    _sha256(b"core"),
                    _sha256(b"core"),
                ),
                _delete(path, b"core", kind=ArtifactKind.CORE_DOCUMENT),
            ):
                with self.subTest(path=path, operation=type(operation).__name__):
                    base = {path: b"core"}
                    change_set = build_artifact_change_set(base, (operation,))
                    self.assert_relationship_error(
                        ArtifactRelationshipErrorCode.PROTECTED_CORE_OPERATION,
                        lambda: validate_artifact_relationships(
                            base,
                            change_set,
                            attached_core_paths=(),
                        ),
                        operation_index=0,
                    )
                    self.assertEqual(base, {path: b"core"})

    def test_scenario_broken_rename_returns_every_inbound_blocker(self) -> None:
        opening = "manuscript/part-01/opening.md"
        arrival = "manuscript/part-01/arrival.md"
        first = "manuscript/part-01/chapter-list.md"
        second = "manuscript/part-01/index.md"
        base = {
            first: b"[Opening](opening.md)",
            second: b"Before [Opening](opening.md) after",
            opening: b"chapter",
        }
        change_set = build_artifact_change_set(
            base,
            (_rename(opening, arrival, base[opening]),),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertFalse(validation.is_valid)
        self.assertEqual(len(validation.projected_dangling_edges), 2)
        self.assertEqual(
            tuple(edge.source_path for edge in validation.projected_dangling_edges),
            (first, second),
        )
        self.assertTrue(
            all(
                edge.target_path == opening
                for edge in validation.projected_dangling_edges
            )
        )
        self.assertEqual(validation.added_edges, ())
        self.assertEqual(validation.removed_edges, ())
        self.assertEqual(validation.retained_edges, validation.projected_dangling_edges)

    def test_scenario_atomic_rename_and_all_inbound_repairs_is_valid(self) -> None:
        opening = "manuscript/part-01/opening.md"
        arrival = "manuscript/part-01/arrival.md"
        chapter_list = "manuscript/part-01/chapter-list.md"
        index = "manuscript/part-01/index.md"
        before = b"[Opening](opening.md)"
        after = b"[Arrival](arrival.md)"
        base = {chapter_list: before, index: before, opening: b"chapter"}
        operations = (
            _patch(chapter_list, before, after),
            _patch(index, before, after),
            _rename(opening, arrival, b"chapter"),
        )
        change_set = build_artifact_change_set(base, operations)
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertTrue(validation.is_valid)
        self.assertEqual(len(validation.removed_edges), 2)
        self.assertEqual(len(validation.added_edges), 2)
        self.assertEqual(validation.retained_edges, ())
        self.assertEqual(
            {edge.target_path for edge in validation.removed_edges},
            {opening},
        )
        self.assertEqual(
            {edge.target_path for edge in validation.added_edges},
            {arrival},
        )

    def test_same_change_can_repair_an_existing_dangling_reference(self) -> None:
        missing = "manuscript/part-01/missing.md"
        before = b"[Missing](missing.md)"
        base = {_MANUSCRIPT: before}
        change_set = build_artifact_change_set(
            base,
            (
                _create(missing, b"restored"),
                _replace(_MANUSCRIPT, before, before + b"\nreconfirmed"),
            ),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.added_edges, ())
        self.assertEqual(validation.removed_edges, ())
        self.assertEqual(len(validation.retained_edges), 1)

    def test_renamed_manuscript_re_resolves_outbound_links_at_destination(self) -> None:
        source = "manuscript/part-01/scenes/source.md"
        destination = "manuscript/part-02/scenes/source.md"
        old_target = "manuscript/part-01/target.md"
        new_target = "manuscript/part-02/target.md"
        content = b"[Target](../target.md)"
        base = {source: content, old_target: b"old", new_target: b"new"}
        change_set = build_artifact_change_set(
            base,
            (_rename(source, destination, content),),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertTrue(validation.is_valid)
        self.assertEqual(
            tuple(
                (edge.source_path, edge.target_path)
                for edge in validation.removed_edges
            ),
            ((source, old_target),),
        )
        self.assertEqual(
            tuple(
                (edge.source_path, edge.target_path) for edge in validation.added_edges
            ),
            ((destination, new_target),),
        )

    def test_multi_inbound_delete_returns_every_projected_blocker(self) -> None:
        target = "manuscript/part-01/target.md"
        inbound = {
            f"manuscript/part-01/source-{index}.md": b"[Target](target.md)"
            for index in range(3)
        }
        base = {**inbound, target: b"target"}
        change_set = build_artifact_change_set(
            base,
            (_delete(target, b"target"),),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertFalse(validation.is_valid)
        self.assertEqual(len(validation.projected_dangling_edges), 3)
        self.assertEqual(
            tuple(edge.source_path for edge in validation.projected_dangling_edges),
            tuple(inbound),
        )

    def test_all_finite_cycles_and_self_cycle_are_allowed_without_recursion(
        self,
    ) -> None:
        first = "manuscript/part-01/a.md"
        second = "manuscript/part-01/b.md"
        third = "manuscript/part-01/c.md"
        operations = (
            _create(first, b"[B](b.md)"),
            _create(second, b"[C](c.md)"),
            _create(third, b"[A](a.md) [Self](c.md)"),
        )
        change_set = build_artifact_change_set({}, operations)
        validation = validate_artifact_relationships(
            {},
            change_set,
            attached_core_paths=(),
        )
        self.assertTrue(validation.is_valid)
        self.assertEqual(len(validation.added_edges), 4)
        self.assertEqual(validation.removed_edges, ())
        self.assertEqual(validation.retained_edges, ())
        self.assertEqual(ARTIFACT_RELATIONSHIP_COMPANION_RULES, ())

    def test_empty_companions_never_infer_edges_from_parts_names_or_prose(self) -> None:
        first = "manuscript/part-01/opening.md"
        second = "manuscript/part-01/opening-notes.md"
        operations = (
            _create(second, b"The global outline and opening are companions."),
            _create(
                first,
                b"Part 01 opening is near opening-notes.md. "
                b"SEARCH opening; ${core_documents/global_outline.md}; "
                b"$$manuscript/part-01/opening-notes.md",
            ),
        )
        change_set = build_artifact_change_set({}, operations)
        validation = validate_artifact_relationships(
            {},
            change_set,
            attached_core_paths=(),
        )
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.added_edges, ())
        self.assertEqual(validation.removed_edges, ())
        self.assertEqual(validation.retained_edges, ())
        self.assertEqual(validation.projected_dangling_edges, ())


class ArtifactReviewerFactTests(RelationshipTestCase):
    def test_every_manuscript_operation_requires_tone_execution(self) -> None:
        cases = (
            ({}, _create(_MANUSCRIPT, b"new"), _MANUSCRIPT),
            (
                {_MANUSCRIPT: b"before"},
                _patch(_MANUSCRIPT, b"before", b"after"),
                _MANUSCRIPT,
            ),
            (
                {_MANUSCRIPT: b"before"},
                _replace(_MANUSCRIPT, b"before", b"after"),
                _MANUSCRIPT,
            ),
            (
                {_MANUSCRIPT: b"before"},
                _rename(_MANUSCRIPT, "manuscript/part-01/arrival.md", b"before"),
                _MANUSCRIPT,
            ),
            (
                {_MANUSCRIPT: b"before"},
                _delete(_MANUSCRIPT, b"before"),
                _MANUSCRIPT,
            ),
        )
        for base, operation, expected_path in cases:
            with self.subTest(operation=type(operation).__name__):
                change_set = build_artifact_change_set(base, (operation,))
                validation = validate_artifact_relationships(
                    base,
                    change_set,
                    attached_core_paths=(),
                )
                self.assertIn(
                    ArtifactReviewFact(
                        ArtifactReviewDomain.TONE_EXECUTION,
                        ArtifactReviewReason.MANUSCRIPT_OPERATION,
                        expected_path,
                        0,
                    ),
                    validation.review_facts,
                )

    def test_attached_core_evidence_requires_all_mapped_domains_and_exact_input(
        self,
    ) -> None:
        attached = tuple(sorted(CORE_ARTIFACT_PATHS, key=lambda path: path.encode()))
        validation = _validation_for_replace(
            b"before",
            b"after",
            attached_core_paths=attached,
        )
        attached_facts = tuple(
            fact
            for fact in validation.review_facts
            if fact.reason is ArtifactReviewReason.ATTACHED_CORE_EVIDENCE
        )
        self.assertEqual(len(attached_facts), 4)
        self.assertEqual(
            {(fact.artifact_path, fact.domain) for fact in attached_facts},
            set(_CORE_DOMAINS.items()),
        )
        self.assertTrue(all(fact.operation_index is None for fact in attached_facts))
        invalid_values = (
            list(attached),
            tuple(reversed(attached)),
            (attached[0], attached[0]),
            (_MANUSCRIPT,),
            (1,),
        )
        base = {_MANUSCRIPT: b"before"}
        change_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, b"before", b"after"),),
        )
        for value in invalid_values:
            with self.subTest(value=value):
                self.assert_relationship_error(
                    ArtifactRelationshipErrorCode.INVALID_ATTACHED_CORE_PATHS,
                    lambda value=value: validate_artifact_relationships(
                        base,
                        change_set,
                        attached_core_paths=value,  # type: ignore[arg-type]
                    ),
                )

    def test_direct_base_and_projected_core_relationships_add_deduped_facts(
        self,
    ) -> None:
        character = "core_documents/character_sheets.md"
        rules = "core_documents/narrative_rules.md"
        before = (
            b"[Character](/core_documents/character_sheets.md)\n"
            b"[Character again](/core_documents/character_sheets.md)"
        )
        after = before + b"\n[Rules](/core_documents/narrative_rules.md)"
        base = {_MANUSCRIPT: before, character: b"characters", rules: b"rules"}
        change_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, before, after),),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        relationship_facts = tuple(
            fact
            for fact in validation.review_facts
            if fact.reason is ArtifactReviewReason.RELATIONSHIP_CORE_EVIDENCE
        )
        self.assertEqual(
            {
                (fact.domain, fact.artifact_path, fact.operation_index)
                for fact in relationship_facts
            },
            {
                (ArtifactReviewDomain.CHARACTER_WORLD, character, 0),
                (ArtifactReviewDomain.TONE_EXECUTION, rules, 0),
            },
        )

    def test_relationship_relevance_is_incident_only_not_transitive_or_nlp(
        self,
    ) -> None:
        changed = "manuscript/part-01/a.md"
        middle = "manuscript/part-01/b.md"
        core = "core_documents/character_sheets.md"
        before = b"[B](b.md) Character sheets should be consulted."
        after = before + b"\nchanged"
        base = {
            changed: before,
            middle: b"[Core](/core_documents/character_sheets.md)",
            core: b"characters",
        }
        change_set = build_artifact_change_set(
            base,
            (_replace(changed, before, after),),
        )
        validation = validate_artifact_relationships(
            base,
            change_set,
            attached_core_paths=(),
        )
        self.assertEqual(
            validation.review_facts,
            (
                ArtifactReviewFact(
                    ArtifactReviewDomain.TONE_EXECUTION,
                    ArtifactReviewReason.MANUSCRIPT_OPERATION,
                    changed,
                    0,
                ),
            ),
        )

    def test_review_facts_are_utf8_deterministic_across_mapping_insertion_order(
        self,
    ) -> None:
        rules = "core_documents/narrative_rules.md"
        before = b"[Rules](/core_documents/narrative_rules.md)"
        after = before + b"\nchanged"
        first_base = {_MANUSCRIPT: before, rules: b"rules"}
        second_base = {rules: b"rules", _MANUSCRIPT: before}
        operation = _replace(_MANUSCRIPT, before, after)
        first_set = build_artifact_change_set(first_base, (operation,))
        second_set = build_artifact_change_set(second_base, (operation,))
        first = validate_artifact_relationships(
            first_base,
            first_set,
            attached_core_paths=(),
        )
        second = validate_artifact_relationships(
            second_base,
            second_set,
            attached_core_paths=(),
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first.review_facts, tuple(sorted(first.review_facts, key=repr))
        )


class ArtifactRelationshipBoundTests(RelationshipTestCase):
    def test_artifact_count_accepts_exact_n_and_rejects_base_and_projected_n_plus_one(
        self,
    ) -> None:
        base = {
            f"manuscript/part-01/item-{index:04d}.md": b"x"
            for index in range(MAX_RELATIONSHIP_ARTIFACTS)
        }
        first = next(iter(base))
        exact_set = build_artifact_change_set(
            base,
            (_replace(first, b"x", b"y"),),
        )
        self.assertTrue(
            validate_artifact_relationships(
                base,
                exact_set,
                attached_core_paths=(),
            ).is_valid
        )
        overflow_base = {
            **base,
            "manuscript/part-01/overflow.md": b"x",
        }
        overflow_set = build_artifact_change_set(
            overflow_base,
            (_replace(first, b"x", b"y"),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.ARTIFACT_COUNT_LIMIT,
            lambda: validate_artifact_relationships(
                overflow_base,
                overflow_set,
                attached_core_paths=(),
            ),
        )
        projected_set = build_artifact_change_set(
            base,
            (_create("manuscript/part-01/projected-overflow.md", b"x"),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.ARTIFACT_COUNT_LIMIT,
            lambda: validate_artifact_relationships(
                base,
                projected_set,
                attached_core_paths=(),
            ),
        )

    def test_per_artifact_size_accepts_exact_n_and_rejects_each_n_plus_one_phase(
        self,
    ) -> None:
        exact = b"a" * MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES
        changed = b"b" + exact[1:]
        base = {_MANUSCRIPT: exact}
        exact_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, exact, changed),),
        )
        self.assertTrue(
            validate_artifact_relationships(
                base,
                exact_set,
                attached_core_paths=(),
            ).is_valid
        )
        overflow = exact + b"x"
        overflow_base = {_MANUSCRIPT: overflow}
        overflow_base_set = build_artifact_change_set(
            overflow_base,
            (_replace(_MANUSCRIPT, overflow, b"b" + overflow[1:]),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.ARTIFACT_SIZE_LIMIT,
            lambda: validate_artifact_relationships(
                overflow_base,
                overflow_base_set,
                attached_core_paths=(),
            ),
            artifact_index=0,
        )
        projected_overflow_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, exact, overflow),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.ARTIFACT_SIZE_LIMIT,
            lambda: validate_artifact_relationships(
                base,
                projected_overflow_set,
                attached_core_paths=(),
            ),
            artifact_index=0,
        )

    def test_total_size_accepts_exact_n_and_rejects_projected_n_plus_one(self) -> None:
        chunk = b"a" * MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES
        base = {
            f"manuscript/part-{index + 1:02d}/chunk.md": chunk
            for index in range(
                MAX_RELATIONSHIP_STATE_UTF8_BYTES
                // MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES
            )
        }
        first = next(iter(base))
        exact_set = build_artifact_change_set(
            base,
            (_replace(first, chunk, b"b" + chunk[1:]),),
        )
        self.assertTrue(
            validate_artifact_relationships(
                base,
                exact_set,
                attached_core_paths=(),
            ).is_valid
        )
        overflow_base = {
            **base,
            "manuscript/part-09/base-overflow.md": b"x",
        }
        overflow_base_set = build_artifact_change_set(
            overflow_base,
            (
                _replace(
                    "manuscript/part-09/base-overflow.md",
                    b"x",
                    b"y",
                ),
            ),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.STATE_SIZE_LIMIT,
            lambda: validate_artifact_relationships(
                overflow_base,
                overflow_base_set,
                attached_core_paths=(),
            ),
            artifact_index=len(overflow_base) - 1,
        )
        small_path = "manuscript/part-09/extra.md"
        smaller = dict(base)
        smaller[first] = chunk[:-1]
        smaller[small_path] = b"x"
        projected_overflow = build_artifact_change_set(
            smaller,
            (_replace(small_path, b"x", b"xy"),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.STATE_SIZE_LIMIT,
            lambda: validate_artifact_relationships(
                smaller,
                projected_overflow,
                attached_core_paths=(),
            ),
            artifact_index=len(smaller) - 1,
        )

    def test_edge_limit_accepts_exact_n_and_rejects_n_plus_one_without_partial_result(
        self,
    ) -> None:
        target = "manuscript/part-01/target.md"
        marker = b"[T](target.md)\n"
        exact = marker * MAX_RELATIONSHIP_EDGES
        base = {_MANUSCRIPT: exact, target: b"target"}
        exact_set = build_artifact_change_set(
            base,
            (_replace(target, b"target", b"target revised"),),
        )
        validation = validate_artifact_relationships(
            base,
            exact_set,
            attached_core_paths=(),
        )
        self.assertTrue(validation.is_valid)
        self.assertEqual(len(validation.retained_edges), MAX_RELATIONSHIP_EDGES)
        overflow = exact + marker
        overflow_base = {_MANUSCRIPT: overflow, target: b"target"}
        overflow_set = build_artifact_change_set(
            overflow_base,
            (_replace(target, b"target", b"target revised"),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT,
            lambda: validate_artifact_relationships(
                overflow_base,
                overflow_set,
                attached_core_paths=(),
            ),
            artifact_index=0,
            link_index=MAX_RELATIONSHIP_EDGES,
        )
        projected_overflow_set = build_artifact_change_set(
            {target: b"target"},
            (_create(_MANUSCRIPT, overflow),),
        )
        self.assert_relationship_error(
            ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT,
            lambda: validate_artifact_relationships(
                {target: b"target"},
                projected_overflow_set,
                attached_core_paths=(),
            ),
            artifact_index=0,
            link_index=MAX_RELATIONSHIP_EDGES,
        )

    def test_patched_small_bounds_prove_preflight_and_projected_phase_boundaries(
        self,
    ) -> None:
        base = {_MANUSCRIPT: b"[I](index.md)", _SECOND_MANUSCRIPT: b"index"}
        change_set = build_artifact_change_set(
            base,
            (_replace(_SECOND_MANUSCRIPT, b"index", b"changed"),),
        )
        with patch.object(relationship_module, "MAX_RELATIONSHIP_EDGES", 0):
            self.assert_relationship_error(
                ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT,
                lambda: validate_artifact_relationships(
                    base,
                    change_set,
                    attached_core_paths=(),
                ),
                artifact_index=1,
                link_index=0,
            )
        create_set = build_artifact_change_set(
            {},
            (_create(_MANUSCRIPT, b"[Missing](missing.md)"),),
        )
        with patch.object(relationship_module, "MAX_RELATIONSHIP_EDGES", 0):
            self.assert_relationship_error(
                ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT,
                lambda: validate_artifact_relationships(
                    {},
                    create_set,
                    attached_core_paths=(),
                ),
                artifact_index=0,
                link_index=0,
            )


class ArtifactRelationshipRobustnessTests(RelationshipTestCase):
    def test_inputs_are_immutable_and_validation_has_no_filesystem_or_log_side_effect(
        self,
    ) -> None:
        before = b"[Index](index.md)"
        base = {_MANUSCRIPT: before, _SECOND_MANUSCRIPT: b"index"}
        change_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, before, before + b"\nchanged"),),
        )
        base_snapshot = dict(base)
        operations_snapshot = tuple(change_set.operations)
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sentinel = root / "core_documents" / "global_outline.md"
                sentinel.parent.mkdir()
                sentinel.write_bytes(b"filesystem sentinel")
                before_files = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                previous_cwd = Path.cwd()
                previous_environment = dict(os.environ)
                validation = validate_artifact_relationships(
                    MappingProxyType(base),
                    change_set,
                    attached_core_paths=(),
                )
                after_files = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
        finally:
            root_logger.removeHandler(handler)
        self.assertTrue(validation.is_valid)
        self.assertEqual(base, base_snapshot)
        self.assertEqual(change_set.operations, operations_snapshot)
        self.assertEqual(after_files, before_files)
        self.assertEqual(Path.cwd(), previous_cwd)
        self.assertEqual(dict(os.environ), previous_environment)
        self.assertEqual(records, [])

    def test_malformed_mappings_and_values_fail_content_free(self) -> None:
        secret = "SECRET_MAPPING_STORY_PROSE"
        simple_set = build_artifact_change_set(
            {},
            (_create(_MANUSCRIPT, b"created"),),
        )
        malformed = (
            [],
            {_MANUSCRIPT: "not bytes"},
            {_MANUSCRIPT: b"\xff"},
            {"agent_states/secret.md": secret.encode()},
            _ExplodingMapping(secret),
        )
        for value in malformed:
            with self.subTest(value_type=type(value).__name__):
                error = self.assert_relationship_error(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA,
                    lambda value=value: validate_artifact_relationships(
                        value,  # type: ignore[arg-type]
                        simple_set,
                        attached_core_paths=(),
                    ),
                )
                self.assert_exception_graph_excludes(error, (secret, "not bytes"))

    def test_errors_and_public_values_do_not_retain_story_bytes_labels_or_prose(
        self,
    ) -> None:
        label = "SECRET_LABEL_719"
        prose = "SECRET_SURROUNDING_PROSE_719"
        target = "manuscript/part-01/missing.md"
        content = f"{prose} [{label}](missing.md#SECRET_FRAGMENT_719)".encode()
        validation = _validation_for_replace(content, content + b"\nchanged")
        rendered = repr(validation)
        for secret in (label, prose, "SECRET_FRAGMENT_719"):
            self.assertNotIn(secret, rendered)
        error = self.assert_relationship_error(
            ArtifactRelationshipErrorCode.INVALID_SCHEMA,
            lambda: ArtifactRelationshipEdge(
                _MANUSCRIPT,
                target,
                0,
                0,
                "SECRET_FRAGMENT_719",
                ArtifactRelationshipKind.INLINE_MARKDOWN_LINK,
            ),
        )
        self.assert_exception_graph_excludes(
            error,
            (label, prose, "SECRET_FRAGMENT_719", content.decode()),
        )

    def test_corrupted_frozen_public_values_fail_typed_and_content_free(self) -> None:
        secret = "SECRET_CORRUPTED_VALUE"
        edge = _edge()
        fact = _fact()
        validation = ArtifactRelationshipValidation(
            1,
            "0" * 64,
            "1" * 64,
            "2" * 64,
            (edge,),
            (),
            (),
            (),
            (fact,),
        )
        corruptions = (
            (edge, "source_path", secret),
            (fact, "domain", secret),
            (validation, "added_edges", (secret,)),
        )
        for value, attribute, corrupted in corruptions:
            with self.subTest(value=type(value).__name__, attribute=attribute):
                original = getattr(value, attribute)
                object.__setattr__(value, attribute, corrupted)
                error = self.assert_relationship_error(
                    ArtifactRelationshipErrorCode.INVALID_SCHEMA,
                    value.__post_init__,
                )
                self.assert_exception_graph_excludes(error, (secret,))
                object.__setattr__(value, attribute, original)

    def test_change_set_failures_propagate_without_relationship_wrapping(self) -> None:
        base = {_MANUSCRIPT: b"before"}
        change_set = build_artifact_change_set(
            base,
            (_replace(_MANUSCRIPT, b"before", b"after"),),
        )
        with self.assertRaises(ArtifactChangeSetError) as raised:
            validate_artifact_relationships(
                {_MANUSCRIPT: b"different"},
                change_set,
                attached_core_paths=(),
            )
        self.assertIs(
            raised.exception.code,
            ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH,
        )
        object.__setattr__(change_set, "operations", ())
        with self.assertRaises(ArtifactChangeSetError) as corrupted:
            validate_artifact_relationships(
                base,
                change_set,
                attached_core_paths=(),
            )
        self.assertIs(corrupted.exception.code, ArtifactChangeSetErrorCode.NO_OP)


class ArtifactRelationshipBoundaryTests(RelationshipTestCase):
    def test_module_imports_only_stdlib_catalog_and_change_sets(self) -> None:
        source_path = Path(relationship_module.__file__)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=os.fspath(source_path),
        )
        relative_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        }
        absolute_roots = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and not node.level
        }
        self.assertEqual(relative_imports, {"catalog", "change_sets", "specialist_routing"})
        self.assertEqual(
            absolute_roots,
            {"__future__", "collections", "dataclasses", "enum", "re"},
        )
        forbidden = {
            "agents",
            "backend",
            "config",
            "conversation_retrieval",
            "orchestrator",
            "provider_adapter",
            "repository",
            "runtime_references",
            "workspace",
        }
        self.assertTrue(relative_imports.isdisjoint(forbidden))

    def test_public_fields_expose_no_agent_runtime_authority_or_git_contract(
        self,
    ) -> None:
        forbidden_fragments = (
            "agent",
            "authority",
            "authorization",
            "capability",
            "commit",
            "confirmation",
            "filesystem",
            "git",
            "provider",
            "repository",
            "runtime",
            "workspace",
        )
        for value_type in (
            ArtifactRelationshipEdge,
            ArtifactReviewFact,
            ArtifactRelationshipValidation,
        ):
            names = tuple(field.name.lower() for field in fields(value_type))
            for fragment in forbidden_fragments:
                self.assertFalse(
                    any(fragment in name for name in names),
                    (value_type.__name__, fragment, names),
                )

    def test_clean_direct_and_facade_imports_need_no_optional_collaborator(
        self,
    ) -> None:
        script = """
import sys
sys.path.insert(0, sys.argv[1])
for name in (
    'generic_rag', 'jsonl_viewer', 'openai', 'anthropic',
):
    sys.modules[name] = None
import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import relationship_policy
assert writing_artifacts.ArtifactRelationshipEdge is relationship_policy.ArtifactRelationshipEdge
assert not hasattr(story_writing_agents, 'ArtifactRelationshipEdge')
assert tuple(relationship_policy.__all__) == tuple(sys.argv[2].split(','))
"""
        completed = subprocess.run(
            (
                sys.executable,
                "-I",
                "-c",
                script,
                os.fspath(_SOURCE_ROOT.parent),
                ",".join(_MODULE_EXPORTS),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_ast_graph_counts_cycles_and_module_index_match_accepted_brief(
        self,
    ) -> None:
        modules: dict[str, Path] = {}
        for path in _SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(_SOURCE_ROOT)
            parts = (
                ("story_writing_agents", *relative.parts[:-1])
                if relative.name == "__init__.py"
                else (
                    "story_writing_agents",
                    *relative.with_suffix("").parts,
                )
            )
            modules[".".join(parts)] = path

        def resolve(target: str) -> str | None:
            parts = target.split(".")
            while parts and ".".join(parts) not in modules:
                parts.pop()
            candidate = ".".join(parts)
            return candidate if candidate in modules else None

        def runtime_nodes(node):
            yield node
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                for child in node.orelse:
                    yield from runtime_nodes(child)
            else:
                for child in ast.iter_child_nodes(node):
                    yield from runtime_nodes(child)

        edges: set[tuple[str, str]] = set()
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            context = (
                module.split(".")
                if path.name == "__init__.py"
                else module.split(".")[:-1]
            )
            for node in runtime_nodes(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = context[: len(context) - (node.level - 1)]
                        if node.module:
                            base.extend(node.module.split("."))
                        target = ".".join(base)
                    else:
                        target = node.module or ""
                    targets.append(target)
                    targets.extend(
                        f"{target}.{alias.name}" if target else alias.name
                        for alias in node.names
                    )
                for target in targets:
                    resolved = resolve(target)
                    if resolved is not None and resolved != module:
                        edges.add((module, resolved))
        self.assertEqual(len(modules), 143)
        self.assertEqual(len(edges), 576)
        relationship_name = "story_writing_agents.writing_artifacts.relationship_policy"
        self.assertEqual(
            {target for source, target in edges if source == relationship_name},
            {
                "story_writing_agents.writing_artifacts.catalog",
                "story_writing_agents.writing_artifacts.change_sets",
                "story_writing_agents.specialist_routing",
            },
        )
        self.assertIn(
            ("story_writing_agents.writing_artifacts", relationship_name),
            edges,
        )

        successors = {module: set() for module in modules}
        for source, target in edges:
            successors[source].add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"internal import cycle reaches {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in successors[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in modules:
            visit(module)
        index = (_ROOT / "PYTHON_MODULE_INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Importable production units indexed: 143.", index)
        self.assertIn("Direct internal dependency edges indexed: 576.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        self.assertIn(
            "### `story_writing_agents.writing_artifacts.relationship_policy`",
            index,
        )


if __name__ == "__main__":
    unittest.main()
