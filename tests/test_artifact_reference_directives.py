"""Pure contracts for canonical writing-artifact reference directives."""

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from unittest import mock

import story_writing_agents
from story_writing_agents import writing_artifacts as artifact_facade
from story_writing_agents.writing_artifacts import (
    MAX_REFERENCE_COUNT,
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
    ArtifactReferenceDirectiveError,
    parse_artifact_references,
)
from story_writing_agents.writing_artifacts import directives as directive_module
from story_writing_agents.writing_artifacts import references as reference_module


_CORE_PATH = "core_documents/global_outline.md"
_SECOND_CORE_PATH = "core_documents/narrative_rules.md"
_MANUSCRIPT_PATH = "manuscript/part-01/chapter-01.md"
_SECOND_MANUSCRIPT_PATH = "manuscript/part-02/scenes/opening.md"


class _TextSubclass(str):
    """Prove parser and value schemas require exact built-in strings."""


def _token(path: str, suffix: str = "") -> str:
    return f"${{{path}}}{suffix}"


def _unique_references(count: int) -> tuple[ArtifactReference, ...]:
    return tuple(
        ArtifactReference(f"manuscript/part-01/chapter-{index:02d}.md")
        for index in range(1, count + 1)
    )


class ArtifactReferenceDirectiveParsingTests(unittest.TestCase):
    def test_path_range_order_adjacency_and_exact_text_are_preserved(self) -> None:
        text = (
            f"Use {_token(_CORE_PATH)}, then "
            f"({_token(_MANUSCRIPT_PATH, '#L7-L9')}). "
            f"Adjacent: {_token(_SECOND_MANUSCRIPT_PATH)}"
            f"{_token(_SECOND_CORE_PATH)}!"
        )

        batch = parse_artifact_references(text)

        self.assertEqual(batch.source_text, text)
        self.assertEqual(batch.provider_text, text)
        self.assertEqual(
            batch.references,
            (
                ArtifactReference(_CORE_PATH),
                ArtifactReference(
                    _MANUSCRIPT_PATH,
                    ArtifactLineRange(7, 9),
                ),
                ArtifactReference(_SECOND_MANUSCRIPT_PATH),
                ArtifactReference(_SECOND_CORE_PATH),
            ),
        )
        self.assertEqual(
            tuple(reference.token for reference in batch.references),
            (
                _token(_CORE_PATH),
                _token(_MANUSCRIPT_PATH, "#L7-L9"),
                _token(_SECOND_MANUSCRIPT_PATH),
                _token(_SECOND_CORE_PATH),
            ),
        )

    def test_escape_parity_projects_provider_text_without_changing_source(self) -> None:
        expected = (
            (0, _token(_CORE_PATH), (_token(_CORE_PATH),)),
            (1, _token(_CORE_PATH), ()),
            (2, "\\\\" + _token(_CORE_PATH), (_token(_CORE_PATH),)),
            (3, "\\\\" + _token(_CORE_PATH), ()),
            (4, "\\\\\\\\" + _token(_CORE_PATH), (_token(_CORE_PATH),)),
        )
        for backslash_count, provider_text, expected_tokens in expected:
            with self.subTest(backslash_count=backslash_count):
                source = "\\" * backslash_count + _token(_CORE_PATH)
                batch = parse_artifact_references(source)
                self.assertEqual(batch.source_text, source)
                self.assertEqual(batch.provider_text, provider_text)
                self.assertEqual(
                    tuple(reference.token for reference in batch.references),
                    expected_tokens,
                )

    def test_bare_dollars_escaped_directives_and_no_references_are_literal(self) -> None:
        plain = "Price $5; trailing $; no writing artifact here."
        plain_batch = parse_artifact_references(plain)
        self.assertEqual(
            plain_batch,
            ArtifactReferenceBatch(plain, plain, ()),
        )

        source = "\\" + _token(_MANUSCRIPT_PATH, "#L1-L2")
        escaped = parse_artifact_references(source)
        self.assertEqual(escaped.source_text, source)
        self.assertEqual(
            escaped.provider_text,
            _token(_MANUSCRIPT_PATH, "#L1-L2"),
        )
        self.assertEqual(escaped.references, ())

    def test_reserved_and_unescaped_malformed_dollar_syntax_is_exact(self) -> None:
        malformed = (
            ("$$", "unresolved dollar-dollar syntax is reserved"),
            (
                "$" + _token(_CORE_PATH),
                "unresolved dollar-dollar syntax is reserved",
            ),
            ("${", "reference directive is malformed"),
            (
                "prefix ${manuscript/part-01/chapter.md",
                "reference directive is malformed",
            ),
        )
        for text, message in malformed:
            with self.subTest(text=text):
                with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
                    parse_artifact_references(text)
                self.assertEqual(str(raised.exception), message)

    def test_range_trailing_punctuation_and_adjacent_token_are_unambiguous(self) -> None:
        punctuation = (".", ",", ")", ";", ":", "!", "?")
        for suffix in punctuation:
            with self.subTest(suffix=suffix):
                text = _token(_MANUSCRIPT_PATH, "#L1-L2") + suffix
                batch = parse_artifact_references(text)
                self.assertEqual(batch.source_text, text)
                self.assertEqual(batch.provider_text, text)
                self.assertEqual(
                    batch.references,
                    (
                        ArtifactReference(
                            _MANUSCRIPT_PATH,
                            ArtifactLineRange(1, 2),
                        ),
                    ),
                )

        adjacent = (
            _token(_MANUSCRIPT_PATH, "#L1-L2")
            + _token(_SECOND_CORE_PATH)
        )
        self.assertEqual(
            parse_artifact_references(adjacent).references,
            (
                ArtifactReference(
                    _MANUSCRIPT_PATH,
                    ArtifactLineRange(1, 2),
                ),
                ArtifactReference(_SECOND_CORE_PATH),
            ),
        )

    def test_source_only_paths_spaces_unicode_and_delimiters_preserve_order(
        self,
    ) -> None:
        paths = (
            "manuscript/root.md",
            "manuscript/older drafts/世界 章.md",
            "manuscript/{archive$/scene#1.md",
        )
        text = (
            f"Use {_token(paths[0])}, then {_token(paths[1], '#L2-L4')} "
            f"and {_token(paths[2])}."
        )

        batch = parse_artifact_references(text)

        self.assertEqual(batch.source_text, text)
        self.assertEqual(batch.provider_text, text)
        self.assertEqual(
            batch.references,
            (
                ArtifactReference(paths[0]),
                ArtifactReference(paths[1], ArtifactLineRange(2, 4)),
                ArtifactReference(paths[2]),
            ),
        )

    def test_range_word_and_additional_range_continuations_reject(self) -> None:
        suffixes = (
            "x",
            "é",
            "_next",
            "-L3",
            "-l3",
            "#L3-L4",
            "#l3-L4",
        )
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                text = _token(_MANUSCRIPT_PATH, "#L1-L2") + suffix
                with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
                    parse_artifact_references(text)
                self.assertEqual(
                    str(raised.exception),
                    "reference range is malformed",
                )

        non_range_suffix = _token(_MANUSCRIPT_PATH) + "#Q1"
        self.assertEqual(
            parse_artifact_references(non_range_suffix).references,
            (ArtifactReference(_MANUSCRIPT_PATH),),
        )


