"""Independent definitions for the workspace and debate context tests."""

from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path

import story_writing_agents
from story_writing_agents import (
    AGENT_SPECS,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    DebateContextMode,
    PLOT_ARCHITECT,
    ROSTER,
    StoryRuntime,
    Workspace,
    initialize_workspace,
    workspace as workspace_facade,
)
from story_writing_agents.agents import CORE_DOCUMENTS
from story_writing_agents.errors import (
    DebateExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.workspace import core as workspace_module
from story_writing_agents.workspace.schema import DEFAULT_AGENT_PROMPTS
from tests.helpers import initialized_workspace, validated_initialized_workspace


_CONTEXT_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def snapshot_directory_namespace(directory: Path) -> dict[str, bytes | None]:
    """Return every descendant name and file byte sequence below a directory."""

    return {
        path.relative_to(directory).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(directory.rglob("*"))
    }


def debate_context_window(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
) -> ConversationContextWindow:
    """Return one canonical complete turn for sidecar persistence tests."""

    events = (
        ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            "turn_a",
            1,
            _CONTEXT_TIMESTAMP,
            "user",
            PLOT_ARCHITECT,
            ConversationEventKind.USER_MESSAGE,
            "question",
        ),
        ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            "turn_a",
            2,
            _CONTEXT_TIMESTAMP,
            PLOT_ARCHITECT,
            "user",
            ConversationEventKind.AGENT_REPLY,
            "answer",
        ),
    )
    return ConversationContextWindow(
        events=events,
        total_complete_turns=3,
        retained_turns=1,
        omitted_turns=2,
        retained_utf8_bytes=sum(len(event.content.encode("utf-8")) for event in events),
        truncated=True,
    )


