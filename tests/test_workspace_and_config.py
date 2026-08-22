"""Agent metadata, workspace, cascading config, and directive contracts."""

from __future__ import annotations

import errno
import inspect
import json
import os
import pickle
import stat
import tempfile
import traceback
import unittest
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    DebateContextMode,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
    StoryRuntime,
    Workspace,
    initialize_workspace,
    parse_directive,
)
from story_writing_agents import workspace as workspace_module
from story_writing_agents.agents import CORE_DOCUMENTS
from story_writing_agents.config import (
    AgentConfig,
    ConfigurationSnapshot,
    ConfigManager,
    SystemDefaults,
)
from story_writing_agents.directives import DirectiveKind
from story_writing_agents.errors import (
    ConfigurationError,
    DebateExistsError,
    DirectiveError,
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.workspace import (
    DEFAULT_AGENT_PROMPTS,
    DEFAULT_SYSTEM_CONFIG,
    WORKSPACE_DIRECTORIES,
)

from tests.helpers import initialized_workspace


_CONTEXT_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def snapshot_directory_namespace(directory: Path) -> dict[str, bytes | None]:
    """Return every descendant name and file byte sequence below a directory."""

    return {
        path.relative_to(directory).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(directory.rglob("*"))
    }


class _PendingDescriptorTracker:
    """Observe pending-root descriptor ordering without mocking filesystem state."""

    def __init__(
        self,
        root: Path,
        *,
        set_failure: Callable[[str], BaseException | None] | None = None,
    ) -> None:
        self._root = root
        self._set_failure = set_failure
        self._real_open = os.open
        self._real_mkdir = os.mkdir
        self._real_fstat = os.fstat
        self._real_close = os.close
        self._real_set_inheritable = os.set_inheritable
        self._next_token = 0
        self._live: dict[int, int] = {}
        self._paths: dict[int, str] = {}
        self._final_closed = False
        self.active = True
        self.events: list[tuple[object, ...]] = []
        self.opened: list[int] = []
        self.closed: list[int] = []
        self.double_closes: list[int] = []

    def open(
        self,
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if self.active and dir_fd in self._live:
            self.events.append(("use", self._live[dir_fd], "open"))
        descriptor = self._real_open(path, flags, mode, dir_fd=dir_fd)
        if not self.active:
            return descriptor
        token = self._next_token
        self._next_token += 1
        self._live[descriptor] = token
        self._paths[token] = os.fspath(path)
        self.opened.append(token)
        self.events.append(("open", token))
        return descriptor

    def mkdir(
        self,
        path: object,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if self.active and dir_fd in self._live:
            self.events.append(("use", self._live[dir_fd], "mkdir"))
        self._real_mkdir(path, mode, dir_fd=dir_fd)

    def fstat(self, descriptor: int) -> os.stat_result:
        if self.active and descriptor in self._live:
            self.events.append(("use", self._live[descriptor], "fstat"))
        return self._real_fstat(descriptor)

    def set_inheritable(self, descriptor: int, inheritable: bool) -> None:
        if not self.active or descriptor not in self._live:
            self._real_set_inheritable(descriptor, inheritable)
            return
        token = self._live[descriptor]
        self.events.append(("set_inheritable", token, inheritable))
        if self._set_failure is not None:
            failure = self._set_failure(self._paths[token])
            if failure is not None:
                raise failure
        self._real_set_inheritable(descriptor, inheritable)

    def close(self, descriptor: int) -> None:
        if not self.active or descriptor not in self._live:
            if self.active:
                self.double_closes.append(descriptor)
            self._real_close(descriptor)
            return
        token = self._live.pop(descriptor)
        self._real_close(descriptor)
        self.closed.append(token)
        self.events.append(("close", token))
        if self._paths[token] == self._root.name:
            self._final_closed = True
        if self._final_closed and not self._live:
            self.active = False


def debate_context_window(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
) -> ConversationContextWindow:
    """Return one canonical complete turn for sidecar persistence tests."""

    events = (
        ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            "turn_a",
            1,
            _CONTEXT_TIMESTAMP,
            "user",
            PLOT_ARCHITECT,
            ConversationEventKind.USER_MESSAGE,
            "question",
        ),
        ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            "turn_a",
            2,
            _CONTEXT_TIMESTAMP,
            PLOT_ARCHITECT,
            "user",
            ConversationEventKind.AGENT_REPLY,
            "answer",
        ),
    )
    return ConversationContextWindow(
        events=events,
        total_complete_turns=3,
        retained_turns=1,
        omitted_turns=2,
        retained_utf8_bytes=sum(
            len(event.content.encode("utf-8")) for event in events
        ),
        truncated=True,
    )


class AgentRosterTests(unittest.TestCase):
    def test_exact_four_agent_roster_and_specialist_metadata(self) -> None:
        self.assertEqual(
            ROSTER,
            (
                LEAD_ORCHESTRATOR,
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        self.assertEqual(
            SPECIALIST_IDS,
            (
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        self.assertEqual(set(AGENT_SPECS), set(ROSTER))
        self.assertFalse(AGENT_SPECS[LEAD_ORCHESTRATOR].votes)
        self.assertEqual(
            AGENT_SPECS[LEAD_ORCHESTRATOR].context_documents,
            CORE_DOCUMENTS,
        )
        expected_context = {
            PLOT_ARCHITECT: ("global_outline.md", "part_outlines.md"),
            CHARACTER_CUSTODIAN: ("character_sheets.md",),
            EXECUTION_SPECIALIST: ("narrative_rules.md",),
        }
        expected_prompt_files = {
            agent_id: f"config/agents/{agent_id}/AGENTS.md"
            for agent_id in ROSTER
        }
        self.assertEqual(
            {
                agent_id: AGENT_SPECS[agent_id].system_prompt_file
                for agent_id in ROSTER
            },
            expected_prompt_files,
        )
        for agent_id in SPECIALIST_IDS:
            with self.subTest(agent_id=agent_id):
                self.assertTrue(AGENT_SPECS[agent_id].votes)
                self.assertEqual(
                    AGENT_SPECS[agent_id].context_documents,
                    expected_context[agent_id],
                )


class WorkspaceTests(unittest.TestCase):
    def assert_identity_unavailable(
        self,
        action: Callable[[], object],
    ) -> OSError:
        """Require the exact public error for an unbound workspace root."""

        expected_errno = getattr(errno, "ENOTSUP", errno.ENOSYS)
        with self.assertRaises(OSError) as raised:
            action()
        error = raised.exception
        self.assertIs(type(error), OSError)
        self.assertEqual(
            error.args,
            (expected_errno, "workspace root identity is unavailable"),
        )
        self.assertEqual(error.errno, expected_errno)
        self.assertEqual(
            error.strerror,
            "workspace root identity is unavailable",
        )
        return error

    def assert_terminal_initialize_has_no_io(
        self,
        workspace: Workspace,
    ) -> OSError:
        """Require a failed pending bind to reject retry before every I/O seam."""

        with (
            mock.patch.object(
                workspace_module.os,
                "open",
                side_effect=AssertionError("terminal retry reached os.open"),
            ) as opened,
            mock.patch.object(
                workspace_module.os,
                "mkdir",
                side_effect=AssertionError("terminal retry reached os.mkdir"),
            ) as created,
            mock.patch.object(
                workspace_module.os,
                "fstat",
                side_effect=AssertionError("terminal retry reached os.fstat"),
            ) as opened_stat,
            mock.patch.object(
                workspace_module.os,
                "stat",
                side_effect=AssertionError("terminal retry reached os.stat"),
            ) as named_stat,
            mock.patch.object(
                workspace_module.os,
                "set_inheritable",
                side_effect=AssertionError(
                    "terminal retry reached os.set_inheritable"
                ),
            ) as set_inheritable,
            mock.patch.object(
                workspace,
                "_directory",
                side_effect=AssertionError("terminal retry reached child I/O"),
            ) as child,
            mock.patch.object(
                workspace,
                "_ensure_json",
                side_effect=AssertionError("terminal retry reached defaults I/O"),
            ) as ensure_json,
            mock.patch.object(
                workspace,
                "_ensure_text",
                side_effect=AssertionError("terminal retry reached scaffold I/O"),
            ) as ensure_text,
        ):
            error = self.assert_identity_unavailable(workspace.initialize)
        for seam in (
            opened,
            created,
            opened_stat,
            named_stat,
            set_inheritable,
            child,
            ensure_json,
            ensure_text,
        ):
            seam.assert_not_called()
        return error

    def assert_originating_traceback(
        self,
        failure: BaseException,
        function_name: str,
    ) -> None:
        """Require direct propagation to retain its injection-frame traceback."""

        self.assertIn(
            function_name,
            {
                frame.name
                for frame in traceback.extract_tb(failure.__traceback__)
            },
        )

    def test_root_identity_seams_preserve_public_surface_and_verify_existing_root(
        self,
    ) -> None:
        self.assertEqual(
            tuple(inspect.signature(Workspace.__init__).parameters),
            ("self", "root"),
        )
        self.assertEqual(
            tuple(inspect.signature(Workspace.initialize).parameters),
            ("self", "overwrite_prompts"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    workspace_module.open_workspace_root_directory
                ).parameters
            ),
            ("workspace",),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    workspace_module.verify_workspace_root_directory
                ).parameters
            ),
            ("workspace", "descriptor"),
        )
        for internal_name in (
            "open_workspace_root_directory",
            "verify_workspace_root_directory",
        ):
            self.assertNotIn(internal_name, story_writing_agents.__all__)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            workspace = Workspace(root)
            expected = os.stat(root, follow_symlinks=False)
            descriptor = workspace_module.open_workspace_root_directory(workspace)
            try:
                workspace_module.verify_workspace_root_directory(
                    workspace,
                    descriptor,
                )
                opened = os.fstat(descriptor)
                self.assertEqual(
                    (opened.st_dev, opened.st_ino),
                    (expected.st_dev, expected.st_ino),
                )
                self.assertTrue(stat.S_ISDIR(opened.st_mode))
            finally:
                os.close(descriptor)

    def test_existing_root_identity_rejects_symlink_and_directory_replacements(
        self,
    ) -> None:
        for replacement_kind in ("directory", "symlink"):
            with self.subTest(replacement_kind=replacement_kind):
                with tempfile.TemporaryDirectory() as temporary:
                    parent = Path(temporary)
                    root = parent / "workspace"
                    parked = parent / "parked-workspace"
                    outside = parent / "outside-workspace"
                    root.mkdir()
                    (root / "parked-sentinel").write_bytes(b"parked bytes")
                    outside.mkdir()
                    (outside / "outside-sentinel").write_bytes(b"outside bytes")
                    workspace = Workspace(root)
                    descriptor = workspace_module.open_workspace_root_directory(
                        workspace
                    )

                    root.rename(parked)
                    if replacement_kind == "directory":
                        root.mkdir()
                        (root / "replacement-sentinel").write_bytes(
                            b"replacement bytes"
                        )
                    else:
                        try:
                            root.symlink_to(outside, target_is_directory=True)
                        except NotImplementedError as exc:
                            self.skipTest(
                                f"directory symlink creation is unsupported: {exc}"
                            )
                        except OSError as exc:
                            unsupported = {
                                errno.EACCES,
                                errno.EPERM,
                                errno.ENOSYS,
                                getattr(errno, "ENOTSUP", errno.EINVAL),
                                getattr(errno, "EOPNOTSUPP", errno.EINVAL),
                            }
                            if exc.errno in unsupported:
                                self.skipTest(
                                    "directory symlink creation is unsupported: "
                                    f"{exc}"
                                )
                            raise
                    parked_before = tuple(
                        (path.name, path.read_bytes())
                        for path in sorted(parked.iterdir())
                    )
                    outside_before = tuple(
                        (path.name, path.read_bytes())
                        for path in sorted(outside.iterdir())
                    )
                    replacement_before = (
                        tuple(
                            (path.name, path.read_bytes())
                            for path in sorted(root.iterdir())
                        )
                        if replacement_kind == "directory"
                        else os.readlink(root)
                    )
                    try:
                        with self.assertRaises(WorkspaceSecurityError):
                            workspace_module.verify_workspace_root_directory(
                                workspace,
                                descriptor,
                            )
                        with self.assertRaises(WorkspaceSecurityError):
                            workspace_module.open_workspace_root_directory(
                                workspace
                            )
                    finally:
                        os.close(descriptor)

                    self.assertEqual(
                        tuple(
                            (path.name, path.read_bytes())
                            for path in sorted(parked.iterdir())
                        ),
                        parked_before,
                    )
                    self.assertEqual(
                        tuple(
                            (path.name, path.read_bytes())
                            for path in sorted(outside.iterdir())
                        ),
                        outside_before,
                    )
                    if replacement_kind == "directory":
                        self.assertEqual(
                            tuple(
                                (path.name, path.read_bytes())
                                for path in sorted(root.iterdir())
                            ),
                            replacement_before,
                        )
                    else:
                        self.assertEqual(os.readlink(root), replacement_before)

    def test_missing_root_identity_binds_once_during_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "workspace"
            workspace = Workspace(root)

            with self.assertRaises(OSError):
                workspace_module.open_workspace_root_directory(workspace)
            self.assertFalse(root.exists())

            workspace.initialize()
            bound = os.stat(root, follow_symlinks=False)
            descriptor = workspace_module.open_workspace_root_directory(workspace)
            os.close(descriptor)
            workspace.initialize()
            repeated = os.stat(root, follow_symlinks=False)
            self.assertEqual(
                (repeated.st_dev, repeated.st_ino),
                (bound.st_dev, bound.st_ino),
            )

            parked = parent / "parked-workspace"
            root.rename(parked)
            root.mkdir()
            with self.assertRaises(WorkspaceSecurityError):
                workspace_module.open_workspace_root_directory(workspace)
            self.assertEqual(tuple(root.iterdir()), ())
            self.assertTrue((parked / "core_documents").is_dir())

    def test_pending_root_creates_final_only_and_multiple_missing_ancestors(
        self,
    ) -> None:
        """Create each missing component safely and bind only after scaffolding."""

        for components in (
            ("final_only",),
            ("ancestor_one", "ancestor_two", "final_root"),
        ):
            with self.subTest(components=components):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                parent = Path(temporary.name)
                root = parent.joinpath(*components)
                workspace = Workspace(root)
                with mock.patch.object(
                    workspace_module,
                    "_bind_pending_workspace_root_identity",
                    wraps=workspace_module._bind_pending_workspace_root_identity,
                ) as bind:
                    self.assertIs(workspace.initialize(), workspace)
                    bind.assert_called_once_with(workspace)
                    bound = os.stat(root, follow_symlinks=False)
                    self.assertIs(workspace.initialize(), workspace)
                    bind.assert_called_once_with(workspace)

                self.assertTrue(
                    all(
                        parent.joinpath(*components[:index]).is_dir()
                        for index in range(1, len(components) + 1)
                    )
                )
                self.assertTrue(
                    all(
                        workspace.path(relative).is_dir()
                        for relative in WORKSPACE_DIRECTORIES
                    )
                )
                self.assertTrue(
                    workspace.path("config/system_defaults.json").is_file()
                )
                repeated = os.stat(root, follow_symlinks=False)
                self.assertEqual(
                    (repeated.st_dev, repeated.st_ino),
                    (bound.st_dev, bound.st_ino),
                )

    def test_each_missing_ancestor_substitution_never_enters_external_target(
        self,
    ) -> None:
        """Reject deterministic no-follow races at every controlled component."""

        required_dir_fd = (os.rename, os.symlink)
        if any(
            operation not in getattr(os, "supports_dir_fd", ())
            for operation in required_dir_fd
        ):
            self.skipTest("descriptor-relative rename/symlink is unavailable")

        components = ("race_ancestor_one", "race_ancestor_two", "race_final")
        for external_kind in ("empty", "scaffolded"):
            for substitution_index, substitution_name in enumerate(components):
                with self.subTest(
                    external_kind=external_kind,
                    substitution=substitution_name,
                ):
                    temporary = tempfile.TemporaryDirectory()
                    self.addCleanup(temporary.cleanup)
                    parent = Path(temporary.name)
                    external = parent / "external"
                    external.mkdir()
                    if external_kind == "scaffolded":
                        Workspace(external).initialize()
                    external_before = snapshot_directory_namespace(external)
                    root = parent.joinpath(*components)
                    workspace = Workspace(root)
                    real_mkdir = os.mkdir
                    substituted = False
                    parked_name = f".parked-{substitution_name}"

                    def substitute_after_create(
                        path: object,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> None:
                        nonlocal substituted
                        real_mkdir(path, mode, dir_fd=dir_fd)
                        if path != substitution_name or substituted:
                            return
                        substituted = True
                        os.rename(
                            substitution_name,
                            parked_name,
                            src_dir_fd=dir_fd,
                            dst_dir_fd=dir_fd,
                        )
                        os.symlink(
                            external,
                            substitution_name,
                            target_is_directory=True,
                            dir_fd=dir_fd,
                        )

                    with (
                        mock.patch.object(
                            workspace_module.os,
                            "mkdir",
                            side_effect=substitute_after_create,
                        ),
                        mock.patch.object(
                            workspace,
                            "_directory",
                            wraps=workspace._directory,
                        ) as child_directory,
                        self.assertRaises(NotADirectoryError) as raised,
                    ):
                        workspace.initialize()

                    self.assertTrue(substituted)
                    self.assertEqual(raised.exception.errno, errno.ENOTDIR)
                    child_directory.assert_not_called()
                    substitution_path = parent.joinpath(
                        *components[: substitution_index + 1]
                    )
                    self.assertTrue(substitution_path.is_symlink())
                    self.assertEqual(
                        snapshot_directory_namespace(external),
                        external_before,
                    )
                    self.assert_terminal_initialize_has_no_io(workspace)
                    self.assertEqual(
                        snapshot_directory_namespace(external),
                        external_before,
                    )

    def test_final_component_directory_file_and_broken_symlink_races(
        self,
    ) -> None:
        """Accept only a raced real directory and terminalize unsafe final types."""

        for raced_kind in ("directory", "file", "broken_symlink"):
            with self.subTest(raced_kind=raced_kind):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                parent = Path(temporary.name)
                root = parent / f"raced_{raced_kind}"
                workspace = Workspace(root)
                real_open = os.open
                raced = False

                def insert_final_before_retry(
                    path: object,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    nonlocal raced
                    if path == root.name and dir_fd is not None and not raced:
                        raced = True
                        if raced_kind == "directory":
                            root.mkdir()
                        elif raced_kind == "file":
                            root.write_bytes(b"raced regular file\x00")
                        else:
                            try:
                                root.symlink_to(
                                    parent / "missing-target",
                                    target_is_directory=True,
                                )
                            except (NotImplementedError, OSError) as exc:
                                self.skipTest(
                                    f"broken directory symlink is unavailable: {exc}"
                                )
                        raise FileNotFoundError(
                            errno.ENOENT,
                            "simulated first final lookup miss",
                            root.name,
                        )
                    return real_open(path, flags, mode, dir_fd=dir_fd)

                with mock.patch.object(
                    workspace_module.os,
                    "open",
                    side_effect=insert_final_before_retry,
                ):
                    if raced_kind == "directory":
                        self.assertIs(workspace.initialize(), workspace)
                    else:
                        with self.assertRaises(NotADirectoryError) as raised:
                            workspace.initialize()
                        self.assertEqual(raised.exception.errno, errno.ENOTDIR)

                self.assertTrue(raced)
                if raced_kind == "directory":
                    self.assertTrue(
                        workspace.path("config/system_defaults.json").is_file()
                    )
                    self.assertIs(workspace.initialize(), workspace)
                else:
                    self.assert_terminal_initialize_has_no_io(workspace)
                    if raced_kind == "file":
                        self.assertEqual(root.read_bytes(), b"raced regular file\x00")
                    else:
                        self.assertTrue(root.is_symlink())

    def test_missing_root_symlink_race_aborts_before_external_scaffold(self) -> None:
        """Reject a late root alias without changing any external target entry."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "workspace"
            external = parent / "external"
            external.mkdir()
            (external / "sentinel.bin").write_bytes(b"external sentinel\x00")
            nested = external / "existing"
            nested.mkdir()
            (nested / "preserved.txt").write_bytes(b"preserved bytes\n")
            workspace = Workspace(root)
            before = snapshot_directory_namespace(external)
            try:
                root.symlink_to(external, target_is_directory=True)
            except NotImplementedError as exc:
                self.skipTest(f"directory symlink creation is unsupported: {exc}")
            except OSError as exc:
                unsupported = {
                    errno.EACCES,
                    errno.EPERM,
                    errno.ENOSYS,
                    getattr(errno, "ENOTSUP", errno.EINVAL),
                    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
                }
                if exc.errno in unsupported:
                    self.skipTest(
                        f"directory symlink creation is unsupported: {exc}"
                    )
                raise
            link_target = os.readlink(root)

            with mock.patch.object(
                workspace,
                "_directory",
                wraps=workspace._directory,
            ) as child_directory:
                with self.assertRaises(NotADirectoryError) as raised:
                    workspace.initialize()
                first_error = raised.exception
                self.assertEqual(snapshot_directory_namespace(external), before)
                second_error = self.assert_identity_unavailable(
                    workspace.initialize
                )
            child_directory.assert_not_called()

            self.assertTrue(root.is_symlink())
            self.assertEqual(os.readlink(root), link_target)
            self.assertEqual(snapshot_directory_namespace(external), before)
            self.assertEqual(first_error.errno, errno.ENOTDIR)
            self.assertNotEqual(second_error.args, first_error.args)

    def test_missing_root_stat_failure_is_terminal_across_initialize_calls(
        self,
    ) -> None:
        """Do not retry a pending bind whose one root stat attempt failed."""

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary) / "workspace")
            canonical_root = workspace.root
            real_stat = os.stat
            injected = OSError("PRIVATE_PENDING_BIND_STAT_FAILURE")

            def fail_root_identity(
                path: object,
                *args: object,
                **kwargs: object,
            ):
                if (
                    Path(path) == canonical_root
                    and kwargs.get("follow_symlinks") is False
                ):
                    raise injected
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(
                workspace,
                "_directory",
                wraps=workspace._directory,
            ) as child_directory:
                with mock.patch.object(
                    workspace_module.os,
                    "stat",
                    side_effect=fail_root_identity,
                ):
                    try:
                        workspace.initialize()
                    except OSError as first_error:
                        self.assertIs(first_error, injected)
                        self.assert_originating_traceback(
                            first_error,
                            "fail_root_identity",
                        )
                    else:
                        self.fail("pending bind stat failure did not propagate")
                self.assertEqual(tuple(canonical_root.iterdir()), ())
                second_error = self.assert_identity_unavailable(
                    workspace.initialize
                )
            child_directory.assert_not_called()

            self.assertTrue(canonical_root.is_dir())
            self.assertEqual(tuple(canonical_root.iterdir()), ())
            self.assertNotEqual(second_error.args, injected.args)

    def test_missing_root_nonmeaningful_bind_aborts_before_scaffold(self) -> None:
        """Require a meaningful pending identity before creating child entries."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            workspace = Workspace(root)
            canonical_root = workspace.root
            real_stat = os.stat
            zero_identity = mock.Mock(
                st_mode=stat.S_IFDIR | 0o700,
                st_dev=0,
                st_ino=0,
            )

            def zero_root_identity(
                path: object,
                *args: object,
                **kwargs: object,
            ):
                if (
                    Path(path) == canonical_root
                    and kwargs.get("follow_symlinks") is False
                ):
                    return zero_identity
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(
                workspace,
                "_directory",
                wraps=workspace._directory,
            ) as child_directory:
                with mock.patch.object(
                    workspace_module.os,
                    "stat",
                    side_effect=zero_root_identity,
                ):
                    first_error = self.assert_identity_unavailable(
                        workspace.initialize
                    )
                self.assertEqual(tuple(canonical_root.iterdir()), ())
                second_error = self.assert_identity_unavailable(
                    workspace.initialize
                )
            child_directory.assert_not_called()

            self.assertTrue(canonical_root.is_dir())
            self.assertEqual(tuple(canonical_root.iterdir()), ())
            self.assertEqual(second_error.args, first_error.args)

    def test_pending_initialization_propagates_first_failure_and_never_retries(
        self,
    ) -> None:
        """Terminalize every creation, bind, child, and defaults failure seam."""

        for seam in (
            "intermediate_creation",
            "final_creation",
            "bind_stat",
            "child_scaffold",
            "defaults_write",
            "text_scaffold",
        ):
            for exception_type in (OSError, KeyboardInterrupt, SystemExit):
                with self.subTest(seam=seam, exception=exception_type.__name__):
                    temporary = tempfile.TemporaryDirectory()
                    self.addCleanup(temporary.cleanup)
                    parent = Path(temporary.name)
                    root = parent / "failure_ancestor" / "failure_final"
                    workspace = Workspace(root)
                    failure = exception_type(f"{seam} first failure")

                    def raise_injected(*_args: object, **_kwargs: object) -> None:
                        raise failure

                    real_mkdir = os.mkdir

                    def fail_selected_creation(
                        path: object,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> None:
                        selected = (
                            "failure_ancestor"
                            if seam == "intermediate_creation"
                            else "failure_final"
                        )
                        if path == selected:
                            raise_injected()
                        real_mkdir(path, mode, dir_fd=dir_fd)

                    real_stat = os.stat

                    def fail_bind_stat(
                        path: object,
                        *args: object,
                        **kwargs: object,
                    ):
                        if (
                            Path(path) == workspace.root
                            and kwargs.get("follow_symlinks") is False
                        ):
                            raise_injected()
                        return real_stat(path, *args, **kwargs)

                    if seam in {"intermediate_creation", "final_creation"}:
                        patcher = mock.patch.object(
                            workspace_module.os,
                            "mkdir",
                            side_effect=fail_selected_creation,
                        )
                    elif seam == "bind_stat":
                        patcher = mock.patch.object(
                            workspace_module.os,
                            "stat",
                            side_effect=fail_bind_stat,
                        )
                    elif seam == "child_scaffold":
                        patcher = mock.patch.object(
                            workspace,
                            "_directory",
                            side_effect=raise_injected,
                        )
                    elif seam == "defaults_write":
                        patcher = mock.patch.object(
                            workspace,
                            "_ensure_json",
                            side_effect=raise_injected,
                        )
                    else:
                        patcher = mock.patch.object(
                            workspace,
                            "_ensure_text",
                            side_effect=raise_injected,
                        )

                    with patcher:
                        try:
                            workspace.initialize()
                        except exception_type as caught:
                            self.assertIs(caught, failure)
                            self.assert_originating_traceback(
                                caught,
                                "raise_injected",
                            )
                        else:
                            self.fail(
                                f"{seam} {exception_type.__name__} did not propagate"
                            )
                    self.assert_terminal_initialize_has_no_io(workspace)
                    if seam == "intermediate_creation":
                        self.assertFalse(parent.joinpath("failure_ancestor").exists())
                    else:
                        self.assertTrue(parent.joinpath("failure_ancestor").is_dir())
                    if seam in {
                        "bind_stat",
                        "child_scaffold",
                        "defaults_write",
                        "text_scaffold",
                    }:
                        self.assertTrue(root.is_dir())

    def test_pending_root_descriptors_are_non_inheritable_before_first_use(
        self,
    ) -> None:
        """Mark every opened traversal descriptor before child or identity I/O."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "descriptor_ancestor" / "descriptor_root"
            workspace = Workspace(root)
            tracker = _PendingDescriptorTracker(workspace.root)
            with (
                mock.patch.object(
                    workspace_module.os,
                    "open",
                    side_effect=tracker.open,
                ),
                mock.patch.object(
                    workspace_module.os,
                    "mkdir",
                    side_effect=tracker.mkdir,
                ),
                mock.patch.object(
                    workspace_module.os,
                    "fstat",
                    side_effect=tracker.fstat,
                ),
                mock.patch.object(
                    workspace_module.os,
                    "set_inheritable",
                    side_effect=tracker.set_inheritable,
                ),
                mock.patch.object(
                    workspace_module.os,
                    "close",
                    side_effect=tracker.close,
                ),
            ):
                self.assertIs(workspace.initialize(), workspace)

            self.assertGreaterEqual(len(tracker.opened), 3)
            self.assertEqual(sorted(tracker.closed), tracker.opened)
            self.assertEqual(tracker.double_closes, [])
            for token in tracker.opened:
                with self.subTest(token=token):
                    opened_at = tracker.events.index(("open", token))
                    self.assertEqual(
                        tracker.events[opened_at + 1],
                        ("set_inheritable", token, False),
                    )
                    set_at = opened_at + 1
                    for used_at, event in enumerate(tracker.events):
                        if event[:2] == ("use", token):
                            self.assertGreater(used_at, set_at)

    def test_pending_root_set_inheritable_platform_failures_are_best_effort(
        self,
    ) -> None:
        """Do not change successful initialization for unsupported setter calls."""

        for exception_type in (AttributeError, OSError):
            with self.subTest(exception=exception_type.__name__):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                parent = Path(temporary.name)
                external = parent / "external"
                external.mkdir()
                (external / "sentinel.bin").write_bytes(b"external sentinel\x00")
                external_before = snapshot_directory_namespace(external)
                root = parent / "descriptor_ancestor" / "descriptor_root"
                workspace = Workspace(root)
                tracker = _PendingDescriptorTracker(
                    workspace.root,
                    set_failure=lambda _path: exception_type(
                        "set_inheritable unavailable"
                    ),
                )
                with (
                    mock.patch.object(
                        workspace_module.os,
                        "open",
                        side_effect=tracker.open,
                    ),
                    mock.patch.object(
                        workspace_module.os,
                        "mkdir",
                        side_effect=tracker.mkdir,
                    ),
                    mock.patch.object(
                        workspace_module.os,
                        "fstat",
                        side_effect=tracker.fstat,
                    ),
                    mock.patch.object(
                        workspace_module.os,
                        "set_inheritable",
                        side_effect=tracker.set_inheritable,
                    ),
                    mock.patch.object(
                        workspace_module.os,
                        "close",
                        side_effect=tracker.close,
                    ),
                ):
                    self.assertIs(workspace.initialize(), workspace)

                set_tokens = [
                    event[1]
                    for event in tracker.events
                    if event[0] == "set_inheritable"
                ]
                self.assertEqual(set_tokens, tracker.opened)
                self.assertEqual(sorted(tracker.closed), tracker.opened)
                self.assertEqual(tracker.double_closes, [])
                self.assertEqual(
                    snapshot_directory_namespace(external),
                    external_before,
                )
                self.assertTrue(
                    workspace.path("config/system_defaults.json").is_file()
                )
                self.assertIs(workspace.initialize(), workspace)

    def test_pending_root_set_inheritable_process_control_closes_descriptors(
        self,
    ) -> None:
        """Reraise controls exactly after closing each acquired descriptor once."""

        for target in ("anchor", "intermediate", "final"):
            for exception_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(target=target, exception=exception_type.__name__):
                    temporary = tempfile.TemporaryDirectory()
                    self.addCleanup(temporary.cleanup)
                    root = (
                        Path(temporary.name)
                        / "descriptor_ancestor"
                        / "descriptor_root"
                    )
                    workspace = Workspace(root)
                    target_path = {
                        "anchor": os.fspath(workspace.root.anchor),
                        "intermediate": "descriptor_ancestor",
                        "final": "descriptor_root",
                    }[target]
                    failure = exception_type(
                        f"{target} set_inheritable process control"
                    )

                    def fail_selected_set_inheritable(
                        path: str,
                    ) -> BaseException | None:
                        if path == target_path:
                            raise failure
                        return None

                    tracker = _PendingDescriptorTracker(
                        workspace.root,
                        set_failure=fail_selected_set_inheritable,
                    )
                    with (
                        mock.patch.object(
                            workspace_module.os,
                            "open",
                            side_effect=tracker.open,
                        ),
                        mock.patch.object(
                            workspace_module.os,
                            "mkdir",
                            side_effect=tracker.mkdir,
                        ),
                        mock.patch.object(
                            workspace_module.os,
                            "fstat",
                            side_effect=tracker.fstat,
                        ),
                        mock.patch.object(
                            workspace_module.os,
                            "set_inheritable",
                            side_effect=tracker.set_inheritable,
                        ),
                        mock.patch.object(
                            workspace_module.os,
                            "close",
                            side_effect=tracker.close,
                        ),
                    ):
                        try:
                            workspace.initialize()
                        except exception_type as caught:
                            self.assertIs(caught, failure)
                            self.assert_originating_traceback(
                                caught,
                                "fail_selected_set_inheritable",
                            )
                        else:
                            self.fail(
                                f"{target} {exception_type.__name__} did not propagate"
                            )

                    self.assertEqual(sorted(tracker.closed), tracker.opened)
                    self.assertEqual(len(set(tracker.closed)), len(tracker.closed))
                    self.assertEqual(tracker.double_closes, [])
                    failed_token = next(
                        event[1]
                        for event in reversed(tracker.events)
                        if event[0] == "set_inheritable"
                    )
                    if target == "anchor":
                        self.assertEqual(tracker.closed[-1:], [failed_token])
                    else:
                        self.assertEqual(
                            tracker.closed[-2:],
                            [failed_token, failed_token - 1],
                        )
                    self.assert_terminal_initialize_has_no_io(workspace)

    def test_missing_safe_creation_primitive_fails_before_mutation(self) -> None:
        """Reject each required capability without creating the pending root."""

        capability_cases = (
            ("open", {"open": None}),
            ("mkdir", {"mkdir": None}),
            ("fstat", {"fstat": None}),
            ("stat", {"stat": None}),
            ("close", {"close": None}),
            ("directory_flag", {"O_DIRECTORY": 0}),
            ("no_follow_flag", {"O_NOFOLLOW": 0}),
            ("dir_fd", {"supports_dir_fd": frozenset()}),
            ("no_follow_stat", {"supports_follow_symlinks": frozenset()}),
        )
        expected_errno = getattr(errno, "ENOTSUP", errno.ENOSYS)
        for label, replacements in capability_cases:
            with self.subTest(capability=label):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                parent = Path(temporary.name)
                root = parent / "pending_workspace"
                workspace = Workspace(root)
                before = snapshot_directory_namespace(parent)
                name, value = next(iter(replacements.items()))
                patcher = mock.patch.object(workspace_module.os, name, value)
                with patcher, self.assertRaises(OSError) as raised:
                    workspace.initialize()
                self.assertEqual(
                    raised.exception.args,
                    (
                        expected_errno,
                        "safe pending workspace root creation is unsupported",
                    ),
                )
                self.assertEqual(snapshot_directory_namespace(parent), before)
                self.assertFalse(root.exists())
                self.assert_terminal_initialize_has_no_io(workspace)

    def test_invalid_overwrite_option_is_pre_io_and_does_not_terminalize(self) -> None:
        """Validate the option before capability checks and permit a later success."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "pending_workspace"
            workspace = Workspace(root)
            with (
                mock.patch.object(
                    workspace_module,
                    "_require_pending_workspace_root_creation_operations",
                    side_effect=AssertionError("invalid option reached capability I/O"),
                ) as capabilities,
                mock.patch.object(
                    workspace_module,
                    "_bind_pending_workspace_root_identity",
                    side_effect=AssertionError("invalid option reached root I/O"),
                ) as bind,
                mock.patch.object(
                    workspace,
                    "_directory",
                    side_effect=AssertionError("invalid option reached child I/O"),
                ) as child,
                self.assertRaises(TypeError) as raised,
            ):
                workspace.initialize(overwrite_prompts=1)  # type: ignore[arg-type]

            self.assertEqual(
                str(raised.exception),
                "overwrite_prompts must be a boolean",
            )
            capabilities.assert_not_called()
            bind.assert_not_called()
            child.assert_not_called()
            self.assertFalse(root.exists())
            self.assertIs(workspace.initialize(), workspace)
            self.assertTrue(
                workspace.path("config/system_defaults.json").is_file()
            )

    def test_unavailable_non_directory_and_zero_root_identity_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            ordinary_root = parent / "ordinary-root"
            ordinary_root.mkdir()
            canonical_root = ordinary_root.resolve()
            real_stat = os.stat

            def unavailable_probe(path: object, *args: object, **kwargs: object):
                if (
                    Path(path) == canonical_root
                    and kwargs.get("follow_symlinks") is False
                ):
                    raise OSError("PRIVATE_ROOT_STAT_FAILURE")
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(
                workspace_module.os,
                "stat",
                side_effect=unavailable_probe,
            ):
                unavailable = Workspace(ordinary_root)

            zero_value = mock.Mock(
                st_mode=stat.S_IFDIR | 0o700,
                st_dev=0,
                st_ino=0,
            )

            def zero_probe(path: object, *args: object, **kwargs: object):
                if (
                    Path(path) == canonical_root
                    and kwargs.get("follow_symlinks") is False
                ):
                    return zero_value
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(
                workspace_module.os,
                "stat",
                side_effect=zero_probe,
            ):
                zero = Workspace(ordinary_root)

            file_root = parent / "not-a-directory"
            file_root.write_bytes(b"not a workspace")
            non_directory = Workspace(file_root)

            for label, workspace in (
                ("unavailable", unavailable),
                ("zero", zero),
                ("non_directory", non_directory),
            ):
                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        workspace_module.os,
                        "open",
                        side_effect=AssertionError(
                            "unbound root identity reached open"
                        ),
                    ) as opened,
                    self.assertRaises(OSError),
                ):
                    workspace_module.open_workspace_root_directory(workspace)
                opened.assert_not_called()

            for label, workspace in (
                ("unavailable", unavailable),
                ("zero", zero),
            ):
                with (
                    self.subTest(label=label, operation="initialize"),
                    mock.patch.object(
                        workspace,
                        "_directory",
                        wraps=workspace._directory,
                    ) as child_directory,
                ):
                    workspace.initialize()
                self.assertEqual(
                    child_directory.call_count,
                    len(WORKSPACE_DIRECTORIES),
                )
                self.assertTrue(
                    all(
                        workspace.path(relative).is_dir()
                        for relative in WORKSPACE_DIRECTORIES
                    )
                )
                self.assertIs(workspace.initialize(), workspace)
                self.assert_identity_unavailable(
                    lambda: workspace_module.open_workspace_root_directory(
                        workspace
                    )
                )

    def test_initial_system_defaults_are_exact_and_legacy_bytes_are_preserved(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG,
            {
                "language": "KO",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
                "codex_model": None,
                "codex_reasoning_effort": None,
            },
        )
        self.assertEqual(
            workspace.read_text("config/system_defaults.json"),
            "{\n"
            '  "codex_model": null,\n'
            '  "codex_reasoning_effort": null,\n'
            '  "language": "KO",\n'
            '  "max_debate_rounds": 1,\n'
            '  "persona": "default_kim_eun_hee"\n'
            "}\n",
        )

        legacy = (
            "{\n"
            '  "language": "KO",\n'
            '  "persona": "default_kim_eun_hee",\n'
            '  "max_debate_rounds": 7\n'
            "}\n"
        )
        workspace.write_text("config/system_defaults.json", legacy)
        workspace.initialize()
        self.assertEqual(
            workspace.read_text("config/system_defaults.json"),
            legacy,
        )
        workspace.initialize(overwrite_prompts=True)
        self.assertEqual(
            workspace.read_text("config/system_defaults.json"),
            legacy,
        )

    def test_initialization_creates_topology_and_preserves_existing_content(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.mkdir(exist_ok=True)
        preserved = root / "core_documents" / "global_outline.md"
        preserved.parent.mkdir(parents=True)
        preserved.write_text("preserve-existing-content", encoding="utf-8")

        workspace = initialize_workspace(root)
        for relative in WORKSPACE_DIRECTORIES:
            with self.subTest(relative=relative):
                self.assertTrue(workspace.path(relative).is_dir())
        for filename in CORE_DOCUMENTS:
            self.assertTrue(workspace.path(f"core_documents/{filename}").is_file())
        for agent_id in ROSTER:
            self.assertTrue(
                workspace.path(
                    AGENT_SPECS[agent_id].system_prompt_file
                ).is_file()
            )
        self.assertTrue(
            workspace.path("config/system_defaults.json").is_file()
        )
        self.assertTrue(workspace.path("config/agent_configs.json").is_file())

        workspace.initialize()
        self.assertEqual(
            preserved.read_text(encoding="utf-8"),
            "preserve-existing-content",
        )

    def test_debug_log_topology_is_legacy_compatible_and_preserves_bytes(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        diagnostics = {
            "debug_logs/runtime.jsonl": b"active\n",
            "debug_logs/runtime.jsonl.1": b"backup\n",
            "debug_logs/.runtime.lock": b"guard",
        }
        for relative, content in diagnostics.items():
            workspace.path(relative).write_bytes(content)

        workspace.initialize()
        self.assertTrue(workspace.path("debug_logs").is_dir())
        self.assertTrue(workspace.path("debug_logs/.gitkeep").is_file())
        self.assertEqual(
            {
                relative: workspace.path(relative).read_bytes()
                for relative in diagnostics
            },
            diagnostics,
        )

        outline_before = workspace.read_text("core_documents/global_outline.md")
        for child in workspace.path("debug_logs").iterdir():
            child.unlink()
        workspace.path("debug_logs").rmdir()
        legacy = Workspace(workspace.root)
        self.assertEqual(
            legacy.read_text("core_documents/global_outline.md"),
            outline_before,
        )
        self.assertFalse(legacy.root.joinpath("debug_logs").exists())

        legacy.initialize()
        self.assertTrue(legacy.path("debug_logs").is_dir())
        self.assertTrue(legacy.path("debug_logs/.gitkeep").is_file())
        self.assertEqual(
            legacy.read_text("core_documents/global_outline.md"),
            outline_before,
        )

    def test_prompt_initialization_preserves_customizations_unless_overwritten(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        customized = {
            agent_id: f"custom prompt for {agent_id}\n"
            for agent_id in ROSTER
        }
        for agent_id, content in customized.items():
            workspace.write_text(
                AGENT_SPECS[agent_id].system_prompt_file,
                content,
            )

        unrelated_contents = {
            "config/system_defaults.json": '{"custom":"config"}\n',
            "config/agent_configs.json": '{"custom":"agents"}\n',
            "config/persona_templates/default_kim_eun_hee.md": "persona\n",
            "core_documents/global_outline.md": "outline\n",
            "agent_states/plot_architect_state/custom.jsonl": "state\n",
            "discussion_logs/custom.jsonl": "log\n",
            "manuscript/custom.md": "manuscript\n",
        }
        for relative, content in unrelated_contents.items():
            workspace.write_text(relative, content)
        unrelated_before = {
            relative: workspace.path(relative).read_bytes()
            for relative in unrelated_contents
        }

        workspace.initialize()
        for agent_id, content in customized.items():
            with self.subTest(preserved_agent=agent_id):
                self.assertEqual(
                    workspace.read_text(
                        AGENT_SPECS[agent_id].system_prompt_file
                    ),
                    content,
                )

        workspace.initialize(overwrite_prompts=True)
        for agent_id in ROSTER:
            with self.subTest(reset_agent=agent_id):
                self.assertEqual(
                    workspace.read_text(
                        AGENT_SPECS[agent_id].system_prompt_file
                    ),
                    DEFAULT_AGENT_PROMPTS[agent_id],
                )
        self.assertEqual(
            {
                relative: workspace.path(relative).read_bytes()
                for relative in unrelated_contents
            },
            unrelated_before,
        )

    def test_utf8_json_and_semantic_config_errors_are_explicit(self) -> None:
        workspace = initialized_workspace(self)
        workspace.path("core_documents/global_outline.md").write_bytes(b"\xff")
        with self.assertRaisesRegex(WorkspaceFormatError, "UTF-8"):
            workspace.read_text("core_documents/global_outline.md")

        workspace.write_text("config/system_defaults.json", "{bad json")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid JSON"):
            workspace.read_json("config/system_defaults.json")

        workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "not valid",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
            },
        )
        with self.assertRaisesRegex(ConfigurationError, "language"):
            ConfigManager(workspace).load_system_defaults()

    def test_path_traversal_absolute_paths_and_symlink_escape_are_rejected(self) -> None:
        workspace = initialized_workspace(self)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_path = Path(outside.name)
        (outside_path / "secret.txt").write_text("outside", encoding="utf-8")
        workspace.path("escape").symlink_to(outside_path, target_is_directory=True)

        unsafe_paths = ("../outside", str(outside_path / "secret.txt"))
        for unsafe in unsafe_paths:
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(WorkspaceSecurityError):
                    workspace.path(unsafe)
        with self.assertRaises(WorkspaceSecurityError):
            workspace.read_text("escape/secret.txt")
        with self.assertRaises(WorkspaceSecurityError):
            workspace.write_text("escape/new.txt", "must-not-escape")

    def test_symlink_aliases_cannot_cross_logical_workspace_boundaries(self) -> None:
        workspace = initialized_workspace(self)
        system_path = workspace.path("config/system_defaults.json")
        system_before = system_path.read_bytes()
        outline_path = workspace.path("core_documents/global_outline.md")
        outline_path.unlink()
        outline_path.symlink_to("../config/system_defaults.json")

        with self.assertRaises(WorkspaceSecurityError):
            workspace.commit_approved_artifacts(
                "alias-test.md",
                "approved-candidate",
                {"global_outline.md": "must-not-corrupt-config"},
            )
        self.assertEqual(system_path.read_bytes(), system_before)
        self.assertFalse(workspace.path("manuscript/alias-test.md").exists())

        tunnel = workspace.path("manuscript/tunnel")
        tunnel.symlink_to("../core_documents", target_is_directory=True)
        with self.assertRaises(WorkspaceSecurityError):
            workspace.write_manuscript("tunnel/escaped.md", "must-stay-in-manuscript")
        self.assertFalse(workspace.path("core_documents/escaped.md").exists())

        workspace.save_session_overrides(
            "session_original",
            {"language": "EN", "persona": None, "agents": {}},
        )
        original_state = workspace.path(
            "agent_states/orchestrator_state/sessions/session_original.json"
        )
        original_before = original_state.read_bytes()
        alias_state = workspace.path(
            "agent_states/orchestrator_state/sessions/session_alias.json"
        )
        alias_state.symlink_to("session_original.json")
        with self.assertRaises(WorkspaceSecurityError):
            workspace.save_session_overrides(
                "session_alias",
                {"language": "JA", "persona": None, "agents": {}},
            )
        self.assertEqual(original_state.read_bytes(), original_before)

    def test_manuscript_creation_never_overwrites_and_prevalidates_core_updates(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        workspace.write_manuscript("approved.md", "first")
        with self.assertRaises(ManuscriptExistsError):
            workspace.write_manuscript("approved.md", "second")
        self.assertEqual(workspace.read_manuscript("approved.md"), "first")

        original_outline = workspace.read_text(
            "core_documents/global_outline.md"
        )
        with self.assertRaises(ManuscriptExistsError):
            workspace.commit_approved_artifacts(
                "approved.md",
                "replacement",
                {"global_outline.md": "must-not-change"},
            )
        self.assertEqual(
            workspace.read_text("core_documents/global_outline.md"),
            original_outline,
        )
        with self.assertRaises(WorkspaceSecurityError):
            workspace.write_manuscript("../escape.md", "unsafe")

    def test_post_replace_failures_roll_back_core_and_manuscript_writes(self) -> None:
        workspace = initialized_workspace(self)
        manuscript_directory = workspace.path("manuscript")

        original_fsync = workspace._fsync_directory

        def fail_manuscript_fsync(directory: Path) -> None:
            if directory == manuscript_directory:
                raise OSError("simulated manuscript directory sync failure")
            original_fsync(directory)

        with mock.patch.object(
            workspace,
            "_fsync_directory",
            side_effect=fail_manuscript_fsync,
        ):
            with self.assertRaisesRegex(OSError, "manuscript directory"):
                workspace.commit_approved_artifacts(
                    "rollback.md",
                    "candidate",
                    {},
                )
        self.assertFalse(workspace.path("manuscript/rollback.md").exists())

        core_directory = workspace.path("core_documents")
        outline_path = workspace.path("core_documents/global_outline.md")
        outline_before = outline_path.read_bytes()
        failed_once = False

        def fail_first_core_fsync(directory: Path) -> None:
            nonlocal failed_once
            if directory == core_directory and not failed_once:
                failed_once = True
                raise OSError("simulated core directory sync failure")
            original_fsync(directory)

        with mock.patch.object(
            workspace,
            "_fsync_directory",
            side_effect=fail_first_core_fsync,
        ):
            with self.assertRaisesRegex(OSError, "core directory"):
                workspace.commit_approved_artifacts(
                    "core-rollback.md",
                    "candidate",
                    {"global_outline.md": "replacement"},
                )
        self.assertEqual(outline_path.read_bytes(), outline_before)
        self.assertFalse(workspace.path("manuscript/core-rollback.md").exists())


class DebateContextSidecarTests(unittest.TestCase):
    """Verify immutable, strict, identity-bound debate context sidecars."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.relative = "discussion_logs/session_a/debate_a.context.json"

    def test_absent_fresh_included_and_empty_schemas_are_exact(self) -> None:
        runtime = StoryRuntime(self.workspace)
        self.assertIsNone(
            self.workspace.inspect_debate_context("session_a", "debate_a")
        )
        self.assertIsNone(
            runtime.inspect_debate_context("session_a", "debate_a")
        )
        self.assertIsNone(
            runtime.session("session_a").inspect_debate_context("debate_a")
        )
        inspect_before = self.workspace.inspect_debate("session_a", "debate_a")
        session_before = self.workspace.inspect_session("session_a")

        self.workspace.save_debate_context(
            "session_a",
            "debate_a",
            conversation_id="conversation_a",
            mode=DebateContextMode.FRESH,
            snapshot=None,
        )
        fresh = self.workspace.inspect_debate_context("session_a", "debate_a")
        assert fresh is not None
        self.assertEqual(
            set(fresh),
            {
                "schema_version",
                "session_id",
                "debate_id",
                "conversation_id",
                "mode",
                "snapshot",
            },
        )
        self.assertEqual(
            fresh,
            {
                "schema_version": 1,
                "session_id": "session_a",
                "debate_id": "debate_a",
                "conversation_id": "conversation_a",
                "mode": "fresh",
                "snapshot": None,
            },
        )
        self.assertEqual(
            self.workspace.inspect_debate("session_a", "debate_a"),
            inspect_before,
        )
        self.assertEqual(self.workspace.inspect_session("session_a"), session_before)

        empty = ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )
        self.workspace.save_debate_context(
            "session_a",
            "empty_debate",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=empty,
        )
        empty_sidecar = self.workspace.inspect_debate_context(
            "session_a", "empty_debate"
        )
        assert empty_sidecar is not None
        self.assertEqual(
            empty_sidecar["snapshot"],
            {
                "events": [],
                "total_complete_turns": 0,
                "retained_turns": 0,
                "omitted_turns": 0,
                "retained_utf8_bytes": 0,
                "truncated": False,
            },
        )

        context = debate_context_window()
        self.workspace.save_debate_context(
            "session_a",
            "included_debate",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=context,
        )
        included = self.workspace.inspect_debate_context(
            "session_a", "included_debate"
        )
        assert included is not None
        snapshot = included["snapshot"]
        assert isinstance(snapshot, dict)
        self.assertEqual(
            set(snapshot),
            {
                "events",
                "total_complete_turns",
                "retained_turns",
                "omitted_turns",
                "retained_utf8_bytes",
                "truncated",
            },
        )
        self.assertEqual(snapshot["events"], [event.to_dict() for event in context.events])
        self.assertTrue(
            all(
                set(event)
                == {
                    "schema_version",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "sequence",
                    "timestamp",
                    "sender",
                    "recipient",
                    "kind",
                    "content",
                }
                for event in snapshot["events"]
            )
        )

    def test_create_only_collision_preserves_bytes_and_blocks_reservation(
        self,
    ) -> None:
        context = debate_context_window()
        path = self.workspace.save_debate_context(
            "session_a",
            "debate_a",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=context,
        )
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        with self.assertRaises(DebateExistsError):
            self.workspace.save_debate_context(
                "session_a",
                "debate_a",
                conversation_id="conversation_a",
                mode=DebateContextMode.FRESH,
                snapshot=None,
            )
        with self.assertRaises(DebateExistsError):
            self.workspace.reserve_debate(
                "session_a",
                "debate_a",
                input_kind="prompt",
                owner_token="owner",
            )
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertFalse(
            self.workspace.path(
                "discussion_logs/session_a/debate_a.state.json"
            ).exists()
        )

    def test_tampering_and_duplicate_fields_raise_without_rewriting(self) -> None:
        self.workspace.save_debate_context(
            "session_a",
            "debate_a",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=debate_context_window(),
        )
        canonical = self.workspace.read_json(self.relative)

        def clone() -> dict[str, object]:
            return json.loads(json.dumps(canonical))

        extra = clone()
        extra["extra"] = True
        wrong_path = clone()
        wrong_path["debate_id"] = "other_debate"
        missing_snapshot = clone()
        missing_snapshot["snapshot"] = None
        wrong_counts = clone()
        assert isinstance(wrong_counts["snapshot"], dict)
        wrong_counts["snapshot"]["retained_turns"] = 2
        wrong_event_identity = clone()
        assert isinstance(wrong_event_identity["snapshot"], dict)
        events = wrong_event_identity["snapshot"]["events"]
        assert isinstance(events, list)
        events[0]["conversation_id"] = "other_conversation"
        reordered = clone()
        assert isinstance(reordered["snapshot"], dict)
        reordered["snapshot"]["events"].reverse()

        for label, value in (
            ("extra top-level field", extra),
            ("path identity", wrong_path),
            ("included null snapshot", missing_snapshot),
            ("inconsistent counts", wrong_counts),
            ("event identity", wrong_event_identity),
            ("event ordering", reordered),
        ):
            with self.subTest(label=label):
                self.workspace.write_json(self.relative, value, overwrite=True)
                before = self.workspace.path(self.relative).read_bytes()
                with self.assertRaises(WorkspaceFormatError):
                    self.workspace.inspect_debate_context("session_a", "debate_a")
                self.assertEqual(
                    self.workspace.path(self.relative).read_bytes(),
                    before,
                )

        duplicate = (
            '{"schema_version":1,"schema_version":1,'
            '"session_id":"session_a","debate_id":"debate_a",'
            '"conversation_id":"conversation_a","mode":"fresh",'
            '"snapshot":null}'
        )
        self.workspace.write_text(self.relative, duplicate, overwrite=True)
        before = self.workspace.path(self.relative).read_bytes()
        with self.assertRaises(WorkspaceFormatError):
            self.workspace.inspect_debate_context("session_a", "debate_a")
        self.assertEqual(self.workspace.path(self.relative).read_bytes(), before)


class ConfigurationCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        for name in ("global_persona", "agent_persona", "runtime_persona"):
            self.workspace.write_text(
                f"config/persona_templates/alternative_templates/{name}.md",
                f"persona:{name}",
            )
        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
            },
        )
        configs = {
            agent_id: {"language": None, "persona": None}
            for agent_id in ROSTER
        }
        configs[PLOT_ARCHITECT] = {
            "language": "JA",
            "persona": "agent_persona",
        }
        self.workspace.write_json("config/agent_configs.json", configs)
        self.manager = ConfigManager(self.workspace)

    def test_system_defaults_fields_legacy_construction_and_pickle_are_exact(
        self,
    ) -> None:
        model_fields = fields(SystemDefaults)
        self.assertEqual(
            tuple(field.name for field in model_fields),
            (
                "language",
                "persona",
                "max_debate_rounds",
                "codex_model",
                "codex_reasoning_effort",
            ),
        )
        self.assertIsNone(model_fields[-2].default)
        self.assertIsNone(model_fields[-1].default)
        legacy = SystemDefaults("KO", "global_persona", 1)
        self.assertEqual(
            legacy,
            SystemDefaults(
                "KO",
                "global_persona",
                1,
                codex_model=None,
                codex_reasoning_effort=None,
            ),
        )
        self.assertFalse(hasattr(legacy, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            legacy.codex_model = "changed"
        self.assertEqual(pickle.loads(pickle.dumps(legacy)), legacy)

    def test_full_snapshot_is_exact_frozen_roster_complete_and_copy_isolated(
        self,
    ) -> None:
        snapshot_fields = fields(ConfigurationSnapshot)
        self.assertEqual(
            tuple(field.name for field in snapshot_fields),
            ("system_defaults", "agent_configs"),
        )
        configs = {agent_id: AgentConfig() for agent_id in ROSTER}
        defaults = SystemDefaults("KO", "global_persona", 1)
        snapshot = ConfigurationSnapshot(defaults, configs)
        configs[PLOT_ARCHITECT] = AgentConfig(backend="gemini")

        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertIs(snapshot.system_defaults, defaults)
        self.assertIsInstance(snapshot.agent_configs, MappingProxyType)
        self.assertEqual(tuple(snapshot.agent_configs), ROSTER)
        self.assertIsNone(snapshot.agent_configs[PLOT_ARCHITECT].backend)
        with self.assertRaises(FrozenInstanceError):
            snapshot.system_defaults = defaults  # type: ignore[misc]
        with self.assertRaises(TypeError):
            snapshot.agent_configs[PLOT_ARCHITECT] = AgentConfig()  # type: ignore[index]

        invalid = (
            lambda: ConfigurationSnapshot(object(), configs),
            lambda: ConfigurationSnapshot(defaults, object()),
            lambda: ConfigurationSnapshot(
                defaults,
                {agent_id: AgentConfig() for agent_id in ROSTER[:-1]},
            ),
            lambda: ConfigurationSnapshot(
                defaults,
                {
                    agent_id: (
                        object() if agent_id == PLOT_ARCHITECT else AgentConfig()
                    )
                    for agent_id in ROSTER
                },
            ),
        )
        for factory in invalid:
            with self.subTest(factory=factory), self.assertRaises(
                (TypeError, ValueError)
            ):
                factory()

    def test_complete_pair_is_read_once_and_snapshot_resolution_does_not_reread(
        self,
    ) -> None:
        original_read = self.workspace.read_json
        with mock.patch.object(
            self.workspace,
            "read_json",
            wraps=original_read,
        ) as read:
            snapshot = self.manager.load_configuration_snapshot()
            self.assertEqual(
                [call.args[0] for call in read.call_args_list],
                ["config/system_defaults.json", "config/agent_configs.json"],
            )

            defaults = self.workspace.read_json("config/system_defaults.json")
            defaults["language"] = "DE"
            self.workspace.write_json("config/system_defaults.json", defaults)
            agents = self.workspace.read_json("config/agent_configs.json")
            agents[PLOT_ARCHITECT]["backend"] = "gemini"
            agents[PLOT_ARCHITECT]["language"] = "FR"
            self.workspace.write_json("config/agent_configs.json", agents)
            reads_before_resolution = read.call_count

            persisted = self.manager.resolve_from_snapshot(
                snapshot,
                PLOT_ARCHITECT,
            )
            overridden = self.manager.resolve_from_snapshot(
                snapshot,
                PLOT_ARCHITECT,
                {
                    "language": "ES",
                    "agents": {
                        PLOT_ARCHITECT: {
                            "language": "PT",
                            "persona": "runtime_persona",
                        }
                    },
                },
                {PLOT_ARCHITECT: "gemini"},
            )

        self.assertEqual(read.call_count, reads_before_resolution)
        self.assertEqual(
            (persisted.backend, persisted.language, persisted.persona),
            ("codex", "JA", "agent_persona"),
        )
        self.assertEqual(
            (overridden.backend, overridden.language, overridden.persona),
            ("gemini", "PT", "runtime_persona"),
        )

    def test_complete_pair_validation_rejects_each_invalid_document(self) -> None:
        system_value = self.workspace.read_json("config/system_defaults.json")
        agent_value = self.workspace.read_json("config/agent_configs.json")
        snapshot = self.manager.validate_configuration(system_value, agent_value)
        self.assertIsInstance(snapshot, ConfigurationSnapshot)

        invalid_system = dict(system_value, unexpected=True)
        with self.assertRaisesRegex(ConfigurationError, "unexpected"):
            self.manager.validate_configuration(invalid_system, agent_value)

        invalid_agents = dict(agent_value)
        invalid_agents[PLOT_ARCHITECT] = dict(
            invalid_agents[PLOT_ARCHITECT],
            backend="unknown",
        )
        with self.assertRaisesRegex(ConfigurationError, "backend"):
            self.manager.validate_configuration(system_value, invalid_agents)

        with self.assertRaises(TypeError):
            self.manager.resolve_from_snapshot(  # type: ignore[arg-type]
                object(),
                PLOT_ARCHITECT,
            )

    def test_codex_defaults_accept_missing_null_exact_enum_and_unicode(self) -> None:
        legacy = self.manager.load_system_defaults()
        self.assertEqual(
            legacy,
            SystemDefaults("KO", "global_persona", 1),
        )

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
                "codex_model": None,
                "codex_reasoning_effort": None,
            },
        )
        self.assertEqual(
            self.manager.load_system_defaults(),
            SystemDefaults("KO", "global_persona", 1, None, None),
        )

        exact_model = " GPT-e\u0301 한🙂 "
        for effort in ("minimal", "low", "medium", "high", "xhigh"):
            with self.subTest(effort=effort):
                self.workspace.write_json(
                    "config/system_defaults.json",
                    {
                        "language": "KO",
                        "persona": "global_persona",
                        "max_debate_rounds": 1,
                        "codex_model": exact_model,
                        "codex_reasoning_effort": effort,
                    },
                )
                loaded = self.manager.load_system_defaults()
                self.assertEqual(loaded.codex_model, exact_model)
                self.assertEqual(loaded.codex_reasoning_effort, effort)

    def test_codex_model_rejects_types_and_unicode_control_categories(self) -> None:
        cases = (
            (False, "type"),
            (7, "type"),
            ([], "type"),
            ("", "empty"),
            ("-gpt", "leading dash"),
            ("gpt\x00tail", "Cc NUL"),
            ("gpt\x85tail", "Cc C1"),
            ("gpt\u200etail", "Cf mark"),
            ("gpt\u2066tail", "Cf isolate"),
            ("gpt\ud800tail", "Cs surrogate"),
        )
        for model, label in cases:
            with self.subTest(label=label):
                value = {
                    "language": "KO",
                    "persona": "global_persona",
                    "max_debate_rounds": 1,
                    "codex_model": model,
                    "codex_reasoning_effort": None,
                }
                self.workspace.write_text(
                    "config/system_defaults.json",
                    json.dumps(
                        value,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
                with self.assertRaises(ConfigurationError) as caught:
                    self.manager.load_system_defaults()
                self.assertEqual(
                    str(caught.exception),
                    "codex_model must be safe non-empty text or null",
                )

    def test_codex_reasoning_rejects_types_and_near_values_exactly(self) -> None:
        invalid = (
            False,
            1,
            [],
            "",
            "MINIMAL",
            " low",
            "low ",
            "ultra",
        )
        expected = (
            "codex_reasoning_effort must be null or one of: "
            "minimal, low, medium, high, xhigh"
        )
        for effort in invalid:
            with self.subTest(effort=effort):
                self.workspace.write_json(
                    "config/system_defaults.json",
                    {
                        "language": "KO",
                        "persona": "global_persona",
                        "max_debate_rounds": 1,
                        "codex_model": None,
                        "codex_reasoning_effort": effort,
                    },
                )
                with self.assertRaises(ConfigurationError) as caught:
                    self.manager.load_system_defaults()
                self.assertEqual(str(caught.exception), expected)

    def test_agent_codex_fields_accept_legacy_documents_and_validate_trailing_fields(
        self,
    ) -> None:
        legacy = self.workspace.read_json("config/agent_configs.json")
        self.assertEqual(
            set(legacy[PLOT_ARCHITECT]),
            {"language", "persona"},
        )
        loaded_legacy = self.manager.load_agent_configs()
        self.assertIsNone(loaded_legacy[PLOT_ARCHITECT].codex_model)
        self.assertIsNone(loaded_legacy[PLOT_ARCHITECT].codex_reasoning_effort)

        trailing = dict(legacy)
        trailing[PLOT_ARCHITECT] = {
            "backend": "codex",
            "language": "JA",
            "persona": "agent_persona",
            "codex_model": "agent-model",
            "codex_reasoning_effort": "xhigh",
        }
        self.workspace.write_json("config/agent_configs.json", trailing)
        loaded = self.manager.load_agent_configs()[PLOT_ARCHITECT]
        self.assertEqual(
            loaded,
            AgentConfig(
                backend="codex",
                language="JA",
                persona="agent_persona",
                codex_model="agent-model",
                codex_reasoning_effort="xhigh",
            ),
        )

        for field, value in (
            ("codex_model", "-unsafe"),
            ("codex_reasoning_effort", "default"),
        ):
            with self.subTest(field=field):
                invalid = dict(trailing)
                invalid[PLOT_ARCHITECT] = dict(trailing[PLOT_ARCHITECT], **{field: value})
                with self.assertRaises(ConfigurationError):
                    self.manager.validate_configuration(
                        self.workspace.read_json("config/system_defaults.json"),
                        invalid,
                    )

    def test_language_and_persona_cascade_independently(self) -> None:
        defaults = self.manager.resolve(CHARACTER_CUSTODIAN)
        self.assertEqual(defaults.backend, "codex")
        self.assertEqual((defaults.language, defaults.persona), ("KO", "global_persona"))

        agent = self.manager.resolve(PLOT_ARCHITECT)
        self.assertEqual(agent.backend, "codex")
        self.assertEqual((agent.language, agent.persona), ("JA", "agent_persona"))

        overridden_backend = self.manager.resolve(
            PLOT_ARCHITECT,
            backend_overrides={PLOT_ARCHITECT: "gemini"},
        )
        self.assertEqual(overridden_backend.backend, "gemini")

        runtime_language = self.manager.resolve(
            PLOT_ARCHITECT,
            {"language": "EN"},
        )
        self.assertEqual(
            (runtime_language.language, runtime_language.persona),
            ("EN", "agent_persona"),
        )

        runtime_persona = self.manager.resolve(
            PLOT_ARCHITECT,
            {"persona": "runtime_persona"},
        )
        self.assertEqual(
            (runtime_persona.language, runtime_persona.persona),
            ("JA", "runtime_persona"),
        )

        runtime_agent = self.manager.resolve(
            PLOT_ARCHITECT,
            {
                "language": "EN",
                "persona": "global_persona",
                "agents": {
                    PLOT_ARCHITECT: {
                        "language": "FR",
                        "persona": "runtime_persona",
                    }
                },
            },
        )
        self.assertEqual(
            (runtime_agent.language, runtime_agent.persona),
            ("FR", "runtime_persona"),
        )

    def test_persisted_configuration_rejects_unknown_keys(self) -> None:
        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
                "unexpected": True,
            },
        )
        with self.assertRaisesRegex(ConfigurationError, "unexpected"):
            self.manager.load_system_defaults()

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
            },
        )
        configs = {
            agent_id: {"language": None, "persona": None}
            for agent_id in ROSTER
        }
        configs[PLOT_ARCHITECT]["unexpected"] = True
        self.workspace.write_json("config/agent_configs.json", configs)
        with self.assertRaisesRegex(ConfigurationError, "unexpected"):
            self.manager.load_agent_configs()


class DirectiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/directive_persona.md",
            "directive-persona",
        )
        self.runtime = StoryRuntime(self.workspace)
        self.session_id = "session_a"

    def test_parser_accepts_only_exact_standalone_directives(self) -> None:
        language = parse_directive("[SYS: LANG=EN]")
        self.assertIsNotNone(language)
        assert language is not None
        self.assertEqual(language.kind, DirectiveKind.LANGUAGE)
        self.assertEqual(language.language, "EN")

        persona = parse_directive(
            "[OVERRIDE: plot_architect Persona=directive_persona]"
        )
        self.assertIsNotNone(persona)
        assert persona is not None
        self.assertEqual(persona.kind, DirectiveKind.PERSONA)
        self.assertEqual(persona.agent_id, PLOT_ARCHITECT)
        self.assertEqual(persona.persona, "directive_persona")

        reset = parse_directive("[RESET: ALL]")
        self.assertIsNotNone(reset)
        assert reset is not None
        self.assertEqual(reset.kind, DirectiveKind.RESET)

        near_misses = (
            " [SYS: LANG=EN]",
            "[SYS: LANG=EN]\n",
            "content [SYS: LANG=EN]",
            "[SYS: LANG=EN] content",
            "[RESET: all]",
            "[OVERRIDE: plot_architect Persona=directive_persona] trailing",
        )
        for text in near_misses:
            with self.subTest(text=text):
                self.assertIsNone(parse_directive(text))

    def test_directives_persist_across_runtime_instances_and_reset_only_runtime(
        self,
    ) -> None:
        system_before = self.workspace.read_text("config/system_defaults.json")
        agents_before = self.workspace.read_text("config/agent_configs.json")

        self.runtime.apply_directive(self.session_id, "[SYS: LANG=EN]")
        self.runtime.apply_directive(
            self.session_id,
            "[OVERRIDE: plot_architect Persona=directive_persona]",
        )

        fresh = StoryRuntime(Workspace(self.workspace.root))
        resolved = fresh.config.resolve(
            PLOT_ARCHITECT,
            fresh.workspace.load_session_overrides(self.session_id),
        )
        self.assertEqual((resolved.language, resolved.persona), ("EN", "directive_persona"))

        reset_state = fresh.apply_directive(self.session_id, "[RESET: ALL]")
        self.assertEqual(
            reset_state,
            {"language": None, "persona": None, "agents": {}},
        )
        self.assertEqual(
            self.workspace.read_text("config/system_defaults.json"),
            system_before,
        )
        self.assertEqual(
            self.workspace.read_text("config/agent_configs.json"),
            agents_before,
        )
        reset_resolved = StoryRuntime(Workspace(self.workspace.root)).config.resolve(
            PLOT_ARCHITECT,
            self.workspace.load_session_overrides(self.session_id),
        )
        self.assertEqual(
            (reset_resolved.language, reset_resolved.persona),
            ("KO", "default_kim_eun_hee"),
        )

    def test_invalid_or_embedded_directives_leave_state_unchanged(self) -> None:
        self.runtime.apply_directive(self.session_id, "[SYS: LANG=EN]")
        state_path = self.workspace.path(
            f"agent_states/orchestrator_state/sessions/{self.session_id}.json"
        )
        before = state_path.read_bytes()
        invalid = (
            "prefix [SYS: LANG=JA]",
            "[SYS: LANG=JA] suffix",
            "[OVERRIDE: unknown_agent Persona=directive_persona]",
            "[OVERRIDE: plot_architect Persona=missing_persona]",
            "[SYS: LANG=not valid]",
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(DirectiveError):
                    self.runtime.apply_directive(self.session_id, text)
                self.assertEqual(state_path.read_bytes(), before)

        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["language"], "EN")


if __name__ == "__main__":
    unittest.main()
