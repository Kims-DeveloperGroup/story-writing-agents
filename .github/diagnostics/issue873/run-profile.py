"""Run two sequential isolated diagnostic workers with source/hardware pins and bounded group cleanup."""
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

P = Path(os.environ['PROFILE_TIMING_DIR'])
P.mkdir(parents=True, exist_ok=True)
CONTROL = Path(__file__).resolve().parent
SOURCE = Path.cwd()
expected = json.loads((CONTROL / 'expected-source.json').read_text())


def save(name, value):
    (P / name).write_text(json.dumps(value, indent=2) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], text=True, cwd=SOURCE, timeout=30).strip()


def source_state():
    hashes = {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
              for name in expected['source_hashes']}
    return {'sha': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
            'hashes_match': hashes == expected['source_hashes'],
            'workflow_pin_matches': hashlib.sha256((SOURCE / '.github/workflows/pr-validation.yml').read_bytes()).hexdigest()
            == '93632712a99d6b27c510183d6988f48dca2a5ad79af599d69dbf96fc26b95d84',
            'digest': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


hardware = {'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'system': platform.system(), 'machine': platform.machine(),
            'cpu_count': os.cpu_count(), 'affinity': sorted(os.sched_getaffinity(0)),
            'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'gc_enabled': gc.isenabled(), 'gc_thresholds': list(gc.get_threshold()),
            'gc_callbacks': len(gc.callbacks), 'profiler_none': sys.getprofile() is None,
            'trace_none': sys.gettrace() is None,
            'run_id': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
            'carrier': os.environ['GITHUB_SHA'], 'ref': os.environ['GITHUB_REF'],
            'repository': os.environ['GITHUB_REPOSITORY']}
save('hardware.json', hardware)
assert hardware['system'] == 'Linux' and hardware['machine'] == 'aarch64'
assert hardware['cpu_count'] == len(hardware['affinity']) == 4
assert hardware['python'] == '3.11.17' and hardware['implementation'] == 'CPython'
assert hardware['gc_enabled'] and hardware['gc_thresholds'] == [700, 10, 10]
assert hardware['gc_callbacks'] == 0 and hardware['profiler_none'] and hardware['trace_none']
assert hardware['repository'] == 'Kims-DeveloperGroup/story-writing-agents'
before = source_state()
assert before['sha'] == expected['candidate_sha'] and before['tree'] == expected['tree']
assert before['hashes_match'] and before['workflow_pin_matches']
save('source-before.json', before)
started = time.monotonic()
work_deadline, hard_deadline = started + 600, started + 630
control = {'worker_seconds_shared': 600, 'cleanup_seconds_shared': 30,
           'modes': ['ordinary', 'profiled'], 'started_monotonic': started,
           'work_deadline': work_deadline, 'hard_deadline': hard_deadline,
           'source': before['sha'],
           'harness_sha256': hashlib.sha256((CONTROL / 'profile-timing.py').read_bytes()).hexdigest(),
           'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
save('control.json', control)
children, errors = [], []
timed_out = False
source_valid = False
comparison = None
try:
    for mode in ('ordinary', 'profiled'):
        if time.monotonic() >= work_deadline:
            timed_out = True
            break
        child_output = P / mode
        if child_output.exists():
            raise RuntimeError('child output already exists; no silent repetition')
        child_started = time.monotonic()
        worker_exit, group_absent = None, False
        with (P / (mode + '-worker.log')).open('x') as log:
            proc = subprocess.Popen([sys.executable, '-u', str(CONTROL / 'profile-timing.py'),
                '--checkout', str(SOURCE), '--source-manifest', str(CONTROL / 'expected-source.json'),
                '--output', str(child_output), '--mode', mode], cwd=SOURCE,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=os.environ.copy())
            print(json.dumps({'mode': mode, 'worker_pid': proc.pid,
                              'shared_work_deadline': work_deadline}), flush=True)
            try:
                worker_exit = proc.wait(timeout=max(0, work_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                try:
                    worker_exit = proc.wait(timeout=max(0.01, hard_deadline - 5 - time.monotonic()))
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    worker_exit = proc.wait(timeout=max(0.01, hard_deadline - time.monotonic()))
            finally:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    group_absent = True
                if not group_absent:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        group_absent = True
                    for _ in range(20):
                        if time.monotonic() >= hard_deadline:
                            break
                        try:
                            os.killpg(proc.pid, 0)
                        except ProcessLookupError:
                            group_absent = True
                            break
                        time.sleep(0.1)
                child = {'mode': mode, 'worker_pid': proc.pid, 'worker_exit': worker_exit,
                         'reaped': proc.poll() is not None, 'group_absent': group_absent,
                         'started_monotonic': child_started, 'finished_monotonic': time.monotonic(),
                         'work_deadline': work_deadline, 'hard_deadline': hard_deadline}
                children.append(child)
                save(mode + '-custody.json', child)
        if worker_exit != 0 or not group_absent or timed_out:
            break
        child_report = json.loads((child_output / 'report.json').read_text())
        stage_report = json.loads((child_output / 'stage-report.json').read_text())
        if not child_report['complete'] or not stage_report['complete'] or stage_report['negative_case_count'] != 67:
            raise RuntimeError('incomplete original fixture/negative corpus')
    if len(children) == 2 and all(x['worker_exit'] == 0 and x['group_absent'] and x['reaped'] for x in children):
        ordinary, profiled = [json.loads((P / mode / 'stage-report.json').read_text()) for mode in ('ordinary', 'profiled')]
        coordinates_equal = [x['mutation'] for x in ordinary['cases']] == [x['mutation'] for x in profiled['cases']]
        codes_equal = [x['rejection_code'] for x in ordinary['cases']] == [x['rejection_code'] for x in profiled['cases']]
        if not coordinates_equal or not codes_equal:
            raise RuntimeError('original negative case order/outcomes differ across contexts')
        comparison = {'mode': 'stage_attribution_only', 'canonical_credit': False, 'performance_credit': False,
                      'ordered_mutation_coordinates_equal': coordinates_equal, 'ordered_public_codes_equal': codes_equal,
                      'raw_corpus_bytes_equal': [x['raw_sha256'] for x in ordinary['cases']] == [x['raw_sha256'] for x in profiled['cases']],
                      'semantic_byte_equivalence_claim': False,
                      'limit': 'Independent fresh offer identities are retained; no blanket normalization or ordinary-stage cost inference.'}
        for clock in ('wall', 'cpu'):
            key = 'elapsed_' + clock + '_seconds'
            plain = sum(x[key] for x in ordinary['cases'])
            observed = sum(x[key] for x in profiled['cases'])
            comparison[clock] = {'ordinary_negative_calls_seconds': plain,
                'profiled_negative_calls_seconds': observed, 'observed_profiled_to_ordinary_ratio': observed / plain,
                'observer_callback_self_seconds_lower_bound': sum(x['observer_callback_' + clock + '_seconds'] for x in profiled['cases'])}
        save('comparison.json', comparison)
except BaseException as error:
    errors.append({'type': type(error).__name__, 'message': str(error)})
finally:
    try:
        after = source_state()
        save('source-after.json', after)
        source_valid = after == before
    except BaseException as error:
        errors.append({'type': type(error).__name__, 'message': str(error)})
    custody = {'children': children, 'timed_out': timed_out, 'errors': errors,
               'elapsed_wall_seconds': time.monotonic() - started,
               'work_deadline': work_deadline, 'hard_deadline': hard_deadline,
               'source_valid': source_valid,
               'complete': bool(comparison) and not timed_out and not errors and source_valid}
    save('custody.json', custody)
exit_code = 124 if timed_out else 0 if custody['complete'] else 1
(P / 'exit-code.txt').write_text(str(exit_code) + '\n')
print(json.dumps({'exit_code': exit_code, **custody}), flush=True)
raise SystemExit(exit_code)
