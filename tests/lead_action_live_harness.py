"""Opt-in A641 provider evidence; imports are inert and every call is reserved.

The manifest authorizes one case on pinned source and an explicit Codex model.
The release ledger is separate from historical live harness budgets. This runner
never confirms a consultation, applies an artifact, or creates a story commit.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from unittest import mock

CASE = "A641-CONTEXT-DRAFT"
OUTLINE = "core_documents/global_outline.md"
MARKER = "A641-LIVE-SYNTHETIC"
DRAFT = "1. A lantern vanishes.\n2. Min finds its keeper.\n3. The lantern returns.\n[" + MARKER + "]\n"
MODEL = "gpt-6-astra"
EFFORT = "xhigh"
EXECUTABLE = "/opt/homebrew/bin/codex"
VERSION = "0.153.4"
CLEANUP_MARGIN_SECONDS = 5
LIMITS = {"release_calls": 12, "release_seconds": 1350, "case_calls": 4, "case_seconds": 450, "call_seconds": 90}


class AuthorizationError(RuntimeError):
    """A fail-closed authorization, identity, budget, or ownership gate."""


def require(value, message):
    if not value:
        raise AuthorizationError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value):
    """Atomic, durable evidence; no credential or ambient environment dumps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".a641-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def source_closure(root: Path | None = None):
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    files = list((root / "src").rglob("*.py"))
    files += [p for p in (root / "config").rglob("*") if p.is_file()]
    files += [root / "tests/lead_action_live_harness.py", root / "pyproject.toml"]
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(files)}


def load_authorization(path: Path | None):
    require(os.environ.get("STORY_RUN_LIVE") == "1", "Explicit live opt-in is required")
    require(path is not None and path.is_file(), "A fresh authorization manifest is required")
    value = json.loads(path.read_bytes())
    require(value.get("schema") == "lead-action-live-authorization-v1", "Unsupported authorization schema")
    require(value.get("case") == CASE and value.get("authorized") is True, "This exact case is not authorized")
    require(value.get("limits") == LIMITS, "Fresh release and case limits must match")
    require(isinstance(value.get("authorization_id"), str) and bool(value["authorization_id"]), "Authorization identity is required")
    require(isinstance(value.get("release_id"), str) and bool(value["release_id"]), "Release identity is required")
    require(value.get("model") == {"executable": EXECUTABLE, "version": VERSION, "model": MODEL, "effort": EFFORT}, "Native model identity is not attested")
    require(value.get("expires_at", 0) > time.time(), "Authorization expired")
    root = Path(value["source_root"]).resolve()
    require(root == Path(__file__).resolve().parents[1], "Authorization belongs to another checkout")
    require(value.get("source_pins") == source_closure(root), "Source or configuration drift")
    executable = Path(EXECUTABLE)
    require(executable.is_file() and value.get("executable_sha256") == sha(executable.read_bytes()), "Executable drift")
    ledger = Path(value["release_ledger"])
    require(ledger.is_absolute() and ledger.parent.is_dir(), "Release ledger must use an existing absolute evidence directory")
    return value


