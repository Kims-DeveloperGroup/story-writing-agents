"""Shared Antigravity adapter test setup and native process inputs."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

from story_writing_agents import PLOT_ARCHITECT, antigravity_agent_definition
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ProviderInvocation
from story_writing_agents.workspace.session_artifact_binding import compose_session_artifact_binding
from story_writing_agents.workspace.session_resources import provision_session_resources
from tests.helpers import initialized_workspace, live_provider_execution_workspace, pristine_initialized_workspace


_UNSET = object()
_DEFAULT_RESULT_SCHEMA = {"type": "object"}


def _json_result(
    conversation_id: str,
    *,
    result_conversation_id: str | None = None,
    status: str = "SUCCESS",
    structured_output: object = _UNSET,
    json_schema: object = _DEFAULT_RESULT_SCHEMA,
    response: object = _UNSET,
    usage: object = _UNSET,
) -> bytes:
    """Build one native JSON result without provider access."""

    terminal: dict[str, object] = {
        "conversation_id": conversation_id if result_conversation_id is None else result_conversation_id,
        "status": status,}
    for field, value in (("structured_output", structured_output), ("json_schema", json_schema), ("response", response), ("usage", usage)):
        if value is not _UNSET:
            terminal[field] = value
    return (json.dumps(terminal, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _mode(path: Path) -> int:
    """Return only one path's no-follow permission bits."""

    return stat.S_IMODE(os.lstat(path).st_mode)


class AntigravityAdapterFixture:
    """Use the same concrete setup in both Antigravity test classes."""

    def setUp(self) -> None:
        self.workspace = (
            pristine_initialized_workspace(self)
            if self._testMethodName == "test_shared_default_and_explicit_timeouts_keep_native_and_outer_deadlines_distinct"
            else initialized_workspace(self)
        )
        codex_definition = PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=PLOT_ARCHITECT,)
        self.definition = replace(
            codex_definition,
            backend="antigravity",)

    def adapter(
        self,
        state_name: str,
        *,
        executable: str = sys.executable,
        timeout_seconds: float = 7,
        model: str | None = None,
        effort: str | None = None,
        system_model: str | None = None,
        system_effort: str | None = None,
        agent_settings=None,
        create_process=None,
        wait_for=None,
        which=None,
        capability_run=None,
    ) -> AntigravityAdapter:
        """Build one adapter with only the requested deterministic seams."""

        arguments: dict[str, object] = {
            "executable": executable,
            "timeout_seconds": timeout_seconds,
            "state_root": self.workspace.path(state_name),
            "explicit_model": model,
            "explicit_effort": effort,
            "system_model": system_model,
            "system_effort": system_effort,
            "agent_settings": agent_settings,}
        if create_process is not None:
            arguments["create_process"] = lambda: create_process
        if wait_for is not None:
            arguments["wait_for"] = lambda: wait_for
        if which is not None:
            arguments["which"] = lambda: which
        if capability_run is not None:
            arguments["capability_run"] = lambda: capability_run
        return AntigravityAdapter(**arguments)  # type: ignore[arg-type]

    def execution_workspace(self, session_id: str, *, install_role: bool = True):
        """Keep a real owner-issued call capability live for this test."""

        if not hasattr(self, "_execution_workspaces"):
            self._execution_control = pristine_initialized_workspace(self)
            self._execution_stack = contextlib.ExitStack()
            self.addCleanup(self._execution_stack.close)
            self._execution_workspaces = {}
        if session_id not in self._execution_workspaces:
            if install_role:
                provision_session_resources(self._execution_control.root, session_id)
                owner = compose_session_artifact_binding(self._execution_control.root, session_id)
                role = antigravity_agent_definition.custom_agent_projection(self.definition)[0]
                with owner.open_ready_resources() as access:
                    access.install_provider_role(role)
            self._execution_workspaces[session_id] = self._execution_stack.enter_context(
                live_provider_execution_workspace(self._execution_control.root, session_id))
        return self._execution_workspaces[session_id]

    def provider_invocation(self, session_id: str) -> ProviderInvocation:
        """Build one minimal valid adapter invocation."""

        return ProviderInvocation(
            session_id=session_id,
            schema={"type": "object"},
            payload={"story": "PRIVATE_STORY_PAYLOAD"},
            thread_id=None,
            execution_workspace=self.execution_workspace(session_id),
        )

    def directory_symlink(self, link: Path, target: Path) -> None:
        """Create one directory symlink or precisely skip unsupported hosts."""

        self.file_symlink(link, target, target_is_directory=True)

    def file_symlink(
        self,
        link: Path,
        target: Path,
        *,
        target_is_directory: bool = False,
    ) -> None:
        """Create one symlink or precisely skip unsupported hosts."""

        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except NotImplementedError as exc:
            self.skipTest(f"symlinks are unsupported: {exc}")
        except OSError as exc:
            unsupported = {
                errno.EACCES,
                errno.EPERM,
                errno.ENOSYS,
                getattr(errno, "ENOTSUP", errno.EINVAL),
                getattr(errno, "EOPNOTSUPP", errno.EINVAL),
            }
            if exc.errno in unsupported:
                self.skipTest(f"symlinks are unsupported: {exc}")
            raise

    def result_bytes(
        self,
        adapter: AntigravityAdapter,
        session_id: str,
        conversation_id: str,
        **values: object,
    ) -> bytes:
        """Build one valid native result for a prepared invocation."""

        return _json_result(conversation_id, **values)

    def assert_agent_installation(self, target: Path, expected: bytes, *, mode: int = 0o600) -> None:
        """Assert one exact private custom-agent file and no temporary residue."""

        self.assertEqual(target.read_bytes(), expected)
        if os.name == "posix" and hasattr(os, "fchmod"):
            self.assertEqual(_mode(target), mode)
        self.assertEqual(
            [
                entry.name
                for entry in target.parent.iterdir()
                if entry.name.startswith(f".{target.name}.")],
            [],
        )
