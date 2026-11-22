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
from story_writing_agents import antigravity_agent_definition
from story_writing_agents import (
    AgentBackend,
    AgentReply,
    BackendProtocolError,
    PLOT_ARCHITECT,
    ReplyTrace,
    ReplyTraceOrigin,
)
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.agent_context import (
    ContextComponentKind,
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage,
    canonical_provider_json,
)
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
    ProviderResponseDiagnostic,
    provider_failure_accounting,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from story_writing_agents.workspace.session_artifact_binding import (
    SessionArtifactBindingError,
    SessionArtifactAccess,
    compose_session_artifact_binding,
)
from story_writing_agents.workspace.session_resources import provision_session_resources

from tests.antigravity_adapter_fixture import (
    AntigravityAdapterFixture,
    _DEFAULT_RESULT_SCHEMA,
    _UNSET,
    _json_result,
    _mode,
)
from tests.helpers import live_provider_execution_workspace, pristine_initialized_workspace
from story_writing_agents.agent_context.request_compaction import compact_context_payload
from tests.test_agent_context_accounting import _shared_context_payload
from tests.test_antigravity_native_output_schema import (
    AntigravitySchemaRenderingCases,
    _singleton_correlation_schema,
)



_NO_FOLLOW_DIRECTORY_HARDENING = "chmod" in {
    getattr(operation, "__name__", None)
    for operation in getattr(os, "supports_follow_symlinks", ())
} or (
    bool(getattr(os, "O_NOFOLLOW", 0))
    and bool(getattr(os, "O_DIRECTORY", 0))
    and hasattr(os, "fchmod")
)


class AntigravitySchemaRenderingTests(unittest.TestCase):
    """Retain the canonical IDs for the independent rendering cases."""

    test_nested_schema_positions_render_exact_null_and_integer_singletons = (
        AntigravitySchemaRenderingCases.test_nested_schema_positions_render_exact_null_and_integer_singletons
    )

    test_annotations_and_non_target_constraints_remain_byte_exact = (
        AntigravitySchemaRenderingCases.test_annotations_and_non_target_constraints_remain_byte_exact
    )

    test_rendering_is_deeply_immutable_idempotent_and_order_preserving = (
        AntigravitySchemaRenderingCases.test_rendering_is_deeply_immutable_idempotent_and_order_preserving
    )

    test_safe_integer_edges_and_original_constraint_equivalence = (
        AntigravitySchemaRenderingCases.test_safe_integer_edges_and_original_constraint_equivalence
    )


class AntigravityAdapterTests(AntigravityAdapterFixture, unittest.IsolatedAsyncioTestCase):
    """Verify binding, protocol, transport, and backend integration offline."""

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
                execution_workspace=execution_workspace,)
            process = mock.AsyncMock(side_effect=AssertionError("provider process invoked"))
            with mock.patch("story_writing_agents.antigravity_adapter.run_process", process):
                with self.assertRaises(ProviderInvocationError) as caught:
                    await adapter.invoke(binding, invocation)
            self.assertEqual(str(caught.exception), "Antigravity provider workspace could not be prepared")
            process.assert_not_awaited()

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
            "Antigravity provider workspace could not be prepared",)
        self.assertIs(error.taint_existing_thread, False)
        self.assertNotIn("PRIVATE", str(error))
        forbidden_process.assert_not_awaited()
        return error

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
                target.rename(ready.identity.worktree_root / f"original-{session_id}.bin")
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
            PYTHONDONTWRITEBYTECODE="1",)
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
                    usage={"input_tokens": 0, "output_tokens": 4},),
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
                    "cache_read_tokens": 3,},
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
                    "unknown": 4,},
            ),
            expected_thread_id=None,
            expected_schema=_DEFAULT_RESULT_SCHEMA,

        )

        self.assertEqual(result.thread_id, "conversation-usage")
        self.assertEqual(result.response_text, '{"value":"valid provider response"}')
        self.assertIsNone(result.usage)

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
            "story": "CONFIDENTIAL_CIRCULAR_PAYLOAD",}
        circular_payload["cycle"] = circular_payload
        cases = (
            (
                "nonserializable schema",
                {"type": "object", "const": confidential_schema_value},
                {"story": "safe"},),
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
                        "Antigravity request could not be encoded",)
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
                        ProviderCallState.PREPARED,)
                    self.assertIsNone(
                        provider_failure_accounting(error).actual_transport_utf8_bytes)
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
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json")
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
                "updated_at": thread["updated_at"],},
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
                response_text='{"response_type":"candidate_draft","response_text":"resumed"}',)
        )
        with mock.patch.object(
            resumed_backend._antigravity_adapter,
            "invoke",
            second_invoke,
        ):
            resumed_reply = await resumed_bound.invoke(
                self.request(session_id, debate_id="debate-two"))
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
                    "Antigravity session ID is invalid",),
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
            (self.workspace.path("validation-state") / "sessions").exists())

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
                "Antigravity CLI exited with status 9",),
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
                            b"CONFIDENTIAL_PROVIDER_STDERR",)
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
                    wait_for=wait_for,)
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
            antigravity_executable="CONFIDENTIAL_AGY_EXECUTABLE",)
        self.addCleanup(backend.close)
        resolved = mock.Mock(backend="antigravity")
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )

        with (
            mock.patch(
                "story_writing_agents.backend.ConfigManager.resolve_from_snapshot",
                return_value=resolved,),
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
            "Antigravity CLI headless capabilities could not be verified",)
        self.assertNotIn("CONFIDENTIAL", str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        executable_available.assert_called_once_with()
        headless_capabilities_available.assert_called_once_with()
        forbidden_process.assert_not_awaited()


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


if __name__ == "__main__":
    unittest.main()
