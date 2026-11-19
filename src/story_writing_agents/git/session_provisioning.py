"""Restart-safe Git session provisioning and journal-driven publication."""

from __future__ import annotations

import os
import stat
import time
import uuid
from pathlib import Path

from .contracts import GitBaselineManifest, GitEffectiveIdentity
from .session_contracts import (
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionProvisioningJournal,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from .session_resource_files import open_bound_directory, path_lexists
from .session_resource_inspection import ResourceFacts, SessionResourceInspector
from .session_resource_repository import SessionResourceRepository
from .session_resource_values import (
    parse_identity,
    parse_object_id,
    recovery,
    resource_error,
    seed_payload,
    unknown,
)


_ZERO_BY_LENGTH = {40: "0" * 40, 64: "0" * 64}

class SessionResourceProvisioner:
    """Publish or complete one restart-safe session resource journal."""

    def __init__(self, repository: SessionResourceRepository) -> None:
        self.repository = repository
        self.inspector = SessionResourceInspector(repository)

    def provision(
        self,
        manifest: GitBaselineManifest,
        identity: GitSessionIdentity,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> GitSessionResourceInspection:
        refs = self.inspector.data.read_refs()
        baseline = self.inspector.data.verify_main(
            manifest,
            refs,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        before = self.inspector.facts(refs)
        self.inspector.require_other_sessions_exact(identity, baseline, before)
        current = self.inspector.classify(identity, baseline, before)
        self.inspector.data.require_refs_current(refs)
        if current.state is GitSessionResourceState.READY:
            return current
        if current.state not in {
            GitSessionResourceState.ABSENT,
            GitSessionResourceState.PROVISIONING,
        }:
            return current
        if self._unregistered_worktree_collides(identity, before):
            return recovery(identity, baseline)
        journal = self._journal(identity)
        if journal is None:
            if current.state is not GitSessionResourceState.ABSENT:
                return recovery(identity, baseline)
            effective_identity = self._effective_identity()
            journal = GitSessionProvisioningJournal(
                baseline_commit_id=baseline,
                effective_identity=effective_identity,
                provisioning_id=uuid.uuid4().hex,
                seed_timestamp_seconds=int(time.time()),
                session_id=identity.session_id,
            )
            self.inspector.data.require_refs_current(refs)
            self.repository.write_journal(journal)
        elif (
            journal.baseline_commit_id != baseline
            or journal.session_id != identity.session_id
        ):
            return recovery(identity, baseline)

        try:
            prefix_refs = self.inspector.data.read_refs()
            observed = self.inspector.facts(prefix_refs)
            prefix = self.inspector.classify(identity, baseline, observed, journal=journal)
            self.inspector.data.require_refs_current(prefix_refs)
            if prefix.state is not GitSessionResourceState.PROVISIONING:
                return prefix
            seed = self.inspector.seed(journal)
            self.inspector.data.require_refs_current(refs)
            self._create_seed(seed, journal)
            observed = self.inspector.facts()
            branch_tip = observed.branches.get(identity.branch)
            internal_tip = observed.internal_refs.get(identity.internal_ref)
            worktree = observed.worktrees.get(identity.worktree_root)
            if internal_tip is None:
                self._cas_ref(identity.internal_ref, seed)
            elif internal_tip != seed and not self.inspector.root_anchor(internal_tip, seed):
                return recovery(identity, baseline)
            observed = self.inspector.facts()
            branch_tip = observed.branches.get(identity.branch)
            if branch_tip is None:
                self._cas_ref(f"refs/heads/{identity.branch}", baseline)
            elif not self.inspector.root_anchor(branch_tip, baseline):
                return recovery(identity, baseline)
            observed = self.inspector.facts()
            worktree = observed.worktrees.get(identity.worktree_root)
            if worktree is None:
                if path_lexists(identity.worktree_root):
                    return recovery(identity, baseline)
                self.repository.required(
                    (
                        "worktree",
                        "add",
                        "--no-guess-remote",
                        os.fspath(identity.worktree_root),
                        identity.branch,
                    )
                )
            completed_refs = self.inspector.data.read_refs()
            completed_facts = self.inspector.facts(completed_refs)
            completed = self.inspector.classify(
                identity,
                baseline,
                completed_facts,
                journal=journal,
            )
            self.inspector.data.require_refs_current(completed_refs)
            if (
                completed.state is not GitSessionResourceState.PROVISIONING
                or completed_facts.internal_refs.get(identity.internal_ref) is None
                or completed_facts.branches.get(identity.branch) is None
                or completed_facts.worktrees.get(identity.worktree_root) is None
            ):
                return completed
            self.repository.remove_journal(identity)
            refs = self.inspector.data.read_refs()
            final_baseline = self.inspector.data.verify_main(
                manifest,
                refs,
                allowed_worktree_changes=allowed_worktree_changes,
            )
            if final_baseline != baseline:
                return recovery(identity, baseline)
            result = self.inspector.classify(identity, baseline, self.inspector.facts(refs))
            self.inspector.data.require_refs_current(refs)
            return result
        except BaseException as exc:
            after: GitSessionResourceInspection | None = None
            try:
                recovery_refs = self.inspector.data.read_refs()
                after = self.inspector.classify(
                    identity, baseline, self.inspector.facts(recovery_refs), journal=journal
                )
                self.inspector.data.require_refs_current(recovery_refs)
            except BaseException:
                after = None
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if after is not None and after.state is GitSessionResourceState.READY:
                return after
            if after is not None and after.state in {
                GitSessionResourceState.PROVISIONING,
                GitSessionResourceState.RECOVERY_REQUIRED,
            }:
                return after
            if isinstance(exc, GitSessionResourceError):
                raise exc from None
            return unknown(identity, baseline)

    def _unregistered_worktree_collides(
        self,
        identity: GitSessionIdentity,
        facts: ResourceFacts,
    ) -> bool:
        if identity.worktree_root in facts.worktrees:
            return False
        parent = identity.worktree_root.parent
        try:
            observed = os.lstat(parent)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if (
            not stat.S_ISDIR(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or Path(os.path.realpath(parent)) != parent
        ):
            return True
        descriptor = -1
        try:
            descriptor = open_bound_directory(
                parent,
                (observed.st_dev, observed.st_ino),
            )
        except GitSessionResourceError:
            pass
        if descriptor < 0:
            return True
        try:
            try:
                os.stat(
                    identity.worktree_root.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return False
            except OSError:
                return True
            return True
        finally:
            os.close(descriptor)

    def _journal(
        self, identity: GitSessionIdentity
    ) -> GitSessionProvisioningJournal | None:
        return self.inspector.facts().journals.get(identity.session_id)

    def _effective_identity(self) -> GitEffectiveIdentity:
        author = parse_identity(
            self.repository.required(("var", "GIT_AUTHOR_IDENT"), read_only=True).stdout
        )
        committer = parse_identity(
            self.repository.required(("var", "GIT_COMMITTER_IDENT"), read_only=True).stdout
        )
        return GitEffectiveIdentity(author=author, committer=committer)

    def _create_seed(
        self, expected: str, journal: GitSessionProvisioningJournal
    ) -> None:
        empty_tree = parse_object_id(
            self.repository.required(
                ("hash-object", "-w", "-t", "tree", "--stdin"),
                stdin=b"",
            ).stdout
        )
        payload = seed_payload(journal, empty_tree)
        observed = parse_object_id(
            self.repository.required(
                ("hash-object", "-w", "-t", "commit", "--stdin"), stdin=payload
            ).stdout
        )
        if observed != expected or not self.inspector.verify_seed(
            expected,
            GitSessionIdentity(self.repository.main_root, journal.session_id),
            journal,
        ):
            raise resource_error(
                GitSessionFailureCode.MUTATION_FAILED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )

    def _cas_ref(self, ref: str, target: str) -> None:
        self.repository.required(("update-ref", ref, target, _ZERO_BY_LENGTH[len(target)]))
