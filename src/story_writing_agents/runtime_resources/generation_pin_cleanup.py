"""Generation-pin cleanup with the existing escaping-failure note priority."""

from __future__ import annotations

from ..workspace.initialization import SessionArtifactGenerationPin


def add_generation_pin_cleanup_note(
    failure: BaseException,
    cleanup_failure: BaseException,
) -> None:
    """Attach only a cleanup exception type without replacing the primary failure."""

    try:
        failure.add_note(
            "pending story commit cleanup failed: "
            f"{type(cleanup_failure).__name__}"
        )
    except BaseException:
        pass


def close_untransferred_generation_pin(
    generation_pin: SessionArtifactGenerationPin,
    failure: BaseException | None = None,
) -> None:
    """Close untransferred pin ownership without masking a primary failure."""

    try:
        generation_pin.close()
    except BaseException as cleanup_failure:
        if failure is None:
            raise
        add_generation_pin_cleanup_note(failure, cleanup_failure)