class ArtifactReferenceDirectiveValidationTests(unittest.TestCase):
    def test_noncanonical_internal_and_unsafe_paths_have_one_safe_error(self) -> None:
        overlong_path = (
            "manuscript/part-01/"
            + "/".join("a" * 120 for _ in range(9))
            + "/chapter.md"
        )
        paths = (
            "discussion_logs/private.md",
            "agent_states/orchestrator_state/state.md",
            "core_documents/extra.md",
            "manuscript/part-01/chapter.txt",
            "manuscript/part-01/chapter.MD",
            "manuscript/closing}brace.md",
            "manuscript/control\tname.md",
            "manuscript/format\u200dname.md",
            "manuscript/line\u2028separator.md",
            "manuscript/paragraph\u2029separator.md",
            "manuscript/part-01/../chapter.md",
            "/manuscript/part-01/chapter.md",
            "manuscript\\part-01\\chapter.md",
            "manuscript/part-01//chapter.md",
            overlong_path,
        )
        for path in paths:
            with self.subTest(path=path[:80]):
                with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
                    parse_artifact_references(_token(path))
                self.assertEqual(
                    str(raised.exception),
                    "reference path is not an eligible writing-artifact source",
                )
                self.assertNotIn(path, str(raised.exception))

    def test_empty_path_has_one_safe_error(self) -> None:
        with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
            parse_artifact_references(_token(""))
        self.assertEqual(str(raised.exception), "reference path must be nonempty")

    def test_positive_uppercase_ordered_range_grammar_is_exact(self) -> None:
        text = (
            _token(_MANUSCRIPT_PATH, "#L1-L1")
            + " "
            + _token(
                _SECOND_MANUSCRIPT_PATH,
                f"#L{sys.maxsize}-L{sys.maxsize}",
            )
        )
        batch = parse_artifact_references(text)
        self.assertEqual(
            batch.references,
            (
                ArtifactReference(
                    _MANUSCRIPT_PATH,
                    ArtifactLineRange(1, 1),
                ),
                ArtifactReference(
                    _SECOND_MANUSCRIPT_PATH,
                    ArtifactLineRange(sys.maxsize, sys.maxsize),
                ),
            ),
        )

        enormous = "9" * (len(str(sys.maxsize)) + 1)
        malformed = (
            ("#l1-L2", "reference ranges require an uppercase L marker"),
            ("#l1-l2", "reference ranges require an uppercase L marker"),
            ("#L1-l2", "reference range is malformed"),
            ("#L0-L1", "reference range is malformed"),
            ("#L01-L2", "reference range is malformed"),
            ("#L1-L02", "reference range is malformed"),
            ("#L1", "reference range is malformed"),
            ("#L-L2", "reference range is malformed"),
            ("#L3-L2", "reference range must be ordered"),
            (
                f"#L{enormous}-L{enormous}",
                "reference line bound is too large",
            ),
        )
        for suffix, message in malformed:
            with self.subTest(suffix=suffix):
                with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
                    parse_artifact_references(_token(_MANUSCRIPT_PATH, suffix))
                self.assertEqual(str(raised.exception), message)

    def test_duplicate_whole_range_and_overlap_conflicts_are_order_independent(
        self,
    ) -> None:
        whole = _token(_MANUSCRIPT_PATH)
        first = _token(_MANUSCRIPT_PATH, "#L2-L5")
        overlap = _token(_MANUSCRIPT_PATH, "#L4-L6")
        cases = (
            (
                whole + whole,
                "reference input contains an exact duplicate",
            ),
            (
                first + first,
                "reference input contains an exact duplicate",
            ),
            (
                whole + first,
                "reference input mixes whole and ranged directives",
            ),
            (
                first + whole,
                "reference input mixes whole and ranged directives",
            ),
            (
                first + overlap,
                "reference input contains overlapping ranges",
            ),
            (
                overlap + first,
                "reference input contains overlapping ranges",
            ),
        )
        for text, message in cases:
            with self.subTest(message=message, text=text):
                with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
                    parse_artifact_references(text)
                self.assertEqual(str(raised.exception), message)

    def test_adjacent_disjoint_unsorted_ranges_are_accepted_in_source_order(
        self,
    ) -> None:
        ranges = (
            ArtifactLineRange(9, 10),
            ArtifactLineRange(1, 2),
            ArtifactLineRange(3, 8),
        )
        text = "".join(
            _token(
                _MANUSCRIPT_PATH,
                f"#L{line_range.start}-L{line_range.end}",
            )
            for line_range in ranges
        )
        batch = parse_artifact_references(text)
        self.assertEqual(
            batch.references,
            tuple(
                ArtifactReference(_MANUSCRIPT_PATH, line_range)
                for line_range in ranges
            ),
        )

    def test_sixteen_occurrences_are_accepted_and_seventeen_rejects(self) -> None:
        references = _unique_references(MAX_REFERENCE_COUNT + 1)
        accepted_text = "".join(
            reference.token for reference in references[:MAX_REFERENCE_COUNT]
        )
        accepted = parse_artifact_references(accepted_text)
        self.assertEqual(accepted.references, references[:MAX_REFERENCE_COUNT])

        rejected_text = accepted_text + references[MAX_REFERENCE_COUNT].token
        with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
            parse_artifact_references(rejected_text)
        self.assertEqual(
            str(raised.exception),
            "reference input exceeds the directive limit",
        )


