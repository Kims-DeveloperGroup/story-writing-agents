"""Independent physical definition of the Backend close diagnostic case."""

from __future__ import annotations

from story_writing_agents import Workspace
from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH, WorkspaceDebugLog
from tests.backend_lifecycle_diagnostic_fixture import (
    _cleanup_rows_since,
    _debug_records,
    _fresh_literal_object_side_effect,
)


class BackendDiagnosticEventCases:
    """Exercise close diagnostics with the existing Backend lifecycle fixture."""

    def test_close_diagnostic_control_flow_does_not_change_cleanup(self) -> None:
        error_backend = self.backend()
        error_tokens = {
            "error_sink_alpha": "PRIVATE_ERROR_SINK_ALPHA_TOKEN",
            "error_sink_beta": "PRIVATE_ERROR_SINK_BETA_TOKEN",
        }
        error_staging = self.prepare_close_state(
            error_backend,
            "error_sink",
            error_tokens,
        )
        error_failure = OSError("PRIVATE_ERROR_SINK_RELEASE_FAILURE")
        error_attempts: list[tuple[str, str]] = []

        def release_for_error_sink(_workspace: Workspace, session_id: str, token: str) -> None:
            error_attempts.append((session_id, token))
            if session_id == "error_sink_alpha":
                raise error_failure

        error_mark = len(_debug_records(self.workspace))
        with (
            _fresh_literal_object_side_effect(error_backend._access_transactions, 'release_access_claim', release_for_error_sink),
            _fresh_literal_object_side_effect(WorkspaceDebugLog, "error", KeyboardInterrupt("diagnostic interrupt")) as debug_error,
        ):
            self.assertIsNone(error_backend.close())

        self.assertEqual(error_attempts, list(error_tokens.items()))
        debug_error.assert_called_once()
        self.assertIs(debug_error.call_args.args[2], error_failure)
        self.assert_close_resources_cleared(error_backend, error_staging)
        self.assertEqual(
            _cleanup_rows_since(self.workspace, error_mark),
            [],
        )

        trace_backend = self.backend()
        trace_tokens = {
            "trace_sink_alpha": "PRIVATE_TRACE_SINK_ALPHA_TOKEN",
            "trace_sink_beta": "PRIVATE_TRACE_SINK_BETA_TOKEN",
        }
        trace_staging = self.prepare_close_state(
            trace_backend,
            "trace_sink",
            trace_tokens,
        )
        trace_failure = OSError("PRIVATE_TRACE_SINK_RELEASE_FAILURE")
        trace_attempts: list[tuple[str, str]] = []

        def release_for_trace_sink(_workspace: Workspace, session_id: str, token: str) -> None:
            trace_attempts.append((session_id, token))
            if session_id == "trace_sink_alpha":
                raise trace_failure

        trace_mark = len(_debug_records(self.workspace))
        with (
            _fresh_literal_object_side_effect(trace_backend._access_transactions, 'release_access_claim', release_for_trace_sink),
            _fresh_literal_object_side_effect(WorkspaceDebugLog, 'trace', SystemExit('diagnostic exit')) as debug_trace,
        ):
            self.assertIsNone(trace_backend.close())

        self.assertEqual(trace_attempts, list(trace_tokens.items()))
        self.assert_close_resources_cleared(trace_backend, trace_staging)
        self.assertEqual(debug_trace.call_count, 2)
        self.assertEqual(
            [call.kwargs["phase"].value for call in debug_trace.call_args_list],
            ["started", "failed"],
        )
        self.assertEqual(
            [
                None
                if call.kwargs.get("outcome") is None
                else call.kwargs["outcome"].value
                for call in debug_trace.call_args_list
            ],
            [None, "failed"],
        )
        self.assertEqual(
            _cleanup_rows_since(self.workspace, trace_mark),
            [],
        )
        debug_path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        raw = debug_path.read_text(encoding="utf-8") if debug_path.exists() else ""
        for private in (
            *error_tokens.values(),
            *trace_tokens.values(),
            str(error_failure),
            str(trace_failure),
            str(error_staging),
            str(trace_staging),
            str(self.workspace.root),
        ):
            self.assertNotIn(private, raw)
