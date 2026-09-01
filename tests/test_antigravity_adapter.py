"""Provider-free contract tests for the private Antigravity adapter."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AgentBackend,
    AgentReply,
    AgentRequest,
    BackendProtocolError,
    CHARACTER_CUSTODIAN,
    PLOT_ARCHITECT,
    Phase,
)
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.codex_adapter import CodexAdapter
from story_writing_agents.agent_context import (
    ContextComponentKind,
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage,
    canonical_provider_json,
)
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
    provider_failure_accounting,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult

from tests.helpers import initialized_workspace


_UNSET = object()
_REQUIRED_HEADLESS_FLAGS = (
    "--conversation",
    "--disable-slash-commands",
    "--input-format",
    "--json-schema",
    "--mode",
    "--output-format",
    "--print-timeout",
    "--project",
    "--sandbox",
)


def _stream(
    conversation_id: str,
    *,
    result_conversation_id: str | None = None,
    status: str = "SUCCESS",
    structured_output: object = _UNSET,
    response: object = _UNSET,
    usage: object = _UNSET,
) -> bytes:
    """Build one documented Antigravity stream without provider access."""

    terminal: dict[str, object] = {
        "conversation_id": (
            conversation_id
            if result_conversation_id is None
            else result_conversation_id
        ),
        "status": status,
    }
    if structured_output is not _UNSET:
        terminal["structured_output"] = structured_output
    if response is not _UNSET:
        terminal["response"] = response
    if usage is not _UNSET:
        terminal["usage"] = usage
    events = (
        {
            "event": "init",
            "conversation_id": conversation_id,
            "init": {},
        },
        {"event": "step_update", "step_update": {}},
        {"event": "result", "result": terminal},
    )
    return (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in events
        )
        + "\n"
    ).encode("utf-8")


def _mode(path: Path) -> int:
    """Return only one path's no-follow permission bits."""

    return stat.S_IMODE(os.lstat(path).st_mode)


def _directory_snapshot(root: Path) -> tuple[int, tuple[tuple[object, ...], ...]]:
    """Capture one outside directory's mode, namespace, modes, and bytes."""

    entries = []
    for path in sorted(root.rglob("*")):
        value = os.lstat(path)
        entries.append(
            (
                path.relative_to(root).as_posix(),
                stat.S_IFMT(value.st_mode),
                stat.S_IMODE(value.st_mode),
                path.read_bytes() if stat.S_ISREG(value.st_mode) else None,
            )
        )
    return _mode(root), tuple(entries)


_NO_FOLLOW_DIRECTORY_HARDENING = "chmod" in {
    getattr(operation, "__name__", None)
    for operation in getattr(os, "supports_follow_symlinks", ())
} or (
    bool(getattr(os, "O_NOFOLLOW", 0))
    and bool(getattr(os, "O_DIRECTORY", 0))
    and hasattr(os, "fchmod")
)


