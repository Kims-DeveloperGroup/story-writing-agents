"""Pure exact-invocation usage projection; provider counters are never estimated."""

from __future__ import annotations

from ..agent_context.accounting import normalized_provider_usage

from dataclasses import dataclass, field

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
    scope: str = field(default="invocation", kw_only=True, compare=False)
    call_count: int | None = field(default=None, kw_only=True, compare=False)
    elapsed_ns: int | None = field(default=None, kw_only=True, compare=False)


def response_usage(
    events: tuple[CollaborationEvent, ...], *, session_id: str,
    conversation_id: str, turn_id: str, step_id: str,
    invocation_id: str | None, role: str,
    invocation_kinds: frozenset[CollaborationInvocationKind],
    call_id: str | None = None,
) -> ResponseUsage:
    """Sum the complete distinct submitted set for one exact response owner."""
    scope = "physical_call" if call_id is not None else "invocation"
    unavailable = ResponseUsage(scope=scope, call_count=1 if call_id is not None else None)
    if invocation_id is None:
        return unavailable
    identity = session_id, conversation_id, turn_id, step_id
    calls: dict[str, CollaborationProviderCallPayload] = {}
    kinds: set[CollaborationInvocationKind] = set()
    for event in events:
        payload = event.payload
        if type(payload) is not CollaborationProviderCallPayload or payload.invocation_id != invocation_id:
            continue
        if ((event.session_id, event.conversation_id, event.turn_id, event.step_id) != identity
                or payload.role != role or payload.invocation_kind not in invocation_kinds):
            return unavailable
        kinds.add(payload.invocation_kind)
        if payload.state is not CollaborationProviderCallState.SUBMITTED:
            continue
        prior = calls.get(payload.call_id)
        if prior is not None and prior != payload:
            return unavailable
        calls[payload.call_id] = payload
    if len(kinds) != 1 or not calls:
        return unavailable
    observed_ordinals = [call.ordinal for call in calls.values()]
    if len(set(observed_ordinals)) != len(observed_ordinals):
        return unavailable
    if call_id is None and set(observed_ordinals) != set(range(1, len(calls) + 1)):
        return unavailable
    if call_id is not None:
        if call_id not in calls:
            return unavailable
        calls = {call_id: calls[call_id]}

    predecessors = {}
    ambiguous = set()
    for event in events:
        payload = event.payload
        if type(payload) is not CollaborationProviderCallPayload or payload.accounting is None:
            continue
        observation = payload.accounting.usage_observation
        if observation is None or observation.session_ref is None or observation.prefix_ref is None:
            continue
        key = observation.session_ref, observation.prefix_ref
        old = predecessors.get(key)
        if old is not None and old != observation:
            ambiguous.add(key)
        predecessors[key] = observation
    attributed = {}
    for identity, call in calls.items():
        observation = None if call.accounting is None else call.accounting.usage_observation
        if observation is None:
            attributed[identity] = call.usage
        else:
            key = observation.session_ref, observation.previous_prefix_ref
            attributed[identity] = (None if key in ambiguous else
                normalized_provider_usage(observation, predecessors.get(key)))

    def counter(name: str) -> int | None:
        values: list[int | None] = [getattr(usage, name, None) for usage in attributed.values()]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    call = next(iter(calls.values())) if call_id is not None and len(calls) == 1 else None
    elapsed_ns = call.accounting.elapsed_ns if call is not None and call.accounting is not None else None
    return ResponseUsage(counter("input_tokens"), counter("output_tokens"),
                         scope=scope, call_count=len(calls), elapsed_ns=elapsed_ns)


def response_footer(outcome: str, usage: ResponseUsage, *, elapsed: str | None = None) -> str:
    """Keep zero distinct from missing accounting and avoid cached-input double counting."""
    first = f"Outcome: {outcome}"
    if elapsed is None and usage.elapsed_ns is not None:
        seconds = usage.elapsed_ns // 1_000_000_000
        elapsed = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    if elapsed is not None:
        first += f" | Elapsed: {elapsed}"
    return first + "\n" + turn_usage_summary(usage)


def turn_usage_summary(usage: ResponseUsage, *, elapsed: str | None = None) -> str:
    """Describe invocation cost outside any speaker's response metadata."""
    input_count = "unavailable" if usage.input_tokens is None else f"{usage.input_tokens:,}"
    output_count = "unavailable" if usage.output_tokens is None else f"{usage.output_tokens:,}"
    count = (f" ({usage.call_count} {'call' if usage.call_count == 1 else 'calls'})"
             if usage.call_count is not None else "")
    scope = ("physical call" if usage.scope == "physical_call" else
             "invocation" + count)
    prefix = "" if elapsed is None else f"Elapsed: {elapsed}\n"
    return prefix + f"Usage scope: {scope}\nInput tokens: {input_count} | Output tokens: {output_count}"
