"""Workspace defaults, lifecycle automation, and run-input tests."""

from __future__ import annotations

import contextlib
import errno
import inspect
import io
import json
import os
import stat
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from story_writing_agents import (
    AgentReply,
    BackendProtocolError,
    Phase,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    Workspace,
)
from story_writing_agents import cli as cli_module
from story_writing_agents.cli_context import (
    CliContext,
    load_cli_context,
    load_default_conversation,
    resolve_session,
    resolve_workspace,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.errors import (
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.run_inputs import read_draft_file
from story_writing_agents.session_recovery import (
    SessionRecoveryFailureCode,
    SessionRecoveryNextAction,
    SessionRecoveryOwner,
    SessionRecoveryResult,
    SessionRecoveryState,
)
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionAssessmentState,
    SessionTransitionNextAction,
    SessionTransitionOperation,
    SessionTransitionScope,
)
from story_writing_agents.workspace import initialization as workspace_initialization

from tests.helpers import ScriptedBackend, pristine_initialized_workspace


CLI_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
CONVERSATION_DEFAULT_DIRECTORY = "agent_states/orchestrator_state/conversation_defaults"
V1_CONVERSATION_DIRECTORY = "agent_states/orchestrator_state/conversation_selections"
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"
_LEGACY_WORKSPACE_SENTINELS = (
    "config/system_defaults.json",
    "config/agent_configs.json",
    "config/persona_templates/default_kim_eun_hee.md",
    "config/agents/lead_orchestrator/AGENTS.md",
)


def conversation_default_path(workspace: Workspace, session_id: str) -> Path:
    """Return one safe test path for a V2 conversation default."""

    return workspace.root / CONVERSATION_DEFAULT_DIRECTORY / f"{session_id}.json"


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture file bytes, modes, and timestamps for purity assertions."""

    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def boundary_snapshot(
    root: Path,
) -> dict[str, tuple[int, int, int, bytes | str | None]]:
    """Capture a boundary namespace without following symbolic links."""

    snapshot: dict[str, tuple[int, int, int, bytes | str | None]] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            payload: bytes | str | None = os.readlink(path)
        elif stat.S_ISREG(metadata.st_mode):
            payload = path.read_bytes()
        else:
            payload = None
        snapshot[path.relative_to(root).as_posix()] = (
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            payload,
        )
    return snapshot


def invoke_cli(arguments: list[str]) -> tuple[int, str, str]:
    """Invoke the CLI in process and capture its public streams."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli_module.main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@contextlib.contextmanager
def working_directory(path: Path) -> Iterator[None]:
    """Temporarily switch the caller directory and always restore it."""

    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def successful_backend() -> ScriptedBackend:
    """Return a provider-free backend that reaches immediate consensus."""

    def respond(request):
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, "neutral candidate")
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")

    return ScriptedBackend(respond)


