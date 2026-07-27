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
transcripts. A definition fingerprint change starts a new thread.

## Debate Ownership and Recovery

The runtime atomically reserves a session/debate pair before any backend call,
so concurrent runtimes on the same filesystem cannot both own the same debate
ID. The persisted `owner_token` coordinates state transitions and is omitted
from normal inspection output. It is not a user authentication mechanism.

An `active` state marker proves that an ID was reserved; it does not prove that
the originating process is still alive. There is no heartbeat, lease timeout,
automatic stale-owner cleanup, or same-ID resume operation.

External coroutine cancellation is propagated to in-flight provider calls and
best-effort persisted as terminal `interrupted`. Reserved IDs are immutable:
consensus, deadlock, failure, interruption, and abandoned active work all
require a new debate ID for recovery. Inspect the old debate, resolve any
user-level conflict, and start the replacement work under a new ID.

Separately, one team-level provider lease prevents concurrent local processes
from using the same active provider session. A lease is reclaimed only when it
names the local host and its recorded process is demonstrably gone. Closing
`AgentBackend` releases owned leases.

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
- Inspection reports persisted state; it does not establish process liveness
  or validate the truthfulness of provider-generated content.

For workflow details, see [Architecture](architecture.md). For provider and
inspection contracts, see the [API reference](api.md).
