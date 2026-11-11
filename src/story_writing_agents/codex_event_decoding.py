"""Pure Codex event decoding, strict JSON hooks, and native usage qualification."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from .agent_context.accounting import (
    ProviderUsageObservation,
    ProviderUsageScope,
    canonical_provider_json,
)


def reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in pairs:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def reject_constant(_value: str) -> None:
    raise ValueError("nonfinite JSON value")


def finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON value")
    return number


def extract_stream_text(value: object) -> str:
    """Extract string text from a string, list of entries, or dictionary."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, dict):
                text_val = entry.get("text") or entry.get("content") or entry.get("summary")
                if isinstance(text_val, str):
                    parts.append(text_val)
        return "".join(parts)
    if isinstance(value, dict):
        text_val = value.get("text") or value.get("content") or value.get("summary")
        if isinstance(text_val, str):
            return text_val
    return ""


def resolve_aliased_field(
    mapping: dict[str, object],
    primary_key: str,
    alias_key: str,
    *,
    normalize_case: bool = False,
) -> tuple[str | None, bool]:
    """Resolve aliased fields from mapping.

    Returns:
        (resolved_value, has_conflict)
    """
    has_primary = primary_key in mapping
    has_alias = alias_key in mapping
    if not has_primary and not has_alias:
        return None, False

    val1 = mapping.get(primary_key)
    val2 = mapping.get(alias_key)

    if has_primary and (val1 is None or not isinstance(val1, str)):
        return None, True
    if has_alias and (val2 is None or not isinstance(val2, str)):
        return None, True

    if has_primary and has_alias:
        assert isinstance(val1, str) and isinstance(val2, str)
        s1 = val1.strip()
        s2 = val2.strip()
        if not s1 or not s2:
            return None, True
        c1 = s1.lower() if normalize_case else s1
        c2 = s2.lower() if normalize_case else s2
        if c1 != c2:
            return None, True
        return c1, False

    if has_primary:
        assert isinstance(val1, str)
        s1 = val1.strip()
        if not s1:
            return None, True
        return (s1.lower() if normalize_case else s1), False

    assert isinstance(val2, str)
    s2 = val2.strip()
    if not s2:
        return None, True
    return (s2.lower() if normalize_case else s2), False


def codex_usage_observation(raw, observed, invocation):
    if raw is None:
        return None

    def unknown(reason):
        return ProviderUsageObservation(raw, ProviderUsageScope.UNKNOWN,
            "codex.turn.completed.usage:" + reason)

    if observed is None:
        return unknown("context_not_inspected")
    if observed.witness is None:
        return unknown("native_" + observed.check.reason.value)
    if observed.usage_pair is None:
        return unknown("usage_missing")
    witness, (total, last), previous_total, first_native_turn = (observed.witness, observed.usage_pair, observed.previous_usage, observed.first_native_turn)
    previous = invocation.context_witness
    if previous is not None and previous.settings_digest != witness.settings_digest:
        return unknown("settings_changed")
    if raw != total:
        return unknown("raw_total_mismatch")
    names = ("input_tokens", "output_tokens", "cached_input_tokens")
    session_start = previous is None and invocation.thread_id is None and first_native_turn
    if previous is None:
        if session_start and total != last:
            return unknown("first_turn_counters_mismatch")
        # A verified resumed prefix is a baseline, not this call's usage. Its
        # successor may establish an exact delta without inventing the gap.
    elif previous_total is None:
        return unknown("predecessor_missing")
    elif any(getattr(total, name) < getattr(previous_total, name) for name in names):
        return unknown("cumulative_reset")
    elif any(getattr(total, name) - getattr(previous_total, name) != getattr(last, name) for name in names):
        return unknown("native_last_mismatch")

    def prefix(value):
        return hashlib.sha256(canonical_provider_json({"scope": value.scope_digest,
            "thread": value.thread_id, "turn": value.turn_id, "count": value.input_lineage_count,
            "lineage": value.input_lineage_sha256, "settings": value.settings_digest})).hexdigest()
    session = hashlib.sha256(canonical_provider_json({"scope": witness.scope_digest,
        "thread": witness.thread_id, "settings": witness.settings_digest})).hexdigest()
    return ProviderUsageObservation(raw, ProviderUsageScope.SESSION_CUMULATIVE,
        "codex.turn.completed+native.token_count.total_and_last", session_ref=session,
        prefix_ref=prefix(witness), previous_prefix_ref=None if previous is None else prefix(previous),
        session_start=session_start)
