"""Contracts for private bounded provider model and effort discovery."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import provider_catalog as catalog_module
from story_writing_agents.provider_catalog import (
    CATALOG_MAX_EFFORTS_PER_MODEL,
    CATALOG_MAX_MODELS,
    CATALOG_MAX_STDOUT_BYTES,
    CATALOG_PROVIDERS,
    ProviderCatalog,
    ProviderCatalogDiscovery,
    ProviderCatalogService,
    ProviderCatalogState,
    ProviderEffort,
    ProviderModel,
)


def _codex_bytes(models: list[object]) -> bytes:
    """Encode one deterministic strict-JSON Codex catalog fixture."""

    return json.dumps(
        {"models": models},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _codex_model(
    slug: str,
    *,
    display_name: str | None = None,
    visibility: str = "list",
    efforts: list[dict[str, object]] | None = None,
    default: str | None = None,
    description: str | None = None,
) -> dict[str, object]:
    """Build one exact native Codex row without using production helpers."""

    return {
        "slug": slug,
        "display_name": slug if display_name is None else display_name,
        "description": description,
        "visibility": visibility,
        "supported_reasoning_levels": [] if efforts is None else efforts,
        "default_reasoning_level": default,
    }


class _ChunkStream:
    """Return scripted reader chunks and expose exact close/read behavior."""

    def __init__(self, *chunks: object) -> None:
        self.chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int) -> object:
        self.read_sizes.append(size)
        if not self.chunks:
            return b""
        value = self.chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class _CloseReleasedStream:
    """Model a pipe reader that exits only after the owner closes stdout."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()
        self.closed = False

    def read(self, _size: int) -> bytes:
        self.started.set()
        self.released.wait(timeout=1.0)
        return b""

    def close(self) -> None:
        self.closed = True
        self.released.set()


class _FakeProcess:
    """Expose deterministic Popen process-control outcomes and events."""

    def __init__(
        self,
        stdout: object | None,
        *,
        wait_outcomes: tuple[object, ...] = (0,),
        poll_outcomes: tuple[object, ...] = (None,),
    ) -> None:
        self.stdout = stdout
        self.wait_outcomes = list(wait_outcomes)
        self.poll_outcomes = list(poll_outcomes)
        self.events: list[tuple[str, object | None]] = []

    def wait(self, *, timeout: float) -> int:
        self.events.append(("wait", timeout))
        if not self.wait_outcomes:
            raise AssertionError("unexpected process wait")
        value = self.wait_outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def poll(self) -> int | None:
        self.events.append(("poll", None))
        if not self.poll_outcomes:
            return None
        value = self.poll_outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def terminate(self) -> None:
        self.events.append(("terminate", None))

    def kill(self) -> None:
        self.events.append(("kill", None))


