"""Secure bounded rejected-provider-response artifact tests."""

from __future__ import annotations

import ast
import errno
import hashlib
import inspect
import json
import multiprocessing
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import ROSTER
from story_writing_agents import provider_error_artifacts as artifact_module
from story_writing_agents.provider_error_artifacts import (
    PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES,
    PROVIDER_ERROR_ARTIFACT_DIRECTORY,
    PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
    PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
    PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH,
    PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS,
    PROVIDER_ERROR_ARTIFACT_MAX_FILES,
    PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH,
    PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES,
    WorkspaceProviderErrorArtifacts,
)
from story_writing_agents.errors import SessionIdError, WorkspaceSecurityError
from story_writing_agents.workspace import Workspace

from tests.helpers import initialized_workspace


_FIXED_TIME = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=UTC)
_FIXED_UUID = UUID("00112233-4455-4677-8899-aabbccddeeff")
_ARTIFACT_NAME = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\.json")


def _record(
    owner: WorkspaceProviderErrorArtifacts,
    *,
    provider: str = "codex",
    agent_id: str = ROSTER[0],
    session_id: str = "session_1",
    response_text: str = "provider response",
    debate_id: str | None = "debate_1",
    conversation_id: str | None = None,
) -> None:
    """Record one representative rejected response."""

    owner.record_rejected_response(
        provider=provider,
        agent_id=agent_id,
        session_id=session_id,
        response_text=response_text,
        failure_category=PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
        debate_id=debate_id,
        conversation_id=conversation_id,
    )


def _record_process_artifact(workspace: Workspace, session_id: str) -> None:
    """Record one process artifact through a transferred authorized lease."""

    _record(
        WorkspaceProviderErrorArtifacts(workspace),
        session_id=session_id,
        response_text=session_id,
        debate_id="debate",
    )


def _artifact_files(owner: WorkspaceProviderErrorArtifacts) -> list[Path]:
    """Return complete artifact leaves in deterministic order."""

    if not owner.directory.exists():
        return []
    return sorted(
        path
        for path in owner.directory.iterdir()
        if _ARTIFACT_NAME.fullmatch(path.name) is not None
    )


def _records(owner: WorkspaceProviderErrorArtifacts) -> list[dict[str, object]]:
    """Decode all complete artifacts as strict UTF-8 JSON."""

    return [
        json.loads(path.read_text(encoding="utf-8")) for path in _artifact_files(owner)
    ]


def _namespace_snapshot(
    directory: Path,
) -> dict[str, tuple[int, int, int, int, bytes | None]]:
    """Capture names, identities, link counts, and regular-file bytes."""

    if not directory.exists() or not directory.is_dir():
        return {}
    result: dict[str, tuple[int, int, int, int, bytes | None]] = {}
    for path in sorted(directory.iterdir()):
        value = os.stat(path, follow_symlinks=False)
        result[path.name] = (
            value.st_mode,
            value.st_dev,
            value.st_ino,
            value.st_nlink,
            path.read_bytes() if stat.S_ISREG(value.st_mode) else None,
        )
    return result


