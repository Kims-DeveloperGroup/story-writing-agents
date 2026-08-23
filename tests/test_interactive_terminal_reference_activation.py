"""Pure contracts for interactive writing-artifact reference activation."""

from __future__ import annotations

import inspect
import pickle
import unittest
from dataclasses import FrozenInstanceError, fields

import story_writing_agents
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import (
    _reference_activation as activation,
)
from story_writing_agents.interactive_terminal._model import (
    LineInputState,
    MainCommandControlState,
    MainInputControlState,
)
from story_writing_agents.writing_artifacts import parse_artifact_references


_PATHS = (
    "core_documents/character_sheets.md",
    "core_documents/global_outline.md",
    "core_documents/narrative_rules.md",
    "manuscript/part-01/chapter.md",
)


def _state(buffer: str, cursor: int | None = None) -> MainCommandControlState:
    """Build one history-active state to prove edits preserve editor metadata."""

    line = LineInputState(buffer, len(buffer) if cursor is None else cursor)
    return MainCommandControlState(
        MainInputControlState(
            line,
            history_index=1,
            saved_draft=LineInputState("saved draft", 5),
        )
    )


class ReferenceActivationContractTests(unittest.TestCase):
    """Lock private immutable values without growing either public facade."""

    def test_models_are_exact_frozen_slotted_picklable_and_private(self) -> None:
        values = (
            activation.ReferenceInputScope(1, 3),
            activation.ReferenceActivationRequest(
                activation.ReferenceActivationKind.PICK,
                1,
                3,
                _PATHS,
            ),
            activation.ReferenceInputProjection(
                failure=activation.ReferenceInputFailureKind.STALE
            ),
        )
        expected_fields = (
            ("start", "end"),
            ("kind", "start", "end", "paths", "path"),
            ("completion", "request", "failure"),
        )
        for value, names in zip(values, expected_fields):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(
                    tuple(inspect.signature(type(value)).parameters),
                    names,
                )
                self.assertEqual(tuple(field.name for field in fields(value)), names)
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertEqual(pickle.loads(pickle.dumps(value)), value)
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, names[0], None)

        self.assertEqual(
            [item.value for item in activation.ReferenceActivationKind],
            ["pick", "view"],
        )
        self.assertEqual(
            [item.value for item in activation.ReferenceInputFailureKind],
            ["empty_catalog", "stale", "unreadable", "unavailable", "invalid"],
        )
        for name in activation.__all__:
            self.assertNotIn(name, terminal_facade.__all__)
            self.assertNotIn(name, story_writing_agents.__all__)
        self.assertFalse(hasattr(terminal_facade, "ReferenceActivationRequest"))

    def test_invalid_relations_fail_before_any_projection(self) -> None:
        invalid = (
            (lambda: activation.ReferenceInputScope(True, 1), TypeError),
            (lambda: activation.ReferenceInputScope(2, 1), ValueError),
            (
                lambda: activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.PICK,
                    0,
                    1,
                    tuple(reversed(_PATHS)),
                ),
                ValueError,
            ),
            (
                lambda: activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.PICK,
                    0,
                    1,
                    _PATHS,
                    path=_PATHS[0],
                ),
                ValueError,
            ),
            (
                lambda: activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.VIEW,
                    0,
                    1,
                ),
                ValueError,
            ),
            (lambda: activation.ReferenceInputProjection(), ValueError),
            (
                lambda: activation.ReferenceInputProjection(
                    failure=activation.ReferenceInputFailureKind.STALE,
                    request=activation.ReferenceActivationRequest(
                        activation.ReferenceActivationKind.VIEW,
                        0,
                        1,
                        path=_PATHS[0],
                    ),
                ),
                ValueError,
            ),
        )
        for call, expected in invalid:
            with self.subTest(call=call), self.assertRaises(expected):
                call()


