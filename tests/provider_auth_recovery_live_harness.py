"""Opt-in Issue 631 live evidence; native login terminal bytes are never recorded."""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import threading
import time
from unittest import mock

from story_writing_agents import AgentBackend, StoryRuntime, Workspace
from story_writing_agents import antigravity_adapter, codex_adapter
from story_writing_agents.agents import ROSTER
from story_writing_agents.config import ConfigManager
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision, CollaborationEventKind,
    CollaborationLeadSynthesisPayload, CollaborationSpecialistReplyPayload,
    CollaborationProviderCallPayload, CollaborationStepState, LeadStepDecision,
)
from story_writing_agents.interactive_controller._lead_policy import InteractiveLeadAdapter
from story_writing_agents.provider_auth import ProviderAuthService
from story_writing_agents.provider_process import ProcessResult
from story_writing_agents.workspace import WORKSPACE_DIRECTORIES
from story_writing_agents.workspace.initialization import provision_session_resources
from tests.helpers import initialize_test_workspace


CASES = {
    "L631-PRO-AUTH-PTY": ("plot_architect", "gemini-3.1-pro-low", 2400),
    "L631-FLASH-MIXED": ("character_custodian", "gemini-3.8-flash-low", 1200),
    "L631-SONNET-MIXED": ("execution_specialist", "claude-sonnet-4-6", 1200),
    "L631-PRO-MIXED-NOAUTH": ("plot_architect", "gemini-3.1-pro-low", 600),
}


def save(path, value):
    staged = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    staged.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
    os.replace(staged, path)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def prepare(root, prior_root=None):
    """Prepare evidence only; this function starts no native processes."""
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    ledger = (json.loads((prior_root / "ledger.json").read_text()) if prior_root is not None
              else dict(t0=None, real=0, synthetic=0, probes=0, login=0, cases={}))
    if prior_root is not None:
        ledger["prior_evidence_root"] = str(prior_root)
        ledger["limits"] = dict(real=36, synthetic=4, probes=360, login=4,
                              global_wall_seconds=3900, pro_attempt4_wall_seconds=400)
    save(root / "ledger.json", ledger)
    save(root / "baseline-reported.json", {
        "provenance": "root read-only observations before this harness",
        "codex": "Logged in using ChatGPT",
        "antigravity_models": [value[1] for value in CASES.values()],
        "recovery_completion": False,
    })


