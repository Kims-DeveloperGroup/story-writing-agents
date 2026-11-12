"""Registered prepared Commits and their publication and cleanup invariants."""

from __future__ import annotations

from ..workspace import Workspace
from ..conversations import StoryCommitRequestPayload
from .prepared_story_commit import PreparedStoryCommit
from ..runtime_resources.generation_pin_cleanup import add_generation_pin_cleanup_note


class PendingStoryCommits:
    """Own registered Commit resources, exact replacement, and retirement."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self._pending_story_commits: dict[
            tuple[str, str, str], PreparedStoryCommit
        ] = {}

    def install(
        self,
        key: tuple[str, str, str],
        pending: PreparedStoryCommit,
    ) -> None:
        """Install one exact owner and retire only the observed predecessor."""

        previous = self._pending_story_commits.get(key)
        self._pending_story_commits[key] = pending
        if previous is not None and previous is not pending:
            previous.close()

    def retire(
        self,
        key: tuple[str, str, str],
        pending: PreparedStoryCommit,
        *,
        failure: BaseException | None = None,
    ) -> None:
        """Remove only the exact registered owner, then close it idempotently."""

        if self._pending_story_commits.get(key) is not pending:
            del self._pending_story_commits[key]
        try:
            pending.close()
        except BaseException as cleanup_failure:
            if failure is None:
                raise
            add_generation_pin_cleanup_note(failure, cleanup_failure)

    def retire_archived_conversation(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        """Release package-internal Commit owners after a durable archive."""

        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id)
        failure: BaseException | None = None
        for key, pending in tuple(self._pending_story_commits.items()):
            if key[:2] != (session_id, conversation_id):
                continue
            try:
                self.retire(
                    key,
                    pending,
                    failure=failure,
                )
            except BaseException as cleanup_failure:
                failure = cleanup_failure
        if failure is not None:
            raise failure

    def observe_for_commit(
        self,
        key: tuple[str, str, str],
        request: StoryCommitRequestPayload,
    ) -> tuple[PreparedStoryCommit | None, PreparedStoryCommit | None]:
        observed_pending = self._pending_story_commits.get(key)
        pending = (
            observed_pending
            if observed_pending is not None
            and observed_pending.request == request
            else None
        )
        return observed_pending, pending

    def replace_after_application(
        self,
        key: tuple[str, str, str],
        pending: PreparedStoryCommit | None,
    ) -> None:
        previous_commit = self._pending_story_commits.get(key)
        if previous_commit is not None:
            self.retire(key, previous_commit)
        if pending is not None:
            self.install(key, pending)
            pending = None
