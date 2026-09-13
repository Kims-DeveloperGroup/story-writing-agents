"""Exact runtime isolation for session-bound writing artifacts."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import pickle
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import story_writing_agents.workspace as workspace_facade
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.agent_context.manifest import (
    ContextScope,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    ContextSourceError,
    ContextSourceErrorCode,
    OwnAgentStateSourceAdapter,
)
from story_writing_agents.agents import (
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    behavior_policy_files,
)
from story_writing_agents.conversations import (
    CollaborationNextAction,
    CollaborationStepState,
)
from story_writing_agents.errors import WorkspaceFormatError, WorkspaceSecurityError
from story_writing_agents.git import repository_lock as lock_module
from story_writing_agents.git import session_resources as session_resources_module
from story_writing_agents.git.session_contracts import (
    GitSessionFailureCode,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
)
from story_writing_agents.git.process import GitProcessRunner
from story_writing_agents.models import AgentStateEntry, DebateMessage
from story_writing_agents.workspace import Workspace
from story_writing_agents.workspace import core as workspace_core
from story_writing_agents.workspace.core import SessionSourceWorkspace
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    SessionArtifactGenerationPin,
    compose_session_artifact_binding,
    inspect_session_resources,
    provision_session_resources,
)
from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _visible_outcome,
)


_OUTLINE = "core_documents/global_outline.md"


def _generation_pin_descriptors(
    pin: SessionArtifactGenerationPin,
) -> tuple[int, ...]:
    git_pin = pin._pin
    return () if git_pin is None else git_pin._descriptors


class RuntimeSessionArtifactBindingTests(LeadOrchestrationTestCase):
    """Prove runtime artifact reads never borrow the control or another session."""

    def control_snapshot(self) -> tuple[bytes, bytes, bytes]:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.control_workspace.root
            ),
            subprocess.check_output(
                ["git", "status", "--porcelain=v1", "-z"],
                cwd=self.control_workspace.root,
            ),
            self.control_workspace.path(_OUTLINE).read_bytes(),
        )

    def append_source_state(self, workspace: Workspace, content: str) -> None:
        workspace.append_agent_state(
            AgentStateEntry(
                PLOT_ARCHITECT,
                self.session_id,
                "source_debate",
                "audit",
                1,
                "r1",
                "outcome",
                content,
                "2026-09-11T00:00:00+00:00",
            )
        )

    def append_source_debate(self, workspace: Workspace, content: str) -> None:
        workspace.reserve_debate(
            self.session_id,
            "source_debate",
            input_kind="prompt",
            owner_token="PRIVATE_OWNER_TOKEN",
        )
        workspace.append_debate_message(
            self.session_id,
            "source_debate",
            DebateMessage(
                LEAD_ORCHESTRATOR,
                "proposal",
                1,
                "r1",
                "proposal",
                content,
                "2026-09-11T00:00:01+00:00",
            ),
        )

    def source_workspace(self) -> SessionSourceWorkspace:
        return SessionSourceWorkspace.from_artifact_binding(
            compose_session_artifact_binding(
                self.control_workspace.root,
                self.session_id,
            )
        )

    async def test_generation_pin_is_opaque_noninheritable_and_lease_free(self) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        leases = []
        original_lock = session_resources_module.lock_git_repository

        @contextmanager
        def capture_lock(root):
            with original_lock(root) as lease:
                leases.append(lease)
                yield lease

        with mock.patch.object(
            initialization_module,
            "_require_ready_locked_git_session_resources",
            side_effect=AssertionError("generation pins must not run READY"),
        ) as require_ready, mock.patch.object(
            GitProcessRunner,
            "run",
            side_effect=AssertionError("generation pins must not run Git"),
        ) as run_git, mock.patch.object(
            session_resources_module,
            "lock_git_repository",
            new=capture_lock,
        ):
            pin = binding.acquire_generation_pin()
            binding.require_generation_pin(pin)
        require_ready.assert_not_called()
        run_git.assert_not_called()
        self.assertIs(type(pin), SessionArtifactGenerationPin)
        descriptors = _generation_pin_descriptors(pin)
        self.assertEqual(len(descriptors), 6)
        self.assertTrue(
            all(not os.get_inheritable(descriptor) for descriptor in descriptors)
        )
        self.assertEqual(repr(pin), "SessionArtifactGenerationPin(<opaque>)")
        self.assertEqual(repr(pin._pin), "GitSessionGenerationPin(<opaque>)")
        with self.assertRaisesRegex(TypeError, "cannot be serialized"):
            pickle.dumps(pin)
        with self.assertRaisesRegex(TypeError, "cannot be serialized"):
            pickle.dumps(pin._pin)
        self.assertIsNone(binding._current_repository_access())
        self.assertTrue(leases)
        self.assertTrue(all(not lease._active for lease in leases))

        await asyncio.sleep(0)

        self.assertIsNone(binding._current_repository_access())
        with mock.patch.object(
            initialization_module,
            "_require_ready_locked_git_session_resources",
            side_effect=AssertionError("generation pins must not run READY"),
        ) as require_ready, mock.patch.object(
            GitProcessRunner,
            "run",
            side_effect=AssertionError("generation pins must not run Git"),
        ) as run_git, mock.patch.object(
            session_resources_module,
            "lock_git_repository",
            new=capture_lock,
        ):
            binding.require_generation_pin(pin)
        require_ready.assert_not_called()
        run_git.assert_not_called()
        self.assertTrue(all(not lease._active for lease in leases))
        pin.close()
        pin.close()
        self.assertEqual(_generation_pin_descriptors(pin), ())
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        with self.assertRaises(SessionArtifactBindingError):
            binding.require_generation_pin(pin)

    def test_generation_pins_reject_foreign_thread_and_fork_before_io(self) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        pin = binding.acquire_generation_pin()
        descriptors = _generation_pin_descriptors(pin)
        self.assertEqual(len(descriptors), 6)

        def foreign_thread_calls():
            results = []
            with mock.patch.object(
                session_resources_module,
                "lock_git_repository",
                side_effect=AssertionError("foreign pin must fail before lock"),
            ), mock.patch.object(
                session_resources_module,
                "_require_retained_generation_descriptors",
                side_effect=AssertionError(
                    "foreign pin must fail before descriptor validation"
                ),
            ):
                for operation in (
                    pin._pin.require_current,
                    lambda: pin._require_identity(binding._generation_identity),
                    lambda: binding.require_generation_pin(pin),
                ):
                    try:
                        operation()
                    except BaseException as failure:
                        results.append(failure)
            return tuple(results)

        with ThreadPoolExecutor(max_workers=1) as executor:
            thread_failures = executor.submit(foreign_thread_calls).result(timeout=10)
        self.assertEqual(len(thread_failures), 3)
        self.assertIs(type(thread_failures[0]), GitSessionResourceError)
        self.assertIs(type(thread_failures[1]), SessionArtifactBindingError)
        self.assertIs(type(thread_failures[2]), SessionArtifactBindingError)
        raw_failure = thread_failures[0]
        self.assertIs(raw_failure.code, GitSessionFailureCode.UNSAFE_ROOT)
        self.assertIs(raw_failure.operation, GitSessionOperationKind.INSPECT)
        self.assertIs(raw_failure.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            raw_failure.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        self.assertIsNone(raw_failure.__cause__)
        binding.require_generation_pin(pin)

        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def foreign_process_calls() -> None:
            outcomes = []
            with mock.patch.object(
                session_resources_module,
                "lock_git_repository",
                side_effect=AssertionError("forked pin must fail before lock"),
            ), mock.patch.object(
                session_resources_module,
                "_require_retained_generation_descriptors",
                side_effect=AssertionError(
                    "forked pin must fail before descriptor validation"
                ),
            ):
                for operation in (
                    pin._pin.require_current,
                    lambda: pin._require_identity(binding._generation_identity),
                    lambda: binding.require_generation_pin(pin),
                ):
                    try:
                        operation()
                    except BaseException as failure:
                        outcomes.append(
                            (
                                type(failure).__name__,
                                getattr(getattr(failure, "code", None), "value", None),
                                getattr(
                                    getattr(failure, "operation", None), "value", None
                                ),
                                getattr(getattr(failure, "state", None), "value", None),
                                getattr(
                                    getattr(failure, "recovery_action", None),
                                    "value",
                                    None,
                                ),
                                failure.__cause__ is None,
                            )
                        )
            pin.close()
            child_results.put(tuple(outcomes))

        process = context.Process(target=foreign_process_calls)
        try:
            process.start()
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            outcomes = child_results.get(timeout=3)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        self.assertEqual(
            outcomes,
            (
                (
                    "GitSessionResourceError",
                    GitSessionFailureCode.UNSAFE_ROOT.value,
                    GitSessionOperationKind.INSPECT.value,
                    GitSessionResourceState.RECOVERY_REQUIRED.value,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY.value,
                    True,
                ),
                ("SessionArtifactBindingError", None, None, None, None, True),
                ("SessionArtifactBindingError", None, None, None, None, True),
            ),
        )
        binding.require_generation_pin(pin)
        pin.close()
        pin.close()
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_generation_pin_fails_closed_for_every_named_topology_component(
        self,
    ) -> None:
        identity = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )._generation_identity
        assert identity is not None
        control_root = identity.session.main_root
        common_git = control_root / ".git"
        administrative_root = identity.administrative_root
        worktree_root = identity.session.worktree_root
        git_indirection = worktree_root / ".git"
        common_indirection = administrative_root / "commondir"

        @contextmanager
        def replace_directory(path, suffix):
            moved = path.with_name(f"{path.name}.{suffix}")
            path.rename(moved)
            path.mkdir()
            try:
                yield
            finally:
                shutil.rmtree(path)
                moved.rename(path)

        @contextmanager
        def remove_directory(path, suffix):
            moved = path.with_name(f"{path.name}.{suffix}")
            path.rename(moved)
            try:
                yield
            finally:
                moved.rename(path)

        @contextmanager
        def drift_file(path, _suffix):
            content = path.read_bytes()
            path.write_bytes(content + b" ")
            try:
                yield
            finally:
                path.write_bytes(content)

        cases = (
            ("control_root", control_root, remove_directory),
            ("common_git", common_git, replace_directory),
            ("session_admin", administrative_root, remove_directory),
            ("worktree", worktree_root, replace_directory),
            ("git_indirection", git_indirection, drift_file),
            ("common_indirection", common_indirection, drift_file),
        )
        for name, path, mutate in cases:
            with self.subTest(component=name):
                binding = compose_session_artifact_binding(
                    self.control_workspace.root,
                    self.session_id,
                )
                pin = binding.acquire_generation_pin()
                descriptors = _generation_pin_descriptors(pin)
                try:
                    with mutate(path, f"generation-{name}"), self.assertRaises(
                        SessionArtifactBindingError
                    ):
                        binding.require_generation_pin(pin)
                finally:
                    pin.close()
                for descriptor in descriptors:
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)

    def test_generation_pin_capture_race_and_wrong_ownership_fail_closed(
        self,
    ) -> None:
        original_capture = (
            initialization_module.capture_locked_git_session_generation
        )

        def change_expected_worktree(lease, resources, **kwargs):
            expected = kwargs["expected_worktree_identity"]
            return original_capture(
                lease,
                resources,
                expected_worktree_identity=(expected[0], expected[1] + 1),
            )

        with mock.patch.object(
            initialization_module,
            "capture_locked_git_session_generation",
            side_effect=change_expected_worktree,
        ), self.assertRaises(SessionArtifactBindingError) as caught:
            compose_session_artifact_binding(
                self.control_workspace.root,
                self.session_id,
            )
        self.assertIsNone(caught.exception.__cause__)

        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        equivalent = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        other_id = "other_generation_session"
        provision_session_resources(self.control_workspace.root, other_id)
        foreign = compose_session_artifact_binding(
            self.control_workspace.root,
            other_id,
        )
        pin = binding.acquire_generation_pin()
        equivalent.require_generation_pin(pin)
        with self.assertRaises(SessionArtifactBindingError):
            foreign.require_generation_pin(pin)
        binding.require_generation_pin(pin)
        pin.close()
        with self.assertRaises(SessionArtifactBindingError):
            binding.require_generation_pin(pin)

    def test_generation_pin_partial_capture_failures_close_local_descriptors(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        original_directory = session_resources_module._open_generation_directory
        original_duplicate = (
            session_resources_module._duplicate_generation_directory
        )
        original_file = session_resources_module._open_generation_file

        def exercise(operation):
            opened: list[int] = []

            def capture_directory(*args, **kwargs):
                descriptor = original_directory(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            def capture_duplicate(*args, **kwargs):
                descriptor = original_duplicate(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            def capture_file(parent, name):
                if name == "commondir":
                    raise OSError("injected partial generation capture")
                result = original_file(parent, name)
                opened.append(result[0])
                return result

            with mock.patch.object(
                session_resources_module,
                "_open_generation_directory",
                side_effect=capture_directory,
            ), mock.patch.object(
                session_resources_module,
                "_duplicate_generation_directory",
                side_effect=capture_duplicate,
            ), mock.patch.object(
                session_resources_module,
                "_open_generation_file",
                side_effect=capture_file,
            ), self.assertRaises(SessionArtifactBindingError):
                operation()
            self.assertEqual(len(opened), 5)
            for descriptor in opened:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

        exercise(binding.acquire_generation_pin)

        pin = binding.acquire_generation_pin()
        retained = _generation_pin_descriptors(pin)
        try:
            exercise(lambda: binding.require_generation_pin(pin))
            for descriptor in retained:
                os.fstat(descriptor)
        finally:
            pin.close()
        for descriptor in retained:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_session_source_workspace_is_internal_and_accepts_safe_posix_paths(
        self,
    ) -> None:
        self.assertFalse(hasattr(workspace_facade, "SessionSourceWorkspace"))
        self.assertNotIn("SessionSourceWorkspace", workspace_facade.__all__)
        accepted = {
            "core_documents/global_outline.md": (
                "core_documents",
                "global_outline.md",
            ),
            "manuscript/part-01/opening.md": (
                "manuscript",
                "part-01",
                "opening.md",
            ),
            "discussion_logs/session/debate-검증.jsonl": (
                "discussion_logs",
                "session",
                "debate-검증.jsonl",
            ),
        }
        for relative, expected_parts in accepted.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    SessionSourceWorkspace._safe_relative_parts(relative),
                    expected_parts,
                )

        sources = self.source_workspace()
        self.assertEqual(
            sources._read_bound(
                lambda root: sources._read_text_at(root, _OUTLINE)
            ),
            "Outline first line.\nOutline second line.\n",
        )

    def test_session_context_state_reads_overrides_from_control_owner(
        self,
    ) -> None:
        control_overrides = {
            "language": "EN",
            "persona": None,
            "agents": {},
        }
        session_overrides = {
            "language": "KO",
            "persona": None,
            "agents": {},
        }
        self.runtime.set_runtime_overrides(
            self.session_id,
            control_overrides,
        )
        self.workspace.save_session_overrides(
            self.session_id,
            session_overrides,
        )

        state = self.runtime._session_context_state(self.session_id)

        self.assertIn('"language":"EN"', state)
        self.assertNotIn('"language":"KO"', state)

    def test_guarded_definition_uses_session_policy_and_control_configuration(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        policy_relative = behavior_policy_files(PLOT_ARCHITECT)[-1]
        policy_path = self.workspace.path(policy_relative)
        marker = b"\nSESSION_WORKTREE_POLICY_MARKER\n"
        policy_path.write_bytes(policy_path.read_bytes() + marker)
        control_definition = self.runtime.prompts.build_contextual_definition(
            session_id=self.session_id,
            agent_id=PLOT_ARCHITECT,
        )

        guarded = self.runtime._build_guarded_contextual_definition(
            artifact_binding=binding,
            session_id=self.session_id,
            agent_id=PLOT_ARCHITECT,
        )

        self.assertIn(marker.strip(), guarded.system_prompt_file.content)
        self.assertNotIn(marker.strip(), control_definition.system_prompt_file.content)
        self.assertNotEqual(guarded.fingerprint, control_definition.fingerprint)
        self.assertEqual(guarded.backend, control_definition.backend)
        self.assertEqual(guarded.language, control_definition.language)
        self.assertEqual(guarded.persona_file, control_definition.persona_file)
        self.assertEqual(
            guarded.system_prompt_file.path,
            self.control_workspace.path(policy_relative),
        )

        policy_path.write_bytes(policy_path.read_bytes() + b"SECOND_POLICY_VERSION\n")
        rebuilt = self.runtime._build_guarded_contextual_definition(
            artifact_binding=binding,
            session_id=self.session_id,
            agent_id=PLOT_ARCHITECT,
        )
        self.assertNotEqual(rebuilt.fingerprint, guarded.fingerprint)

    def test_session_source_paths_reject_unsafe_lexical_forms_before_io(
        self,
    ) -> None:
        rejected = (
            "",
            ".",
            "./core_documents/global_outline.md",
            "core_documents/./global_outline.md",
            "../core_documents/global_outline.md",
            "core_documents/../global_outline.md",
            "core_documents//global_outline.md",
            "core_documents/",
            "/core_documents/global_outline.md",
            "C:/core_documents/global_outline.md",
            "core_documents\\global_outline.md",
            "core_documents/global_outline.md\x00suffix",
            "core_documents/\ud800.md",
        )
        for relative in rejected:
            with self.subTest(relative=repr(relative)), mock.patch.object(
                workspace_core.os,
                "open",
            ) as open_call, mock.patch.object(
                workspace_core.os,
                "dup",
            ) as duplicate_call, self.assertRaises(WorkspaceSecurityError):
                SessionSourceWorkspace._read_bytes_at(123456, relative)
            open_call.assert_not_called()
            duplicate_call.assert_not_called()

    def test_session_source_file_read_limit_accepts_boundary_and_rejects_next_byte(
        self,
    ) -> None:
        limit = 8 * 1024 * 1024
        with tempfile.TemporaryDirectory(prefix="session-source-file-limit-") as root:
            path = os.path.join(root, "source.bin")
            source = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.ftruncate(source, limit)
            os.close(source)
            descriptor = os.open(root, os.O_RDONLY)
            try:
                self.assertEqual(
                    len(SessionSourceWorkspace._read_bytes_at(descriptor, "source.bin")),
                    limit,
                )
                source = os.open(path, os.O_WRONLY | os.O_APPEND)
                os.write(source, b"x")
                os.close(source)
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "exceeds read limit",
                ):
                    SessionSourceWorkspace._read_bytes_at(descriptor, "source.bin")
            finally:
                os.close(descriptor)

    def test_session_source_directory_inventory_limit_is_exact_and_repeatable(
        self,
    ) -> None:
        limit = 8192
        with tempfile.TemporaryDirectory(prefix="session-source-inventory-") as root:
            directory_path = os.path.join(root, "inventory")
            os.mkdir(directory_path)
            for ordinal in range(limit):
                entry = os.open(
                    os.path.join(directory_path, f"entry-{ordinal:04d}"),
                    os.O_WRONLY | os.O_CREAT,
                    0o600,
                )
                os.close(entry)
            root_descriptor = os.open(root, os.O_RDONLY)
            descriptor = os.open(directory_path, os.O_RDONLY)
            try:
                inventory = SessionSourceWorkspace._capture_directory_inventory(
                    descriptor
                )
                self.assertEqual(len(inventory), limit)
                entry = os.open(
                    os.path.join(directory_path, "entry-over-limit"),
                    os.O_WRONLY | os.O_CREAT,
                    0o600,
                )
                os.close(entry)
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "exceeds inventory limit",
                ):
                    SessionSourceWorkspace._capture_directory_inventory(descriptor)
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "exceeds inventory limit",
                ):
                    chain = SessionSourceWorkspace._open_directory_chain_at(
                        root_descriptor,
                        "inventory",
                    )
                    try:
                        SessionSourceWorkspace._verify_directory_at(
                            root_descriptor,
                            "inventory",
                            chain,
                            inventory,
                        )
                    finally:
                        SessionSourceWorkspace._close_directory_chain(chain)
            finally:
                os.close(descriptor)
                os.close(root_descriptor)

    def test_bounded_jsonl_scanner_matches_python_splitline_boundaries(self) -> None:
        separators = (
            "\n",
            "\r\n",
            "\r",
            "\v",
            "\f",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x85",
            "\u2028",
            "\u2029",
        )

        class SplitForbidden(str):
            def splitlines(self, *args, **kwargs):
                del args, kwargs
                raise AssertionError("bounded scanner must not call splitlines")

        for separator in separators:
            text = SplitForbidden(
                f"first{separator}{separator}third{separator}"
            )
            with self.subTest(separator=ascii(separator)):
                observed = tuple(
                    SessionSourceWorkspace._iter_bounded_jsonl_lines(
                        text,
                        max_records=3,
                        limit_message="line limit",
                    )
                )
                self.assertEqual(
                    observed,
                    tuple(enumerate(str(text).splitlines(), 1)),
                )
                with self.assertRaisesRegex(WorkspaceSecurityError, "line limit"):
                    tuple(
                        SessionSourceWorkspace._iter_bounded_jsonl_lines(
                            text,
                            max_records=2,
                            limit_message="line limit",
                        )
                    )

    def test_agent_state_aggregate_byte_limit_is_exact_and_propagated(self) -> None:
        limit = 8 * 1024 * 1024
        first_size = 1024 * 1024
        def state_line(debate_id: str) -> bytes:
            self.workspace.append_agent_state(
                AgentStateEntry(
                    PLOT_ARCHITECT,
                    self.session_id,
                    debate_id,
                    "audit",
                    1,
                    "r1",
                    "outcome",
                    "aggregate state",
                    "2026-09-11T00:00:00+00:00",
                )
            )
            path = self.workspace.path(
                self.workspace._agent_state_relative(
                    PLOT_ARCHITECT,
                    self.session_id,
                    debate_id,
                )
            )
            content = path.read_bytes()
            path.unlink()
            return content

        def padded(line: bytes, size: int) -> bytes:
            self.assertLessEqual(len(line), size)
            return line.rstrip(b"\n") + b" " * (size - len(line)) + b"\n"

        first = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "aggregate_a",
            )
        )
        second = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "aggregate_b",
            )
        )
        first.write_bytes(padded(state_line("aggregate_a"), first_size))
        second.write_bytes(
            padded(state_line("aggregate_b"), limit - first_size)
        )
        sources = self.source_workspace()
        remaining_limits: list[int] = []
        original_read = SessionSourceWorkspace._read_text_at

        def capture_limit(
            root,
            relative,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            remaining_limits.append(max_bytes)
            return original_read(
                root,
                relative,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )

        with mock.patch.object(
            SessionSourceWorkspace,
            "_read_text_at",
            side_effect=capture_limit,
        ):
            state = sources.read_agent_session_state(
                PLOT_ARCHITECT,
                self.session_id,
            )
        self.assertEqual(
            tuple(item.debate_id for item in state),
            ("aggregate_a", "aggregate_b"),
        )
        self.assertEqual(remaining_limits, [limit, limit - first_size])

        second.write_bytes(second.read_bytes() + b" ")
        with mock.patch.object(
            SessionSourceWorkspace,
            "_read_text_at",
            wraps=original_read,
        ) as read_call, self.assertRaisesRegex(
            WorkspaceSecurityError,
            "aggregate read limit",
        ):
            sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        read_call.assert_not_called()

    def test_agent_state_aggregate_record_limit_rejects_first_excess_before_parse(
        self,
    ) -> None:
        debate_id = "record_limit"
        self.append_source_state(self.workspace, "bounded record")
        source = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "source_debate",
            )
        )
        line = source.read_bytes().replace(
            b"source_debate",
            debate_id.encode("utf-8"),
        )
        target = source.with_name(f"{debate_id}.jsonl")
        source.unlink()
        target.write_bytes(line * 10_000)
        sources = self.source_workspace()

        state = sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        self.assertEqual(len(state), 10_000)
        self.assertTrue(all(item.debate_id == debate_id for item in state))

        target.write_bytes(target.read_bytes() + b"not-json\n")
        with self.assertRaisesRegex(
            WorkspaceSecurityError,
            "record limit",
        ):
            sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)

    def test_debate_log_record_limit_accepts_ten_thousand_and_rejects_next(
        self,
    ) -> None:
        self.append_source_debate(self.workspace, "bounded debate")
        path = self.workspace.path(
            f"discussion_logs/{self.session_id}/source_debate.jsonl"
        )
        line = path.read_bytes()
        path.write_bytes(line * 10_000)
        sources = self.source_workspace()

        inspected = sources.inspect_debate(self.session_id, "source_debate")
        self.assertEqual(len(inspected["messages"]), 10_000)

        path.write_bytes(path.read_bytes() + b"not-json\n")
        with self.assertRaisesRegex(
            WorkspaceSecurityError,
            "debate log exceeds record limit",
        ):
            sources.inspect_debate(self.session_id, "source_debate")

    def test_ancestor_replacement_with_same_leaf_generation_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="session-source-ancestor-") as root:
            parent = os.path.join(root, "parent")
            moved = os.path.join(root, "moved")
            os.mkdir(parent)
            path = os.path.join(parent, "source.txt")
            with open(path, "wb") as stream:
                stream.write(b"original leaf bytes")
            root_descriptor = os.open(root, os.O_RDONLY)
            original_read = workspace_core.os.read
            replaced = False

            def replace_parent(descriptor, size):
                nonlocal replaced
                content = original_read(descriptor, size)
                if not replaced:
                    replaced = True
                    os.rename(parent, moved)
                    os.mkdir(parent)
                    os.rename(
                        os.path.join(moved, "source.txt"),
                        os.path.join(parent, "source.txt"),
                    )
                return content

            try:
                with mock.patch.object(
                    workspace_core.os,
                    "read",
                    side_effect=replace_parent,
                ), self.assertRaises(WorkspaceSecurityError) as caught:
                    SessionSourceWorkspace._read_bytes_at(
                        root_descriptor,
                        "parent/source.txt",
                    )
                self.assertNotIn("original leaf bytes", str(caught.exception))
            finally:
                if replaced:
                    os.rename(
                        os.path.join(parent, "source.txt"),
                        os.path.join(moved, "source.txt"),
                    )
                    os.rmdir(parent)
                    os.rename(moved, parent)
                os.close(root_descriptor)

        with tempfile.TemporaryDirectory(prefix="session-source-missing-") as root:
            root_descriptor = os.open(root, os.O_RDONLY)
            closed_chains: list[tuple[int, ...]] = []
            original_close = SessionSourceWorkspace._close_directory_chain

            def capture_close(descriptors):
                closed_chains.append(tuple(descriptors))
                original_close(descriptors)

            try:
                with mock.patch.object(
                    SessionSourceWorkspace,
                    "_close_directory_chain",
                    side_effect=capture_close,
                ):
                    self.assertIsNone(
                        SessionSourceWorkspace._read_bytes_at(
                            root_descriptor,
                            "missing/compound/source.txt",
                            missing_ok=True,
                        )
                    )
                self.assertTrue(closed_chains)
                for descriptor in {
                    item for chain in closed_chains for item in chain
                }:
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)
            finally:
                os.close(root_descriptor)

    def test_compound_state_and_debate_ancestor_replacement_fails_closed(
        self,
    ) -> None:
        self.append_source_state(self.workspace, "state generation")
        self.append_source_debate(self.workspace, "debate generation")
        state_path = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "source_debate",
            )
        )
        debate_path = self.workspace.path(
            f"discussion_logs/{self.session_id}/source_debate.jsonl"
        )
        cases = (
            (
                state_path,
                state_path.parents[2],
                state_path.parent.relative_to(state_path.parents[2]),
                lambda: self.source_workspace().read_agent_session_state(
                    PLOT_ARCHITECT,
                    self.session_id,
                ),
            ),
            (
                debate_path,
                debate_path.parents[1],
                debate_path.parent.relative_to(debate_path.parents[1]),
                lambda: self.source_workspace().inspect_debate(
                    self.session_id,
                    "source_debate",
                ),
            ),
        )
        original_read = workspace_core.os.read

        for ordinal, (target, ancestor, final_relative, read) in enumerate(cases):
            moved = ancestor.with_name(f"{ancestor.name}.generation-{ordinal}")
            replaced = False
            target_stat = target.stat()
            target_identity = (target_stat.st_dev, target_stat.st_ino)

            def replace_ancestor(descriptor, size):
                nonlocal replaced
                content = original_read(descriptor, size)
                descriptor_stat = os.fstat(descriptor)
                descriptor_identity = (
                    descriptor_stat.st_dev,
                    descriptor_stat.st_ino,
                )
                if not replaced and descriptor_identity == target_identity:
                    replaced = True
                    ancestor.rename(moved)
                    destination = ancestor / final_relative
                    destination.parent.mkdir(parents=True)
                    source = moved / final_relative
                    original_identity = (source.stat().st_dev, source.stat().st_ino)
                    source.rename(destination)
                    self.assertEqual(
                        (destination.stat().st_dev, destination.stat().st_ino),
                        original_identity,
                    )
                return content

            try:
                with self.subTest(ancestor=ancestor.name), mock.patch.object(
                    workspace_core.os,
                    "read",
                    side_effect=replace_ancestor,
                ), self.assertRaises(WorkspaceSecurityError) as caught:
                    read()
                self.assertNotIn("generation", str(caught.exception))
            finally:
                if replaced:
                    destination = ancestor / final_relative
                    source = moved / final_relative
                    source.parent.mkdir(parents=True, exist_ok=True)
                    destination.rename(source)
                    shutil.rmtree(ancestor)
                    moved.rename(ancestor)

    def test_session_source_rejects_fifo_and_hardlinked_owned_records(
        self,
    ) -> None:
        self.append_source_state(self.workspace, "owned state")
        self.append_source_debate(self.workspace, "owned debate")
        debate_root = self.workspace.path(
            f"discussion_logs/{self.session_id}"
        )
        result_path = debate_root / "source_debate.result.json"
        self.workspace.write_json(
            f"discussion_logs/{self.session_id}/source_debate.result.json",
            {
                "session_id": self.session_id,
                "debate_id": "source_debate",
                "input_kind": "prompt",
                "status": "consensus",
                "revision_id": "r1",
                "rounds_completed": 1,
                "final_candidate": "candidate",
                "latest_candidate": "candidate",
                "manuscript_path": None,
                "core_updates": [],
                "error": None,
                "action_required": None,
            },
        )
        sources = self.source_workspace()
        state_path = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "source_debate",
            )
        )
        owned_paths = (
            (state_path, lambda: sources.read_agent_session_state(
                PLOT_ARCHITECT, self.session_id
            )),
            (debate_root / "source_debate.state.json", lambda: sources.inspect_debate(
                self.session_id, "source_debate"
            )),
            (result_path, lambda: sources.inspect_debate(
                self.session_id, "source_debate"
            )),
            (debate_root / "source_debate.jsonl", lambda: sources.inspect_debate(
                self.session_id, "source_debate"
            )),
        )
        for index, (owned_path, read) in enumerate(owned_paths):
            link = self.workspace.root.parent / f"foreign-hardlink-{index}"
            os.link(owned_path, link)
            try:
                with self.subTest(path=owned_path.name), self.assertRaises(
                    WorkspaceSecurityError
                ):
                    read()
            finally:
                link.unlink()

        fifo = self.workspace.path("fifo-source")
        os.mkfifo(fifo)
        root_descriptor = os.open(self.workspace.root, os.O_RDONLY)
        try:
            with mock.patch.object(workspace_core.os, "read") as read_call, (
                self.assertRaises(WorkspaceSecurityError)
            ):
                SessionSourceWorkspace._read_bytes_at(
                    root_descriptor,
                    "fifo-source",
                )
            read_call.assert_not_called()
        finally:
            os.close(root_descriptor)

    def test_session_source_rejects_foreign_debate_ownership_and_metadata_drift(
        self,
    ) -> None:
        self.append_source_debate(self.workspace, "owned debate")
        state_relative = (
            f"discussion_logs/{self.session_id}/source_debate.state.json"
        )
        state = self.workspace.read_json(state_relative)
        sources = self.source_workspace()
        for field, value in (
            ("session_id", "foreign_session"),
            ("debate_id", "foreign_debate"),
        ):
            foreign = dict(state)
            foreign[field] = value
            self.workspace.write_json(state_relative, foreign)
            try:
                with self.subTest(record="state", field=field), self.assertRaises(
                    WorkspaceFormatError
                ):
                    sources.inspect_debate(self.session_id, "source_debate")
            finally:
                self.workspace.write_json(state_relative, state)

        result_relative = (
            f"discussion_logs/{self.session_id}/source_debate.result.json"
        )
        result = {
            "session_id": self.session_id,
            "debate_id": "source_debate",
            "input_kind": "prompt",
            "status": "consensus",
            "revision_id": "r1",
            "rounds_completed": 1,
            "final_candidate": "candidate",
            "latest_candidate": "candidate",
            "manuscript_path": None,
            "core_updates": [],
            "error": None,
            "action_required": None,
        }
        for field, value in (
            ("session_id", "foreign_session"),
            ("debate_id", "foreign_debate"),
        ):
            foreign = dict(result)
            foreign[field] = value
            self.workspace.write_json(result_relative, foreign)
            try:
                with self.subTest(record="result", field=field), self.assertRaises(
                    WorkspaceFormatError
                ):
                    sources.inspect_debate(self.session_id, "source_debate")
            finally:
                self.workspace.path(result_relative).unlink(missing_ok=True)

        root_descriptor = os.open(self.workspace.root, os.O_RDONLY)
        initial = os.stat(self.workspace.path(_OUTLINE), follow_symlinks=False)
        changed = SimpleNamespace(
            st_mode=initial.st_mode,
            st_nlink=initial.st_nlink,
            st_dev=initial.st_dev,
            st_ino=initial.st_ino,
            st_size=initial.st_size,
            st_mtime_ns=initial.st_mtime_ns + 1,
            st_ctime_ns=initial.st_ctime_ns,
        )
        try:
            with mock.patch.object(
                workspace_core.os,
                "fstat",
                side_effect=(initial, changed),
            ), self.assertRaises(WorkspaceSecurityError):
                SessionSourceWorkspace._read_bytes_at(root_descriptor, _OUTLINE)
        finally:
            os.close(root_descriptor)

    def test_agent_state_parent_swap_and_inventory_change_fail_closed(self) -> None:
        self.append_source_state(self.workspace, "retained agent state")
        sources = self.source_workspace()
        directory = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "source_debate",
            )
        ).parent

        def read():
            return sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        original_read = SessionSourceWorkspace._read_text_at

        moved = directory.with_name(f"{directory.name}.generation-a")
        swapped = False

        def swap_after_read(
            root,
            relative,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            nonlocal swapped
            value = original_read(
                root,
                relative,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )
            if not swapped:
                swapped = True
                directory.rename(moved)
                directory.mkdir()
            return value

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_read_text_at",
                side_effect=swap_after_read,
            ), self.assertRaises(WorkspaceSecurityError):
                read()
        finally:
            if swapped:
                directory.rmdir()
                moved.rename(directory)

        extra = directory / "unexpected-inventory-entry"
        changed = False

        def add_after_read(
            root,
            relative,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            nonlocal changed
            value = original_read(
                root,
                relative,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )
            if not changed:
                changed = True
                extra.write_bytes(b"replacement inventory must not be accepted")
            return value

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_read_text_at",
                side_effect=add_after_read,
            ), self.assertRaises(WorkspaceSecurityError):
                read()
        finally:
            extra.unlink(missing_ok=True)

    def test_agent_state_parent_stable_absence_is_empty_but_late_creation_fails(
        self,
    ) -> None:
        sources = self.source_workspace()
        directory = self.workspace.path(
            self.workspace._agent_state_relative(
                PLOT_ARCHITECT,
                self.session_id,
                "source_debate",
            )
        ).parent
        self.assertFalse(directory.exists())
        original = SessionSourceWorkspace._require_directory_absent_at

        with mock.patch.object(
            SessionSourceWorkspace,
            "_require_directory_absent_at",
            wraps=original,
        ) as stable_absence:
            self.assertEqual(
                sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id),
                (),
            )
        self.assertEqual(stable_absence.call_count, 2)

        calls = 0

        def create_after_first_check(root, relative):
            nonlocal calls
            result = original(root, relative)
            calls += 1
            if calls == 1:
                directory.mkdir(parents=True)
            return result

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_require_directory_absent_at",
                side_effect=create_after_first_check,
            ), self.assertRaises(WorkspaceSecurityError):
                sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        finally:
            directory.rmdir()
        self.assertEqual(calls, 1)

    def test_debate_parent_swap_and_inventory_change_fail_closed(self) -> None:
        self.append_source_debate(self.workspace, "retained debate")
        sources = self.source_workspace()
        directory = self.workspace.path(f"discussion_logs/{self.session_id}")

        def read():
            return sources.inspect_debate(self.session_id, "source_debate")
        original_read = SessionSourceWorkspace._read_text_at

        moved = directory.with_name(f"{directory.name}.generation-a")
        swapped = False

        def swap_after_read(
            root,
            relative,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            nonlocal swapped
            value = original_read(
                root,
                relative,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )
            if not swapped:
                swapped = True
                directory.rename(moved)
                directory.mkdir()
            return value

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_read_text_at",
                side_effect=swap_after_read,
            ), self.assertRaises(WorkspaceSecurityError):
                read()
        finally:
            if swapped:
                directory.rmdir()
                moved.rename(directory)

        extra = directory / "unexpected-inventory-entry"
        changed = False

        def add_after_read(
            root,
            relative,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            nonlocal changed
            value = original_read(
                root,
                relative,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )
            if not changed:
                changed = True
                extra.write_bytes(b"replacement inventory must not be accepted")
            return value

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_read_text_at",
                side_effect=add_after_read,
            ), self.assertRaises(WorkspaceSecurityError):
                read()
        finally:
            extra.unlink(missing_ok=True)

    def test_session_sources_ignore_conflicting_control_state_with_legacy_parity(
        self,
    ) -> None:
        self.append_source_state(self.control_workspace, "control state must be ignored")
        self.append_source_debate(
            self.control_workspace,
            "control debate must be ignored",
        )
        self.append_source_state(self.workspace, "session state is authoritative")
        self.append_source_debate(self.workspace, "session debate is authoritative")
        sources = self.source_workspace()
        state_scope = ContextScope(
            "workspace",
            self.session_id,
            ContextSubjectKind.CONVERSATION,
            "conversation",
            PLOT_ARCHITECT,
        )
        debate_scope = ContextScope(
            "workspace",
            self.session_id,
            ContextSubjectKind.DEBATE,
            "source_debate",
            PLOT_ARCHITECT,
        )

        state = OwnAgentStateSourceAdapter(sources, state_scope).capture(
            f"agent-state/{PLOT_ARCHITECT}",
            CONTEXT_SOURCE_POLICY,
        )
        debate = ActiveDebateSourceAdapter(
            sources,
            debate_scope,
            "source_debate",
        ).capture("debate/source_debate", CONTEXT_SOURCE_POLICY)

        self.assertIn(b"session state is authoritative", state.content)
        self.assertNotIn(b"control state must be ignored", state.content)
        self.assertIn(b"session debate is authoritative", debate.content)
        self.assertNotIn(b"control debate must be ignored", debate.content)
        self.assertEqual(
            OwnAgentStateSourceAdapter(self.workspace, state_scope).capture(
                f"agent-state/{PLOT_ARCHITECT}",
                CONTEXT_SOURCE_POLICY,
            ),
            state,
        )
        self.assertEqual(
            ActiveDebateSourceAdapter(
                self.workspace,
                debate_scope,
                "source_debate",
            ).capture("debate/source_debate", CONTEXT_SOURCE_POLICY),
            debate,
        )

    def test_session_sources_reject_root_replacement_before_later_and_during_read(
        self,
    ) -> None:
        self.append_source_state(self.workspace, "original session state")
        state_scope = ContextScope(
            "workspace",
            self.session_id,
            ContextSubjectKind.CONVERSATION,
            "conversation",
            PLOT_ARCHITECT,
        )
        locator = f"agent-state/{PLOT_ARCHITECT}"

        for read_before_replacement in (False, True):
            with self.subTest(read_before_replacement=read_before_replacement):
                adapter = OwnAgentStateSourceAdapter(
                    self.source_workspace(),
                    state_scope,
                )
                if read_before_replacement:
                    self.assertIn(
                        b"original session state",
                        adapter.capture(locator, CONTEXT_SOURCE_POLICY).content,
                    )
                with self.replace_session_worktree() as (_moved, replacement):
                    sentinel = replacement / "replacement-sentinel"
                    sentinel.write_bytes(b"replacement bytes must remain unread")
                    with self.assertRaises(ContextSourceError) as caught:
                        adapter.capture(locator, CONTEXT_SOURCE_POLICY)
                    self.assertIs(
                        caught.exception.code,
                        ContextSourceErrorCode.SOURCE_UNAVAILABLE,
                    )
                    self.assertNotIn("replacement bytes", repr(caught.exception))
                    self.assertEqual(
                        sentinel.read_bytes(),
                        b"replacement bytes must remain unread",
                    )

        sources = self.source_workspace()
        adapter = OwnAgentStateSourceAdapter(sources, state_scope)
        original_read_text = SessionSourceWorkspace._read_text_at
        replacement_context = self.replace_session_worktree()
        entered = False

        def replace_during_read(
            root,
            relative,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            nonlocal entered
            if not entered:
                entered = True
                _moved, replacement = replacement_context.__enter__()
                (replacement / "replacement-sentinel").write_bytes(
                    b"mid-read replacement bytes"
                )
            return original_read_text(
                root,
                relative,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_read_text_at",
                side_effect=replace_during_read,
            ), self.assertRaises(ContextSourceError) as caught:
                adapter.capture(locator, CONTEXT_SOURCE_POLICY)
            self.assertIs(
                caught.exception.code,
                ContextSourceErrorCode.SOURCE_UNAVAILABLE,
            )
        finally:
            if entered:
                replacement_context.__exit__(None, None, None)

    def test_session_sources_discard_bytes_when_ready_index_drifts_same_inode(
        self,
    ) -> None:
        self.append_source_state(self.workspace, "semantic drift sentinel")
        relative = self.workspace._agent_state_relative(
            PLOT_ARCHITECT,
            self.session_id,
            "source_debate",
        )
        identity = (
            os.stat(self.workspace.root).st_dev,
            os.stat(self.workspace.root).st_ino,
        )
        sources = self.source_workspace()

        def stage() -> None:
            subprocess.run(
                ["git", "add", "-f", "--", relative],
                cwd=self.workspace.root,
                check=True,
                capture_output=True,
            )

        def unstage() -> None:
            subprocess.run(
                ["git", "reset", "--", relative],
                cwd=self.workspace.root,
                check=True,
                capture_output=True,
            )

        stage()
        try:
            with self.assertRaises(SessionArtifactBindingError):
                sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        finally:
            unstage()

        original_read = SessionSourceWorkspace._read_text_at
        drifted = False

        def stage_during_read(
            root,
            path,
            *,
            missing_ok=False,
            max_bytes=workspace_core._SESSION_SOURCE_MAX_FILE_BYTES,
        ):
            nonlocal drifted
            if not drifted:
                drifted = True
                stage()
            return original_read(
                root,
                path,
                missing_ok=missing_ok,
                max_bytes=max_bytes,
            )

        try:
            with mock.patch.object(
                SessionSourceWorkspace,
                "_read_text_at",
                side_effect=stage_during_read,
            ), self.assertRaises(SessionArtifactBindingError):
                sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        finally:
            if drifted:
                unstage()
        self.assertEqual(
            (os.stat(self.workspace.root).st_dev, os.stat(self.workspace.root).st_ino),
            identity,
        )

    def test_binding_and_source_reads_close_descriptors_and_allow_core_deletion(
        self,
    ) -> None:
        self.workspace.path(_OUTLINE).unlink()
        opened: list[int] = []
        original_open = initialization_module._open_existing_root

        def record_open(root, expected):
            descriptor = original_open(root, expected)
            opened.append(descriptor)
            return descriptor

        with mock.patch.object(
            initialization_module,
            "_open_existing_root",
            side_effect=record_open,
        ):
            binding = compose_session_artifact_binding(
                self.control_workspace.root,
                self.session_id,
            )
        self.assertTrue(opened)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        opened.clear()

        self.append_source_state(self.workspace, "descriptor cleanup state")
        sources = SessionSourceWorkspace.from_artifact_binding(binding)
        source_descriptors: list[int] = []
        original_duplicate = (
            initialization_module.SessionArtifactAccess.duplicate_worktree_descriptor
        )

        def record_source_open(access):
            descriptor = original_duplicate(access)
            source_descriptors.append(descriptor)
            return descriptor

        with mock.patch.object(
            initialization_module.SessionArtifactAccess,
            "duplicate_worktree_descriptor",
            record_source_open,
        ):
            self.assertTrue(
                sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
            )
        self.assertTrue(source_descriptors)
        for descriptor in source_descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

        source_descriptors.clear()
        with mock.patch.object(
            initialization_module.SessionArtifactAccess,
            "duplicate_worktree_descriptor",
            record_source_open,
        ), mock.patch.object(
            SessionSourceWorkspace,
            "_read_text_at",
            side_effect=WorkspaceSecurityError("injected source failure"),
        ), self.assertRaises(WorkspaceSecurityError):
            sources.read_agent_session_state(PLOT_ARCHITECT, self.session_id)
        self.assertTrue(source_descriptors)
        for descriptor in source_descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_binding_repository_is_private_and_access_expires_at_exit(self) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        self.assertFalse(hasattr(binding, "repository"))
        with binding.open_ready_resources() as access:
            repository = access.repository
            self.assertEqual(
                repository.read_text(_OUTLINE),
                "Outline first line.\nOutline second line.\n",
            )
            duplicate = access.duplicate_worktree_descriptor()
            os.close(duplicate)
        for operation in (
            lambda: access.repository,
            lambda: access.resources,
            lambda: access.duplicate_worktree_descriptor(),
        ):
            with self.subTest(operation=operation), self.assertRaises(
                SessionArtifactBindingError
            ):
                operation()

    def test_outer_transaction_performs_only_pre_and_post_ready_inspections(
        self,
    ) -> None:
        self.append_source_state(self.workspace, "transaction state")
        self.append_source_debate(self.workspace, "transaction debate")
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        sources = SessionSourceWorkspace.from_artifact_binding(binding)
        original_ready = (
            initialization_module._require_ready_locked_git_session_resources
        )

        with mock.patch.object(
            initialization_module,
            "_require_ready_locked_git_session_resources",
            wraps=original_ready,
        ) as require_ready:
            with binding.open_ready_resources() as outer:
                repository = outer.repository
                with binding.open_ready_resources() as nested:
                    self.assertIs(nested, outer)
                    self.assertEqual(
                        repository.read_text(_OUTLINE),
                        "Outline first line.\nOutline second line.\n",
                    )
                    self.assertIn(_OUTLINE, repository.list_paths())
                    self.assertTrue(repository.capture_complete_state())
                    self.assertEqual(
                        len(
                            sources.read_agent_session_state(
                                PLOT_ARCHITECT,
                                self.session_id,
                            )
                        ),
                        1,
                    )
                    self.assertEqual(
                        len(
                            sources.inspect_debate(
                                self.session_id,
                                "source_debate",
                            )["messages"]
                        ),
                        1,
                    )
                self.assertEqual(require_ready.call_count, 1)
            self.assertEqual(require_ready.call_count, 2)

    def test_story_commit_borrow_runs_one_pre_and_post_validation(self) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        validations = 0

        def validate_ready():
            nonlocal validations
            validations += 1
            return binding.resources

        with binding.open_story_commit_artifact_repository(
            validate_ready
        ) as repository:
            self.assertEqual(validations, 1)
            self.assertEqual(
                repository.read_text(_OUTLINE),
                "Outline first line.\nOutline second line.\n",
            )
            self.assertIn(_OUTLINE, repository.list_paths())
            self.assertTrue(repository.capture_complete_state())
            self.assertEqual(validations, 1)
        self.assertEqual(validations, 2)

    def test_outer_transaction_semantic_drift_fails_before_or_after_body(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        drifted = replace(
            binding.resources,
            current_branch_tip="0" * len(binding.resources.current_branch_tip),
        )
        entered = False
        with mock.patch.object(
            initialization_module,
            "_require_ready_locked_git_session_resources",
            return_value=drifted,
        ) as require_ready, self.assertRaises(SessionArtifactBindingError):
            with binding.open_ready_resources():
                entered = True
        self.assertFalse(entered)
        self.assertEqual(require_ready.call_count, 1)

        ready_calls = 0
        read_completed = False

        def persistent_drift(*args, **kwargs):
            nonlocal ready_calls
            del args, kwargs
            ready_calls += 1
            return binding.resources if ready_calls == 1 else drifted

        def read_transaction():
            nonlocal read_completed
            with binding.open_ready_resources() as access:
                value = access.repository.read_text(_OUTLINE)
                read_completed = True
                return value

        with mock.patch.object(
            initialization_module,
            "_require_ready_locked_git_session_resources",
            side_effect=persistent_drift,
        ), self.assertRaises(SessionArtifactBindingError):
            read_transaction()
        self.assertTrue(read_completed)
        self.assertEqual(ready_calls, 2)

    def test_repository_operation_checks_named_root_liveness(self) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        root = self.session_resources.identity.worktree_root
        moved = root.with_name(f"{root.name}-temporarily-deleted")

        with binding.open_ready_resources() as access:
            repository = access.repository
            with self.replace_session_worktree(), self.assertRaises(
                SessionArtifactBindingError
            ):
                repository.read_text(_OUTLINE)
            self.assertEqual(
                repository.read_text(_OUTLINE),
                "Outline first line.\nOutline second line.\n",
            )

            root.rename(moved)
            try:
                with self.assertRaises(SessionArtifactBindingError):
                    repository.list_paths()
            finally:
                moved.rename(root)
            self.assertIn(_OUTLINE, repository.list_paths())

    def test_repository_operations_require_live_same_thread_nested_access(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        with binding.open_ready_resources() as outer:
            repository = outer.repository
            with binding.open_ready_resources() as nested:
                self.assertIs(nested, outer)
                self.assertEqual(
                    repository.read_text(_OUTLINE),
                    "Outline first line.\nOutline second line.\n",
                )

                def foreign_read() -> BaseException | None:
                    try:
                        repository.read_text(_OUTLINE)
                    except BaseException as exc:
                        return exc
                    return None

                with ThreadPoolExecutor(max_workers=1) as executor:
                    foreign = executor.submit(foreign_read).result()
                self.assertIs(type(foreign), SessionArtifactBindingError)

            self.assertEqual(
                repository.read_text(_OUTLINE),
                "Outline first line.\nOutline second line.\n",
            )
            with mock.patch.object(
                repository,
                "_ensure_source_directories",
                side_effect=RuntimeError("injected repository setup failure"),
            ), self.assertRaisesRegex(RuntimeError, "repository setup failure"):
                repository.read_text(_OUTLINE)
            self.assertEqual(
                repository.read_text(_OUTLINE),
                "Outline first line.\nOutline second line.\n",
            )

        for operation in (
            lambda: repository.read_text(_OUTLINE),
            repository.capture_complete_state,
            repository.list_paths,
        ):
            with self.subTest(operation=operation), self.assertRaises(
                SessionArtifactBindingError
            ):
                operation()

    def test_nested_access_requires_ready_before_entering_body(self) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        with binding.open_ready_resources() as outer:
            entered = False
            with self.replace_session_worktree():
                with self.assertRaises(SessionArtifactBindingError):
                    with binding.open_ready_resources():
                        entered = True
            self.assertFalse(entered)
            outer.require_ready()

    def test_foreign_thread_access_surface_fails_and_exit_revokes_every_token(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        manager = binding.open_ready_resources()
        access = manager.__enter__()
        descriptor = access._descriptor
        operations = (
            lambda: access.repository,
            lambda: access.resources,
            lambda: access.worktree_identity,
            access.require_active,
            access.require_ready,
            access.duplicate_worktree_descriptor,
            access._close,
        )

        def foreign_calls():
            failures = []
            for operation in operations:
                try:
                    operation()
                except SessionArtifactBindingError as exc:
                    failures.append(exc)
            return tuple(failures)

        with ThreadPoolExecutor(max_workers=1) as executor:
            failures = executor.submit(foreign_calls).result(timeout=10)
        self.assertEqual(len(failures), len(operations))
        os.fstat(descriptor)
        self.assertEqual(
            binding._operation_tokens,
            {access._owner_thread_token: [access]},
        )

        body_failure = RuntimeError("preserve artifact caller failure")

        def foreign_exit():
            try:
                manager.__exit__(RuntimeError, body_failure, None)
            except SessionArtifactBindingError as exc:
                return exc
            return None

        with ThreadPoolExecutor(max_workers=1) as executor:
            exit_failure = executor.submit(foreign_exit).result(timeout=10)
        self.assertIs(type(exit_failure), SessionArtifactBindingError)
        self.assertEqual(binding._operation_tokens, {})
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        with binding.open_ready_resources() as replacement:
            replacement.require_ready()

    def test_recycled_numeric_ident_cannot_discover_or_reuse_artifact_access(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        manager = binding.open_ready_resources()
        access = manager.__enter__()
        descriptor = access._descriptor
        creator_token = lock_module.current_thread_token()
        numeric_ident = threading.get_ident()
        successor_token = object()
        lock_module._THREAD_STATE.token = successor_token
        try:
            self.assertEqual(threading.get_ident(), numeric_ident)
            self.assertIsNone(binding._current_repository_access())
            self.assertEqual(
                binding._operation_tokens,
                {creator_token: [access]},
            )
            operations = (
                lambda: access.repository,
                lambda: access.resources,
                lambda: access.worktree_identity,
                access.duplicate_worktree_descriptor,
                access.require_active,
                access.require_ready,
                access._close,
            )
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(
                    SessionArtifactBindingError
                ):
                    operation()
            os.fstat(descriptor)
            with self.assertRaises(SessionArtifactBindingError):
                manager.__exit__(RuntimeError, RuntimeError("body failure"), None)
        finally:
            lock_module._THREAD_STATE.token = creator_token

        self.assertEqual(binding._operation_tokens, {})
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        with binding.open_ready_resources() as replacement:
            replacement.require_ready()

    def test_postcheck_binding_loss_dominates_body_failure_and_preserves_clean_exit(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        original_open = initialization_module._open_existing_root
        opened: list[int] = []

        def record_open(root, expected):
            descriptor = original_open(root, expected)
            opened.append(descriptor)
            return descriptor

        for body_failure in (
            None,
            RuntimeError("body failure"),
            asyncio.CancelledError(),
        ):
            with self.subTest(body_failure=type(body_failure).__name__):
                replacement = self.replace_session_worktree()
                entered = False
                access = None
                try:
                    with mock.patch.object(
                        initialization_module,
                        "_open_existing_root",
                        side_effect=record_open,
                    ), self.assertRaises(SessionArtifactBindingError):
                        with binding.open_ready_resources() as access:
                            replacement.__enter__()
                            entered = True
                            if body_failure is not None:
                                raise body_failure
                finally:
                    if entered:
                        replacement.__exit__(None, None, None)
                assert access is not None
                with self.assertRaises(SessionArtifactBindingError):
                    _ = access.repository

        for body_failure in (
            RuntimeError("clean-root body failure"),
            asyncio.CancelledError(),
        ):
            with self.subTest(clean_root=type(body_failure).__name__):
                access = None
                with self.assertRaises(type(body_failure)) as caught:
                    with binding.open_ready_resources() as access:
                        raise body_failure
                self.assertIs(caught.exception, body_failure)
                assert access is not None
                with self.assertRaises(SessionArtifactBindingError):
                    _ = access.repository
        self.assertTrue(opened)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_nested_and_story_commit_postchecks_preserve_exception_precedence(
        self,
    ) -> None:
        for holder in ("nested_ready", "story_commit"):
            for body_failure in (
                RuntimeError(f"{holder} body failure"),
                asyncio.CancelledError(),
            ):
                with self.subTest(holder=holder, failure=type(body_failure).__name__):
                    binding = compose_session_artifact_binding(
                        self.control_workspace.root,
                        self.session_id,
                    )
                    opened: list[int] = []
                    original_open = initialization_module._open_existing_root

                    def record_open(root, expected):
                        descriptor = original_open(root, expected)
                        opened.append(descriptor)
                        return descriptor

                    if holder == "nested_ready":
                        with binding.open_ready_resources() as outer:
                            replacement = self.replace_session_worktree()
                            entered = False
                            try:
                                replacement.__enter__()
                                entered = True
                                with self.assertRaises(
                                    SessionArtifactBindingError
                                ):
                                    with binding.open_ready_resources() as nested:
                                        self.assertIs(nested, outer)
                                        raise body_failure
                            finally:
                                if entered:
                                    replacement.__exit__(None, None, None)
                            outer.require_ready()
                    else:
                        replacement = self.replace_session_worktree()
                        entered = False
                        repository = None
                        try:
                            with mock.patch.object(
                                initialization_module,
                                "_open_existing_root",
                                side_effect=record_open,
                            ), self.assertRaises(SessionArtifactBindingError):
                                with binding.open_story_commit_artifact_repository(
                                    lambda: binding.resources
                                ) as repository:
                                    replacement.__enter__()
                                    entered = True
                                    raise body_failure
                        finally:
                            if entered:
                                replacement.__exit__(None, None, None)
                        assert repository is not None
                        with self.assertRaises(SessionArtifactBindingError):
                            repository.list_paths()
                        self.assertTrue(opened)
                        for descriptor in opened:
                            with self.assertRaises(OSError):
                                os.fstat(descriptor)
                    self.assertEqual(binding._operation_tokens, {})

        for holder in ("nested_ready", "story_commit"):
            for body_failure in (
                RuntimeError(f"clean {holder} body failure"),
                asyncio.CancelledError(),
            ):
                with self.subTest(clean_holder=holder, failure=type(body_failure).__name__):
                    binding = compose_session_artifact_binding(
                        self.control_workspace.root,
                        self.session_id,
                    )

                    @contextmanager
                    def holder_access():
                        if holder == "nested_ready":
                            with binding.open_ready_resources():
                                with binding.open_ready_resources():
                                    yield
                        else:
                            with binding.open_story_commit_artifact_repository(
                                lambda: binding.resources
                            ):
                                yield

                    observed = None
                    observed_traceback = None
                    original_traceback = None
                    try:
                        raise body_failure
                    except BaseException as seeded:
                        original_traceback = seeded.__traceback__
                        try:
                            with holder_access():
                                raise
                        except BaseException as caught:
                            observed = caught
                            observed_traceback = caught.__traceback__
                    self.assertIs(observed, body_failure)
                    self.assertIsNotNone(original_traceback)
                    self.assertIsNotNone(observed_traceback)
                    chain = []
                    while observed_traceback is not None:
                        chain.append(observed_traceback)
                        observed_traceback = observed_traceback.tb_next
                    self.assertIn(original_traceback, chain)
                    self.assertEqual(binding._operation_tokens, {})

    async def test_guarded_reference_binding_loss_maps_to_recovery_before_provider(
        self,
    ) -> None:
        conversation = self.conversation("Guarded reference binding loss")
        failure = SessionArtifactBindingError("exact guarded marker")
        before = (
            len(self.backend.calls),
            len(self.backend.reference_calls),
            len(self.backend.providers),
            len(self.backend.definitions),
        )
        with mock.patch.object(
            orchestrator_module,
            "prepare_registered_guarded_runtime_reference_operation",
            side_effect=failure,
        ) as prepare:
            result = await self.runtime.lead_turn(
                self.session_id,
                conversation,
                f"Read ${{{_OUTLINE}}} before answering.",
            )
        self.assertIs(result.state, CollaborationStepState.RECOVERY_REQUIRED)
        self.assertIs(
            result.next_action,
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
        )
        self.assertEqual(
            (
                len(self.backend.calls),
                len(self.backend.reference_calls),
                len(self.backend.providers),
                len(self.backend.definitions),
            ),
            before,
        )
        prepare.assert_called_once()

    async def test_two_ready_sessions_read_and_inspect_only_their_own_ledger(
        self,
    ) -> None:
        self.workspace.path(_OUTLINE).write_text(
            "First-session outline.\n",
            encoding="utf-8",
        )
        other = provision_session_resources(
            self.control_workspace.root,
            "other_session",
        )
        other_source = other.identity.worktree_root / _OUTLINE
        other_source.write_text("Other-session outline.\n", encoding="utf-8")
        before = self.control_snapshot()
        operation_ids: dict[str, str] = {}

        for session_id, expected in (
            (self.session_id, "First-session outline.\n"),
            ("other_session", "Other-session outline.\n"),
            (self.session_id, "First-session outline.\n"),
        ):
            with self.subTest(session_id=session_id):
                conversation = (
                    self.runtime.conversation_storage_services.catalog.create(
                        session_id,
                        f"Reference {session_id}",
                    ).conversation_id
                )
                self.backend.enqueue(_visible_outcome("answer"))
                await self.runtime.session(session_id).lead_turn(
                    conversation,
                    f"Read ${{{_OUTLINE}}} before answering.",
                )
                self.assertEqual(
                    self.backend.reference_contexts[-1],
                    ({"ordinal": 1, "content": expected},),
                )
                operation_id = self.backend.reference_calls[-1].reference_operation_id
                operation_ids[session_id] = operation_id
                history = self.runtime.session(
                    session_id
                ).inspect_reference_operation(operation_id)
                self.assertIsNotNone(history)
                assert history is not None
                self.assertEqual(history.manifest.operation_id, operation_id)

        for owner, foreign in (
            (self.session_id, operation_ids["other_session"]),
            ("other_session", operation_ids[self.session_id]),
        ):
            with self.subTest(owner=owner):
                with self.assertRaisesRegex(ValueError, "does not belong"):
                    self.runtime.session(owner).inspect_reference_operation(foreign)

        self.assertEqual(self.control_snapshot(), before)
        self.assertEqual(other_source.read_text(encoding="utf-8"), "Other-session outline.\n")

    async def test_absent_session_fails_before_provider_io_without_provisioning(
        self,
    ) -> None:
        session_id = "absent_session"
        bound = self.runtime.session(session_id)
        conversation = self.runtime.conversation_storage_services.catalog.create(
            session_id,
            "Absent artifact resources",
        ).conversation_id
        calls_before = (
            len(self.backend.calls),
            len(self.backend.reference_calls),
        )
        resources_before = inspect_session_resources(
            self.control_workspace.root,
            session_id,
        )
        control_before = self.control_snapshot()
        self.backend.enqueue(_visible_outcome("answer"))

        blocked = await bound.lead_turn(
            conversation,
            f"Read ${{{_OUTLINE}}} before answering.",
        )
        self.assertIs(blocked.state, CollaborationStepState.RECOVERY_REQUIRED)
        self.assertIs(
            blocked.next_action,
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
        )
        self.assertIsNone(blocked.proposal)
        self.assertIsNone(blocked.artifact_proposal)

        self.assertEqual(
            (len(self.backend.calls), len(self.backend.reference_calls)),
            calls_before,
        )
        self.assertEqual(
            inspect_session_resources(self.control_workspace.root, session_id),
            resources_before,
        )
        self.assertEqual(self.control_snapshot(), control_before)

    def test_bound_repository_rejects_physical_root_replacement_before_and_after_io(
        self,
    ) -> None:
        sibling = provision_session_resources(
            self.control_workspace.root,
            "sibling_session",
        )
        sibling_source = sibling.identity.worktree_root / _OUTLINE
        sibling_source.write_text("Sibling-session outline.\n", encoding="utf-8")
        main_before = self.control_snapshot()
        sibling_before = sibling_source.read_bytes()

        for read_before_replacement in (False, True):
            with self.subTest(read_before_replacement=read_before_replacement):
                binding = compose_session_artifact_binding(
                    self.control_workspace.root,
                    self.session_id,
                )
                if read_before_replacement:
                    with binding.open_ready_resources() as access:
                        self.assertEqual(
                            access.repository.read_text(_OUTLINE),
                            "Outline first line.\nOutline second line.\n",
                        )
                with self.replace_session_worktree() as (moved, replacement):
                    replacement_source = replacement / _OUTLINE
                    replacement_source.parent.mkdir(parents=True)
                    replacement_source.write_text(
                        "Replacement must remain unread.\n",
                        encoding="utf-8",
                    )
                    moved_before = (moved / _OUTLINE).read_bytes()

                    with self.assertRaises(SessionArtifactBindingError):
                        with binding.open_ready_resources() as access:
                            access.repository.read_text(_OUTLINE)

                    self.assertEqual(
                        replacement_source.read_text(encoding="utf-8"),
                        "Replacement must remain unread.\n",
                    )
                    self.assertEqual((moved / _OUTLINE).read_bytes(), moved_before)
                    self.assertEqual(sibling_source.read_bytes(), sibling_before)
                    self.assertEqual(self.control_snapshot(), main_before)


if __name__ == "__main__":
    import unittest

    unittest.main()