class LifecycleCommandBackend:
    """Provide correlated lifecycle facts for CLI automation tests."""

    def __init__(
        self,
        *_args: Any,
        fail_at: str | None = None,
        assessment: SessionTransitionAssessment | None = None,
        **_kwargs: Any,
    ) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, str | None]] = []
        self.assessment_calls: list[dict[str, object]] = []
        self.assessment = assessment
        self._states: dict[str, SessionLifecycleState] = {}

    @staticmethod
    def state(
        session_id: str,
        *,
        durable: SessionDurableState,
        access: SessionAccessState,
    ) -> SessionLifecycleState:
        """Build one exact lifecycle command result."""

        absent = durable is SessionDurableState.ABSENT
        held = access is not SessionAccessState.CLAIMABLE
        return SessionLifecycleState(
            session_id=session_id,
            durable_state=durable,
            access_state=access,
            owner_hostname="test-host" if held else None,
            owner_pid=123 if held else None,
            created_at=None if absent else _CREATED,
            updated_at=None if absent else _UPDATED,
        )

    def seed(
        self,
        session_id: str,
        *,
        durable: SessionDurableState,
        access: SessionAccessState = SessionAccessState.CLAIMABLE,
    ) -> None:
        """Seed one lifecycle fact without adding an observed call."""

        self._states[session_id] = self.state(
            session_id,
            durable=durable,
            access=access,
        )

    def _record(self, method: str, session_id: str) -> None:
        self.calls.append((method, session_id))
        if self.fail_at == method:
            raise BackendProtocolError(f"{method} failed")

    def validate_provider_configuration(self, session_id: str) -> bool:
        self._record("validate", session_id)
        return True

    def provision_session_resources(self, session_id: str) -> object:
        self._record("provision", session_id)
        return object()

    def create_session_with_access(self, session_id: str) -> SessionLifecycleState:
        self._record("create", session_id)
        state = self.state(
            session_id,
            durable=SessionDurableState.READY,
            access=SessionAccessState.HELD_HERE,
        )
        self._states[session_id] = state
        return state

    def reopen_session_with_access(self, session_id: str) -> SessionLifecycleState:
        self._record("reopen", session_id)
        state = self.state(
            session_id,
            durable=SessionDurableState.READY,
            access=SessionAccessState.HELD_HERE,
        )
        self._states[session_id] = state
        return state

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        self._record("acquire", session_id)
        state = self.state(
            session_id,
            durable=SessionDurableState.READY,
            access=SessionAccessState.HELD_HERE,
        )
        self._states[session_id] = state
        return state

    def pause_session_and_release_access(
        self, session_id: str
    ) -> SessionLifecycleState:
        self._record("pause", session_id)
        state = self.state(
            session_id,
            durable=SessionDurableState.PAUSED,
            access=SessionAccessState.CLAIMABLE,
        )
        self._states[session_id] = state
        return state

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        self._record("get", session_id)
        return self._states.get(
            session_id,
            self.state(
                session_id,
                durable=SessionDurableState.ABSENT,
                access=SessionAccessState.CLAIMABLE,
            ),
        )

    def assess_session_transition(
        self,
        *,
        lifecycle_state: SessionLifecycleState,
        requested_operation: SessionTransitionOperation,
        conversation_id: str | None = None,
        collaboration_store: object | None = None,
    ) -> SessionTransitionAssessment:
        self._record("assess", lifecycle_state.session_id)
        self.assessment_calls.append(
            {
                "lifecycle_state": lifecycle_state,
                "requested_operation": requested_operation,
                "conversation_id": conversation_id,
                "collaboration_store": collaboration_store,
            }
        )
        if self.assessment is not None:
            return self.assessment
        return SessionTransitionAssessment(
            session_id=lifecycle_state.session_id,
            conversation_id=None,
            scope=SessionTransitionScope.IDLE,
            state=SessionTransitionAssessmentState.IDLE_READY,
            next_action=SessionTransitionNextAction.BEGIN_WITH_LEAD,
            owner=None,
            failure_code=SessionRecoveryFailureCode.NONE,
            allowed_operations=tuple(SessionTransitionOperation),
            recovery_result=None,
        )

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        self._record("release", session_id)
        durable = self._states[session_id].durable_state
        state = self.state(
            session_id, durable=durable, access=SessionAccessState.CLAIMABLE
        )
        self._states[session_id] = state
        return state

    def close(self) -> None:
        self.calls.append(("close", None))


def active_transition_assessment(
    session_id: str,
    conversation_id: str,
    state: SessionRecoveryState,
) -> SessionTransitionAssessment:
    """Build one exact active assessment for lifecycle CLI tests."""

    next_action = {
        SessionRecoveryState.AWAITING_APPLICATION: (
            SessionRecoveryNextAction.SUBMIT_APPLICATION_DECISION
        ),
        SessionRecoveryState.STAGED: SessionRecoveryNextAction.INSPECT_STORY_COMMIT,
    }[state]
    values: dict[str, object] = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "collaboration_tail_sequence": 9,
        "state": state,
        "next_action": next_action,
        "owner": (
            SessionRecoveryOwner.STORY_COMMIT
            if state is SessionRecoveryState.STAGED
            else SessionRecoveryOwner.LEAD
        ),
        "failure_code": SessionRecoveryFailureCode.NONE,
        "proposal_id": "proposal-9",
        "correlation_id": "correlation-9",
    }
    if state is SessionRecoveryState.AWAITING_APPLICATION:
        values["confirmation_request_id"] = "R9"
        operations = tuple(SessionTransitionOperation)
    else:
        values.update(
            application_id="application-9",
            story_commit_candidate_id="candidate-9",
            story_commit_operation_id="operation-9",
        )
        operations = (SessionTransitionOperation.ENTER_DESTINATION,)
    recovery = SessionRecoveryResult(**values)  # type: ignore[arg-type]
    return SessionTransitionAssessment(
        session_id=session_id,
        conversation_id=conversation_id,
        scope=SessionTransitionScope.ACTIVE,
        state=SessionTransitionAssessmentState.ACTIVE,
        next_action=SessionTransitionNextAction.FOLLOW_ACTIVE_RECOVERY,
        owner=None,
        failure_code=SessionRecoveryFailureCode.NONE,
        allowed_operations=operations,
        recovery_result=recovery,
    )


