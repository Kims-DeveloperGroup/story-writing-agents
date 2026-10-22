"""Run one isolated source pair and optional profile under one finite deadline."""
from pathlib import Path
import datetime
import gc
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time

CONTROL=Path(__file__).resolve().parent
P=Path(os.environ['PROFILE_TIMING_DIR'])
P.mkdir(parents=True,exist_ok=False)
PAIR=int(os.environ['PAIR_INDEX'])
assert PAIR in (1,2,3)
sources={'baseline':Path(os.environ['BASELINE_CHECKOUT']).resolve(),
         'candidate':Path(os.environ['CANDIDATE_CHECKOUT']).resolve()}
manifests={name:json.loads((CONTROL/(name+'-expected.json')).read_text()) for name in sources}
def save(name,value):
    (P/name).write_text(json.dumps(value,indent=2)+'\n')
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def source_state(name):
    root,expected=sources[name],manifests[name]
    git=lambda *args:subprocess.check_output(['git',*args],cwd=root,text=True,timeout=30).strip()
    value={'sha':git('rev-parse','HEAD'),'tree':git('rev-parse','HEAD^{tree}'),
           'hashes':{path:sha(root/path) for path in expected['source_hashes']}}
    assert value['sha']==expected['candidate_sha'] and value['tree']==expected['tree']
    assert value['hashes']==expected['source_hashes']
    return value
hardware={'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'pair':PAIR,'system':platform.system(),'machine':platform.machine(),
          'cpus':os.cpu_count(),'affinity':sorted(os.sched_getaffinity(0)),
          'python':platform.python_version(),'gc_enabled':gc.isenabled(),
          'thresholds':gc.get_threshold(),'gc_callbacks':len(gc.callbacks),
          'profiler_none':sys.getprofile() is None,'trace_none':sys.gettrace() is None,
          'runner_name':os.environ.get('RUNNER_NAME'),'run_id':os.environ['GITHUB_RUN_ID'],
          'attempt':os.environ['GITHUB_RUN_ATTEMPT'],'carrier':os.environ['GITHUB_SHA'],
          'cpuinfo':Path('/proc/cpuinfo').read_text()}
save('hardware.json',hardware)
assert hardware['system']=='Linux' and hardware['machine']=='aarch64'
assert hardware['cpus']==len(hardware['affinity'])==4 and hardware['python']=='3.11.17'
assert hardware['gc_enabled'] and hardware['thresholds']==(700,10,10) and hardware['gc_callbacks']==0
assert hardware['profiler_none'] and hardware['trace_none']
before={name:source_state(name) for name in sources}
save('source-before.json',before)
started=time.monotonic()
work_deadline,hard_deadline=started+600,started+630
order=['baseline','candidate'] if PAIR in (1,3) else ['candidate','baseline']
sequence=[(name,'ordinary') for name in order]
if PAIR==1:
    sequence.append(('candidate','profiled'))
control={'shared_work_seconds':600,'shared_cleanup_seconds':30,'pair':PAIR,
         'sequence':sequence,'started_monotonic':started,'work_deadline':work_deadline,
         'hard_deadline':hard_deadline,'harness_sha256':sha(CONTROL/'profile-timing.py'),
         'controller_sha256':sha(Path(__file__)),'fixture_source':manifests['baseline']['candidate_sha']}
save('control.json',control)
children,errors=[],[]
timed_out=False
try:
    for name,mode in sequence:
        if time.monotonic()>=work_deadline:
            timed_out=True
            break
        label=name+'-'+mode
        output=P/label
        environment=os.environ.copy()
        environment['PYTHONPATH']=str(sources[name]/'src')+os.pathsep+str(sources[name])
        environment['PROFILE_FIXTURE_CHECKOUT']=str(sources['baseline'])
        command=[sys.executable,'-u',str(CONTROL/'profile-timing.py'),'--checkout',str(sources[name]),
                 '--source-manifest',str(CONTROL/(name+'-expected.json')),
                 '--output',str(output),'--mode',mode]
        child_started=time.monotonic()
        with (P/(label+'.log')).open('x') as log:
            proc=subprocess.Popen(command,cwd=sources[name],env=environment,stdout=log,
                                  stderr=subprocess.STDOUT,start_new_session=True)
            print(json.dumps({'pair':PAIR,'source':name,'mode':mode,'pid':proc.pid}),flush=True)
            try:
                code=proc.wait(timeout=max(0,work_deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out=True
                try:os.killpg(proc.pid,signal.SIGINT)
                except ProcessLookupError:pass
                try:code=proc.wait(timeout=max(.01,hard_deadline-5-time.monotonic()))
                except subprocess.TimeoutExpired:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    code=proc.wait(timeout=max(.01,hard_deadline-time.monotonic()))
            finally:
                absent=False
                for _ in range(20):
                    try:os.killpg(proc.pid,0)
                    except ProcessLookupError:
                        absent=True
                        break
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        absent=True
                        break
                    if time.monotonic()>=hard_deadline:break
                    time.sleep(.1)
                child={'label':label,'pid':proc.pid,'returncode':proc.poll(),
                       'reaped':proc.poll() is not None,'group_absent':absent,
                       'started_monotonic':child_started,'finished_monotonic':time.monotonic()}
                children.append(child)
                save('children.json',children)
        if code or timed_out or not absent:
            raise RuntimeError('child failed/incomplete: '+label)
        report=json.loads((output/'report.json').read_text())
        stage=json.loads((output/'stage-report.json').read_text())
        if not report['complete'] or not stage['complete'] or len(stage['cases'])!=67:
            raise RuntimeError('incomplete original 67-call workload: '+label)
except BaseException as failure:
    errors.append({'type':type(failure).__name__,'message':str(failure)})
finally:
    after={name:source_state(name) for name in sources}
    save('source-after.json',after)
    complete=not errors and not timed_out and len(children)==len(sequence) and before==after
    custody={'complete':complete,'timed_out':timed_out,'errors':errors,'children':children,
             'source_unchanged':before==after,'elapsed_wall_seconds':time.monotonic()-started,
             'finished_monotonic':time.monotonic(),'work_deadline':work_deadline,'hard_deadline':hard_deadline}
    save('custody.json',custody)
code=0 if complete else 124 if timed_out else 1
(P/'exit-code.txt').write_text(str(code)+'\n')
print(json.dumps(custody),flush=True)
raise SystemExit(code)
