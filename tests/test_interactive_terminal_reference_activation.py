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
    EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX,
    RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX,
    CommandCompletionCandidate,
    CommandCompletionProjection,
    LineInputState,
    MainCommandControlState,
    MainInputControlState,
)
from story_writing_agents.writing_artifacts import parse_artifact_references


_PATHS = (
    "core_documents/character_sheets.md",
    "core_documents/global_outline.md",
    "core_documents/narrative_rules.md",
    "manuscript/older drafts/{世界$ 章#1.md",
    "manuscript/root.md",
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
            ["empty_sources", "stale", "unreadable", "unavailable", "invalid"],
        )
        for name in activation.__all__:
            self.assertNotIn(name, terminal_facade.__all__)
            self.assertNotIn(name, story_writing_agents.__all__)
        self.assertFalse(hasattr(terminal_facade, "ReferenceActivationRequest"))

    def test_invalid_relations_fail_before_any_projection(self) -> None:
        empty_position = "artifact-reference:empty:0"
        empty_completion = CommandCompletionProjection(
            empty_position,
            0,
            1,
            (),
        )
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
            (
                lambda: activation.ReferenceInputProjection(
                    completion=empty_completion,
                    failure=activation.ReferenceInputFailureKind.STALE,
                ),
                ValueError,
            ),
            (
                lambda: activation.ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        "artifact-reference:0",
                        0,
                        1,
                        (),
                    ),
                    failure=activation.ReferenceInputFailureKind.EMPTY_SOURCES,
                ),
                ValueError,
            ),
        )
        for call, expected in invalid:
            with self.subTest(call=call), self.assertRaises(expected):
                call()

        self.assertEqual(
            activation.ReferenceInputProjection(
                completion=empty_completion,
                failure=activation.ReferenceInputFailureKind.EMPTY_SOURCES,
            ).completion,
            empty_completion,
        )


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
        position = f"{RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}4"
        pick = activation.project_reference_input(
            "use $$", 6, activation.ReferenceInputScope(0, 6), _PATHS
        )
        self.assertEqual(
            pick,
            activation.ReferenceInputProjection(
                completion=CommandCompletionProjection(
                    position,
                    4,
                    6,
                    tuple(
                        CommandCompletionCandidate(position, path)
                        for path in _PATHS
                    ),
                ),
                request=activation.ReferenceActivationRequest(
                    activation.ReferenceActivationKind.PICK, 4, 6, _PATHS
                )
            ),
        )
        for path in (_PATHS[1], _PATHS[3], _PATHS[4]):
            with self.subTest(path=path):
                expression = f"use $${{{path}}}"
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
                            path=path,
                        )
                    ),
                )

    def test_direct_marker_construction_with_space_unicode_and_delimiters_is_exact(
        self,
    ) -> None:
        path = _PATHS[3]
        marker = f"$${{{path}}}"
        scope = activation.ReferenceInputScope(0, len(marker))

        picker = activation.project_reference_input(
            "$$",
            2,
            activation.ReferenceInputScope(0, 2),
            _PATHS,
        )
        assert picker is not None and picker.completion is not None
        self.assertTrue(
            picker.completion.position.startswith(
                RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX
            )
        )
        self.assertEqual(
            activation.project_reference_input(
                "$${",
                3,
                activation.ReferenceInputScope(0, 3),
                _PATHS,
            ),
            activation.ReferenceInputProjection(
                failure=activation.ReferenceInputFailureKind.INVALID
            ),
        )
        completed = activation.project_reference_input(
            marker,
            len(marker),
            scope,
            _PATHS,
        )
        assert completed is not None and completed.request is not None
        self.assertEqual(completed.request.path, path)

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

    def test_complete_single_reference_is_inert_but_noncanonical_stays_active(
        self,
    ) -> None:
        path = _PATHS[1]
        marker = f"${{{path}}}"
        suffixes = ("", ".", ",", ")", "word", "#L2-L3", "#L2-L3word")
        for suffix in suffixes:
            for failure in (
                None,
                activation.ReferenceInputFailureKind.STALE,
                activation.ReferenceInputFailureKind.UNAVAILABLE,
            ):
                with self.subTest(suffix=suffix, failure=failure):
                    buffer = f"use {marker}{suffix}"
                    self.assertIsNone(
                        activation.project_reference_input(
                            buffer,
                            buffer.index("$") + 1,
                            activation.ReferenceInputScope(0, len(buffer)),
                            (),
                            failure=failure,
                        )
                    )

        for marker in (
            "${core",
            "${}",
            "${../escape.md}",
            "$Core",
            "${Core_documents/global_outline.md}",
        ):
            with self.subTest(active=marker):
                buffer = f"use {marker}"
                projected = activation.project_reference_input(
                    buffer,
                    buffer.index("$") + 1,
                    activation.ReferenceInputScope(0, len(buffer)),
                    _PATHS,
                )
                self.assertIsNotNone(projected)
                assert projected is not None
                self.assertIsNotNone(projected.completion)

    def test_outer_braced_spans_shield_nested_dollars_and_later_markers(
        self,
    ) -> None:
        path = _PATHS[3]
        complete_whole = f"${{{path}}}"
        unfinished_whole = complete_whole[:-1]
        complete_direct = f"$${{{path}}}"
        unfinished_direct = complete_direct[:-1]
        cases = (
            (complete_whole, None, None, None),
            (unfinished_whole, "completion", 0, len(unfinished_whole)),
            (complete_direct, "view", 0, len(complete_direct)),
            (unfinished_direct, "invalid", None, None),
        )
        for buffer, outcome, start, end in cases:
            for cursor in (buffer.rfind("$") + 1, len(buffer)):
                with self.subTest(buffer=buffer, cursor=cursor):
                    projected = activation.project_reference_input(
                        buffer,
                        cursor,
                        activation.ReferenceInputScope(0, len(buffer)),
                        _PATHS,
                    )
                    if outcome is None:
                        self.assertIsNone(projected)
                    elif outcome == "completion":
                        assert projected is not None
                        assert projected.completion is not None
                        self.assertEqual(
                            (projected.completion.start, projected.completion.end),
                            (start, end),
                        )
                    elif outcome == "view":
                        assert projected is not None and projected.request is not None
                        self.assertIs(
                            projected.request.kind,
                            activation.ReferenceActivationKind.VIEW,
                        )
                        self.assertEqual(
                            (projected.request.start, projected.request.end),
                            (start, end),
                        )
                    else:
                        self.assertEqual(
                            projected,
                            activation.ReferenceInputProjection(
                                failure=activation.ReferenceInputFailureKind.INVALID
                            ),
                        )
            self.assertEqual(
                activation.previous_reference_marker_start(buffer, len(buffer)),
                0,
            )

        for marker in (
            complete_whole,
            unfinished_whole,
            complete_direct,
            unfinished_direct,
        ):
            escaped = f"\\{marker}"
            with self.subTest(escaped=escaped):
                for cursor in (escaped.rfind("$") + 1, len(escaped)):
                    self.assertIsNone(
                        activation.project_reference_input(
                            escaped,
                            cursor,
                            activation.ReferenceInputScope(0, len(escaped)),
                            _PATHS,
                        )
                    )
                self.assertIsNone(
                    activation.previous_reference_marker_start(
                        escaped,
                        len(escaped),
                    )
                )

        even_escape = f"\\\\{unfinished_whole}"
        even_projection = activation.project_reference_input(
            even_escape,
            even_escape.rfind("$") + 1,
            activation.ReferenceInputScope(0, len(even_escape)),
            _PATHS,
        )
        assert even_projection is not None and even_projection.completion is not None
        self.assertEqual(even_projection.completion.start, 2)
        self.assertEqual(
            activation.previous_reference_marker_start(
                even_escape,
                len(even_escape),
            ),
            2,
        )

        later = f"literal {complete_whole} then $"
        later_start = later.rfind("$")
        later_projection = activation.project_reference_input(
            later,
            len(later),
            activation.ReferenceInputScope(0, len(later)),
            _PATHS,
        )
        assert later_projection is not None
        assert later_projection.completion is not None
        self.assertEqual(later_projection.completion.start, later_start)
        self.assertEqual(
            activation.previous_reference_marker_start(later, len(later)),
            later_start,
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
        empty_position = "artifact-reference:empty:0"
        cases = (
            (
                "$",
                (),
                None,
                activation.ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        empty_position,
                        0,
                        1,
                        (),
                    ),
                    failure=activation.ReferenceInputFailureKind.EMPTY_SOURCES,
                ),
            ),
            (
                "$$",
                (),
                None,
                activation.ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        f"{EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}0",
                        0,
                        2,
                        (),
                    ),
                    failure=activation.ReferenceInputFailureKind.EMPTY_SOURCES,
                ),
            ),
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
                    expected
                    if isinstance(expected, activation.ReferenceInputProjection)
                    else activation.ReferenceInputProjection(failure=expected),
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
