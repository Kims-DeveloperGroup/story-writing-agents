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
   otherwise use the stage-authorized disposable detached validation worktree with a recorded
   temporary test-merge commit/tree. Record ownership/path, retain evidence, and remove only
   that clean recoverable test checkout without force. Delivery resources remain protected.
   Conflicts stop integration. Never rewrite the delivery branch to manufacture evidence.
3. Read the pinned [.github/workflows/pr-validation.yml](../../../../.github/workflows/pr-validation.yml),
   including `classify`, setup, every selected job, and reconciliation. Use both old and new
   names for renamed/deleted paths. Include `test:full` and other current selection inputs.
   If classification is uncertain or the diff is incomplete, run all lanes.
4. Current static-only selection covers documentation, templates, skills, development-agent
   configuration, and module-index-only changes. Runtime/dependency paths, tests, test policy,
   the validation workflow, or `test:full` also select fast and all four full shards. Runtime-code
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
unsupported environment is a gap, not a successful lane. A guest wall clock must remain stable
during validation; record clock corrections and retain resulting timestamp-order
failures. The coordinator does not reconfigure or restart executors.

In Bash, set these values to the already verified base and head. This example reads the
verified refs once; recheck them against the GitHub snapshot before accepting the evidence:

```bash
export BASE_SHA="$(git rev-parse origin/main)"
export HEAD_SHA="$(git rev-parse HEAD)"
ci_report_dir="$(mktemp -d "${TMPDIR:-/tmp}/story-local-ci.XXXXXXXXXX")"
chmod 700 "$ci_report_dir"
```

## Execute the pinned workflow through the host queue

Use the host coordinator from a clean checkout with pinned base/head SHAs. The
campaign head must exactly equal checkout HEAD. For a disposable prospective
merge checkout, pin its temporary merge commit as the campaign head and retain
the original PR head separately in evidence. The coordinator
reads the workflow's actual classifier, including old and new paths and supplied
PR labels, and executes every selected static `run` step. Ruby extracts these
steps; Node executes the classifier. Unresolved workflow expressions, missing
tools, or unsupported selection fail visibly rather than skipping a check.

```bash
PYTHONPATH=src .venv/bin/python -m tests.local_ci run \
  --checkout "$PWD" --base-sha "$BASE_SHA" --head-sha "$HEAD_SHA" \
  --report-dir "$ci_report_dir" --task "Issue NUMBER local CI" \
  --module tests.CHOSEN_AFFECTED_MODULE --timing-mode isolated
```

Supply repeated `--module`/`--id` selectors for affected runtime behavior and
repeated `--label` values for relevant PR labels, including `test:full`. Static-only
selection runs all static checks; runtime/test selection also runs fast,
affected when supplied, four complementary full shards, reconciliation, and the strict frozen
budget check. Record the selectors and explain any absent affected lane.

Every task thread and checkout shares four host execution permits. A campaign
schedules exactly four complementary full shards. Shared mode permits concurrent feedback;
isolated mode drains active work and reserves the host for measurement. Full
shards run sequentially by default in isolated mode and up to four concurrently in shared
mode. `--full-concurrency 1`, `2`, `3`, or `4` overrides this choice; campaign metadata
records it. A failed shard does not skip the remaining shards. Queue
wait appears in job metadata, separately from canonical test durations. It never
waives test time or erases a validation failure. The campaign command provides
no automatic wall-time exception; an explicitly authorized exception still
requires the separate record and budget procedure in TEST_POLICY.md.

For Lima, invoke the command on the host with `--executor lima:INSTANCE` and
`--guest-checkout /absolute/prepared/checkout`. Prepare matching source and a
locked Python 3.11 `.venv` in the guest. Guest jobs use the host's admission
permits; direct guest queues are rejected. Retain actual guest environment and
host/guest source identities. A disconnect or uncertain death quarantines the
permit until termination is proved.

```bash
PYTHONPATH=src .venv/bin/python -m tests.local_ci status
PYTHONPATH=src .venv/bin/python -m tests.local_ci cancel JOB_ID \
  --owner-file "$HOME/.cache/story-local-ci/jobs/JOB_ID/ownership.json"
```

Running cancellation may retain quarantine after the original group exits;
separately spawned children cannot be assumed dead. Only complete executor-level
death proof, including a verified host/guest restart, retires those uncertain
jobs, with invalid evidence. Executor restarts require separate operator action.

Cancellation requires the owner capability and affects only that job's process
group or campaign children. Keep the capability file owner-only. Coordinator
state and per-job evidence live outside repositories under
`~/.cache/story-local-ci`; a restarted coordinator retains unfinished jobs and
uncertain permits.

The fresh report directory contains `static.json`, selected canonical lane
reports, `full-exit-status.json`, `full-reconciled.json`, and `campaign.json`.
Per-job state retains
commands, source hashes at enqueue/start/finish, worker exit codes, and stdout /
stderr, including crashes before a canonical report is written. Source drift
makes evidence stale. Preserve failures, cancellations, quarantine, and partial
reports; diagnostic reruns cannot be assembled into complete shard evidence.

Implementation diagnostics may still use exact `tests.runner run` selectors;
they enter the host queue automatically and retain dirty-source hashes. Final
integration evidence requires the clean, pinned, unchanged candidate and every
selected lane. Recheck base/head and relevant inputs after a campaign finishes.

[TEST_POLICY.md](../../../../TEST_POLICY.md) retains exact IDs, subtest/source caps,
and 1,080-second total / 540-second shard ceilings. Timeout, missing IDs,
failures, incomplete/overlapping shards, stale inputs, or exceeded budgets fail.

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
