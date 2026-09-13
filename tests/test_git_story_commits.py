"""End-to-end authority, decision, commit, and recovery coverage."""

from __future__ import annotations

import copy
import multiprocessing
import os
import pickle
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import story_commits as commits_module
from story_writing_agents.git import repository_lock as lock_module
from story_writing_agents.git.session_history import (
    GitSessionHistory,
    GitSessionRecoveryHistory,
    open_git_session_history,
)
from story_writing_agents.git.session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryRecoveryAction,
    GitSessionHistoryState,
    advance_commit_operation,
    encode_commit_operation,
)
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitRecoveryAction,
    StoryCommitState,
)
from story_writing_agents.git.story_commits import StoryCommitService
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
)
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.schema import canonical_initializer_scaffold
from story_writing_agents.writing_artifacts import (
    ArtifactKind,
    ArtifactReplaceOperation,
    build_artifact_change_set,
    canonical_artifact_kind,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository

from tests.test_git_story_commit_candidates import (
    StoryCommitGitTestCase,
    manifest,
    sha,
)


class StoryCommitServiceTests(StoryCommitGitTestCase):
    def prepared(self, name: str = "commit story 한글", *, object_format: str = "sha1"):
        root, session_root, evidence = self.applied_replace(
            name, object_format=object_format
        )
        service = StoryCommitService(root, manifest(), "novel")
        candidate = service.prepare(
            evidence, "Commit exact 장면 🌙", ("reader impact",)
        )
        return root, session_root, service, candidate

    def prepared_bound(self, name: str):
        root, session_root, evidence = self.applied_replace(name)
        observed = os.stat(session_root)
        artifact_binding = compose_session_artifact_binding(root, "novel")
        service = commits_module.create_identity_bound_story_commit_service(
            root,
            manifest(),
            "novel",
            (observed.st_dev, observed.st_ino),
            artifact_binding._generation_identity,
            artifact_repository_access=(
                artifact_binding.open_story_commit_artifact_repository
            ),
        )
        candidate = service.prepare(evidence, "Commit exact change", ("impact",))
        return root, session_root, service, candidate

    def apply_followup(
        self,
        session_root,
        *,
        suffix: str,
    ) -> StoryCommitApplicationEvidence:
        path = "core_documents/global_outline.md"
        base = {
            item.path: (session_root / item.path).read_bytes()
            for item in canonical_initializer_scaffold()
            if canonical_artifact_kind(item.path) is not None
        }
        after = f"Follow-up scene {suffix}.\n".encode()
        change_set = build_artifact_change_set(
            base,
            (
                ArtifactReplaceOperation(
                    path,
                    ArtifactKind.CORE_DOCUMENT,
                    sha(base[path]),
                    after,
                    sha(after),
                ),
            ),
        )
        (session_root / path).write_bytes(after)
        WritingArtifactRepository(session_root).list_paths()
        return StoryCommitApplicationEvidence(
            "novel",
            f"application:{suffix}",
            f"proposal:{suffix}",
            f"correlation:{suffix}",
            change_set,
            change_set.projected_state_digest,
        )

    def assert_terminal_reads_do_not_mutate(
        self,
        service,
        operation_id,
        *,
        expected=None,
        failure_code=None,
    ) -> None:
        with mock.patch.object(
            commits_module,
            "advance_commit_operation",
            wraps=commits_module.advance_commit_operation,
        ) as advance, mock.patch.object(
            GitSessionHistory,
            "write_commit_operation",
            autospec=True,
        ) as write:
            for action in (service.inspect, service.recover):
                with self.subTest(action=action.__name__):
                    if failure_code is None:
                        self.assertEqual(action(operation_id), expected)
                    else:
                        with self.assertRaises(StoryCommitError) as caught:
                            action(operation_id)
                        self.assertIs(caught.exception.code, failure_code)
            advance.assert_not_called()
            write.assert_not_called()

    @staticmethod
    def confirmation(candidate, decision=StoryCommitDecision.COMMIT, suffix="1"):
        return StoryCommitConfirmation(
            f"confirmation:{suffix}",
            candidate.candidate_id,
            candidate.proposal_id,
            candidate.correlation_id,
            decision,
        )

    def test_exports_are_closed_and_authority_is_one_use_owner_thread_bound_nonserializable(
        self,
    ) -> None:
        self.assertEqual(
            tuple(commits_module.__all__),
            ("StoryCommitAuthority", "StoryCommitService"),
        )
        for name in commits_module.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)
        root, _session_root, service, candidate = self.prepared("authority")
        authority = service.authorize(candidate, self.confirmation(candidate))
        self.assertEqual(repr(authority), "StoryCommitAuthority(<process-local>)")
        for action in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(action=action.__name__):
                with self.assertRaises(StoryCommitError) as caught:
                    action(authority)
                self.assertIs(
                    caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID
                )
        original_pid = authority._pid  # noqa: SLF001
        authority._pid = -1  # noqa: SLF001
        with self.assertRaises(StoryCommitError) as caught:
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        authority._pid = os.getpid()  # noqa: SLF001
        self.assertEqual(original_pid, authority._pid)  # noqa: SLF001
        foreign_service = StoryCommitService(root, manifest(), "novel")
        with self.assertRaises(StoryCommitError) as caught:
            foreign_service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        with ThreadPoolExecutor(max_workers=1) as executor:
            error = executor.submit(self._execute_error, service, authority).result()
        self.assertIs(error.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        result = service.execute(authority)
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        with self.assertRaises(StoryCommitError) as caught:
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_CONSUMED)

    def test_forked_authority_rejects_before_copied_lock_and_parent_can_consume(
        self,
    ) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        _root, _session_root, service, candidate = self.prepared_bound(
            "forked-authority-lock"
        )
        authority = service.authorize(
            candidate,
            self.confirmation(candidate, suffix="forked-lock"),
        )
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def consume_in_child() -> None:
            try:
                with mock.patch.object(
                    commits_module,
                    "open_git_session_history",
                    side_effect=AssertionError("forked authority reached Git I/O"),
                ):
                    service.execute(authority)
            except BaseException as failure:
                child_results.put(
                    (
                        type(failure).__name__,
                        getattr(getattr(failure, "code", None), "value", None),
                        failure.__cause__ is None,
                    )
                )
            else:
                child_results.put((None, None, None))

        authority._lock.acquire()  # noqa: SLF001
        process = context.Process(target=consume_in_child)
        try:
            process.start()
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            child = child_results.get(timeout=3)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            authority._lock.release()  # noqa: SLF001
        self.assertEqual(
            child,
            (
                "StoryCommitError",
                StoryCommitFailureCode.AUTHORITY_INVALID.value,
                True,
            ),
        )
        self.assertFalse(authority._consumed)  # noqa: SLF001
        result = service.execute(authority)
        self.assertIs(result.state, StoryCommitState.COMMITTED)

    def test_identity_bound_service_rejects_foreign_thread_and_fork_before_io(
        self,
    ) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        _root, _session_root, service, candidate = self.prepared_bound(
            "service-affinity"
        )
        authority = service.authorize(
            candidate,
            self.confirmation(candidate, suffix="creator-authority"),
        )

        class PoisonLock:
            def __enter__(self):
                raise AssertionError("foreign service touched state lock")

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

        class PoisonBindings(dict):
            def __getitem__(self, key):
                raise AssertionError("foreign service read candidate map")

            def get(self, key, default=None):
                del key, default
                raise AssertionError("foreign service read candidate map")

            def pop(self, key, default=None):
                del key, default
                raise AssertionError("foreign service mutated candidate map")

        actions = (
            lambda: service.prepare(None, "foreign", ()),
            lambda: service.authorize(None, None),
            lambda: service.reject(None, None),
            lambda: service.revise(None, None),
            lambda: service.execute(authority),
            lambda: service.inspect("foreign-operation"),
            lambda: service.recover("foreign-operation"),
        )
        original_lock = service._state_lock
        original_bindings = service._bindings

        def call_all():
            failures = []
            with mock.patch.object(
                commits_module,
                "open_git_session_history",
                side_effect=AssertionError("foreign service reached history I/O"),
            ), mock.patch.object(
                commits_module,
                "prepare_identity_bound_story_commit_candidate",
                side_effect=AssertionError("foreign service reached filesystem I/O"),
            ), mock.patch.object(
                StoryCommitService,
                "_stage_git",
                autospec=True,
                side_effect=AssertionError("foreign service reached Git I/O"),
            ):
                for action in actions:
                    try:
                        action()
                    except BaseException as failure:
                        failures.append(
                            (
                                type(failure).__name__,
                                getattr(getattr(failure, "code", None), "value", None),
                                failure.__cause__ is None,
                            )
                        )
            return tuple(failures)

        service._state_lock = PoisonLock()
        service._bindings = PoisonBindings()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                thread_failures = executor.submit(call_all).result(timeout=10)
            context = multiprocessing.get_context("fork")
            child_results = context.Queue()

            def call_in_child() -> None:
                child_results.put(call_all())

            process = context.Process(target=call_in_child)
            try:
                process.start()
                process.join(timeout=10)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
                process_failures = child_results.get(timeout=3)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        finally:
            service._state_lock = original_lock
            service._bindings = original_bindings
        expected = (
            (
                "StoryCommitError",
                StoryCommitFailureCode.AUTHORITY_INVALID.value,
                True,
            ),
        ) * 7
        self.assertEqual(thread_failures, expected)
        self.assertEqual(process_failures, expected)
        self.assertFalse(authority._consumed)
        committed = service.execute(authority)
        self.assertIs(committed.state, StoryCommitState.COMMITTED)

        _root, _session_root, unbound, unbound_candidate = self.prepared(
            "unbound-service-compatibility"
        )
        unbound_authority = unbound.authorize(
            unbound_candidate,
            self.confirmation(unbound_candidate, suffix="unbound"),
        )
        self.assertIs(
            unbound.execute(unbound_authority).state,
            StoryCommitState.COMMITTED,
        )

    def test_recycled_numeric_ident_cannot_consume_story_commit_authority(
        self,
    ) -> None:
        _root, _session_root, service, candidate = self.prepared_bound(
            "recycled-authority-token"
        )
        authority = service.authorize(
            candidate,
            self.confirmation(candidate, suffix="recycled-token"),
        )
        creator_token = lock_module.current_thread_token()
        numeric_ident = threading.get_ident()
        successor_token = object()
        lock_module._THREAD_STATE.token = successor_token
        try:
            self.assertEqual(threading.get_ident(), numeric_ident)
            with mock.patch.object(
                commits_module,
                "open_git_session_history",
                side_effect=AssertionError("history I/O was attempted"),
            ) as history_io, mock.patch.object(
                StoryCommitService,
                "_stage_git",
                autospec=True,
            ) as stage_git, self.assertRaises(StoryCommitError) as caught:
                service.execute(authority)
            self.assertIs(
                caught.exception.code,
                StoryCommitFailureCode.AUTHORITY_INVALID,
            )
            self.assertFalse(authority._consumed)
            history_io.assert_not_called()
            stage_git.assert_not_called()
        finally:
            lock_module._THREAD_STATE.token = creator_token

        committed = service.execute(authority)
        self.assertIs(committed.state, StoryCommitState.COMMITTED)
        self.assertTrue(authority._consumed)
        with self.assertRaises(StoryCommitError) as caught:
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_CONSUMED)

    @staticmethod
    def _execute_error(service, authority):
        try:
            service.execute(authority)
        except StoryCommitError as error:
            return error
        raise AssertionError("cross-thread authority unexpectedly executed")

    def test_only_exact_commit_decision_authorizes_and_confirmation_cannot_replay(
        self,
    ) -> None:
        _root, _session_root, service, candidate = self.prepared("decision")
        for decision in (StoryCommitDecision.REJECT, StoryCommitDecision.REVISE):
            with self.subTest(decision=decision):
                with self.assertRaises(StoryCommitError) as caught:
                    service.authorize(
                        candidate,
                        self.confirmation(candidate, decision, decision.value),
                    )
                self.assertIs(
                    caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID
                )
        confirmation = self.confirmation(candidate, suffix="exact")
        authority = service.authorize(candidate, confirmation)
        self.assertEqual(
            authority._operation_id,
            commits_module._candidate_operation_id("commit", candidate.candidate_id),
        )
        with self.assertRaises(StoryCommitError) as caught:
            service.authorize(candidate, confirmation)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        mismatched = StoryCommitConfirmation(
            "confirmation:mismatch",
            candidate.candidate_id,
            "proposal:other",
            candidate.correlation_id,
            StoryCommitDecision.COMMIT,
        )
        with self.assertRaises(StoryCommitError) as caught:
            service.authorize(candidate, mismatched)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)

    def test_apply_identity_blocks_pre_running_replacement_and_marks_post_running_unknown(
        self,
    ) -> None:
        def prepared_bound(name):
            root, session_root, service, candidate = self.prepared_bound(name)
            authority = service.authorize(candidate, self.confirmation(candidate))
            return root, session_root, service, authority

        _root, session_root, service, authority = prepared_bound(
            "pre-running-root-replacement"
        )
        before_head = self.run_git(session_root, "rev-parse", "HEAD").stdout
        before_index = self.run_git(
            session_root,
            "diff",
            "--cached",
            "--name-only",
        ).stdout
        with self.replace_session_root(session_root), self.assertRaises(
            StoryCommitError
        ) as pre_running:
            service.execute(authority)
        self.assertIs(
            pre_running.exception.code,
            StoryCommitFailureCode.CANDIDATE_DRIFT,
        )
        self.assertEqual(self.run_git(session_root, "rev-parse", "HEAD").stdout, before_head)
        self.assertEqual(
            self.run_git(session_root, "diff", "--cached", "--name-only").stdout,
            before_index,
        )

        _root, session_root, service, authority = prepared_bound(
            "post-running-root-replacement"
        )
        before_head = self.run_git(session_root, "rev-parse", "HEAD").stdout
        before_index = self.run_git(
            session_root,
            "diff",
            "--cached",
            "--name-only",
        ).stdout
        original_stage = service._stage_git

        def replace_after_running(revalidation):
            with self.replace_session_root(session_root):
                return original_stage(revalidation)

        with mock.patch.object(
            StoryCommitService,
            "_stage_git",
            side_effect=replace_after_running,
        ), self.assertRaises(StoryCommitError) as post_running:
            service.execute(authority)
        self.assertIs(
            post_running.exception.code,
            StoryCommitFailureCode.OUTCOME_UNKNOWN,
        )
        self.assertEqual(self.run_git(session_root, "rev-parse", "HEAD").stdout, before_head)
        self.assertEqual(
            self.run_git(session_root, "diff", "--cached", "--name-only").stdout,
            before_index,
        )

    def test_generation_loss_obeys_pre_and_post_running_cutoffs(self) -> None:
        root, session_root, service, candidate = self.prepared_bound(
            "generation-loss-before-running"
        )
        authority = service.authorize(
            candidate,
            self.confirmation(candidate, suffix="generation-before"),
        )
        before_head = self.run_git(session_root, "rev-parse", "HEAD").stdout
        before_index = self.run_git(
            session_root, "diff", "--cached", "--name-only"
        ).stdout
        root_identity = os.stat(session_root)
        with self.replace_session_git_indirection(session_root):
            self.assertEqual(
                (os.stat(session_root).st_dev, os.stat(session_root).st_ino),
                (root_identity.st_dev, root_identity.st_ino),
            )
            with open_git_session_history(root, manifest(), "novel") as history:
                self.assertEqual(
                    history.require_semantic_ready().identity.worktree_root,
                    session_root,
                )
            with self.assertRaises(StoryCommitError) as caught:
                service.execute(authority)
        self.assertIs(
            caught.exception.code,
            StoryCommitFailureCode.RECOVERY_REQUIRED,
        )
        self.assertEqual(self.run_git(session_root, "rev-parse", "HEAD").stdout, before_head)
        self.assertEqual(
            self.run_git(session_root, "diff", "--cached", "--name-only").stdout,
            before_index,
        )
        with open_git_session_history(root, manifest(), "novel") as history:
            self.assertEqual(history.snapshot().commit_operations, ())

        root, session_root, service, candidate = self.prepared_bound(
            "generation-loss-after-running"
        )
        authority = service.authorize(
            candidate,
            self.confirmation(candidate, suffix="generation-after"),
        )
        original_write = GitSessionHistory.write_commit_operation
        replacement = None

        def replace_after_running(history, operation):
            nonlocal replacement
            result = original_write(history, operation)
            if (
                operation.state is GitSessionHistoryState.RUNNING
                and replacement is None
            ):
                replacement = self.replace_session_git_indirection(session_root)
                replacement.__enter__()
            return result

        try:
            with mock.patch.object(
                GitSessionHistory,
                "write_commit_operation",
                autospec=True,
                side_effect=replace_after_running,
            ), self.assertRaises(StoryCommitError) as caught:
                service.execute(authority)
        finally:
            if replacement is not None:
                replacement.__exit__(None, None, None)
        self.assertIs(
            caught.exception.code,
            StoryCommitFailureCode.OUTCOME_UNKNOWN,
        )
        with open_git_session_history(root, manifest(), "novel") as history:
            operations = history.snapshot().commit_operations
        self.assertEqual(len(operations), 1)
        self.assertIs(
            operations[0].state,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
        )

    def test_staged_topology_loss_is_always_post_running_uncertainty(self) -> None:
        original = StoryCommitService._require_staged_candidate
        for ordinal in range(1, 4):
            with self.subTest(ordinal=ordinal):
                root, session_root, service, candidate = self.prepared_bound(
                    f"staged-topology-cutoff-{ordinal}"
                )
                authority = service.authorize(
                    candidate,
                    self.confirmation(candidate, suffix=f"staged-{ordinal}"),
                )
                calls = 0

                def fail_once(current, *args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == ordinal:
                        raise StoryCommitError(
                            StoryCommitFailureCode.CANDIDATE_DRIFT
                        )
                    return original(current, *args, **kwargs)

                with mock.patch.object(
                    StoryCommitService,
                    "_require_staged_candidate",
                    autospec=True,
                    side_effect=fail_once,
                ), self.assertRaises(StoryCommitError) as caught:
                    service.execute(authority)
                self.assertIs(
                    caught.exception.code,
                    StoryCommitFailureCode.OUTCOME_UNKNOWN,
                )
                self.run_git(
                    session_root,
                    "reset",
                    "--",
                    *(item.path for item in candidate.paths),
                )
                with open_git_session_history(root, manifest(), "novel") as history:
                    operations = history.snapshot().commit_operations
                self.assertEqual(len(operations), 1)
                self.assertIs(
                    operations[0].state,
                    GitSessionHistoryState.OUTCOME_UNKNOWN,
                )

    def test_bound_commit_reuses_one_guarded_repository_for_every_capture(
        self,
    ) -> None:
        root, session_root, evidence = self.applied_replace("bound-captures")
        observed = os.stat(session_root)
        artifact_binding = compose_session_artifact_binding(root, "novel")
        service = commits_module.create_identity_bound_story_commit_service(
            root,
            manifest(),
            "novel",
            (observed.st_dev, observed.st_ino),
            artifact_binding._generation_identity,
            artifact_repository_access=(
                artifact_binding.open_story_commit_artifact_repository
            ),
        )
        captures = []
        original_capture = WritingArtifactRepository.capture_complete_state
        original_generation_ready = GitSessionHistory.require_generation_ready

        def capture(repository):
            value = original_capture(repository)
            captures.append((id(repository), value))
            return value

        with mock.patch.object(
            WritingArtifactRepository,
            "__init__",
            side_effect=AssertionError("bound capture created an independent repository"),
        ), mock.patch.object(
            WritingArtifactRepository,
            "capture_complete_state",
            autospec=True,
            side_effect=capture,
        ), mock.patch.object(
            GitSessionHistory,
            "require_generation_ready",
            autospec=True,
            side_effect=original_generation_ready,
        ) as generation_ready, mock.patch.object(
            initialization_module,
            "lock_git_repository",
            side_effect=AssertionError("borrowed history opened a second Git lease"),
        ) as second_lock:
            candidate = service.prepare(evidence, "Bound capture", ("impact",))
            authority = service.authorize(
                candidate,
                self.confirmation(candidate, suffix="bound-capture"),
            )
            result = service.execute(authority)
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        self.assertEqual(len(captures), 3)
        self.assertEqual(len({repository for repository, _value in captures}), 1)
        self.assertEqual(captures[0][1], captures[1][1])
        self.assertEqual(captures[1][1], captures[2][1])
        self.assertEqual(generation_ready.call_count, 6)
        second_lock.assert_not_called()

    def test_borrowed_story_commit_generation_loss_overrides_body_failure(self) -> None:
        root, _session_root, _evidence = self.applied_replace(
            "borrowed-generation-loss"
        )
        artifact_binding = compose_session_artifact_binding(root, "novel")
        body_failure = RuntimeError("artifact capture failed")
        validations = 0

        def validate_ready():
            nonlocal validations
            validations += 1
            if validations == 1:
                return artifact_binding.resources
            return None

        with self.assertRaises(SessionArtifactBindingError) as caught:
            with artifact_binding.open_story_commit_artifact_repository(
                validate_ready,
                expected_generation_identity=(
                    artifact_binding._generation_identity
                ),
            ):
                raise body_failure
        self.assertIsNot(caught.exception, body_failure)
        self.assertEqual(validations, 2)
        self.assertEqual(artifact_binding._operation_tokens, {})

    def test_bound_capture_loss_before_and_after_each_phase_prevents_git_entry(
        self,
    ) -> None:
        for phase in ("prepare", "authorize", "execute"):
            for position in ("before", "after"):
                with self.subTest(phase=phase, position=position):
                    root, session_root, evidence = self.applied_replace(
                        f"bound-{phase}-{position}"
                    )
                    observed = os.stat(session_root)
                    artifact_binding = compose_session_artifact_binding(root, "novel")

                    @contextmanager
                    def failing_access(
                        validate,
                        *,
                        expected_generation_identity,
                    ):
                        if position == "before":
                            raise SessionArtifactBindingError("binding lost")
                        with artifact_binding.open_story_commit_artifact_repository(
                            validate,
                            expected_generation_identity=(
                                expected_generation_identity
                            ),
                        ) as repository:
                            yield repository
                            raise SessionArtifactBindingError("binding lost")

                    normal_access = (
                        artifact_binding.open_story_commit_artifact_repository
                    )
                    service = commits_module.create_identity_bound_story_commit_service(
                        root,
                        manifest(),
                        "novel",
                        (observed.st_dev, observed.st_ino),
                        artifact_binding._generation_identity,
                        artifact_repository_access=(
                            failing_access if phase == "prepare" else normal_access
                        ),
                    )
                    candidate = authority = None
                    if phase != "prepare":
                        candidate = service.prepare(
                            evidence,
                            f"Bound {phase}",
                            ("impact",),
                        )
                        if phase == "execute":
                            authority = service.authorize(
                                candidate,
                                self.confirmation(
                                    candidate,
                                    suffix=f"{phase}-{position}",
                                ),
                            )
                        service._artifact_repository_access = (
                            lambda validate_ready: failing_access(
                                validate_ready,
                                expected_generation_identity=(
                                    artifact_binding._generation_identity
                                ),
                            )
                        )

                    with self.assertRaises(StoryCommitError) as caught:
                        if phase == "prepare":
                            service.prepare(evidence, "Bound prepare", ("impact",))
                        elif phase == "authorize":
                            assert candidate is not None
                            service.authorize(
                                candidate,
                                self.confirmation(
                                    candidate,
                                    suffix=f"{phase}-{position}",
                                ),
                            )
                        else:
                            assert authority is not None
                            service.execute(authority)
                    self.assertIs(
                        caught.exception.code,
                        StoryCommitFailureCode.CANDIDATE_DRIFT,
                    )
                    self.assertEqual(
                        self.run_git(
                            session_root,
                            "diff",
                            "--cached",
                            "--name-only",
                        ).stdout,
                        b"",
                    )
                    with open_git_session_history(
                        root,
                        manifest(),
                        "novel",
                    ) as history:
                        snapshot = history.snapshot()
                    self.assertEqual(snapshot.commit_operations, ())
                    self.assertEqual(
                        len(snapshot.candidates),
                        0 if phase == "prepare" else 1,
                    )

    def test_exact_staged_recovery_is_inspectable_and_transitions_only_running(
        self,
    ) -> None:
        for initial_state, expected_recovered in (
            (GitSessionHistoryState.RUNNING, GitSessionHistoryState.STAGED),
            (GitSessionHistoryState.STAGED, GitSessionHistoryState.STAGED),
            (
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
            ),
        ):
            with self.subTest(initial_state=initial_state):
                root, _session_root, service, candidate = self.prepared_bound(
                    f"staged-recovery-{initial_state.value}"
                )
                binding = service._bindings[candidate.candidate_id]
                revalidation = service._revalidate_candidate(binding)
                operation = GitSessionCommitOperationRecord(
                    session_id="novel",
                    operation_id=commits_module._candidate_operation_id(
                        "commit", candidate.candidate_id
                    ),
                    candidate_id=candidate.candidate_id,
                    correlation_id=candidate.correlation_id,
                    state=GitSessionHistoryState.RUNNING,
                    sequence=0,
                    expected_branch_tip=candidate.branch_tip,
                )
                with open_git_session_history(root, manifest(), "novel") as history:
                    history.write_commit_operation(operation)
                    if initial_state is not GitSessionHistoryState.RUNNING:
                        operation = advance_commit_operation(
                            operation,
                            initial_state,
                        )
                        history.write_commit_operation(operation)
                service._stage_git(revalidation)

                with mock.patch.object(
                    commits_module,
                    "advance_commit_operation",
                    wraps=commits_module.advance_commit_operation,
                ) as inspect_advance, mock.patch.object(
                    GitSessionRecoveryHistory,
                    "write_commit_operation",
                    autospec=True,
                ) as inspect_write:
                    inspected = service.inspect(operation.operation_id)
                self.assertIs(inspected.state, StoryCommitState(initial_state.value))
                inspect_advance.assert_not_called()
                inspect_write.assert_not_called()

                original_topology = (
                    GitSessionRecoveryHistory.require_recovery_topology
                )
                original_write = GitSessionRecoveryHistory.write_commit_operation
                with mock.patch.object(
                    GitSessionRecoveryHistory,
                    "require_recovery_topology",
                    autospec=True,
                    side_effect=lambda current: original_topology(current),
                ) as topology, mock.patch.object(
                    GitSessionRecoveryHistory,
                    "write_commit_operation",
                    autospec=True,
                    side_effect=lambda current, value: original_write(current, value),
                ) as recovery_write, mock.patch.object(
                    commits_module,
                    "advance_commit_operation",
                    wraps=commits_module.advance_commit_operation,
                ) as recovery_advance:
                    recovered = service.recover(operation.operation_id)
                self.assertIs(
                    recovered.state,
                    StoryCommitState(expected_recovered.value),
                )
                self.assertGreaterEqual(topology.call_count, 2)
                if initial_state is GitSessionHistoryState.RUNNING:
                    self.assertEqual(recovery_advance.call_count, 1)
                    self.assertEqual(recovery_write.call_count, 1)
                else:
                    recovery_advance.assert_not_called()
                    recovery_write.assert_not_called()

    def test_staged_recovery_rejects_every_nonexact_topology_without_mutation(
        self,
    ) -> None:
        for variant in (
            "wrong_tree",
            "wrong_path",
            "unstaged_content",
            "untracked",
            "unmerged",
            "external_marker",
            "branch",
            "internal_ref",
            "main_status",
        ):
            with self.subTest(variant=variant):
                root, session_root, service, candidate = self.prepared_bound(
                    f"staged-negative-{variant}"
                )
                binding = service._bindings[candidate.candidate_id]
                revalidation = service._revalidate_candidate(binding)
                operation = GitSessionCommitOperationRecord(
                    session_id="novel",
                    operation_id=commits_module._candidate_operation_id(
                        "commit", candidate.candidate_id
                    ),
                    candidate_id=candidate.candidate_id,
                    correlation_id=candidate.correlation_id,
                    state=GitSessionHistoryState.RUNNING,
                    sequence=0,
                    expected_branch_tip=candidate.branch_tip,
                )
                operation = advance_commit_operation(
                    operation,
                    GitSessionHistoryState.STAGED,
                )
                with open_git_session_history(root, manifest(), "novel") as history:
                    history.write_commit_operation(
                        replace(
                            operation,
                            state=GitSessionHistoryState.RUNNING,
                            sequence=0,
                        )
                    )
                    history.write_commit_operation(operation)
                service._stage_git(revalidation)
                path = candidate.paths[0].path

                if variant == "wrong_tree":
                    (session_root / path).write_bytes(b"wrong staged tree\n")
                    self.run_git(session_root, "add", "--", path)
                elif variant == "wrong_path":
                    other = "core_documents/character_rules.md"
                    (session_root / other).write_bytes(b"extra staged path\n")
                    self.run_git(session_root, "add", "--", other)
                elif variant == "unstaged_content":
                    (session_root / path).write_bytes(b"unstaged content drift\n")
                elif variant == "untracked":
                    (session_root / "untracked-private.txt").write_bytes(b"untracked\n")
                elif variant == "unmerged":
                    first = self.run_git(
                        session_root,
                        "hash-object",
                        "-w",
                        "--stdin",
                        input_bytes=b"conflict first\n",
                    ).stdout.strip().decode()
                    second = self.run_git(
                        session_root,
                        "hash-object",
                        "-w",
                        "--stdin",
                        input_bytes=b"conflict second\n",
                    ).stdout.strip().decode()
                    zero = "0" * len(first)
                    self.run_git(
                        session_root,
                        "update-index",
                        "--index-info",
                        input_bytes=(
                            f"0 {zero}\t{path}\n"
                            f"100644 {first} 1\t{path}\n"
                            f"100644 {first} 2\t{path}\n"
                            f"100644 {second} 3\t{path}\n"
                        ).encode(),
                    )
                elif variant == "external_marker":
                    marker = Path(
                        self.run_git(
                            session_root,
                            "rev-parse",
                            "--git-path",
                            "MERGE_HEAD",
                        ).stdout.strip().decode()
                    )
                    if not marker.is_absolute():
                        marker = session_root / marker
                    marker.write_text(candidate.branch_tip + "\n", encoding="ascii")
                elif variant == "branch":
                    self.run_git(session_root, "switch", "-c", "story/session/foreign")
                elif variant == "internal_ref":
                    self.run_git(root, "update-ref", "-d", binding.internal_ref)
                elif variant == "main_status":
                    (root / path).write_bytes(b"tracked main-worktree drift\n")

                self.assert_terminal_reads_do_not_mutate(
                    service,
                    operation.operation_id,
                    failure_code=StoryCommitFailureCode.RECOVERY_REQUIRED,
                )

    def test_initial_running_history_append_failure_is_predurable_and_never_enters_git(
        self,
    ) -> None:
        for history_code, expected_code in (
            (
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
                StoryCommitFailureCode.RECOVERY_REQUIRED,
            ),
            (
                GitSessionHistoryFailureCode.PERSISTENCE_FAILED,
                StoryCommitFailureCode.HISTORY_FAILED,
            ),
        ):
            with self.subTest(history_code=history_code):
                root, session_root, service, candidate = self.prepared_bound(
                    f"initial-running-history-{history_code.value}"
                )
                authority = service.authorize(
                    candidate,
                    self.confirmation(candidate, suffix=history_code.value),
                )
                before_head = self.run_git(session_root, "rev-parse", "HEAD").stdout
                before_index = self.run_git(
                    session_root,
                    "diff",
                    "--cached",
                    "--name-only",
                ).stdout
                failure = GitSessionHistoryError(
                    history_code,
                    GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
                )

                with mock.patch.object(
                    GitSessionHistory,
                    "write_commit_operation",
                    autospec=True,
                    side_effect=failure,
                ), mock.patch.object(
                    StoryCommitService,
                    "_stage_git",
                    autospec=True,
                ) as stage_git, self.assertRaises(StoryCommitError) as caught:
                    service.execute(authority)

                self.assertIs(caught.exception.code, expected_code)
                stage_git.assert_not_called()
                self.assertEqual(
                    self.run_git(session_root, "rev-parse", "HEAD").stdout,
                    before_head,
                )
                self.assertEqual(
                    self.run_git(
                        session_root,
                        "diff",
                        "--cached",
                        "--name-only",
                    ).stdout,
                    before_index,
                )
                with open_git_session_history(root, manifest(), "novel") as history:
                    self.assertEqual(history.snapshot().commit_operations, ())

    def test_final_committed_history_write_failure_durably_preserves_unknown(
        self,
    ) -> None:
        root, session_root, service, candidate = self.prepared_bound(
            "final-history-write-failure"
        )
        authority = service.authorize(
            candidate,
            self.confirmation(candidate, suffix="final-history-write"),
        )
        original = GitSessionHistory.write_commit_operation
        calls = 0

        def fail_final_write(history, record):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise GitSessionHistoryError(
                    GitSessionHistoryFailureCode.PERSISTENCE_FAILED,
                    GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
                )
            return original(history, record)

        with mock.patch.object(
            GitSessionHistory,
            "write_commit_operation",
            autospec=True,
            side_effect=fail_final_write,
        ), self.assertRaises(StoryCommitError) as caught:
            service.execute(authority)
        self.assertIs(
            caught.exception.code,
            StoryCommitFailureCode.OUTCOME_UNKNOWN,
        )
        self.assertEqual(
            self.run_git(session_root, "diff", "--cached", "--name-only").stdout,
            b"",
        )
        self.assertEqual(
            self.run_git(session_root, "status", "--porcelain=v1").stdout,
            b"",
        )
        with open_git_session_history(root, manifest(), "novel") as history:
            operations = history.snapshot().commit_operations
        self.assertEqual(len(operations), 1)
        self.assertIs(
            operations[0].state,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
        )
        self.assertIsNone(operations[0].commit_id)

    def test_inspect_and_recover_revalidate_apply_root_without_state_advancement(
        self,
    ) -> None:
        for action_name, guard_ordinals in (
            ("inspect", range(1, 4)),
            ("recover", range(1, 7)),
        ):
            with self.subTest(action=action_name):
                root, session_root, service, candidate = self.prepared_bound(
                    f"{action_name}-root-revalidation"
                )
                result = service.reject(
                    candidate,
                    self.confirmation(
                        candidate,
                        StoryCommitDecision.REJECT,
                        action_name,
                    ),
                )
                with open_git_session_history(root, manifest(), "novel") as history:
                    before = history.read_commit_operation(result.operation_id)

                for ordinal in guard_ordinals:
                    with self.subTest(action=action_name, guard_ordinal=ordinal):
                        original_guard = service._require_recovery_bound_session_root
                        replacement = None
                        calls = 0

                        def replace_before_guard(_service):
                            nonlocal calls, replacement
                            calls += 1
                            if calls == ordinal:
                                replacement = self.replace_session_root(session_root)
                                replacement.__enter__()
                            return original_guard()

                        try:
                            with mock.patch.object(
                                StoryCommitService,
                                "_require_recovery_bound_session_root",
                                autospec=True,
                                side_effect=replace_before_guard,
                            ), mock.patch.object(
                                commits_module,
                                "advance_commit_operation",
                                wraps=commits_module.advance_commit_operation,
                            ) as advance, self.assertRaises(StoryCommitError) as caught:
                                getattr(service, action_name)(result.operation_id)
                            self.assertIs(
                                caught.exception.code,
                                StoryCommitFailureCode.RECOVERY_REQUIRED,
                            )
                            advance.assert_not_called()
                        finally:
                            if replacement is not None:
                                replacement.__exit__(None, None, None)

                        with open_git_session_history(
                            root, manifest(), "novel"
                        ) as history:
                            self.assertEqual(
                                history.read_commit_operation(result.operation_id),
                                before,
                            )

    def test_inspect_and_recover_generation_loss_never_advances_state(self) -> None:
        root, session_root, service, candidate = self.prepared_bound(
            "inspect-recover-generation-revalidation"
        )
        result = service.reject(
            candidate,
            self.confirmation(
                candidate,
                StoryCommitDecision.REJECT,
                "inspect-recover-generation",
            ),
        )
        with open_git_session_history(root, manifest(), "novel") as history:
            before = history.read_commit_operation(result.operation_id)

        with self.replace_session_git_indirection(session_root):
            with open_git_session_history(root, manifest(), "novel") as history:
                self.assertEqual(
                    history.require_semantic_ready().identity.worktree_root,
                    session_root,
                )
            with mock.patch.object(
                GitSessionHistory,
                "write_commit_operation",
                autospec=True,
            ) as ready_write, mock.patch.object(
                GitSessionRecoveryHistory,
                "write_commit_operation",
                autospec=True,
            ) as recovery_write, mock.patch.object(
                commits_module,
                "advance_commit_operation",
                wraps=commits_module.advance_commit_operation,
            ) as advance:
                for action_name in ("inspect", "recover"):
                    with self.subTest(action=action_name), self.assertRaises(
                        StoryCommitError
                    ) as caught:
                        getattr(service, action_name)(result.operation_id)
                    self.assertIs(
                        caught.exception.code,
                        StoryCommitFailureCode.RECOVERY_REQUIRED,
                    )
                advance.assert_not_called()
                ready_write.assert_not_called()
                recovery_write.assert_not_called()

        with open_git_session_history(root, manifest(), "novel") as history:
            self.assertEqual(
                history.read_commit_operation(result.operation_id),
                before,
            )

    def test_reject_and_revise_persist_result_without_git_or_artifact_mutation(
        self,
    ) -> None:
        for decision in (StoryCommitDecision.REJECT, StoryCommitDecision.REVISE):
            with self.subTest(decision=decision):
                root, session_root, service, candidate = self.prepared(
                    f"decision-{decision.value}"
                )
                before = self.repository_snapshot(root)
                staged_before = self.run_git(
                    session_root, "diff", "--cached", "--name-only"
                ).stdout
                artifact_before = (
                    session_root / "core_documents/global_outline.md"
                ).read_bytes()
                action = (
                    service.reject
                    if decision is StoryCommitDecision.REJECT
                    else service.revise
                )
                result = action(
                    candidate, self.confirmation(candidate, decision, decision.value)
                )
                self.assertEqual(
                    result.operation_id,
                    commits_module._candidate_operation_id(
                        decision.value,
                        candidate.candidate_id,
                    ),
                )
                self.assertIs(result.state, StoryCommitState.REJECTED)
                self.assertIs(result.recovery_action, StoryCommitRecoveryAction.NONE)
                self.assertEqual(self.repository_snapshot(root), before)
                self.assertEqual(
                    self.run_git(
                        session_root, "diff", "--cached", "--name-only"
                    ).stdout,
                    staged_before,
                )
                self.assertEqual(
                    (session_root / "core_documents/global_outline.md").read_bytes(),
                    artifact_before,
                )
                self.assertEqual(service.inspect(result.operation_id), result)
                if decision is StoryCommitDecision.REVISE:
                    with self.assertRaises(StoryCommitError) as revised:
                        service.authorize(
                            candidate,
                            self.confirmation(
                                candidate,
                                suffix="revised-candidate",
                            ),
                        )
                    self.assertIs(
                        revised.exception.code,
                        StoryCommitFailureCode.AUTHORITY_INVALID,
                    )

        for decision in (StoryCommitDecision.REJECT, StoryCommitDecision.REVISE):
            for timing in ("before_write", "after_write"):
                with self.subTest(decision=decision, generation_loss=timing):
                    root, session_root, service, candidate = self.prepared_bound(
                        f"decision-generation-{decision.value}-{timing}"
                    )
                    action = (
                        service.reject
                        if decision is StoryCommitDecision.REJECT
                        else service.revise
                    )
                    operation_id = commits_module._candidate_operation_id(
                        decision.value,
                        candidate.candidate_id,
                    )
                    original_write = GitSessionHistory.write_commit_operation
                    replacement = None

                    def replace_after_write(history, operation):
                        nonlocal replacement
                        result = original_write(history, operation)
                        if (
                            operation.operation_id == operation_id
                            and replacement is None
                        ):
                            replacement = self.replace_session_git_indirection(
                                session_root
                            )
                            replacement.__enter__()
                        return result

                    try:
                        if timing == "before_write":
                            replacement = self.replace_session_git_indirection(
                                session_root
                            )
                            replacement.__enter__()
                            patcher = mock.patch.object(
                                GitSessionHistory,
                                "write_commit_operation",
                                autospec=True,
                                wraps=original_write,
                            )
                        else:
                            patcher = mock.patch.object(
                                GitSessionHistory,
                                "write_commit_operation",
                                autospec=True,
                                side_effect=replace_after_write,
                            )
                        with patcher as write, self.assertRaises(
                            StoryCommitError
                        ) as caught:
                            action(
                                candidate,
                                self.confirmation(
                                    candidate,
                                    decision,
                                    f"generation-{decision.value}-{timing}",
                                ),
                            )
                    finally:
                        if replacement is not None:
                            replacement.__exit__(None, None, None)
                    self.assertIs(
                        caught.exception.code,
                        StoryCommitFailureCode.RECOVERY_REQUIRED,
                    )
                    if timing == "before_write":
                        write.assert_not_called()
                        with open_git_session_history(
                            root, manifest(), "novel"
                        ) as history:
                            with self.assertRaises(GitSessionHistoryError) as missing:
                                history.read_commit_operation(operation_id)
                        self.assertIs(
                            missing.exception.code,
                            GitSessionHistoryFailureCode.RECORD_NOT_FOUND,
                        )
                        continue
                    with open_git_session_history(
                        root, manifest(), "novel"
                    ) as history:
                        persisted = history.read_commit_operation(operation_id)
                    self.assertIs(
                        persisted.state,
                        GitSessionHistoryState.REJECTED,
                    )

    def test_real_sha1_and_sha256_commits_have_exact_tree_parent_identity_and_invariants(
        self,
    ) -> None:
        for algorithm in ("sha1", "sha256"):
            with self.subTest(algorithm=algorithm):
                root, session_root, service, candidate = self.prepared(
                    f"{algorithm} root with spaces 한글", object_format=algorithm
                )
                main_before = self.repository_snapshot(root)
                main_head_before = self.run_git(root, "rev-parse", "HEAD").stdout
                internal_before = self.run_git(
                    root,
                    "rev-parse",
                    "refs/story-writing-agents/runtime/sessions/novel",
                ).stdout
                unrelated = session_root / "unrelated private bytes.txt"
                unrelated.write_bytes(b"preserve unrelated private bytes\n")
                result = service.execute(
                    service.authorize(
                        candidate, self.confirmation(candidate, suffix=algorithm)
                    )
                )
                self.assertIs(result.state, StoryCommitState.COMMITTED)
                self.assertIsNotNone(result.commit_id)
                self.assertEqual(
                    len(result.commit_id or ""), 40 if algorithm == "sha1" else 64
                )
                commit = self.run_git(
                    session_root, "cat-file", "commit", result.commit_id or ""
                ).stdout
                headers, message = commit.split(b"\n\n", 1)
                self.assertIn(
                    f"tree {candidate.expected_tree}\n".encode(), headers + b"\n"
                )
                self.assertEqual(
                    [
                        line
                        for line in headers.splitlines()
                        if line.startswith(b"parent ")
                    ],
                    [f"parent {candidate.branch_tip}".encode()],
                )
                self.assertIn(
                    b"author Session Author <session-author@example.invalid>", headers
                )
                self.assertIn(
                    b"committer Session Committer <session-committer@example.invalid>",
                    headers,
                )
                self.assertEqual(message, "Commit exact 장면 🌙\n".encode())
                self.assertEqual(
                    self.run_git(session_root, "rev-parse", "HEAD^{tree}")
                    .stdout.strip()
                    .decode(),
                    candidate.expected_tree,
                )
                self.assertEqual(
                    self.run_git(root, "rev-parse", "HEAD").stdout, main_head_before
                )
                self.assertEqual(
                    self.run_git(
                        root,
                        "status",
                        "--porcelain=v2",
                        "-z",
                        "--untracked-files=all",
                    ).stdout,
                    main_before[4],
                )
                self.assertEqual(
                    self.run_git(
                        root,
                        "rev-parse",
                        "refs/story-writing-agents/runtime/sessions/novel",
                    ).stdout,
                    internal_before,
                )
                self.assertEqual(
                    unrelated.read_bytes(), b"preserve unrelated private bytes\n"
                )
                self.assertEqual((root / ".git" / "index").read_bytes(), main_before[5])

    def test_restart_recovers_correlated_commit_after_branch_cas(self) -> None:
        root, _session_root, service, candidate = self.prepared("restart")
        authority = service.authorize(
            candidate, self.confirmation(candidate, suffix="restart")
        )
        with (
            mock.patch.object(
                StoryCommitService,
                "_verify_invariants",
                side_effect=StoryCommitError(StoryCommitFailureCode.OUTCOME_UNKNOWN),
            ),
            self.assertRaises(StoryCommitError) as caught,
        ):
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.OUTCOME_UNKNOWN)
        with open_git_session_history(root, manifest(), "novel") as history:
            operation_id = history.inspect().commit_operations[0][0]
        restarted = StoryCommitService(root, manifest(), "novel")
        recovered = restarted.recover(operation_id)
        self.assertIs(recovered.state, StoryCommitState.COMMITTED)
        self.assertIsNotNone(recovered.commit_id)
        self.assertEqual(restarted.inspect(operation_id), recovered)

    def test_historical_committed_remains_read_only_after_later_commit(self) -> None:
        for algorithm in ("sha1", "sha256"):
            with self.subTest(algorithm=algorithm):
                root, session_root, evidence = self.applied_replace(
                    f"historical-committed-{algorithm}",
                    object_format=algorithm,
                )
                first_service = StoryCommitService(root, manifest(), "novel")
                first_candidate = first_service.prepare(
                    evidence,
                    "Commit generation A",
                    ("generation A",),
                )
                first = first_service.execute(
                    first_service.authorize(
                        first_candidate,
                        self.confirmation(first_candidate, suffix=f"A-{algorithm}"),
                    )
                )
                followup = self.apply_followup(
                    session_root,
                    suffix=f"B-{algorithm}",
                )
                second_service = StoryCommitService(root, manifest(), "novel")
                second_candidate = second_service.prepare(
                    followup,
                    "Commit generation B",
                    ("generation B",),
                )
                second = second_service.execute(
                    second_service.authorize(
                        second_candidate,
                        self.confirmation(second_candidate, suffix=f"B-{algorithm}"),
                    )
                )
                self.assertIs(first.state, StoryCommitState.COMMITTED)
                self.assertIs(second.state, StoryCommitState.COMMITTED)
                self.assertNotEqual(first.commit_id, second.commit_id)
                self.assertEqual(
                    self.run_git(session_root, "rev-parse", "HEAD")
                    .stdout.strip()
                    .decode(),
                    second.commit_id,
                )
                self.assert_terminal_reads_do_not_mutate(
                    StoryCommitService(root, manifest(), "novel"),
                    first.operation_id,
                    expected=first,
                )

    def test_terminal_committed_reset_away_fails_closed_without_history_mutation(
        self,
    ) -> None:
        root, session_root, service, candidate = self.prepared(
            "terminal-reset-away"
        )
        committed = service.execute(
            service.authorize(
                candidate,
                self.confirmation(candidate, suffix="reset-away"),
            )
        )
        self.run_git(session_root, "reset", "--hard", candidate.branch_tip)
        self.assertNotEqual(
            self.run_git(session_root, "rev-parse", "HEAD").stdout.strip().decode(),
            committed.commit_id,
        )
        self.assert_terminal_reads_do_not_mutate(
            StoryCommitService(root, manifest(), "novel"),
            committed.operation_id,
            failure_code=StoryCommitFailureCode.RECOVERY_REQUIRED,
        )

    def test_terminal_committed_candidate_mismatch_fails_closed_without_history_mutation(
        self,
    ) -> None:
        root, session_root, service, candidate = self.prepared(
            "terminal-candidate-mismatch"
        )
        committed = service.execute(
            service.authorize(
                candidate,
                self.confirmation(candidate, suffix="candidate-mismatch"),
            )
        )
        self.assertEqual(
            self.run_git(session_root, "rev-parse", "HEAD").stdout.strip().decode(),
            committed.commit_id,
        )
        mismatched = replace(candidate, proposed_message="Mismatched candidate")
        with mock.patch.object(
            commits_module,
            "load_story_commit_candidate",
            return_value=mismatched,
        ):
            self.assert_terminal_reads_do_not_mutate(
                StoryCommitService(root, manifest(), "novel"),
                committed.operation_id,
                failure_code=StoryCommitFailureCode.RECOVERY_REQUIRED,
            )

    def test_terminal_operation_candidate_cross_links_fail_without_mutation(
        self,
    ) -> None:
        root, _session_root, service, candidate = self.prepared(
            "terminal-operation-candidate-cross-links"
        )
        rejected = service.reject(
            candidate,
            self.confirmation(
                candidate,
                StoryCommitDecision.REJECT,
                "cross-links",
            ),
        )
        variants = (
            replace(
                candidate,
                session_id="foreign",
                branch="story/session/foreign",
            ),
            replace(candidate, candidate_id="0" * 64),
            replace(candidate, correlation_id="foreign-correlation"),
            replace(candidate, branch_tip="0" * len(candidate.branch_tip)),
        )
        for mismatched in variants:
            with self.subTest(mismatched=mismatched), mock.patch.object(
                commits_module,
                "load_story_commit_candidate",
                return_value=mismatched,
            ):
                self.assert_terminal_reads_do_not_mutate(
                    StoryCommitService(root, manifest(), "novel"),
                    rejected.operation_id,
                    failure_code=StoryCommitFailureCode.RECOVERY_REQUIRED,
                )

    def test_terminal_operation_repointed_to_second_valid_candidate_fails_without_mutation(
        self,
    ) -> None:
        root, _session_root, evidence = self.applied_replace(
            "terminal-operation-repoint"
        )
        first_service = StoryCommitService(root, manifest(), "novel")
        first_candidate = first_service.prepare(
            evidence,
            "Reject candidate A",
            ("candidate A",),
        )
        rejected = first_service.reject(
            first_candidate,
            self.confirmation(
                first_candidate,
                StoryCommitDecision.REJECT,
                "candidate-A",
            ),
        )
        second_candidate = StoryCommitService(root, manifest(), "novel").prepare(
            evidence,
            "Prepare candidate B",
            ("candidate B",),
        )
        self.assertNotEqual(first_candidate.candidate_id, second_candidate.candidate_id)
        self.assertEqual(first_candidate.session_id, second_candidate.session_id)
        self.assertEqual(first_candidate.correlation_id, second_candidate.correlation_id)
        self.assertEqual(first_candidate.branch_tip, second_candidate.branch_tip)

        with open_git_session_history(root, manifest(), "novel") as history:
            original = history.read_commit_operation(rejected.operation_id)
            candidate_ids = history.inspect().candidate_ids
        self.assertIn(first_candidate.candidate_id, candidate_ids)
        self.assertIn(second_candidate.candidate_id, candidate_ids)
        repointed = replace(original, candidate_id=second_candidate.candidate_id)
        operation_path = (
            root
            / ".git"
            / GIT_SESSION_HISTORY_DIRECTORY
            / "novel"
            / "commit-operations"
            / f"{rejected.operation_id}.json"
        )
        operation_path.write_bytes(encode_commit_operation(repointed))
        corrupted_payload = operation_path.read_bytes()

        self.assert_terminal_reads_do_not_mutate(
            StoryCommitService(root, manifest(), "novel"),
            rejected.operation_id,
            failure_code=StoryCommitFailureCode.RECOVERY_REQUIRED,
        )
        self.assertEqual(operation_path.read_bytes(), corrupted_payload)

    def test_historical_rejected_remains_read_only_after_later_commit(self) -> None:
        root, session_root, evidence = self.applied_replace("historical-rejected")
        first_service = StoryCommitService(root, manifest(), "novel")
        rejected_candidate = first_service.prepare(
            evidence,
            "Reject generation A",
            ("generation A",),
        )
        rejected = first_service.reject(
            rejected_candidate,
            self.confirmation(
                rejected_candidate,
                StoryCommitDecision.REJECT,
                "reject-A",
            ),
        )
        second_service = StoryCommitService(root, manifest(), "novel")
        second_candidate = second_service.prepare(
            evidence,
            "Commit generation B",
            ("generation B",),
        )
        committed = second_service.execute(
            second_service.authorize(
                second_candidate,
                self.confirmation(second_candidate, suffix="commit-B"),
            )
        )
        self.assertIs(rejected.state, StoryCommitState.REJECTED)
        self.assertIs(committed.state, StoryCommitState.COMMITTED)
        self.assertEqual(
            self.run_git(session_root, "rev-parse", "HEAD").stdout.strip().decode(),
            committed.commit_id,
        )
        self.assert_terminal_reads_do_not_mutate(
            StoryCommitService(root, manifest(), "novel"),
            rejected.operation_id,
            expected=rejected,
        )


if __name__ == "__main__":
    unittest.main()