class CallGuard:
    """One held release lock and durable reservations before native submission."""

    def __init__(self, authorization, evidence: Path, *, validate, monotonic=time.monotonic):
        self.authorization = authorization
        self.evidence = evidence
        self.validate = validate
        self.clock = monotonic
        self.started = self.clock()
        self.calls = []
        self.children = {}
        self.lock = None
        self.ledger_path = Path(authorization["release_ledger"])
        self.ledger = None

    def __enter__(self):
        self.validate()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.lock = open(self.ledger_path.with_suffix(".lock"), "a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.ledger = json.loads(self.ledger_path.read_bytes()) if self.ledger_path.exists() else {
                "schema": "lead-action-release-ledger-v1", "release_id": self.authorization["release_id"], "limits": LIMITS,
                "reserved": 0, "seconds": 0, "attempts": [],
            }
            require(self.ledger.get("schema") == "lead-action-release-ledger-v1" and self.ledger.get("release_id") == self.authorization["release_id"] and self.ledger.get("limits") == LIMITS, "Foreign or historical ledger")
            require(all(a.get("status") in {"passed", "failed"} for a in self.ledger["attempts"]), "A prior interrupted case needs explicit accounting resolution")
            require(self.authorization["authorization_id"] not in [a["authorization_id"] for a in self.ledger["attempts"]], "A case attempt must never retry automatically")
            self.ledger["attempts"].append({"authorization_id": self.authorization["authorization_id"], "case": CASE, "status": "running", "reserved": 0, "seconds": 0})
            save(self.ledger_path, self.ledger)
            save(self.evidence / "authorization.json", self.authorization)
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, exc_type, exc, traceback):
        elapsed = self.clock() - self.started
        attempt = self.ledger["attempts"][-1]
        attempt.update(status="passed" if exc_type is None else "failed", seconds=elapsed, error_type=None if exc_type is None else exc_type.__name__)
        self.ledger["seconds"] += elapsed
        save(self.ledger_path, self.ledger)
        save(self.evidence / "result.json", {**attempt, "calls": self.calls, "owned_children_remaining": list(self.children)})
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def reserve(self, request):
        self.validate()
        require(len(self.calls) < LIMITS["case_calls"], "Case call cap reached")
        require(self.ledger["reserved"] < LIMITS["release_calls"], "Release call cap reached")
        elapsed = self.clock() - self.started
        require(elapsed + request.timeout_seconds + CLEANUP_MARGIN_SECONDS <= LIMITS["case_seconds"], "Case time cap reached")
        require(self.ledger["seconds"] + elapsed + request.timeout_seconds + CLEANUP_MARGIN_SECONDS <= LIMITS["release_seconds"], "Release time cap reached")
        require(0 < request.timeout_seconds <= LIMITS["call_seconds"], "Native call time cap exceeded")
        command = list(request.command)
        require(command[0] == self.authorization["model"]["executable"], "Foreign provider executable")
        require("--model" in command and command[command.index("--model") + 1] == MODEL, "Model drift")
        require('model_reasoning_effort="xhigh"' in command, "Reasoning effort drift")
        self.ledger["reserved"] += 1
        self.ledger["attempts"][-1]["reserved"] += 1
        # The release ledger commits this physical call before any process exists.
        save(self.ledger_path, self.ledger)
        record = {"ordinal": len(self.calls) + 1, "submitted": False, "status": "reserved", "stdin_sha256": sha(request.stdin)}
        self.calls.append(record)
        directory = self.evidence / "calls" / f"{record['ordinal']:02}"
        directory.mkdir(parents=True)
        (directory / "stdin.raw").write_bytes(request.stdin)
        save(directory / "reservation.json", record)
        return record, directory

    async def join_owned(self):
        for pid, child in tuple(self.children.items()):
            if child.returncode is None:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(child.wait(), 2)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await child.wait()
            else:
                await child.wait()
            self.children.pop(pid, None)

    async def invoke(self, original, request, **kwargs):
        record, directory = self.reserve(request)
        create = kwargs.pop("create_process", asyncio.create_subprocess_exec)
        async def owned_create(*command, **options):
            options["start_new_session"] = True
            child = await create(*command, **options)
            self.children[child.pid] = child
            record.update(submitted=True, pid=child.pid, status="submitted")
            save(directory / "reservation.json", record)
            return child
        started = self.clock()
        try:
            actual = await original(request, create_process=owned_create, **kwargs)
            (directory / "stdout.raw").write_bytes(actual.stdout)
            (directory / "stderr.raw").write_bytes(actual.stderr)
            command = list(request.command)
            if "--output-last-message" in command:
                output = Path(command[command.index("--output-last-message") + 1])
                if output.is_file():
                    (directory / "native-result.raw").write_bytes(output.read_bytes())
            record.update(status="returned", returncode=actual.returncode, stdout_sha256=sha(actual.stdout), stderr_sha256=sha(actual.stderr))
            return actual  # The actual native response is never rewritten or repaired.
        except BaseException as exc:
            record.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            record["seconds"] = self.clock() - started
            await asyncio.shield(self.join_owned())
            save(directory / "reservation.json", record)


def snapshot(runtime, workspace):
    artifacts = {a.path: sha(a.content) for a in runtime.artifacts.capture_complete_state()}
    def git(*args):
        return sha(subprocess.check_output(["git", *args], cwd=workspace.root))
    return {"artifacts": artifacts, "head": git("rev-parse", "HEAD"), "index": git("ls-files", "--stage", "-z"), "tracked_diff": git("diff", "--binary", "HEAD")}