class WorkspaceDiscoveryTests(unittest.TestCase):
    """Verify explicit and nearest-ancestor workspace resolution."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_nearest_strict_workspace_and_explicit_precedence(self) -> None:
        outer = pristine_initialized_workspace(self)
        nested = outer.root / "core_documents"
        self.assertEqual(resolve_workspace(None, start=nested).root, outer.root)
        self.assertEqual(
            resolve_workspace(str(outer.root), start=nested).root, outer.root
        )

        unsupported = outer.root / "manuscript" / "unsupported"
        (unsupported / "config").mkdir(parents=True)
        (unsupported / "config/workspace.json").write_text(
            '{"schema_version":1}\n',
            encoding="utf-8",
        )
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            resolve_workspace(None, start=unsupported)

    def test_dangling_exact_legacy_sentinel_is_nearest_unsafe_boundary(self) -> None:
        outer = pristine_initialized_workspace(self)
        self.assertEqual(
            workspace_initialization._LEGACY_SENTINELS,
            _LEGACY_WORKSPACE_SENTINELS,
        )

        for index, relative in enumerate(_LEGACY_WORKSPACE_SENTINELS):
            with self.subTest(relative=relative):
                inner = outer.root / "manuscript" / f"legacy-boundary-{index}"
                child = inner / "nested" / "child"
                child.mkdir(parents=True)
                sentinel = inner / relative
                sentinel.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sentinel.symlink_to("missing-legacy-target")
                except NotImplementedError:
                    self.skipTest("symbolic links are unavailable")
                except OSError as exc:
                    unsupported = {
                        errno.EACCES,
                        errno.EPERM,
                        getattr(errno, "ENOTSUP", errno.EPERM),
                        getattr(errno, "EOPNOTSUPP", errno.EPERM),
                    }
                    if exc.errno not in unsupported:
                        raise
                    self.skipTest("symbolic links are unavailable")

                before = boundary_snapshot(inner)
                discovered = workspace_initialization.discover_workspace_root(child)
                self.assertEqual(discovered, inner)
                self.assertNotEqual(discovered, outer.root)
                with self.assertRaises(WorkspaceSecurityError) as raised:
                    resolve_workspace(None, start=child)
                self.assertEqual(
                    str(raised.exception),
                    "workspace contains an unsafe entry",
                )
                self.assertEqual(boundary_snapshot(inner), before)

    def test_uninitialized_and_absent_discovery_are_actionable(self) -> None:
        uninitialized = self.base / "plain"
        uninitialized.mkdir()
        with self.assertRaisesRegex(WorkspaceFormatError, "workspace is not ready"):
            resolve_workspace(str(uninitialized), start=uninitialized)
        with self.assertRaisesRegex(
            ConfigurationError, "no initialized story workspace"
        ):
            resolve_workspace(None, start=uninitialized)


class CliDefaultPersistenceTests(unittest.TestCase):
    """Verify strict V2 default schemas and clean-break rejection."""

    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)

    def test_default_session_missing_save_load_and_resolve_are_exact(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(CliContext).parameters), ("default_session_id",)
        )
        self.assertEqual(
            tuple(inspect.signature(save_default_session).parameters),
            ("workspace", "session_id"),
        )
        self.assertEqual(
            tuple(inspect.signature(resolve_session).parameters),
            ("workspace", "context", "option_session"),
        )
        for public_callable in (
            load_cli_context,
            save_default_session,
            load_default_conversation,
            save_default_conversation,
            resolve_session,
        ):
            with self.subTest(public_callable=public_callable.__name__):
                self.assertTrue(inspect.getdoc(public_callable))
        before = tree_snapshot(self.workspace.root)
        self.assertEqual(load_cli_context(self.workspace), CliContext())
        self.assertEqual(tree_snapshot(self.workspace.root), before)
        saved = save_default_session(self.workspace, "story_session")
        self.assertEqual(saved, CliContext(default_session_id="story_session"))
        self.assertEqual(
            self.workspace.read_json(CLI_CONTEXT_PATH),
            {"schema_version": 2, "default_session_id": "story_session"},
        )
        context = load_cli_context(self.workspace)
        self.assertEqual(
            resolve_session(
                workspace=self.workspace, context=context, option_session=None
            ),
            "story_session",
        )
        self.assertEqual(
            resolve_session(
                workspace=self.workspace, context=context, option_session="explicit"
            ),
            "explicit",
        )
        self.assertEqual(load_cli_context(self.workspace), context)
        with self.assertRaisesRegex(ConfigurationError, "no default session"):
            resolve_session(
                workspace=self.workspace, context=CliContext(), option_session=None
            )

    def test_read_time_disappearance_is_absent_but_other_io_propagates(self) -> None:
        save_default_conversation(self.workspace, "chapter", "conversation")
        target = conversation_default_path(self.workspace, "chapter")
        original_read_text = self.workspace.read_text

        def disappear_then_read(relative: str | os.PathLike[str]) -> str:
            """Remove the target immediately before the guarded read."""

            target.unlink()
            return original_read_text(relative)

        with mock.patch.object(
            self.workspace,
            "read_text",
            side_effect=disappear_then_read,
        ) as read_text:
            self.assertIsNone(load_default_conversation(self.workspace, "chapter"))
        read_text.assert_called_once_with(
            f"{CONVERSATION_DEFAULT_DIRECTORY}/chapter.json"
        )
        self.assertFalse(target.exists())

        save_default_conversation(self.workspace, "chapter", "conversation")
        before = (target.read_bytes(), target.stat().st_mtime_ns)

        def deny_read(_relative: str | os.PathLike[str]) -> str:
            """Expose a wrapped non-absence I/O failure to the loader."""

            try:
                raise PermissionError("read-secret")
            except PermissionError as exc:
                raise WorkspaceFormatError("cannot read default") from exc

        with mock.patch.object(
            self.workspace,
            "read_text",
            side_effect=deny_read,
        ):
            with self.assertRaises(WorkspaceFormatError) as raised:
                load_default_conversation(self.workspace, "chapter")
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_default_session_schema_is_strict_and_v1_is_unchanged(self) -> None:
        valid = {"schema_version": 2, "default_session_id": "story_session"}
        rows = (
            ([], WorkspaceFormatError),
            ({"schema_version": 2}, WorkspaceFormatError),
            ({**valid, "extra": True}, WorkspaceFormatError),
            ({**valid, "schema_version": True}, WorkspaceFormatError),
            ({**valid, "schema_version": 1}, WorkspaceVersionUnsupportedError),
            ({**valid, "default_session_id": None}, WorkspaceFormatError),
            ({**valid, "default_session_id": "../escape"}, WorkspaceFormatError),
        )
        target = self.workspace.path(CLI_CONTEXT_PATH)
        for index, (value, exception) in enumerate(rows):
            with self.subTest(index=index):
                self.workspace.write_json(CLI_CONTEXT_PATH, value)
                before = (target.read_bytes(), target.stat().st_mtime_ns)
                with self.assertRaises(exception):
                    load_cli_context(self.workspace)
                self.assertEqual(
                    (target.read_bytes(), target.stat().st_mtime_ns), before
                )
        self.workspace.write_text(
            CLI_CONTEXT_PATH,
            '{"schema_version":2,"schema_version":2,"default_session_id":"story"}',
        )
        with self.assertRaises(WorkspaceFormatError):
            load_cli_context(self.workspace)

    def test_conversation_defaults_are_independent_exact_v2_records(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(load_default_conversation).parameters),
            ("workspace", "session_id"),
        )
        self.assertEqual(
            tuple(inspect.signature(save_default_conversation).parameters),
            ("workspace", "session_id", "default_conversation_id"),
        )
        before = tree_snapshot(self.workspace.root)
        self.assertIsNone(load_default_conversation(self.workspace, "chapter_one"))
        self.assertEqual(tree_snapshot(self.workspace.root), before)
        save_default_session(self.workspace, "story_session")
        context_before = self.workspace.path(CLI_CONTEXT_PATH).read_bytes()
        values = {"chapter_one": "conversation_one", "chapter_two": "conversation_two"}
        for session, conversation in values.items():
            save_default_conversation(self.workspace, session, conversation)
            self.assertEqual(
                load_default_conversation(self.workspace, session), conversation
            )
            self.assertEqual(
                self.workspace.read_json(
                    f"{CONVERSATION_DEFAULT_DIRECTORY}/{session}.json"
                ),
                {
                    "schema_version": 2,
                    "session_id": session,
                    "default_conversation_id": conversation,
                },
            )
        save_default_conversation(self.workspace, "chapter_one", None)
        self.assertIsNone(load_default_conversation(self.workspace, "chapter_one"))
        self.assertEqual(
            load_default_conversation(self.workspace, "chapter_two"), "conversation_two"
        )
        self.assertEqual(
            self.workspace.path(CLI_CONTEXT_PATH).read_bytes(), context_before
        )

    def test_conversation_default_schema_and_v1_artifact_fail_unchanged(self) -> None:
        valid = {
            "schema_version": 2,
            "session_id": "chapter_one",
            "default_conversation_id": "conversation_one",
        }
        target = conversation_default_path(self.workspace, "chapter_one")
        rows = (
            ([], WorkspaceFormatError),
            ({**valid, "schema_version": 1}, WorkspaceVersionUnsupportedError),
            ({**valid, "schema_version": True}, WorkspaceFormatError),
            ({**valid, "session_id": "chapter_two"}, WorkspaceFormatError),
            ({**valid, "default_conversation_id": 7}, WorkspaceFormatError),
            ({**valid, "extra": True}, WorkspaceFormatError),
        )
        for index, (value, exception) in enumerate(rows):
            self.workspace.write_json(
                f"{CONVERSATION_DEFAULT_DIRECTORY}/chapter_one.json",
                value,
            )
            before = (target.read_bytes(), target.stat().st_mtime_ns)
            with self.subTest(index=index), self.assertRaises(exception):
                load_default_conversation(self.workspace, "chapter_one")
            self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

        target.unlink()
        v1 = self.workspace.write_json(
            f"{V1_CONVERSATION_DIRECTORY}/chapter_one.json",
            {"schema_version": 1},
        )
        before = (v1.read_bytes(), v1.stat().st_mtime_ns)
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            load_default_conversation(self.workspace, "chapter_one")
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            save_default_conversation(self.workspace, "chapter_one", "conversation")
        self.assertEqual((v1.read_bytes(), v1.stat().st_mtime_ns), before)

    def test_malformed_and_non_utf8_defaults_reject_without_rewriting(self) -> None:
        targets = (
            (self.workspace.path(CLI_CONTEXT_PATH), load_cli_context),
            (
                conversation_default_path(self.workspace, "chapter"),
                lambda _workspace: load_default_conversation(
                    self.workspace,
                    "chapter",
                ),
            ),
        )
        for target, loader in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            for label, content in (
                ("malformed", b"{not-json}\n"),
                ("non_utf8", b"\xff\n"),
            ):
                with self.subTest(target=target.name, content=label):
                    target.write_bytes(content)
                    before = (target.read_bytes(), target.stat().st_mtime_ns)
                    with self.assertRaises(WorkspaceFormatError):
                        loader(self.workspace)
                    self.assertEqual(
                        (target.read_bytes(), target.stat().st_mtime_ns),
                        before,
                    )

    def test_identifier_symlink_and_atomic_write_failures_are_safe(self) -> None:
        for operation in (
            lambda: save_default_session(self.workspace, "../session"),
            lambda: load_default_conversation(self.workspace, "../session"),
            lambda: save_default_conversation(
                self.workspace, "session", "../conversation"
            ),
        ):
            with self.assertRaises(SessionIdError):
                operation()

        save_default_conversation(self.workspace, "chapter", "first")
        target = conversation_default_path(self.workspace, "chapter")
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        with mock.patch(
            "story_writing_agents.workspace.core.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaises(OSError):
                save_default_conversation(self.workspace, "chapter", "second")
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

        second = pristine_initialized_workspace(self)
        external = second.root.parent / "external"
        external.mkdir()
        directory = second.root / CONVERSATION_DEFAULT_DIRECTORY
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        try:
            directory.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        with self.assertRaises(WorkspaceSecurityError):
            save_default_conversation(second, "chapter", "conversation")

    def test_symlinked_default_directory_and_file_are_rejected_safely(self) -> None:
        external = self.workspace.root.parent / "external-defaults"
        external.mkdir()
        directory = self.workspace.root / CONVERSATION_DEFAULT_DIRECTORY
        if directory.exists():
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
        try:
            directory.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        for operation in (
            lambda: load_default_conversation(self.workspace, "chapter"),
            lambda: save_default_conversation(
                self.workspace,
                "chapter",
                "conversation",
            ),
        ):
            with (
                self.subTest(kind="directory"),
                self.assertRaises(WorkspaceSecurityError),
            ):
                operation()

        second = pristine_initialized_workspace(self)
        target = conversation_default_path(second, "chapter")
        target.parent.mkdir(parents=True, exist_ok=True)
        external_file = second.root.parent / "outside-default.json"
        external_file.write_text("{}\n", encoding="utf-8")
        target.symlink_to(external_file)
        for operation in (
            lambda: load_default_conversation(second, "chapter"),
            lambda: save_default_conversation(second, "chapter", "conversation"),
        ):
            with self.subTest(kind="file"), self.assertRaises(WorkspaceSecurityError):
                operation()
        self.assertEqual(external_file.read_text(encoding="utf-8"), "{}\n")


class RunInputModuleTests(unittest.TestCase):
    """Preserve the direct draft loader independently of retired CLI execution."""

    def test_shared_draft_loader_preserves_full_path_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            caller = Path(temporary)
            exact = "\ufefffirst\r\nsecond\x00  \n"
            relative = caller / "neutral.md"
            relative.write_bytes(exact.encode("utf-8"))
            absolute = caller / "absolute.md"
            absolute.write_text("absolute draft", encoding="utf-8")
            metachar = caller / 'literal ;$(touch marker) [*] "quoted".md'
            metachar.write_text("literal metacharacters", encoding="utf-8")
            empty = caller / "empty.md"
            empty.write_bytes(b"")
            linked = caller / "linked.md"
            try:
                linked.symlink_to(absolute)
            except (OSError, NotImplementedError):
                linked = None

            before = tree_snapshot(caller)
            with working_directory(caller):
                self.assertEqual(read_draft_file("neutral.md"), exact)
                self.assertEqual(read_draft_file(absolute), "absolute draft")
                self.assertEqual(
                    read_draft_file(metachar.name),
                    "literal metacharacters",
                )
                self.assertEqual(read_draft_file(empty), "")
                if linked is not None:
                    self.assertEqual(read_draft_file(linked.name), "absolute draft")
            self.assertEqual(tree_snapshot(caller), before)
            self.assertFalse((caller / "marker").exists())

    def test_draft_loader_errors_are_typed_and_provider_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = root / "invalid.md"
            invalid.write_bytes(b"\xff")
            missing = root / "missing.md"
            with mock.patch.object(cli_module, "AgentBackend") as backend_class:
                with self.assertRaisesRegex(ConfigurationError, "not valid UTF-8"):
                    read_draft_file(invalid)
                with self.assertRaisesRegex(ConfigurationError, "cannot read draft"):
                    read_draft_file(missing)
            backend_class.assert_not_called()


class LifecycleAutomationTests(unittest.TestCase):
    """Verify exact create/reopen/pause grammar, output, and defaults."""

    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        self.enterContext(
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                return_value=self.workspace,
            )
        )

    def invoke_with(
        self, backend: LifecycleCommandBackend, *arguments: str
    ) -> tuple[int, str, str]:
        """Invoke one lifecycle command with a deterministic backend."""

        with mock.patch.object(cli_module, "AgentBackend", return_value=backend):
            return invoke_cli(
                ["session", *arguments, "--workspace", str(self.workspace.root)]
            )

    def test_create_requires_target_saves_default_after_success_and_formats_json(
        self,
    ) -> None:
        backend = LifecycleCommandBackend()
        code, stdout, stderr = self.invoke_with(backend, "create", "created")
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(
            (payload["session_id"], payload["durable_state"], payload["access_state"]),
            ("created", "ready", "claimable"),
        )
        self.assertEqual(
            payload["assessment"],
            {
                "allowed_operations": [
                    "enter_destination",
                    "leave_source",
                    "pause_session",
                ],
                "conversation_id": None,
                "failure_code": "none",
                "next_action": "begin_with_lead",
                "owner": None,
                "recovery_result": None,
                "scope": "idle",
                "session_id": "created",
                "state": "idle_ready",
            },
        )
        self.assertEqual(
            backend.calls,
            [
                ("get", "created"),
                ("validate", "created"),
                ("provision", "created"),
                ("create", "created"),
                ("get", "created"),
                ("assess", "created"),
                ("release", "created"),
                ("close", None),
            ],
        )
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "created")

    def test_reopen_explicit_and_default_save_only_after_success(self) -> None:
        save_default_session(self.workspace, "original")
        backend = LifecycleCommandBackend()
        backend.seed("replacement", durable=SessionDurableState.PAUSED)
        code, stdout, stderr = self.invoke_with(backend, "reopen", "replacement")
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["session_id"], "replacement")
        self.assertEqual(payload["durable_state"], "ready")
        self.assertEqual(payload["access_state"], "claimable")
        self.assertEqual(payload["assessment"]["scope"], "idle")
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id, "replacement"
        )

        backend = LifecycleCommandBackend()
        backend.seed("replacement", durable=SessionDurableState.READY)
        code, stdout, stderr = self.invoke_with(backend, "reopen")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["session_id"], "replacement")
        self.assertEqual(
            backend.calls[0:3],
            [("get", "replacement"), ("validate", "replacement"), ("acquire", "replacement")],
        )

        save_default_session(self.workspace, "retained")
        failing = LifecycleCommandBackend(fail_at="reopen")
        failing.seed("failed", durable=SessionDurableState.PAUSED)
        code, stdout, stderr = self.invoke_with(failing, "reopen", "failed")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("BackendProtocolError", stderr)
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id, "retained"
        )

    def test_pause_uses_optional_default_and_always_retains_it(self) -> None:
        save_default_session(self.workspace, "default_session")
        backend = LifecycleCommandBackend()
        backend.seed("other_session", durable=SessionDurableState.READY)
        code, stdout, stderr = self.invoke_with(backend, "pause", "other_session")
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(
            (payload["session_id"], payload["durable_state"], payload["access_state"]),
            ("other_session", "paused", "claimable"),
        )
        self.assertEqual(payload["assessment"]["scope"], "idle")
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id, "default_session"
        )
        self.assertEqual(
            backend.calls,
            [
                ("get", "other_session"),
                ("acquire", "other_session"),
                ("assess", "other_session"),
                ("pause", "other_session"),
                ("close", None),
            ],
        )

        backend = LifecycleCommandBackend()
        backend.seed("default_session", durable=SessionDurableState.READY)
        code, stdout, stderr = self.invoke_with(backend, "pause")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["session_id"], "default_session")
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id, "default_session"
        )

    def test_noninteractive_reopen_uses_active_default_conversation_and_preserves_awaiting_request_r9(
        self,
    ) -> None:
        services = compose_filesystem_conversation_storage(self.workspace)
        conversation = services.catalog.create("session", "Awaiting application")
        save_default_session(self.workspace, "session")
        save_default_conversation(
            self.workspace,
            "session",
            conversation.conversation_id,
        )
        backend = LifecycleCommandBackend(
            assessment=active_transition_assessment(
                "session",
                conversation.conversation_id,
                SessionRecoveryState.AWAITING_APPLICATION,
            )
        )
        backend.seed("session", durable=SessionDurableState.PAUSED)

        with mock.patch.object(
            cli_module,
            "activate_configured_conversation_storage",
            return_value=SimpleNamespace(services=services),
        ) as activate:
            code, stdout, stderr = self.invoke_with(backend, "reopen", "session")

        self.assertEqual((code, stderr), (0, ""))
        activate.assert_called_once_with(self.workspace)
        self.assertEqual(len(backend.assessment_calls), 1)
        assessment_call = backend.assessment_calls[0]
        self.assertIs(
            assessment_call["requested_operation"],
            SessionTransitionOperation.ENTER_DESTINATION,
        )
        self.assertEqual(
            assessment_call["conversation_id"],
            conversation.conversation_id,
        )
        self.assertIs(assessment_call["collaboration_store"], services.event_store)
        payload = json.loads(stdout)["assessment"]
        self.assertEqual(
            (payload["scope"], payload["state"], payload["next_action"]),
            ("active", "active", "follow_active_recovery"),
        )
        recovery = payload["recovery_result"]
        self.assertEqual(recovery["state"], "awaiting_application")
        self.assertEqual(recovery["confirmation_request_id"], "R9")
        self.assertEqual(recovery["collaboration_tail_sequence"], 9)
        self.assertFalse(
            {"revision", "digest", "choices", "capability"} & set(recovery)
        )

    def test_noninteractive_pause_active_staged_denial_preserves_durable_state_and_releases_only_new_claim(
        self,
    ) -> None:
        services = compose_filesystem_conversation_storage(self.workspace)
        conversation = services.catalog.create("session", "Staged work")
        save_default_session(self.workspace, "session")
        save_default_conversation(
            self.workspace,
            "session",
            conversation.conversation_id,
        )
        default_before = self.workspace.path(CLI_CONTEXT_PATH).read_bytes()
        conversation_before = conversation_default_path(
            self.workspace,
            "session",
        ).read_bytes()
        backend = LifecycleCommandBackend(
            assessment=active_transition_assessment(
                "session",
                conversation.conversation_id,
                SessionRecoveryState.STAGED,
            )
        )
        backend.seed("session", durable=SessionDurableState.READY)

        with mock.patch.object(
            cli_module,
            "activate_configured_conversation_storage",
            return_value=SimpleNamespace(services=services),
        ):
            code, stdout, stderr = self.invoke_with(backend, "pause", "session")

        self.assertEqual((code, stderr), (2, ""))
        self.assertEqual(
            backend.calls,
            [
                ("get", "session"),
                ("acquire", "session"),
                ("assess", "session"),
                ("release", "session"),
                ("close", None),
            ],
        )
        self.assertNotIn(("pause", "session"), backend.calls)
        self.assertEqual(len(backend.assessment_calls), 1)
        assessment_call = backend.assessment_calls[0]
        self.assertIs(
            assessment_call["requested_operation"],
            SessionTransitionOperation.PAUSE_SESSION,
        )
        self.assertEqual(
            assessment_call["conversation_id"],
            conversation.conversation_id,
        )
        self.assertIs(assessment_call["collaboration_store"], services.event_store)
        payload = json.loads(stdout)
        self.assertEqual(payload["durable_state"], "ready")
        self.assertEqual(payload["access_state"], "claimable")
        self.assertEqual(payload["assessment"]["scope"], "active")
        self.assertEqual(
            payload["assessment"]["recovery_result"]["state"],
            "staged",
        )
        self.assertEqual(
            self.workspace.path(CLI_CONTEXT_PATH).read_bytes(),
            default_before,
        )
        self.assertEqual(
            conversation_default_path(self.workspace, "session").read_bytes(),
            conversation_before,
        )

    def test_lifecycle_grammar_has_no_session_option_or_legacy_actions(self) -> None:
        invalid = (
            ["session", "create", "--workspace", str(self.workspace.root)],
            [
                "session",
                "reopen",
                "one",
                "two",
                "--workspace",
                str(self.workspace.root),
            ],
            ["session", "pause", "one", "two", "--workspace", str(self.workspace.root)],
            [
                "session",
                "create",
                "one",
                "--session",
                "two",
                "--workspace",
                str(self.workspace.root),
            ],
            ["session", "start", "one", "--workspace", str(self.workspace.root)],
        )
        for arguments in invalid:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                self.subTest(arguments=arguments),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as caught,
            ):
                cli_module.main(arguments)
            self.assertEqual(caught.exception.code, 2)

        code, stdout, stderr = invoke_cli(
            ["session", "reopen", "--workspace", str(self.workspace.root)]
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("no default session", stderr)


class DirectiveAndInspectDefaultTests(unittest.TestCase):
    """Verify retained management commands resolve the saved session."""

    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        self.enterContext(
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                return_value=self.workspace,
            )
        )

    def test_nested_cwd_infers_default_for_directive_and_inspect(self) -> None:
        save_default_session(self.workspace, "default_session")
        nested = self.workspace.root / "caller" / "commands"
        nested.mkdir(parents=True)
        with working_directory(nested):
            code, stdout, stderr = invoke_cli(["directive", "[SYS: LANG=KO]"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["language"], "KO")

            code, stdout, stderr = invoke_cli(["inspect"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["session_id"], "default_session")
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id,
            "default_session",
        )


if __name__ == "__main__":
    unittest.main()
