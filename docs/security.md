# Security, Privacy, and Limitations

## Filesystem Boundary

`Workspace` resolves its root once and accepts only workspace-relative paths.
It rejects:

- absolute paths;
- any path containing a `..` component;
- paths that do not remain contained beneath the resolved workspace root; and
- every symbolic-link component, including an in-workspace symlink that would
  alias one logical directory to another.

The symlink rule preserves logical boundaries as well as physical containment.
For example, a symlink beneath `manuscript/` cannot redirect a write into
`core_documents/`, and a symlinked core document cannot redirect a
post-consensus update into `config/`.

Session and debate IDs and persona identifiers have separate conservative
ASCII validation rules documented in [Configuration](configuration.md).
When `--workspace` is omitted, CLI discovery selects the nearest fully
initialized ancestor of the caller's current directory. Use an explicit
`--workspace` in automation or whenever multiple nested workspaces could make
that choice unclear.
The orchestration core-document read and update paths are restricted to the
four fixed filenames. A caller using generic `Workspace.write_text()` is a
trusted direct workspace client and can alter any safely contained relative
file. Manuscript names may contain safe relative subdirectories, but cannot be
absolute, contain traversal, or pass through a symlink.

## Artifact Mutation Guarantees

The runtime never silently overwrites a manuscript. Consensus finalization
fails with `ManuscriptExistsError` if a regular target already exists; a
symbolic-link target or path component is rejected earlier with
`WorkspaceSecurityError`. Before content writes, it also validates the
complete core-update map and the manuscript no-overwrite condition.

Writes are staged in the destination directory, flushed, and replaced
atomically per file. A new manuscript uses exclusive creation semantics. When
multiple approved artifacts are involved, the runtime writes allowlisted core
documents first and the manuscript last. If a later operation fails, it makes
a best-effort attempt to remove the new manuscript and restore every earlier
core document; rollback failures are attached to the original exception.

These operations are not a transactional filesystem or database:

- there is no single atomic boundary spanning all core documents, the
  manuscript, logs, state, and result;
- rollback itself can fail;
- an abrupt process, operating-system, hardware, or power failure can leave a
  partially durable cross-file operation.

Inspect the workspace after an infrastructure-level interruption before
starting replacement work.

## Local Plaintext Data

There is no encryption, redaction, access-control manager, or automatic
retention policy. Agent `AGENTS.md` prompts, configuration, personas, core
documents, agent outcomes, discussion logs, lifecycle state, results, and
manuscripts are local plaintext files governed by the operating system's
directory permissions.

The workspace-local `cli_context.json` is also plaintext. It contains only its
schema version and selected session ID, but the name itself may disclose
project information. `--draft-file` reads strict UTF-8 from a caller-selected
path; its content then follows the same provider, log, and manuscript paths as
an inline draft.

The full blackboard JSONL deliberately contains the original user prompt or
draft, complete generated candidates, approvals, critiques, proposals, visible
errors, finalization metadata, and the committed manuscript path. Returned CLI
JSON includes the same message history. Treat `discussion_logs/` as sensitive
as the manuscript and source material.

Owner-isolated agent JSONL is narrower. The runtime does not deliberately copy
the original prompt, full candidate text, or full core-document text into
agent state. Its own summaries retain generic creation/failure outcomes and
updated core filenames; it also retains the owning specialist's visible
approval notes, critiques, and proposals. Each outcome is capped at 4096
characters. Those provider outputs are not scrubbed, so a provider can still
echo sensitive prompt, candidate, or core material into an outcome.
Provider-visible output discipline remains necessary.

Agent history is loaded for the owner across the entire current session. It
currently has no compaction, count limit, expiry, or retention limit. Long
sessions therefore grow both on disk and in later provider requests.

Never place passwords, API keys, access tokens, private keys, or other
credentials in:

- agent prompts, workspace configuration, or persona files;
- user prompts, drafts, or core documents;
- provider-visible reply content, critiques, or proposals;
- logs, agent state, manuscript content, or output filenames.

Use `codex login` or `CODEX_API_KEY` for provider authentication, outside the
workspace and outside `AgentReply` content.

## Hidden Reasoning

The public models contain no hidden-reasoning or chain-of-thought field.
Runtime tasks explicitly instruct backends to return only the requested
structured result without hidden chain-of-thought. The runtime persists public
payloads and concise outcomes, not a separate internal-reasoning trace.

This is a provider contract, not semantic content filtering. A noncompliant
backend that places hidden reasoning or secrets in an ordinary `content` field
causes that text to be treated as visible output and potentially written to
the blackboard or agent state.

## Built-In Smoke Command

`story smoke` uses fixed synthetic content and an internal deterministic
backend. It never constructs the Codex backend, makes no
provider or network call, and requires no credentials. Its
`provider_tested: false` report field is an explicit warning that a passing
check says nothing about an LLM, model, provider integration, connectivity, or
real story content.

With no workspace argument, the command creates a temporary workspace and
removes it before normal return, including after a reported invariant failure.
An abrupt process or system termination can still interrupt ordinary temporary
directory cleanup.

With `--workspace PATH`, the command initializes any missing scaffold in that
directory and preserves existing files. It retains uniquely named synthetic
session overrides, agent-state JSONL, full blackboard logs, lifecycle/result
JSON, and a manuscript. These diagnostics are plaintext and have the same
permissions, retention, and sensitivity considerations as normal runtime
artifacts. The smoke workflow verifies that pre-existing core-document bytes
remain unchanged, but it is intentionally not read-only elsewhere in the
workspace.

## Codex Provider Boundary