async def contextual_case(guard):
    from story_writing_agents import (
        AgentBackend, SessionAccessState, SessionDurableState, StoryRuntime,
        Workspace, codex_adapter,
    )
    from story_writing_agents.agents import LEAD_ORCHESTRATOR
    from story_writing_agents import conversations as c
    temporary = tempfile.TemporaryDirectory(prefix="a641-owned-", dir=guard.evidence)
    backend = None
    runtime = None
    conversation_id = None
    try:
        workspace = Workspace(Path(temporary.name) / "main").initialize()
        session_id = "synthetic_a641"
        defaults = workspace.read_json("config/system_defaults.json")
        defaults.update(codex_model=MODEL, codex_reasoning_effort=EFFORT)
        workspace.write_json("config/system_defaults.json", defaults)
        require(workspace.path(OUTLINE).read_bytes() == b"", "Initializer empty-outline prerequisite failed")
        backend = AgentBackend(workspace, executable=EXECUTABLE, model=MODEL, timeout_seconds=90, backend_overrides={LEAD_ORCHESTRATOR: "codex"})
        require(backend.validate_provider_configuration(session_id) is True,
                "Disposable provider configuration is not valid")
        backend.provision_session_resources(session_id)
        lifecycle = backend.create_session_with_access(session_id)
        require(lifecycle.durable_state is SessionDurableState.READY
                and lifecycle.access_state is SessionAccessState.HELD_HERE,
                "Disposable native session is not ready and held here")
        require(backend.get_session_lifecycle(session_id) == lifecycle,
                "Disposable native session lifecycle changed after creation")
        save(guard.evidence / "lifecycle.json", {
            "session_id": session_id, "durable_state": lifecycle.durable_state,
            "access_state": lifecycle.access_state,
            "workspace_root": str(workspace.root), "repeated_observation_matches": True,
        })
        runtime = StoryRuntime(workspace, backend)
        services = runtime.conversation_storage_services
        conversation_id = services.catalog.create(session_id, "Disposable contextual outline").conversation_id
        baseline = snapshot(runtime, workspace)
        save(guard.evidence / "baseline.json", baseline)
        save(guard.evidence / "profile.json", {"provider": "codex", "model": MODEL, "effort": EFFORT, "executable": EXECUTABLE, "version": VERSION, "configured_path": "config/system_defaults.json", "configured_sha256": sha(workspace.path("config/system_defaults.json").read_bytes()), "lead_route": "codex"})
        original = codex_adapter.run_process
        async def capture(request, **kwargs):
            return await guard.invoke(original, request, **kwargs)
        with mock.patch.object(codex_adapter, "run_process", capture):
            first = await runtime.lead_turn(session_id, conversation_id,
                f"This is disposable synthetic test content. Prepare an outline for {OUTLINE}. First offer exactly the following complete tiny draft as your whole visible message, and wait for my approval before preparing a candidate.\n{DRAFT}")
            save(guard.evidence / "first-result.json", {
                "session_id": first.session_id, "conversation_id": first.conversation_id,
                "turn_id": first.turn_id, "step_id": first.step_id,
                "kind": first.kind, "state": first.state, "next_action": first.next_action,
                "message_sha256": sha(first.message.encode("utf-8")),
                "message_utf8_bytes": len(first.message.encode("utf-8")),
                "confirmation_request_id": first.confirmation_request_id,
                "proposal_present": first.proposal is not None,
                "artifact_proposal_present": first.artifact_proposal is not None,
                "apply_request_present": first.artifact_application_request is not None,
                "commit_request_present": first.story_commit_request is not None,
            })
            require(first.message.rstrip("\n") == DRAFT.rstrip("\n"), "Provider did not offer the supplied tiny draft (apart from final newline)")
            observed_draft = first.message.encode("utf-8")
            require(first.artifact_proposal is None and first.proposal is None, "Draft offer prematurely requested work")
            history = services.event_store.read(session_id, conversation_id)
            projection = c.project_collaboration_history(history)
            require(len(projection.artifact_objectives) <= 1, "Draft offer created multiple objectives")
            objective = projection.artifact_objectives[0] if projection.artifact_objectives else None
            if objective is not None:
                require(objective.state is c.ArtifactObjectiveState.OPEN and objective.facts.origin_conversation_id == conversation_id, "Initial objective is not OPEN and owned")
                require(objective.facts.target_paths == (OUTLINE,), "Initial objective target mismatch")
            offered = next((s for s in projection.offered_draft_sources if s.content_digest == "sha256:" + sha(observed_draft)), None)
            require(offered is not None, "Exact whole-message draft reference is absent")
            second = await runtime.lead_turn(session_id, conversation_id, "네, 그 초안 그대로 문서로 준비해 줘.")
            require(second.kind is c.LeadOutcomeKind.PROPOSAL and second.artifact_proposal is not None, "Abbreviated approval did not prepare a candidate")
            candidate = second.artifact_proposal.candidate.candidate
            require(len(candidate.change_set.operations) == 1, "Candidate scope expanded")
            operation = candidate.change_set.operations[0]
            require(operation.operation_kind.value == "replace" and operation.path == OUTLINE and operation.content == observed_draft, "Candidate does not exactly replace the existing empty outline")
            history = services.event_store.read(session_id, conversation_id)
            projection = c.project_collaboration_history(history)
            require(len(projection.artifact_objectives) == 1, "Approval must select exactly one objective")
            continued = projection.artifact_objectives[0]
            require(continued.state is c.ArtifactObjectiveState.OPEN and continued.facts.origin_conversation_id == conversation_id and continued.facts.target_paths == (OUTLINE,), "Review request lacks its OPEN exact owner objective")
            if objective is not None:
                require(continued.facts == objective.facts, "Initial objective origin changed")
            else:
                approval = next(e for e in history if e.turn_id == second.turn_id and e.direction is c.CollaborationDirection.USER_TO_LEAD and isinstance(e.payload, c.CollaborationMessagePayload))
                require(continued.facts.origin_user_event_id == approval.event_id, "New objective did not originate at the actual approval")
            require(continued.draft_sources == (offered,), "Candidate selected another draft")
            require(continued.candidate_binding.candidate_digest == candidate.candidate_digest, "Candidate binding drift")
            require(second.confirmation_request_id is not None and second.artifact_application_request is None and second.story_commit_request is None, "Review/Apply/Commit authority boundary failed")
            require(not any(isinstance(e.payload, (c.CollaborationSpecialistReplyPayload, c.ArtifactApplicationRequestPayload, c.ArtifactApplicationResultPayload, c.StoryCommitRequestPayload)) for e in history), "Unauthorized downstream workflow appeared")
            require(snapshot(runtime, workspace) == baseline, "Artifact or Git state changed after fixture initialization")
            save(guard.evidence / "case-assertions.json", {"case": CASE, "passed": True, "source_event_id": offered.event_id, "source_digest": offered.content_digest, "candidate_digest": candidate.candidate_digest, "objective_id": continued.facts.objective_id, "state": continued.state, "initial_objective_present": objective is not None, "observed_draft_sha256": sha(observed_draft), "cleanup_margin_seconds": CLEANUP_MARGIN_SECONDS, "abbreviated_input": "네, 그 초안 그대로 문서로 준비해 줘."})
    finally:
        if runtime is not None and conversation_id is not None:
            history = runtime.conversation_storage_services.event_store.read("synthetic_a641", conversation_id)
            (guard.evidence / "events.jsonl").write_bytes(b"\n".join(c.encode_collaboration_event(e) for e in history) + b"\n")
        await guard.join_owned()
        if backend is not None:
            backend.close()
        temporary.cleanup()
        save(guard.evidence / "cleanup.json", {"workspace_removed": not Path(temporary.name).exists(), "owned_children_remaining": list(guard.children)})


async def run_authorized(manifest: Path, evidence: Path):
    authorization = load_authorization(manifest)
    from story_writing_agents.provider_auth import ProviderAuthService
    version = subprocess.check_output([EXECUTABLE, "--version"], timeout=10, text=True).strip()
    require(version.endswith(VERSION), "Codex version drift")
    auth = ProviderAuthService(Path(authorization["source_root"]), codex_executable=EXECUTABLE).probe("codex")
    require(auth.authenticated, "Native authentication is unavailable; zero model submissions")
    with CallGuard(authorization, evidence, validate=lambda: load_authorization(manifest)) as guard:
        try:
            await asyncio.wait_for(contextual_case(guard), timeout=LIMITS["case_seconds"] - CLEANUP_MARGIN_SECONDS)
        finally:
            await guard.join_owned()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--case", required=True, choices=[CASE])
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(run_authorized(args.manifest, args.evidence))


if __name__ == "__main__":
    main()
