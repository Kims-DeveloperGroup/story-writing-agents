"""One prepared story Commit and its exact retained generation pin."""

from __future__ import annotations

from ..git.story_commits import StoryCommitService
from ..workspace.initialization import SessionArtifactGenerationPin
from ..conversations import StoryCommitRequestPayload


class PreparedStoryCommit:
    """Own one exact service and physical-generation pin until publication."""

    __slots__ = ("request", "service", "_generation_pin")

    def __init__(
        self,
        request: StoryCommitRequestPayload,
        service: StoryCommitService,
        generation_pin: SessionArtifactGenerationPin,
    ) -> None:
        if (
            type(request) is not StoryCommitRequestPayload
            or type(service) is not StoryCommitService
            or type(generation_pin) is not SessionArtifactGenerationPin
        ):
            raise TypeError("pending story commit is invalid")
        self.request = request
        self.service = service
        self._generation_pin: SessionArtifactGenerationPin | None = generation_pin

    def close(self) -> None:
        """Release the retained generation exactly once."""

        generation_pin = self._generation_pin
        if generation_pin is None:
            return
        self._generation_pin = None
        generation_pin.close()

    def __del__(self) -> None:
        """Best-effort final release when an owning runtime is abandoned."""

        try:
            self.close()
        except BaseException:
            pass
