"""Exact runtime isolation for session-bound writing artifacts."""

from __future__ import annotations

import os
import subprocess
from unittest import mock

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
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.errors import WorkspaceSecurityError
from story_writing_agents.models import AgentStateEntry, DebateMessage
from story_writing_agents.workspace import SessionSourceWorkspace, Workspace
from story_writing_agents.workspace import core as workspace_core
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
    inspect_session_resources,
    provision_session_resources,
)
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _visible_outcome,
)


_OUTLINE = "core_documents/global_outline.md"


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

        def replace_during_read(root, relative, *, missing_ok=False):
            nonlocal entered
            if not entered:
                entered = True
                _moved, replacement = replacement_context.__enter__()
                (replacement / "replacement-sentinel").write_bytes(
                    b"mid-read replacement bytes"
                )
            return original_read_text(root, relative, missing_ok=missing_ok)

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
        original_source_open = workspace_core.open_workspace_root_directory

        def record_source_open(workspace):
            descriptor = original_source_open(workspace)
            source_descriptors.append(descriptor)
            return descriptor

        with mock.patch.object(
            workspace_core,
            "open_workspace_root_directory",
            side_effect=record_source_open,
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
            workspace_core,
            "open_workspace_root_directory",
            side_effect=record_source_open,
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

        with self.assertRaises(SessionArtifactBindingError):
            await bound.lead_turn(
                conversation,
                f"Read ${{{_OUTLINE}}} before answering.",
            )

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
                    self.assertEqual(
                        binding.repository.read_text(_OUTLINE),
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

                    with self.assertRaisesRegex(
                        ArtifactRootSecurityError,
                        "workspace root identity changed",
                    ):
                        binding.repository.read_text(_OUTLINE)

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