class ArtifactReferenceValueContractTests(unittest.TestCase):
    def test_values_are_exact_frozen_slotted_hash_safe_and_content_free(self) -> None:
        line_range = ArtifactLineRange(2, 4)
        reference = ArtifactReference(_MANUSCRIPT_PATH, line_range)
        batch = ArtifactReferenceBatch(
            source_text=reference.token,
            provider_text=reference.token,
            references=(reference,),
        )

        self.assertEqual(
            tuple(field.name for field in fields(ArtifactLineRange)),
            ("start", "end"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ArtifactReference)),
            ("path", "line_range"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ArtifactReferenceBatch)),
            ("source_text", "provider_text", "references"),
        )
        self.assertFalse(hasattr(reference, "content"))
        self.assertFalse(hasattr(batch, "content"))
        self.assertEqual(len({line_range, reference, batch}), 3)
        for value, name in (
            (line_range, "start"),
            (reference, "path"),
            (batch, "source_text"),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, name, "changed")
                self.assertFalse(hasattr(value, "__dict__"))

    def test_direct_construction_rejects_invalid_schema_and_relations(self) -> None:
        reference = ArtifactReference(_MANUSCRIPT_PATH)
        another = ArtifactReference(_SECOND_MANUSCRIPT_PATH)
        for start, end in ((True, 1), (1, False), (1.0, 2), (1, "2")):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(
                    TypeError,
                    "line range bounds must be integers",
                ):
                    ArtifactLineRange(start, end)  # type: ignore[arg-type]
        with self.assertRaisesRegex(
            ValueError,
            "line range must be one-based, inclusive, and ordered",
        ):
            ArtifactLineRange(3, 2)
        with self.assertRaisesRegex(
            ValueError,
            "reference path is not an eligible writing-artifact source",
        ):
            ArtifactReference("discussion_logs/private.md")
        with self.assertRaisesRegex(
            TypeError,
            "line_range must be an ArtifactLineRange or None",
        ):
            ArtifactReference(_MANUSCRIPT_PATH, (1, 2))  # type: ignore[arg-type]

        with self.assertRaisesRegex(TypeError, "references must be a tuple"):
            ArtifactReferenceBatch("x", "x", [reference])  # type: ignore[arg-type]
        with self.assertRaisesRegex(
            TypeError,
            "references must contain ArtifactReference values",
        ):
            ArtifactReferenceBatch("x", "x", (object(),))  # type: ignore[arg-type]
        with self.assertRaisesRegex(
            ArtifactReferenceDirectiveError,
            "reference input exceeds the directive limit",
        ):
            ArtifactReferenceBatch(
                "x",
                "x",
                _unique_references(MAX_REFERENCE_COUNT + 1),
            )
        with self.assertRaisesRegex(
            ArtifactReferenceDirectiveError,
            "reference input contains an exact duplicate",
        ):
            ArtifactReferenceBatch("x", "x", (reference, reference))
        with self.assertRaisesRegex(
            ArtifactReferenceDirectiveError,
            "reference input mixes whole and ranged directives",
        ):
            ArtifactReferenceBatch(
                "x",
                "x",
                (
                    reference,
                    ArtifactReference(
                        _MANUSCRIPT_PATH,
                        ArtifactLineRange(1, 2),
                    ),
                ),
            )
        with self.assertRaisesRegex(
            ArtifactReferenceDirectiveError,
            "reference input contains overlapping ranges",
        ):
            ArtifactReferenceBatch(
                "x",
                "x",
                (
                    ArtifactReference(
                        _MANUSCRIPT_PATH,
                        ArtifactLineRange(1, 3),
                    ),
                    ArtifactReference(
                        _MANUSCRIPT_PATH,
                        ArtifactLineRange(3, 5),
                    ),
                ),
            )
        self.assertEqual(
            ArtifactReferenceBatch("x", "x", (reference, another)).references,
            (reference, another),
        )

    def test_input_and_batch_text_require_exact_strict_utf8_without_nul(self) -> None:
        for value in (None, 1, b"text", _TextSubclass("text")):
            with self.subTest(parser_type=type(value).__name__):
                with self.assertRaisesRegex(
                    TypeError,
                    "reference input must be a string",
                ):
                    parse_artifact_references(value)  # type: ignore[arg-type]

        for value, message in (
            ("contains\x00nul", "reference input must not contain NUL"),
            ("lone\ud800surrogate", "reference input must be strict UTF-8"),
        ):
            with self.subTest(message=message):
                with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
                    parse_artifact_references(value)
                self.assertEqual(str(raised.exception), message)

        reference = ArtifactReference(_CORE_PATH)
        for field_name in ("source_text", "provider_text"):
            for value in (None, _TextSubclass("text")):
                with self.subTest(field_name=field_name, value=type(value).__name__):
                    values = {
                        "source_text": "text",
                        "provider_text": "text",
                        "references": (reference,),
                    }
                    values[field_name] = value
                    with self.assertRaisesRegex(
                        TypeError,
                        f"{field_name} must be a string",
                    ):
                        ArtifactReferenceBatch(**values)  # type: ignore[arg-type]
            for value, message in (
                ("contains\x00nul", "reference input must not contain NUL"),
                ("lone\ud800surrogate", "reference input must be strict UTF-8"),
            ):
                with self.subTest(field_name=field_name, message=message):
                    values = {
                        "source_text": "text",
                        "provider_text": "text",
                        "references": (reference,),
                    }
                    values[field_name] = value
                    with self.assertRaises(
                        ArtifactReferenceDirectiveError,
                    ) as raised:
                        ArtifactReferenceBatch(**values)  # type: ignore[arg-type]
                    self.assertEqual(str(raised.exception), message)

    def test_error_type_is_value_error_with_content_free_messages(self) -> None:
        secret = "internal-secret-that-must-not-echo.md"
        with self.assertRaises(ArtifactReferenceDirectiveError) as raised:
            parse_artifact_references(_token(secret))
        self.assertIsInstance(raised.exception, ValueError)
        self.assertEqual(
            str(raised.exception),
            "reference path is not an eligible writing-artifact source",
        )
        self.assertNotIn(secret, str(raised.exception))


class ArtifactReferenceFacadeTests(unittest.TestCase):
    def test_package_facade_exports_canonical_objects_and_root_is_unchanged(
        self,
    ) -> None:
        expected = {
            "ArtifactReferenceBatch": reference_module.ArtifactReferenceBatch,
            "ArtifactReferenceDirectiveError": (
                reference_module.ArtifactReferenceDirectiveError
            ),
            "parse_artifact_references": (
                directive_module.parse_artifact_references
            ),
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertIn(name, artifact_facade.__all__)
                self.assertIs(getattr(artifact_facade, name), value)
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))
        self.assertEqual(len(story_writing_agents.__all__), 120)

    def test_parser_is_provider_free_and_uses_no_operational_io(self) -> None:
        failure = AssertionError("directive parsing must not open files")
        with mock.patch("builtins.open", side_effect=failure) as opened:
            batch = parse_artifact_references(_token(_CORE_PATH))
        opened.assert_not_called()
        self.assertEqual(batch.references, (ArtifactReference(_CORE_PATH),))
        for forbidden in (
            "workspace",
            "repository",
            "backend",
            "provider",
            "controller",
            "interactive_terminal",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, directive_module.__dict__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