def _hard_link_or_skip(
    test_case: unittest.TestCase,
    source: Path,
    destination: Path,
) -> None:
    """Create a real hard link or skip only unsupported hosts."""

    unsupported = {
        errno.EACCES,
        errno.EPERM,
        errno.EXDEV,
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        os.link(source, destination)
    except (AttributeError, NotImplementedError) as exc:
        test_case.skipTest(f"hard-link creation is unsupported: {exc}")
    except OSError as exc:
        if exc.errno in unsupported or getattr(exc, "winerror", None) == 1314:
            test_case.skipTest(f"hard-link creation is unsupported: {exc}")
        raise


def _fifo_or_skip(test_case: unittest.TestCase, path: Path) -> None:
    """Create a FIFO or skip only unsupported hosts."""

    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is None:
        test_case.skipTest("FIFO creation is unsupported")
    try:
        mkfifo(path, 0o600)
    except (AttributeError, NotImplementedError) as exc:
        test_case.skipTest(f"FIFO creation is unsupported: {exc}")
    except OSError as exc:
        unsupported = {
            errno.EACCES,
            errno.EPERM,
            errno.EINVAL,
            errno.ENOSYS,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }
        if exc.errno in unsupported:
            test_case.skipTest(f"FIFO creation is unsupported: {exc}")
        raise


class ProviderErrorArtifactContractTests(unittest.TestCase):
    """Verify the exact private schema, bounds, and module boundary."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.owner = WorkspaceProviderErrorArtifacts(self.workspace)

    def test_internal_surface_constants_signature_and_facade_are_exact(self) -> None:
        self.assertEqual(
            artifact_module.__all__,
            [
                "PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES",
                "PROVIDER_ERROR_ARTIFACT_DIRECTORY",
                "PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY",
                "PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES",
                "PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH",
                "PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS",
                "PROVIDER_ERROR_ARTIFACT_MAX_FILES",
                "PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH",
                "PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES",
                "WorkspaceProviderErrorArtifacts",
            ],
        )
        self.assertEqual(
            (
                PROVIDER_ERROR_ARTIFACT_DIRECTORY,
                PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH,
                PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH,
                PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES,
                PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
                PROVIDER_ERROR_ARTIFACT_MAX_FILES,
                PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES,
                PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS,
                PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
            ),
            (
                "debug_logs/provider_error_responses",
                "debug_logs/provider_error_responses/.lock",
                "debug_logs/provider_error_responses/.pending",
                262_144,
                2_097_152,
                32,
                16_777_216,
                0.1,
                "structured_response_rejected",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    WorkspaceProviderErrorArtifacts.record_rejected_response
                ).parameters
            ),
            (
                "self",
                "provider",
                "agent_id",
                "session_id",
                "response_text",
                "failure_category",
                "debate_id",
                "conversation_id",
            ),
        )
        for name in artifact_module.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)

    def test_construction_and_directory_property_are_io_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = Workspace(root)
            before = tuple(root.iterdir())
            owner = WorkspaceProviderErrorArtifacts(workspace)
            self.assertEqual(
                owner.directory,
                workspace.root / PROVIDER_ERROR_ARTIFACT_DIRECTORY,
            )
            self.assertEqual(tuple(root.iterdir()), before)
            with self.assertRaises(TypeError):
                WorkspaceProviderErrorArtifacts(root)  # type: ignore[arg-type]
            self.assertEqual(tuple(root.iterdir()), before)

    def test_debate_and_conversation_schema_serialization_and_names_are_exact(
        self,
    ) -> None:
        fixed_datetime = mock.Mock(wraps=datetime)
        fixed_datetime.now.return_value = _FIXED_TIME
        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(artifact_module, "uuid4", return_value=_FIXED_UUID),
        ):
            _record(self.owner, response_text="응답🙂\n", debate_id="debate_7")

        path = _artifact_files(self.owner)[0]
        self.assertEqual(
            path.name,
            "20260827T010203456789Z-00112233445546778899aabbccddeeff.json",
        )
        expected = {
            "schema_version": 1,
            "timestamp": "2026-08-27T01:02:03.456+00:00",
            "provider": "codex",
            "agent_id": ROSTER[0],
            "session_id": "session_1",
            "debate_id": "debate_7",
            "failure_category": "structured_response_rejected",
            "response_utf8_bytes": len("응답🙂\n".encode()),
            "retained_utf8_bytes": len("응답🙂\n".encode()),
            "response_sha256": hashlib.sha256("응답🙂\n".encode()).hexdigest(),
            "truncated": False,
            "body": "응답🙂\n",
        }
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)
        self.assertEqual(
            path.read_bytes(),
            json.dumps(
                expected,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            + b"\n",
        )
        self.assertNotIn(b"\\u", path.read_bytes())

        conversation_workspace = initialized_workspace(self)
        conversation_owner = WorkspaceProviderErrorArtifacts(conversation_workspace)
        _record(
            conversation_owner,
            provider="antigravity",
            agent_id=ROSTER[-1],
            session_id="S-9",
            response_text="conversation",
            debate_id=None,
            conversation_id="conversation_9",
        )
        record = _records(conversation_owner)[0]
        self.assertEqual(record["provider"], "antigravity")
        self.assertEqual(record["conversation_id"], "conversation_9")
        self.assertNotIn("debate_id", record)

    def test_providers_roster_and_existing_identifier_grammar_are_accepted(
        self,
    ) -> None:
        for index, agent_id in enumerate(ROSTER):
            _record(
                self.owner,
                provider="codex" if index % 2 == 0 else "antigravity",
                agent_id=agent_id,
                session_id="s" + "a" * 63,
                response_text=f"response-{index}",
                debate_id=None if index % 2 else "d" + "_" * 63,
                conversation_id="c" + "-" * 63 if index % 2 else None,
            )
        records = _records(self.owner)
        self.assertEqual({record["agent_id"] for record in records}, set(ROSTER))
        self.assertEqual(
            {record["provider"] for record in records}, {"codex", "antigravity"}
        )

    def test_invalid_inputs_raise_before_storage_and_leak_no_values(self) -> None:
        calls = (
            (ValueError, {"provider": "gemini"}),
            (ValueError, {"provider": b"codex"}),
            (ValueError, {"agent_id": "not_an_agent"}),
            (TypeError, {"agent_id": 1}),
            (SessionIdError, {"session_id": "../PRIVATE_SESSION"}),
            (TypeError, {"response_text": b"PRIVATE_RESPONSE"}),
            (ValueError, {"failure_category": "PRIVATE_CATEGORY"}),
            (ValueError, {"debate_id": "debate", "conversation_id": "conversation"}),
            (SessionIdError, {"debate_id": "../PRIVATE_DEBATE"}),
        )
        for expected_type, overrides in calls:
            with self.subTest(overrides=overrides):
                arguments: dict[str, object] = {
                    "provider": "codex",
                    "agent_id": ROSTER[0],
                    "session_id": "session",
                    "response_text": "response",
                    "failure_category": PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
                    "debate_id": "debate",
                    "conversation_id": None,
                }
                arguments.update(overrides)
                with self.assertRaises(expected_type) as raised:
                    self.owner.record_rejected_response(**arguments)  # type: ignore[arg-type]
                self.assertNotIn("PRIVATE_RESPONSE", str(raised.exception))
        self.assertFalse(self.owner.directory.exists())

    def test_hostile_plaintext_round_trips_without_redaction_or_rendering(self) -> None:
        body = 'NUL:\0 ESC:\x1b quote:" slash:/ backslash:\\\nRTL:\u202e 한글 🙂'
        _record(self.owner, response_text=body)
        path = _artifact_files(self.owner)[0]
        record = _records(self.owner)[0]
        encoded = body.encode("utf-8")
        self.assertEqual(record["body"], body)
        self.assertEqual(record["response_utf8_bytes"], len(encoded))
        self.assertEqual(record["retained_utf8_bytes"], len(encoded))
        self.assertEqual(record["response_sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertLessEqual(
            path.stat().st_size, PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES
        )

    def test_body_bound_digest_and_codepoint_safe_truncation_are_exact(self) -> None:
        exact = "a" * PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES
        _record(self.owner, response_text=exact, session_id="exact")
        exact_record = next(
            record for record in _records(self.owner) if record["session_id"] == "exact"
        )
        self.assertIs(exact_record["truncated"], False)
        self.assertEqual(
            exact_record["retained_utf8_bytes"], PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES
        )

        full = "a" * (PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES - 1) + "🙂tail"
        _record(self.owner, response_text=full, session_id="split")
        split = next(
            record for record in _records(self.owner) if record["session_id"] == "split"
        )
        retained = str(split["body"]).encode("utf-8")
        self.assertEqual(retained, b"a" * (PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES - 1))
        self.assertTrue(split["truncated"])
        self.assertEqual(split["response_utf8_bytes"], len(full.encode()))
        self.assertEqual(split["retained_utf8_bytes"], len(retained))
        self.assertEqual(
            split["response_sha256"], hashlib.sha256(full.encode()).hexdigest()
        )

    def test_serialized_cap_is_checked_before_storage(self) -> None:
        fixed_datetime = mock.Mock(wraps=datetime)
        fixed_datetime.now.return_value = _FIXED_TIME
        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(WorkspaceProviderErrorArtifacts, "_store") as capture,
        ):
            _record(self.owner)
        payload = capture.call_args.args[0]

        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(
                artifact_module,
                "PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES",
                len(payload),
            ),
            mock.patch.object(
                WorkspaceProviderErrorArtifacts,
                "_store",
            ) as exact_store,
        ):
            _record(self.owner)
        exact_store.assert_called_once()

        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(
                artifact_module,
                "PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES",
                len(payload) - 1,
            ),
            mock.patch.object(WorkspaceProviderErrorArtifacts, "_store") as store,
            self.assertRaisesRegex(ValueError, "serialized bound"),
        ):
            _record(self.owner)
        store.assert_not_called()
        self.assertFalse(self.owner.directory.exists())

    def test_clean_import_dependency_direction_and_facade_isolation(self) -> None:
        source_path = Path(artifact_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertEqual(dependencies, {"agents", "workspace"})
        source_root = source_path.parent
        workspace_package = source_root / "workspace"
        filesystem_owner = source_root / "conversation_storage" / "filesystem.py"
        dependency_paths = {
            "agents": (source_root / "agents.py",),
            "workspace": (
                workspace_package / "__init__.py",
                workspace_package / "core.py",
                filesystem_owner,
            ),
        }
        self.assertFalse((source_root / "workspace.py").exists())
        for dependency, paths in dependency_paths.items():
            with self.subTest(dependency=dependency):
                for path in paths:
                    self.assertTrue(path.is_file(), path)
                    dependency_source = path.read_text(encoding="utf-8")
                    self.assertNotIn(
                        "from .provider_error_artifacts import",
                        dependency_source,
                    )
                    self.assertNotIn(
                        "from ..provider_error_artifacts import",
                        dependency_source,
                    )

        facade_tree = ast.parse(
            (workspace_package / "__init__.py").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                (node.level, node.module)
                for node in ast.walk(facade_tree)
                if isinstance(node, ast.ImportFrom) and node.level > 0
            },
            {(1, "core"), (2, "conversation_storage.filesystem")},
        )
        core_tree = ast.parse(
            (workspace_package / "core.py").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                node.module
                for node in ast.walk(core_tree)
                if isinstance(node, ast.ImportFrom) and node.level == 2
            },
            {"agents", "conversations", "errors", "models", "writing_artifacts"},
        )
        owner_tree = ast.parse(filesystem_owner.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                node.module
                for node in ast.walk(owner_tree)
                if isinstance(node, ast.ImportFrom) and node.level == 2
            },
            {"conversations", "conversations.events", "errors", "workspace.core"},
        )

        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "from pathlib import Path; import story_writing_agents as facade; "
            "from story_writing_agents import provider_error_artifacts as module; "
            "from story_writing_agents import workspace; "
            "from story_writing_agents.workspace import core; "
            "from story_writing_agents.conversation_storage import filesystem; "
            "assert all(name not in facade.__all__ for name in module.__all__); "
            "assert module.Workspace is workspace.Workspace is core.Workspace; "
            "assert workspace.FilesystemConversationCatalog is "
            "filesystem.FilesystemConversationCatalog; "
            "assert not hasattr(workspace, 'FilesystemConversationEventStore'); "
            "assert not hasattr(filesystem, 'FilesystemConversationEventStore'); "
            "assert module.open_workspace_root_directory is "
            "workspace.open_workspace_root_directory; "
            "assert module.verify_workspace_root_directory is "
            "workspace.verify_workspace_root_directory; "
            "assert list(Path.cwd().iterdir()) == []; print('ok')"
        )
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
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])


class ProviderErrorArtifactStorageTests(unittest.TestCase):
    """Verify retention, concurrency, containment, and cleanup."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.owner = WorkspaceProviderErrorArtifacts(self.workspace)

    def test_first_write_stale_pending_and_durable_complete_state(self) -> None:
        _record(self.owner, session_id="first")
        directory = self.owner.directory
        self.assertTrue(directory.is_dir())
        self.assertTrue(
            self.workspace.path(PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH).is_file()
        )
        self.assertFalse(
            self.workspace.path(PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH).exists()
        )
        self.assertEqual(
            [record["session_id"] for record in _records(self.owner)], ["first"]
        )

        pending = self.workspace.path(PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH)
        pending.write_bytes(b"crash residue")
        _record(self.owner, session_id="recovered")
        self.assertFalse(pending.exists())
        self.assertEqual(
            {record["session_id"] for record in _records(self.owner)},
            {"first", "recovered"},
        )

    def test_count_and_total_byte_retention_prune_oldest_filename_first(self) -> None:
        times = [_FIXED_TIME + timedelta(seconds=index) for index in range(4)]
        fixed_datetime = mock.Mock(wraps=datetime)
        fixed_datetime.now.side_effect = times
        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(artifact_module, "PROVIDER_ERROR_ARTIFACT_MAX_FILES", 3),
        ):
            for index in range(4):
                _record(
                    self.owner,
                    session_id=f"count_{index}",
                    response_text=f"body-{index}",
                )
        records = _records(self.owner)
        self.assertEqual(
            [record["session_id"] for record in records],
            ["count_1", "count_2", "count_3"],
        )
        self.assertLessEqual(len(records), 3)

        byte_workspace = initialized_workspace(self)
        byte_owner = WorkspaceProviderErrorArtifacts(byte_workspace)
        _record(byte_owner, session_id="byte_0", response_text="same-0")
        _record(byte_owner, session_id="byte_1", response_text="same-1")
        current_files = _artifact_files(byte_owner)
        exact_total = sum(path.stat().st_size for path in current_files)
        with mock.patch.object(
            artifact_module,
            "PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES",
            exact_total,
        ):
            _record(byte_owner, session_id="byte_2", response_text="same-2")
            files = _artifact_files(byte_owner)
            self.assertLessEqual(
                sum(path.stat().st_size for path in files), exact_total
            )
        byte_records = _records(byte_owner)
        self.assertNotIn("byte_0", {record["session_id"] for record in byte_records})
        self.assertIn("byte_2", {record["session_id"] for record in byte_records})

    def test_filename_collisions_retry_boundedly_without_overwrite(self) -> None:
        fixed_datetime = mock.Mock(wraps=datetime)
        fixed_datetime.now.return_value = _FIXED_TIME
        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(artifact_module, "uuid4", return_value=_FIXED_UUID),
        ):
            _record(self.owner, session_id="original")
        original_path = _artifact_files(self.owner)[0]
        original_bytes = original_path.read_bytes()
        next_uuid = UUID("11112233-4455-4677-8899-aabbccddeeff")
        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(
                artifact_module, "uuid4", side_effect=[_FIXED_UUID, next_uuid]
            ),
        ):
            _record(self.owner, session_id="retried")
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(len(_artifact_files(self.owner)), 2)

        before = _namespace_snapshot(self.owner.directory)
        with (
            mock.patch.object(artifact_module, "datetime", fixed_datetime),
            mock.patch.object(artifact_module, "uuid4", return_value=_FIXED_UUID),
            self.assertRaises(FileExistsError),
        ):
            _record(self.owner, session_id="exhausted")
        self.assertEqual(_namespace_snapshot(self.owner.directory), before)

    def test_atomic_publication_race_never_replaces_destination(self) -> None:
        real_rename = artifact_module._rename_noreplace
        raced: dict[str, Path] = {}

        def insert_destination(
            source_directory: int,
            source: str,
            destination_directory: int,
            destination: str,
        ) -> None:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=destination_directory,
            )
            try:
                os.write(descriptor, b"outside collision bytes")
            finally:
                os.close(descriptor)
            raced["path"] = self.owner.directory / destination
            real_rename(source_directory, source, destination_directory, destination)

        with (
            mock.patch.object(
                artifact_module, "_rename_noreplace", side_effect=insert_destination
            ),
            self.assertRaises(FileExistsError),
        ):
            _record(self.owner, session_id="raced")
        self.assertEqual(raced["path"].read_bytes(), b"outside collision bytes")
        self.assertTrue(
            self.workspace.path(PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH).is_file()
        )

    def test_guard_create_loser_opens_and_verifies_descriptor_relative_winner(
        self,
    ) -> None:
        real_open = os.open
        observations: dict[str, object] = {}
        guard_calls: list[tuple[int, int | None]] = []

        def create_winner_then_raise(
            filename: str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if filename != artifact_module._GUARD_FILENAME:
                return real_open(filename, flags, mode, dir_fd=dir_fd)
            guard_calls.append((flags, dir_fd))
            if flags & os.O_EXCL:
                winner = real_open(filename, flags, mode, dir_fd=dir_fd)
                try:
                    metadata = os.fstat(winner)
                    observations["winner_identity"] = (
                        metadata.st_dev,
                        metadata.st_ino,
                    )
                finally:
                    os.close(winner)
                raise FileExistsError(
                    errno.EEXIST,
                    "simulated guard create winner",
                    filename,
                )
            fallback = real_open(filename, flags, mode, dir_fd=dir_fd)
            metadata = os.fstat(fallback)
            observations["fallback_identity"] = (
                metadata.st_dev,
                metadata.st_ino,
            )
            observations["fallback_descriptor"] = fallback
            return fallback

        directories = artifact_module._prepare_directories(self.workspace)
        real_verify = artifact_module._verify_open_file
        with (
            mock.patch.object(
                artifact_module,
                "_prepare_directories",
                return_value=directories,
            ) as prepare,
            mock.patch.object(
                artifact_module.os,
                "open",
                side_effect=create_winner_then_raise,
            ),
            mock.patch.object(
                artifact_module,
                "_verify_open_file",
                wraps=real_verify,
            ) as verify,
        ):
            _record(self.owner, session_id="guard_create_winner")

        prepare.assert_called_once_with(self.workspace)
        self.assertEqual(len(guard_calls), 2)
        create_flags, create_parent = guard_calls[0]
        fallback_flags, fallback_parent = guard_calls[1]
        self.assertIsNotNone(create_parent)
        self.assertEqual(fallback_parent, create_parent)
        self.assertEqual(
            create_flags & (os.O_CREAT | os.O_EXCL), os.O_CREAT | os.O_EXCL
        )
        self.assertEqual(fallback_flags & (os.O_CREAT | os.O_EXCL), 0)
        self.assertEqual(
            observations["fallback_identity"],
            observations["winner_identity"],
        )
        guard_verifications = [
            call
            for call in verify.call_args_list
            if call.args[2] == artifact_module._GUARD_FILENAME
        ]
        self.assertTrue(guard_verifications)
        self.assertEqual(
            {call.args[0] for call in guard_verifications},
            {observations["fallback_descriptor"]},
        )
        guard = self.workspace.path(PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH)
        metadata = guard.stat(follow_symlinks=False)
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(metadata.st_nlink, 1)
        self.assertEqual(
            [record["session_id"] for record in _records(self.owner)],
            ["guard_create_winner"],
        )

    def test_threads_and_processes_leave_only_complete_unique_artifacts(self) -> None:
        barrier = threading.Barrier(9)
        attempted = {f"thread_{index}" for index in range(8)}
        failures: list[BaseException] = []

        def write(session_id: str) -> None:
            try:
                barrier.wait(timeout=5)
                _record(
                    WorkspaceProviderErrorArtifacts(self.workspace),
                    session_id=session_id,
                )
            except BaseException as exc:
                failures.append(exc)

        threads = [
            threading.Thread(target=write, args=(session,)) for session in attempted
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        thread_records = _records(self.owner)
        persisted = [str(record["session_id"]) for record in thread_records]
        self.assertTrue(persisted)
        self.assertEqual(len(persisted), len(set(persisted)))
        self.assertTrue(set(persisted).issubset(attempted))

        process_workspace = initialized_workspace(self)
        process_attempted = {f"process_{index}" for index in range(6)}
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(
                target=_record_process_artifact,
                args=(process_workspace, session),
            )
            for session in process_attempted
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)
            alive = tuple(process.is_alive() for process in processes)
            exitcodes = tuple(process.exitcode for process in processes)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                process.close()
        self.assertEqual(alive, (False,) * len(processes))
        self.assertEqual(exitcodes, (0,) * len(processes))
        process_owner = WorkspaceProviderErrorArtifacts(process_workspace)
        process_records = _records(process_owner)
        persisted = [str(record["session_id"]) for record in process_records]
        self.assertTrue(persisted)
        self.assertEqual(len(persisted), len(set(persisted)))
        self.assertTrue(set(persisted).issubset(process_attempted))

    def test_process_and_advisory_contention_drop_promptly_and_recover(self) -> None:
        holder = artifact_module._process_lock_for(self.workspace)
        holder.lock.acquire()
        started = time.monotonic()
        try:
            with mock.patch.object(
                artifact_module,
                "PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS",
                0.02,
            ):
                _record(self.owner, session_id="process_dropped")
        finally:
            holder.lock.release()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(self.owner.directory.exists())
        _record(self.owner, session_id="process_recovered")

        if artifact_module._fcntl is None:
            self.skipTest("real advisory contention requires fcntl")
        guard = self.workspace.path(PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH)
        descriptor = os.open(guard, os.O_RDWR)
        try:
            artifact_module._fcntl.flock(descriptor, artifact_module._fcntl.LOCK_EX)
            started = time.monotonic()
            with mock.patch.object(
                artifact_module,
                "PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS",
                0.02,
            ):
                _record(self.owner, session_id="advisory_dropped")
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            artifact_module._fcntl.flock(descriptor, artifact_module._fcntl.LOCK_UN)
            os.close(descriptor)
        _record(self.owner, session_id="advisory_recovered")
        sessions = {record["session_id"] for record in _records(self.owner)}
        self.assertNotIn("process_dropped", sessions)
        self.assertNotIn("advisory_dropped", sessions)
        self.assertIn("advisory_recovered", sessions)

    def test_staging_fsync_failure_never_publishes_partial_and_recovers(self) -> None:
        real_fsync = os.fsync
        failed = False

        def fail_first_file_fsync(descriptor: int) -> None:
            nonlocal failed
            value = os.fstat(descriptor)
            if stat.S_ISREG(value.st_mode) and value.st_size > 1 and not failed:
                failed = True
                raise OSError("fixed staging sync failure")
            real_fsync(descriptor)

        with (
            mock.patch.object(
                artifact_module.os, "fsync", side_effect=fail_first_file_fsync
            ),
            self.assertRaisesRegex(OSError, "fixed staging sync failure") as raised,
        ):
            _record(self.owner, session_id="PRIVATE_RESPONSE_NOT_IN_ERROR")
        self.assertNotIn("PRIVATE_RESPONSE", str(raised.exception))
        self.assertEqual(_artifact_files(self.owner), [])
        self.assertTrue(
            self.workspace.path(PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH).is_file()
        )

        _record(self.owner, session_id="recovered")
        self.assertFalse(
            self.workspace.path(PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH).exists()
        )
        self.assertEqual(
            [record["session_id"] for record in _records(self.owner)], ["recovered"]
        )

    def test_unsafe_leaf_states_fail_closed_without_mutating_targets(self) -> None:
        complete_name = "20260827T010203456789Z-00112233445546778899aabbccddeeff.json"
        candidates = (".lock", ".pending", complete_name)
        for kind in ("symlink", "hardlink", "directory"):
            for candidate in candidates:
                with self.subTest(kind=kind, candidate=candidate):
                    workspace = initialized_workspace(self)
                    owner = WorkspaceProviderErrorArtifacts(workspace)
                    owner.directory.mkdir()
                    outside = (
                        workspace.root.parent
                        / f"outside-{kind}-{candidate.replace('.', 'x')}"
                    )
                    if kind == "directory":
                        (owner.directory / candidate).mkdir()
                        outside_before = None
                    else:
                        outside.write_bytes(b"outside immutable bytes")
                        if kind == "symlink":
                            (owner.directory / candidate).symlink_to(outside)
                        else:
                            _hard_link_or_skip(
                                self, outside, owner.directory / candidate
                            )
                        outside_before = (
                            outside.read_bytes(),
                            os.stat(outside, follow_symlinks=False).st_nlink,
                        )
                    before = _namespace_snapshot(owner.directory)
                    with self.assertRaises((OSError, ValueError, UnicodeError)):
                        _record(owner)
                    self.assertEqual(_namespace_snapshot(owner.directory), before)
                    if outside_before is not None:
                        self.assertEqual(
                            (
                                outside.read_bytes(),
                                os.stat(outside, follow_symlinks=False).st_nlink,
                            ),
                            outside_before,
                        )
                    if outside.exists():
                        outside.unlink()

    def test_fifo_oversized_unsafe_and_malformed_states_fail_closed(self) -> None:
        cases = ("fifo", "oversized_pending", "unsafe_name", "malformed_complete")
        for case in cases:
            with self.subTest(case=case):
                workspace = initialized_workspace(self)
                owner = WorkspaceProviderErrorArtifacts(workspace)
                owner.directory.mkdir()
                if case == "fifo":
                    _fifo_or_skip(self, owner.directory / ".pending")
                elif case == "oversized_pending":
                    (owner.directory / ".pending").write_bytes(
                        b"x" * (PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES + 1)
                    )
                elif case == "unsafe_name":
                    (owner.directory / "unexpected").write_bytes(b"unsafe")
                else:
                    (
                        owner.directory
                        / "20260827T010203456789Z-00112233445546778899aabbccddeeff.json"
                    ).write_bytes(b"{}\n")
                before = _namespace_snapshot(owner.directory)
                started = time.monotonic()
                with self.assertRaises((OSError, ValueError, UnicodeError)):
                    _record(owner)
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(_namespace_snapshot(owner.directory), before)

    def test_workspace_and_artifact_directory_replacement_cannot_redirect(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.workspace.root.parent) as temporary:
            temporary_path = Path(temporary)
            original_root = self.workspace.root
            parked = temporary_path / "parked-root"
            replacement = temporary_path / "replacement-root"
            os.rename(original_root, parked)
            replacement.mkdir()
            (replacement / "debug_logs").mkdir()
            original_root.symlink_to(replacement, target_is_directory=True)
            try:
                with self.assertRaises(WorkspaceSecurityError):
                    _record(self.owner, session_id="root_race")
                self.assertEqual(tuple((replacement / "debug_logs").iterdir()), ())
            finally:
                original_root.unlink()
                os.rename(parked, original_root)

        outside = self.workspace.root.parent / "outside-artifact-directory"
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        real_scan = artifact_module._scan_artifact_directory
        swapped = False
        parked_directory = self.workspace.root.parent / "parked-artifact-directory"

        def swap_then_scan(directories: object):
            nonlocal swapped
            if not swapped:
                os.rename(self.owner.directory, parked_directory)
                self.owner.directory.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_scan(directories)

        try:
            with (
                mock.patch.object(
                    artifact_module,
                    "_scan_artifact_directory",
                    side_effect=swap_then_scan,
                ),
                self.assertRaises((OSError, ValueError)),
            ):
                _record(self.owner, session_id="directory_race")
            self.assertEqual(tuple(outside.iterdir()), ())
        finally:
            if swapped:
                self.owner.directory.unlink()
                os.rename(parked_directory, self.owner.directory)

    def test_unsupported_primitives_and_process_controls_fail_with_cleanup(
        self,
    ) -> None:
        before = tuple(self.workspace.path("debug_logs").iterdir())
        with (
            mock.patch.object(artifact_module.os, "O_NOFOLLOW", 0),
            self.assertRaises(OSError),
        ):
            _record(self.owner, session_id="unsupported")
        self.assertEqual(tuple(self.workspace.path("debug_logs").iterdir()), before)

        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type.__name__):
                workspace = initialized_workspace(self)
                owner = WorkspaceProviderErrorArtifacts(workspace)
                control = exception_type("PRIVATE_PROCESS_CONTROL")
                captured: dict[str, object] = {}
                real_prepare = artifact_module._prepare_directories
                real_open_guard = artifact_module._open_guard

                def capture_directories(value: Workspace):
                    directories = real_prepare(value)
                    captured["directories"] = directories
                    return directories

                def capture_guard(directories: object) -> int:
                    descriptor = real_open_guard(directories)
                    captured["guard"] = descriptor
                    return descriptor

                with (
                    mock.patch.object(
                        artifact_module,
                        "_prepare_directories",
                        side_effect=capture_directories,
                    ),
                    mock.patch.object(
                        artifact_module, "_open_guard", side_effect=capture_guard
                    ),
                    mock.patch.object(
                        artifact_module, "_publish_under_guard", side_effect=control
                    ),
                    self.assertRaises(exception_type) as raised,
                ):
                    _record(owner)
                self.assertIs(raised.exception, control)
                directories = captured["directories"]
                for descriptor in (
                    captured["guard"],
                    directories.artifact_descriptor,  # type: ignore[attr-defined]
                    directories.debug_descriptor,  # type: ignore[attr-defined]
                    directories.root_descriptor,  # type: ignore[attr-defined]
                ):
                    with self.assertRaises(OSError) as closed:
                        os.fstat(descriptor)  # type: ignore[arg-type]
                    self.assertEqual(closed.exception.errno, errno.EBADF)
                lock = artifact_module._process_lock_for(workspace)
                self.assertTrue(lock.lock.acquire(blocking=False))
                lock.lock.release()
                _record(owner, session_id="recovered")
                self.assertEqual(
                    [record["session_id"] for record in _records(owner)], ["recovered"]
                )


if __name__ == "__main__":
    unittest.main()
