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
from types import SimpleNamespace
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
    PromptFileReference,
    ReplyTrace,
    ReplyTraceOrigin,
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
    ProviderResponseDiagnostic,
    provider_failure_accounting,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    SessionArtifactAccess,
    compose_session_artifact_binding,
    provision_session_resources,
)

from tests.helpers import initialized_workspace, live_provider_execution_workspace, pristine_initialized_workspace
from story_writing_agents.agent_context.request_compaction import compact_context_payload
from tests.test_agent_context_accounting import _shared_context_payload


_UNSET = object()
_DEFAULT_RESULT_SCHEMA = {"type": "object"}
_REQUIRED_HEADLESS_FLAGS = (
    "--agent",
    "--conversation",
    "--disable-slash-commands",
    "--effort",
    "--input-format",
    "--json-schema",
    "--mode",
    "--model",
    "--new-project",
    "--output-format",
    "--print",
    "--print-timeout",
    "--sandbox",
)


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
        "status": status,
    }
    for field, value in (("structured_output", structured_output), ("json_schema", json_schema), ("response", response), ("usage", usage)):
        if value is not _UNSET:
            terminal[field] = value
    return (json.dumps(terminal, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


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



def _expected_antigravity_role(value: bytes, *, contextual: bool = True) -> bytes:
    return value

def _singleton_correlation_schema():
    """Synthetic public shape from the native compatibility regression."""
    return {
        "type": "object",
        "properties": {
            "round_number": {"type": "null", "enum": [None]},
            "invocation_ordinal": {"type": "integer", "enum": [1]},
            "attempt": {"type": "integer", "enum": [1]},
        },
    }


class AntigravitySchemaRenderingTests(unittest.TestCase):
    """Verify equivalent schema transport without optional dependencies."""

    def test_nested_schema_positions_render_exact_null_and_integer_singletons(self):
        leaf = {"type": "integer", "enum": [1], "description": "retained"}
        expected = {"type": "integer", "minimum": 1, "maximum": 1,
                    "description": "retained"}
        maps = ("properties", "patternProperties", "$defs", "definitions",
                "dependentSchemas", "dependencies")
        arrays = ("allOf", "anyOf", "oneOf", "prefixItems", "items")
        values = ("additionalProperties", "additionalItems", "unevaluatedProperties",
                  "unevaluatedItems", "items", "contains", "propertyNames", "not",
                  "if", "then", "else", "contentSchema")
        for keyword in maps:
            with self.subTest(keyword=keyword):
                self.assertEqual(AntigravityAdapter.native_output_schema(
                    {keyword: {"x": leaf, "boolean": True, "dependency": ["name"]}}),
                    {keyword: {"x": expected, "boolean": True, "dependency": ["name"]}})
        for keyword in arrays:
            with self.subTest(keyword=keyword):
                self.assertEqual(AntigravityAdapter.native_output_schema(
                    {keyword: [leaf, False]}), {keyword: [expected, False]})
        for keyword in values:
            with self.subTest(keyword=keyword):
                self.assertEqual(AntigravityAdapter.native_output_schema(
                    {keyword: leaf}), {keyword: expected})
        nested = {"properties": {"action": {"allOf": [_singleton_correlation_schema()]}}}
        result = AntigravityAdapter.native_output_schema(nested)
        properties = result["properties"]["action"]["allOf"][0]["properties"]
        self.assertEqual(properties["round_number"], {"type": "null"})
        self.assertEqual(properties["attempt"], {"type": "integer", "minimum": 1, "maximum": 1})
        self.assertEqual(len(canonical_provider_json(result)) - len(canonical_provider_json(nested)), 12)

    def test_annotations_and_non_target_constraints_remain_byte_exact(self):
        targets = {"type": "integer", "enum": [1]}
        annotations = {name: targets for name in ("const", "default", "examples", "custom")}
        annotations["enum"] = [targets]
        self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(annotations)),
                         canonical_provider_json(annotations))
        cases = [
            {"type": "integer", "enum": value}
            for value in ([], [1, 2], [None, 1], [True], [1.0], [2**53], [-2**53])
        ]
        cases += [{"type": kind, "enum": [1]} for kind in ("number", "string", ["integer", "null"])]
        cases += [{"type": "integer", "enum": [1], bound: 0}
                  for bound in ("minimum", "maximum")]
        cases += [{"type": "null", "enum": [False]}, {"enum": [1]}]
        for node in cases:
            with self.subTest(node=node):
                self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(node)),
                                 canonical_provider_json(node))

    def test_rendering_is_deeply_immutable_idempotent_and_order_preserving(self):
        shared = {"description": "first", "enum": [2], "type": "integer", "title": "last"}
        original = {"properties": {"a": shared, "b": shared}, "default": shared}
        before = canonical_provider_json(original)
        rendered = AntigravityAdapter.native_output_schema(original)
        self.assertEqual(canonical_provider_json(original), before)
        self.assertEqual(list(rendered["properties"]["a"]),
                         ["description", "minimum", "maximum", "type", "title"])
        self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(rendered)),
                         canonical_provider_json(rendered))
        rendered["properties"]["a"]["title"] = "changed"
        rendered["default"]["enum"].append(3)
        self.assertEqual(canonical_provider_json(original), before)
        self.assertEqual(rendered["properties"]["b"]["title"], "last")
        no_target = {"title": "ordered", "additionalProperties": False, "type": "object"}
        self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(no_target)),
                         canonical_provider_json(no_target))

    def test_safe_integer_edges_and_original_constraint_equivalence(self):
        for number in (0, -1, 1, -(2**53 - 1), 2**53 - 1):
            original = {"type": "integer", "enum": [number], "exclusiveMinimum": number - 1}
            lowered = AntigravityAdapter.native_output_schema(original)
            self.assertEqual(lowered, {"type": "integer", "minimum": number,
                                      "maximum": number, "exclusiveMinimum": number - 1})
            for candidate in (number - 1, number, number + 1, None, True, str(number), number + .5):
                # Exact typed runtime scalar contract; no undeclared schema validator.
                original_accepts = type(candidate) is int and candidate == number
                lowered_accepts = (type(candidate) is int and lowered["minimum"] <= candidate
                                   <= lowered["maximum"] and candidate > lowered["exclusiveMinimum"])
                self.assertEqual(original_accepts, lowered_accepts)
        impossible = {"type": "integer", "enum": [1], "exclusiveMinimum": 1}
        self.assertEqual(AntigravityAdapter.native_output_schema(impossible),
                         {"type": "integer", "minimum": 1, "maximum": 1, "exclusiveMinimum": 1})


