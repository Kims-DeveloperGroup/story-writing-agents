"""Built-in provider-free smoke command and CLI contract tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentReply,
    ConversationEventKind,
    ConversationMemoryService,
    ConversationMetadata,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchScope,
    DebateStatus,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    LEAD_ORCHESTRATOR,
    Phase,
    PLOT_ARCHITECT,
    StoryRuntime,
    Workspace,
    initialize_workspace,
)
from story_writing_agents import backend as backend_module
from story_writing_agents import cli as cli_module
from story_writing_agents import smoke as smoke_module
from story_writing_agents.agents import CORE_DOCUMENTS, ROSTER, SPECIALIST_IDS
from story_writing_agents.conversation_retrieval import (
    ConversationRetrievalActionKind,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalSearchObservation,
)

from tests.helpers import ScriptedBackend, all_manuscripts


EXPECTED_CHECKS = [
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "conversation_routing",
    "conversation_search",
    "conversation_continuity",
    "conversation_retrieval",
    "consensus_context",
    "revision_workflow",
    "consensus_persisted",
    "manuscript_committed",
    "core_documents_unchanged",
]

SUCCESS_REPORT_KEYS = {
    "checks",
    "command",
    "debate_id",
    "debate_status",
    "note",
    "provider_tested",
    "revision_id",
    "rounds_completed",
    "session_id",
    "status",
    "workspace",
}


def invoke_cli(arguments: list[str]) -> tuple[int, str, str]:
    """Invoke the CLI in-process and return code, stdout, and stderr."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = cli_module.main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def snapshot_retained_smoke_artifacts(
    workspace: Workspace,
    *,
    session_id: str,
    debate_id: str,
    manuscript_path: str,
) -> dict[str, bytes]:
    """Read one run's durable artifacts without including shared workspace files."""

    relative_paths = {
        f"agent_states/orchestrator_state/sessions/{session_id}.json",
        manuscript_path,
    }
    discussion_root = workspace.path(f"discussion_logs/{session_id}")
    relative_paths.update(
        path.relative_to(workspace.root).as_posix()
        for path in discussion_root.rglob("*")
        if path.is_file()
    )
    relative_paths.update(
        path.relative_to(workspace.root).as_posix()
        for path in workspace.path("agent_states").rglob(f"{debate_id}.jsonl")
        if path.is_file() and session_id in path.parts
    )
    return {
        relative: workspace.path(relative).read_bytes()
        for relative in sorted(relative_paths)
    }