The concrete backend launches the installed Codex CLI without shell
evaluation. It requests no approvals, a read-only sandbox, ignored user
configuration and rules, and disabled shell, web-search, app, and multi-agent
tools. These controls reduce exposure but do not make a remote model or the
locally installed executable a security boundary.

An `AgentDefinition` exposes absolute paths and SHA-256 digests for the role
prompt, selected persona, and that role's allowlisted core documents. During
binding, the backend verifies those bytes and assembles them into an isolated
temporary top-level `AGENTS.md`. The per-turn subprocess input contains only
dynamic request JSON. SHA-256 detects a mismatch; it does not encrypt, redact,
authorize, or hide content.

The isolated staging directory and `project_root_markers=[]` prevent normal
repository-root project instruction discovery. Codex may still apply
installation-level or vendor-global instructions outside the project
discovery path; review the installed CLI and account configuration before
processing sensitive material.

Codex stores its own resumable conversations according to its CLI and account
behavior. The runtime persists one thread ID per `(session_id, agent_id)` and
reuses it across debates. `story session end` marks the team session inactive
but deliberately retains these references and does not delete provider
transcripts. Detaching or closing a backend likewise releases only its lease;
neither operation deletes provider references or transcripts. A definition
fingerprint change starts a new thread.

## Provider Session Attachment and Lease Liveness

Provider-session manifests and leases use the same workspace containment and
no-symlink checks as other runtime persistence. Manifest replacement is atomic
per file, and lease acquisition uses exclusive creation rather than silently
overwriting another claimant. These operations do not form a cross-file or
distributed transaction.

Every logical manifest and lease consumed by a status read or lifecycle
operation is strictly validated. Validation covers the exact schema-v1 keys
and types, path/session identity, timezone-aware timestamps, manifest
timestamp ordering, thread records, and positive lease PIDs. Invalid or
malformed state raises a validation error; it is not treated as stale and
reclaimed.

Lease liveness is deliberately conservative:

- no sidecar is `available`;
- a matching token held by the current backend is `owned`;
- only an unowned lease whose hostname exactly matches the local host and whose
  PID is demonstrably alive is `in_use`;
- a same-host PID that is demonstrably gone is `available`, but read APIs leave
  its sidecar untouched; and
- a remote-host lease or any ambiguous local probe, including a permission
  failure, is `unverified` and blocks acquisition.

Only a later acquisition may reclaim a demonstrably dead same-host sidecar.
There is no remote liveness probe, heartbeat, lease timeout, age-based expiry,
or stale-remote reclamation. PID probing cannot distinguish PID reuse, and
hostname equality is not a distributed identity guarantee. Use lease state as
local coordination evidence, not authentication or proof of a remote owner's
health.

Cooperating local processes serialize lease claims with the persistent
workspace-global `provider_sessions/.lease-claims.lock` advisory guard. POSIX
uses an exclusive `flock`; Windows uses advisory one-byte file locking. The
guard is opened without following symbolic links where the platform provides
that flag, checked as a regular file, and verified so the opened descriptor and
pathname retain the same file identity. Unsafe, replaced, unverifiable, or
unsupported guard state fails closed instead of proceeding with a lease claim.
Closing the descriptor, including at process exit after a crash, releases the
OS lock.

The guard file itself is persistent and must not be deleted or replaced while
the workspace may be in use: a replacement pathname could split cooperating
processes across different advisory locks. The mechanism coordinates only
cooperating processes on the local filesystem. It is not authentication, a
distributed lock, a network-filesystem guarantee, or evidence of session-owner
liveness. The guard remains separate from the per-session lease sidecar that
records ownership.

Contention errors identify the safe session ID, PID, and a control-sanitized,
length-limited hostname so an operator can diagnose the owner. They never
include the opaque lease token. That token is a process-ownership value stored
in the plaintext sidecar, not a credential or access-control mechanism.

Ending a session atomically writes its inactive manifest before releasing its
lease. Provider thread references, externally stored provider conversations or
transcripts, CLI selection, session overrides, agent state, debate logs,
manuscripts, and unrelated session-owned files are retained. Detach leaves the
manifest active and retains the same data. Deletion and retention of external
provider conversations remain governed by the provider, not this runtime.

## Debate Ownership and Recovery

The runtime atomically reserves a session/debate pair before any backend call,
so concurrent runtimes on the same filesystem cannot both own the same debate
ID. The persisted `owner_token` coordinates state transitions and is omitted
from normal inspection output. It is not a user authentication mechanism.

An `active` debate state marker proves that an ID was reserved; it does not
prove that the originating process is still alive. There is no heartbeat,
lease timeout, automatic stale-owner cleanup, or same-debate-ID resume
operation.

External coroutine cancellation is propagated to in-flight provider calls and
best-effort persisted as terminal `interrupted`. Reserved IDs are immutable:
consensus, deadlock, failure, interruption, and abandoned active work all
require a new debate ID for recovery. Inspect the old debate, resolve any
user-level conflict, and start the replacement work under a new ID.

## Operational Limitations

- Codex is the only implemented provider. Gemini is a reserved configuration
  name and currently fails preflight.
- Provider calls have a configurable timeout but no automatic retry or
  rate-limit manager.
- A deadlock reports `action_required` but has no built-in continuation or
  user-ruling resume API.
- Stable JSONL order is a roster-order record of outcomes, not provider
  completion order.
- Filesystem safety assumes the operating system and underlying filesystem
  correctly implement the atomic primitives used by Python.
- Debate inspection reports persisted state and does not establish process
  liveness. Provider-session summaries perform only the limited local lease
  probe described above. Neither validates provider-generated content.

For workflow details, see [Architecture](architecture.md). For provider and
inspection contracts, see the [API reference](api.md).