class AntigravityAdapterTests(unittest.IsolatedAsyncioTestCase):
    """Verify binding, protocol, transport, and backend integration offline."""

    def setUp(self) -> None:
        self.workspace = (
            pristine_initialized_workspace(self)
            if self._testMethodName == "test_shared_default_and_explicit_timeouts_keep_native_and_outer_deadlines_distinct"
            else initialized_workspace(self)
        )
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
                role = AntigravityAdapter._custom_agent_projection(self.definition)[0]
                with owner.open_ready_resources() as access:
                    access.install_provider_role(role)
            self._execution_workspaces[session_id] = self._execution_stack.enter_context(
                live_provider_execution_workspace(self._execution_control.root, session_id)
            )
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

    def role_boundary(self, session_id: str):
        """Provision a real session while leaving role publication to the testcase."""
        control = pristine_initialized_workspace(self)
        if not hasattr(self, "_role_control_roots"):
            self._role_control_roots = {}
        self._role_control_roots[session_id] = control.root
        ready = provision_session_resources(control.root, session_id)
        adapter = self.adapter(f"{session_id}-state")
        binding = adapter.bind(self.definition)
        self.addCleanup(adapter.release, binding)
        owner = compose_session_artifact_binding(control.root, session_id)
        plan = adapter.session_role_projection(binding)
        target = ready.identity.worktree_root / ".agents" / "agents" / plan.native_agent_id / "agent.md"
        return ready, adapter, binding, owner, plan, target

    async def assert_role_prelaunch_failure(
        self, adapter: AntigravityAdapter, binding: ProviderBinding, session_id: str,
    ) -> None:
        """Require role validation before a provider process can start."""
        with live_provider_execution_workspace(self._role_control_roots[session_id], session_id) as execution_workspace:
            invocation = ProviderInvocation(
                session_id=session_id, schema={"type": "object"},
                payload={"story": "PRIVATE_STORY_PAYLOAD"}, thread_id=None,
                execution_workspace=execution_workspace,
            )
            process = mock.AsyncMock(side_effect=AssertionError("provider process invoked"))
            with mock.patch("story_writing_agents.antigravity_adapter.run_process", process):
                with self.assertRaises(ProviderInvocationError) as caught:
                    await adapter.invoke(binding, invocation)
            self.assertEqual(str(caught.exception), "Antigravity provider workspace could not be prepared")
            process.assert_not_awaited()

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
            / self.execution_workspace(session_id).generation_provenance.removeprefix("sha256:")
            / self.definition.agent_id
            / self.definition.fingerprint
        )

    def runtime_working_directory(
        self,
        adapter: AntigravityAdapter,
        session_id: str,
    ) -> Path:
        """Return the owner-issued session worktree used by this invocation."""

        return self.execution_workspace(session_id, install_role=False).worktree_root

    def runtime_agent_path(
        self,
        adapter: AntigravityAdapter,
        session_id: str,
    ) -> Path:
        """Return the only installed custom-agent file for one invocation."""

        native_agent_id = AntigravityAdapter._custom_agent_projection(
            self.definition
        )[0].native_agent_id
        return (
            self.runtime_working_directory(adapter, session_id)
            / ".agents"
            / "agents"
            / native_agent_id
            / "agent.md"
        )

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
                for component in first.binding_components
            ),
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
                if path.is_file()
            ],
            [f"definitions/plot_architect/{self.definition.fingerprint}/agent.md"],
        )
        body, body_components = AntigravityAdapter._definition_file(self.definition)
        custom_agent, custom_components = AntigravityAdapter._custom_agent_file(
            self.definition
        )
        native_agent_id = adapter.session_role_projection(first).native_agent_id
        wrapper = AntigravityAdapter._custom_agent_wrapper(native_agent_id)
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
                    strict=True,
                )
            ),
            (len(wrapper),) + (0,) * (len(body_components) - 1),
        )
        rules = body.decode("utf-8")
        original_prompt = self.definition.system_prompt_file.read_text()
        original_clause = "contract_guidance"
        staged_clause = "invoke another agent, provider subagent, or model."
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
            rules,
        )
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
            codex_bytes, codex_components = CodexAdapter._definition_file(definition)
            agy_bytes, agy_components = AntigravityAdapter._definition_file(definition)
            role = definition.system_prompt_file.content
            prefix = role + b"\n" + definition.persona_file.content + b"\n" + f"Output:{definition.language}.\n".encode()
            self.assertTrue(codex_bytes.startswith(prefix))
            self.assertTrue(agy_bytes.startswith(prefix))
            self.assertEqual(tuple(item.kind for item in codex_components), tuple(item.kind for item in agy_components))
            for body, components in ((codex_bytes, codex_components), (agy_bytes, agy_components)):
                self.assertEqual(len(body), sum(item.serialized_utf8_bytes for item in components))
                self.assertEqual(next(item.serialized_utf8_bytes for item in components if item.kind is ContextComponentKind.ROLE_SYSTEM_PROMPT), len(role))
                for token in (b"agent-provider-wire-v10", b"contract_guidance.input_fields", b"contract_guidance.response_rules", b"agent_action", b"action_type", b"task_result", b"response_type", b"Select only an offered agent_action"):
                    self.assertIn(token, body[len(prefix):])
            self.assertIn(b"Use finish only for structured delivery of the exact schema-selected object; do not use other tools, subagents, or hidden reasoning.", agy_bytes[len(prefix):])
            self.assertNotIn(b"Do not invoke tools, other agents", agy_bytes)
            if definition is specialist:
                self.assertIn(b"Do not invoke tools except the bound provider's required structured-result\ndelivery mechanism", role)
            self.assertNotIn(b"Use finish only", codex_bytes[len(prefix):])
            self.assertEqual(CodexAdapter._definition_file(definition), (codex_bytes, codex_components))
            self.assertEqual(AntigravityAdapter._definition_file(definition), (agy_bytes, agy_components))
            self.assertEqual(definition.system_prompt_file.content, role)
        self.assertEqual(lead.fingerprint, "0" * 64)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "umask") and _NO_FOLLOW_DIRECTORY_HARDENING,
        "requires supported POSIX no-follow directory hardening",
    )
    async def test_owned_directory_chains_are_private_without_changing_parent(self) -> None:
        session_id = "private-role-chain"
        ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
        parent = ready.identity.worktree_root
        original_mode = _mode(parent)
        sentinel = parent / "PRIVATE_PARENT_SENTINEL"
        sentinel.write_bytes(b"PRIVATE_PARENT_BYTES")
        with owner.open_ready_resources() as access:
            access.install_provider_role(plan)
        for directory in (target.parent.parent.parent, target.parent.parent):
            with self.subTest(directory=directory.name):
                self.assertEqual(_mode(directory), 0o700)
        self.assertEqual(_mode(target.parent), 0o700)
        self.assert_agent_installation(target, plan.content, mode=0o400)
        self.assertEqual(_mode(parent), original_mode)
        self.assertEqual(sentinel.read_bytes(), b"PRIVATE_PARENT_BYTES")

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
                            structured_output={"value": "ok"},
                        ),
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
                            adapter_os,
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
                        invocation,
                    )

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
                        f"control-mode-{stage}-{exception_type.__name__.lower()}"
                    )
                    if stage == "invoke":
                        binding = adapter.bind(self.definition)
                        invocation = self.provider_invocation(
                            f"control-{exception_type.__name__.lower()}"
                        )
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
                                invocation,
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

    async def test_runtime_chain_rejects_links_and_files_before_launch(self) -> None:
        for component, kind in ((".agents", "link"), (".agents", "file"), ("agents", "link"), ("agents", "file")):
            with self.subTest(component=component, kind=kind):
                session_id = f"unsafe-chain-{'root' if component == '.agents' else 'nested'}-{kind}"
                ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
                outside = self.workspace.path(f"outside-{session_id}")
                outside.mkdir()
                sentinel = outside / "sentinel.bin"
                sentinel.write_bytes(b"PRIVATE_OUTSIDE_BYTES")
                entry = ready.identity.worktree_root / ".agents"
                if component == "agents":
                    entry.mkdir(mode=0o700)
                    entry /= "agents"
                if kind == "link":
                    self.directory_symlink(entry, outside)
                else:
                    entry.write_bytes(b"PRIVATE_RETAINED_ENTRY")
                with owner.open_ready_resources() as access:
                    with self.assertRaises(SessionArtifactBindingError):
                        access.install_provider_role(plan)
                await self.assert_role_prelaunch_failure(adapter, binding, session_id)
                self.assertEqual(sentinel.read_bytes(), b"PRIVATE_OUTSIDE_BYTES")
                self.assertTrue(entry.is_symlink() if kind == "link" else entry.is_file())

    async def test_agent_target_symlinks_preserve_outside_and_never_launch(self) -> None:
        for label in ("existing", "dangling"):
            with self.subTest(target=label):
                session_id = f"role-symlink-{label}"
                ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
                outside = self.workspace.path(f"outside-{session_id}.bin")
                if label == "existing":
                    outside.write_bytes(b"PRIVATE_OUTSIDE_ROLE")
                target.parent.mkdir(parents=True, mode=0o700)
                self.file_symlink(target, outside)
                with owner.open_ready_resources() as access:
                    with self.assertRaises(SessionArtifactBindingError):
                        access.install_provider_role(plan)
                await self.assert_role_prelaunch_failure(adapter, binding, session_id)
                self.assertTrue(target.is_symlink())
                self.assertEqual(target.readlink(), outside)
                if label == "existing":
                    self.assertEqual(outside.read_bytes(), b"PRIVATE_OUTSIDE_ROLE")
                else:
                    self.assertFalse(outside.exists())

    async def test_nonregular_and_multiply_linked_agent_files_are_rejected(self) -> None:
        for kind in ("directory", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                session_id = f"unsafe-role-{kind}"
                ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
                target.parent.mkdir(parents=True, mode=0o700)
                outside = self.workspace.path(f"outside-{session_id}.bin")
                if kind == "directory":
                    target.mkdir()
                    (target / "sentinel.bin").write_bytes(b"PRIVATE_DIRECTORY_BYTES")
                elif kind == "hardlink":
                    outside.write_bytes(b"PRIVATE_OUTSIDE_BYTES")
                    self.hard_link(outside, target)
                else:
                    self.fifo(target)
                with owner.open_ready_resources() as access:
                    with self.assertRaises(SessionArtifactBindingError):
                        access.install_provider_role(plan)
                await self.assert_role_prelaunch_failure(adapter, binding, session_id)
                if kind == "directory":
                    self.assertEqual((target / "sentinel.bin").read_bytes(), b"PRIVATE_DIRECTORY_BYTES")
                elif kind == "hardlink":
                    self.assertEqual(outside.read_bytes(), b"PRIVATE_OUTSIDE_BYTES")
                    self.assertEqual(target.stat().st_nlink, 2)
                else:
                    self.assertTrue(stat.S_ISFIFO(target.lstat().st_mode))

    async def test_agent_identity_mismatches_and_leaf_swap_are_contained(self) -> None:
        for kind in ("content-mismatch", "outside-symlink"):
            with self.subTest(kind=kind):
                session_id = f"role-identity-{kind}"
                ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
                with owner.open_ready_resources() as access:
                    access.install_provider_role(plan)
                original_inode = target.stat().st_ino
                target.unlink()
                outside = self.workspace.path(f"outside-{session_id}.bin")
                outside.write_bytes(b"PRIVATE_OUTSIDE_ROLE")
                if kind == "content-mismatch":
                    target.write_bytes(b"PRIVATE_REPLACED_ROLE")
                    target.chmod(0o400)
                    self.assertNotEqual(target.stat().st_ino, original_inode)
                else:
                    self.file_symlink(target, outside)
                await self.assert_role_prelaunch_failure(adapter, binding, session_id)
                self.assertEqual(outside.read_bytes(), b"PRIVATE_OUTSIDE_ROLE")
                if kind == "content-mismatch":
                    self.assertEqual(target.read_bytes(), b"PRIVATE_REPLACED_ROLE")
                else:
                    self.assertTrue(target.is_symlink())

    async def test_agent_writes_are_complete_bounded_and_failure_safe(self) -> None:
        real_write = os.write
        for kind in ("short", "zero", "oversize", "bool", "partial-error"):
            with self.subTest(write=kind):
                session_id = f"role-write-{kind}"
                ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
                writes = 0
                def controlled_write(descriptor: int, data: bytes) -> int:
                    nonlocal writes
                    writes += 1
                    if kind == "short":
                        return real_write(descriptor, data[:max(1, len(data) // 2)])
                    if kind == "partial-error" and writes == 1:
                        return real_write(descriptor, data[:1])
                    if kind == "partial-error":
                        raise OSError(errno.EIO, "PRIVATE_WRITE_FAILURE")
                    return {"zero": 0, "oversize": len(data) + 1, "bool": True}[kind]
                with owner.open_ready_resources() as access:
                    with mock.patch("story_writing_agents.workspace.session_role_projection.os.write", side_effect=controlled_write):
                        if kind == "short":
                            access.install_provider_role(plan)
                        else:
                            with self.assertRaises(SessionArtifactBindingError):
                                access.install_provider_role(plan)
                self.assertGreater(writes, 0)
                if kind == "short":
                    self.assert_agent_installation(target, plan.content, mode=0o400)
                else:
                    self.assertFalse(target.exists())
                    await self.assert_role_prelaunch_failure(adapter, binding, session_id)

    async def test_agent_cleanup_exception_precedence_is_exact(self) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            with self.subTest(control=exception_type.__name__):
                session_id = f"role-control-{exception_type.__name__.lower()}"
                ready, adapter, binding, owner, plan, target = self.role_boundary(session_id)
                with owner.open_ready_resources() as access:
                    access.install_provider_role(plan)
                with live_provider_execution_workspace(self._role_control_roots[session_id], session_id) as execution_workspace:
                    invocation = ProviderInvocation(
                        session_id=session_id,
                        schema={"type": "object"},
                        payload={"story": "PRIVATE_STORY_PAYLOAD"},
                        thread_id=None,
                        execution_workspace=execution_workspace,
                    )
                    process = mock.AsyncMock(side_effect=exception_type("PRIVATE_CONTROL"))
                    with mock.patch("story_writing_agents.antigravity_adapter.run_process", process):
                        with self.assertRaises(exception_type):
                            await adapter.invoke(binding, invocation)
                    process.assert_awaited_once()
                self.assert_agent_installation(target, plan.content, mode=0o400)

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

    async def test_command_stdin_paths_and_fresh_resume_results_are_exact(self) -> None:
        adapter = self.adapter("runtime-state", model="agy-model", effort="high")
        binding = adapter.bind(self.definition)
        schema = {"type": "object", "properties": {
            "revision_id": {"type": "string"}, "content": {"type": "string"}},
            "required": ["revision_id", "content"], "additionalProperties": False}
        payload = {"task": "PRIVATE_STORY_PROMPT", "user_payload": "PRIVATE_STORY_PAYLOAD"}
        structured = ({"revision_id": "r0", "content": "structured response"},
                      {"revision_id": "r1", "content": "second structured response"})
        results = iter(ProcessResult(stdout=self.result_bytes(
            adapter, "story-session", "conversation-one", structured_output=value,
            json_schema=schema, **({"response": "IGNORED_FALLBACK_SECRET"} if index == 0 else {})),
            stderr=b"", returncode=0) for index, value in enumerate(structured))
        requests: list[ProcessRequest] = []

        async def capture_process(request: ProcessRequest, **_seams: object) -> ProcessResult:
            requests.append(request)
            return next(results)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=capture_process):
            fresh = await adapter.invoke(binding, ProviderInvocation(
                session_id="story-session", schema=schema, payload=payload, thread_id=None,
                execution_workspace=self.execution_workspace("story-session", install_role=True)))
            resumed = await adapter.invoke(binding, ProviderInvocation(
                session_id="story-session", schema=schema, payload=payload, thread_id=fresh.thread_id,
                execution_workspace=self.execution_workspace("story-session", install_role=True)))
        for result, value in zip((fresh, resumed), structured):
            self.assertEqual(result, ProviderResult("conversation-one", canonical_provider_json(value).decode()))
            self.assertEqual(result.actual_transport_utf8_bytes, len(canonical_provider_json(payload)))
            self.assertIsNone(result.usage)
        self.assertEqual(len(requests), 2)
        provider_directory = self.runtime_root(adapter, "story-session") / "provider"
        working_directory = self.runtime_working_directory(adapter, "story-session")
        role = self.runtime_agent_path(adapter, "story-session")
        expected_command = [
            str(Path(sys.executable).resolve(strict=True)), f"--gemini_dir={provider_directory}",
            "--input-format", "text", "--output-format", "json", "--json-schema",
            canonical_provider_json(schema).decode(), "--agent",
            adapter.session_role_projection(binding).native_agent_id, "--sandbox", "--mode", "plan",
            "--disable-slash-commands", "--print-timeout", "7s", "--model", "agy-model", "--effort", "high"]
        printed_payload = canonical_provider_json(payload).decode()
        self.assertEqual(list(requests[0].command), expected_command + ["--new-project", "--print", printed_payload])
        self.assertEqual(list(requests[1].command), expected_command + ["--conversation", "conversation-one", "--print", printed_payload])
        self.assertNotIn("--project", requests[0].command)
        self.assertNotIn("--project", requests[1].command)
        self.assertNotIn("--conversation", requests[0].command)
        self.assertNotIn("--new-project", requests[1].command)
        for request in requests:
            self.assertIsNone(request.cwd)
            self.assertIs(request.execution_workspace, self.execution_workspace("story-session"))
            self.assertEqual(request.execution_workspace.worktree_root, working_directory)
            self.assertEqual(request.timeout_seconds, 37)
            self.assertIsNone(request.env)
            self.assertEqual(request.stdin, b"")
            self.assertEqual(request.command.count("--print"), 1)
            self.assertEqual(request.command[request.command.index("--print") + 1], printed_payload)
            self.assertIsNone(request.cwd)
        self.assertEqual(json.loads(expected_command[expected_command.index("--json-schema") + 1]), schema)
        self.assertEqual(role.read_bytes(), (binding.staging_directory / "agent.md").read_bytes())
        self.assert_agent_installation(role, (binding.staging_directory / "agent.md").read_bytes(), mode=0o400)
        self.assertEqual(list(provider_directory.iterdir()), [])
        self.assertEqual([path.relative_to(working_directory).as_posix()
            for path in (working_directory / ".agents").rglob("*") if path.is_file()],
            [f".agents/agents/{adapter.session_role_projection(binding).native_agent_id}/agent.md"])

        def native_backend(control, agy_executable):
            return AgentBackend(control, executable=sys.executable,
                backend_overrides={PLOT_ARCHITECT: "antigravity"}, antigravity_executable=agy_executable)

        for prefix in (".agents", ".AGENTS"):
            session_id = f"tracked-selected-{prefix[1:]}"
            control = pristine_initialized_workspace(self)
            ready = provision_session_resources(control.root, session_id)
            session_root = ready.identity.worktree_root
            subprocess.run(("git", "config", "core.ignoreCase", "false"), cwd=session_root, check=True)
            backend = native_backend(control, "private-agy")
            self.addCleanup(backend.close)
            backend.create_session_with_access(session_id)
            bound = await backend.bind(self.definition)
            role = backend._antigravity_adapter.session_role_projection(bound._provider_binding)
            selected = session_root / prefix / "agents" / role.native_agent_id / "agent.md"
            selected.parent.mkdir(parents=True)
            selected.write_bytes(role.content)
            selected.chmod(0o400)
            original_inode = selected.stat().st_ino
            subprocess.run(("git", "add", "-f", "--", selected.relative_to(session_root).as_posix()),
                           cwd=session_root, check=True)
            exclusion = control.root / ".git/info/exclude"
            exclusion_before = exclusion.read_bytes() if exclusion.exists() else None
            invoke = mock.AsyncMock()
            with mock.patch.object(backend._antigravity_adapter, "invoke", invoke), self.assertRaises(
                SessionArtifactBindingError
            ) as caught:
                await bound.invoke(self.request(session_id, debate_id=session_id))
            self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
            invoke.assert_not_awaited()
            self.assertEqual(selected.read_bytes(), role.content)
            self.assertEqual(selected.stat().st_ino, original_inode)
            self.assertEqual(exclusion.read_bytes() if exclusion.exists() else None, exclusion_before)
            if prefix == ".AGENTS":
                self.assertIn(".AGENTS", os.listdir(session_root))
                self.assertNotIn(".agents", os.listdir(session_root))

        control = pristine_initialized_workspace(self)
        ready = provision_session_resources(control.root, "swapped-selected-role")
        backend = native_backend(control, sys.executable)
        self.addCleanup(backend.close)
        backend.create_session_with_access("swapped-selected-role")
        bound = await backend.bind(self.definition)
        outside = self.workspace.path("private-swapped-role")
        outside.write_bytes(b"PRIVATE_OUTSIDE_ROLE")
        selected = ready.identity.worktree_root / ".agents/agents" / (
            backend._antigravity_adapter.session_role_projection(bound._provider_binding).native_agent_id
        ) / "agent.md"
        real_install = SessionArtifactAccess.install_provider_role

        def swap_after_install(access, plan):
            real_install(access, plan)
            selected.unlink()
            self.file_symlink(selected, outside)

        process = mock.AsyncMock()
        with mock.patch.object(SessionArtifactAccess, "install_provider_role", new=swap_after_install), mock.patch(
            "story_writing_agents.antigravity_adapter.run_process", new=process
        ), self.assertRaises(BackendProtocolError) as caught:
            await bound.invoke(self.request("swapped-selected-role", debate_id="swapped-selected-role"))
        self.assertEqual(str(caught.exception), "Antigravity provider workspace could not be prepared")
        process.assert_not_awaited()
        self.assertTrue(selected.is_symlink())
        self.assertEqual(outside.read_bytes(), b"PRIVATE_OUTSIDE_ROLE")

    async def test_contextual_unicode_print_argument_and_transport_accounting_match_payload(
        self,
    ) -> None:
        adapter = self.adapter("contextual-runtime")
        binding = adapter.bind(self.definition)
        schema = {"type": "object"}
        payload, component_paths = compact_context_payload(_shared_context_payload(), {
            ("task_input",): ContextComponentKind.CURRENT_INPUT, ("session_history",): ContextComponentKind.CONVERSATION_CONTEXT})
        payload.update({
            "request_correlation_token": "A" * 43,
            "response_contract": {"workflow_phase": "conversation", "allowed_action_types": ["search_context", "submit_result"], "result_contract_id": "text"},
        })
        invocation = ProviderInvocation(
            session_id="contextual-session",
            schema=schema,
            payload=payload,
            thread_id=None,
            call_kind=ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
            component_paths=component_paths,
            execution_workspace=self.execution_workspace("contextual-session"),
        )
        requests: list[ProcessRequest] = []

        async def capture_process(
            request: ProcessRequest,
            **_seams: object,
        ) -> ProcessResult:
            requests.append(request)
            return ProcessResult(
                stdout=self.result_bytes(
                    adapter,
                    "contextual-session",
                    "contextual-conversation",
                    structured_output={"action": "answer"},
                    json_schema=schema,
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
        self.assertEqual(requests[0].stdin, b"")
        self.assertEqual(requests[0].command[requests[0].command.index("--print") + 1].encode("utf-8"), invocation.payload_json)
        self.assertEqual(result.actual_transport_utf8_bytes, len(invocation.payload_json))
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
            _json_result(
                "conversation-usage",
                structured_output={"value": "valid provider response"},
                usage={
                    "input_tokens": True,
                    "output_tokens": -1,
                    "cache_read_tokens": "3",
                    "unknown": 4,
                },
            ),
            expected_thread_id=None,
            expected_schema=_DEFAULT_RESULT_SCHEMA,

        )

        self.assertEqual(result.thread_id, "conversation-usage")
        self.assertEqual(result.response_text, '{"value":"valid provider response"}')
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
        native_agent_id = AntigravityAdapter._custom_agent_projection(
            self.definition
        )[0].native_agent_id
        command = adapter.command(
            provider_state_directory=self.workspace.path("provider"),
            schema={"type": "object"},
            thread_id=None,
            agent_id=PLOT_ARCHITECT,
            native_agent_id=native_agent_id,
        )
        self.assertEqual(
            command[command.index("--model") :],
            ["--model", "agent-model", "--effort", "medium", "--new-project"],
        )
        self.assertEqual(
            command[command.index("--agent") : command.index("--sandbox")],
            ["--agent", native_agent_id],
        )
        for semantic_id, selected_id in (("../unsafe", native_agent_id), (PLOT_ARCHITECT, "../unsafe")):
            with self.assertRaisesRegex(ValueError, "Antigravity custom agent ID is invalid"):
                adapter.command(
                    provider_state_directory=self.workspace.path("provider"),
                    schema={"type": "object"},
                    thread_id=None,
                    agent_id=semantic_id,
                    native_agent_id=selected_id,
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
                                execution_workspace=self.execution_workspace(
                                    f"encoding-{label.replace(' ', '-')}"
                                ),
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
        control = pristine_initialized_workspace(self)
        provision_session_resources(control.root, session_id)
        backend = AgentBackend(
            control,
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
                response_text='{"response_type":"candidate_draft","response_text":"candidate"}',
            )
        )
        with mock.patch.object(
            backend._antigravity_adapter,
            "invoke",
            first_invoke,
        ):
            reply = await bound.invoke(self.request(session_id, debate_id="debate-one"))
        self.assertEqual(
            reply,
            replace(
                AgentReply.candidate("r0", "candidate"),
                trace=ReplyTrace(
                    role=PLOT_ARCHITECT,
                    action="candidate",
                    context_sources=("current_input",),
                    recovered_fields=(),
                    origin=ReplyTraceOrigin.PROVIDER,
                ),
            ),
        )
        first_invocation = first_invoke.await_args.args[1]
        self.assertIsNone(first_invocation.thread_id)
        manifest = control.read_json(
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
        )
        self.assertEqual(len(manifest["provider_threads"]), 1)
        thread_key, thread = next(iter(manifest["provider_threads"].items()))
        self.assertRegex(thread_key, r"\Aantigravity:plot_architect:[0-9a-f]{64}\Z")
        self.assertEqual(
            thread,
            {
                "backend": "antigravity",
                "agent_id": "plot_architect",
                "fingerprint": thread_key.removeprefix("antigravity:plot_architect:"),
                "thread_id": "agy-conversation",
                "tainted": False,
                "updated_at": thread["updated_at"],
            },
        )
        retained_staging = bound.staging_directory
        backend.close()

        resumed_backend = AgentBackend(
            control,
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
                response_text='{"response_type":"candidate_draft","response_text":"resumed"}',
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
        self.assertEqual(
            resumed_reply,
            replace(
                AgentReply.candidate("r0", "resumed"),
                trace=ReplyTrace(
                    role=PLOT_ARCHITECT,
                    action="candidate",
                    context_sources=("current_input",),
                    recovered_fields=(),
                    origin=ReplyTraceOrigin.PROVIDER,
                ),
            ),
        )
        second_invocation = second_invoke.await_args.args[1]
        self.assertEqual(second_invocation.thread_id, "agy-conversation")

    async def test_identifier_and_json_failures_are_fixed_and_confidential(
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

        malformed = "Antigravity CLI returned malformed JSON"
        bad_output = "Antigravity CLI returned malformed structured output"
        result_cases = (
            ("unsafe ID", _json_result("--CONFIDENTIAL_PROVIDER_OUTPUT"), None, "Antigravity CLI returned an invalid conversation ID"),
            ("mismatched ID", _json_result("other"), "expected", "Antigravity CLI returned an invalid conversation ID"),
            ("invalid UTF-8", b"\xff", None, "Antigravity CLI result is not valid UTF-8"),
            ("malformed JSON", b'{"CONFIDENTIAL_PROVIDER_OUTPUT":', None, malformed),
            ("empty", b"", None, malformed),
            ("array", b"[]", None, "Antigravity CLI returned a non-object JSON result"),
            ("scalar", b"null", None, "Antigravity CLI returned a non-object JSON result"),
            ("trailing data", _json_result("safe") + b"CONFIDENTIAL_PROVIDER_OUTPUT", None, malformed),
            ("NDJSON", _json_result("safe") * 2, None, malformed),
            ("duplicate root key", b'{"status":"SUCCESS","status":"ERROR"}', None, malformed),
            ("duplicate nested key", b'{"nested":{"x":1,"x":2}}', None, malformed),
            *((label, ('{"value":'+value+'}').encode(), None, malformed) for label,value in (("NaN", "NaN"), ("infinity", "Infinity"), ("overflow", "1e999"))),
            ("non-success", _json_result("safe", status="FAILED_CONFIDENTIAL_PROVIDER_OUTPUT"), None, "Antigravity CLI did not complete successfully"),
            ("missing structured output", _json_result("safe", response=confidential), None, bad_output),
            ("scalar structured output", _json_result("safe", structured_output=confidential), None, bad_output),
            ("missing schema echo", _json_result("safe", structured_output={}, json_schema=_UNSET), None, bad_output),
            ("mismatched schema echo", _json_result("safe", structured_output={}, json_schema={"type":"string"}), None, bad_output),
            ("surrogate output", b'{"status":"SUCCESS","conversation_id":"safe","structured_output":{"value":"\\ud800"},"json_schema":{"type":"object"}}', None, bad_output),
        )
        for label, stdout, expected_thread_id, diagnostic in result_cases:
            with (
                self.subTest(result=label),
                self.assertRaises(ProviderInvocationError) as caught,
            ):
                adapter._provider_result(
                    stdout,
                    expected_thread_id=expected_thread_id,
                    expected_schema=_DEFAULT_RESULT_SCHEMA,

                )
            self.assertEqual(str(caught.exception), diagnostic)
            self.assertIs(caught.exception.taint_existing_thread, True)
            self.assertNotIn(confidential, str(caught.exception))
            self.assertIsNone(caught.exception.continuation_rejection)

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
                    execution_workspace=self.execution_workspace(f"transport-{label}"),
                )
                assert invocation.payload_json is not None
                expected_stdin = b""
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
                    self.assertIsNone(caught.exception.continuation_rejection)
                process.communicate.assert_called_once_with(expected_stdin)
                accounting = provider_failure_accounting(caught.exception)
                self.assertIs(accounting.state, ProviderCallState.SUBMITTED)
                self.assertEqual(
                    accounting.actual_transport_utf8_bytes,
                    len(invocation.payload_json),
                )

                if communication_failure is None:
                    self.assertEqual(events, ["launch", "communicate"])
                    self.assertEqual(timeouts, [34])
                    self.assertFalse(communication.cancelled())
                else:
                    self.assertEqual(
                        events,
                        ["launch", "communicate", "terminate", "wait"],
                    )
                    self.assertEqual(timeouts, [34, 2])
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
        self.assertNotIn(b"--gemini_dir", full_help)
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
            *(
                (
                    f"missing {missing_flag}",
                    "/private/fake-agy",
                    subprocess.CompletedProcess(
                        [],
                        0,
                        stdout=" ".join(
                            flag
                            for flag in _REQUIRED_HEADLESS_FLAGS
                            if flag != missing_flag
                        ).encode("utf-8"),
                        stderr=b"",
                    ),
                    False,
                )
                for missing_flag in _REQUIRED_HEADLESS_FLAGS
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
        agent_before = (binding.staging_directory / "agent.md").read_bytes()
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
                    (binding.staging_directory / "agent.md").read_bytes(),
                    agent_before,
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
                sys.executable,
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


    async def test_rendered_schema_echo_is_exact_for_success_and_recovered_error(self):
        fixtures = Path(__file__).parent / "fixtures/antigravity_recovered_finish"
        schema = _singleton_correlation_schema()
        lowered = AntigravityAdapter.native_output_schema(schema)
        for status in ("SUCCESS", "ERROR"):
            with self.subTest(status=status):
                adapter = self.adapter("rendered-" + status.lower())
                binding = adapter.bind(self.definition)
                terminal = json.loads((fixtures / "terminal.json").read_text())
                terminal["status"] = status
                terminal["json_schema"] = lowered
                invocation = ProviderInvocation(execution_workspace=self.execution_workspace("rendered-echo"),
                    session_id="rendered-echo", schema=schema,
                    payload=json.loads((fixtures / "request.json").read_text()), thread_id=None)
                async def completed(request, **_kwargs):
                    self.assertEqual(request.command[request.command.index("--json-schema") + 1].encode(),
                                     canonical_provider_json(lowered))
                    provider = Path(request.command[1].split("=", 1)[1])
                    trace = provider / "antigravity-cli/brain/synthetic-thread/.system_generated/logs/transcript_full.jsonl"
                    trace.parent.mkdir(parents=True)
                    trace.write_bytes((fixtures / "transcript.jsonl").read_bytes())
                    return ProcessResult(stdout=canonical_provider_json(terminal), stderr=b"warning", returncode=0)
                with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=completed):
                    result = await adapter.invoke(binding, invocation)
                self.assertEqual(json.loads(result.response_text), terminal["structured_output"])
                if status == "ERROR":
                    self.assertEqual(result.response_diagnostic.stdout, canonical_provider_json(terminal))
                self.assertEqual(schema, _singleton_correlation_schema())

    async def test_unrendered_or_mismatched_echo_retains_completed_failure_bytes(self):
        schema = _singleton_correlation_schema()
        lowered = AntigravityAdapter.native_output_schema(schema)
        wrong_bound = json.loads(canonical_provider_json(lowered))
        wrong_bound["properties"]["attempt"]["maximum"] = 2
        reordered = dict(reversed(list(lowered.items())))
        for index, echoed in enumerate((schema, wrong_bound, reordered)):
            with self.subTest(index=index):
                adapter = self.adapter("bad-echo-" + str(index))
                binding = adapter.bind(self.definition)
                invocation = ProviderInvocation(execution_workspace=self.execution_workspace("echo-failure"), session_id="echo-failure", schema=schema,
                                                payload={"q": "synthetic"}, thread_id=None)
                stdout = _json_result("echo-thread", structured_output={}, json_schema=echoed)
                with mock.patch("story_writing_agents.antigravity_adapter.run_process",
                                new=mock.AsyncMock(return_value=ProcessResult(
                                    stdout=stdout, stderr=b"retained", returncode=0))):
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(binding, invocation)
                self.assertEqual(caught.exception.response_diagnostic,
                                 ProviderResponseDiagnostic(stdout, b"retained", 0))

    async def test_direct_invocation_preserves_prepared_payload_and_failure_facts(self):
        adapter = self.adapter("direct-schema")
        binding = adapter.bind(self.definition)
        schema = AntigravityAdapter.native_output_schema(_singleton_correlation_schema())
        payload = {"q": "captured", "nested": [1]}
        invocation = ProviderInvocation(execution_workspace=self.execution_workspace("direct"), session_id="direct", schema=schema, payload=payload,
                                        thread_id=None, binding_components=binding.binding_components)
        original_payload = invocation.payload_json
        original_preparation = invocation.call_accounting
        payload["q"] = "later mutation"
        payload["nested"].append(2)
        async def failed(request, **_kwargs):
            self.assertEqual(request.command[request.command.index("--print") + 1].encode(), original_payload)
            return ProcessResult(stdout=b"completed failure", stderr=b"diagnostic", returncode=7)
        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=failed):
            with self.assertRaises(ProviderInvocationError) as caught:
                await adapter.invoke(binding, invocation)
        self.assertIs(invocation.call_accounting, original_preparation)
        self.assertEqual(invocation.payload_json, original_payload)
        self.assertEqual(invocation.payload_accounting.schema_utf8_bytes, len(canonical_provider_json(schema)))
        facts = provider_failure_accounting(caught.exception)
        self.assertIs(facts.state, ProviderCallState.SUBMITTED)
        self.assertEqual(facts.actual_transport_utf8_bytes, len(original_payload))
        self.assertIsNone(facts.usage)
        self.assertEqual(caught.exception.response_diagnostic,
                         ProviderResponseDiagnostic(b"completed failure", b"diagnostic", 7))

    async def test_completed_failures_retain_exact_native_diagnostic_and_accounting(self) -> None:
        adapter = self.adapter('diagnostic-state')
        binding = adapter.bind(self.definition)
        invocation = ProviderInvocation(execution_workspace=self.execution_workspace('diagnostic'), session_id='diagnostic', schema={'type': 'object'}, payload={'q': 'synthetic'}, thread_id=None)
        for label, stdout, code in (('exit', b'raw output', 7), ('utf8', b'\xff', 0), ('json', b'{', 0), ('status', b'{"status":"ERROR"}', 0), ('schema', _json_result('thread', structured_output={}, json_schema={}), 0), ('identity', _json_result('../bad', structured_output={}), 0)):
            with self.subTest(label=label), mock.patch('story_writing_agents.antigravity_adapter.run_process', new=mock.AsyncMock(return_value=ProcessResult(stdout=stdout, stderr=b'private stderr', returncode=code))), self.assertRaises(ProviderInvocationError) as caught:
                await adapter.invoke(binding, invocation)
            error = caught.exception
            self.assertEqual(error.response_diagnostic, ProviderResponseDiagnostic(stdout, b'private stderr', code))
            self.assertNotIn('private stderr', str(error))
            self.assertNotIn('private stderr', repr(error.response_diagnostic))
            self.assertTrue(error.taint_existing_thread)
            self.assertIs(provider_failure_accounting(error).state, ProviderCallState.SUBMITTED)
            self.assertEqual(provider_failure_accounting(error).actual_transport_utf8_bytes, len(invocation.payload_json))

    async def test_recovered_invoke_uses_actual_current_trace_and_retains_error_envelope(self) -> None:
        adapter = self.adapter('recovered-invoke')
        binding = adapter.bind(self.definition)
        fixtures = Path(__file__).parent / 'fixtures/antigravity_recovered_finish'
        terminal = json.loads((fixtures / 'terminal.json').read_text())
        invocation = ProviderInvocation(execution_workspace=self.execution_workspace('recovery'), session_id='recovery', schema=terminal['json_schema'], payload=json.loads((fixtures / 'request.json').read_text()), thread_id=None)

        async def completed(request, **_kwargs):
            provider = Path(request.command[1].split('=', 1)[1])
            trace = provider / 'antigravity-cli/brain/synthetic-thread/.system_generated/logs/transcript_full.jsonl'
            trace.parent.mkdir(parents=True)
            trace.write_bytes((fixtures / 'transcript.jsonl').read_bytes())
            return ProcessResult(stdout=canonical_provider_json(terminal), stderr=b'native warning', returncode=0)
        with mock.patch('story_writing_agents.antigravity_adapter.run_process', new=completed):
            result = await adapter.invoke(binding, invocation)
        self.assertEqual(json.loads(result.response_text), terminal['structured_output'])
        self.assertEqual(result.response_diagnostic.stdout, canonical_provider_json(terminal))
        self.assertEqual(result.actual_transport_utf8_bytes, len(invocation.payload_json))

    async def test_shared_default_and_explicit_timeouts_keep_native_and_outer_deadlines_distinct(self):
        native_state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        native_state.chmod(0o700)
        self.enterContext(mock.patch.dict(os.environ, {"CODEX_HOME": str(native_state)}))
        from story_writing_agents.provider_process import ProcessTimedOut

        for configured in (None, 12.5):
            with self.subTest(configured=configured):
                expected = 900 if configured is None else configured
                for vendor in ("codex", "antigravity"):
                    with self.subTest(vendor=vendor):
                        backend = AgentBackend(self.workspace, executable=sys.executable, backend_overrides={PLOT_ARCHITECT: vendor},
                                               antigravity_executable=sys.executable,
                                               **({} if configured is None else {"timeout_seconds": configured}))
                        self.addCleanup(backend.close)
                        adapter = backend._codex_adapter if vendor == "codex" else backend._antigravity_adapter
                        self.assertEqual(adapter.timeout_seconds, expected)
                        binding = adapter.bind(replace(self.definition, backend=vendor))
                        label = "default" if configured is None else "explicit"
                        invocation = self.provider_invocation(f"deadline-{vendor}-{label}")

                        async def expire(request, **_kwargs):
                            self.assertEqual(request.timeout_seconds, expected + (30 if vendor == "antigravity" else 0))
                            if vendor == "antigravity":
                                self.assertEqual(request.command[request.command.index("--print-timeout") + 1], f"{expected:g}s")
                            else:
                                self.assertNotIn("--print-timeout", request.command)
                            raise ProcessTimedOut()

                        with contextlib.ExitStack() as stack:
                            if vendor == "codex":
                                workspace = stack.enter_context(live_provider_execution_workspace(self.workspace.root, invocation.session_id))
                                invocation = replace(invocation, execution_workspace=workspace)
                            with mock.patch(f"story_writing_agents.{vendor}_adapter.run_process", new=expire):
                                with self.assertRaises(ProviderInvocationError) as caught:
                                    await adapter.invoke(binding, invocation)
                        self.assertIn(f"after {expected:g} seconds", str(caught.exception))
                        self.assertTrue(AgentBackend._context_failure_timed_out(caught.exception))

    async def test_partial_exit_zero_requires_independent_current_finish_for_fresh_and_resumed_calls(self):
        fixtures = Path(__file__).parent / "fixtures/antigravity_recovered_finish"
        captured = json.loads((fixtures / "terminal.json").read_text())
        steps = [json.loads(line) for line in (fixtures / "transcript.jsonl").read_text().splitlines()]
        clean_steps = [steps[0], steps[-2], steps[-1]]
        terminal = dict(captured, status="SUCCESS")
        terminal.pop("error")
        markers = (
            ("timeout", b"[agy] print timeout after 900s with turn in progress; returning partial output\n"),
            ("truncated", b"error: native failure (response may be truncated)\n"),
        )
        for label, marker in markers:
            for resumed in (False, True):
                for trace_kind in ("complete", "missing", "incomplete", "stale", "wrong_request", "wrong_finish"):
                    with self.subTest(marker=label, resumed=resumed, trace=trace_kind):
                        adapter = self.adapter(f"partial-{label}-{resumed}-{trace_kind}")
                        binding = adapter.bind(self.definition)
                        invocation = ProviderInvocation(execution_workspace=self.execution_workspace("partial"), session_id="partial", schema=terminal["json_schema"],
                                                        payload=json.loads((fixtures / "request.json").read_text()),
                                                        thread_id="synthetic-thread" if resumed else None)
                        trace = self.runtime_root(adapter, "partial") / "provider/antigravity-cli/brain/synthetic-thread/.system_generated/logs/transcript_full.jsonl"

                        def encoded(items, offset=0):
                            return b"".join(canonical_provider_json(dict(item, step_index=offset + i)) + b"\n"
                                            for i, item in enumerate(items))

                        prefix = encoded(clean_steps) if resumed or trace_kind == "stale" else b""
                        if prefix:
                            trace.parent.mkdir(parents=True)
                            trace.write_bytes(prefix)

                        async def partial(_request, **_kwargs):
                            current = json.loads(json.dumps(clean_steps))
                            if trace_kind == "incomplete":
                                current.pop()
                            elif trace_kind == "wrong_request":
                                current[0]["content"] = "<USER_REQUEST>\nother input\n</USER_REQUEST>"
                            elif trace_kind == "wrong_finish":
                                current[-2]["tool_calls"][0]["args"] = {"unrelated": True}
                            if trace_kind not in {"missing", "stale"}:
                                trace.parent.mkdir(parents=True, exist_ok=True)
                                trace.write_bytes(prefix + encoded(current, len(clean_steps) if resumed else 0))
                            return ProcessResult(stdout=canonical_provider_json(terminal), stderr=marker, returncode=0)

                        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=partial):
                            if trace_kind == "complete":
                                result = await adapter.invoke(binding, invocation)
                                self.assertEqual(json.loads(result.response_text), terminal["structured_output"])
                                self.assertEqual(result.response_diagnostic.stderr, marker)
                            else:
                                with self.assertRaises(ProviderInvocationError) as caught:
                                    await adapter.invoke(binding, invocation)
                                self.assertEqual(caught.exception.timed_out, label == "timeout")
                                self.assertEqual(AgentBackend._context_failure_timed_out(caught.exception), label == "timeout")
                                self.assertTrue(caught.exception.taint_existing_thread)
                                self.assertIs(provider_failure_accounting(caught.exception).state, ProviderCallState.SUBMITTED)
                                self.assertEqual(caught.exception.response_diagnostic.exit_code, 0)

    async def test_partial_timeout_rejects_incomplete_envelopes_and_preserves_failure_facts(self):
        adapter = self.adapter("partial-envelope")
        binding = adapter.bind(self.definition)
        invocation = self.provider_invocation("partial-envelope")
        marker = b"[agy] print timeout after 7s with turn in progress; returning partial output\r\n"
        for stdout, exit_code in ((b"", 0), (b"{", 0), (b"\xff", 0),
                                   (_json_result("thread", structured_output={}), 7)):
            with self.subTest(stdout=stdout, exit_code=exit_code):
                with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=mock.AsyncMock(
                    return_value=ProcessResult(stdout=stdout, stderr=marker, returncode=exit_code))):
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(binding, invocation)
                self.assertTrue(caught.exception.timed_out)
                self.assertTrue(AgentBackend._context_failure_timed_out(caught.exception))
                self.assertIs(provider_failure_accounting(caught.exception).state, ProviderCallState.SUBMITTED)
                self.assertEqual(caught.exception.response_diagnostic, ProviderResponseDiagnostic(stdout, marker, exit_code))

    async def test_unavailable_process_output_is_not_invented(self) -> None:
        from story_writing_agents.provider_process import ProcessTimedOut, ProcessExecutableNotFound
        adapter = self.adapter('missing-output')
        binding = adapter.bind(self.definition)
        invocation = ProviderInvocation(execution_workspace=self.execution_workspace('missing'), session_id='missing', schema={'type': 'object'}, payload={'q': 'synthetic'}, thread_id=None)
        for failure in (ProcessTimedOut(), ProcessExecutableNotFound(), asyncio.CancelledError()):
            with self.subTest(type=type(failure).__name__), mock.patch('story_writing_agents.antigravity_adapter.run_process', new=mock.AsyncMock(side_effect=failure)), self.assertRaises((ProviderInvocationError, asyncio.CancelledError)) as caught:
                await adapter.invoke(binding, invocation)
            self.assertIsNone(getattr(caught.exception, 'response_diagnostic', None))


class AntigravityRecoveryEvidenceTests(unittest.TestCase):
    """Exercise real no-follow native evidence reads with synthetic public facts."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.adapter = AntigravityAdapter(executable='unused', timeout_seconds=1, state_root=self.root, explicit_model=None, explicit_effort=None)
        self.provider = self.root / 'provider'
        self.provider.mkdir()
        fixtures = Path(__file__).parent / 'fixtures/antigravity_recovered_finish'
        self.terminal = json.loads((fixtures / 'terminal.json').read_text())
        self.prompt = (fixtures / 'request.json').read_text()
        self.steps = [json.loads(line) for line in (fixtures / 'transcript.jsonl').read_text().splitlines()]
        self.trace = self.provider / 'antigravity-cli/brain/synthetic-thread/.system_generated/logs/transcript_full.jsonl'

    def write_trace(self, steps=None, *, content=None) -> None:
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        self.trace.write_bytes(content if content is not None else b''.join((canonical_provider_json(step) + b'\n' for step in (self.steps if steps is None else steps))))

    def accepts(self, baseline, *, terminal=None):
        value = self.terminal if terminal is None else terminal
        diagnostic = ProviderResponseDiagnostic(canonical_provider_json(value), b'diagnostic', 0)
        return self.adapter._provider_result(diagnostic.stdout, expected_thread_id=baseline.thread_id if baseline else None, expected_schema=self.terminal['json_schema'], response_diagnostic=diagnostic, completion_check=lambda thread, output, error: self.adapter._has_current_finish(self.provider, baseline, thread, self.prompt, output, error))

    def test_captured_shape_and_clean_current_finish_recover(self) -> None:
        for clean in (False, True):
            with self.subTest(clean=clean):
                if self.trace.exists():
                    self.trace.unlink()
                    import shutil
                    shutil.rmtree(self.trace.parents[2])
                baseline = self.adapter._native_recovery_baseline(self.provider, None)
                steps = self.steps if not clean else [self.steps[0], self.steps[-2], self.steps[-1]]
                steps = json.loads(json.dumps(steps))
                for index, step in enumerate(steps):
                    step['step_index'] = index
                self.write_trace(steps)
                result = self.accepts(baseline)
                self.assertEqual(json.loads(result.response_text), self.terminal['structured_output'])
                self.assertIsNotNone(result.response_diagnostic)

    def test_reordered_finish_arguments_match_but_scalar_types_do_not(self) -> None:
        baseline = self.adapter._native_recovery_baseline(self.provider, None)
        args = self.steps[-2]['tool_calls'][0]['args']
        self.steps[-2]['tool_calls'][0]['args'] = dict(reversed(list(args.items())))
        self.write_trace()
        self.assertIsNotNone(self.accepts(baseline))
        for expected, actual in ((True, 1), (1, True), (1, 1.0), ('1', 1)):
            with self.subTest(expected=expected, actual=actual):
                terminal = json.loads(json.dumps(self.terminal))
                terminal['structured_output']['action']['result']['typed'] = expected
                self.steps[-2]['tool_calls'][0]['args']['action']['result']['typed'] = actual
                self.write_trace()
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline, terminal=terminal)

    def test_resume_requires_unchanged_prefix_and_new_matching_request(self) -> None:
        self.write_trace()
        baseline = self.adapter._native_recovery_baseline(self.provider, 'synthetic-thread')
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        resumed = json.loads(json.dumps(self.steps))
        for step in resumed:
            step['step_index'] += len(self.steps)
        self.write_trace(self.steps + resumed)
        self.assertIsNotNone(self.accepts(baseline))
        self.steps[0]['content'] += 'changed'
        self.write_trace(self.steps + resumed)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)

    def test_missing_preexisting_wrong_request_or_replaced_directory_reject(self) -> None:
        baseline = self.adapter._native_recovery_baseline(self.provider, None)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        self.write_trace()
        preexisting = self.adapter._native_recovery_baseline(self.provider, None)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(preexisting)
        self.steps[0]['content'] = '<USER_REQUEST>\nother\n</USER_REQUEST>'
        self.write_trace()
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        self.provider.rename(self.root / 'old-provider')
        self.provider.mkdir()
        self.write_trace()
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)

    def test_unsafe_native_paths_and_bounded_trace_reject(self) -> None:
        import story_writing_agents.antigravity_adapter as module
        for kind in ('symlink', 'hardlink', 'fifo', 'directory', 'oversized', 'too_many_steps', 'long_line'):
            with self.subTest(kind=kind):
                if self.trace.exists() or self.trace.is_symlink():
                    if self.trace.is_dir():
                        self.trace.rmdir()
                    else:
                        self.trace.unlink()
                baseline = self.adapter._native_recovery_baseline(self.provider, None)
                self.write_trace()
                baseline = replace(baseline, conversation_ids=frozenset())
                if kind == 'oversized':
                    with mock.patch.object(module, '_NATIVE_TRACE_MAX_BYTES', 10):
                        with self.assertRaises(ProviderInvocationError):
                            self.accepts(baseline)
                elif kind == 'too_many_steps':
                    with mock.patch.object(module, '_NATIVE_TRACE_MAX_STEPS', 2):
                        with self.assertRaises(ProviderInvocationError):
                            self.accepts(baseline)
                elif kind == 'long_line':
                    with mock.patch.object(module, '_NATIVE_TRACE_MAX_LINE_BYTES', 8):
                        with self.assertRaises(ProviderInvocationError):
                            self.accepts(baseline)
                else:
                    self.trace.unlink()
                    outside = self.root / ('outside-' + kind)
                    if kind == 'symlink':
                        outside.write_text('outside')
                        self.trace.symlink_to(outside)
                    elif kind == 'hardlink':
                        outside.write_text('outside')
                        os.link(outside, self.trace)
                    elif kind == 'fifo':
                        os.mkfifo(self.trace)
                    else:
                        self.trace.mkdir()
                    with self.assertRaises(ProviderInvocationError):
                        self.accepts(baseline)
                    if outside.exists():
                        self.assertEqual(outside.read_text(), 'outside')
        if self.trace.exists() or self.trace.is_symlink():
            if self.trace.is_dir():
                self.trace.rmdir()
            else:
                self.trace.unlink()
        self.write_trace()
        brain = self.provider / 'antigravity-cli/brain'
        with mock.patch.object(module, '_NATIVE_SNAPSHOT_MAX_ENTRIES', 0):
            self.assertIsNone(self.adapter._native_recovery_baseline(self.provider, None))
        baseline = self.adapter._native_recovery_baseline(self.provider, None)
        baseline = replace(baseline, conversation_ids=frozenset())
        real_read = os.read
        mutated = False

        def changing_read(fd, length):
            nonlocal mutated
            value = real_read(fd, length)
            if not mutated and value:
                mutated = True
                with self.trace.open('ab') as stream:
                    stream.write(b'\n')
            return value
        with mock.patch.object(module.os, 'read', side_effect=changing_read):
            with self.assertRaises(ProviderInvocationError):
                self.accepts(baseline)
        self.assertTrue(mutated)
        self.write_trace()
        original_bytes = self.trace.read_bytes()
        outside_brain = self.root / 'outside-brain'
        brain.rename(outside_brain)
        brain.symlink_to(outside_brain, target_is_directory=True)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        outside_trace = outside_brain / 'synthetic-thread/.system_generated/logs/transcript_full.jsonl'
        self.assertEqual(outside_trace.read_bytes(), original_bytes)

    def test_incomplete_ambiguous_nonmonotonic_and_forged_native_steps_reject(self) -> None:
        baseline = self.adapter._native_recovery_baseline(self.provider, None)
        original = json.loads(json.dumps(self.steps))
        variants = []

        def changed(index, key, value):
            steps = json.loads(json.dumps(original))
            steps[index][key] = value
            return steps
        variants += [original[:-1], original + [dict(original[-1], step_index=7)], changed(0, 'step_index', True), changed(2, 'source', 'MODEL'), changed(-2, 'status', 'ACTIVE'), changed(-1, 'type', 'ERROR_MESSAGE'), changed(2, 'content', 'Error: unrelated failure'), changed(4, 'content', original[2]['content']), changed(4, 'content', original[4]['content'][:-1] + '1'), changed(-1, 'created_at', '2025-01-01T00:00:00Z'), changed(-1, 'content', 'Task is complete.'), changed(-2, 'tool_calls', [{'name': 'search', 'args': {}}]), changed(-2, 'tool_calls', [{'name': 'finish', 'args': {}}])]
        for index, steps in enumerate(variants):
            with self.subTest(index=index):
                self.write_trace(steps)
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline)
        for content in (b'\xff\n', b'{}\n{}', b'{"step_index":0,"step_index":0}\n', b'{"step_index":NaN}\n'):
            with self.subTest(content=content):
                self.write_trace(content=content)
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline)

    def test_status_identity_schema_and_diagnostic_requirements_remain_strict(self) -> None:
        baseline = self.adapter._native_recovery_baseline(self.provider, None)
        self.write_trace()
        for key, value in (('status', 'FAILED'), ('error', 'unrelated'), ('error', self.terminal['error'][:-1] + '9'), ('conversation_id', '../escape'), ('structured_output', []), ('json_schema', {})):
            with self.subTest(key=key, value=value):
                terminal = dict(self.terminal, **{key: value})
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline, terminal=terminal)
        reordered = dict(self.terminal, json_schema=dict(reversed(list(self.terminal['json_schema'].items()))))
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline, terminal=reordered)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(None)
        success = dict(self.terminal, status='SUCCESS')
        self.assertIsNone(self.accepts(None, terminal=success).response_diagnostic)

if __name__ == "__main__":
    unittest.main()