class WorkspaceContractDefinitions:
    """Verify the intentionally breaking v2 public facade."""

    def test_all_default_prompts_are_checked_in_byte_identical_for_fresh_workspace(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        checked_in = {
            agent_id: (
                repository_root / AGENT_SPECS[agent_id].system_prompt_file
            ).read_text(encoding="utf-8")
            for agent_id in ROSTER
        }
        self.assertTrue(all(prompt.endswith("\n") for prompt in checked_in.values()))
        self.assertEqual(DEFAULT_AGENT_PROMPTS, checked_in)
        with tempfile.TemporaryDirectory(prefix="lead-default-prompt-") as temporary:
            workspace = Workspace(temporary).initialize()
            for agent_id in ROSTER:
                relative = AGENT_SPECS[agent_id].system_prompt_file
                self.assertEqual(workspace.read_text(relative), checked_in[agent_id])

    def test_workspace_facade_exports_and_signatures_are_exact(self) -> None:
        self.assertEqual(
            workspace_facade.__all__,
            ["Workspace", "initialize_workspace", "FilesystemConversationCatalog"],
        )
        self.assertEqual(
            workspace_module.__all__, ["Workspace", "initialize_workspace"]
        )
        self.assertIs(workspace_facade.Workspace, Workspace)
        self.assertIs(story_writing_agents.Workspace, Workspace)
        self.assertIs(workspace_facade.initialize_workspace, initialize_workspace)
        self.assertIs(story_writing_agents.initialize_workspace, initialize_workspace)
        self.assertEqual(tuple(inspect.signature(Workspace).parameters), ("root",))
        self.assertEqual(
            tuple(inspect.signature(initialize_workspace).parameters),
            ("root",),
        )
        self.assertNotIn(
            "overwrite_prompts",
            inspect.signature(Workspace.initialize).parameters,
        )
        for retired in (
            "DEFAULT_AGENT_CONFIGS",
            "DEFAULT_AGENT_PROMPTS",
            "DEFAULT_SYSTEM_CONFIG",
            "_initialize_pending_workspace_entries",
        ):
            with self.subTest(retired=retired):
                self.assertFalse(hasattr(workspace_module, retired))
                self.assertFalse(hasattr(workspace_facade, retired))
                self.assertFalse(hasattr(story_writing_agents, retired))

    def test_generic_writes_reject_canonical_artifacts_but_keep_other_helpers(
        self,
    ) -> None:
        workspace = validated_initialized_workspace(self)
        canonical_paths = (
            *(f"core_documents/{filename}" for filename in CORE_DOCUMENTS),
            "manuscript/part-01/chapter.md",
            "manuscript/manuscript/chapter.md",
        )
        for relative in canonical_paths:
            with self.subTest(relative=relative):
                target = workspace.path(relative)
                before = target.read_bytes() if target.is_file() else None
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "published by the repository",
                ):
                    workspace.write_text(relative, "generic write")
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "published by the repository",
                ):
                    workspace.write_json(relative, {"generic": "write"})
                after = target.read_bytes() if target.is_file() else None
                self.assertEqual(after, before)

        workspace.write_text("discussion_logs/unrelated.txt", "still writable")
        workspace.write_json("agent_states/unrelated.json", {"value": 1})
        self.assertEqual(
            workspace.read_text("discussion_logs/unrelated.txt"),
            "still writable",
        )
        self.assertEqual(
            workspace.read_json("agent_states/unrelated.json"),
            {"value": 1},
        )

    def test_generic_writes_reject_reserved_case_and_normalization_aliases(
        self,
    ) -> None:
        workspace = validated_initialized_workspace(self)
        cases = (
            (
                "Core_Documents/global_outline.md",
                "published by the repository",
            ),
            (
                "CORE_DOCUMENTS/global_outline.md",
                "published by the repository",
            ),
            (
                "ｍａｎｕｓｃｒｉｐｔ/part-01/chapter.md",
                "published by the repository",
            ),
            (
                "Manuscript/part-01/chapter.md",
                "published by the repository",
            ),
            ("config/workspace.json", "owned by initialization"),
            ("Config/Workspace.JSON", "owned by initialization"),
            (
                "ｃｏｎｆｉｇ/ｗｏｒｋｓｐａｃｅ．ｊｓｏｎ",
                "owned by initialization",
            ),
            (
                ".story-writing-agents-initialize-v2.json",
                "owned by initialization",
            ),
            (
                ".STORY-WRITING-AGENTS-INITIALIZE-V2.JSON",
                "owned by initialization",
            ),
            (
                "．ｓｔｏｒｙ－ｗｒｉｔｉｎｇ－ａｇｅｎｔｓ－ｉｎｉｔｉａｌｉｚｅ－ｖ２．ｊｓｏｎ",
                "owned by initialization",
            ),
        )
        for relative, error in cases:
            with self.subTest(relative=relative):
                target = workspace.path(relative)
                before = (
                    (True, target.read_bytes())
                    if target.is_file()
                    else (target.exists(), None)
                )
                with self.assertRaisesRegex(WorkspaceSecurityError, error):
                    workspace.write_text(relative, "must not be written")
                with self.assertRaisesRegex(WorkspaceSecurityError, error):
                    workspace.write_json(relative, {"must": "not be written"})
                after = (
                    (True, target.read_bytes())
                    if target.is_file()
                    else (target.exists(), None)
                )
                self.assertEqual(after, before)

        workspace.write_text("discussion_logs/Case_Alias.txt", "unrelated")
        self.assertEqual(
            workspace.read_text("discussion_logs/Case_Alias.txt"),
            "unrelated",
        )

    def test_utf8_and_json_read_errors_are_explicit_and_nonmutating(self) -> None:
        workspace = validated_initialized_workspace(self)
        invalid_utf8 = workspace.path("discussion_logs/invalid-utf8.txt")
        invalid_utf8.write_bytes(b"\xff")
        with self.assertRaisesRegex(WorkspaceFormatError, "not valid UTF-8"):
            workspace.read_text("discussion_logs/invalid-utf8.txt")
        self.assertEqual(invalid_utf8.read_bytes(), b"\xff")

        invalid_json = workspace.path("agent_states/invalid-json.json")
        invalid_json.write_bytes(b"{bad json")
        with self.assertRaisesRegex(WorkspaceFormatError, "not valid JSON"):
            workspace.read_json("agent_states/invalid-json.json")
        self.assertEqual(invalid_json.read_bytes(), b"{bad json")

    def test_path_traversal_absolute_paths_and_symlink_escape_are_rejected(
        self,
    ) -> None:
        workspace = validated_initialized_workspace(self)
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary)
            secret = outside / "secret.txt"
            secret.write_text("outside", encoding="utf-8")
            outside_before = snapshot_directory_namespace(outside)
            escape = workspace.root / "escape"
            escape.symlink_to(outside, target_is_directory=True)

            for unsafe in ("../outside", str(secret)):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(WorkspaceSecurityError):
                        workspace.path(unsafe)
            with self.assertRaises(WorkspaceSecurityError):
                workspace.read_text("escape/secret.txt")
            with self.assertRaises(WorkspaceSecurityError):
                workspace.write_text("escape/new.txt", "must-not-escape")

            self.assertEqual(snapshot_directory_namespace(outside), outside_before)
            self.assertTrue(escape.is_symlink())
            self.assertEqual(escape.resolve(), outside.resolve())

    def test_session_state_symlink_alias_is_rejected_without_target_mutation(
        self,
    ) -> None:
        workspace = validated_initialized_workspace(self)
        workspace.save_session_overrides(
            "session_original",
            {"language": "EN", "persona": None, "agents": {}},
        )
        original = workspace.path(
            "agent_states/orchestrator_state/sessions/session_original.json"
        )
        original_before = original.read_bytes()
        alias = workspace.path(
            "agent_states/orchestrator_state/sessions/session_alias.json"
        )
        alias.symlink_to("session_original.json")

        with self.assertRaises(WorkspaceSecurityError):
            workspace.load_session_overrides("session_alias")
        with self.assertRaises(WorkspaceSecurityError):
            workspace.save_session_overrides(
                "session_alias",
                {"language": "JA", "persona": None, "agents": {}},
            )

        self.assertEqual(original.read_bytes(), original_before)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.readlink(), Path("session_original.json"))


