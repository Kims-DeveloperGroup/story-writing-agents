"""Opt-in #644 synthetic semantic evaluation with a fresh eight-call ledger."""
from __future__ import annotations
import asyncio
import base64
import dataclasses
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback
from unittest import mock

from story_writing_agents import AgentBackend, StoryRuntime, Workspace
from story_writing_agents import codex_adapter, antigravity_adapter
from story_writing_agents.agents import ROSTER
from story_writing_agents import conversations as c
from story_writing_agents.workspace import WORKSPACE_DIRECTORIES, initialize_workspace
from story_writing_agents.workspace.initialization import provision_session_resources

CASES = (
    ("empty-outline-ko", ("plot_architect",), True,
     "비어 있는 core_documents/global_outline.md의 글로벌 아웃라인 초안을 먼저 완성하고 인물 시트는 다음 별도 작업으로 남겨 주세요. "
     "기획 브리프: 짧은 탐색 이야기입니다. 도입에서 잘못된 지도가 발견되고, 중간에서 두 경로의 증거를 비교하고, 마지막에서 올바른 길을 선택한 결과를 보여 주세요. "
     "이번에는 줄거리 순서와 인과 구조만 다룹니다. 충분한 이 브리프로 담당 전문가의 초안 작성을 제안하고 확인을 기다려 주세요. 파일 변경은 요청하지 않습니다."),
    ("empty-character", ("character_custodian",), True,
     "Draft only the empty core_documents/character_sheets.md from this brief: Iri is a careful adult map archivist who values accuracy, fears misleading travelers, wants to earn colleagues' trust, and struggles to admit uncertainty. "
     "The world has a public map archive with a rule requiring two independent checks. Propose the needed drafting work, wait for confirmation, then return draft material for Lead integration. No plot, prose, or file changes in this task."),
    ("mixed-proposal", ("plot_architect", "character_custodian", "execution_specialist"), False,
     "Propose drafting a new opening scene at manuscript/part-01/opening.md. The causal order is discovery, investigation, then decision; the archivist's motivation is trust versus fear of error; the prose should use restrained close third person. "
     "The scope includes plot structure, character/world motivation, and execution/prose. Disclose every affected specialist and wait for confirmation."),
    ("workflow-direct", (), False,
     "Explain the runtime confirmation difference between starting a specialist draft, reviewing a final artifact candidate, and applying that candidate. This is a workflow mechanics question; do not perform story work or propose a consultation."),
)


