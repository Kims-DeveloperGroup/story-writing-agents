"""Pure exact-invocation usage projection; provider counters are never estimated."""

from __future__ import annotations

from dataclasses import dataclass

from ..conversations import (
    CollaborationEvent,
    CollaborationInvocationKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
)


@dataclass(frozen=True, slots=True)
class ResponseUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


def response_usage(
    events: tuple[CollaborationEvent, ...], *, session_id: str,
    conversation_id: str, turn_id: str, step_id: str,
    invocation_id: str | None, role: str,
    invocation_kinds: frozenset[CollaborationInvocationKind],
    call_id: str | None = None,
) -> ResponseUsage:
    """Sum the complete distinct submitted set for one exact response owner."""
    if invocation_id is None:
        return ResponseUsage()
    identity = session_id, conversation_id, turn_id, step_id
    calls: dict[str, CollaborationProviderCallPayload] = {}
    kinds: set[CollaborationInvocationKind] = set()
    for event in events:
        payload = event.payload
        if type(payload) is not CollaborationProviderCallPayload or payload.invocation_id != invocation_id:
            continue
        if ((event.session_id, event.conversation_id, event.turn_id, event.step_id) != identity
                or payload.role != role or payload.invocation_kind not in invocation_kinds):
            return ResponseUsage()
        kinds.add(payload.invocation_kind)
        if payload.state is not CollaborationProviderCallState.SUBMITTED:
            continue
        prior = calls.get(payload.call_id)
        if prior is not None and prior != payload:
            return ResponseUsage()
        calls[payload.call_id] = payload
    if len(kinds) != 1 or not calls:
        return ResponseUsage()
    observed_ordinals = [call.ordinal for call in calls.values()]
    if len(set(observed_ordinals)) != len(observed_ordinals):
        return ResponseUsage()
    if call_id is None and set(observed_ordinals) != set(range(1, len(calls) + 1)):
        return ResponseUsage()
    if call_id is not None:
        if call_id not in calls:
            return ResponseUsage()
        calls = {call_id: calls[call_id]}

    def counter(name: str) -> int | None:
        values: list[int | None] = [getattr(call.usage, name, None) for call in calls.values()]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    return ResponseUsage(counter("input_tokens"), counter("output_tokens"))


def response_footer(outcome: str, usage: ResponseUsage, *, elapsed: str | None = None) -> str:
    """Keep zero distinct from missing accounting and avoid cached-input double counting."""
    first = f"Outcome: {outcome}"
    if elapsed is not None:
        first += f" | Elapsed: {elapsed}"
    input_count = "unavailable" if usage.input_tokens is None else f"{usage.input_tokens:,}"
    output_count = "unavailable" if usage.output_tokens is None else f"{usage.output_tokens:,}"
    return first + f"\nInput tokens: {input_count} | Output tokens: {output_count}"
