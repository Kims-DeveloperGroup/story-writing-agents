# Local CI during Actions non-execution

[GITHUB_WORKFLOW.md](../../../../GITHUB_WORKFLOW.md#actions-non-execution-fallback) owns
authorization, review fallback, and merge gates. This procedure executes validation; it does
not grant additional stage or GitHub mutation authority.

## Establish the fallback and source

1. Retain the hosted run/check URL and diagnostic establishing that execution is unavailable,
   for example a job-not-started billing annotation. A failing assertion, unknown cause, slow
   review, or absent response is not proof of infrastructure non-execution.
2. Verify repository, accepted base, current PR head (or implementation candidate), branch,
   checkout commit/tree, and porcelain. Pin full SHAs rather than moving branch names. Validate
   the prospective merge tree: when the head contains the accepted base, the head tree suffices;
   otherwise use an authorized disposable validation worktree with a recorded test merge.
   Conflicts stop integration. Never rewrite the delivery branch to manufacture evidence.
3. Read the pinned [.github/workflows/pr-validation.yml](../../../../.github/workflows/pr-validation.yml),
   including `classify`, setup, every selected job, and reconciliation. Use both old and new
   names for renamed/deleted paths. Include `test:full` and other current selection inputs.
   If classification is uncertain or the diff is incomplete, run all lanes.
4. Current static-only selection covers documentation, templates, skills, development-agent
   configuration, and module-index-only changes. Runtime/dependency paths, tests, test policy,
   the validation workflow, or `test:full` also select fast and both full shards. Runtime-code
   changes additionally need affected exact modules/IDs. Re-read classification if it changes;
   this list does not replace the workflow as the command/selection source.
5. Record dirty content with hashes during implementation. Before integration, require evidence
   for the exact committed base/head/checkout inputs; reuse only after checking all relevant
   inputs, source hashes, environment, and selection. Changed inputs invalidate affected evidence.

## Prepare the local environment

Use Python 3.11 and the lockfile, as CI does. Ruby with YAML support and Node are also needed
by static validation. Use the workflow's pinned uv version (currently 0.12.17). In the verified
validation worktree, initialize the pinned submodules and install the locked dependencies:

```bash
git submodule update --init --recursive
uv sync --locked --all-extras --python 3.11 --no-progress
.venv/bin/python --version
ruby --version
node --version
uv --version
```

Do not modify the user's global Git identity. If the runtime suite needs CI's deterministic
identity, configure it only in the disposable validation checkout. Retain the actual OS,
Python, tool versions, setup results, and any platform differences; a missing dependency or
unsupported environment is a gap, not a successful lane.

In Bash, set these values to the already verified base and head. This example reads the
verified refs once; recheck them against the GitHub snapshot before accepting the evidence:

```bash
export BASE_SHA="$(git rev-parse origin/main)"
export HEAD_SHA="$(git rev-parse HEAD)"
ci_report_dir="$(mktemp -d "${TMPDIR:-/tmp}/story-local-ci.XXXXXXXXXX")"
chmod 700 "$ci_report_dir"
```

## Execute every static step from the workflow

The recipe reads the current static job rather than maintaining another copy of its checks.
`uv run` supplies Python 3.11 on the child shell's PATH. Each shell uses CI's error and
pipeline handling. The report retains failures as well as successful checks, the literal
commands, source hashes, and the checkout identity. It creates a new report exclusively.

```bash
uv run --locked --all-extras --python 3.11 python - "$ci_report_dir/static.json" <<'PY'
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()

workflow = Path(".github/workflows/pr-validation.yml")
extract = '''
document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false)
steps = document.fetch("jobs").fetch("static").fetch("steps")
print JSON.generate(steps.select { |step| step.key?("run") })
'''
steps = json.loads(subprocess.check_output(
    ["ruby", "-ryaml", "-rjson", "-e", extract, str(workflow)], text=True))
if not steps:
    raise SystemExit("Static workflow has no executable steps")
report = {
    "base_sha": git("rev-parse", os.environ["BASE_SHA"]),
    "head_sha": git("rev-parse", os.environ["HEAD_SHA"]),
    "checkout_sha": git("rev-parse", "HEAD"),
    "checkout_tree": git("rev-parse", "HEAD^{tree}"),
    "porcelain": git("status", "--porcelain", "--untracked-files=all"),
    "workflow_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
    "environment": {"python": sys.version, "platform": platform.platform()},
    "checks": [],
}
for step in steps:
    command = step["run"]
    if "${{" in command:
        raise SystemExit("Resolve new workflow expressions before executing locally")
    started = time.monotonic()
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
        text=True, capture_output=True)
    report["checks"].append({
        "name": step.get("name", "Unnamed static step"), "command": command,
        "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "seconds": time.monotonic() - started,
    })
    print(f'{step.get("name")}: {"PASS" if result.returncode == 0 else "FAIL"}')
paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
report["source_sha256"] = {
    os.fsdecode(raw): hashlib.sha256(Path(os.fsdecode(raw)).read_bytes()).hexdigest()
    for raw in paths.split(b"\0") if raw and Path(os.fsdecode(raw)).is_file()
}
with open(sys.argv[1], "x", encoding="utf-8") as target:
    json.dump(report, target, indent=2)
    target.write("\n")
raise SystemExit(int(any(check["exit_code"] != 0 for check in report["checks"])))
PY
```

An unresolved expression or setup failure requires diagnosis before calling the result
complete. Preserve an interrupted run's available evidence and rerun its missing checks.
For static-only changes, the runtime lanes below are not selected. Also inspect the final
diff, links/anchors, policy invariants, and any scenario-specific checks required by the change.

## Execute selected runtime lanes

For runtime/test selection, execute the fast tier before starting the full shards:

```bash
PYTHONPATH=src .venv/bin/python -m tests.runner run \
  --tier fast --json "$ci_report_dir/fast.json"
```

For runtime-code changes, also run affected exact modules or IDs with a fresh JSON filename,
using the canonical runner's `--module`/`--id` selectors chosen from the changed behavior.
Record those literal selectors and results; fast or aggregate full-suite counts do not
replace affected-case evidence.

Run no more than these two test processes concurrently. Preserve both complete reports and
logs even when one fails; do not combine partial reruns into a complete shard:

```bash
PYTHONPATH=src .venv/bin/python -m tests.runner run \
  --shard 1/2 --json "$ci_report_dir/full-1.json" \
  >"$ci_report_dir/full-1.log" 2>&1 &
ci_shard_one_pid=$!
PYTHONPATH=src .venv/bin/python -m tests.runner run \
  --shard 2/2 --json "$ci_report_dir/full-2.json" \
  >"$ci_report_dir/full-2.log" 2>&1 &
ci_shard_two_pid=$!
ci_shard_status=0
wait "$ci_shard_one_pid" || ci_shard_status=1
wait "$ci_shard_two_pid" || ci_shard_status=1
test "$ci_shard_status" -eq 0
```

Then run both reconciliation and the current frozen inventory/time-budget check:

```bash
PYTHONPATH=src .venv/bin/python -m tests.runner reconcile \
  "$ci_report_dir/full-1.json" "$ci_report_dir/full-2.json" \
  --json "$ci_report_dir/full-reconciled.json"
PYTHONPATH=src .venv/bin/python -m tests.suite_budget \
  --base-ref "$BASE_SHA" \
  --report "$ci_report_dir/full-1.json" --report "$ci_report_dir/full-2.json"
```

[TEST_POLICY.md](../../../../TEST_POLICY.md) retains its exact inventory, subtest/source-size
limits, and 1,080-second total / 540-second shard ceilings. Timeout, missing IDs, failures,
incomplete/overlapping shards, stale source, or exceeded budgets are not passes. Preserve
diagnostic reruns separately; complete final evidence still needs fresh complete affected
shards and successful reconciliation/budget checks.

## Record and accept evidence

Use the raw [Evidence template](../../../../.github/templates/evidence.md) and update the PR's
Verification section. Record why hosted execution was unavailable, selected/skipped lanes,
accepted base/head and actual checkout/tree, local environment, exact commands/IDs, exit
codes, timings, file hashes, artifact paths/links, failures/gaps, and the root's assessment.
Mark private/local artifacts as such. Verify required approvals and GitHub-enforced checks
separately. Report local CI as local evidence; never create a passing status for a hosted
job that did not execute. If enforcement blocks integration, retain the branch/worktree and
name the external owner/action. Review orchestration and manual closure use the canonical
workflow policy, preserving native requests, numbered history, and all findings.
