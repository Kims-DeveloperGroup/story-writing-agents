"""Existing Backend global shutdown scenarios."""

from __future__ import annotations

from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH

from tests.backend_lifecycle_diagnostic_fixture import (
    _cleanup_rows_since,
    _debug_records,
    _fresh_literal_object_side_effect,
)


class BackendShutdownResourceCases:
    """Shutdown scenarios collected by AgentBackendLifecycleTests."""

    def test_close_success_records_terminal_and_is_idempotent(self) -> None:
        backend = self.backend()
        tokens = {"close_success": "PRIVATE_CLOSE_SUCCESS_TOKEN"}
        staging = self.prepare_close_state(backend, "close_success", tokens)
        attempts: list[tuple[str, str]] = []
        close_order: list[str] = []

        def release(_workspace: Workspace, session_id: str, token: str) -> None:
            attempts.append((session_id, token))

        codex_close = backend._codex_adapter.close
        antigravity_close = backend._antigravity_adapter.close
        trace_debug = backend._trace_debug

        def close_codex() -> None:
            close_order.append("codex")
            codex_close()

        def close_antigravity() -> None:
            close_order.append("antigravity")
            antigravity_close()

        def trace(operation: object, phase: object, **fields: object) -> None:
            if operation == "cleanup" and phase in {"failed", "succeeded"}:
                close_order.append("terminal")
            trace_debug(operation, phase, **fields)  # type: ignore[arg-type]

        mark = len(_debug_records(self.workspace))
        with (
            _fresh_literal_object_side_effect(backend._access_transactions, 'release_access_claim', release),
            _fresh_literal_object_side_effect(backend._codex_adapter, "close", close_codex),
            _fresh_literal_object_side_effect(backend._antigravity_adapter, "close", close_antigravity),
            mock.patch.object(backend, "_trace_debug", side_effect=trace),
        ):
            self.assertIsNone(backend.close())

        self.assertEqual(attempts, list(tokens.items()))
        self.assertEqual(close_order, ["codex", "antigravity", "terminal"])
        self.assert_close_resources_cleared(backend, staging)
        self.assertEqual(
            _cleanup_rows_since(self.workspace, mark),
            [],
        )
        debug_path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        raw = debug_path.read_text(encoding="utf-8") if debug_path.exists() else ""
        for private in (*tokens.values(), str(staging), str(self.workspace.root)):
            self.assertNotIn(private, raw)

        repeat_mark = len(_debug_records(self.workspace))
        self.assertIsNone(backend.close())
        self.assertEqual(len(_debug_records(self.workspace)), repeat_mark)

    def test_close_ordinary_release_failures_record_failed_terminal(self) -> None:
        cases = (
            ("one", ("close_beta",)),
            ("multiple", ("close_alpha", "close_gamma")),
        )
        for label, failed_sessions in cases:
            with self.subTest(label=label):
                backend = self.backend()
                tokens = {
                    "close_alpha": "PRIVATE_CLOSE_ALPHA_TOKEN",
                    "close_beta": "PRIVATE_CLOSE_BETA_TOKEN",
                    "close_gamma": "PRIVATE_CLOSE_GAMMA_TOKEN",
                }
                staging = self.prepare_close_state(
                    backend,
                    f"ordinary_{label}",
                    tokens,
                )
                failures = {
                    session_id: OSError(f"PRIVATE_{session_id.upper()}_FAILURE")
                    for session_id in failed_sessions
                }
                failure_messages = {
                    session_id: str(failure) for session_id, failure in failures.items()
                }
                attempts: list[tuple[str, str]] = []

                def release(_workspace: Workspace, session_id: str, token: str) -> None:
                    attempts.append((session_id, token))
                    failure = failures.get(session_id)
                    if failure is not None:
                        raise failure

                mark = len(_debug_records(self.workspace))
                with _fresh_literal_object_side_effect(backend._access_transactions, 'release_access_claim', release):
                    self.assertIsNone(backend.close())

                self.assertEqual(attempts, list(tokens.items()))
                self.assert_close_resources_cleared(backend, staging)
                self.assertEqual(
                    _cleanup_rows_since(self.workspace, mark),
                    [],
                )
                for failure in failures.values():
                    self.assertIsNone(failure.__cause__)
                    self.assertIsNone(failure.__context__)
                debug_path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
                raw = (
                    debug_path.read_text(encoding="utf-8")
                    if debug_path.exists()
                    else ""
                )
                for private in (
                    *tokens.values(),
                    *failure_messages.values(),
                    str(staging),
                    str(self.workspace.root),
                ):
                    self.assertNotIn(private, raw)

    def test_close_release_base_exception_preserves_interrupted_state(self) -> None:
        backend = self.backend()
        tokens = {
            "interrupt_alpha": "PRIVATE_INTERRUPT_ALPHA_TOKEN",
            "interrupt_beta": "PRIVATE_INTERRUPT_BETA_TOKEN",
        }
        staging = self.prepare_close_state(backend, "interrupt", tokens)
        failure = KeyboardInterrupt("PRIVATE_APPLICATION_RELEASE_INTERRUPT")
        failure_message = str(failure)
        attempts: list[tuple[str, str]] = []

        def release(_workspace: Workspace, session_id: str, token: str) -> None:
            attempts.append((session_id, token))
            raise failure

        mark = len(_debug_records(self.workspace))
        with (
            _fresh_literal_object_side_effect(backend._access_transactions, 'release_access_claim', release),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            backend.close()

        self.assertIs(raised.exception, failure)
        self.assertEqual(str(raised.exception), failure_message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(attempts, [next(iter(tokens.items()))])
        self.assertTrue(backend._closed)
        self.assertEqual(backend._access_claim_tokens, tokens)
        self.assertTrue(backend._thread_locks)
        self.assertEqual(backend._staging_directories, {staging})
        self.assertTrue(staging.is_dir())
        self.assertEqual(
            _cleanup_rows_since(self.workspace, mark),
            [],
        )
        debug_path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        raw = debug_path.read_text(encoding="utf-8") if debug_path.exists() else ""
        for private in (
            *tokens.values(),
            failure_message,
            str(staging),
            str(self.workspace.root),
        ):
            self.assertNotIn(private, raw)

        repeat_mark = len(_debug_records(self.workspace))
        self.assertIsNone(backend.close())
        self.assertEqual(len(_debug_records(self.workspace)), repeat_mark)
        self.assertEqual(backend._access_claim_tokens, tokens)
        self.assertEqual(backend._staging_directories, {staging})