class Ledger:
    def __init__(self, root, case, attempt):
        self.root, self.case, self.attempt = root, case, attempt

    def reserve(self, kind, *, model=None):
        with (self.root / "ledger.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = json.loads((self.root / "ledger.json").read_text())
            now = time.time()
            if kind in ("real", "login") and value["t0"] is None:
                value["t0"] = now
            if value["t0"] is not None:
                require(now - value["t0"] < 3900, "GLOBAL_WALL_CAP")
            row = value["cases"].setdefault(self.attempt, dict(t0=now, real=0, synthetic=0))
            related = [entry for key, entry in value["cases"].items() if key == self.case or key.startswith(self.case + "-attempt")]
            final_attempt = self.attempt.endswith("-attempt4-ctrl-d")
            elapsed = now - (row["t0"] if final_attempt or self.attempt.endswith("-attempt3") else min(entry["t0"] for entry in related))
            require(elapsed < (400 if final_attempt else 600 if self.attempt.endswith("-attempt3") else CASES[self.case][2]), "CASE_WALL_CAP")
            limits = {"real": 36, "synthetic": 4, "probes": 360, "login": 4}
            value["limits"] = dict(limits, global_wall_seconds=3900, pro_attempt4_wall_seconds=400)
            require(value[kind] < limits[kind], "GLOBAL_" + kind.upper() + "_CAP")
            if kind in ("real", "synthetic"):
                require(row[kind] < (12 if kind == "real" else 1), "CASE_CALL_CAP")
                if kind == "real":
                    require(sum(entry["real"] for entry in related) < 12, "CUMULATIVE_CASE_CALL_CAP")
                row[kind] += 1
            value[kind] += 1
            if kind == "real" and model == "gemini-3.1-pro-low":
                require(value.get("actual_pro_models", 0) < 12, "ACTUAL_PRO_MODEL_CAP")
                value["actual_pro_models"] = value.get("actual_pro_models", 0) + 1
            save(self.root / "ledger.json", value)
            return value[kind]


class Progress:
    async def run(self, operation):
        return await operation


class Case:
    def __init__(self, root, case, *, resume=False, attempt=None):
        self.root, self.case = root, case
        self.attempt = attempt or case
        self.directory = root / self.attempt
        self.resume = resume
        self.directory.mkdir(mode=0o700, exist_ok=resume)
        self.agent, self.model, self.limit = CASES[case]
        self.ledger = Ledger(root, case, self.attempt)
        self.calls, self.auth_processes = [], []
        self.backend = None
        self.rejected = False
        self.main_thread = threading.get_ident()
        self.result_path = self.directory / ("result-continuation.json" if resume else "result.json")
        if resume:
            prior = json.loads((self.directory / "result.json").read_text())
            require(prior.get("assertion_code") in ("LEAD_DID_NOT_PROPOSE", "CONTINUATION_DID_NOT_PROPOSE"), "RESUME_REQUIRES_VALID_NONPROPOSAL")
            require(not self.result_path.exists(), "CONTINUATION_ALREADY_EXECUTED")
            self.calls = [json.loads(p.read_text()) for p in sorted(self.directory.glob("attempt-*/summary.json"))]

    def event(self, kind, **facts):
        with (self.directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(dict(kind=kind, at=time.time(), **facts)) + "\n")
            stream.flush()

    async def transport(self, original, vendor, request, **kwargs):
        argv = list(request.command)
        if vendor == "antigravity":
            payload = argv[argv.index("--print") + 1].encode()
            model = argv[argv.index("--model") + 1]
            native_agent_id = argv[argv.index("--agent") + 1]
            matches = tuple(
                semantic_id
                for semantic_id in ROSTER
                if native_agent_id.startswith(semantic_id + "-")
                and len(native_agent_id) == len(semantic_id) + 65
                and all(
                    character in "0123456789abcdef"
                    for character in native_agent_id[len(semantic_id) + 1 :]
                )
            )
            require(
                matches == (self.agent,),
                "UNAPPROVED_SPECIALIST",
            )
            require(model == self.model, "UNAPPROVED_AGY_MODEL")
            schema = argv[argv.index("--json-schema") + 1].encode()
        else:
            payload = request.stdin
            model = argv[argv.index("--model") + 1]
            require(model == "gpt-5.6-sol", "UNAPPROVED_CODEX_MODEL")
            require('model_reasoning_effort="medium"' in argv, "UNAPPROVED_CODEX_EFFORT")
            schema = Path(argv[argv.index("--output-schema") + 1]).read_bytes()
        synthetic = vendor == "antigravity" and self.case == "L631-PRO-AUTH-PTY" and not self.rejected
        number = self.ledger.reserve("synthetic" if synthetic else "real", model=model)
        row = dict(sequence=len(self.calls) + 1, vendor=vendor, model=model,
                   synthetic=synthetic, reservation=number, payload_sha256=sha(payload),
                   schema_sha256=sha(schema), before_spawn=True)
        if vendor == "antigravity":
            row["native_agent_id"] = native_agent_id
        self.calls.append(row)
        directory = self.directory / ("attempt-%02d" % row["sequence"])
        directory.mkdir(mode=0o700)
        save(directory / "command.private.json", dict(argv=argv, cwd=str(request.cwd)))
        (directory / "stdin.private").write_bytes(request.stdin)
        (directory / "payload.private").write_bytes(payload)
        self.event("attempt_reserved", **row)
        if synthetic:
            self.rejected = True
            result = ProcessResult(json.dumps(dict(conversation_id="", status="ERROR", response="",
                error="authentication failed or timed out", duration_seconds=0, num_turns=0,
                usage=dict(input_tokens=0, output_tokens=0, thinking_tokens=0,
                           cache_read_tokens=0, total_tokens=0))).encode() + b"\n", b"", 1)
            row["provenance"] = "synthetic_before_native_spawn"
        else:
            native_create = kwargs.pop("create_process", asyncio.create_subprocess_exec)
            async def spawn(*args, **options):
                child = await native_create(*args, **options)
                row["pid"] = child.pid
                self.event("model_spawned", sequence=row["sequence"], pid=child.pid)
                return child
            try:
                result = await original(dataclasses.replace(request, timeout_seconds=min(180, request.timeout_seconds)),
                                        create_process=spawn, **kwargs)
            except BaseException as failure:
                row["exception"] = type(failure).__name__
                save(directory / "summary.json", row)
                self.event("attempt_failed", **row)
                raise
            row["provenance"] = "unmodified_native_result"
        row.update(returncode=result.returncode, stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr))
        (directory / "stdout.private").write_bytes(result.stdout)
        (directory / "stderr.private").write_bytes(result.stderr)
        save(directory / "summary.json", row)
        self.event("attempt_completed", **row)
        return result

    def auth_factory(self, command, **options):
        native = tuple(command) == ("agy",)
        require(native or tuple(command) in (("agy", "models"), ("codex", "login", "status")),
                "UNAPPROVED_AUTH_COMMAND")
        self.ledger.reserve("login" if native else "probes")
        if native:
            require(all(os.isatty(fd) for fd in (0, 1, 2)), "NATIVE_TTY_REQUIRED")
            require(set(options) == {"cwd", "shell"}, "NATIVE_STREAM_OR_ENV_OVERRIDE")
        child = subprocess.Popen(command, **options)
        self.auth_processes.append(child)
        self.event("native_enter" if native else "probe_spawned", pid=child.pid,
                   tty=native, bare_command=native)
        return child

    def recover(self, provider):
        require(self.case == "L631-PRO-AUTH-PTY" and provider == "antigravity", "UNPLANNED_AUTH")
        require(threading.get_ident() == self.main_thread, "AUTH_NOT_MAIN_THREAD")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError("AUTH_WITH_RUNNING_LOOP")
        auth = ProviderAuthService(self.workspace.root, _process_factory=self.auth_factory)
        baseline = auth.probe(provider)
        self.event("recovery_baseline", state=baseline.state.value, authenticated=baseline.authenticated)
        self.event("recovery_started")
        result = auth.login(provider, recovery=True)
        self.event("native_exit", returncode=result.returncode, authenticated=result.authenticated)
        fresh = auth.probe(provider)
        self.event("recovery_completed", authenticated=fresh.authenticated)
        return result.authenticated and fresh.authenticated

    def open_backend(self, create=False):
        self.backend = AgentBackend(self.workspace, model="gpt-5.6-sol", timeout_seconds=180,
            antigravity_model=self.model, backend_overrides={"lead_orchestrator": "codex", self.agent: "antigravity"})
        if create:
            self.backend.provision_session_resources("live")
            self.backend.create_session_with_access("live")
        else:
            self.backend.acquire_session_access("live")
        self.runtime = StoryRuntime(self.workspace, self.backend)
        self.adapter = InteractiveLeadAdapter(self.runtime.session("live"), self.runner, Progress(),
            None, None, None, recover_authentication=self.recover)

    def snapshot(self):
        artifacts = {str(p.relative_to(self.workspace.root)): sha(p.read_bytes())
            for folder in ("core_documents", "manuscript")
            for p in self.workspace.path(folder).rglob("*") if p.is_file()}
        git = lambda *args: subprocess.check_output(("git", "-C", str(self.workspace.root), *args))
        return dict(artifacts=artifacts, head=git("rev-parse", "HEAD").decode().strip(),
                    index=sha(git("ls-files", "--stage", "-z")),
                    refs=sha(git("show-ref")))

    def history(self):
        return self.runtime.conversation_storage_services.event_store.read("live", self.conversation)

    def turn_checkpoint(self, turn, label):
        self.event("typed_turn", label=label, outcome_kind=turn.kind.value,
            state=turn.state.value if turn.state is not None else None,
            next_action=turn.next_action.value, proposal=turn.proposal is not None,
            artifact_proposal=turn.artifact_proposal is not None,
            artifact_application=turn.artifact_application_request is not None)

    def drafting_request(self):
        directions = {
            "plot_architect": (
                "Draft three causal outline beats leading from the beacon failure to Mara entering "
                "the cellar, with one fair clue before that turning point. Hold Mara's decision, "
                "her relationship with Iona, all world facts, and prose style fixed. Work only on "
                "plot structure; do not invent character motivation or finished scene prose."
            ),
            "character_custodian": (
                "Draft a short character note on Mara's conflicting loyalty to Iona and fear of "
                "breaking her promise. The event sequence and her decision to enter the cellar "
                "are fixed. Work only on character motivation and relationship continuity; "
                "do not add plot events, world rules, or finished scene prose."
            ),
            "execution_specialist": (
                "Draft three sentences of close-third-person scene prose: the beacon fails, "
                "Mara hears Iona's rhyme, and she enters the cellar despite her promise. Keep "
                "that event order, decision, motivation, relationships, and world facts fixed. "
                "Work only on sensory phrasing, sentence rhythm, and narrative perspective."
            ),
        }
        return (
            "Please disclose exactly one bounded, one-round drafting consultation with " + self.agent + ". "
            "The requested material is new: use authoring activity draft and assignment mode draft "
            "with no review basis. This is a brief, not a supplied review candidate. "
            "Mara is a lighthouse keeper who promised her sister Iona never to enter the storm cellar. "
            "Tonight the beacon fails and a voice below repeats Iona's private childhood rhyme. "
            + directions[self.agent] + " Use only this self-contained premise and the single named domain; "
            "hold the other domains fixed. Disclose the exact proposal for my separate structured Confirm. "
            "After that specialist drafts, synthesize the material without another round. "
            "Keep the draft in conversation; do not prepare an artifact candidate, Apply, or Commit."
        )

    def seek_proposal(self):
        self.event("proposal_continuation_started", new_user_turn=True, repeated_call=False)
        text = "Continue this same bounded drafting request. " + self.drafting_request()
        turn = self.adapter._run_operation(self.runtime.lead_turn("live", self.conversation, text))
        self.turn_checkpoint(turn, "proposal_continuation")
        require(turn.proposal is not None, "CONTINUATION_DID_NOT_PROPOSE")
        return turn

    def run(self):
        if self.resume:
            paths = json.loads((self.directory / "workspace.private.json").read_text())
            self.workspace = Workspace(paths["session"])
            events = [json.loads(line) for line in (self.directory / "events.jsonl").read_text().splitlines()]
            self.conversation = next(e["conversation_id"] for e in events if e["kind"] == "conversation_created")
        else:
            main = initialize_test_workspace(self.directory / "workspace")
            resources = provision_session_resources(main.root, "live")
            self.workspace = Workspace(resources.identity.worktree_root)
            for relative in WORKSPACE_DIRECTORIES:
                self.workspace.path(relative).mkdir(parents=True, exist_ok=True)
            manager = ConfigManager(self.workspace)
            baseline = manager.load_configuration_baseline()
            configs = dict(baseline.configuration.agent_configs)
            configs["lead_orchestrator"] = dataclasses.replace(configs["lead_orchestrator"], codex_reasoning_effort="medium")
            manager.commit_configuration(baseline, dataclasses.replace(baseline.configuration, agent_configs=configs))
            save(self.directory / "workspace.private.json", dict(main=str(main.root), session=str(self.workspace.root)))
        with asyncio.Runner() as self.runner, mock.patch.object(codex_adapter, "run_process",
                new=lambda request, **kw: self.transport(codex_adapter_run, "codex", request, **kw)), \
                mock.patch.object(antigravity_adapter, "run_process",
                new=lambda request, **kw: self.transport(antigravity_adapter_run, "antigravity", request, **kw)):
            try:
                self.open_backend(create=not self.resume)
                before = self.snapshot()
                save(self.directory / ("continuation-baseline.json" if self.resume else "baseline.json"), before)
                if not self.resume:
                    self.conversation = self.runtime.conversation_storage_services.catalog.create("live", self.case).conversation_id
                    self.event("conversation_created", conversation_id=self.conversation)
                prompt = self.drafting_request()
                if self.resume:
                    turn = self.seek_proposal()
                else:
                    turn = self.adapter._run_operation(self.runtime.lead_turn("live", self.conversation, prompt))
                    self.turn_checkpoint(turn, "initial")
                    if turn.proposal is None:
                        turn = self.seek_proposal()
                require([a.specialist_id for a in turn.proposal.assignments] == [self.agent], "WRONG_PROPOSAL_ROSTER")
                scope = turn.proposal.authoring_scope
                domain = {"plot_architect": "plot_structure", "character_custodian": "character_world",
                          "execution_specialist": "tone_execution"}[self.agent]
                require(scope is not None and scope.activity.value == "draft"
                        and tuple(item.value for item in scope.domains) == (domain,)
                        and not scope.target_paths, "WRONG_DRAFT_SCOPE")
                assignment = turn.proposal.assignments[0]
                require(assignment.mode.value == "draft" and assignment.review_basis is None,
                        "WRONG_DRAFT_ASSIGNMENT")
                self.event("draft_scope", domain=domain, task_id=scope.task_id, task_revision=scope.task_revision)
                require(not any(c["vendor"] == "antigravity" for c in self.calls), "SPECIALIST_BEFORE_CONFIRM")
                decision = LeadStepDecision(confirmation_request_id=turn.confirmation_request_id,
                    session_id=turn.session_id, conversation_id=turn.conversation_id, step_id=turn.step_id,
                    proposal_id=turn.proposal.proposal_id, proposal_revision=turn.proposal.proposal_revision,
                    proposal_digest=turn.proposal.proposal_digest, kind=CollaborationConfirmationDecision.CONFIRM,
                    revision_request=None)
                self.event("exact_confirm", confirmation_request_id=decision.confirmation_request_id,
                           proposal_digest=decision.proposal_digest)
                result = self.adapter._run_operation(self.runtime.decide_lead_step(decision))
                require(result.turn.state is CollaborationStepState.COMPLETED, "CONSULTATION_NOT_COMPLETED")
                kinds = [e.kind for e in self.history()]
                for kind in (CollaborationEventKind.ASSIGNMENT_DISPATCH, CollaborationEventKind.SPECIALIST_REPLY,
                             CollaborationEventKind.LEAD_SYNTHESIS):
                    require(kinds.count(kind) == 1, "COLLABORATION_EVENT_COUNT_" + kind.value)
                reply = next(e for e in self.history() if type(e.payload) is CollaborationSpecialistReplyPayload)
                synthesis = next(e.payload for e in self.history() if type(e.payload) is CollaborationLeadSynthesisPayload)
                require(reply.payload.mode.value == "draft" and reply.payload.assessment.value == "drafted"
                        and reply.payload.assignment_id == assignment.assignment_id, "SPECIALIST_DRAFT_NOT_CORRELATED")
                require(synthesis.authoring_scope == scope and synthesis.reply_event_ids == (reply.event_id,)
                        and synthesis.proposal_digest == turn.proposal.proposal_digest,
                        "SYNTHESIS_DRAFT_SCOPE_NOT_CORRELATED")
                self.event("synthesis_completed")
                prior_history, prior_calls = self.history(), len(self.calls)
                self.backend.close()
                self.open_backend()
                require(self.history() == prior_history and len(self.calls) == prior_calls, "RESTART_MUTATION")
                try:
                    self.adapter._run_operation(self.runtime.decide_lead_step(decision))
                except ValueError:
                    pass
                else:
                    raise AssertionError("CONSUMED_CONFIRMATION_ACCEPTED")
                require(len(self.calls) == prior_calls, "RESTART_AUTOMATIC_CALL")
                self.event("restart_verified", automatic_calls=0)
                followup = self.adapter._run_operation(self.runtime.lead_turn("live", self.conversation,
                    "Give a factual two-sentence summary of the specialist draft and your synthesis "
                    "already recorded above, with workflow authoring scope. Do not add recommendations, "
                    "draft new story material, propose more consultation, retrieve, or create artifacts."))
                self.turn_checkpoint(followup, "restart_followup")
                require(followup.proposal is None, "FOLLOWUP_NOT_DIRECT")
                require(self.snapshot() == before, "STORY_ARTIFACT_OR_GIT_CHANGED")
                self.verify_exchanges()
                save(self.result_path, dict(outcome="passed", real=sum(not c["synthetic"] for c in self.calls),
                    synthetic=sum(c["synthetic"] for c in self.calls), history_events=len(self.history()),
                    artifacts_and_git_unchanged=True, restart_automatic_calls=0,
                    real_browser_oauth_proven=False, native_stream_recorded=False))
                if self.case == "L631-PRO-MIXED-NOAUTH":
                    require(not self.auth_processes and not self.rejected, "NOAUTH_CASE_STARTED_AUTH")
                    save(self.directory / "no-auth-scope.json", dict(
                        authentication_recovery_tested=False, terminal_behavior_tested=False,
                        synthetic_attempts=0, native_logins=0, purpose="real Pro model collaboration"))
            finally:
                if self.backend is not None:
                    self.backend.close()
                require(not asyncio.all_tasks(self.runner.get_loop()), "PENDING_ASYNC_TASKS")
                require(all(p.poll() is not None for p in self.auth_processes), "UNJOINED_AUTH_PROCESS")

    def verify_exchanges(self):
        records = [json.loads(line) for p in self.workspace.path("debug_logs/agent_exchanges/live").glob("*.jsonl")
                   for line in p.read_text().splitlines()]
        groups = {}
        for row in records:
            groups.setdefault(row["exchange_id"], []).append(row)
        require(len(groups) == len(self.calls), "PHYSICAL_EXCHANGE_COUNT")
        require(all([r["request_type"] for r in rows] == ["request", "response", "outcome"]
                    for rows in groups.values()), "INCOMPLETE_EXCHANGE_LIFECYCLE")
        accounting = [e.payload for e in self.history() if isinstance(e.payload, CollaborationProviderCallPayload)]
        call_ids = {p.call_id for p in accounting}
        require(len(call_ids) == len(self.calls), "PHYSICAL_ACCOUNTING_COUNT")
        if self.case == "L631-PRO-AUTH-PTY":
            agy = [c for c in self.calls if c["vendor"] == "antigravity"]
            require(len(agy) >= 2 and agy[0]["synthetic"] and not agy[1]["synthetic"], "MISSING_REPLAY")
            require(all(agy[0][key] == agy[1][key] for key in ("payload_sha256", "schema_sha256")), "REPLAY_PAYLOAD_DRIFT")
        save(self.directory / "exchange-proof.json", dict(distinct_exchanges=len(groups),
             distinct_accounted_calls=len(call_ids), physical_attempts=len(self.calls), complete_lifecycles=True))


