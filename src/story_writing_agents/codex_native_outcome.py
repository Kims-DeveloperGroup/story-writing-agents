"""Interpret completed Codex CLI outcomes and strict native auth evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import re
from typing import Any

import story_writing_agents.codex_invocation_files as codex_invocation_files
import story_writing_agents.codex_native_context as codex_native_context
from .agent_context import ProviderUsage
from .codex_event_decoding import (
    SAFE_THREAD_ID as _SAFE_THREAD_ID,
    codex_usage_observation as _codex_usage_observation,
)
from .errors import ProviderAuthenticationRequiredError
from .provider_adapter import (
    NativeContextCheck,
    NativeContextCheckReason,
    NativeContextCheckStage,
    NativeContextObservation,
    NativeContextSourceStage,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
)
from .provider_execution_workspace import ProviderExecutionWorkspace
from .provider_process import ProcessResult


_Events = tuple[dict[str, Any], ...]


async def interpret_completed_native_outcome(
    result: ProcessResult,
    invocation: ProviderInvocation,
    execution_workspace: ProviderExecutionWorkspace,
    output_path: Path,
    stdin: bytes,
    *,
    native_state: Path,
    native_deadline: float,
    native_precheck: NativeContextCheck | None,
    native_pre_source_path: Path | None,
    native_pre_rediscovered: bool,
    get_authentication_failure: Callable[
        [], Callable[[bytes], ProviderAuthenticationRequiredError | None]
    ],
    get_parse_events: Callable[[], Callable[[bytes], _Events]],
    get_started_thread: Callable[[], Callable[[_Events], str | None]],
    get_provider_usage: Callable[[], Callable[[_Events], ProviderUsage | None]],
) -> ProviderResult:
    authentication = (
        None if result.returncode == 0 and codex_invocation_files.regular_output_exists(output_path)
        else get_authentication_failure()(result.stdout)
    )
    if authentication is not None:
        raise ProviderInvocationError(
            str(authentication),
            taint_existing_thread=True,
            authentication_failure=authentication,
        )
    if result.returncode != 0:
        raise ProviderInvocationError(
            f"Codex CLI exited with status {result.returncode}",
            taint_existing_thread=True,
        )

    events = get_parse_events()(result.stdout)
    thread_id = invocation.thread_id
    reported_thread_ids = tuple(
        event.get("thread_id") for event in events
        if event.get("type") == "thread.started"
    )
    if thread_id is not None and any(
        reported != thread_id for reported in reported_thread_ids
    ):
        reported = next(value for value in reported_thread_ids if value != thread_id)
        observed_thread = (reported if type(reported) is str
                           and _SAFE_THREAD_ID.fullmatch(reported) is not None else None)
        check = (NativeContextCheck(invocation.context_call_id,
            None if invocation.context_witness is None else invocation.context_witness.call_id,
            (() if native_precheck is None else native_precheck.observations) + (
                NativeContextObservation(NativeContextCheckStage.POST_COMPLETE,
                    NativeContextSourceStage.RECORDS, NativeContextCheckReason.THREAD_MISMATCH),),
            observed_thread_id=observed_thread, normal_exit_code=result.returncode)
            if invocation.context_call_id is not None else None)
        raise ProviderInvocationError(
            "Codex CLI returned a different continuation identity",
            taint_existing_thread=True,
            native_context_check=check,
        )
    if thread_id is None:
        thread_id = get_started_thread()(events)
        if thread_id is None:
            raise ProviderInvocationError(
                "Codex CLI did not report a new thread ID",
                taint_existing_thread=False,
            )
    try:
        execution_workspace.require_current()
        response_text = codex_invocation_files.read_regular_output(output_path)
    except OSError as exc:
        raise ProviderInvocationError(
            "Codex CLI did not write its final response",
            taint_existing_thread=True,
        ) from exc
    except UnicodeError as exc:
        raise ProviderInvocationError(
            "Codex CLI returned malformed structured output",
            taint_existing_thread=True,
        ) from exc
    native_observation = (await codex_native_context.observe_completed_native_context(native_state, invocation, thread_id,
        deadline=native_deadline, precheck=native_precheck,
        initial_path=native_pre_source_path, discovery_used=native_pre_rediscovered)
        if invocation.context_call_id is not None else None)
    raw_usage = get_provider_usage()(events)
    provider_result = ProviderResult(
        thread_id=thread_id,
        response_text=response_text,
        usage=raw_usage,
        last_token_usage=(
            None if native_observation is None or native_observation.witness is None
            else native_observation.last_token_usage
        ),
        usage_observation=_codex_usage_observation(raw_usage, native_observation, invocation),
        actual_transport_utf8_bytes=len(stdin),
        context_witness=None if native_observation is None else native_observation.witness,
        native_context_check=None if native_observation is None else replace(native_observation.check,
            observed_thread_id=reported_thread_ids[0] if reported_thread_ids else None,
            normal_exit_code=result.returncode),
        native_context_cause=None if native_observation is None else native_observation.cause)
    execution_workspace.require_current()
    return provider_result


def classify_native_authentication_failure(
    stdout: bytes,
    *,
    get_parse_events: Callable[[], Callable[..., _Events]],
) -> ProviderAuthenticationRequiredError | None:
    try:
        events = get_parse_events()(stdout, strict=True)
    except ProviderInvocationError:
        return None
    if any(event.get("type") == "turn.completed" for event in events):
        return None
    for event in events:
        if event.get("type") not in {"error", "turn.failed"}:
            continue
        error = event.get("error", event)
        if type(error) is not dict:
            continue
        native_401 = any(
            type(error.get(key)) is int and error[key] == 401
            for key in ("status", "status_code", "code")
        )
        message = error.get("message")
        native_401 = native_401 or (
            type(message) is str
            and re.search(r"\b(?:HTTP(?: status)?|status(?: code)?)\s*:?\s*401\b|\b401 Unauthorized\b", message, re.IGNORECASE)
            is not None
        )
        if native_401:
            return ProviderAuthenticationRequiredError("codex")
    return None
