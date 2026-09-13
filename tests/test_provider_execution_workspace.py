"""Provider process workspace capability and Darwin confinement tests."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pickle
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from story_writing_agents.git.session_resources import GitSessionGenerationPin
from story_writing_agents.codex_adapter import CodexAdapter
from story_writing_agents.orchestrator import _codex_execution_workspace_manager
from story_writing_agents.provider_execution_workspace import (
    ProviderExecutionWorkspace,
    mint_provider_execution_workspace,
)
from story_writing_agents import provider_process as process_module
from story_writing_agents.provider_process import (
    DarwinSandboxRequirements,
    ProcessRequest,
    ProcessResult,
    ProcessStartFailed,
    run_process,
)
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
    provision_session_resources,
)

from tests.helpers import pristine_initialized_workspace


_PROVENANCE = "sha256:" + "1" * 64


class _BindingLost(RuntimeError):
    pass


class ProviderExecutionWorkspaceTests(unittest.TestCase):
    """Exercise the opaque call-local capability without Git fixture cost."""

    def setUp(self) -> None:
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-workspace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "session"
        self.root.mkdir()
        self.pin = object.__new__(GitSessionGenerationPin)
        self.require_pin = mock.patch.object(
            GitSessionGenerationPin,
            "require_current",
            autospec=True,
        ).start()
        self.close_pin = mock.patch.object(
            GitSessionGenerationPin,
            "close",
            autospec=True,
        ).start()
        self.addCleanup(mock.patch.stopall)

    def mint(self) -> ProviderExecutionWorkspace:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        identity = os.fstat(descriptor)
        try:
            return mint_provider_execution_workspace(
                session_id="session",
                worktree_root=self.root,
                root_identity=(identity.st_dev, identity.st_ino),
                generation_provenance=_PROVENANCE,
                root_descriptor=descriptor,
                generation_pin=self.pin,
                failure_factory=_BindingLost,
            )
        except BaseException:
            os.close(descriptor)
            raise

    def test_construction_is_mint_only_opaque_and_immutable(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        identity = os.fstat(descriptor)
        try:
            with self.assertRaisesRegex(TypeError, "invalid"):
                ProviderExecutionWorkspace(
                    object(),
                    session_id="session",
                    worktree_root=self.root,
                    root_identity=(identity.st_dev, identity.st_ino),
                    generation_provenance=_PROVENANCE,
                    root_descriptor=descriptor,
                    generation_pin=self.pin,
                    failure_factory=_BindingLost,
                )
        finally:
            os.close(descriptor)

        workspace = self.mint()
        self.addCleanup(workspace.close)
        self.assertEqual(workspace.session_id, "session")
        self.assertEqual(workspace.worktree_root, self.root)
        self.assertEqual(workspace.generation_provenance, _PROVENANCE)
        self.assertEqual(repr(workspace), "ProviderExecutionWorkspace(<opaque>)")
        with self.assertRaises(AttributeError):
            workspace.session_id = "other"  # type: ignore[misc]
        with self.assertRaisesRegex(TypeError, "cannot be copied"):
            copy.copy(workspace)
        with self.assertRaisesRegex(TypeError, "cannot be copied"):
            copy.deepcopy(workspace)
        with self.assertRaisesRegex(TypeError, "cannot be serialized"):
            pickle.dumps(workspace)

    def test_continuation_digest_is_generation_and_domain_bound(self) -> None:
        workspace = self.mint()
        self.addCleanup(workspace.close)
        first = workspace.continuation_digest("agent", "sha256:existing")
        self.assertEqual(first, workspace.continuation_digest("agent", "sha256:existing"))
        self.assertNotEqual(
            first,
            workspace.continuation_digest("context", "sha256:existing"),
        )
        self.assertRegex(first, r"\Asha256:[0-9a-f]{64}\Z")
        for domain, digest in (("", "value"), ("agent", "")):
            with self.subTest(domain=domain, digest=digest), self.assertRaises(TypeError):
                workspace.continuation_digest(domain, digest)

    def test_missing_symlink_and_replaced_named_roots_fail_closed(self) -> None:
        for case in ("missing", "symlink", "replacement"):
            with self.subTest(case=case):
                self.root.mkdir(exist_ok=True)
                workspace = self.mint()
                moved = self.root.with_name("session-original")
                self.root.rename(moved)
                if case == "symlink":
                    self.root.symlink_to(moved, target_is_directory=True)
                elif case == "replacement":
                    self.root.mkdir()
                try:
                    with self.assertRaises(_BindingLost):
                        workspace.require_current()
                    with self.assertRaises(_BindingLost):
                        workspace.duplicate_launch_descriptor()
                finally:
                    workspace.close()
                    if self.root.is_symlink():
                        self.root.unlink()
                    elif self.root.exists():
                        self.root.rmdir()
                    moved.rename(self.root)

    def test_foreign_pid_and_thread_cannot_use_capability(self) -> None:
        workspace = self.mint()
        failures: list[BaseException] = []

        def foreign() -> None:
            for operation in (
                workspace.require_current,
                workspace.duplicate_launch_descriptor,
            ):
                try:
                    operation()
                except BaseException as failure:
                    failures.append(failure)

        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([type(item) for item in failures], [_BindingLost, _BindingLost])

        object.__setattr__(workspace, "_creator_pid", os.getpid() + 1)
        with self.assertRaises(_BindingLost):
            workspace.require_current()
        workspace.close()
        workspace.close()
        self.close_pin.assert_called_once_with(self.pin)

    def test_concurrent_close_is_idempotent_and_releases_descriptor_once(self) -> None:
        workspace = self.mint()
        descriptor = workspace._root_descriptor
        failures: list[BaseException] = []

        def close() -> None:
            try:
                workspace.close()
            except BaseException as failure:
                failures.append(failure)

        threads = [threading.Thread(target=close) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(failures, [])
        self.close_pin.assert_called_once_with(self.pin)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        with self.assertRaises(_BindingLost):
            workspace.require_current()

    def test_mint_rejects_foreign_root_identity_without_consuming_descriptor(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        identity = os.fstat(descriptor)
        with self.assertRaisesRegex(TypeError, "root is invalid"):
            mint_provider_execution_workspace(
                session_id="session",
                worktree_root=self.root,
                root_identity=(identity.st_dev, identity.st_ino + 1),
                generation_provenance=_PROVENANCE,
                root_descriptor=descriptor,
                generation_pin=self.pin,
                failure_factory=_BindingLost,
            )
        os.fstat(descriptor)
        os.close(descriptor)

    def test_story_runtime_specialist_bridge_acquires_only_for_codex(self) -> None:
        control = pristine_initialized_workspace(self)
        provision_session_resources(control.root, "story_session")
        owner = compose_session_artifact_binding(control.root, "story_session")
        open_workspace = mock.Mock(side_effect=owner.open_provider_execution_workspace)
        binding = SimpleNamespace(open_provider_execution_workspace=open_workspace)

        antigravity = SimpleNamespace(vendor="antigravity", agent_id="plot_architect")
        self.assertIsNone(_codex_execution_workspace_manager(antigravity, binding))
        open_workspace.assert_not_called()

        codex = SimpleNamespace(vendor="codex", agent_id="plot_architect")
        manager = _codex_execution_workspace_manager(codex, binding)
        assert manager is not None
        with manager as execution_workspace:
            execution_workspace.require_current()
            self.assertEqual(execution_workspace.session_id, "story_session")
        open_workspace.assert_called_once_with()
        with self.assertRaises(SessionArtifactBindingError):
            execution_workspace.require_current()

    def test_codex_command_uses_session_relative_cwd_and_no_staging_fallback(self) -> None:
        workspace = self.mint()
        self.addCleanup(workspace.close)
        base = Path(self.temporary.name).resolve()
        executable = base / "native-codex"
        executable.write_bytes(b"native-placeholder")
        staging = base / "staging"
        staging.mkdir()
        instructions = "Exact staged instructions"
        (staging / "AGENTS.md").write_text(instructions, encoding="utf-8")
        schema = base / "schema.json"
        output = base / "response.json"
        schema.write_text("{}", encoding="utf-8")
        native = base / "codex-home"
        owner = SimpleNamespace(
            executable="codex",
            _which=mock.Mock(return_value=mock.Mock(return_value=str(executable))),
            invocation_settings=mock.Mock(return_value=(None, None)),
        )
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(native)}):
            command, sandbox = CodexAdapter._confined_command(
                owner,  # type: ignore[arg-type]
                staging,
                schema,
                output,
                None,
                "plot_architect",
                workspace,
            )
        self.assertEqual(command[0], str(executable))
        self.assertEqual(command[command.index("--cd") + 1], ".")
        self.assertIn(
            "developer_instructions=" + json.dumps(instructions),
            command,
        )
        self.assertNotIn(str(staging), command)
        self.assertNotIn("project_doc_max_bytes=", "\n".join(command))
        self.assertEqual(sandbox.executable, executable)
        self.assertEqual(sandbox.read_subpaths, (executable.parent, self.root))
        self.assertEqual(sandbox.read_paths, (schema,))
        self.assertEqual(sandbox.write_subpaths, (native,))
        self.assertEqual(sandbox.write_paths, (output,))
        self.assertEqual(sandbox.deny_subpaths, (self.root / ".git", staging))
        self.assertEqual(sandbox.deny_write_subpaths, (self.root,))
        self.assertTrue(sandbox.network)


class ProviderDescriptorExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_helper_executes_in_retained_original_inode_without_fd_leak(self) -> None:
        parent_cwd = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="provider-descriptor-") as temporary:
            base = Path(temporary).resolve()
            root = base / "session"
            moved = base / "moved"
            root.mkdir()
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            expected = os.fstat(descriptor)
            root.rename(moved)
            root.mkdir()
            replacement = os.stat(root)
            probe = (
                "import fcntl,json,os,sys;opened=[];"
                "[(opened.append(fd) if not _closed(fd,fcntl) else None) "
                "for fd in range(3,256)];s=os.stat('.');"
                "print(json.dumps({'dev':s.st_dev,'ino':s.st_ino,'fds':opened," 
                "'passed_open':int(sys.argv[1]) in opened}))"
            )
            helper_prelude = (
                "def _closed(fd,fcntl):\n"
                " try: fcntl.fcntl(fd,fcntl.F_GETFD); return False\n"
                " except OSError: return True\n"
            )
            command = (
                os.path.realpath(sys.executable),
                "-I",
                "-c",
                process_module._DESCRIPTOR_EXEC_HELPER,
                str(descriptor),
                os.path.realpath(sys.executable),
                "-I",
                "-c",
                helper_prelude + probe,
                str(descriptor),
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    pass_fds=(descriptor,),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
            finally:
                os.close(descriptor)
            self.assertEqual(process.returncode, 0, stderr.decode())
            observed = json.loads(stdout)
            self.assertEqual((observed["dev"], observed["ino"]), (expected.st_dev, expected.st_ino))
            self.assertNotEqual(
                (observed["dev"], observed["ino"]),
                (replacement.st_dev, replacement.st_ino),
            )
            self.assertEqual(observed["fds"], [])
            self.assertFalse(observed["passed_open"])
            self.assertEqual(os.getcwd(), parent_cwd)


class DarwinSandboxCompilationTests(unittest.TestCase):
    def test_profile_has_exact_allows_and_deny_overrides(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin SBPL is platform-specific")
        with tempfile.TemporaryDirectory(prefix="provider-sandbox-") as temporary:
            root = Path(temporary).resolve()
            executable = Path(os.path.realpath(sys.executable))
            session = root / "session"
            schema = root / "schema.json"
            output = root / "response.json"
            native = root / "native"
            staging = root / "staging"
            requirements = DarwinSandboxRequirements(
                executable=executable,
                read_subpaths=(executable.parent, session),
                read_paths=(schema,),
                write_subpaths=(native,),
                write_paths=(output,),
                deny_subpaths=(session / ".git", staging),
                deny_write_subpaths=(session,),
                network=True,
            )
            profile = process_module._darwin_sandbox_profile(requirements)
        self.assertIn("(deny default)", profile)
        self.assertIn(f'(allow process-exec (literal "{executable}"))', profile)
        self.assertIn('(allow file-read-data (literal "/"))', profile)
        self.assertIn(f'(allow file-read* (subpath "{session}"))', profile)
        self.assertIn(f'(allow file-read* (literal "{schema}"))', profile)
        self.assertIn(f'(allow file-write* (literal "{output}"))', profile)
        self.assertIn(f'(deny file-read* (subpath "{session / ".git"}"))', profile)
        self.assertIn(f'(deny file-read* (subpath "{staging}"))', profile)
        self.assertIn(f'(deny file-write* (subpath "{session}"))', profile)
        self.assertIn("(allow network-outbound)", profile)

    def test_confinement_rejects_wrong_executable_and_noncanonical_inputs(self) -> None:
        executable = Path(os.path.realpath(sys.executable))
        requirements = DarwinSandboxRequirements(executable=executable)
        with mock.patch.object(process_module.sys, "platform", "darwin"):
            with self.assertRaisesRegex(ProcessStartFailed, "InvalidDarwinSandboxExecutable"):
                process_module._darwin_confined_command(("wrong",), requirements)
        with self.assertRaisesRegex(ValueError, "canonical paths"):
            DarwinSandboxRequirements(executable=Path("relative"))


class ProcessWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_rejects_cwd_or_sandbox_without_exact_workspace(self) -> None:
        executable = Path(os.path.realpath(sys.executable))
        requirements = DarwinSandboxRequirements(executable=executable)
        with self.assertRaisesRegex(ValueError, "execution workspace"):
            ProcessRequest((str(executable),), b"", 1, darwin_sandbox=requirements)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            ProcessRequest(
                (str(executable),),
                b"",
                1,
                cwd=Path("/"),
                execution_workspace=object(),  # type: ignore[arg-type]
            )

    async def test_run_process_transfers_then_closes_launch_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="provider-process-") as temporary:
            root = Path(temporary).resolve()
            pin = object.__new__(GitSessionGenerationPin)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            identity = os.fstat(descriptor)
            with mock.patch.object(GitSessionGenerationPin, "require_current"), mock.patch.object(
                GitSessionGenerationPin,
                "close",
            ):
                workspace = mint_provider_execution_workspace(
                    session_id="session",
                    worktree_root=root,
                    root_identity=(identity.st_dev, identity.st_ino),
                    generation_provenance=_PROVENANCE,
                    root_descriptor=descriptor,
                    generation_pin=pin,
                    failure_factory=_BindingLost,
                )
                transferred = -1
                process = mock.Mock(returncode=0)

                async def communicate(value: bytes) -> tuple[bytes, bytes]:
                    self.assertEqual(value, b"payload")
                    return b"out", b"err"

                process.communicate = communicate

                async def create(*command: object, **options: object) -> object:
                    nonlocal transferred
                    self.assertEqual(command[2], "-c")
                    self.assertEqual(command[3], process_module._DESCRIPTOR_EXEC_HELPER)
                    transferred = options["pass_fds"][0]  # type: ignore[index]
                    os.fstat(transferred)
                    return process

                try:
                    result = await run_process(
                        ProcessRequest(
                            command=(os.path.realpath(sys.executable), "--version"),
                            stdin=b"payload",
                            timeout_seconds=1,
                            execution_workspace=workspace,
                        ),
                        create_process=create,
                    )
                finally:
                    workspace.close()
            self.assertEqual(result, ProcessResult(b"out", b"err", 0))
            with self.assertRaises(OSError):
                os.fstat(transferred)

    async def test_postspawn_and_postreturn_generation_loss_fail_closed(self) -> None:
        for stage, require_results, expected_communicates in (
            ("postspawn", (None, None, None, _BindingLost()), 0),
            ("postreturn", (None, None, None, None, _BindingLost()), 1),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(
                prefix=f"provider-{stage}-"
            ) as temporary:
                root = Path(temporary).resolve()
                pin = object.__new__(GitSessionGenerationPin)
                descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                identity = os.fstat(descriptor)
                workspace = mint_provider_execution_workspace(
                    session_id="session",
                    worktree_root=root,
                    root_identity=(identity.st_dev, identity.st_ino),
                    generation_provenance=_PROVENANCE,
                    root_descriptor=descriptor,
                    generation_pin=pin,
                    failure_factory=_BindingLost,
                )
                process = mock.Mock(returncode=0)
                process.communicate = mock.AsyncMock(return_value=(b"out", b"err"))

                async def create(*_command: object, **_options: object) -> object:
                    return process

                with mock.patch.object(
                    GitSessionGenerationPin, "close", autospec=True,
                ), mock.patch.object(
                        ProviderExecutionWorkspace,
                        "require_current",
                        autospec=True,
                        side_effect=require_results,
                ), mock.patch.object(
                        process_module,
                        "stop_process",
                        new=mock.AsyncMock(),
                ) as stop:
                    try:
                        with self.assertRaises(_BindingLost):
                            await run_process(
                                ProcessRequest(
                                    command=(os.path.realpath(sys.executable), "--version"),
                                    stdin=b"payload",
                                    timeout_seconds=1,
                                    execution_workspace=workspace,
                                ),
                                create_process=create,
                            )
                    finally:
                        workspace.close()
                self.assertEqual(process.communicate.await_count, expected_communicates)
                if stage == "postspawn":
                    stop.assert_awaited_once_with(process, wait_for=asyncio.wait_for)
                else:
                    stop.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
