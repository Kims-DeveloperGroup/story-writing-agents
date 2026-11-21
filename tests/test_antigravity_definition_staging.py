"""Existing Antigravity definition staging tests."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import story_writing_agents
from story_writing_agents import PLOT_ARCHITECT, PromptFileReference, antigravity_agent_definition
from story_writing_agents.agent_context import ContextComponentKind
from story_writing_agents.codex_definition_sources import read_definition_source
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ProviderResult
from story_writing_agents.provider_process import ProcessResult
from tests.antigravity_adapter_fixture import AntigravityAdapterFixture, _mode


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
                path.read_bytes() if stat.S_ISREG(value.st_mode) else None,)
        )
    return _mode(root), tuple(entries)


def _expected_antigravity_role(value: bytes, *, contextual: bool = True) -> bytes:
    return value


class AntigravityDefinitionStagingTests(AntigravityAdapterFixture, unittest.IsolatedAsyncioTestCase):
    """Verify definition assembly, staging paths, and directory protections."""

    def test_binding_is_stable_isolated_and_uses_private_custom_agent(self) -> None:
        state_root = self.workspace.path("binding-state")
        adapter = self.adapter("binding-state")
        self.assertFalse(state_root.exists())
        self.assertNotIn("AntigravityAdapter", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "AntigravityAdapter"))

        first = adapter.bind(self.definition)
        agent_before = (first.staging_directory / "agent.md").read_bytes()
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
                for component in first.binding_components),
            len(agent_before),
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
                    for _ in self.definition.context_document_files),
                ContextComponentKind.PROVIDER_CONSTRAINTS,
            ),
        )
        self.assertEqual(
            tuple(
                component.identity
                for component in first.binding_components
                if component.kind is ContextComponentKind.CORE_DOCUMENT),
            tuple(
                f"core_documents/{reference.path.name}"
                for reference in self.definition.context_document_files
            ),
        )
        self.assertEqual(
            (second.staging_directory / "agent.md").read_bytes(),
            agent_before,
        )
        self.assert_agent_installation(
            second.staging_directory / "agent.md",
            agent_before,
        )
        self.assertEqual(
            [
                path.relative_to(state_root).as_posix()
                for path in state_root.rglob("*")
                if path.is_file()],
            [f"definitions/plot_architect/{self.definition.fingerprint}/agent.md"],
        )
        body, body_components = antigravity_agent_definition.definition_file(self.definition)
        custom_agent, custom_components = antigravity_agent_definition.custom_agent_file(
            self.definition)
        native_agent_id = adapter.session_role_projection(first).native_agent_id
        wrapper = antigravity_agent_definition.custom_agent_wrapper(native_agent_id)
        self.assertEqual(agent_before, custom_agent)
        self.assertEqual(custom_agent, wrapper + body)
        self.assertRegex(native_agent_id, rf"^{self.definition.agent_id}-[0-9a-f]{{64}}$")
        self.assertEqual(adapter.session_role_projection(first).native_agent_id, native_agent_id)
        self.assertEqual(custom_agent.count(body), 1)
        self.assertEqual(len(custom_agent), len(wrapper) + len(body))
        self.assertEqual(
            sum(component.serialized_utf8_bytes for component in custom_components),
            len(custom_agent),
        )
        self.assertEqual(custom_components, first.binding_components)
        self.assertEqual(
            tuple(
                custom.serialized_utf8_bytes - original.serialized_utf8_bytes
                for custom, original in zip(
                    custom_components,
                    body_components,
                    strict=True,)
            ),
            (len(wrapper),) + (0,) * (len(body_components) - 1),
        )
        rules = body.decode("utf-8")
        original_prompt = self.definition.system_prompt_file.read_text()
        original_clause = "contract_guidance"
        self.assertIn(original_clause, original_prompt)
        self.assertIn(_expected_antigravity_role(original_prompt.encode(), contextual=False).decode(), rules)
        self.assertIn(original_clause, rules)
        self.assertEqual(self.definition.system_prompt_file.read_text(), original_prompt)
        self.assertIn(self.definition.persona_file.read_text(), rules)
        for reference in self.definition.context_document_files:
            self.assertIn(reference.path.name, rules)
            self.assertIn(reference.read_text(), rules)
        self.assertIn(
            f"Use {self.definition.language} for all visible story content.",
            rules,)
        self.assertIn("Do not use shell commands, web search, plugins", rules)
        self.assertIn("by calling finish with that complete object as its arguments.", rules)
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

    def test_contextual_binding_shared_semantics_and_vendor_constraints_are_exact(self):
        specialist = PromptAssembler(self.workspace, backend_overrides={PLOT_ARCHITECT: "antigravity"}).build_contextual_definition(session_id="definition_session", agent_id=PLOT_ARCHITECT)
        lead = replace(specialist, fingerprint="0" * 64, system_prompt_file=PromptFileReference.from_path(Path(__file__).resolve().parents[1] / "config/agents/lead_orchestrator/AGENTS.md"), behavior_policy_components=(), runtime_behavior_policy=b"")
        for definition in (specialist, lead):
            self.assertIs(definition.provider_managed_context, True)
            self.assertEqual(definition.context_document_files, ())
            codex_bytes, codex_components = read_definition_source(definition)
            agy_bytes, agy_components = antigravity_agent_definition.definition_file(definition)
            role = definition.system_prompt_file.content
            prefix = role + b"\n" + definition.persona_file.content + b"\n" + f"Output:{definition.language}.\n".encode()
            self.assertTrue(codex_bytes.startswith(prefix))
            self.assertTrue(agy_bytes.startswith(prefix))
            self.assertEqual(tuple(item.kind for item in codex_components), tuple(item.kind for item in agy_components))
            for body, components in ((codex_bytes, codex_components), (agy_bytes, agy_components)):
                self.assertEqual(len(body), sum(item.serialized_utf8_bytes for item in components))
                self.assertEqual(next(item.serialized_utf8_bytes for item in components if item.kind is ContextComponentKind.ROLE_SYSTEM_PROMPT), len(role))
                for token in (b"agent-provider-wire-v11", b"contract_guidance.input_fields", b"contract_guidance.response_rules", b"agent_action", b"action_type", b"task_result", b"response_type", b"Select only an offered agent_action"):
                    self.assertIn(token, body[len(prefix):])
            self.assertIn(b"Use finish only for structured delivery of the exact schema-selected object; do not use other tools, subagents, or hidden reasoning.", agy_bytes[len(prefix):])
            self.assertNotIn(b"Do not invoke tools, other agents", agy_bytes)
            if definition is specialist:
                self.assertIn(b"Do not invoke tools except the bound provider's required structured-result\ndelivery mechanism", role)
            self.assertNotIn(b"Use finish only", codex_bytes[len(prefix):])
            self.assertEqual(read_definition_source(definition), (codex_bytes, codex_components))
            self.assertEqual(antigravity_agent_definition.definition_file(definition), (agy_bytes, agy_components))
            self.assertEqual(definition.system_prompt_file.content, role)
        self.assertEqual(lead.fingerprint, "0" * 64)

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
                (outside / "agent.md").write_bytes(b"PRIVATE_OUTSIDE_AGENT")
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
                        forbidden_process,),
                    self.assertRaises(NotADirectoryError) as caught,
                ):
                    adapter.bind(self.definition)

                self.assertEqual(
                    str(caught.exception),
                    "Antigravity state directory is unsafe",)
                self.assertEqual(_directory_snapshot(outside), outside_before)
                self.assertEqual(_mode(parent), parent_mode_before)
                self.assertEqual(
                    parent_sentinel.read_bytes(),
                    b"PRIVATE_PARENT_BYTES",)
                if unsafe_before is not None:
                    self.assertEqual(
                        (_mode(unsafe), unsafe.read_bytes()),
                        unsafe_before,)
                forbidden_process.assert_not_awaited()

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
                        stdout=self.result_bytes(
                            adapter,
                            f"ordinary-{seam}",
                            "ordinary-mode",
                            structured_output={"value": "ok"},),
                        stderr=b"",
                        returncode=0,
                    )
                )
                invocation = self.provider_invocation(f"ordinary-{seam}")
                with contextlib.ExitStack() as stack:
                    adapter_os = SimpleNamespace(**vars(os))
                    if seam == "chmod":
                        adapter_os.supports_follow_symlinks = {real_chmod}
                        adapter_os.chmod = mock.Mock(side_effect=failure)
                    else:
                        adapter_os.supports_follow_symlinks = set()
                        adapter_os.fchmod = mock.Mock(side_effect=failure)
                    stack.enter_context(
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.os",
                            adapter_os,)
                    )
                    stack.enter_context(mock.patch("story_writing_agents.antigravity_rules.os", adapter_os))
                    stack.enter_context(mock.patch("story_writing_agents.antigravity_state_directories.os", adapter_os))
                    stack.enter_context(
                        mock.patch(
                            "story_writing_agents.antigravity_adapter.run_process",
                            process,)
                    )
                    binding = adapter.bind(self.definition)
                    result = await adapter.invoke(
                        binding,
                        invocation,)

                self.assertEqual(
                    result,
                    ProviderResult("ordinary-mode", '{"value":"ok"}'),
                )
                self.assertEqual(retained.read_bytes(), b"PRIVATE_RETAINED_BYTES")
                process.assert_awaited_once()

        controls = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
        for stage in ("bind", "invoke"):
            for exception_type in controls:
                with self.subTest(stage=stage, control=exception_type.__name__):
                    adapter = self.adapter(
                        f"control-mode-{stage}-{exception_type.__name__.lower()}")
                    if stage == "invoke":
                        binding = adapter.bind(self.definition)
                        invocation = self.provider_invocation(
                            f"control-{exception_type.__name__.lower()}")
                    failure = exception_type(f"PRIVATE_{stage.upper()}_CONTROL")
                    forbidden_process = mock.AsyncMock(
                        side_effect=AssertionError("provider process invoked"))
                    with (
                        mock.patch.object(os, "supports_follow_symlinks", set()),
                        mock.patch(
                            "story_writing_agents.antigravity_rules.os.fchmod",
                            side_effect=failure,),
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
                                invocation,
                            )

                    self.assertIs(caught.exception, failure)
                    forbidden_process.assert_not_awaited()