class SmokeHelpTests(unittest.TestCase):
    def test_smoke_help_explains_provider_free_and_temporary_default(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            cli_module.main(["smoke", "--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stderr.getvalue(), "")
        help_text = stdout.getvalue().lower()
        self.assertIn("provider-free", help_text)
        self.assertIn("temporary", help_text)
        self.assertIn("--workspace", help_text)

    def test_smoke_rejects_backend_argument_with_argparse_code_two(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            cli_module.main(
                ["smoke", "--backend", "provider.module:Backend"]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("unrecognized arguments: --backend", stderr.getvalue())


class SmokeTemporaryTests(unittest.TestCase):
    def test_zero_argument_smoke_is_compact_provider_free_and_temporary(
        self,
    ) -> None:
        """Keep the exact success report while forbidding every external path."""

        credential = "SMOKE_TEST_CREDENTIAL_MUST_NOT_BE_READ"
        original_environment_getitem = type(os.environ).__getitem__

        def reject_codex_credential_read(
            environment: object,
            key: str,
        ) -> str:
            """Delegate environment reads except for the forbidden credential."""

            if key == "CODEX_API_KEY":
                raise AssertionError("smoke must not read provider credentials")
            return original_environment_getitem(  # type: ignore[arg-type]
                environment,
                key,
            )

        forbidden = AssertionError("smoke must remain provider-free")
        with (
            mock.patch.dict(os.environ, {"CODEX_API_KEY": credential}),
            mock.patch.object(
                type(os.environ),
                "__getitem__",
                new=reject_codex_credential_read,
            ),
            mock.patch.object(
                backend_module.AgentBackend,
                "__init__",
                side_effect=forbidden,
            ) as agent_backend,
            mock.patch.object(
                smoke_module.asyncio,
                "create_subprocess_exec",
                side_effect=forbidden,
            ) as async_subprocess,
            mock.patch.object(
                subprocess,
                "Popen",
                side_effect=forbidden,
            ) as popen,
            mock.patch.object(
                subprocess,
                "run",
                side_effect=forbidden,
            ) as subprocess_run,
            mock.patch.object(
                socket,
                "create_connection",
                side_effect=forbidden,
            ) as network_connection,
            mock.patch("urllib.request.urlopen", side_effect=forbidden) as urlopen,
            mock.patch("webbrowser.open", side_effect=forbidden) as web_open,
        ):
            exit_code, stdout, stderr = invoke_cli(["smoke"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        agent_backend.assert_not_called()
        async_subprocess.assert_not_awaited()
        popen.assert_not_called()
        subprocess_run.assert_not_called()
        network_connection.assert_not_called()
        urlopen.assert_not_called()
        web_open.assert_not_called()
        self.assertNotIn(credential, stdout)
        self.assertEqual(len(stdout.splitlines()), 1)
        report = json.loads(stdout)
        self.assertEqual(
            stdout,
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )
        self.assertEqual(set(report), SUCCESS_REPORT_KEYS)
        self.assertIs(type(report["checks"]), list)
        self.assertIs(type(report["command"]), str)
        self.assertIs(type(report["debate_id"]), str)
        self.assertIs(type(report["debate_status"]), str)
        self.assertIs(type(report["note"]), str)
        self.assertIs(type(report["provider_tested"]), bool)
        self.assertIs(type(report["revision_id"]), str)
        self.assertIs(type(report["rounds_completed"]), int)
        self.assertIs(type(report["session_id"]), str)
        self.assertIs(type(report["status"]), str)
        self.assertIs(type(report["workspace"]), dict)
        self.assertEqual(
            set(report["workspace"]),
            {"mode", "path", "retained"},
        )
        self.assertIs(type(report["workspace"]["mode"]), str)
        self.assertIs(type(report["workspace"]["path"]), str)
        self.assertIs(type(report["workspace"]["retained"]), bool)
        self.assertEqual(report["command"], "smoke")
        self.assertEqual(report["status"], "passed")
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(report["debate_status"], "consensus")
        self.assertEqual(report["revision_id"], "r1")
        self.assertEqual(report["rounds_completed"], 1)
        self.assertIn("No LLM or external provider", report["note"])
        self.assertEqual(report["workspace"]["mode"], "temporary")
        self.assertIs(report["workspace"]["retained"], False)
        self.assertFalse(Path(report["workspace"]["path"]).exists())


class SmokePersistentTests(unittest.TestCase):
    def test_persistent_smoke_retains_consensus_artifacts_and_preserves_core(
        self,
    ) -> None:
        """Reconstruct and inspect every retained conversation and debate artifact."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(Path(temporary.name) / "workspace")
        for index, filename in enumerate(CORE_DOCUMENTS):
            workspace.write_text(
                f"core_documents/{filename}",
                "" if index == 0 else f"neutral-core-{index}\n",
            )
        core_before = {
            filename: workspace.path(
                f"core_documents/{filename}"
            ).read_bytes()
            for filename in CORE_DOCUMENTS
        }
        self.assertEqual(core_before[CORE_DOCUMENTS[0]], b"")
        selection_paths = (
            "agent_states/orchestrator_state/cli_context.json",
            "agent_states/orchestrator_state/conversation_selections/"
            "unrelated_session.json",
        )
        workspace.write_text(selection_paths[0], "{malformed-cli-selection")
        workspace.write_text(
            selection_paths[1],
            "{malformed-conversation-selection",
        )
        selections_before = {
            relative: workspace.path(relative).read_bytes()
            for relative in selection_paths
        }

        exit_code, stdout, stderr = invoke_cli(
            ["smoke", "--workspace", str(workspace.root)]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(report["workspace"]["mode"], "persistent")
        self.assertIs(report["workspace"]["retained"], True)
        self.assertEqual(
            Path(report["workspace"]["path"]),
            workspace.root,
        )
        self.assertTrue(workspace.root.exists())

        reconstructed_workspace = Workspace(workspace.root)
        catalog = FilesystemConversationCatalog(reconstructed_workspace)
        event_store = FilesystemConversationEventStore(
            reconstructed_workspace,
            catalog=catalog,
        )
        conversations = catalog.list(report["session_id"])
        self.assertEqual(len(conversations), 2)
        source, retrieval = conversations
        self.assertEqual(
            (source.subject, retrieval.subject),
            (smoke_module._SOURCE_SUBJECT, smoke_module._RETRIEVAL_SUBJECT),
        )
        self.assertNotEqual(source.conversation_id, retrieval.conversation_id)
        self.assertEqual(
            catalog.list(report["session_id"], include_archived=True),
            conversations,
        )
        self.assertEqual(
            FilesystemConversationCatalog(Workspace(workspace.root)).list(
                report["session_id"]
            ),
            conversations,
        )
        self.assertEqual(
            catalog.get(report["session_id"], source.conversation_id),
            source,
        )
        self.assertEqual(
            catalog.get(report["session_id"], retrieval.conversation_id),
            retrieval,
        )

        source_events = event_store.read(
            report["session_id"],
            source.conversation_id,
        )
        retrieval_events = event_store.read(
            report["session_id"],
            retrieval.conversation_id,
        )
        self.assertEqual(
            tuple(event.sequence for event in source_events),
            (1, 2, 3, 4),
        )
        self.assertEqual(
            tuple(event.content for event in source_events),
            (
                smoke_module._LEAD_QUESTION,
                smoke_module._LEAD_REPLY,
                smoke_module._SPECIALIST_QUESTION,
                smoke_module._SPECIALIST_REPLY,
            ),
        )
        self.assertEqual(
            tuple(event.kind for event in source_events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(
            tuple(event.sequence for event in retrieval_events),
            (1, 2),
        )
        self.assertEqual(
            tuple(event.content for event in retrieval_events),
            (smoke_module._RETRIEVAL_QUESTION, smoke_module._RETRIEVAL_REPLY),
        )
        self.assertEqual(
            tuple(event.kind for event in retrieval_events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertTrue(
            all(
                event.conversation_id == source.conversation_id
                for event in source_events
            )
        )
        self.assertTrue(
            all(
                event.conversation_id == retrieval.conversation_id
                for event in retrieval_events
            )
        )
        self.assertTrue(
            {event.turn_id for event in source_events}.isdisjoint(
                {event.turn_id for event in retrieval_events}
            )
        )

        memory = ConversationMemoryService(
            ConversationSearchScope(report["session_id"]),
            catalog,
            event_store,
        )
        subject_result = memory.search(
            ConversationSearchQuery(smoke_module._SOURCE_SUBJECT)
        )
        transcript_result = memory.search(
            ConversationSearchQuery(smoke_module._VISIBLE_SEARCH_TEXT)
        )
        self.assertEqual(len(subject_result.hits), 1)
        self.assertEqual(len(transcript_result.hits), 1)
        subject_hit = subject_result.hits[0]
        transcript_hit = transcript_result.hits[0]
        self.assertIs(
            subject_hit.match_kind,
            ConversationSearchMatchKind.EXACT_SUBJECT,
        )
        self.assertEqual(
            subject_hit.citation.conversation_id,
            source.conversation_id,
        )
        self.assertEqual(subject_hit.citation.session_id, report["session_id"])
        self.assertIsNone(subject_hit.citation.turn_id)
        self.assertIsNone(subject_hit.citation.sequence)
        self.assertIsNone(subject_hit.citation.event_kind)
        self.assertIs(
            transcript_hit.match_kind,
            ConversationSearchMatchKind.TRANSCRIPT,
        )
        self.assertEqual(
            transcript_hit.citation.conversation_id,
            source.conversation_id,
        )
        self.assertEqual(transcript_hit.citation.sequence, 2)
        self.assertEqual(
            transcript_hit.citation.turn_id,
            source_events[1].turn_id,
        )
        self.assertIs(
            transcript_hit.citation.event_kind,
            ConversationEventKind.AGENT_REPLY,
        )

        runtime = StoryRuntime(reconstructed_workspace)
        inspected = runtime.inspect_debate(
            report["session_id"],
            report["debate_id"],
        )
        self.assertEqual(inspected["status"], "consensus")
        self.assertTrue(inspected["terminal"])
        self.assertEqual(inspected["result"]["status"], "consensus")
        self.assertEqual(inspected["result"]["revision_id"], "r1")
        self.assertEqual(inspected["result"]["rounds_completed"], 1)
        self.assertTrue(inspected["messages"])
        context = event_store.read_context(
            report["session_id"],
            source.conversation_id,
        )
        self.assertEqual(context.events, source_events)
        self.assertEqual(
            (
                context.total_complete_turns,
                context.retained_turns,
                context.omitted_turns,
                context.truncated,
            ),
            (2, 2, 0, False),
        )
        self.assertEqual(
            runtime.inspect_debate_context(
                report["session_id"],
                report["debate_id"],
            ),
            {
                "schema_version": 1,
                "session_id": report["session_id"],
                "debate_id": report["debate_id"],
                "conversation_id": source.conversation_id,
                "mode": "included",
                "snapshot": {
                    "events": [event.to_dict() for event in source_events],
                    "total_complete_turns": context.total_complete_turns,
                    "retained_turns": context.retained_turns,
                    "omitted_turns": context.omitted_turns,
                    "retained_utf8_bytes": context.retained_utf8_bytes,
                    "truncated": context.truncated,
                },
            },
        )
        manuscript_path = inspected["result"]["manuscript_path"]
        self.assertIsNotNone(manuscript_path)
        assert manuscript_path is not None
        self.assertTrue(workspace.path(manuscript_path).is_file())
        self.assertEqual(
            workspace.read_text(manuscript_path),
            smoke_module._REVISION_ONE,
        )
        core_after = {
            filename: workspace.path(
                f"core_documents/{filename}"
            ).read_bytes()
            for filename in CORE_DOCUMENTS
        }
        self.assertEqual(core_after, core_before)
        self.assertEqual(
            {
                relative: workspace.path(relative).read_bytes()
                for relative in selection_paths
            },
            selections_before,
        )
        self.assertEqual(
            {
                path.name
                for path in workspace.path(
                    "agent_states/orchestrator_state/conversation_selections"
                ).iterdir()
            },
            {"unrelated_session.json"},
        )

    def test_two_persistent_runs_create_unique_session_debate_and_outputs(
        self,
    ) -> None:
        """Ensure a second run creates new artifacts without replacing the first."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(Path(temporary.name) / "workspace")
        first_exit, first_stdout, first_stderr = invoke_cli(
            ["smoke", "--workspace", str(workspace.root)]
        )
        self.assertEqual(first_exit, 0)
        self.assertEqual(first_stderr, "")
        first_report = json.loads(first_stdout)
        first_inspected = StoryRuntime(workspace).inspect_debate(
            first_report["session_id"],
            first_report["debate_id"],
        )
        first_manuscript = first_inspected["result"]["manuscript_path"]
        self.assertIsNotNone(first_manuscript)
        assert first_manuscript is not None
        first_artifacts = snapshot_retained_smoke_artifacts(
            workspace,
            session_id=first_report["session_id"],
            debate_id=first_report["debate_id"],
            manuscript_path=first_manuscript,
        )
        first_conversations = FilesystemConversationCatalog(workspace).list(
            first_report["session_id"]
        )

        second_exit, second_stdout, second_stderr = invoke_cli(
            ["smoke", "--workspace", str(workspace.root)]
        )
        self.assertEqual(second_exit, 0)
        self.assertEqual(second_stderr, "")
        second_report = json.loads(second_stdout)
        second_inspected = StoryRuntime(workspace).inspect_debate(
            second_report["session_id"],
            second_report["debate_id"],
        )
        second_manuscript = second_inspected["result"]["manuscript_path"]
        self.assertIsNotNone(second_manuscript)
        assert second_manuscript is not None
        second_artifacts = snapshot_retained_smoke_artifacts(
            workspace,
            session_id=second_report["session_id"],
            debate_id=second_report["debate_id"],
            manuscript_path=second_manuscript,
        )
        second_conversations = FilesystemConversationCatalog(workspace).list(
            second_report["session_id"]
        )
        reports = (first_report, second_report)

        self.assertEqual(len({item["session_id"] for item in reports}), 2)
        self.assertEqual(len({item["debate_id"] for item in reports}), 2)
        self.assertEqual(len({first_manuscript, second_manuscript}), 2)
        self.assertTrue(
            all(report["checks"] == EXPECTED_CHECKS for report in reports)
        )
        self.assertEqual(len(first_conversations), 2)
        self.assertEqual(len(second_conversations), 2)
        all_conversation_ids = {
            conversation.conversation_id
            for conversation in (*first_conversations, *second_conversations)
        }
        self.assertEqual(len(all_conversation_ids), 4)
        self.assertEqual(
            {
                relative: workspace.path(relative).read_bytes()
                for relative in first_artifacts
            },
            first_artifacts,
        )
        self.assertTrue(set(first_artifacts).isdisjoint(second_artifacts))
        first_agent_states = {
            relative
            for relative in first_artifacts
            if relative.startswith("agent_states/")
            and "/debates/" in relative
        }
        second_agent_states = {
            relative
            for relative in second_artifacts
            if relative.startswith("agent_states/")
            and "/debates/" in relative
        }
        self.assertEqual(len(first_agent_states), len(ROSTER))
        self.assertEqual(len(second_agent_states), len(ROSTER))
        self.assertTrue(first_agent_states.isdisjoint(second_agent_states))
        self.assertEqual(
            sum(relative.endswith("/metadata.json") for relative in first_artifacts),
            2,
        )
        self.assertEqual(
            sum(relative.endswith("/transcript.jsonl") for relative in first_artifacts),
            2,
        )
        first_debate_prefix = (
            f"discussion_logs/{first_report['session_id']}/"
            f"{first_report['debate_id']}"
        )
        self.assertTrue(
            {
                f"{first_debate_prefix}.state.json",
                f"{first_debate_prefix}.jsonl",
                f"{first_debate_prefix}.result.json",
                f"{first_debate_prefix}.context.json",
            }.issubset(first_artifacts)
        )
        self.assertIn(
            "agent_states/orchestrator_state/sessions/"
            f"{first_report['session_id']}.json",
            first_artifacts,
        )
        self.assertIn(first_manuscript, first_artifacts)
        self.assertEqual(
            {path.name for path in all_manuscripts(workspace)},
            {Path(first_manuscript).name, Path(second_manuscript).name},
        )

    def test_internal_trace_covers_routing_retrieval_context_and_revision(
        self,
    ) -> None:
        """Inspect both fake backends and every timed smoke verification trace."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"
        backend_instances: list[smoke_module._SmokeBackend] = []
        search_evidence: list[
            tuple[
                ConversationMemoryService,
                str,
                smoke_module._SmokeSearchEvidence,
            ]
        ] = []
        backend_type = smoke_module._SmokeBackend
        search_source = smoke_module._search_source_conversation

        def construct_recording_backend(
            session_id: str,
            debate_id: str,
        ) -> smoke_module._SmokeBackend:
            """Construct one real smoke backend while retaining its trace."""

            instance = backend_type(session_id, debate_id)
            backend_instances.append(instance)
            return instance

        def record_search_evidence(
            memory: ConversationMemoryService,
            source: ConversationMetadata,
            *,
            failed_check: str,
        ) -> smoke_module._SmokeSearchEvidence:
            """Delegate one source search while retaining collaborator identity."""

            evidence = search_source(
                memory,
                source,
                failed_check=failed_check,
            )
            search_evidence.append((memory, failed_check, evidence))
            return evidence

        with (
            mock.patch.object(
                smoke_module,
                "_SmokeBackend",
                side_effect=construct_recording_backend,
            ),
            mock.patch.object(
                smoke_module,
                "_search_source_conversation",
                side_effect=record_search_evidence,
            ),
            mock.patch.object(
                smoke_module,
                "_verify_preconsensus_artifact_isolation",
                wraps=smoke_module._verify_preconsensus_artifact_isolation,
            ) as isolation_verification,
            mock.patch.object(
                smoke_module,
                "_verify_conversation_retrieval",
                wraps=smoke_module._verify_conversation_retrieval,
            ) as retrieval_verification,
            mock.patch.object(
                smoke_module,
                "_verify_revision_workflow",
                wraps=smoke_module._verify_revision_workflow,
            ) as revision_verification,
        ):
            report, exit_code = smoke_module.run_smoke(workspace_path)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(len(backend_instances), 2)
        conversation_backend, resumed_backend = backend_instances
        self.assertIsNot(conversation_backend, resumed_backend)
        self.assertEqual(conversation_backend.requests, [])
        self.assertEqual(conversation_backend.retrieval_requests, [])
        self.assertEqual(resumed_backend.conversation_requests, [])

        self.assertEqual(len(conversation_backend.conversation_requests), 2)
        lead_request, specialist_request = conversation_backend.conversation_requests
        self.assertEqual(lead_request.agent_id, LEAD_ORCHESTRATOR)
        self.assertEqual(lead_request.context.events, ())
        self.assertEqual(specialist_request.agent_id, PLOT_ARCHITECT)
        self.assertEqual(
            tuple(event.content for event in specialist_request.context.events),
            (smoke_module._LEAD_QUESTION, smoke_module._LEAD_REPLY),
        )
        self.assertEqual(
            tuple(event.kind for event in specialist_request.context.events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(
            lead_request.conversation_id,
            specialist_request.conversation_id,
        )
        self.assertEqual(len(resumed_backend.retrieval_requests), 1)
        retrieval_request = resumed_backend.retrieval_requests[0]
        self.assertNotEqual(
            retrieval_request.conversation_id,
            lead_request.conversation_id,
        )
        self.assertEqual(retrieval_request.context.events, ())

        self.assertEqual(len(search_evidence), 2)
        self.assertEqual(
            [entry[1] for entry in search_evidence],
            ["conversation_search", "conversation_continuity"],
        )
        self.assertIsNot(search_evidence[0][0], search_evidence[1][0])
        self.assertEqual(
            search_evidence[0][2].subject_hit.citation,
            search_evidence[1][2].subject_hit.citation,
        )
        self.assertEqual(
            search_evidence[0][2].transcript_hit.citation,
            search_evidence[1][2].transcript_hit.citation,
        )

        self.assertEqual(
            tuple(action.action_index for action in resumed_backend.retrieval_actions),
            (1, 2, 3),
        )
        self.assertEqual(
            tuple(action.kind for action in resumed_backend.retrieval_actions),
            (
                ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
                ConversationRetrievalActionKind.READ_TRANSCRIPT,
                ConversationRetrievalActionKind.ANSWER,
            ),
        )
        search_outcome, read_outcome, answer_outcome = (
            resumed_backend.retrieval_outcomes
        )
        self.assertIs(type(search_outcome), ConversationRetrievalSearchObservation)
        self.assertIs(type(read_outcome), ConversationRetrievalReadObservation)
        self.assertIs(type(answer_outcome), ConversationRetrievalReply)
        assert type(read_outcome) is ConversationRetrievalReadObservation
        assert type(answer_outcome) is ConversationRetrievalReply
        self.assertEqual(len(read_outcome.events), 2)
        self.assertEqual(
            tuple(source.citation for source in answer_outcome.sources),
            tuple(event.citation for event in read_outcome.events),
        )

        retrieval_verification.assert_called_once()
        retrieval_result = retrieval_verification.call_args.args[2]
        progress = retrieval_verification.call_args.args[3]
        self.assertEqual(retrieval_result.sources, answer_outcome.sources)
        self.assertEqual(
            tuple(event.phase for event in progress),
            (
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                ConversationRetrievalProgressPhase.READ_STARTED,
                ConversationRetrievalProgressPhase.READ_COMPLETED,
            ),
        )
        self.assertEqual(
            tuple(event.action_index for event in progress),
            (1, 1, 2, 2),
        )
        self.assertEqual(progress[1].search_hit_count, 1)
        self.assertEqual(progress[3].transcript_turn_count, 1)
        self.assertEqual(progress[3].transcript_event_count, 2)
        self.assertTrue(all(event.truncated is False for event in progress))

        expected_context = resumed_backend.expected_consensus_context
        self.assertIsNotNone(expected_context)
        self.assertEqual(len(resumed_backend.requests), 10)
        self.assertTrue(
            all(
                request.conversation_context == expected_context
                for request in resumed_backend.requests
            )
        )
        self.assertEqual(
            tuple(
                call.kwargs["failed_check"]
                for call in isolation_verification.call_args_list
            ),
            ("conversation_routing", "conversation_retrieval"),
        )

        revision_verification.assert_called_once()
        self.assertIs(revision_verification.call_args.args[1], resumed_backend)
        counts = Counter(request.phase for request in resumed_backend.requests)
        self.assertEqual(
            counts,
            {
                Phase.DRAFT: 1,
                Phase.AUDIT: 6,
                Phase.PROPOSE_REVISION: 1,
                Phase.SYNTHESIZE: 1,
                Phase.FINALIZE: 1,
            },
        )
        self.assertEqual(
            resumed_backend.maximum_audit_active,
            len(SPECIALIST_IDS),
        )
        self.assertEqual(
            Counter(
                definition.agent_id
                for definition in resumed_backend.definitions
            ),
            Counter({agent_id: 1 for agent_id in ROSTER}),
        )
        self.assertTrue(
            all(
                not hasattr(request, "system_prompt")
                for request in resumed_backend.requests
            )
        )


class SmokeFailureTests(unittest.TestCase):
    def test_malformed_existing_config_uses_existing_cli_error_contract(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(Path(temporary.name) / "workspace")
        workspace.path("config/system_defaults.json").write_bytes(b"{invalid")

        exit_code, stdout, stderr = invoke_cli(
            ["smoke", "--workspace", str(workspace.root)]
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("WorkspaceFormatError: "))
        self.assertIn("config/system_defaults.json", stderr)
        self.assertTrue(stderr.endswith("\n"))

    def test_invariant_failure_is_compact_failed_json_with_code_one(self) -> None:
        """Preserve the post-debate invariant report and code-one contract."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"

        with mock.patch.object(
            smoke_module,
            "_verify_revision_workflow",
            side_effect=smoke_module._SmokeInvariantError(
                "revision_workflow"
            ),
        ):
            exit_code, stdout, stderr = invoke_cli(
                ["smoke", "--workspace", str(workspace_path)]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(len(stdout.splitlines()), 1)
        report = json.loads(stdout)
        self.assertEqual(
            stdout,
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_check"], "revision_workflow")
        self.assertEqual(set(report), SUCCESS_REPORT_KEYS | {"failed_check"})
        self.assertEqual(report["checks"], EXPECTED_CHECKS[:8])
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(report["debate_status"], "consensus")

    def test_predebate_invariant_failure_removes_temporary_workspace(self) -> None:
        """Report a named pre-debate failure and remove all temporary artifacts."""

        with mock.patch.object(
            smoke_module,
            "_verify_conversation_catalog",
            side_effect=smoke_module._SmokeInvariantError(
                "conversation_catalog_persisted"
            ),
        ):
            exit_code, stdout, stderr = invoke_cli(["smoke"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(len(stdout.splitlines()), 1)
        report = json.loads(stdout)
        self.assertEqual(
            stdout,
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )
        self.assertEqual(set(report), SUCCESS_REPORT_KEYS | {"failed_check"})
        self.assertEqual(report["status"], "failed")
        self.assertEqual(
            report["failed_check"],
            "conversation_catalog_persisted",
        )
        self.assertEqual(report["checks"], EXPECTED_CHECKS[:2])
        self.assertEqual(report["debate_status"], "not_run")
        self.assertIsNone(report["revision_id"])
        self.assertEqual(report["rounds_completed"], 0)
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(report["workspace"]["mode"], "temporary")
        self.assertIs(report["workspace"]["retained"], False)
        self.assertFalse(Path(report["workspace"]["path"]).exists())

    def test_bind_invariant_uses_active_conversation_stage(self) -> None:
        """Attribute real bind invariants to routing or retrieval as active."""

        real_bind = smoke_module._SmokeBackend.bind
        selected_failed_check = ""
        observed_binding_checks: list[str] = []

        async def fail_bind_during_selected_stage(
            backend: smoke_module._SmokeBackend,
            definition: object,
        ) -> object:
            """Run the real bind while invalidating only the selected stage."""

            observed_binding_checks.append(backend._binding_failed_check)
            if backend._binding_failed_check != selected_failed_check:
                return await real_bind(
                    backend,
                    definition,  # type: ignore[arg-type]
                )
            with mock.patch.object(smoke_module, "ROSTER", ()):
                return await real_bind(
                    backend,
                    definition,  # type: ignore[arg-type]
                )

        cases = (
            ("conversation_routing", EXPECTED_CHECKS[:3]),
            ("conversation_retrieval", EXPECTED_CHECKS[:6]),
        )
        for failed_check, completed_checks in cases:
            with self.subTest(failed_check=failed_check):
                selected_failed_check = failed_check
                observed_binding_checks.clear()
                with mock.patch.object(
                    smoke_module._SmokeBackend,
                    "bind",
                    new=fail_bind_during_selected_stage,
                ):
                    report, exit_code = smoke_module.run_smoke()

                self.assertEqual(exit_code, 1)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["failed_check"], failed_check)
                self.assertEqual(report["checks"], completed_checks)
                self.assertIn(failed_check, observed_binding_checks)


class ExistingRunExitCodeTests(unittest.TestCase):
    def test_run_deadlock_and_failed_codes_remain_three_and_four(self) -> None:
        cases = (
            ("deadlock", 3),
            ("failed", 4),
        )
        for outcome, expected_code in cases:
            with self.subTest(outcome=outcome):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                workspace = initialize_workspace(
                    Path(temporary.name) / "workspace"
                )

                def respond(request):
                    if request.phase is Phase.AUDIT:
                        if outcome == "failed" and request.agent_id == PLOT_ARCHITECT:
                            return AgentReply.approval("wrong-revision")
                        if request.agent_id == PLOT_ARCHITECT:
                            return AgentReply.conflict(
                                request.revision_id,
                                "neutral unresolved conflict",
                            )
                        return AgentReply.approval(request.revision_id)
                    raise AssertionError(
                        f"unexpected phase: {request.phase}"
                    )

                backend = ScriptedBackend(respond)
                with mock.patch.object(
                    cli_module,
                    "AgentBackend",
                    return_value=backend,
                ):
                    exit_code, stdout, stderr = invoke_cli(
                        [
                            "run",
                            "--workspace",
                            str(workspace.root),
                            "--session",
                            f"{outcome}_session",
                            "--backend",
                            "plot_architect=codex",
                            "--draft",
                            "neutral candidate",
                            "--debate-id",
                            f"{outcome}_debate",
                            "--max-debate-rounds",
                            "0",
                        ]
                    )

                self.assertEqual(exit_code, expected_code)
                self.assertEqual(stderr, "")
                report = json.loads(stdout)
                self.assertEqual(report["status"], outcome)
                self.assertEqual(all_manuscripts(workspace), [])


if __name__ == "__main__":
    unittest.main()
