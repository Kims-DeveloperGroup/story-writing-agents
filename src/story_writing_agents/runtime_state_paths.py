"""Pure closed path grammar for orchestrator runtime-owned state."""

from __future__ import annotations

import re


CONTEXT_LEDGER_DIRECTORY = "agent_states/orchestrator_state/context_ledger/v2"
CONTEXTUAL_PROVIDER_DIRECTORY = (
    "agent_states/orchestrator_state/provider_sessions/.contextual/v1"
)
WRITING_ARTIFACT_DIRECTORY = "agent_states/orchestrator_state/writing_artifacts"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_EVENT = re.compile(r"(?!00000000)[0-9]{8}\.json")
_STAGED = re.compile(
    r"\.artifacts-[0-9a-f]{32}-(?P<attempt>[0-9]{1,3})\."
    r"(?:held|retired|completed)"
)


def is_orchestrator_runtime_directory(path: str) -> bool:
    """Recognize only directories created by the three registered owners."""

    if path in {
        "agent_states/orchestrator_state/context_ledger",
        CONTEXT_LEDGER_DIRECTORY,
        "agent_states/orchestrator_state/provider_sessions/.contextual",
        CONTEXTUAL_PROVIDER_DIRECTORY,
        WRITING_ARTIFACT_DIRECTORY,
        f"{WRITING_ARTIFACT_DIRECTORY}/applications",
        f"{WRITING_ARTIFACT_DIRECTORY}/transactions",
        f"{WRITING_ARTIFACT_DIRECTORY}/input_snapshots",
        f"{WRITING_ARTIFACT_DIRECTORY}/staging",
    }:
        return True
    parts = path.split("/")
    if len(parts) == 6 and "/".join(parts[:5]) == CONTEXTUAL_PROVIDER_DIRECTORY:
        return _SESSION.fullmatch(parts[5]) is not None
    snapshot = f"{WRITING_ARTIFACT_DIRECTORY}/input_snapshots"
    if (
        len(parts) >= 5
        and "/".join(parts[:4]) == snapshot
        and _IDENTIFIER.fullmatch(parts[4])
    ):
        tail = parts[5:]
        if tail in ([], ["operation_events"], ["invocations"]):
            return True
        return (
            len(tail) in {2, 3}
            and tail[0] == "invocations"
            and bool(_IDENTIFIER.fullmatch(tail[1]))
            and (len(tail) == 2 or tail[2] == "events")
        )
    return False


def is_orchestrator_runtime_file(path: str) -> bool:
    """Recognize exact owner leaves while leaving content validation to owners."""

    prefix, _, name = path.rpartition("/")
    if prefix == CONTEXT_LEDGER_DIRECTORY:
        return (name.endswith(".json") or name.endswith(".lock")) and bool(
            _HEX64.fullmatch(name[:-5])
        )
    contextual = CONTEXTUAL_PROVIDER_DIRECTORY
    if (
        prefix.startswith(contextual + "/")
        and prefix.count("/") == contextual.count("/") + 1
    ):
        return (
            bool(_SESSION.fullmatch(prefix.rsplit("/", 1)[1]))
            and name.endswith(".json")
            and bool(_HEX64.fullmatch(name[:-5]))
        )
    if prefix == WRITING_ARTIFACT_DIRECTORY:
        return name in {
            ".artifacts.lock",
            ".artifacts.lock.anchor",
            ".artifacts.lock.ready",
            "catalog.json",
            "recovery_blocked.json",
        }
    if prefix in {
        f"{WRITING_ARTIFACT_DIRECTORY}/applications",
        f"{WRITING_ARTIFACT_DIRECTORY}/transactions",
    }:
        identity = name[:-5] if name.endswith(".json") else ""
        return bool(
            (_IDENTIFIER if prefix.endswith("applications") else _HEX32).fullmatch(
                identity
            )
        )
    if prefix == f"{WRITING_ARTIFACT_DIRECTORY}/staging":
        if re.fullmatch(r"\.artifacts-[0-9a-f]{32}\.tmp", name):
            return True
        staged = _STAGED.fullmatch(name)
        return staged is not None and int(staged["attempt"]) < 128
    snapshot = f"{WRITING_ARTIFACT_DIRECTORY}/input_snapshots"
    if prefix.startswith(snapshot + "/"):
        parts = path.split("/")
        tail = parts[5:]
        if not _IDENTIFIER.fullmatch(parts[4]):
            return False
        if tail == ["manifest.json"]:
            return True
        if len(tail) == 2 and tail[0] == "operation_events":
            return bool(_EVENT.fullmatch(tail[1]))
        if (
            len(tail) >= 3
            and tail[0] == "invocations"
            and _IDENTIFIER.fullmatch(tail[1])
        ):
            return tail[2:] == ["manifest.json"] or (
                len(tail) == 4
                and tail[2] == "events"
                and bool(_EVENT.fullmatch(tail[3]))
            )
    return False


__all__: list[str] = []