def source_digest():
    root=Path(__file__).resolve().parents[1]
    paths=[p for folder in (root/'src',root/'config') for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths.append(Path(__file__))
    return hashlib.sha256(b''.join(str(p.relative_to(root)).encode()+hashlib.sha256(p.read_bytes()).digest() for p in sorted(paths))).hexdigest()


def decision(turn):
    p=turn.proposal
    return c.LeadStepDecision(session_id=turn.session_id,conversation_id=turn.conversation_id,step_id=turn.step_id,
        confirmation_request_id=turn.confirmation_request_id,proposal_id=p.proposal_id,proposal_revision=p.proposal_revision,
        proposal_digest=p.proposal_digest,kind=c.CollaborationConfirmationDecision.CONFIRM)


async def evaluate(output: Path, previous_ledger: Path | None = None, *, setup_only: bool = False):
    if os.environ.get('STORY_ROUTING_644_LIVE') != '1':
        raise RuntimeError('Explicit #644 provider opt-in is required')
    output.mkdir(parents=True,exist_ok=True)
    ledger_path=output/'provider-ledger.json'
    if ledger_path.exists():
        raise RuntimeError('A fresh ledger is required; prior attempts cannot be replayed')
    started=time.monotonic();digest=source_digest()
    ledger={'authorization':'644-tests-v4','max_calls':8,'max_seconds':300,'source_digest':digest,
            'calls':[],'cases':[],'unexecuted':[x[0] for x in CASES]}
    if previous_ledger is not None:
        previous=json.loads(previous_ledger.read_text())
        if previous['actual_physical_calls'] != 0:
            raise RuntimeError('This continuation requires a zero-call prior setup failure')
        ledger['explicit_continuation']={'prior_ledger':str(previous_ledger.resolve()),'prior_sha256':hashlib.sha256(previous_ledger.read_bytes()).hexdigest(),
            'prior_seconds':previous['seconds']+previous.get('explicit_continuation',{}).get('prior_seconds',0),'prior_physical_calls':0,'approved_cumulative_seconds':360,'remaining_attempt_seconds':300}
    def save():
        ledger['seconds']=time.monotonic()-started
        ledger_path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
    save()
    def guard(original,vendor):
        async def invoke(request,**kwargs):
            if source_digest()!=digest: raise RuntimeError('Evaluation source changed')
            remaining=300-(time.monotonic()-started)
            if len(ledger['calls'])>=8 or remaining<=2: raise RuntimeError('Authorized provider budget exhausted')
            record={'ordinal':len(ledger['calls'])+1,'vendor':vendor,'spawned':False,
                    'payload_sha256':hashlib.sha256(request.stdin).hexdigest()}
            ledger['calls'].append(record);save()
            create=kwargs['create_process']
            async def tracked(*args,**options):
                process=await create(*args,**options)
                record['spawned']=True;save();return process
            kwargs['create_process']=tracked
            try:
                result=await original(replace(request,timeout_seconds=min(request.timeout_seconds,remaining-2)),**kwargs)
                record['returncode']=result.returncode
                record['stdout_sha256']=hashlib.sha256(result.stdout).hexdigest()
                return result
            except BaseException as failure:
                record['error_type']=type(failure).__name__;raise
            finally:save()
        return invoke
    backend=None
    temporary=None
    try:
        temporary=tempfile.TemporaryDirectory(prefix='routing-644-live-')
        directory=temporary.name
        root=Path(directory).resolve()
        main=initialize_workspace(root/'workspace')
        resources=provision_session_resources(main.root,'synthetic')
        workspace=Workspace(resources.identity.worktree_root)
        if not workspace.root.is_relative_to(root):raise RuntimeError('Disposable workspace isolation failed')
        for relative in WORKSPACE_DIRECTORIES:
            workspace.path(relative).mkdir(parents=True,exist_ok=True)
        backend=AgentBackend(workspace,timeout_seconds=40)
        backend.provision_session_resources('synthetic')
        backend.create_session_with_access('synthetic')
        ledger['requested_execution_profiles']=[{'role':p.agent_id,'provider':p.provider,'model':p.model,'effort':p.effort,
            'provider_source':p.provider_source.value,'model_source':p.model_source.value,'effort_source':p.effort_source.value}
            for p in backend.execution_profiles()]
        ledger['resolved_model_limitation']='Requested typed profiles are recorded; provider-resolved model identity is not independently verified.'
        save()
        runtime=StoryRuntime(workspace,backend)
        artifact_baseline=tuple((x.path,x.content) for x in runtime.artifacts.capture_complete_state())
        git_baseline=subprocess.check_output(['git','rev-parse','HEAD'],cwd=workspace.root)
        index_baseline=subprocess.check_output(['git','ls-files','--stage','-z'],cwd=workspace.root)
        if setup_only:
            runtime.conversation_storage_services.catalog.create('synthetic','setup-preflight')
            ledger['setup_preflight_passed']=True
            return ledger
        with mock.patch.object(codex_adapter,'run_process',guard(codex_adapter.run_process,'codex')), mock.patch.object(antigravity_adapter,'run_process',guard(antigravity_adapter.run_process,'antigravity')):
            for case_id,roles,confirm,text in CASES:
                before=len(ledger['calls'])
                conversation=runtime.conversation_storage_services.catalog.create('synthetic',case_id).conversation_id
                result=await runtime.lead_turn('synthetic',conversation,text)
                row={'id':case_id,'outcome':result.kind.value,'next_action':result.next_action.value,'initial_lead_message':result.message,
                     'roles':list(result.proposal.participants) if result.proposal else [],'passed':False}
                ledger['cases'].append(row)
                history=runtime.conversation_storage_services.event_store.read('synthetic',conversation)
                row['failure_codes']=[e.payload.failure_code.value for e in history if getattr(e.payload,'failure_code',None) is not None]
                save()
                if roles:
                    if result.proposal is None or result.proposal.participants!=roles:raise AssertionError('Declared specialist routing differs from selected case')
                    row['scope']=c.authoring_scope_to_dict(result.proposal.authoring_scope)
                    if confirm:
                        if any(a.mode.value!='draft' for a in result.proposal.assignments):raise AssertionError('Empty-target authoring was not draft mode')
                        confirmed=await runtime.decide_lead_step(decision(result))
                        row['terminal_state']=None if confirmed.turn.state is None else confirmed.turn.state.value
                        row['terminal_message']=confirmed.turn.message
                        row['terminal_next_action']=confirmed.turn.next_action.value
                        history=runtime.conversation_storage_services.event_store.read('synthetic',conversation)
                        replies=[e for e in history if type(e.payload) is c.CollaborationSpecialistReplyPayload]
                        row['lead_synthesis']=[e.payload.message for e in history if type(e.payload) is c.CollaborationLeadSynthesisPayload]
                        row['replies']=[{'role':e.sender.agent_id,'mode':e.payload.mode.value,'assessment':e.payload.assessment.value,'material_bytes':len(e.payload.message.encode()),'message':e.payload.message} for e in replies]
                        if confirmed.turn.state is not c.CollaborationStepState.COMPLETED or len(replies)!=1 or replies[0].payload.assessment is not c.CollaborationSpecialistAssessment.DRAFTED:raise AssertionError('Selected draft flow did not return completed typed draft material')
                elif result.proposal is not None or result.next_action is c.CollaborationNextAction.RETRY_LEAD_TURN:
                    raise AssertionError('Workflow-only case did not return a direct response')
                if tuple((x.path,x.content) for x in runtime.artifacts.capture_complete_state())!=artifact_baseline:raise AssertionError('Provider evaluation changed artifacts')
                if subprocess.check_output(['git','rev-parse','HEAD'],cwd=workspace.root)!=git_baseline:raise AssertionError('Provider evaluation changed Git HEAD')
                if subprocess.check_output(['git','ls-files','--stage','-z'],cwd=workspace.root)!=index_baseline:raise AssertionError('Provider evaluation changed Git index')
                history=runtime.conversation_storage_services.event_store.read('synthetic',conversation)
                row['submitted_roles']=[e.payload.role for e in history if type(e.payload) is c.CollaborationProviderCallPayload and e.payload.state is c.CollaborationProviderCallState.SUBMITTED]
                row.update(passed=True,calls=len(ledger['calls'])-before)
                ledger['unexecuted'].remove(case_id);save()
    except BaseException as failure:
        ledger['failure']={'type':type(failure).__name__,'message':str(failure)[:240]}
    finally:
        if backend is not None:backend.close()
        if temporary is not None:temporary.cleanup()
        ledger['actual_physical_calls']=sum(call['spawned'] for call in ledger['calls'])
        ledger['passed']='failure' not in ledger and (ledger.get('setup_preflight_passed',False) if setup_only else not ledger['unexecuted'])
        save()
    return ledger


RUNTIME_DRAFT_NOTICE = 'Draft material is available for Lead integration and concrete review. Story authoring is not complete; no Apply is authorized.'


class EvidenceJournal:
    """Retain observations before checks, including failures during finalization."""

    def __init__(self, output, ledger):
        self.output, self.ledger = Path(output), ledger
        self.output.mkdir(parents=True, exist_ok=True)
        ledger.setdefault('assertions', [])
        ledger.setdefault('additional_failures', [])

    def write(self, name, value):
        def encode(item):
            if dataclasses.is_dataclass(item):
                return dataclasses.asdict(item)
            if isinstance(item, bytes):
                return {'base64': base64.b64encode(item).decode(), 'sha256': hashlib.sha256(item).hexdigest()}
            raise TypeError(f'Unsupported evidence value: {type(item).__name__}')
        target = self.output / name
        temporary = target.with_name(target.name + '.tmp')
        try:
            temporary.write_text(json.dumps(value, default=encode, ensure_ascii=False, indent=2))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def check(self, name, predicate, message):
        row = {'id': name, 'status': 'running'}
        self.ledger['assertions'].append(row)
        self.write('assertions.json', self.ledger['assertions'])
        try:
            if not predicate():
                raise AssertionError(message)
        except BaseException as error:
            row.update(status='failed', error_type=type(error).__name__, message=str(error))
            raise
        else:
            row['status'] = 'passed'
        finally:
            self.finalize('assertion_capture', lambda: self.write('assertions.json', self.ledger['assertions']))

    def capture(self, stage, events, result=None):
        # Encoded history includes exact candidate operations and objective facts.
        (self.output / f'{stage}-events.jsonl').write_bytes(
            b'\n'.join(c.encode_collaboration_event(event) for event in events) + b'\n')
        self.write(f'{stage}-result.json', result)

    def finalize(self, name, action):
        try:
            action()
        except BaseException as error:
            self.ledger['passed'] = False
            self.ledger['additional_failures'].append({
                'id': name, 'type': type(error).__name__, 'message': str(error),
                'traceback': traceback.format_exc()})


async def observe_provider_call(original, request, directory, row, **kwargs):
    """Capture exact native bytes and elapsed time without rewriting the result."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    row.update(started_monotonic=started, timeout_seconds=request.timeout_seconds, status='submitted')
    (directory / 'stdin.raw').write_bytes(request.stdin)
    primary = None
    try:
        result = await original(request, **kwargs)
        for name in ('stdout', 'stderr'):
            raw = getattr(result, name)
            (directory / f'{name}.raw').write_bytes(raw)
            row[name + '_sha256'] = hashlib.sha256(raw).hexdigest()
        command = list(request.command)
        if '--output-last-message' in command:
            native_path = Path(command[command.index('--output-last-message') + 1])
            if native_path.is_file():
                (directory / 'native-result.raw').write_bytes(native_path.read_bytes())
        row.update(returncode=result.returncode, status='returned')
        return result
    except BaseException as error:
        primary = error
        row.update(status='failed', error_type=type(error).__name__, traceback=traceback.format_exc())
        raise
    finally:
        row['seconds'] = time.monotonic() - started
        try:
            (directory / 'observation.json').write_text(json.dumps(row, indent=2))
        except BaseException as error:
            row['capture_failure'] = {'type': type(error).__name__, 'message': str(error)}
            if primary is None:
                raise


def objective_source_fingerprint():
    """Freeze current production, this harness, published fixtures and installed RAG."""
    import sys
    root = Path(__file__).resolve().parents[1]
    paths = {str(p.relative_to(root)): p for folder in (root / 'src', root / 'config',
             root / 'tests/fixtures/workspace_policy_baselines')
             for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    paths['tests/specialist_routing_live.py'] = Path(__file__)
    site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    for folder in (site / 'generic_rag', site / 'generic_rag-0.1.0.dist-info'):
        for p in folder.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                paths['installed/' + str(p.relative_to(site))] = p
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(paths.items())}


async def evaluate_objective(output: Path, *, setup_only: bool = False):
    """One v4 recommendation renewal; setup mode forbids every native provider launch."""
    if not setup_only and os.environ.get('STORY_ROUTING_644_LIVE') != '1':
        raise RuntimeError('Explicit #644 provider opt-in is required')
    if setup_only and os.environ.get('STORY_ROUTING_644_SETUP') != '1':
        raise RuntimeError('Explicit provider-free setup opt-in is required')
    started = time.monotonic()
    maximum_seconds = 60 if setup_only else 260
    output.mkdir(parents=True, exist_ok=True)
    ledger_path = output / 'provider-ledger.json'
    if ledger_path.exists():
        raise RuntimeError('A fresh ledger is required; prior attempts cannot be replayed')
    fingerprint = objective_source_fingerprint()
    ledger = {'authorization': '644-v17-objective-renewal', 'setup_only': setup_only,
              'historical_calls': 19, 'historical_seconds': 529.804833790753,
              'max_calls': 0 if setup_only else 6, 'max_seconds': maximum_seconds,
              'inputs': fingerprint, 'calls': [], 'native_launches': [], 'stages': [],
              'unexecuted': ['draft_proposal', 'confirmed_draft', 'recommendation_prepare'],
              'wire_contract': 'lead-response-v6', 'passed': False}
    backend = None
    temporary = None
    temporary_root = None
    journal = EvidenceJournal(output, ledger)
    check_unchanged = None
    history = None

    def save():
        ledger['seconds'] = time.monotonic() - started
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2))

    ledger['planned_assertions'] = ['Synthesis must disclose scoped artifact recommendation', 'Preparation requires exact structured recommendation Confirm', 'Native synthesis must disclose current synthesis contract', 'Confirmed recommendation must disclose exact Plot candidate review', 'Current task synthesis missing', 'Disposable session escaped its private root', 'Draft did not complete', 'Draft/preparation cannot satisfy objective', 'Durable synthesis source digest mismatch', 'Empty existing outline requires exact replace', 'Empty outline requires current scoped draft', 'Expected one actual Plot draft', 'Final candidate requires separate review', 'Initial objective must select CONSULT', 'Native request did not disclose the current ten-field contract', 'Objective candidate digest mismatch', 'Objective did not bind PREPARE candidate', 'Objective identity changed', 'Preparation changed task/revision', 'Preparation did not select exact durable synthesis', 'Prepared candidate contains runtime receipt', 'Prepared candidate must contain substantive draft content', 'Published fixture bytes drifted', 'Renewal changed Git HEAD/index', 'Renewal changed canonical artifact bytes', 'Renewal changed main/session policy bytes', 'Renewal must disclose only Plot drafting', 'Renewal must retain one objective', 'Renewal source/dependency input drift', 'Review must bind the same exact candidate input', 'Setup attempted provider work', 'Substantive draft/synthesis missing', 'Unconfirmed final review cannot authorize Apply', 'Wrong published fixture identity']

    def assertion(name, predicate):
        def run():
            try:
                journal.check(ledger.get('active_stage', 'setup') + ':' + name, predicate, name)
            finally:
                if ledger['assertions']:
                    ledger['assertions'][-1]['name'] = name
        if ledger.get('active_stage') == 'final_preservation':
            journal.finalize(name, run)
        else:
            run()

    def remaining():
        return maximum_seconds - (time.monotonic() - started)

    def guard(original, vendor):
        async def invoke(request, **kwargs):
            if setup_only:
                ledger.setdefault('forbidden_native_attempts', []).append(vendor)
                save()
                raise AssertionError('Setup-only mode forbids provider process invocation')
            if objective_source_fingerprint() != fingerprint:
                raise RuntimeError('Evaluation input changed before provider invocation')
            if len(ledger['calls']) >= 6 or remaining() <= 5:
                raise RuntimeError('Authorized provider call/time budget exhausted')
            row = {'ordinal': len(ledger['calls']) + 1, 'vendor': vendor,
                   'payload_sha256': hashlib.sha256(request.stdin).hexdigest(),
                   'contains_v6': b'lead-response-v6' in request.stdin,
                   'contains_summary_v4': b'consultation-summary-v4' in request.stdin,
                   'contains_ten_lead_fields': all(field.encode() in request.stdin for field in
                    ('scope', 'authoring_scope', 'evidence_status', 'evidence_ids', 'kind',
                     'message', 'artifact_intent', 'proposal', 'objective_intent', 'recommended_action'))}
            ledger['calls'].append(row)
            save()
            create = kwargs['create_process']

            async def tracked(*args, **options):
                if len(ledger['native_launches']) >= 6 or remaining() <= 5:
                    raise RuntimeError('Native launch budget exhausted')
                launch = {'ordinal': len(ledger['native_launches']) + 1,
                          'call_ordinal': row['ordinal'], 'spawned': False}
                ledger['native_launches'].append(launch)
                save()
                process = await create(*args, **options)
                launch.update(spawned=True, pid=process.pid)
                save()
                return process

            kwargs['create_process'] = tracked
            try:
                result = await observe_provider_call(original, replace(request, timeout_seconds=min(
                    request.timeout_seconds, 40, remaining() - 5)),
                    output / 'calls' / f"{row['ordinal']:02}", row, **kwargs)
                row.update(returncode=result.returncode,
                           stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
                return result
            except BaseException as failure:
                row['error_type'] = type(failure).__name__
                raise
            finally:
                save()
        return invoke

    save()
    try:
        with (mock.patch.object(codex_adapter, 'run_process', guard(codex_adapter.run_process, 'codex')),
              mock.patch.object(antigravity_adapter, 'run_process', guard(antigravity_adapter.run_process, 'antigravity'))):
            from story_writing_agents.workspace import initialization
            from story_writing_agents.workspace.schema import WorkspaceScaffoldEntry, canonical_initializer_scaffold
            fixtures = Path(__file__).parent / 'fixtures/workspace_policy_baselines/published_main_1e482fe'
            provenance = json.loads((fixtures / 'manifest.json').read_text())
            assertion('Wrong published fixture identity', lambda: not (provenance['source_commit'] != '1e482fe9ed37f94da0e747a8cb54240af2624f5c'))
            policies = {name: (fixtures / row['fixture']).read_bytes()
                        for name, row in provenance['files'].items()}
            assertion('Published fixture bytes drifted', lambda: not (any((hashlib.sha256(policies[name]).hexdigest() != row['sha256'] for name, row in provenance['files'].items()))))
            scaffold = tuple(WorkspaceScaffoldEntry(e.path, policies.get(e.path, e.content))
                             for e in canonical_initializer_scaffold())
            temporary = tempfile.TemporaryDirectory(prefix='routing-644-objective-')
            temporary_root = Path(temporary.name).resolve()
            # Substitute independently captured bytes only during original baseline creation.
            with mock.patch.object(initialization, 'canonical_initializer_scaffold', return_value=scaffold):
                main = initialize_workspace(temporary_root / 'workspace')
            main.require_ready()
            resources = provision_session_resources(main.root, 'synthetic')
            workspace = Workspace(resources.identity.worktree_root).require_ready()
            assertion('Disposable session escaped its private root', lambda: not (not workspace.root.is_relative_to(temporary_root)))
            for relative in WORKSPACE_DIRECTORIES:
                workspace.path(relative).mkdir(parents=True, exist_ok=True)
            backend = AgentBackend(workspace, timeout_seconds=40)
            backend.provision_session_resources('synthetic')
            backend.create_session_with_access('synthetic')
            ledger['requested_execution_profiles'] = [
                {'role': p.agent_id, 'provider': p.provider, 'model': p.model, 'effort': p.effort}
                for p in backend.execution_profiles()]
            ledger['resolved_model_limitation'] = 'Requested profiles recorded; provider-resolved model identity not independently verified.'
            runtime = StoryRuntime(workspace, backend)
            conversation = runtime.conversation_storage_services.catalog.create(
                'synthetic', 'Published objective renewal').conversation_id
            ledger['published_commit'] = provenance['source_commit']
            ledger['temporary_root'] = str(temporary_root)
            baseline_artifacts = tuple((a.path, a.content) for a in runtime.artifacts.capture_complete_state())
            baseline_policy = {(str(root), name): (root / name).read_bytes()
                               for root in (main.root, workspace.root) for name in policies}
            baseline_git = {(str(root), command): subprocess.check_output(['git', *command], cwd=root)
                            for root in (main.root, workspace.root)
                            for command in (('rev-parse', 'HEAD'), ('ls-files', '--stage', '-z'))}

            def check_unchanged():
                assertion('Renewal changed canonical artifact bytes', lambda: not (tuple(((a.path, a.content) for a in runtime.artifacts.capture_complete_state())) != baseline_artifacts))
                assertion('Renewal changed main/session policy bytes', lambda: not (any(((Path(root) / name).read_bytes() != content for (root, name), content in baseline_policy.items()))))
                assertion('Renewal changed Git HEAD/index', lambda: not (any((subprocess.check_output(['git', *command], cwd=root) != value for (root, command), value in baseline_git.items()))))
                assertion('Renewal source/dependency input drift', lambda: not (objective_source_fingerprint() != fingerprint))

            def history():
                return runtime.conversation_storage_services.event_store.read('synthetic', conversation)

            def objective(expected_id=None):
                projected = c.project_collaboration_history(history())
                assertion('Renewal must retain one objective', lambda: not (len(projected.artifact_objectives) != 1))
                value = projected.artifact_objectives[0]
                assertion('Draft/preparation cannot satisfy objective', lambda: not (value.state is not c.ArtifactObjectiveState.OPEN))
                assertion('Objective identity changed', lambda: not (expected_id is not None and value.facts.objective_id != expected_id))
                return value

            if setup_only:
                check_unchanged()
                assertion('Setup attempted provider work', lambda: not (ledger['calls'] or ledger.get('forbidden_native_attempts')))
                ledger['setup_preflight_passed'] = True
            else:
                async with asyncio.timeout(max(0.01, remaining() - 5)):
                    brief = (
                        '비어 있는 core_documents/global_outline.md에 다음 세 장면의 인과 구조만 초안으로 작성해 주세요. '
                        '잘못된 지도를 발견하고, 두 경로의 증거를 비교하고, 올바른 길을 선택한 결과로 끝납니다. '
                        '이 문서 작성 목표를 시작하고 담당 Plot 전문가의 초안 작성 협의를 제안한 뒤 확인을 기다려 주세요. '
                        '인물/세계관 설정과 본문 문체는 이번 범위에 포함하지 않습니다. '
                        '초안 협의가 완료되면 종합 답변에서 현재 목표를 이어가는 prepare_artifact recommended_action을 제시하세요. '
                        'source_ids는 current_response 하나로 전체 종합 초안을 가리키고 target_paths는 global_outline.md만 포함하세요. '
                        '그 추천에 대한 별도 확인 이후에만 후보를 준비하며 지금 파일을 바꾸지는 않습니다.'
                    )
                    ledger['active_stage'] = 'draft_proposal'
                    proposed = await runtime.lead_turn('synthetic', conversation, brief)
                    journal.capture('draft_proposal', history(), proposed)
                    assertion('Renewal must disclose only Plot drafting', lambda: not (proposed.proposal is None or proposed.proposal.participants != ('plot_architect',)))
                    assertion('Empty outline requires current scoped draft', lambda: not (any((a.mode.value != 'draft' for a in proposed.proposal.assignments))))
                    obj = objective()
                    assertion('Initial objective must select CONSULT', lambda: not (obj.next_step is not c.ArtifactObjectiveNextStep.CONSULT))
                    task = proposed.proposal.authoring_scope
                    ledger['objective_id'] = obj.facts.objective_id
                    ledger['authoring_scope'] = c.authoring_scope_to_dict(task)
                    ledger['stages'].append({'id': 'draft_proposal', 'message': proposed.message})
                    ledger['unexecuted'].remove('draft_proposal')
                    check_unchanged(); save()
                    ledger['active_stage'] = 'confirmed_draft'
                    completed = await runtime.decide_lead_step(decision(proposed))
                    journal.capture('confirmed_draft', history(), completed)
                    assertion('Draft did not complete', lambda: any(
                        type(e.payload) is c.CollaborationStepStatePayload
                        and e.step_id == proposed.step_id
                        and e.payload.state is c.CollaborationStepState.COMPLETED for e in history()))
                    events = history()
                    replies = [e for e in events if type(e.payload) is c.CollaborationSpecialistReplyPayload]
                    syntheses = [e for e in events if type(e.payload) is c.CollaborationLeadSynthesisPayload]
                    assertion('Expected one actual Plot draft', lambda: not (len(replies) != 1 or replies[0].sender.agent_id != 'plot_architect' or replies[0].payload.assessment is not c.CollaborationSpecialistAssessment.DRAFTED))
                    assertion('Substantive draft/synthesis missing', lambda: not (len(syntheses) != 1 or len(replies[0].payload.message.strip()) < 40))
                    synthesis = syntheses[0]
                    assertion('Current task synthesis missing', lambda: not (synthesis.payload.authoring_scope != task or len(synthesis.payload.message.strip()) < 40))
                    selected = next(s for s in c.project_collaboration_history(events).offered_draft_sources
                                    if s.event_id == synthesis.event_id)
                    source_digest_value = 'sha256:' + hashlib.sha256(synthesis.payload.message.encode()).hexdigest()
                    assertion('Durable synthesis source digest mismatch', lambda: not (selected.content_digest != source_digest_value))
                    objective(obj.facts.objective_id)
                    ledger['stages'].append({'id': 'confirmed_draft', 'draft': replies[0].payload.message,
                        'durable_synthesis': synthesis.payload.message, 'displayed_turn': completed.turn.message,
                        'source_event_id': synthesis.event_id, 'source_digest': selected.content_digest})
                    ledger['unexecuted'].remove('confirmed_draft')
                    check_unchanged(); save()
                    ledger['active_stage'] = 'recommendation_prepare'
                    request = completed.turn.recommendation_request
                    assertion('Synthesis must disclose scoped artifact recommendation', lambda:
                        request is not None and request.kind is c.LeadRecommendationActionKind.PREPARE_ARTIFACT
                        and request.authoring_scope == task and request.objective_id == obj.facts.objective_id
                        and request.target_paths == ('core_documents/global_outline.md',)
                        and request.draft_sources == (selected,))
                    choice = c.LeadRecommendationDecision(
                        session_id='synthetic', conversation_id=conversation,
                        request_event_id=request.request_event_id, request_tail_sequence=request.tail_sequence,
                        request_id=request.request_id, action_id=request.action_id,
                        action_revision=request.action_revision, action_digest=request.action_digest,
                        kind=c.CollaborationConfirmationDecision.CONFIRM)
                    user_count = sum(type(e.payload) is c.CollaborationMessagePayload
                                     and e.direction is c.CollaborationDirection.USER_TO_LEAD for e in history())
                    prepared = await runtime.decide_lead_recommendation(choice)
                    journal.capture('recommendation_prepare', history(), prepared)
                    decisions = [e.payload.decision for e in history() if type(e.payload) is c.LeadRecommendationDecisionPayload]
                    assertion('Preparation requires exact structured recommendation Confirm', lambda:
                        decisions == [choice] and sum(type(e.payload) is c.CollaborationMessagePayload
                            and e.direction is c.CollaborationDirection.USER_TO_LEAD for e in history()) == user_count)
                    assertion('Native synthesis must disclose current synthesis contract', lambda:
                        any(row['contains_summary_v4'] for row in ledger['calls']))
                    assertion('Confirmed recommendation must disclose exact Plot candidate review', lambda: not (prepared.proposal is None or prepared.artifact_proposal is None or prepared.proposal.participants != ('plot_architect',)))
                    scope = prepared.proposal.authoring_scope
                    assertion('Preparation changed task/revision', lambda: not ((scope.task_id, scope.task_revision) != (task.task_id, task.task_revision)))
                    assertion('Final candidate requires separate review', lambda: not (any((a.mode.value != 'review' for a in prepared.proposal.assignments))))
                    obj = objective(obj.facts.objective_id)
                    assertion('Objective did not bind PREPARE candidate', lambda: not (obj.next_step is not c.ArtifactObjectiveNextStep.PREPARE or obj.candidate_binding is None))
                    assertion('Preparation did not select exact durable synthesis', lambda: not (obj.draft_sources != (selected,)))
                    candidate_event = next(e for e in history() if e.event_id == obj.candidate_binding.candidate_event_id)
                    candidate = candidate_event.payload.candidate
                    assertion('Objective candidate digest mismatch', lambda: not (candidate.candidate_digest != obj.candidate_binding.candidate_digest))
                    operations = candidate.change_set.operations
                    assertion('Empty existing outline requires exact replace', lambda: not (len(operations) != 1 or operations[0].path != 'core_documents/global_outline.md' or operations[0].operation_kind.value != 'replace'))
                    assertion('Prepared candidate must contain substantive draft content', lambda: not (len(operations[0].content.decode('utf-8').strip()) < 40))
                    assertion('Prepared candidate contains runtime receipt', lambda: not (RUNTIME_DRAFT_NOTICE.encode() in operations[0].content))
                    ledger['candidate_semantic_review'] = 'pending_root_review_of_retained_draft_and_candidate'
                    candidate_inputs = [i.input_id for i in prepared.proposal.basis.inputs if i.kind is c.CollaborationInputKind.ARTIFACT_CANDIDATE]
                    assertion('Review must bind the same exact candidate input', lambda: not (len(candidate_inputs) != 1 or any((a.review_basis.candidate_input_ids != tuple(candidate_inputs) for a in prepared.proposal.assignments))))
                    assertion('Unconfirmed final review cannot authorize Apply', lambda: not (prepared.artifact_application_request is not None or prepared.story_commit_request is not None))
                    assertion('Native request did not disclose the current ten-field contract', lambda: not (not any((row['contains_v4'] and row['contains_ten_lead_fields'] for row in ledger['calls']))))
                    ledger['stages'].append({'id': 'recommendation_prepare', 'candidate_digest': candidate.candidate_digest,
                                            'objective_state': obj.state.value, 'review_roles': list(prepared.proposal.participants)})
                    ledger['unexecuted'].remove('recommendation_prepare')
                    check_unchanged(); save()
    except BaseException as failure:
        import traceback
        ledger['failure'] = {'type': type(failure).__name__, 'message': str(failure), 'traceback': traceback.format_exc()}
    finally:
        ledger['active_stage'] = 'final_preservation'
        if history is not None:
            journal.finalize('final_history_capture', lambda: journal.capture('final', history()))
        if check_unchanged is not None:
            journal.finalize('final_invariant_checks', check_unchanged)
        else:
            ledger['final_invariants'] = 'not_reached: setup baseline unavailable'
        if backend is not None:
            journal.finalize('backend_close', backend.close)
        if temporary is not None:
            journal.finalize('temporary_cleanup', temporary.cleanup)
        ledger['temporary_removed'] = temporary_root is None or not temporary_root.exists()
        ledger['actual_physical_calls'] = sum(row['spawned'] for row in ledger['native_launches'])
        ledger['inputs_unchanged'] = objective_source_fingerprint() == fingerprint
        ledger['unexecuted_assertions'] = sorted(set(ledger['planned_assertions']) -
            {row['name'] for row in ledger['assertions'] if 'name' in row})
        ledger['passed'] = ('failure' not in ledger and not ledger['additional_failures']
                            and ledger['temporary_removed'] and ledger['inputs_unchanged']
                            and remaining() >= 0 and (ledger.get('setup_preflight_passed', False) if setup_only else not ledger['unexecuted']))
        ledger['semantic_acceptance'] = 'not_assessed_by_harness'
        save()
    return ledger



if __name__=='__main__':
    import sys
    if sys.argv[1] in ('--objective-renewal', '--objective-setup'):
        report = asyncio.run(evaluate_objective(Path(sys.argv[2]), setup_only=sys.argv[1] == '--objective-setup'))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(asyncio.run(evaluate(Path(sys.argv[1]),Path(sys.argv[2]) if len(sys.argv)>2 else None)),ensure_ascii=False,indent=2))