class ReferenceProjectionTests(unittest.TestCase):
    """Verify exact markers, provider scope, escaping, and catalog outcomes."""

    def test_single_marker_projects_exact_sorted_canonical_completion(self) -> None:
        buffer = "revise $ before publication"
        scope = activation.ReferenceInputScope(0, len(buffer))
        projected = activation.project_reference_input(buffer, 8, scope, _PATHS)
        self.assertIsNotNone(projected)
        assert projected is not None and projected.completion is not None
        self.assertEqual(
            (
                projected.completion.position,
                projected.completion.start,
                projected.completion.end,
            ),
            ("artifact-reference:7", 7, 8),
        )
        self.assertEqual(
            tuple(item.replacement for item in projected.completion.candidates),
            tuple(f"${{{path}}}" for path in _PATHS),
        )

    def test_picker_and_direct_view_are_explicit_and_catalog_correlated(self) -> None:
        pick = activation.project_reference_input(
            "use $$", 6, activation.ReferenceInputScope(0, 6), _PATHS
        )
        self.assertEqual(
            pick,
            activation.ReferenceInputProjection(
                request=activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.PICK, 4, 6, _PATHS
                )
            ),
        )
        expression = f"use $${{{_PATHS[1]}}}"
        view = activation.project_reference_input(
            expression,
            len(expression),
            activation.ReferenceInputScope(0, len(expression)),
            _PATHS,
        )
        self.assertEqual(
            view,
            activation.ReferenceInputProjection(
                request=activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.VIEW,
                    4,
                    len(expression),
                    path=_PATHS[1],
                )
            ),
        )

    def test_completed_direct_view_excludes_every_suffix_and_later_cursor(self) -> None:
        path = _PATHS[1]
        marker = f"$${{{path}}}"
        marker_start = 4
        marker_end = marker_start + len(marker)
        expected = activation.ReferenceInputProjection(
            request=activation.ReferenceActivationRequest(
                activation.ReferenceActivationKind.VIEW,
                marker_start,
                marker_end,
                path=path,
            )
        )
        for suffix in (".", ",", ")", "word"):
            with self.subTest(suffix=suffix):
                buffer = f"use {marker}{suffix}"
                scope = activation.ReferenceInputScope(0, len(buffer))

                projected = activation.project_reference_input(
                    buffer,
                    marker_end,
                    scope,
                    _PATHS,
                )

                self.assertEqual(projected, expected)
                assert projected is not None and projected.request is not None
                self.assertEqual(buffer[projected.request.end :], suffix)
                self.assertIsNone(
                    activation.project_reference_input(
                        buffer,
                        len(buffer),
                        scope,
                        _PATHS,
                    )
                )

    def test_scope_cursor_escape_and_fragment_boundaries_do_not_overreach(self) -> None:
        cases = (
            ("prefix $", 8, None, None),
            ("prefix $", 8, activation.ReferenceInputScope(0, 6), None),
            ("prefix \\$", 9, activation.ReferenceInputScope(0, 9), None),
            (
                "prefix \\\\$",
                10,
                activation.ReferenceInputScope(0, 10),
                "artifact-reference:9",
            ),
            (
                "prefix $; suffix",
                8,
                activation.ReferenceInputScope(0, 16),
                "artifact-reference:7",
            ),
        )
        for buffer, cursor, scope, position in cases:
            with self.subTest(buffer=buffer, cursor=cursor):
                result = activation.project_reference_input(
                    buffer,
                    cursor,
                    scope,
                    _PATHS,
                )
                if position is None:
                    self.assertIsNone(result)
                else:
                    assert result is not None and result.completion is not None
                    self.assertEqual(result.completion.position, position)

    def test_empty_stale_invalid_and_repository_failures_are_content_free(self) -> None:
        direct = f"$${{{_PATHS[0]}}}"
        cases = (
            ("$", (), None, activation.ReferenceInputFailureKind.EMPTY_CATALOG),
            ("$$", (), None, activation.ReferenceInputFailureKind.EMPTY_CATALOG),
            (
                direct,
                _PATHS[1:],
                None,
                activation.ReferenceInputFailureKind.STALE,
            ),
            (
                "$${../escape.md}",
                _PATHS,
                None,
                activation.ReferenceInputFailureKind.INVALID,
            ),
            (
                "$${core_documents/global_outline.md",
                _PATHS,
                None,
                activation.ReferenceInputFailureKind.INVALID,
            ),
            (
                "$${}",
                _PATHS,
                None,
                activation.ReferenceInputFailureKind.INVALID,
            ),
            (
                "$$core_documents/global_outline.md}",
                _PATHS,
                None,
                activation.ReferenceInputFailureKind.INVALID,
            ),
            (
                "$",
                _PATHS,
                activation.ReferenceInputFailureKind.UNAVAILABLE,
                activation.ReferenceInputFailureKind.UNAVAILABLE,
            ),
            (
                direct,
                _PATHS,
                activation.ReferenceInputFailureKind.UNREADABLE,
                activation.ReferenceInputFailureKind.UNREADABLE,
            ),
        )
        for buffer, paths, failure, expected in cases:
            with self.subTest(buffer=buffer, expected=expected):
                result = activation.project_reference_input(
                    buffer,
                    len(buffer),
                    activation.ReferenceInputScope(0, len(buffer)),
                    paths,
                    failure=failure,
                )
                self.assertEqual(
                    result,
                    activation.ReferenceInputProjection(failure=expected),
                )