class DebateContextSidecarDefinitions:
    """Verify immutable, strict, identity-bound debate context sidecars."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.relative = "discussion_logs/session_a/debate_a.context.json"

    def test_absent_fresh_included_and_empty_schemas_are_exact(self) -> None:
        runtime = StoryRuntime(self.workspace)
        self.assertIsNone(
            self.workspace.inspect_debate_context("session_a", "debate_a")
        )
        self.assertIsNone(runtime.inspect_debate_context("session_a", "debate_a"))
        self.assertIsNone(
            runtime.session("session_a").inspect_debate_context("debate_a")
        )
        inspect_before = self.workspace.inspect_debate("session_a", "debate_a")
        session_before = self.workspace.inspect_session("session_a")

        self.workspace.save_debate_context(
            "session_a",
            "debate_a",
            conversation_id="conversation_a",
            mode=DebateContextMode.FRESH,
            snapshot=None,
        )
        fresh = self.workspace.inspect_debate_context("session_a", "debate_a")
        assert fresh is not None
        self.assertEqual(
            set(fresh),
            {
                "schema_version",
                "session_id",
                "debate_id",
                "conversation_id",
                "mode",
                "snapshot",
            },
        )
        self.assertEqual(
            fresh,
            {
                "schema_version": 1,
                "session_id": "session_a",
                "debate_id": "debate_a",
                "conversation_id": "conversation_a",
                "mode": "fresh",
                "snapshot": None,
            },
        )
        self.assertEqual(
            self.workspace.inspect_debate("session_a", "debate_a"),
            inspect_before,
        )
        self.assertEqual(self.workspace.inspect_session("session_a"), session_before)

        empty = ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )
        self.workspace.save_debate_context(
            "session_a",
            "empty_debate",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=empty,
        )
        empty_sidecar = self.workspace.inspect_debate_context(
            "session_a", "empty_debate"
        )
        assert empty_sidecar is not None
        self.assertEqual(
            empty_sidecar["snapshot"],
            {
                "events": [],
                "total_complete_turns": 0,
                "retained_turns": 0,
                "omitted_turns": 0,
                "retained_utf8_bytes": 0,
                "truncated": False,
            },
        )

        context = debate_context_window()
        self.workspace.save_debate_context(
            "session_a",
            "included_debate",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=context,
        )
        included = self.workspace.inspect_debate_context("session_a", "included_debate")
        assert included is not None
        snapshot = included["snapshot"]
        assert isinstance(snapshot, dict)
        self.assertEqual(
            set(snapshot),
            {
                "events",
                "total_complete_turns",
                "retained_turns",
                "omitted_turns",
                "retained_utf8_bytes",
                "truncated",
            },
        )
        self.assertEqual(
            snapshot["events"], [event.to_dict() for event in context.events]
        )
        self.assertTrue(
            all(
                set(event)
                == {
                    "schema_version",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "sequence",
                    "timestamp",
                    "sender",
                    "recipient",
                    "kind",
                    "content",
                }
                for event in snapshot["events"]
            )
        )

    def test_create_only_collision_preserves_bytes_and_blocks_reservation(
        self,
    ) -> None:
        context = debate_context_window()
        path = self.workspace.save_debate_context(
            "session_a",
            "debate_a",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=context,
        )
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        with self.assertRaises(DebateExistsError):
            self.workspace.save_debate_context(
                "session_a",
                "debate_a",
                conversation_id="conversation_a",
                mode=DebateContextMode.FRESH,
                snapshot=None,
            )
        with self.assertRaises(DebateExistsError):
            self.workspace.reserve_debate(
                "session_a",
                "debate_a",
                input_kind="prompt",
                owner_token="owner",
            )
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertFalse(
            self.workspace.path(
                "discussion_logs/session_a/debate_a.state.json"
            ).exists()
        )

    def test_tampering_and_duplicate_fields_raise_without_rewriting(self) -> None:
        self.workspace.save_debate_context(
            "session_a",
            "debate_a",
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
            snapshot=debate_context_window(),
        )
        canonical = self.workspace.read_json(self.relative)

        def clone() -> dict[str, object]:
            return json.loads(json.dumps(canonical))

        extra = clone()
        extra["extra"] = True
        wrong_path = clone()
        wrong_path["debate_id"] = "other_debate"
        missing_snapshot = clone()
        missing_snapshot["snapshot"] = None
        wrong_counts = clone()
        assert isinstance(wrong_counts["snapshot"], dict)
        wrong_counts["snapshot"]["retained_turns"] = 2
        wrong_event_identity = clone()
        assert isinstance(wrong_event_identity["snapshot"], dict)
        events = wrong_event_identity["snapshot"]["events"]
        assert isinstance(events, list)
        events[0]["conversation_id"] = "other_conversation"
        reordered = clone()
        assert isinstance(reordered["snapshot"], dict)
        reordered["snapshot"]["events"].reverse()

        for label, value in (
            ("extra top-level field", extra),
            ("path identity", wrong_path),
            ("included null snapshot", missing_snapshot),
            ("inconsistent counts", wrong_counts),
            ("event identity", wrong_event_identity),
            ("event ordering", reordered),
        ):
            with self.subTest(label=label):
                self.workspace.write_json(self.relative, value, overwrite=True)
                before = self.workspace.path(self.relative).read_bytes()
                with self.assertRaises(WorkspaceFormatError):
                    self.workspace.inspect_debate_context("session_a", "debate_a")
                self.assertEqual(
                    self.workspace.path(self.relative).read_bytes(),
                    before,
                )

        duplicate = (
            '{"schema_version":1,"schema_version":1,'
            '"session_id":"session_a","debate_id":"debate_a",'
            '"conversation_id":"conversation_a","mode":"fresh",'
            '"snapshot":null}'
        )
        self.workspace.write_text(self.relative, duplicate, overwrite=True)
        before = self.workspace.path(self.relative).read_bytes()
        with self.assertRaises(WorkspaceFormatError):
            self.workspace.inspect_debate_context("session_a", "debate_a")
        self.assertEqual(self.workspace.path(self.relative).read_bytes(), before)
