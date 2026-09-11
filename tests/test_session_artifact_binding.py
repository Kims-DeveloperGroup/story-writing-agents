"""Exact runtime isolation for session-bound writing artifacts."""

from __future__ import annotations

import subprocess

from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    inspect_session_resources,
    provision_session_resources,
)

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


if __name__ == "__main__":
    import unittest

    unittest.main()
