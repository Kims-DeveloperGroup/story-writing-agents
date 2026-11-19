"""Read-only classification of one resource or an exhaustive inventory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .contracts import GIT_BASELINE_BRANCH, GitBaselineManifest
from .process import GitProcessFailure, GitProcessRunner
from .session_contracts import (
    GIT_SESSION_BRANCH_PREFIX,
    GIT_SESSION_INTERNAL_REF_PREFIX,
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionProvisioningJournal,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceInventory,
    GitSessionResourceState,
)
from .session_inspection_data import RefSnapshot, SessionInspectionData
from .session_resource_files import bind_directory, linked_worktree_common_git
from .session_resource_repository import SessionResourceRepository
from .session_resource_values import (
    SESSION_OBJECT_ID,
    absent,
    decode_lines,
    decode_utf8,
    parse_object_id,
    provisioning,
    recovery,
    resource_error,
    seed_payload,
    valid_seed_payload,
)


_MAX_WORKTREES = 65_536


class ResourceFacts:
    __slots__ = ("branches", "internal_refs", "journals", "worktrees")

    def __init__(
        self,
        branches: dict[str, str],
        internal_refs: dict[str, str],
        worktrees: dict[Path, tuple[str, str]],
        journals: dict[str, GitSessionProvisioningJournal],
    ) -> None:
        self.branches = branches
        self.internal_refs = internal_refs
        self.worktrees = worktrees
        self.journals = journals

class SessionResourceInspector:
    """Correlate session branches, refs, worktrees and journal phases."""

    def __init__(self, repository: SessionResourceRepository) -> None:
        self.repository = repository
        self.data = SessionInspectionData(repository)

    def inspect(
        self,
        manifest: GitBaselineManifest,
        identity: GitSessionIdentity,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> GitSessionResourceInspection:
        refs = self.data.read_refs(allow_loose=True)
        # Immutable-object results live only until this inspection's exit check.
        internal = next(
            (oid for name, oid, _ in refs.rows if name == identity.internal_ref), None
        )
        commit_objects: dict[str, bytes] = {}
        baseline = self.data.verify_main(
            manifest,
            refs,
            allowed_worktree_changes=allowed_worktree_changes,
            commit_objects=commit_objects,
            internal_tip=internal,
            allow_loose=True,
        )
        facts = self.facts(refs)
        result = self.classify(
            identity,
            baseline,
            facts,
            inspected_baseline=baseline,
            inspected_tip=(internal, commit_objects.get(internal))
            if internal is not None else None,
        )
        self.data.require_refs_current(refs, allow_loose=True)
        return result

    def list(
        self,
        manifest: GitBaselineManifest,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> GitSessionResourceInventory:
        refs = self.data.read_refs()
        baseline = self.data.verify_main(
            manifest,
            refs,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        facts = self.facts(refs)
        session_ids = self._inventory_session_ids(facts)
        sessions = tuple(
            self.classify(
                GitSessionIdentity(self.repository.main_root, session_id), baseline, facts
            )
            for session_id in session_ids
        )
        self._require_exhaustive(facts, sessions)
        self.data.require_refs_current(refs)
        return GitSessionResourceInventory(baseline, sessions)

    def facts(self, refs: RefSnapshot | None = None) -> ResourceFacts:
        if refs is None:
            refs = self.data.read_refs()
        branches: dict[str, str] = {}
        internal: dict[str, str] = {}
        for name, oid, _is_head in refs.rows:
            if name.startswith("refs/heads/"):
                branches[name.removeprefix("refs/heads/")] = oid
            elif name.startswith(GIT_SESSION_INTERNAL_REF_PREFIX):
                internal[name] = oid
            else:
                raise resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
        worktrees = self._worktrees()
        journals = self.repository.journals()
        return ResourceFacts(branches, internal, worktrees, journals)

    def _worktrees(self) -> dict[Path, tuple[str, str]]:
        output = self.repository.required(
            ("worktree", "list", "--porcelain", "-z"), read_only=True
        ).stdout
        records: dict[Path, tuple[str, str]] = {}
        current: dict[str, str] = {}
        for field in output.split(b"\x00"):
            if not field:
                if current:
                    self._finish_worktree(current, records)
                    current = {}
                continue
            try:
                key, value = field.split(b" ", 1)
            except ValueError:
                key, value = field, b""
            current[key.decode("ascii")] = decode_utf8(value)
        if current:
            self._finish_worktree(current, records)
        if len(records) > _MAX_WORKTREES:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return records

    def _finish_worktree(
        self,
        current: dict[str, str],
        records: dict[Path, tuple[str, str]],
    ) -> None:
        if (
            "worktree" not in current
            or "HEAD" not in current
            or "branch" not in current
        ):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        path = Path(current["worktree"])
        if not path.is_absolute() or path in records:
            raise resource_error(
                GitSessionFailureCode.RESOURCE_COLLISION,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        records[path] = (parse_object_id(current["HEAD"]), current["branch"])

    def classify(
        self,
        identity: GitSessionIdentity,
        baseline: str,
        facts: ResourceFacts,
        *,
        journal: GitSessionProvisioningJournal | None = None,
        require_clean_session_index: bool = True,
        inspected_seed: tuple[str | None, bytes | None] | None = None,
        inspected_baseline: str | None = None,
        inspected_tip: tuple[str, bytes | None] | None = None,
    ) -> GitSessionResourceInspection:
        journal = journal or facts.journals.get(identity.session_id)
        branch = facts.branches.get(identity.branch)
        internal = facts.internal_refs.get(identity.internal_ref)
        worktree = facts.worktrees.get(identity.worktree_root)
        present = (internal is not None, branch is not None, worktree is not None)
        if present == (False, False, False):
            if journal is None:
                return absent(identity, baseline)
            if (
                journal.baseline_commit_id == baseline
                and journal.session_id == identity.session_id
            ):
                return provisioning(identity, baseline)
            return recovery(identity, baseline)
        if journal is None and present != (True, True, True):
            return recovery(identity, baseline)
        if present not in {
            (True, False, False),
            (True, True, False),
            (True, True, True),
        }:
            return recovery(identity, baseline)
        if (
            branch is not None
            and not (branch == baseline == inspected_baseline)
            and not self.root_anchor(branch, baseline)
        ):
            return recovery(identity, baseline)
        seed = None
        if internal is not None:
            seed_verified = False
            tip_payload = (
                inspected_tip[1]
                if inspected_tip is not None and inspected_tip[0] == internal else None
            )
            if inspected_seed is not None:
                seed, payload = inspected_seed
            elif tip_payload is not None and self.verify_seed(
                internal, identity, journal, payload=tip_payload
            ):
                # Exact seed payloads contain no parents: the TIP is its own root.
                seed, payload = internal, tip_payload
                seed_verified = True
            else:
                seed = self._root_commit(internal)
                payload = tip_payload if seed == internal else None
            if seed is None or (
                not seed_verified
                and not self.verify_seed(seed, identity, journal, payload=payload)
            ):
                return recovery(identity, baseline)
        if journal is not None and (
            (branch is not None and branch != baseline)
            or (internal is not None and internal != seed)
        ):
            return recovery(identity, baseline)
        if worktree is not None:
            worktree_head, worktree_branch = worktree
            if (
                worktree_branch != f"refs/heads/{identity.branch}"
                or worktree_head != branch
            ):
                return recovery(identity, baseline)
            if (
                not self._verify_worktree_indirection(identity)
                or (
                    require_clean_session_index
                    and not self._session_index_is_clean(identity)
                )
            ):
                return recovery(identity, baseline)
        if present == (True, True, True):
            return GitSessionResourceInspection(
                identity=identity,
                state=(
                    GitSessionResourceState.READY
                    if journal is None
                    else GitSessionResourceState.PROVISIONING
                ),
                recovery_action=(
                    GitSessionRecoveryAction.REOPEN
                    if journal is None
                    else GitSessionRecoveryAction.RETRY_CREATE
                ),
                baseline_commit_id=baseline,
                seed_commit_id=seed,
                initial_branch_tip=baseline,
                current_branch_tip=branch,
                initial_internal_tip=seed,
                current_internal_tip=internal,
            )
        if journal is not None:
            return GitSessionResourceInspection(
                identity=identity,
                state=GitSessionResourceState.PROVISIONING,
                recovery_action=GitSessionRecoveryAction.RETRY_CREATE,
                baseline_commit_id=baseline,
                seed_commit_id=seed,
                initial_branch_tip=baseline if branch is not None else None,
                current_branch_tip=branch,
                initial_internal_tip=seed,
                current_internal_tip=internal,
            )
        return recovery(identity, baseline)

    def _inventory_session_ids(self, facts: ResourceFacts) -> tuple[str, ...]:
        values: set[str] = set(facts.journals)
        for branch in facts.branches:
            if branch == GIT_BASELINE_BRANCH:
                continue
            if not branch.startswith(GIT_SESSION_BRANCH_PREFIX):
                raise resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.LIST,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            values.add(branch.removeprefix(GIT_SESSION_BRANCH_PREFIX))
        for ref in facts.internal_refs:
            values.add(ref.removeprefix(GIT_SESSION_INTERNAL_REF_PREFIX))
        for path in facts.worktrees:
            if path == self.repository.main_root:
                continue
            parent = self.repository.main_root.with_name(f"{self.repository.main_root.name}.story-worktrees")
            try:
                relative = path.relative_to(parent)
            except ValueError:
                raise resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.LIST,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                ) from None
            if len(relative.parts) != 1:
                raise resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.LIST,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            values.add(relative.name)
        identities = tuple(
            GitSessionIdentity(self.repository.main_root, value) for value in values
        )
        paths = [
            os.path.normcase(os.fspath(value.worktree_root)) for value in identities
        ]
        if len(paths) != len(set(paths)):
            raise resource_error(
                GitSessionFailureCode.RESOURCE_COLLISION,
                GitSessionOperationKind.LIST,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return tuple(sorted(values, key=lambda value: value.encode("ascii")))

    def _require_exhaustive(
        self,
        facts: ResourceFacts,
        sessions: tuple[GitSessionResourceInspection, ...],
    ) -> None:
        allowed_branches = {
            GIT_BASELINE_BRANCH,
            *(value.identity.branch for value in sessions),
        }
        allowed_refs = {value.identity.internal_ref for value in sessions}
        allowed_worktrees = {
            self.repository.main_root,
            *(value.identity.worktree_root for value in sessions),
        }
        allowed_journals = {
            value.identity.session_id
            for value in sessions
            if value.state is GitSessionResourceState.PROVISIONING
        }
        if (
            GIT_BASELINE_BRANCH not in facts.branches
            or self.repository.main_root not in facts.worktrees
            or not set(facts.branches).issubset(allowed_branches)
            or not set(facts.internal_refs).issubset(allowed_refs)
            or not set(facts.worktrees).issubset(allowed_worktrees)
            or set(facts.journals) != allowed_journals
        ):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.LIST,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )

    def require_other_sessions_exact(
        self,
        requested: GitSessionIdentity,
        baseline: str,
        facts: ResourceFacts,
    ) -> None:
        session_ids = self._inventory_session_ids(facts)
        inspections = tuple(
            self.classify(GitSessionIdentity(self.repository.main_root, value), baseline, facts)
            for value in session_ids
            if value != requested.session_id
        )
        if any(
            value.state is not GitSessionResourceState.READY for value in inspections
        ):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )

    def seed(self, journal: GitSessionProvisioningJournal) -> str:
        empty_tree = parse_object_id(
            self.repository.required(
                ("hash-object", "-t", "tree", "--stdin"),
                stdin=b"",
                read_only=True,
            ).stdout
        )
        payload = seed_payload(journal, empty_tree)
        constructor = (
            hashlib.sha1 if len(journal.baseline_commit_id) == 40 else hashlib.sha256
        )
        header = f"commit {len(payload)}\0".encode("ascii")
        return constructor(header + payload).hexdigest()

    def root_anchor(self, tip: str, expected: str) -> bool:
        roots = decode_lines(
            self.repository.required(("rev-list", "--max-parents=0", tip), read_only=True).stdout
        )
        return roots == (expected,)

    def _root_commit(self, tip: str) -> str | None:
        roots = decode_lines(
            self.repository.required(("rev-list", "--max-parents=0", tip), read_only=True).stdout
        )
        return roots[0] if len(roots) == 1 and SESSION_OBJECT_ID.fullmatch(roots[0]) else None

    def verify_seed(
        self,
        seed: str,
        identity: GitSessionIdentity,
        journal: GitSessionProvisioningJournal | None,
        *,
        payload: bytes | None = None,
    ) -> bool:
        try:
            if SESSION_OBJECT_ID.fullmatch(seed) is None:
                return False
            empty_tree_object = b"tree 0\x00"
            empty = (
                hashlib.sha1(empty_tree_object).hexdigest()
                if len(seed) == 40
                else hashlib.sha256(empty_tree_object).hexdigest()
            )
            if payload is None:
                payload = self.repository.required(
                    ("cat-file", "commit", seed), read_only=True
                ).stdout
            if not valid_seed_payload(
                payload,
                identity=identity,
                empty_tree=empty,
                journal=journal,
            ):
                return False
            if journal is not None:
                expected = self.seed(journal)
                return expected == seed
            return True
        except (GitSessionResourceError, GitProcessFailure):
            return False

    def _verify_worktree_indirection(self, identity: GitSessionIdentity) -> bool:
        linked = linked_worktree_common_git(identity.worktree_root)
        if linked is None:
            return False
        try:
            opened = os.fstat(self.repository.common_descriptor)
        except OSError:
            return False
        common, common_identity = linked
        return common == self.repository.common_git and common_identity == (
            opened.st_dev,
            opened.st_ino,
        )

    def _session_index_is_clean(self, identity: GitSessionIdentity) -> bool:
        """Require the session index to match HEAD without scanning untracked paths."""

        try:
            session_identity = bind_directory(identity.worktree_root)
            result = GitProcessRunner(identity.worktree_root).run(
                ("diff-index", "--cached", "--quiet", "HEAD", "--"),
                expected_identity=session_identity,
                read_only=True,
            )
        except (GitProcessFailure, GitSessionResourceError):
            return False
        return result.returncode == 0 and not result.stdout