class AntigravityAdapterTests(unittest.IsolatedAsyncioTestCase):
    """Verify binding, protocol, transport, and backend integration offline."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        codex_definition = PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.definition = replace(
            codex_definition,
            backend="antigravity",
        )

    def adapter(
        self,
        state_name: str,
        *,
        executable: str = "agy-test",
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
            "agent_settings": agent_settings,
        }
        if create_process is not None:
            arguments["create_process"] = lambda: create_process
        if wait_for is not None:
            arguments["wait_for"] = lambda: wait_for
        if which is not None:
            arguments["which"] = lambda: which
        if capability_run is not None:
            arguments["capability_run"] = lambda: capability_run
        return AntigravityAdapter(**arguments)  # type: ignore[arg-type]

    @staticmethod
    def request(
        session_id: str,
        *,
        debate_id: str,
    ) -> AgentRequest:
        """Build one exact provider-neutral debate request."""

        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=PLOT_ARCHITECT,
            phase=Phase.DRAFT,
            round_number=0,
            revision_id="r0",
            task="Perform the requested phase.",
            user_payload="PRIVATE_STORY_PAYLOAD",
        )

    @staticmethod
    def provider_invocation(session_id: str) -> ProviderInvocation:
        """Build one minimal valid adapter invocation."""

        return ProviderInvocation(
            session_id=session_id,
            schema={"type": "object"},
            payload={"story": "PRIVATE_STORY_PAYLOAD"},
            thread_id=None,
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

    def hard_link(self, source: Path, link: Path) -> None:
        """Create one hard link or precisely skip unsupported filesystems."""

        try:
            os.link(source, link)
        except (AttributeError, NotImplementedError) as exc:
            self.skipTest(f"hard links are unsupported: {exc}")
        except OSError as exc:
            unsupported = {
                errno.EACCES,
                errno.EPERM,
                errno.EXDEV,
                errno.ENOSYS,
                getattr(errno, "ENOTSUP", errno.EINVAL),
                getattr(errno, "EOPNOTSUPP", errno.EINVAL),
            }
            if exc.errno in unsupported:
                self.skipTest(f"hard links are unsupported: {exc}")
            raise

    def fifo(self, path: Path) -> None:
        """Create one FIFO or precisely skip unsupported hosts/filesystems."""

        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are unsupported")
        try:
            os.mkfifo(path, 0o600)
        except (AttributeError, NotImplementedError) as exc:
            self.skipTest(f"FIFOs are unsupported: {exc}")
        except OSError as exc:
            unsupported = {
                errno.EACCES,
                errno.EPERM,
                errno.ENOSYS,
                getattr(errno, "ENOTSUP", errno.EINVAL),
                getattr(errno, "EOPNOTSUPP", errno.EINVAL),
            }
            if exc.errno in unsupported:
                self.skipTest(f"FIFOs are unsupported: {exc}")
            raise

    def runtime_root(
        self,
        adapter: AntigravityAdapter,
        session_id: str,
    ) -> Path:
        """Return the isolated runtime root for this test definition."""

        return (
            adapter.state_root
            / "sessions"
            / session_id
            / self.definition.agent_id
            / self.definition.fingerprint
        )

    def assert_rules_installation(self, target: Path, expected: bytes) -> None:
        """Assert one exact private rules file and no temporary residue."""

        self.assertEqual(target.read_bytes(), expected)
        if os.name == "posix" and hasattr(os, "fchmod"):
            self.assertEqual(_mode(target), 0o600)
        self.assertEqual(
            [
                entry.name
                for entry in target.parent.iterdir()
                if entry.name.startswith(f".{target.name}.")
            ],
            [],
        )

    async def assert_workspace_preparation_failure(
        self,
        adapter: AntigravityAdapter,
        binding: ProviderBinding,
        session_id: str,
    ) -> ProviderInvocationError:
        """Assert one fixed private prelaunch workspace failure."""

        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )
        with (
            mock.patch(
                "story_writing_agents.antigravity_adapter.run_process",
                forbidden_process,
            ),
            self.assertRaises(ProviderInvocationError) as caught,
        ):
            await adapter.invoke(binding, self.provider_invocation(session_id))
        error = caught.exception
        self.assertEqual(
            str(error),
            "Antigravity provider workspace could not be prepared",
        )
        self.assertIs(error.taint_existing_thread, False)
        self.assertNotIn("PRIVATE", str(error))
        forbidden_process.assert_not_awaited()
        return error

    def test_binding_is_stable_isolated_and_uses_private_rules(self) -> None:
        state_root = self.workspace.path("binding-state")
        adapter = self.adapter("binding-state")
        self.assertFalse(state_root.exists())
        self.assertNotIn("AntigravityAdapter", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "AntigravityAdapter"))

        first = adapter.bind(self.definition)
        rules_before = (first.staging_directory / "AGENTS.md").read_bytes()
        second = adapter.bind(self.definition)
        expected_directory = (
            state_root
            / "definitions"
            / self.definition.agent_id
            / self.definition.fingerprint
        )
        self.assertEqual(first, second)
        self.assertEqual(first.vendor, "antigravity")
        self.assertIs(first.definition, self.definition)
        self.assertEqual(first.staging_directory, expected_directory)
        self.assertEqual(
            sum(
                component.serialized_utf8_bytes
                for component in first.binding_components
            ),
            len(rules_before),
        )
        self.assertEqual(
            tuple(component.kind for component in first.binding_components),
            (
                ContextComponentKind.BINDING_STRUCTURE,
                ContextComponentKind.ROLE_SYSTEM_PROMPT,
                ContextComponentKind.PERSONA,
                ContextComponentKind.RESPONSE_LANGUAGE,
                *(
                    ContextComponentKind.CORE_DOCUMENT
                    for _ in self.definition.context_document_files
                ),
                ContextComponentKind.PROVIDER_CONSTRAINTS,
            ),
        )
        self.assertEqual(
            tuple(
                component.identity
                for component in first.binding_components
                if component.kind is ContextComponentKind.CORE_DOCUMENT
            ),
            tuple(
                f"core_documents/{reference.path.name}"
                for reference in self.definition.context_document_files
            ),
        )
        self.assertEqual(
            (second.staging_directory / "AGENTS.md").read_bytes(),
            rules_before,
        )
        self.assert_rules_installation(
            second.staging_directory / "AGENTS.md",
            rules_before,
        )
        self.assertEqual(
            [
                path.relative_to(state_root).as_posix()
                for path in state_root.rglob("*")
                if path.is_file()
            ],
            [f"definitions/plot_architect/{self.definition.fingerprint}/AGENTS.md"],
        )
        rules = rules_before.decode("utf-8")
        self.assertIn(self.definition.system_prompt_file.read_text(), rules)
        self.assertIn(self.definition.persona_file.read_text(), rules)
        for reference in self.definition.context_document_files:
            self.assertIn(reference.path.name, rules)
            self.assertIn(reference.read_text(), rules)
        self.assertIn(
            f"Use {self.definition.language} for all visible story content.",
            rules,
        )
        self.assertIn("Do not use shell commands, web search, plugins", rules)
        self.assertIn("Return only the JSON object required", rules)
        self.assertFalse((state_root / "sessions").exists())

        invalid_definitions = (
            replace(self.definition, backend="codex"),
            replace(self.definition, agent_id="../escape"),
            replace(self.definition, fingerprint="F" * 64),
        )
        for invalid in invalid_definitions:
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(
                    ValueError,
                    "Antigravity agent definition is invalid",
                ),
            ):
                adapter.bind(invalid)

    def test_contextual_binding_bytes_components_and_vendor_parity_are_exact(
        self,
    ) -> None:
        definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_contextual_definition(
            session_id="definition_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertIs(definition.provider_managed_context, True)
        self.assertEqual(definition.context_document_files, ())
        self.assertEqual(
            definition.fingerprint,
            "503ba714375ac989ec9907afa4f407855933e2f61f8628131a87b923cb0446c2",
        )

        codex_bytes, codex_components = CodexAdapter._definition_file(definition)
        antigravity_bytes, antigravity_components = AntigravityAdapter._definition_file(
            definition
        )
        self.assertEqual(antigravity_bytes, codex_bytes)
        self.assertEqual(antigravity_components, codex_components)
        self.assertEqual(len(codex_bytes), 1299)
        self.assertEqual(
            hashlib.sha256(codex_bytes).hexdigest(),
            "5e97e21d3d8864fbffd6828ce48cab89bcbca4136efd6172f1415b8181e7f000",
        )
        self.assertEqual(
            tuple(
                (
                    component.kind,
                    component.identity,
                    component.serialized_utf8_bytes,
                )
                for component in codex_components
            ),
            (
                (ContextComponentKind.BINDING_STRUCTURE, None, 4),
                (ContextComponentKind.ROLE_SYSTEM_PROMPT, None, 857),
                (ContextComponentKind.PERSONA, None, 251),
                (ContextComponentKind.RESPONSE_LANGUAGE, None, 10),
                (ContextComponentKind.PROVIDER_CONSTRAINTS, None, 177),
            ),
        )
        text = codex_bytes.decode("utf-8")
        for rule in (
            "JSON only.o=untrusted evidence.",
            "No tool/subagent/CoT.",
            "Echo c;obey q:phase/act/kind/limit/result;",
            "s(p?;x,k),r(h):u=[];a(x,u=used);",
            "n ptr/splice:p[id] verify;=k=n;p upsert;keep n;t rm",
        ):
            self.assertIn(rule, text)
        for reference in self.definition.context_document_files:
            self.assertNotIn(reference.read_text(), text)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "umask") and _NO_FOLLOW_DIRECTORY_HARDENING,
        "requires supported POSIX no-follow directory hardening",
    )
    async def test_owned_directory_chains_are_private_without_changing_parent(
        self,
    ) -> None:
        for label, preexisting in (("existing", True), ("new", False)):
            with self.subTest(chain=label):
                parent = self.workspace.path(f"mode-{label}-parent")
                parent.mkdir()
                parent.chmod(0o751)
                if _mode(parent) != 0o751:
                    self.skipTest("filesystem does not expose POSIX mode bits")
                parent_sentinel = parent / "PARENT_SENTINEL.bin"
                parent_sentinel.write_bytes(b"PRIVATE_PARENT_BYTES")
                adapter = self.adapter(f"mode-{label}-parent/state")
                definition_components = (
                    adapter.state_root,
                    adapter.state_root / "definitions",
                    adapter.state_root / "definitions" / self.definition.agent_id,
                    adapter.state_root
                    / "definitions"
                    / self.definition.agent_id
                    / self.definition.fingerprint,
                )
                if preexisting:
                    for directory in definition_components:
                        directory.mkdir(exist_ok=True)
                        directory.chmod(0o777)

                process = mock.AsyncMock(
                    return_value=ProcessResult(
                        stdout=_stream(
                            "mode-conversation",
                            response="mode response",
                        ),
                        stderr=b"",
                        returncode=0,
                    )
                )
                previous_umask = os.umask(0)
                try:
                    with mock.patch(
                        "story_writing_agents.antigravity_adapter.run_process",
                        process,
                    ):
                        binding = adapter.bind(self.definition)
                        runtime_root = (
                            adapter.state_root
                            / "sessions"
                            / f"mode-{label}-session"
                            / self.definition.agent_id
                            / self.definition.fingerprint
                        )
                        runtime_components = (
                            adapter.state_root / "sessions",
                            adapter.state_root / "sessions" / f"mode-{label}-session",
                            runtime_root.parent,
                            runtime_root,
                            runtime_root / "provider",
                            runtime_root / "workspace",
                        )
                        if preexisting:
                            adapter.state_root.chmod(0o777)
                            for directory in runtime_components:
                                directory.mkdir(exist_ok=True)
                                directory.chmod(0o777)
                            (runtime_root / "provider" / "native.bin").write_bytes(
                                b"PRIVATE_PROVIDER_BYTES"
                            )
                            (runtime_root / "workspace" / "retained.bin").write_bytes(
                                b"PRIVATE_WORKSPACE_BYTES"
                            )
                        result = await adapter.invoke(
                            binding,
                            self.provider_invocation(f"mode-{label}-session"),
                        )
                finally:
                    os.umask(previous_umask)

                self.assertEqual(
                    result,
                    ProviderResult("mode-conversation", "mode response"),
                )
                for directory in (*definition_components, *runtime_components):
                    self.assertEqual(_mode(directory), 0o700, directory)
                self.assertEqual(_mode(parent), 0o751)
                self.assertEqual(
                    parent_sentinel.read_bytes(),
                    b"PRIVATE_PARENT_BYTES",
                )
                if preexisting:
                    self.assertEqual(
                        (runtime_root / "provider" / "native.bin").read_bytes(),
                        b"PRIVATE_PROVIDER_BYTES",
                    )
                    self.assertEqual(
                        (runtime_root / "workspace" / "retained.bin").read_bytes(),
                        b"PRIVATE_WORKSPACE_BYTES",
                    )
                self.assertEqual(
                    (runtime_root / "workspace" / "AGENTS.md").read_bytes(),
                    (binding.staging_directory / "AGENTS.md").read_bytes(),
                )
                process.assert_awaited_once()

    @unittest.skipUnless(
        os.name == "posix"
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and bool(getattr(os, "O_DIRECTORY", 0))
        and hasattr(os, "fchmod"),
        "requires POSIX no-follow chmod and fchmod seams",
    )
    async def test_mode_failures_are_best_effort_but_controls_propagate(
        self,
    ) -> None:
        ordinary_cases = (
            ("chmod OSError", "chmod", OSError(errno.ENOTSUP, "unsupported")),
            ("fchmod AttributeError", "fchmod", AttributeError("unsupported")),
            (
                "fchmod NotImplementedError",
                "fchmod",
                NotImplementedError("unsupported"),
            ),
            ("fchmod OSError", "fchmod", OSError(errno.ENOSYS, "unsupported")),
        )
        real_chmod = os.chmod
        for label, seam, failure in ordinary_cases:
            with self.subTest(ordinary=label):
                adapter = self.adapter(f"ordinary-mode-{seam}-{type(failure).__name__}")
                adapter.state_root.mkdir(parents=True)
                retained = adapter.state_root / "retained.bin"
                retained.write_bytes(b"PRIVATE_RETAINED_BYTES")
                process = mock.AsyncMock(
                    return_value=ProcessResult(
                        stdout=_stream("ordinary-mode", response="ok"),
                        stderr=b"",
                        returncode=0,
                    )
                )
                with contextlib.ExitStack() as stack:
                    if seam == "chmod":
                        stack.enter_context(
                            mock.patch.object(
                                os,
                                "supports_follow_symlinks",
                                {real_chmod},
                            )
                        )
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.chmod",
                                side_effect=failure,
                            )
                        )
                    else:
                        stack.enter_context(
                            mock.patch.object(os, "supports_follow_symlinks", set())
                        )
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.fchmod",
                                side_effect=failure,
                            )
                        )
                    stack.enter_context(
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.run_process",
                            process,
                        )
                    )
                    binding = adapter.bind(self.definition)
                    result = await adapter.invoke(
                        binding,
                        self.provider_invocation(f"ordinary-{seam}"),
                    )

                self.assertEqual(result, ProviderResult("ordinary-mode", "ok"))
                self.assertEqual(retained.read_bytes(), b"PRIVATE_RETAINED_BYTES")
                process.assert_awaited_once()

        controls = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
        for stage in ("bind", "invoke"):
            for exception_type in controls:
                with self.subTest(stage=stage, control=exception_type.__name__):
                    adapter = self.adapter(
                        f"control-mode-{stage}-{exception_type.__name__.lower()}"
                    )
                    if stage == "invoke":
                        binding = adapter.bind(self.definition)
                    failure = exception_type(f"PRIVATE_{stage.upper()}_CONTROL")
                    forbidden_process = mock.AsyncMock(
                        side_effect=AssertionError("provider process invoked")
                    )
                    with (
                        mock.patch.object(os, "supports_follow_symlinks", set()),
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.os.fchmod",
                            side_effect=failure,
                        ),
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.run_process",
                            forbidden_process,
                        ),
                        self.assertRaises(exception_type) as caught,
                    ):
                        if stage == "bind":
                            adapter.bind(self.definition)
                        else:
                            await adapter.invoke(
                                binding,
                                self.provider_invocation(
                                    f"control-{exception_type.__name__.lower()}"
                                ),
                            )

                    self.assertIs(caught.exception, failure)
                    forbidden_process.assert_not_awaited()

    def test_definition_chain_rejects_links_and_non_directories(self) -> None:
        for component_kind in ("state root link", "fingerprint link", "file"):
            with (
                self.subTest(component=component_kind),
                tempfile.TemporaryDirectory(
                    dir=self.workspace.root.parent
                ) as temporary,
            ):
                label = component_kind.replace(" ", "-")
                parent = self.workspace.path(f"definition-{label}-parent")
                parent.mkdir()
                parent.chmod(0o751)
                parent_mode_before = _mode(parent)
                parent_sentinel = parent / "PARENT_SENTINEL.bin"
                parent_sentinel.write_bytes(b"PRIVATE_PARENT_BYTES")
                adapter = self.adapter(f"definition-{label}-parent/state")
                outside = Path(temporary) / "outside"
                outside.mkdir()
                outside.chmod(0o751)
                (outside / "AGENTS.md").write_bytes(b"PRIVATE_OUTSIDE_RULES")
                (outside / "sentinel.bin").write_bytes(b"PRIVATE_OUTSIDE_BYTES")
                outside_before = _directory_snapshot(outside)
                unsafe = (
                    adapter.state_root
                    if component_kind == "state root link"
                    else adapter.state_root
                    / "definitions"
                    / self.definition.agent_id
                    / self.definition.fingerprint
                )
                unsafe.parent.mkdir(parents=True, exist_ok=True)
                if component_kind == "file":
                    unsafe.write_bytes(b"PRIVATE_UNSAFE_FILE")
                    unsafe.chmod(0o640)
                    unsafe_before = (_mode(unsafe), unsafe.read_bytes())
                else:
                    self.directory_symlink(unsafe, outside)
                    unsafe_before = None
                forbidden_process = mock.AsyncMock(
                    side_effect=AssertionError("provider process invoked")
                )

                with (
                    mock.patch(
                        "story_writing_agents.antigravity_adapter.run_process",
                        forbidden_process,
                    ),
                    self.assertRaises(NotADirectoryError) as caught,
                ):
                    adapter.bind(self.definition)

                self.assertEqual(
                    str(caught.exception),
                    "Antigravity state directory is unsafe",
                )
                self.assertEqual(_directory_snapshot(outside), outside_before)
                self.assertEqual(_mode(parent), parent_mode_before)
                self.assertEqual(
                    parent_sentinel.read_bytes(),
                    b"PRIVATE_PARENT_BYTES",
                )
                if unsafe_before is not None:
                    self.assertEqual(
                        (_mode(unsafe), unsafe.read_bytes()),
                        unsafe_before,
                    )
                forbidden_process.assert_not_awaited()

    async def test_runtime_chain_rejects_links_and_files_before_launch(
        self,
    ) -> None:
        for component_kind in (
            "session link",
            "provider link",
            "workspace link",
            "file",
        ):
            with (
                self.subTest(component=component_kind),
                tempfile.TemporaryDirectory(
                    dir=self.workspace.root.parent
                ) as temporary,
            ):
                label = component_kind.replace(" ", "-")
                parent = self.workspace.path(f"runtime-{label}-parent")
                parent.mkdir()
                parent.chmod(0o751)
                parent_mode_before = _mode(parent)
                parent_sentinel = parent / "PARENT_SENTINEL.bin"
                parent_sentinel.write_bytes(b"PRIVATE_PARENT_BYTES")
                adapter = self.adapter(f"runtime-{label}-parent/state")
                binding = adapter.bind(self.definition)
                session_id = f"unsafe-{label}"
                runtime_root = (
                    adapter.state_root
                    / "sessions"
                    / session_id
                    / self.definition.agent_id
                    / self.definition.fingerprint
                )
                outside = Path(temporary) / "outside"
                outside.mkdir()
                outside.chmod(0o751)
                (outside / "AGENTS.md").write_bytes(b"PRIVATE_OUTSIDE_RULES")
                (outside / "sentinel.bin").write_bytes(b"PRIVATE_OUTSIDE_BYTES")
                outside_before = _directory_snapshot(outside)
                if component_kind == "session link":
                    unsafe = adapter.state_root / "sessions" / session_id
                elif component_kind == "provider link":
                    unsafe = runtime_root / "provider"
                elif component_kind == "workspace link":
                    unsafe = runtime_root / "workspace"
                else:
                    unsafe = runtime_root / "provider"
                unsafe.parent.mkdir(parents=True, exist_ok=True)
                if component_kind == "file":
                    unsafe.write_bytes(b"PRIVATE_UNSAFE_FILE")
                    unsafe.chmod(0o640)
                    unsafe_before = (_mode(unsafe), unsafe.read_bytes())
                else:
                    self.directory_symlink(unsafe, outside)
                    unsafe_before = None
                forbidden_process = mock.AsyncMock(
                    side_effect=AssertionError("provider process invoked")
                )

                with (
                    mock.patch(
                        "story_writing_agents.antigravity_adapter.run_process",
                        forbidden_process,
                    ),
                    self.assertRaises(ProviderInvocationError) as caught,
                ):
                    await adapter.invoke(
                        binding,
                        self.provider_invocation(session_id),
                    )

                error = caught.exception
                self.assertEqual(
                    str(error),
                    "Antigravity provider workspace could not be prepared",
                )
                self.assertIs(error.taint_existing_thread, False)
                self.assertNotIn("PRIVATE", str(error))
                self.assertIsInstance(error.__cause__, NotADirectoryError)
                self.assertEqual(
                    str(error.__cause__),
                    "Antigravity state directory is unsafe",
                )
                self.assertEqual(_directory_snapshot(outside), outside_before)
                self.assertEqual(_mode(parent), parent_mode_before)
                self.assertEqual(
                    parent_sentinel.read_bytes(),
                    b"PRIVATE_PARENT_BYTES",
                )
                if unsafe_before is not None:
                    self.assertEqual(
                        (_mode(unsafe), unsafe.read_bytes()),
                        unsafe_before,
                    )
                forbidden_process.assert_not_awaited()

    async def test_rules_target_symlinks_preserve_outside_and_never_launch(
        self,
    ) -> None:
        for stage in ("binding", "workspace"):
            with (
                self.subTest(stage=stage),
                tempfile.TemporaryDirectory(
                    dir=self.workspace.root.parent
                ) as temporary,
            ):
                adapter = self.adapter(f"rules-{stage}-link-state")
                outside = Path(temporary) / "outside"
                outside.mkdir()
                outside.chmod(0o751)
                outside_rules = outside / "AGENTS.md"
                outside_rules.write_bytes(b"PRIVATE_OUTSIDE_RULES")
                outside_rules.chmod(0o640)
                (outside / "sentinel.bin").write_bytes(b"PRIVATE_OUTSIDE_BYTES")
                outside_before = _directory_snapshot(outside)
                forbidden_process = mock.AsyncMock(
                    side_effect=AssertionError("provider process invoked")
                )

                if stage == "binding":
                    target = (
                        adapter.state_root
                        / "definitions"
                        / self.definition.agent_id
                        / self.definition.fingerprint
                        / "AGENTS.md"
                    )
                    target.parent.mkdir(parents=True)
                    self.file_symlink(target, outside_rules)
                    with (
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.run_process",
                            forbidden_process,
                        ),
                        self.assertRaisesRegex(
                            OSError,
                            "Antigravity rules target is unsafe",
                        ),
                    ):
                        adapter.bind(self.definition)
                else:
                    binding = adapter.bind(self.definition)
                    session_id = "rules-workspace-link"
                    target = (
                        self.runtime_root(adapter, session_id)
                        / "workspace"
                        / "AGENTS.md"
                    )
                    target.parent.mkdir(parents=True)
                    self.file_symlink(target, outside_rules)
                    error = await self.assert_workspace_preparation_failure(
                        adapter,
                        binding,
                        session_id,
                    )
                    self.assertEqual(
                        str(error.__cause__),
                        "Antigravity rules target is unsafe",
                    )

                self.assertTrue(target.is_symlink())
                self.assertEqual(target.readlink(), outside_rules)
                self.assertEqual(_directory_snapshot(outside), outside_before)
                self.assertEqual(
                    [entry.name for entry in target.parent.iterdir()],
                    ["AGENTS.md"],
                )
                forbidden_process.assert_not_awaited()

    async def test_nonregular_and_multiply_linked_rules_are_rejected(
        self,
    ) -> None:
        for kind in ("directory", "fifo", "hard link"):
            with (
                self.subTest(destination=kind),
                tempfile.TemporaryDirectory(
                    dir=self.workspace.root.parent
                ) as temporary,
            ):
                label = kind.replace(" ", "-")
                adapter = self.adapter(f"unsafe-target-{label}-state")
                binding = adapter.bind(self.definition)
                session_id = f"unsafe-target-{label}"
                target = (
                    self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
                )
                target.parent.mkdir(parents=True)
                outside = Path(temporary) / "outside"
                outside.mkdir()
                outside_file = outside / "rules.bin"
                outside_file.write_bytes(b"PRIVATE_OUTSIDE_RULES")
                if kind == "directory":
                    target.mkdir()
                    (target / "sentinel.bin").write_bytes(b"PRIVATE_DIRECTORY_BYTES")
                    before = _directory_snapshot(target)
                elif kind == "fifo":
                    self.fifo(target)
                    before = os.lstat(target).st_mode
                else:
                    self.hard_link(outside_file, target)
                    before = _directory_snapshot(outside)

                error = await self.assert_workspace_preparation_failure(
                    adapter,
                    binding,
                    session_id,
                )

                self.assertEqual(
                    str(error.__cause__),
                    "Antigravity rules target is unsafe",
                )
                if kind == "directory":
                    self.assertEqual(_directory_snapshot(target), before)
                elif kind == "fifo":
                    self.assertTrue(stat.S_ISFIFO(os.lstat(target).st_mode))
                    self.assertEqual(os.lstat(target).st_mode, before)
                else:
                    self.assertEqual(_directory_snapshot(outside), before)
                    self.assertEqual(target.read_bytes(), b"PRIVATE_OUTSIDE_RULES")
                    self.assertEqual(os.lstat(target).st_nlink, 2)
                self.assertEqual(
                    [
                        entry.name
                        for entry in target.parent.iterdir()
                        if entry.name.startswith(".AGENTS.md.")
                    ],
                    [],
                )

        for kind in ("symlink", "directory", "fifo", "hard link"):
            with (
                self.subTest(source=kind),
                tempfile.TemporaryDirectory(
                    dir=self.workspace.root.parent
                ) as temporary,
            ):
                label = kind.replace(" ", "-")
                adapter = self.adapter(f"unsafe-source-{label}-state")
                binding = adapter.bind(self.definition)
                source = binding.staging_directory / "AGENTS.md"
                canonical = source.read_bytes()
                session_id = f"unsafe-source-{label}"
                target = (
                    self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
                )
                target.parent.mkdir(parents=True)
                target.write_bytes(b"PRIVATE_RETAINED_TARGET")
                outside = Path(temporary) / "outside"
                outside.mkdir()
                outside_file = outside / "rules.bin"
                outside_file.write_bytes(b"PRIVATE_OUTSIDE_RULES")

                if kind == "symlink":
                    source.unlink()
                    self.file_symlink(source, outside_file)
                    before = _directory_snapshot(outside)
                elif kind == "directory":
                    source.unlink()
                    source.mkdir()
                    (source / "sentinel.bin").write_bytes(b"PRIVATE_DIRECTORY_BYTES")
                    before = _directory_snapshot(source)
                elif kind == "fifo":
                    source.unlink()
                    self.fifo(source)
                    before = os.lstat(source).st_mode
                else:
                    hard_link = outside / "staged-rules-link.bin"
                    self.hard_link(source, hard_link)
                    before = _directory_snapshot(outside)

                error = await self.assert_workspace_preparation_failure(
                    adapter,
                    binding,
                    session_id,
                )

                self.assertEqual(target.read_bytes(), b"PRIVATE_RETAINED_TARGET")
                self.assertEqual(
                    [entry.name for entry in target.parent.iterdir()],
                    ["AGENTS.md"],
                )
                self.assertIsInstance(error.__cause__, OSError)
                if kind == "symlink":
                    self.assertTrue(source.is_symlink())
                    self.assertEqual(_directory_snapshot(outside), before)
                elif kind == "directory":
                    self.assertEqual(_directory_snapshot(source), before)
                elif kind == "fifo":
                    self.assertTrue(stat.S_ISFIFO(os.lstat(source).st_mode))
                    self.assertEqual(os.lstat(source).st_mode, before)
                else:
                    self.assertEqual(source.read_bytes(), canonical)
                    self.assertEqual(os.lstat(source).st_nlink, 2)
                    self.assertEqual(_directory_snapshot(outside), before)

    async def test_rules_identity_mismatches_and_leaf_swap_are_contained(
        self,
    ) -> None:
        for mismatch in ("source path", "temporary descriptor"):
            with self.subTest(mismatch=mismatch):
                label = mismatch.replace(" ", "-")
                adapter = self.adapter(f"rules-{label}-state")
                binding = adapter.bind(self.definition)
                source = binding.staging_directory / "AGENTS.md"
                canonical = source.read_bytes()
                session_id = f"rules-{label}"
                target = (
                    self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
                )
                target.parent.mkdir(parents=True)
                target.write_bytes(b"PRIVATE_RETAINED_TARGET")

                if mismatch == "source path":
                    real_identity = AntigravityAdapter._rules_path_identity

                    def mismatched_path_identity(
                        path: Path,
                        *,
                        diagnostic: str,
                    ) -> tuple[int, int]:
                        identity = real_identity(path, diagnostic=diagnostic)
                        if path == source:
                            return identity[0], identity[1] + 1
                        return identity

                    contexts = (
                        mock.patch.object(
                            AntigravityAdapter,
                            "_rules_path_identity",
                            side_effect=mismatched_path_identity,
                        ),
                    )
                    expected_cause = "Antigravity rules source is unsafe"
                else:
                    real_identity = AntigravityAdapter._rules_descriptor_identity
                    identity_calls = 0

                    def mismatched_descriptor_identity(
                        descriptor: int,
                        *,
                        diagnostic: str,
                    ) -> tuple[int, int]:
                        nonlocal identity_calls
                        identity_calls += 1
                        identity = real_identity(
                            descriptor,
                            diagnostic=diagnostic,
                        )
                        if identity_calls == 2:
                            return identity[0], identity[1] + 1
                        return identity

                    contexts = (
                        mock.patch.object(
                            AntigravityAdapter,
                            "_read_rules_source",
                            return_value=canonical,
                        ),
                        mock.patch.object(
                            AntigravityAdapter,
                            "_rules_descriptor_identity",
                            side_effect=mismatched_descriptor_identity,
                        ),
                    )
                    expected_cause = "Antigravity rules temporary file is unsafe"

                with contextlib.ExitStack() as stack:
                    for context in contexts:
                        stack.enter_context(context)
                    error = await self.assert_workspace_preparation_failure(
                        adapter,
                        binding,
                        session_id,
                    )

                self.assertEqual(str(error.__cause__), expected_cause)
                self.assertEqual(target.read_bytes(), b"PRIVATE_RETAINED_TARGET")
                self.assertEqual(
                    [entry.name for entry in target.parent.iterdir()],
                    ["AGENTS.md"],
                )

        with tempfile.TemporaryDirectory(dir=self.workspace.root.parent) as temporary:
            adapter = self.adapter("rules-final-leaf-swap-state")
            binding = adapter.bind(self.definition)
            canonical = (binding.staging_directory / "AGENTS.md").read_bytes()
            session_id = "rules-final-leaf-swap"
            target = self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"PRIVATE_RETAINED_TARGET")
            outside = Path(temporary) / "outside"
            outside.mkdir()
            outside_rules = outside / "AGENTS.md"
            outside_rules.write_bytes(b"PRIVATE_OUTSIDE_RULES")
            outside_rules.chmod(0o640)
            outside_before = _directory_snapshot(outside)
            probe = target.parent / "symlink-probe"
            self.file_symlink(probe, outside_rules)
            probe.unlink()
            real_replace = os.replace
            swaps: list[tuple[Path, Path]] = []

            def swap_final_leaf(source: Path, destination: Path) -> None:
                source = Path(source)
                destination = Path(destination)
                if destination == target:
                    target.unlink()
                    target.symlink_to(outside_rules)
                    swaps.append((source, destination))
                real_replace(source, destination)

            process = mock.AsyncMock(
                return_value=ProcessResult(
                    stdout=_stream("rules-leaf-swap", response="safe response"),
                    stderr=b"",
                    returncode=0,
                )
            )
            with (
                mock.patch(
                    "story_writing_agents.antigravity_adapter.os.replace",
                    side_effect=swap_final_leaf,
                ),
                mock.patch(
                    "story_writing_agents.antigravity_adapter.run_process",
                    process,
                ),
            ):
                result = await adapter.invoke(
                    binding,
                    self.provider_invocation(session_id),
                )

            self.assertEqual(
                result,
                ProviderResult("rules-leaf-swap", "safe response"),
            )
            self.assertEqual(len(swaps), 1)
            self.assertFalse(target.is_symlink())
            self.assert_rules_installation(target, canonical)
            self.assertEqual(_directory_snapshot(outside), outside_before)
            request = process.await_args.args[0]
            self.assertEqual(request.cwd, target.parent)

        for installed_kind in ("symlink", "foreign hard link"):
            with (
                self.subTest(installed=installed_kind),
                tempfile.TemporaryDirectory(
                    dir=self.workspace.root.parent
                ) as temporary,
            ):
                label = installed_kind.replace(" ", "-")
                adapter = self.adapter(f"rules-installed-{label}-state")
                binding = adapter.bind(self.definition)
                session_id = f"rules-installed-{label}"
                target = (
                    self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
                )
                target.parent.mkdir(parents=True)
                target.write_bytes(b"PRIVATE_RETAINED_TARGET")
                retained = target.parent / "retained.bin"
                retained.write_bytes(b"PRIVATE_RETAINED_ENTRY")
                outside = Path(temporary) / "outside"
                outside.mkdir()
                outside_rules = outside / "AGENTS.md"
                outside_rules.write_bytes(b"PRIVATE_OUTSIDE_RULES")
                outside_rules.chmod(0o640)
                probe = outside / "link-probe"
                if installed_kind == "symlink":
                    self.file_symlink(probe, outside_rules)
                else:
                    self.hard_link(outside_rules, probe)
                probe.unlink()
                outside_before = _directory_snapshot(outside)
                outside_identity = (
                    os.lstat(outside_rules).st_dev,
                    os.lstat(outside_rules).st_ino,
                    _mode(outside_rules),
                    outside_rules.read_bytes(),
                )
                real_replace = os.replace
                replacements: list[tuple[Path, Path]] = []

                def install_suspicious_leaf(
                    source: Path,
                    destination: Path,
                ) -> None:
                    source = Path(source)
                    destination = Path(destination)
                    source.unlink()
                    if installed_kind == "symlink":
                        source.symlink_to(outside_rules)
                    else:
                        os.link(outside_rules, source)
                    replacements.append((source, destination))
                    real_replace(source, destination)

                with mock.patch(
                    "story_writing_agents.antigravity_adapter.os.replace",
                    side_effect=install_suspicious_leaf,
                ):
                    error = await self.assert_workspace_preparation_failure(
                        adapter,
                        binding,
                        session_id,
                    )

                self.assertEqual(
                    str(error.__cause__),
                    "Antigravity installed rules are unsafe",
                )
                self.assertEqual(len(replacements), 1)
                temporary_path, destination = replacements[0]
                self.assertEqual(destination, target)
                self.assertFalse(temporary_path.exists())
                self.assertEqual(_directory_snapshot(outside), outside_before)
                self.assertEqual(
                    (
                        os.lstat(outside_rules).st_dev,
                        os.lstat(outside_rules).st_ino,
                        _mode(outside_rules),
                        outside_rules.read_bytes(),
                    ),
                    outside_identity,
                )
                if installed_kind == "symlink":
                    self.assertTrue(target.is_symlink())
                    self.assertEqual(target.readlink(), outside_rules)
                else:
                    self.assertFalse(target.is_symlink())
                    self.assertEqual(
                        (
                            os.lstat(target).st_dev,
                            os.lstat(target).st_ino,
                        ),
                        outside_identity[:2],
                    )
                    self.assertEqual(os.lstat(target).st_nlink, 2)
                self.assertEqual(
                    retained.read_bytes(),
                    b"PRIVATE_RETAINED_ENTRY",
                )
                self.assertEqual(
                    sorted(entry.name for entry in target.parent.iterdir()),
                    ["AGENTS.md", "retained.bin"],
                )

    async def test_rules_writes_are_complete_bounded_and_failure_safe(
        self,
    ) -> None:
        for behavior in ("partial writes", "fchmod failure"):
            with self.subTest(success=behavior):
                label = behavior.replace(" ", "-")
                adapter = self.adapter(f"rules-{label}-state")
                binding = adapter.bind(self.definition)
                canonical = (binding.staging_directory / "AGENTS.md").read_bytes()
                session_id = f"rules-{label}"
                target = (
                    self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
                )
                retained = target.parent / "retained.bin"
                if behavior == "fchmod failure":
                    target.parent.mkdir(parents=True)
                    target.write_bytes(b"PRIVATE_RETAINED_TARGET")
                    retained.write_bytes(b"PRIVATE_RETAINED_ENTRY")
                process = mock.AsyncMock(
                    return_value=ProcessResult(
                        stdout=_stream(
                            f"rules-{label}",
                            response="safe response",
                        ),
                        stderr=b"",
                        returncode=0,
                    )
                )
                real_write = os.write
                write_sizes: list[int] = []

                def partial_write(descriptor: int, content) -> int:
                    written = real_write(descriptor, content[:7])
                    write_sizes.append(written)
                    return written

                seam = (
                    mock.patch(
                        "story_writing_agents.antigravity_adapter.os.write",
                        side_effect=partial_write,
                    )
                    if behavior == "partial writes"
                    else mock.patch(
                        "story_writing_agents.antigravity_adapter.os.fchmod",
                        side_effect=OSError(
                            errno.ENOTSUP,
                            "PRIVATE_FCHMOD_FAILURE",
                        ),
                    )
                )
                with (
                    seam,
                    mock.patch(
                        "story_writing_agents.antigravity_adapter.run_process",
                        process,
                    ),
                ):
                    result = await adapter.invoke(
                        binding,
                        self.provider_invocation(session_id),
                    )

                self.assertEqual(
                    result,
                    ProviderResult(f"rules-{label}", "safe response"),
                )
                self.assertEqual(target.read_bytes(), canonical)
                self.assertEqual(
                    [
                        entry.name
                        for entry in target.parent.iterdir()
                        if entry.name.startswith(".AGENTS.md.")
                    ],
                    [],
                )
                if behavior == "partial writes":
                    self.assertGreater(len(write_sizes), 1)
                    self.assertEqual(sum(write_sizes), len(canonical))
                    self.assertTrue(all(0 < size <= 7 for size in write_sizes))
                else:
                    self.assertEqual(
                        retained.read_bytes(),
                        b"PRIVATE_RETAINED_ENTRY",
                    )
                process.assert_awaited_once()

        failures = (
            (
                "collisions",
                "Antigravity rules temporary file name attempts were exhausted",
            ),
            (
                "create",
                "Antigravity rules temporary file could not be created",
            ),
            (
                "write",
                "Antigravity rules temporary file could not be written",
            ),
            ("close", "[Errno 5] PRIVATE_CLOSE_FAILURE"),
            ("replace", "Antigravity rules replacement failed"),
            (
                "unlink",
                "Antigravity rules temporary file could not be written",
            ),
        )
        for behavior, expected_cause in failures:
            with self.subTest(failure=behavior):
                adapter = self.adapter(f"rules-{behavior}-failure-state")
                binding = adapter.bind(self.definition)
                canonical = (binding.staging_directory / "AGENTS.md").read_bytes()
                session_id = f"rules-{behavior}-failure"
                target = (
                    self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
                )
                target.parent.mkdir(parents=True)
                target.write_bytes(b"PRIVATE_RETAINED_TARGET")
                retained = target.parent / "retained.bin"
                retained.write_bytes(b"PRIVATE_RETAINED_ENTRY")
                real_open = os.open
                real_close = os.close
                temporary_descriptors: set[int] = set()
                temporary_attempts: list[Path] = []

                def open_with_failure(
                    path,
                    flags: int,
                    mode: int = 0o777,
                ) -> int:
                    path = Path(path)
                    if flags & os.O_CREAT:
                        temporary_attempts.append(path)
                        if behavior == "collisions":
                            raise FileExistsError(errno.EEXIST, "collision")
                        if behavior == "create":
                            raise OSError(errno.EACCES, "PRIVATE_CREATE_FAILURE")
                    descriptor = real_open(path, flags, mode)
                    if flags & os.O_CREAT:
                        temporary_descriptors.add(descriptor)
                    return descriptor

                def close_with_failure(descriptor: int) -> None:
                    real_close(descriptor)
                    if behavior == "close" and descriptor in temporary_descriptors:
                        raise OSError(errno.EIO, "PRIVATE_CLOSE_FAILURE")

                def write_with_failure(_descriptor: int, _content) -> int:
                    raise OSError(errno.EIO, "PRIVATE_WRITE_FAILURE")

                def replace_with_failure(_source, _target) -> None:
                    raise OSError(errno.EIO, "PRIVATE_REPLACE_FAILURE")

                def unlink_with_failure(_path) -> None:
                    raise OSError(errno.EIO, "PRIVATE_UNLINK_FAILURE")

                with contextlib.ExitStack() as stack:
                    if behavior == "collisions":
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.uuid4",
                                side_effect=[
                                    mock.Mock(hex=f"{attempt:032x}")
                                    for attempt in range(16)
                                ],
                            )
                        )
                    if behavior in ("collisions", "create", "close"):
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.open",
                                side_effect=open_with_failure,
                            )
                        )
                    if behavior == "close":
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.close",
                                side_effect=close_with_failure,
                            )
                        )
                    if behavior in ("write", "unlink"):
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.write",
                                side_effect=write_with_failure,
                            )
                        )
                    if behavior == "replace":
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.replace",
                                side_effect=replace_with_failure,
                            )
                        )
                    if behavior == "unlink":
                        stack.enter_context(
                            mock.patch(
                                "story_writing_agents.antigravity_adapter.os.unlink",
                                side_effect=unlink_with_failure,
                            )
                        )
                    error = await self.assert_workspace_preparation_failure(
                        adapter,
                        binding,
                        session_id,
                    )

                self.assertEqual(str(error.__cause__), expected_cause)
                self.assertEqual(target.read_bytes(), b"PRIVATE_RETAINED_TARGET")
                self.assertEqual(retained.read_bytes(), b"PRIVATE_RETAINED_ENTRY")
                temporaries = [
                    entry
                    for entry in target.parent.iterdir()
                    if entry.name.startswith(".AGENTS.md.")
                ]
                if behavior == "unlink":
                    self.assertEqual(len(temporaries), 1)
                    temporaries[0].unlink()
                else:
                    self.assertEqual(temporaries, [])
                if behavior == "collisions":
                    self.assertEqual(len(temporary_attempts), 16)
                    self.assertEqual(len(set(temporary_attempts)), 16)
                elif behavior == "create":
                    self.assertEqual(len(temporary_attempts), 1)
                self.assertEqual(
                    sorted(entry.name for entry in target.parent.iterdir()),
                    ["AGENTS.md", "retained.bin"],
                )
                self.assertEqual(
                    (binding.staging_directory / "AGENTS.md").read_bytes(),
                    canonical,
                )

    async def test_rules_cleanup_exception_precedence_is_exact(self) -> None:
        controls = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
        for precedence in ("body control", "cleanup control"):
            for exception_type in controls:
                with self.subTest(
                    precedence=precedence,
                    exception=exception_type.__name__,
                ):
                    label = f"{precedence}-{exception_type.__name__}".replace(
                        " ", "-"
                    ).lower()
                    adapter = self.adapter(f"rules-{label}-state")
                    binding = adapter.bind(self.definition)
                    canonical = (binding.staging_directory / "AGENTS.md").read_bytes()
                    session_id = f"rules-{label}"
                    target = (
                        self.runtime_root(adapter, session_id)
                        / "workspace"
                        / "AGENTS.md"
                    )
                    target.parent.mkdir(parents=True)
                    target.write_bytes(b"PRIVATE_RETAINED_TARGET")
                    body_control = exception_type(
                        f"PRIVATE_BODY_{exception_type.__name__.upper()}"
                    )
                    cleanup_control = exception_type(
                        f"PRIVATE_CLEANUP_{exception_type.__name__.upper()}"
                    )
                    body_failure: BaseException = (
                        body_control
                        if precedence == "body control"
                        else OSError(errno.EIO, "PRIVATE_ORDINARY_BODY")
                    )
                    expected = (
                        body_control
                        if precedence == "body control"
                        else cleanup_control
                    )
                    real_open = os.open
                    real_close = os.close
                    temporary_descriptors: set[int] = set()

                    def tracking_open(
                        path,
                        flags: int,
                        mode: int = 0o777,
                    ) -> int:
                        descriptor = real_open(path, flags, mode)
                        if flags & os.O_CREAT:
                            temporary_descriptors.add(descriptor)
                        return descriptor

                    def failing_close(descriptor: int) -> None:
                        real_close(descriptor)
                        if descriptor in temporary_descriptors:
                            raise cleanup_control

                    forbidden_process = mock.AsyncMock(
                        side_effect=AssertionError("provider process invoked")
                    )
                    with (
                        mock.patch.object(
                            AntigravityAdapter,
                            "_read_rules_source",
                            return_value=canonical,
                        ),
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.os.open",
                            side_effect=tracking_open,
                        ),
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.os.write",
                            side_effect=body_failure,
                        ),
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.os.close",
                            side_effect=failing_close,
                        ),
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.run_process",
                            forbidden_process,
                        ),
                        self.assertRaises(exception_type) as caught,
                    ):
                        await adapter.invoke(
                            binding,
                            self.provider_invocation(session_id),
                        )

                    self.assertIs(caught.exception, expected)
                    self.assertEqual(
                        target.read_bytes(),
                        b"PRIVATE_RETAINED_TARGET",
                    )
                    self.assertEqual(
                        [entry.name for entry in target.parent.iterdir()],
                        ["AGENTS.md"],
                    )
                    forbidden_process.assert_not_awaited()

        adapter = self.adapter("rules-ordinary-precedence-state")
        binding = adapter.bind(self.definition)
        canonical = (binding.staging_directory / "AGENTS.md").read_bytes()
        session_id = "rules-ordinary-precedence"
        target = self.runtime_root(adapter, session_id) / "workspace" / "AGENTS.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"PRIVATE_RETAINED_TARGET")
        real_open = os.open
        real_close = os.close
        temporary_descriptors: set[int] = set()

        def tracking_open(path, flags: int, mode: int = 0o777) -> int:
            descriptor = real_open(path, flags, mode)
            if flags & os.O_CREAT:
                temporary_descriptors.add(descriptor)
            return descriptor

        def ordinary_cleanup(descriptor: int) -> None:
            real_close(descriptor)
            if descriptor in temporary_descriptors:
                raise OSError(errno.EIO, "PRIVATE_ORDINARY_CLEANUP")

        with (
            mock.patch.object(
                AntigravityAdapter,
                "_read_rules_source",
                return_value=canonical,
            ),
            mock.patch(
                "story_writing_agents.antigravity_adapter.os.open",
                side_effect=tracking_open,
            ),
            mock.patch(
                "story_writing_agents.antigravity_adapter.os.write",
                side_effect=OSError(errno.EIO, "PRIVATE_ORDINARY_BODY"),
            ),
            mock.patch(
                "story_writing_agents.antigravity_adapter.os.close",
                side_effect=ordinary_cleanup,
            ),
        ):
            error = await self.assert_workspace_preparation_failure(
                adapter,
                binding,
                session_id,
            )

        self.assertEqual(
            str(error.__cause__),
            "Antigravity rules temporary file could not be written",
        )
        self.assertEqual(target.read_bytes(), b"PRIVATE_RETAINED_TARGET")
        self.assertEqual(
            [entry.name for entry in target.parent.iterdir()],
            ["AGENTS.md"],
        )

        source = binding.staging_directory / "AGENTS.md"

        def ordinary_source_cleanup(descriptor: int) -> None:
            real_close(descriptor)
            raise OSError(errno.EIO, "PRIVATE_ORDINARY_CLEANUP")

        with mock.patch(
            "story_writing_agents.antigravity_adapter.os.close",
            side_effect=ordinary_source_cleanup,
        ):
            self.assertEqual(
                AntigravityAdapter._read_rules_source(source),
                canonical,
            )

    def test_private_import_has_no_filesystem_or_public_surface_effect(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = dict(
            os.environ,
            PYTHONPATH=str(repository_root / "src"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        script = (
            "import story_writing_agents as facade; "
            "from story_writing_agents import antigravity_adapter as module; "
            "assert module.AntigravityAdapter.__name__ == 'AntigravityAdapter'; "
            "assert 'AntigravityAdapter' not in facade.__all__; "
            "assert not hasattr(facade, 'AntigravityAdapter'); "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_command_stdin_paths_and_fresh_resume_results_are_exact(
        self,
    ) -> None:
        adapter = self.adapter(
            "runtime-state",
            model="agy-model",
            effort="high",
        )
        binding = adapter.bind(self.definition)
        schema = {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["revision_id", "content"],
            "additionalProperties": False,
        }
        payload = {
            "task": "PRIVATE_STORY_PROMPT",
            "user_payload": "PRIVATE_STORY_PAYLOAD",
        }
        results = iter(
            (
                ProcessResult(
                    stdout=_stream(
                        "conversation-one",
                        structured_output={
                            "revision_id": "r0",
                            "content": "structured response",
                        },
                        response="IGNORED_FALLBACK_SECRET",
                    ),
                    stderr=b"",
                    returncode=0,
                ),
                ProcessResult(
                    stdout=_stream(
                        "conversation-one",
                        response="fallback response",
                    ),
                    stderr=b"",
                    returncode=0,
                ),
            )
        )
        requests: list[ProcessRequest] = []

        async def capture_process(
            request: ProcessRequest,
            **_seams: object,
        ) -> ProcessResult:
            requests.append(request)
            return next(results)

        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            new=capture_process,
        ):
            fresh = await adapter.invoke(
                binding,
                ProviderInvocation(
                    session_id="story-session",
                    schema=schema,
                    payload=payload,
                    thread_id=None,
                ),
            )
            resumed = await adapter.invoke(
                binding,
                ProviderInvocation(
                    session_id="story-session",
                    schema=schema,
                    payload=payload,
                    thread_id=fresh.thread_id,
                ),
            )

        self.assertEqual(
            fresh,
            ProviderResult(
                thread_id="conversation-one",
                response_text=('{"revision_id":"r0","content":"structured response"}'),
            ),
        )
        self.assertEqual(
            resumed,
            ProviderResult(
                thread_id="conversation-one",
                response_text="fallback response",
            ),
        )
        self.assertEqual(
            fresh.actual_transport_utf8_bytes,
            len(requests[0].stdin),
        )
        self.assertEqual(
            resumed.actual_transport_utf8_bytes,
            len(requests[1].stdin),
        )
        self.assertIsNone(fresh.usage)
        self.assertIsNone(resumed.usage)
        self.assertEqual(len(requests), 2)
        runtime_root = (
            self.workspace.path("runtime-state")
            / "sessions"
            / "story-session"
            / self.definition.agent_id
            / self.definition.fingerprint
        )
        provider_directory = runtime_root / "provider"
        working_directory = runtime_root / "workspace"
        expected_command = [
            "agy-test",
            f"--gemini_dir={provider_directory}",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--json-schema",
            canonical_provider_json(schema).decode("utf-8"),
            "--sandbox",
            "--mode",
            "plan",
            "--disable-slash-commands",
            "--project",
            "default-cli-project",
            "--print-timeout",
            "7s",
            "--model",
            "agy-model",
            "--effort",
            "high",
        ]
        self.assertEqual(list(requests[0].command), expected_command)
        self.assertEqual(
            list(requests[1].command),
            expected_command + ["--conversation", "conversation-one"],
        )
        for request in requests:
            self.assertEqual(request.cwd, working_directory)
            self.assertEqual(request.timeout_seconds, 7)
            self.assertIsNone(request.env)
            lines = request.stdin.decode("utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(
                json.loads(lines[0]),
                {
                    "event": "user",
                    "message": {
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    },
                },
            )
            self.assertNotIn(
                "PRIVATE_STORY_PROMPT",
                "\0".join(request.command),
            )
            self.assertNotIn(
                "PRIVATE_STORY_PAYLOAD",
                "\0".join(request.command),
            )
        self.assertEqual(
            json.loads(expected_command[expected_command.index("--json-schema") + 1]),
            schema,
        )
        self.assertEqual(
            (working_directory / "AGENTS.md").read_bytes(),
            (binding.staging_directory / "AGENTS.md").read_bytes(),
        )
        self.assert_rules_installation(
            working_directory / "AGENTS.md",
            (binding.staging_directory / "AGENTS.md").read_bytes(),
        )
        self.assertEqual(list(provider_directory.iterdir()), [])
        self.assertEqual(
            [path.name for path in working_directory.iterdir()],
            ["AGENTS.md"],
        )

    async def test_contextual_unicode_keeps_inner_application_bytes_distinct_from_ndjson_transport(
        self,
    ) -> None:
        adapter = self.adapter("contextual-runtime")
        binding = adapter.bind(self.definition)
        schema = {"type": "object"}
        payload = {
            "c": "A" * 43,
            "q": ["conversation", "sa", "wco", [4, 2, 2, 4, 8, 8, 16, 200], "text"],
            "i": "CONTEXTUAL_한🙂",
        }
        invocation = ProviderInvocation(
            session_id="contextual-session",
            schema=schema,
            payload=payload,
            thread_id=None,
            call_kind=ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
            component_paths={("i",): ContextComponentKind.CURRENT_INPUT},
        )
        requests: list[ProcessRequest] = []

        async def capture_process(
            request: ProcessRequest,
            **_seams: object,
        ) -> ProcessResult:
            requests.append(request)
            return ProcessResult(
                stdout=_stream(
                    "contextual-conversation",
                    structured_output={"action": "answer"},
                    usage={"input_tokens": 0, "output_tokens": 4},
                ),
                stderr=b"",
                returncode=0,
            )

        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            new=capture_process,
        ):
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(len(requests), 1)
        assert invocation.payload_json is not None
        outer = json.loads(requests[0].stdin)
        self.assertEqual(
            outer,
            {
                "event": "user",
                "message": {"content": invocation.payload_json.decode("utf-8")},
            },
        )
        self.assertNotEqual(requests[0].stdin.rstrip(b"\n"), invocation.payload_json)
        self.assertEqual(result.actual_transport_utf8_bytes, len(requests[0].stdin))
        self.assertGreater(
            result.actual_transport_utf8_bytes, len(invocation.payload_json)
        )
        self.assertEqual(result.usage, ProviderUsage(0, 4))
        self.assertEqual(
            invocation.payload_accounting.payload_utf8_bytes,
            len(invocation.payload_json),
        )

    def test_provider_usage_accepts_only_recognized_exact_snapshot_counters(
        self,
    ) -> None:
        cases = (
            (None, None),
            ({}, None),
            ({"unknown": 7}, None),
            (
                {
                    "input_tokens": 0,
                    "output_tokens": 2,
                    "cache_read_tokens": 3,
                },
                ProviderUsage(0, 2, 3),
            ),
            ({"input_tokens": 4}, ProviderUsage(input_tokens=4)),
            (
                {
                    "input_tokens": 5,
                    "output_tokens": -1,
                    "cache_read_tokens": True,
                    "unknown": 9,
                },
                ProviderUsage(input_tokens=5),
            ),
            (
                {
                    "input_tokens": -1,
                    "output_tokens": "2",
                    "cache_read_tokens": False,
                },
                None,
            ),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(AntigravityAdapter.provider_usage(value), expected)

    def test_malformed_usage_never_invalidates_a_valid_provider_response(
        self,
    ) -> None:
        result = AntigravityAdapter._provider_result(
            _stream(
                "conversation-usage",
                response="valid provider response",
                usage={
                    "input_tokens": True,
                    "output_tokens": -1,
                    "cache_read_tokens": "3",
                    "unknown": 4,
                },
            ),
            expected_thread_id=None,
        )

        self.assertEqual(result.thread_id, "conversation-usage")
        self.assertEqual(result.response_text, "valid provider response")
        self.assertIsNone(result.usage)

    def test_model_effort_precedence_and_backend_projection_are_immutable(
        self,
    ) -> None:
        agent_settings = {
            PLOT_ARCHITECT: ("agent-model", "medium"),
            CHARACTER_CUSTODIAN: (None, None),
        }
        adapter = self.adapter(
            "precedence-state",
            system_model="system-model",
            system_effort="low",
            agent_settings=agent_settings,
        )
        agent_settings[PLOT_ARCHITECT] = ("mutated-model", "high")
        self.assertEqual(
            adapter.invocation_settings(PLOT_ARCHITECT),
            ("agent-model", "medium"),
        )
        self.assertEqual(
            adapter.invocation_settings(CHARACTER_CUSTODIAN),
            ("system-model", "low"),
        )
        command = adapter.command(
            provider_state_directory=self.workspace.path("provider"),
            schema={"type": "object"},
            thread_id=None,
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(
            command[command.index("--model") :],
            ["--model", "agent-model", "--effort", "medium"],
        )

        explicit = self.adapter(
            "explicit-state",
            model="explicit-model",
            effort="high",
            system_model="system-model",
            system_effort="low",
            agent_settings={PLOT_ARCHITECT: ("agent-model", "medium")},
        )
        self.assertEqual(
            explicit.invocation_settings(PLOT_ARCHITECT),
            ("explicit-model", "high"),
        )
        self.assertEqual(
            self.adapter("default-state").invocation_settings(None),
            (None, None),
        )
        with self.assertRaisesRegex(
            ValueError,
            "Antigravity agent setting is unavailable",
        ):
            adapter.invocation_settings("unknown_agent")
        with self.assertRaisesRegex(
            ValueError,
            "Antigravity agent definition is invalid",
        ):
            self.adapter(
                "missing-agent-state",
                agent_settings={CHARACTER_CUSTODIAN: (None, None)},
            ).bind(self.definition)

        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults.update(
            {
                "antigravity_model": "persisted-system-model",
                "antigravity_effort": "low",
            }
        )
        self.workspace.write_json("config/system_defaults.json", defaults)
        agents = self.workspace.read_json("config/agent_configs.json")
        agents[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "antigravity_model": "persisted-agent-model",
                "antigravity_effort": "medium",
            }
        )
        self.workspace.write_json("config/agent_configs.json", agents)
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="private-agy",
        )
        self.addCleanup(backend.close)
        self.assertEqual(
            (backend.antigravity_model, backend.antigravity_effort),
            ("persisted-system-model", "low"),
        )
        self.assertEqual(
            backend._antigravity_adapter.invocation_settings(PLOT_ARCHITECT),
            ("persisted-agent-model", "medium"),
        )
        agents[PLOT_ARCHITECT]["antigravity_model"] = "mutated-after-init"
        self.workspace.write_json("config/agent_configs.json", agents)
        self.assertEqual(
            backend._antigravity_adapter.invocation_settings(PLOT_ARCHITECT),
            ("persisted-agent-model", "medium"),
        )

    async def test_request_encoding_failures_are_fixed_private_and_prelaunch(
        self,
    ) -> None:
        adapter = self.adapter("encoding-state")
        binding = adapter.bind(self.definition)
        sessions_root = adapter.state_root / "sessions"
        self.assertFalse(sessions_root.exists())
        confidential_schema_value = type(
            "CONFIDENTIAL_SCHEMA_VALUE",
            (),
            {},
        )()
        circular_payload: dict[str, object] = {
            "story": "CONFIDENTIAL_CIRCULAR_PAYLOAD",
        }
        circular_payload["cycle"] = circular_payload
        cases = (
            (
                "nonserializable schema",
                {"type": "object", "const": confidential_schema_value},
                {"story": "safe"},
            ),
            (
                "circular payload",
                {"type": "object"},
                circular_payload,
            ),
            (
                "surrogate payload",
                {"type": "object"},
                {"story": "CONFIDENTIAL_SURROGATE_PAYLOAD\ud800"},
            ),
        )
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )

        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            forbidden_process,
        ):
            for label, schema, payload in cases:
                with self.subTest(failure=label):
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(
                            binding,
                            ProviderInvocation(
                                session_id=f"encoding-{label.replace(' ', '-')}",
                                schema=schema,
                                payload=payload,
                                thread_id=None,
                            ),
                        )

                    error = caught.exception
                    self.assertEqual(
                        str(error),
                        "Antigravity request could not be encoded",
                    )
                    self.assertEqual(
                        error.args,
                        ("Antigravity request could not be encoded",),
                    )
                    self.assertIs(error.taint_existing_thread, False)
                    self.assertNotIn("CONFIDENTIAL", str(error))
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                    self.assertEqual(
                        provider_failure_accounting(error).state,
                        ProviderCallState.PREPARED,
                    )
                    self.assertIsNone(
                        provider_failure_accounting(error).actual_transport_utf8_bytes
                    )
                    self.assertFalse(sessions_root.exists())

        forbidden_process.assert_not_awaited()

    async def test_backend_routes_and_persists_antigravity_conversation_id(
        self,
    ) -> None:
        session_id = "backend-session"
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
            antigravity_executable="private-agy",
            antigravity_model="agy-model",
            antigravity_effort="medium",
        )
        self.addCleanup(backend.close)
        backend.create_session_with_access(session_id)
        bound = await backend.bind(self.definition)
        self.assertEqual(backend.antigravity_executable, "private-agy")
        self.assertEqual(backend.antigravity_model, "agy-model")
        self.assertEqual(backend.antigravity_effort, "medium")
        first_invoke = mock.AsyncMock(
            return_value=ProviderResult(
                thread_id="agy-conversation",
                response_text='{"revision_id":"r0","content":"candidate"}',
            )
        )
        with mock.patch.object(
            backend._antigravity_adapter,
            "invoke",
            first_invoke,
        ):
            reply = await bound.invoke(self.request(session_id, debate_id="debate-one"))
        self.assertEqual(reply, AgentReply.candidate("r0", "candidate"))
        first_invocation = first_invoke.await_args.args[1]
        self.assertIsNone(first_invocation.thread_id)
        manifest = self.workspace.read_json(
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
        )
        self.assertEqual(
            manifest["provider_threads"]["antigravity:plot_architect"],
            {
                "backend": "antigravity",
                "agent_id": "plot_architect",
                "fingerprint": self.definition.fingerprint,
                "thread_id": "agy-conversation",
                "tainted": False,
                "updated_at": manifest["provider_threads"][
                    "antigravity:plot_architect"
                ]["updated_at"],
            },
        )
        retained_staging = bound.staging_directory
        backend.close()

        resumed_backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
            antigravity_executable="private-agy",
            antigravity_model="agy-model",
            antigravity_effort="medium",
        )
        self.addCleanup(resumed_backend.close)
        resumed_backend.acquire_session_access(session_id)
        resumed_bound = await resumed_backend.bind(self.definition)
        self.assertEqual(resumed_bound.staging_directory, retained_staging)
        self.assertTrue(retained_staging.is_dir())
        second_invoke = mock.AsyncMock(
            return_value=ProviderResult(
                thread_id="agy-conversation",
                response_text='{"revision_id":"r0","content":"resumed"}',
            )
        )
        with mock.patch.object(
            resumed_backend._antigravity_adapter,
            "invoke",
            second_invoke,
        ):
            resumed_reply = await resumed_bound.invoke(
                self.request(session_id, debate_id="debate-two")
            )
        self.assertEqual(resumed_reply, AgentReply.candidate("r0", "resumed"))
        second_invocation = second_invoke.await_args.args[1]
        self.assertEqual(second_invocation.thread_id, "agy-conversation")

    async def test_identifier_and_stream_failures_are_fixed_and_confidential(
        self,
    ) -> None:
        adapter = self.adapter("validation-state")
        binding = adapter.bind(self.definition)
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )
        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            forbidden_process,
        ):
            invalid_invocations = (
                (
                    ProviderInvocation(
                        session_id="../CONFIDENTIAL_SESSION",
                        schema={},
                        payload={},
                        thread_id=None,
                    ),
                    "Antigravity session ID is invalid",
                ),
                (
                    ProviderInvocation(
                        session_id="safe-session",
                        schema={},
                        payload={},
                        thread_id="--CONFIDENTIAL_THREAD",
                    ),
                    "Antigravity conversation ID is invalid",
                ),
            )
            for invocation, diagnostic in invalid_invocations:
                with (
                    self.subTest(diagnostic=diagnostic),
                    self.assertRaises(ProviderInvocationError) as caught,
                ):
                    await adapter.invoke(binding, invocation)
                self.assertEqual(str(caught.exception), diagnostic)
                self.assertIs(caught.exception.taint_existing_thread, False)
                self.assertNotIn("CONFIDENTIAL", str(caught.exception))
        forbidden_process.assert_not_awaited()
        self.assertFalse(
            (self.workspace.path("validation-state") / "sessions").exists()
        )

        confidential = "CONFIDENTIAL_PROVIDER_OUTPUT"
        stream_cases = (
            (
                "unsafe ID",
                _stream(
                    "--CONFIDENTIAL_PROVIDER_OUTPUT",
                    response=confidential,
                ),
                None,
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "mismatched ID",
                _stream("other-conversation", response=confidential),
                "expected-conversation",
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "malformed JSONL",
                b'{"event":"init","CONFIDENTIAL_PROVIDER_OUTPUT":\n',
                None,
                "Antigravity CLI emitted malformed JSONL at line 1",
            ),
            (
                "non-success",
                _stream(
                    "failed-conversation",
                    status="FAILED_CONFIDENTIAL_PROVIDER_OUTPUT",
                    response=confidential,
                ),
                None,
                "Antigravity CLI did not complete successfully",
            ),
        )
        for label, stdout, expected_thread_id, diagnostic in stream_cases:
            with (
                self.subTest(stream=label),
                self.assertRaises(ProviderInvocationError) as caught,
            ):
                adapter._provider_result(
                    stdout,
                    expected_thread_id=expected_thread_id,
                )
            self.assertEqual(str(caught.exception), diagnostic)
            self.assertIs(caught.exception.taint_existing_thread, True)
            self.assertNotIn(confidential, str(caught.exception))

    async def test_nonzero_timeout_and_cancellation_use_bounded_cleanup(
        self,
    ) -> None:
        cases = (
            (
                "nonzero",
                9,
                None,
                "Antigravity CLI exited with status 9",
            ),
            (
                "timeout",
                None,
                TimeoutError("CONFIDENTIAL_TIMEOUT"),
                "Antigravity CLI timed out after 4 seconds",
            ),
            (
                "cancellation",
                None,
                asyncio.CancelledError("CONFIDENTIAL_CANCELLATION"),
                None,
            ),
        )
        for label, returncode, communication_failure, diagnostic in cases:
            with self.subTest(transport=label):
                events: list[str] = []
                timeouts: list[float] = []
                communication = asyncio.Future()
                if communication_failure is None:
                    communication.set_result(
                        (
                            b"CONFIDENTIAL_PROVIDER_STDOUT",
                            b"CONFIDENTIAL_PROVIDER_STDERR",
                        )
                    )
                stopped = asyncio.Future()
                stopped.set_result(None)
                process = mock.Mock(returncode=returncode)
                process.communicate.side_effect = (
                    lambda _stdin: events.append("communicate") or communication
                )
                process.terminate.side_effect = lambda: events.append("terminate")
                process.wait.side_effect = lambda: events.append("wait") or stopped
                process.kill.side_effect = lambda: events.append("kill")

                async def create_process(*_command, **_options):
                    events.append("launch")
                    return process

                async def wait_for(awaitable, *, timeout):
                    timeouts.append(timeout)
                    if awaitable is communication and communication_failure is not None:
                        awaitable.cancel()
                        raise communication_failure
                    return await awaitable

                adapter = self.adapter(
                    f"transport-{label}",
                    timeout_seconds=4,
                    create_process=create_process,
                    wait_for=wait_for,
                )
                binding = adapter.bind(self.definition)
                invocation = ProviderInvocation(
                    session_id=f"transport-{label}",
                    schema={"type": "object"},
                    payload={"secret": "CONFIDENTIAL_STORY_PAYLOAD"},
                    thread_id=None,
                )
                assert invocation.payload_json is not None
                expected_stdin = (
                    canonical_provider_json(
                        {
                            "event": "user",
                            "message": {
                                "content": invocation.payload_json.decode("utf-8")
                            },
                        }
                    )
                    + b"\n"
                )
                if isinstance(communication_failure, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError) as caught:
                        await adapter.invoke(binding, invocation)
                    self.assertIs(caught.exception, communication_failure)
                else:
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(binding, invocation)
                    self.assertEqual(str(caught.exception), diagnostic)
                    self.assertIs(caught.exception.taint_existing_thread, True)
                    self.assertNotIn("CONFIDENTIAL", str(caught.exception))
                accounting = provider_failure_accounting(caught.exception)
                self.assertIs(accounting.state, ProviderCallState.SUBMITTED)
                self.assertEqual(
                    accounting.actual_transport_utf8_bytes,
                    len(expected_stdin),
                )

                if communication_failure is None:
                    self.assertEqual(events, ["launch", "communicate"])
                    self.assertEqual(timeouts, [4])
                    self.assertFalse(communication.cancelled())
                else:
                    self.assertEqual(
                        events,
                        ["launch", "communicate", "terminate", "wait"],
                    )
                    self.assertEqual(timeouts, [4, 2])
                    self.assertTrue(communication.cancelled())
                    process.terminate.assert_called_once_with()
                    process.wait.assert_called_once_with()
                process.kill.assert_not_called()

    def test_backend_preflight_reports_unverified_headless_capabilities(
        self,
    ) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="CONFIDENTIAL_AGY_EXECUTABLE",
        )
        self.addCleanup(backend.close)
        resolved = mock.Mock(backend="antigravity")
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )

        with (
            mock.patch(
                "story_writing_agents.backend.ConfigManager.resolve_from_snapshot",
                return_value=resolved,
            ),
            mock.patch.object(
                backend._antigravity_adapter,
                "executable_available",
                return_value=True,
            ) as executable_available,
            mock.patch.object(
                backend._antigravity_adapter,
                "headless_capabilities_available",
                return_value=False,
            ) as headless_capabilities_available,
            mock.patch(
                "story_writing_agents.antigravity_adapter.run_process",
                forbidden_process,
            ),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            backend.validate_provider_configuration("preflight-session")

        error = caught.exception
        self.assertEqual(
            str(error),
            "Antigravity CLI headless capabilities could not be verified",
        )
        self.assertNotIn("CONFIDENTIAL", str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        executable_available.assert_called_once_with()
        headless_capabilities_available.assert_called_once_with()
        forbidden_process.assert_not_awaited()

    def test_capability_probe_is_bounded_isolated_and_requires_public_flags(
        self,
    ) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        incomplete_help = " ".join(
            flag for flag in _REQUIRED_HEADLESS_FLAGS if flag != "--sandbox"
        ).encode("utf-8")
        keyboard_interrupt = KeyboardInterrupt("PRIVATE_PROBE_INTERRUPT")
        system_exit = SystemExit("PRIVATE_PROBE_EXIT")
        cases = (
            ("unavailable", None, None, False),
            ("oserror", "/private/fake-agy", OSError("PRIVATE_OSERROR"), False),
            (
                "subprocess",
                "/private/fake-agy",
                subprocess.SubprocessError("PRIVATE_SUBPROCESS_ERROR"),
                False,
            ),
            (
                "nonzero",
                "/private/fake-agy",
                subprocess.CompletedProcess(
                    [],
                    7,
                    stdout=b"PRIVATE_STDOUT",
                    stderr=b"PRIVATE_STDERR",
                ),
                False,
            ),
            (
                "invalid UTF-8",
                "/private/fake-agy",
                subprocess.CompletedProcess([], 0, stdout=b"\xff", stderr=b""),
                False,
            ),
            (
                "missing flags",
                "/private/fake-agy",
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=incomplete_help,
                    stderr=b"",
                ),
                False,
            ),
            (
                "success",
                "/private/fake-agy",
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=full_help,
                    stderr=b"",
                ),
                True,
            ),
            (
                "keyboard interrupt",
                "/private/fake-agy",
                keyboard_interrupt,
                None,
            ),
            ("system exit", "/private/fake-agy", system_exit, None),
        )
        calls: list[tuple[str, list[str], dict[str, object]]] = []
        active_label = ""
        behavior: object = None

        def capability_run(
            command: list[str],
            **options: object,
        ) -> subprocess.CompletedProcess[bytes]:
            calls.append((active_label, command, options))
            probe = Path(
                next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--gemini_dir=")
                )
            )
            nested = probe / "nested"
            nested.mkdir(parents=True, exist_ok=True)
            (nested / "runner-residue.bin").write_bytes(b"PRIVATE_PROBE_RESIDUE")
            if isinstance(behavior, BaseException):
                raise behavior
            assert isinstance(behavior, subprocess.CompletedProcess)
            return subprocess.CompletedProcess(
                command,
                behavior.returncode,
                stdout=behavior.stdout,
                stderr=behavior.stderr,
            )

        which = mock.Mock(return_value="/private/fake-agy")
        adapter = self.adapter(
            "capability-state",
            timeout_seconds=9,
            which=which,
            capability_run=capability_run,
        )
        binding = adapter.bind(self.definition)
        rules_before = (binding.staging_directory / "AGENTS.md").read_bytes()
        native_state = (
            adapter.state_root
            / "sessions"
            / "ordinary-session"
            / self.definition.agent_id
            / self.definition.fingerprint
            / "provider"
            / "native-state.bin"
        )
        native_state.parent.mkdir(parents=True)
        native_state.write_bytes(b"PRIVATE_RETAINED_PROVIDER_STATE")
        expected_probe = adapter.state_root / "capability-probe"

        for active_label, executable, behavior, expected in cases:
            with self.subTest(result=active_label):
                which.return_value = executable
                nested = expected_probe / "preexisting" / "nested"
                nested.mkdir(parents=True)
                (nested / "residue.bin").write_bytes(b"PRIVATE_OLD_PROBE_STATE")
                if isinstance(behavior, (KeyboardInterrupt, SystemExit)):
                    with self.assertRaises(type(behavior)) as caught:
                        adapter.headless_capabilities_available()
                    self.assertIs(caught.exception, behavior)
                else:
                    self.assertIs(
                        adapter.headless_capabilities_available(),
                        expected,
                    )
                self.assertFalse(expected_probe.exists())
                self.assertEqual(
                    (binding.staging_directory / "AGENTS.md").read_bytes(),
                    rules_before,
                )
                self.assertEqual(
                    native_state.read_bytes(),
                    b"PRIVATE_RETAINED_PROVIDER_STATE",
                )

        self.assertEqual(which.call_count, len(cases))
        self.assertEqual(
            [label for label, _command, _options in calls],
            [label for label, executable, _behavior, _expected in cases if executable],
        )
        for _label, command, options in calls:
            self.assertEqual(
                command,
                [
                    "agy-test",
                    f"--gemini_dir={expected_probe}",
                    "--help",
                ],
            )
            self.assertEqual(
                options,
                {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "timeout": 5.0,
                    "check": False,
                },
            )
            self.assertFalse(any("dangerously" in argument for argument in command))
        self.assertTrue(binding.staging_directory.is_dir())
        self.assertTrue(native_state.parent.is_dir())

    def test_capability_cleanup_failure_preserves_result_and_active_exception(
        self,
    ) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        incomplete_help = " ".join(
            flag for flag in _REQUIRED_HEADLESS_FLAGS if flag != "--sandbox"
        ).encode("utf-8")
        keyboard_interrupt = KeyboardInterrupt("PRIVATE_ACTIVE_INTERRUPT")
        system_exit = SystemExit("PRIVATE_ACTIVE_EXIT")
        cancellation = asyncio.CancelledError("PRIVATE_ACTIVE_CANCELLATION")
        cases = (
            (
                "success",
                subprocess.CompletedProcess([], 0, stdout=full_help, stderr=b""),
                True,
            ),
            (
                "missing flags",
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=incomplete_help,
                    stderr=b"",
                ),
                False,
            ),
            ("keyboard interrupt", keyboard_interrupt, None),
            ("system exit", system_exit, None),
            ("cancellation", cancellation, None),
        )
        behavior: object = None

        def capability_run(
            command: list[str],
            **_options: object,
        ) -> subprocess.CompletedProcess[bytes]:
            probe = Path(
                next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--gemini_dir=")
                )
            )
            probe.mkdir(parents=True, exist_ok=True)
            (probe / "residue.bin").write_bytes(b"PRIVATE_PROBE_RESIDUE")
            if isinstance(behavior, BaseException):
                raise behavior
            assert isinstance(behavior, subprocess.CompletedProcess)
            return subprocess.CompletedProcess(
                command,
                behavior.returncode,
                stdout=behavior.stdout,
                stderr=behavior.stderr,
            )

        for label, behavior, expected in cases:
            with self.subTest(result=label):
                adapter = self.adapter(
                    f"cleanup-failure-{label.replace(' ', '-')}",
                    which=mock.Mock(return_value="/private/fake-agy"),
                    capability_run=capability_run,
                )
                probe = adapter.state_root / "capability-probe"
                cleanup_failure = OSError("PRIVATE_CLEANUP_FAILURE")
                with mock.patch(
                    "story_writing_agents.antigravity_adapter.shutil.rmtree",
                    side_effect=cleanup_failure,
                ) as cleanup:
                    if isinstance(
                        behavior,
                        (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
                    ):
                        with self.assertRaises(type(behavior)) as caught:
                            adapter.headless_capabilities_available()
                        self.assertIs(caught.exception, behavior)
                    else:
                        self.assertIs(
                            adapter.headless_capabilities_available(),
                            expected,
                        )
                cleanup.assert_called_once_with(probe, ignore_errors=True)
                self.assertTrue(probe.is_dir())

    def test_capability_cleanup_process_control_propagates_exactly(self) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        failures = (
            KeyboardInterrupt("PRIVATE_CLEANUP_INTERRUPT"),
            SystemExit("PRIVATE_CLEANUP_EXIT"),
            asyncio.CancelledError("PRIVATE_CLEANUP_CANCELLATION"),
        )
        for failure in failures:
            with self.subTest(exception=type(failure).__name__):
                capability_run = mock.Mock(
                    return_value=subprocess.CompletedProcess(
                        [],
                        0,
                        stdout=full_help,
                        stderr=b"",
                    )
                )
                adapter = self.adapter(
                    f"cleanup-control-{type(failure).__name__.lower()}",
                    which=mock.Mock(return_value="/private/fake-agy"),
                    capability_run=capability_run,
                )
                probe = adapter.state_root / "capability-probe"
                with (
                    mock.patch(
                        "story_writing_agents.antigravity_adapter.shutil.rmtree",
                        side_effect=failure,
                    ) as cleanup,
                    self.assertRaises(type(failure)) as caught,
                ):
                    adapter.headless_capabilities_available()

                self.assertIs(caught.exception, failure)
                capability_run.assert_called_once()
                cleanup.assert_called_once_with(probe, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