class _ProcessFactory:
    """Return or raise scripted process outcomes while recording exact calls."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if not self.outcomes:
            raise AssertionError("unexpected provider process construction")
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class ProviderCatalogProjectionTests(unittest.TestCase):
    """Verify immutable internal values and exact native-output projection."""

    def test_internal_surface_vocabulary_relations_and_facade_boundary(self) -> None:
        self.assertEqual(CATALOG_PROVIDERS, ("codex", "antigravity"))
        self.assertEqual(
            tuple((member.name, member.value) for member in ProviderCatalogState),
            (
                ("READY", "ready"),
                ("UNAVAILABLE", "unavailable"),
                ("TIMED_OUT", "timed_out"),
                ("COMMAND_FAILED", "command_failed"),
                ("MALFORMED", "malformed"),
                ("EMPTY", "empty"),
                ("OVERSIZED", "oversized"),
                ("FAILED", "failed"),
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderEffort)),
            ("identity", "label", "description"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderModel)),
            ("identity", "label", "description", "default_effort", "efforts"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderCatalog)),
            ("provider", "models"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderCatalogDiscovery)),
            ("provider", "state", "catalog"),
        )

        effort = ProviderEffort("future_2", "future_2", "Future-safe")
        model = ProviderModel(
            "gpt-opaque@2026",
            "GPT Opaque",
            None,
            "future_2",
            (effort,),
        )
        catalog = ProviderCatalog("codex", (model,))
        discovery = ProviderCatalogDiscovery(
            "codex",
            ProviderCatalogState.READY,
            catalog,
        )
        self.assertTrue(discovery.ready)
        self.assertIs(catalog.model_for("gpt-opaque@2026"), model)
        self.assertIsNone(catalog.model_for("GPT-OPAQUE@2026"))
        self.assertIsNone(catalog.model_for(None))
        for value in (effort, model, catalog, discovery):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                field = fields(value)[0]
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, field.name, getattr(value, field.name))

        internal_names = (
            "ProviderCatalogService",
            "ProviderCatalogState",
            "ProviderCatalogDiscovery",
            "ProviderCatalog",
            "ProviderModel",
            "ProviderEffort",
        )
        for name in internal_names:
            with self.subTest(name=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

    def test_projection_models_reject_unsafe_duplicates_and_bad_defaults(self) -> None:
        effort = ProviderEffort("low", "low")
        invalid = (
            lambda: ProviderEffort("LOW", "low"),
            lambda: ProviderEffort("low", " bad"),
            lambda: ProviderEffort("low", "bad\x1b"),
            lambda: ProviderModel("-unsafe", "Unsafe", None, None, ()),
            lambda: ProviderModel("bad model", "Unsafe", None, None, ()),
            lambda: ProviderModel("safe", "Safe", None, "high", (effort,)),
            lambda: ProviderModel("safe", "Safe", None, None, (effort, effort)),
            lambda: ProviderCatalog("codex", ()),
            lambda: ProviderCatalog(
                "codex",
                (
                    ProviderModel("same", "One", None, None, ()),
                    ProviderModel("same", "Two", None, None, ()),
                ),
            ),
            lambda: ProviderCatalogDiscovery(
                "codex",
                ProviderCatalogState.READY,
            ),
            lambda: ProviderCatalogDiscovery(
                "codex",
                ProviderCatalogState.EMPTY,
                ProviderCatalog(
                    "codex",
                    (ProviderModel("safe", "Safe", None, None, ()),),
                ),
            ),
            lambda: ProviderCatalogDiscovery(
                "codex",
                ProviderCatalogState.READY,
                ProviderCatalog(
                    "antigravity",
                    (ProviderModel("safe", "Safe", None, None, ()),),
                ),
            ),
        )
        for operation in invalid:
            with self.subTest(operation=operation):
                with self.assertRaises((TypeError, ValueError)):
                    operation()

    def test_codex_parser_preserves_list_order_and_exact_future_efforts(self) -> None:
        value = _codex_bytes(
            [
                {
                    "visibility": "hidden",
                    "slug": "ignored duplicate",
                },
                _codex_model(
                    "gpt-z",
                    display_name="GPT Zeta",
                    description="First provider row",
                    efforts=[
                        {"effort": "future_2", "description": "Future mode"},
                        {"effort": "low", "description": None},
                    ],
                    default="future_2",
                ),
                _codex_model(
                    "gpt-a",
                    display_name="GPT Alpha 한🙂",
                    efforts=[{"effort": "a", "description": "Minimum"}],
                    default="a",
                ),
                _codex_model(
                    "not-listed",
                    visibility="LIST",
                    efforts=[{"effort": "low"}],
                ),
            ]
        )

        self.assertEqual(
            catalog_module._parse_codex_models(value),
            (
                ProviderModel(
                    "gpt-z",
                    "GPT Zeta",
                    "First provider row",
                    "future_2",
                    (
                        ProviderEffort("future_2", "future_2", "Future mode"),
                        ProviderEffort("low", "low", None),
                    ),
                ),
                ProviderModel(
                    "gpt-a",
                    "GPT Alpha 한🙂",
                    None,
                    "a",
                    (ProviderEffort("a", "a", "Minimum"),),
                ),
            ),
        )

    def test_codex_parser_rejects_non_strict_json_shapes_and_duplicates(self) -> None:
        malformed = (
            b"\xff",
            b"[]",
            b'{"models":null}',
            b'{"models":NaN}',
            b'{"models":[],"models":[]}',
            b'{"models":[{"visibility":"list","slug":"a","slug":"b"}]}',
            b'{"models":[1]}',
            _codex_bytes([_codex_model("missing-label", display_name="")]),
            _codex_bytes(
                [
                    {
                        "slug": "missing-efforts",
                        "display_name": "Missing efforts",
                        "visibility": "list",
                    }
                ]
            ),
            _codex_bytes(
                [
                    _codex_model(
                        "bad-effort",
                        efforts=[{"effort": "FUTURE", "description": None}],
                    )
                ]
            ),
        )
        for value in malformed:
            with self.subTest(value=value[:80]):
                with self.assertRaises((TypeError, ValueError, UnicodeError)):
                    catalog_module._parse_codex_models(value)

    def test_codex_parser_rejects_duplicate_relationships_and_capacities(self) -> None:
        duplicate_models = _codex_bytes(
            [_codex_model("same"), _codex_model("same")]
        )
        duplicate_efforts = _codex_bytes(
            [
                _codex_model(
                    "model",
                    efforts=[{"effort": "low"}, {"effort": "low"}],
                )
            ]
        )
        missing_default = _codex_bytes(
            [
                _codex_model(
                    "model",
                    efforts=[{"effort": "low"}],
                    default="high",
                )
            ]
        )
        too_many_models = _codex_bytes(
            [_codex_model(f"model-{index}") for index in range(CATALOG_MAX_MODELS + 1)]
        )
        too_many_efforts = _codex_bytes(
            [
                _codex_model(
                    "model",
                    efforts=[
                        {"effort": f"e{index}"}
                        for index in range(CATALOG_MAX_EFFORTS_PER_MODEL + 1)
                    ],
                )
            ]
        )
        for value in (
            duplicate_models,
            duplicate_efforts,
            missing_default,
            too_many_models,
            too_many_efforts,
        ):
            with self.subTest(size=len(value)):
                with self.assertRaises(ValueError):
                    catalog_module._parse_codex_models(value)

    def test_antigravity_parser_preserves_opaque_rows_and_exact_global_efforts(
        self,
    ) -> None:
        value = (
            "agy:z@2026  Antigravity Zeta\n"
            "\n"
            "agy/é🙂\tAntigravity Alpha 한🙂\n"
        ).encode("utf-8")
        efforts = (
            ProviderEffort("low", "low"),
            ProviderEffort("medium", "medium"),
            ProviderEffort("high", "high"),
        )
        self.assertEqual(
            catalog_module._parse_antigravity_models(value),
            (
                ProviderModel(
                    "agy:z@2026",
                    "Antigravity Zeta",
                    None,
                    None,
                    efforts,
                ),
                ProviderModel(
                    "agy/é🙂",
                    "Antigravity Alpha 한🙂",
                    None,
                    None,
                    efforts,
                ),
            ),
        )

    def test_antigravity_parser_rejects_unaligned_unsafe_duplicate_and_capacity(
        self,
    ) -> None:
        too_many = "\n".join(
            f"agy-{index}  Model {index}"
            for index in range(CATALOG_MAX_MODELS + 1)
        ).encode("utf-8")
        malformed = (
            b"agy-one One space only\n",
            b"agy-only\n",
            b"-unsafe  Unsafe\n",
            b"same  One\nsame  Two\n",
            b"agy  Label\x1b\n",
            b"\xff",
            too_many,
        )
        for value in malformed:
            with self.subTest(value=value[:80]):
                with self.assertRaises((TypeError, ValueError, UnicodeError)):
                    catalog_module._parse_antigravity_models(value)


class ProviderCatalogServiceTests(unittest.TestCase):
    """Verify fresh bounded subprocess ownership, states, and cleanup."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace_root = Path(temporary.name)

    def service(
        self,
        factory: _ProcessFactory,
        *,
        codex_executable: str = "codex-private",
        antigravity_executable: str = "/resolved/private-agy",
    ) -> ProviderCatalogService:
        """Construct one service around an exact deterministic process factory."""

        return ProviderCatalogService(
            self.workspace_root,
            codex_executable=codex_executable,
            antigravity_executable=antigravity_executable,
            _process_factory=factory,
        )

    def test_construction_is_idle_and_discovery_is_fresh_with_exact_commands(
        self,
    ) -> None:
        codex_payload = _codex_bytes([_codex_model("gpt-ready")])
        agy_payload = b"agy-ready  Antigravity Ready\n"
        streams = (
            _ChunkStream(codex_payload, b""),
            _ChunkStream(agy_payload, b""),
            _ChunkStream(codex_payload, b""),
        )
        factory = _ProcessFactory(
            *(_FakeProcess(stream) for stream in streams)
        )
        before = tuple(self.workspace_root.iterdir())
        service = self.service(factory)
        self.assertEqual(factory.calls, [])

        first = service.discover("codex")
        second = service.discover("antigravity")
        third = service.discover("codex")

        self.assertTrue(first.ready)
        self.assertTrue(second.ready)
        self.assertTrue(third.ready)
        self.assertIsNot(first.catalog, third.catalog)
        self.assertEqual(
            [call[0] for call in factory.calls],
            [
                (("codex-private", "debug", "models", "--bundled"),),
                (("/resolved/private-agy", "models"),),
                (("codex-private", "debug", "models", "--bundled"),),
            ],
        )
        expected_keywords = {
            "cwd": str(self.workspace_root),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        self.assertEqual(
            [call[1] for call in factory.calls],
            [expected_keywords, expected_keywords, expected_keywords],
        )
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertEqual(tuple(self.workspace_root.iterdir()), before)

    def test_unavailable_failed_nonzero_malformed_and_empty_states_are_redacted(
        self,
    ) -> None:
        private = "PRIVATE_PROVIDER_DIAGNOSTIC"
        scenarios = (
            (
                ProviderCatalogState.UNAVAILABLE,
                _ProcessFactory(FileNotFoundError(private)),
            ),
            (ProviderCatalogState.FAILED, _ProcessFactory(RuntimeError(private))),
            (
                ProviderCatalogState.COMMAND_FAILED,
                _ProcessFactory(
                    _FakeProcess(_ChunkStream(private.encode(), b""), wait_outcomes=(9,))
                ),
            ),
            (
                ProviderCatalogState.MALFORMED,
                _ProcessFactory(_FakeProcess(_ChunkStream(private.encode(), b""))),
            ),
            (
                ProviderCatalogState.EMPTY,
                _ProcessFactory(
                    _FakeProcess(_ChunkStream(_codex_bytes([]), b""))
                ),
            ),
        )
        for expected, factory in scenarios:
            with self.subTest(expected=expected):
                result = self.service(factory).discover("codex")
                self.assertIs(result.state, expected)
                self.assertIsNone(result.catalog)
                self.assertFalse(result.ready)
                self.assertNotIn(private, repr(result))
                self.assertEqual(len(factory.calls), 1)

    def test_timeout_terminates_waits_closes_and_joins_without_retry(self) -> None:
        stream = _ChunkStream(b"")
        process = _FakeProcess(stream, wait_outcomes=(0,), poll_outcomes=(None,))
        factory = _ProcessFactory(process)
        with mock.patch.object(
            catalog_module.time,
            "monotonic",
            side_effect=(10.0, 16.0),
        ):
            result = self.service(factory).discover("codex")

        self.assertIs(result.state, ProviderCatalogState.TIMED_OUT)
        self.assertEqual(
            process.events,
            [
                ("poll", None),
                ("terminate", None),
                ("wait", catalog_module.CATALOG_PROCESS_STOP_TIMEOUT_SECONDS),
            ],
        )
        self.assertTrue(stream.closed)
        self.assertEqual(len(factory.calls), 1)
        self.assertFalse(
            any(thread.name == "codex-catalog-reader" for thread in threading.enumerate())
        )

    def test_timeout_escalates_to_kill_and_reaps_with_exact_bounded_waits(self) -> None:
        stream = _ChunkStream(b"")
        process = _FakeProcess(
            stream,
            wait_outcomes=(
                subprocess.TimeoutExpired("codex", 2.0),
                -9,
            ),
            poll_outcomes=(None, None),
        )
        with mock.patch.object(
            catalog_module.time,
            "monotonic",
            side_effect=(20.0, 26.0),
        ):
            result = self.service(_ProcessFactory(process)).discover("codex")

        self.assertIs(result.state, ProviderCatalogState.TIMED_OUT)
        self.assertEqual(
            process.events,
            [
                ("poll", None),
                ("terminate", None),
                ("wait", catalog_module.CATALOG_PROCESS_STOP_TIMEOUT_SECONDS),
                ("poll", None),
                ("kill", None),
                ("wait", catalog_module.CATALOG_PROCESS_STOP_TIMEOUT_SECONDS),
            ],
        )
        self.assertTrue(stream.closed)

    def test_oversized_reader_retains_no_raw_output_and_closes_resources(self) -> None:
        private_chunk = b"S" * CATALOG_MAX_STDOUT_BYTES + b"PRIVATE_TAIL"
        stream = _ChunkStream(private_chunk, b"")
        factory = _ProcessFactory(_FakeProcess(stream))
        result = self.service(factory).discover("codex")

        self.assertIs(result.state, ProviderCatalogState.OVERSIZED)
        self.assertIsNone(result.catalog)
        self.assertNotIn("PRIVATE_TAIL", repr(result))
        self.assertTrue(stream.closed)
        self.assertEqual(len(factory.calls), 1)

    def test_failed_capture_paths_cleanup_stdout_process_and_reader(self) -> None:
        missing_stdout = _FakeProcess(
            None,
            wait_outcomes=(0,),
            poll_outcomes=(None,),
        )
        missing_result = self.service(
            _ProcessFactory(missing_stdout)
        ).discover("codex")
        self.assertIs(missing_result.state, ProviderCatalogState.FAILED)
        self.assertEqual(
            missing_stdout.events,
            [
                ("poll", None),
                ("terminate", None),
                ("wait", catalog_module.CATALOG_PROCESS_STOP_TIMEOUT_SECONDS),
            ],
        )

        wrong_type_stream = _ChunkStream("PRIVATE_NON_BYTES")
        wrong_type_result = self.service(
            _ProcessFactory(_FakeProcess(wrong_type_stream))
        ).discover("codex")
        self.assertIs(wrong_type_result.state, ProviderCatalogState.FAILED)
        self.assertTrue(wrong_type_stream.closed)
        self.assertNotIn("PRIVATE_NON_BYTES", repr(wrong_type_result))

        wait_stream = _ChunkStream(b"")
        wait_process = _FakeProcess(
            wait_stream,
            wait_outcomes=(RuntimeError("PRIVATE_WAIT"), 0),
            poll_outcomes=(None,),
        )
        wait_result = self.service(
            _ProcessFactory(wait_process)
        ).discover("codex")
        self.assertIs(wait_result.state, ProviderCatalogState.FAILED)
        self.assertTrue(wait_stream.closed)
        self.assertNotIn("PRIVATE_WAIT", repr(wait_result))

    def test_reader_is_closed_and_joined_when_eof_requires_pipe_close(self) -> None:
        stream = _CloseReleasedStream()
        process = _FakeProcess(stream)
        with mock.patch.object(
            catalog_module,
            "CATALOG_READER_JOIN_TIMEOUT_SECONDS",
            0.01,
        ):
            result = self.service(_ProcessFactory(process)).discover("codex")

        self.assertTrue(stream.started.is_set())
        self.assertTrue(stream.closed)
        self.assertTrue(stream.released.is_set())
        self.assertIs(result.state, ProviderCatalogState.MALFORMED)
        self.assertFalse(
            any(thread.name == "codex-catalog-reader" for thread in threading.enumerate())
        )

    def test_keyboard_interrupt_and_system_exit_propagate_after_cleanup(self) -> None:
        for failure in (KeyboardInterrupt("interrupt"), SystemExit("exit")):
            with self.subTest(failure=type(failure).__name__):
                stream = _ChunkStream(b"")
                process = _FakeProcess(
                    stream,
                    wait_outcomes=(failure, 0),
                    poll_outcomes=(None,),
                )
                with self.assertRaises(type(failure)) as caught:
                    self.service(_ProcessFactory(process)).discover("codex")
                self.assertIs(caught.exception, failure)
                self.assertTrue(stream.closed)
                self.assertIn(("terminate", None), process.events)
                self.assertFalse(
                    any(
                        thread.name == "codex-catalog-reader"
                        for thread in threading.enumerate()
                    )
                )

        reader_failure = KeyboardInterrupt("reader interrupt")
        reader_stream = _ChunkStream(reader_failure)
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.service(
                _ProcessFactory(_FakeProcess(reader_stream))
            ).discover("codex")
        self.assertIs(caught.exception, reader_failure)
        self.assertTrue(reader_stream.closed)

    def test_validation_and_unexpected_parser_failures_are_fixed_and_content_free(
        self,
    ) -> None:
        with self.assertRaises(TypeError):
            ProviderCatalogService("workspace")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ProviderCatalogService(self.workspace_root / "missing")
        for executable in ("", "bad\x00path"):
            with self.subTest(executable=repr(executable)):
                with self.assertRaises(ValueError):
                    ProviderCatalogService(
                        self.workspace_root,
                        codex_executable=executable,
                    )
        with self.assertRaises(ValueError):
            self.service(_ProcessFactory()).discover("unknown")

        stream = _ChunkStream(_codex_bytes([_codex_model("safe")]), b"")
        with mock.patch.object(
            catalog_module,
            "_parse_codex_models",
            side_effect=RuntimeError("PRIVATE_PARSER_FAILURE"),
        ):
            result = self.service(
                _ProcessFactory(_FakeProcess(stream))
            ).discover("codex")
        self.assertIs(result.state, ProviderCatalogState.FAILED)
        self.assertNotIn("PRIVATE_PARSER_FAILURE", repr(result))
        self.assertTrue(stream.closed)

    def test_direct_import_is_clean_standard_library_only_and_cycle_safe(self) -> None:
        source = Path(catalog_module.__file__)
        parsed = ast.parse(source.read_text(encoding="utf-8"))
        self.assertFalse(
            [
                node
                for node in ast.walk(parsed)
                if isinstance(node, ast.ImportFrom) and node.level > 0
            ]
        )

        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = """
import subprocess
import threading

class NoPopen:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    def __new__(cls, *args, **kwargs):
        raise AssertionError("process on import")

subprocess.Popen = NoPopen
before = tuple(threading.enumerate())
from story_writing_agents import provider_catalog as module
assert module.ProviderCatalogState.READY.value == "ready"
assert tuple(threading.enumerate()) == before
print("clean")
"""
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "clean\n")
            self.assertEqual(tuple(Path(temporary).iterdir()), ())


if __name__ == "__main__":
    unittest.main()
