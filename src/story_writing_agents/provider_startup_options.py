"""Validate explicit startup options for provider adapters."""

from __future__ import annotations

from collections.abc import Callable

from .config import ConfigManager
from .errors import ConfigurationError


def validate_provider_startup_options(
    manager: ConfigManager,
    *,
    model: str | None,
    antigravity_model: str | None,
    antigravity_effort: str | None,
    timeout_seconds: float,
    record_failure: Callable[[ValueError], None],
) -> tuple[str | None, str | None, str | None]:
    """Return the validated explicit model and effort inputs."""

    try:
        explicit_model = manager.validate_codex_model(model)
    except ConfigurationError as exc:
        failure = ValueError("Codex model must be safe non-empty text or None")
        record_failure(failure)
        raise failure from exc
    try:
        explicit_antigravity_model = manager.validate_antigravity_model(
            antigravity_model
        )
    except ConfigurationError as exc:
        failure = ValueError(
            "Antigravity model must be safe non-empty text or None"
        )
        record_failure(failure)
        raise failure from exc
    try:
        explicit_antigravity_effort = manager.validate_antigravity_effort(
            antigravity_effort
        )
    except ConfigurationError as exc:
        failure = ValueError(
            "Antigravity effort must be None or one of: low, medium, high"
        )
        raise failure from exc
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        failure = ValueError("Codex timeout must be a positive number")
        record_failure(failure)
        raise failure
    return explicit_model, explicit_antigravity_model, explicit_antigravity_effort