codex_adapter_run = codex_adapter.run_process
antigravity_adapter_run = antigravity_adapter.run_process


def recorded_processes(directory, child_pid=None, group=None):
    """Inspect only PID relationships, never process command-line content."""
    events_path = directory / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    ids = {e["pid"] for e in events if "pid" in e}
    if child_pid is not None:
        ids.add(child_pid)
    rows = [tuple(map(int, line.split())) for line in subprocess.check_output(
        ["ps", "-axo", "pid=,ppid=,pgid="], text=True).splitlines()]
    return [pid for pid, parent, pgid in rows if pid in ids or parent in ids or group is not None and pgid == group]


def wait_draining(child, master, seconds):
    deadline = time.monotonic() + seconds
    while child.poll() is None and time.monotonic() < deadline:
        readable, _, _ = select.select([master], [], [], min(0.1, max(0, deadline-time.monotonic())))
        if readable:
            try:
                os.read(master, 65536)
            except OSError:
                pass
    return child.poll() is not None


def cleanup(child, group, directory, master):
    """Recheck ownership before signals and tolerate races only after absence."""
    for sig, delay in ((signal.SIGINT, 8), (signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        if child.poll() is not None and not recorded_processes(directory, child.pid, group):
            break
        try:
            if sig == signal.SIGINT and child.poll() is None:
                child.send_signal(sig)
            elif recorded_processes(directory, child.pid, group):
                os.killpg(group, sig)
        except (ProcessLookupError, PermissionError):
            if child.poll() is None:
                try:
                    child.send_signal(sig)
                except (ProcessLookupError, PermissionError):
                    require(not recorded_processes(directory, child.pid, group), "OWNED_PROCESS_SIGNAL_DENIED")
            else:
                require(not recorded_processes(directory, child.pid, group), "OWNED_GROUP_SIGNAL_DENIED")
        if not wait_draining(child, master, delay):
            continue
    require(child.poll() is not None, "OWNED_CHILD_NOT_JOINED")
    child.wait(timeout=1)
    remaining = recorded_processes(directory, child.pid, group)
    save(directory / "process-cleanup.json", dict(child_pid=child.pid, pgid=group, remaining=remaining))
    require(not remaining, "OWNED_DESCENDANTS_REMAIN")


def execute(root, case):
    """Own one controlling PTY; discard every terminal byte, including errors."""
    pro_attempt = os.environ.get("STORY_AUTH631_PRO_ATTEMPT")
    attempt = case + "-attempt" + pro_attempt if case == "L631-PRO-AUTH-PTY" and pro_attempt in ("2", "3") else case
    if case == "L631-PRO-AUTH-PTY" and pro_attempt == "4":
        attempt = case + "-attempt4-ctrl-d"
    directory = root / attempt
    for name in ("result-continuation.json", "result.json"):
        candidate = directory / name
        if candidate.exists() and json.loads(candidate.read_text()).get("outcome") == "passed":
            require(not recorded_processes(directory), "REUSE_HAS_LIVE_PROCESS")
            proof = json.loads((directory / "exchange-proof.json").read_text())
            require(proof["complete_lifecycles"] and proof["physical_attempts"] == proof["distinct_accounted_calls"], "REUSE_INCOMPLETE_PROOF")
            save(directory / "no-provider-reuse.json", dict(result_sha256=sha(candidate.read_bytes()),
                 provider_calls=0, terminal_observation="closed_after_prior_hangup", mode_restoration_proven=False))
            return json.loads(candidate.read_text())
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    def control_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.tcsetpgrp(0, os.getpgrp())
    resumed = (directory / "result.json").exists()
    result_path = directory / ("result-continuation.json" if resumed else "result.json")
    require(not result_path.exists() or not resumed, "CONTINUATION_ALREADY_EXECUTED")
    child = subprocess.Popen([sys.executable, "-B", "-m", __name__, "resume" if resumed else "child", str(root), case],
        stdin=slave, stdout=slave, stderr=slave, preexec_fn=control_terminal,
        cwd=str(Path(__file__).resolve().parents[1]), env=dict(os.environ, TERM="xterm-256color"))
    group = os.getpgid(child.pid)
    started = time.monotonic()
    native_started = None
    native_exit_sent = False
    native_exit_sent_at = None
    try:
        while child.poll() is None:
            now = time.monotonic()
            ctrl_d_attempt = attempt.endswith("-attempt4-ctrl-d")
            require(now - started < (400 if ctrl_d_attempt else 600 if attempt.endswith("-attempt3") else CASES[case][2]), "CASE_PARENT_WALL_CAP")
            ledger = json.loads((root / "ledger.json").read_text())
            require(ledger["t0"] is None or time.time() - ledger["t0"] < 3900, "GLOBAL_PARENT_WALL_CAP")
            events_path = directory / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
            entered = [e for e in events if e["kind"] == "native_enter"]
            exited = any(e["kind"] == "native_exit" for e in events)
            if entered and not exited:
                native_started = native_started or now
                require(now - native_started < 300, "NATIVE_LOGIN_WALL_CAP")
                authenticated_baseline = any(e["kind"] == "recovery_baseline" and e["authenticated"] for e in events)
                if authenticated_baseline and now - native_started >= (10 if ctrl_d_attempt else 5) and not native_exit_sent:
                    os.write(master, b"\x04" if ctrl_d_attempt else b"/exit\r")
                    native_exit_sent = True
                    native_exit_sent_at = now
                    save(directory / "native-exit-input.json", dict(authenticated_baseline=True,
                         stabilization_seconds=now-native_started,
                         **({"action": "Ctrl+D"} if ctrl_d_attempt else {"input": "/exit"}),
                         output_parsed=False, native_terminal_content_recorded=False))
                if native_exit_sent_at is not None:
                    require(now - native_exit_sent_at < (20 if ctrl_d_attempt else 15), "NATIVE_EXIT_GRACE_EXHAUSTED")
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    os.read(master, 65536)
                except OSError:
                    pass
        require(child.wait(timeout=5) == 0, "LIVE_CHILD_FAILED_SEE_CONTENT_FREE_RESULT")
        require(not recorded_processes(directory, child.pid, group), "OWNED_PROCESSES_BEFORE_TERMINAL_OBSERVATION")
        try:
            restored = termios.tcgetattr(slave) == original
        except termios.error as failure:
            if failure.args[0] != errno.ENOTTY:
                raise
            save(directory / "terminal-observation.json", dict(state="closed_after_hangup", mode_restoration_proven=False))
        else:
            require(restored, "PTY_TERMINAL_NOT_RESTORED")
            save(directory / "terminal-observation.json", dict(state="attributes_equal", mode_restoration_proven=True))
    finally:
        try:
            cleanup(child, group, directory, master)
        finally:
            os.close(master)
            os.close(slave)
    return json.loads(result_path.read_text())


if __name__ == "__main__":
    os.umask(0o077)
    _, action, directory, selected = sys.argv
    require(action in ("child", "resume") and selected in CASES, "INVALID_LIVE_SELECTION")
    target = Path(directory)
    pro_attempt = os.environ.get("STORY_AUTH631_PRO_ATTEMPT")
    attempt = selected + "-attempt" + pro_attempt if selected == "L631-PRO-AUTH-PTY" and pro_attempt in ("2", "3") else selected
    if selected == "L631-PRO-AUTH-PTY" and pro_attempt == "4":
        attempt = selected + "-attempt4-ctrl-d"
    try:
        Case(target, selected, resume=action == "resume", attempt=attempt).run()
    except BaseException as failure:
        frames = []
        tb = failure.__traceback__
        while tb is not None:
            frames.append(dict(file=Path(tb.tb_frame.f_code.co_filename).name,
                               function=tb.tb_frame.f_code.co_name, line=tb.tb_lineno))
            tb = tb.tb_next
        save(target / attempt / ("result-continuation.json" if action == "resume" else "result.json"), dict(outcome="failed", exception=type(failure).__name__,
             assertion_code=str(failure) if re.fullmatch(r"[A-Z_]+", str(failure)) else None,
             fingerprint=sha(str(failure).encode()), frames=frames, native_stream_recorded=False))
        raise SystemExit(1)