class ReferenceEditTests(unittest.TestCase):
    """Prove edits are atomic, range-inclusive, ordered, and history-neutral."""

    def test_direct_view_range_replaces_only_marker_before_punctuation(self) -> None:
        path = _PATHS[1]
        marker = f"$${{{path}}}"
        marker_start = 4
        marker_end = marker_start + len(marker)
        replacement = f"${{{path}}}#L2-L3"
        for suffix in (".", ",", ")"):
            with self.subTest(suffix=suffix):
                buffer = f"use {marker}{suffix}"
                projected = activation.project_reference_input(
                    buffer,
                    marker_end,
                    activation.ReferenceInputScope(0, len(buffer)),
                    _PATHS,
                )
                assert projected is not None and projected.request is not None

                updated = activation.replace_reference_range(
                    _state(buffer, marker_end),
                    projected.request,
                    2,
                    3,
                )

                self.assertEqual(
                    updated.main_input.line_state.buffer,
                    f"use {replacement}{suffix}",
                )
                self.assertEqual(
                    updated.main_input.line_state.cursor,
                    marker_start + len(replacement),
                )
                self.assertEqual(buffer, f"use {marker}{suffix}")

    def test_completion_replaces_only_active_span_and_preserves_editor_state(self) -> None:
        original = _state("prefix $ suffix", 8)
        projection = activation.project_reference_input(
            original.main_input.line_state.buffer,
            original.main_input.line_state.cursor,
            activation.ReferenceInputScope(0, 15),
            _PATHS,
        )
        assert projection is not None and projection.completion is not None
        replacement = projection.completion.candidates[1].replacement
        updated = activation.replace_reference_completion(
            original, projection.completion, replacement
        )
        self.assertEqual(
            updated.main_input.line_state.buffer,
            f"prefix {replacement} suffix",
        )
        self.assertEqual(updated.main_input.line_state.cursor, 7 + len(replacement))
        self.assertEqual(updated.main_input.history_index, 1)
        self.assertEqual(
            updated.main_input.saved_draft,
            LineInputState("saved draft", 5),
        )
        self.assertFalse(updated.main_input.overlay_open)
        self.assertEqual(original.main_input.line_state.buffer, "prefix $ suffix")

    def test_picker_selection_then_range_insertion_preserves_multiple_ordered_refs(self) -> None:
        buffer = "compare $ with $$ end"
        second_start = buffer.index("$$")
        picker_request = activation.ReferenceActivationRequest(
            activation.ReferenceActivationKind.PICK,
            second_start,
            second_start + 2,
            _PATHS,
        )
        selected = activation.selected_reference_request(picker_request, _PATHS[3])
        once = activation.replace_reference_range(
            _state(buffer, second_start + 2), selected, 2, 4
        )
        first = activation.project_reference_input(
            once.main_input.line_state.buffer,
            9,
            activation.ReferenceInputScope(0, len(once.main_input.line_state.buffer)),
            _PATHS,
        )
        assert first is not None and first.completion is not None
        first_token = first.completion.candidates[0].replacement
        updated = activation.replace_reference_completion(
            once, first.completion, first_token
        )
        parsed = parse_artifact_references(updated.main_input.line_state.buffer)
        self.assertEqual(
            tuple(reference.token for reference in parsed.references),
            (first_token, f"${{{_PATHS[3]}}}#L2-L4"),
        )
        self.assertTrue(updated.main_input.line_state.buffer.endswith(" end"))

    def test_stale_selection_and_conflicting_edits_leave_origin_unchanged(self) -> None:
        request = activation.ReferenceActivationRequest(
            activation.ReferenceActivationKind.PICK, 0, 2, _PATHS
        )
        for call in (
            lambda: activation.selected_reference_request(request, "missing.md"),
            lambda: activation.replace_reference_range(
                _state("changed", 7),
                activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.VIEW,
                    0,
                    2,
                    path=_PATHS[0],
                ),
                1,
                1,
            ),
        ):
            with self.subTest(call=call), self.assertRaises(
                activation.ReferenceInputFailure
            ) as raised:
                call()
            self.assertIn(
                raised.exception.kind,
                {
                    activation.ReferenceInputFailureKind.STALE,
                    activation.ReferenceInputFailureKind.INVALID,
                },
            )


if __name__ == "__main__":
    unittest.main()
