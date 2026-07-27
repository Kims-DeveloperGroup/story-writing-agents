# Architecture

## Implemented Scope

`story-writing-agents` is a provider-neutral, asynchronous orchestration
runtime. It coordinates four fixed agents around a shared, append-only public
blackboard and persists all runtime state in a workspace directory. It does
not bundle an LLM, provider SDK, or provider adapter.

This document describes the current implementation of
[Issue #1](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1)
and
[Issue #2](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/2).
In this repository, “commit” means committing approved text to filesystem
artifacts. The runtime does not invoke Git.

## Agent Roster

The roster and voting membership are fixed:

| Canonical ID | Role | Context documents | Vote |
| --- | --- | --- | --- |
| `lead_orchestrator` | Moderates, synthesizes, finalizes | All four core documents | No |
| `plot_architect` | Audits plot, structure, flow, and pacing | `global_outline.md`, `part_outlines.md` | Yes |
| `character_custodian` | Audits character, relationship, world, and continuity behavior | `character_sheets.md` | Yes |
| `execution_specialist` | Drafts prompt input and audits prose, POV, tone, grammar, and formatting | `narrative_rules.md` | Yes |

Only the three specialists vote. The lead orchestrator never supplies a
consensus vote and cannot make a conflict disappear by filtering it.

The initialized configuration resolves every agent to
`default_kim_eun_hee`. That template is implemented as strict, logical, candid,
and skeptical writing-room behavior that prioritizes domain consistency over
polite agreement; it is not biographical impersonation. The normal
configuration cascade can replace the persona session-wide or per agent.

## Provider and Prompt Boundary

A provider integration implements one method:

```python
async def invoke(request: AgentRequest) -> AgentReply: ...
```

The runtime owns orchestration, persistence, prompt assembly, concurrency, and
protocol validation. The backend owns only the provider call and conversion to
a structured `AgentReply`.

Configuration, persona text, targeted core documents, and owner-specific agent
history are reread from disk for every invocation. The system prompt always
contains exactly these four blocks in this order:

1. `[Persona]`
2. `[Domain Rules]`
3. `[Language Directive]`
4. `[Context Documents]`

Context hydration is least-privilege by role: the plot architect receives the
two outline files, the character custodian receives the character sheet, the
execution specialist receives the narrative rules, and the lead orchestrator
receives all four allowlisted files.

Content payloads are not interpolated into the system prompt. `AgentRequest`
keeps the provider-facing data in separate fields:

- `task`: the phase instruction plus the no-hidden-reasoning instruction;
- `user_payload`: the original prompt or draft;
- `candidate_payload`: the current candidate, or `None` before prompt drafting;
- `messages`: the public blackboard snapshot visible at invocation time;
- `agent_state`: only the receiving agent's validated history for this session.

The public blackboard and private-by-owner outcome history are separate
channels. Agent state never grants an agent another agent's persisted outcomes.

## Built-In Smoke Workflow

The CLI `smoke` command supplies a private deterministic backend to the same
`StoryRuntime` path; it does not load a provider plugin or make a network call.
It initializes a workspace, persists and rehydrates an `EN` session directive,
and snapshots the four core documents before running one complete synthetic
revision cycle:

```text
prompt → execution draft r0
       → three parallel audits (one plot conflict)
       → plot proposal
       → lead synthesis r1
       → three parallel unanimous audits
       → lead finalization
       → manuscript commit and persisted inspection
```

The command verifies workspace initialization, directive persistence, exact
phase counts and owner-isolated state, persisted consensus, the committed
synthetic `r1` manuscript, and byte-for-byte unchanged core documents. With no
workspace argument, all of this occurs under a temporary directory removed
when the command completes. An explicit workspace initializes or reuses that
directory and retains uniquely identified synthetic sessions, debates, logs,
states, results, and manuscripts for diagnosis.

This checks orchestration and persistence invariants only. It does not test an
LLM, external provider adapter, model, network, credentials, or writing
quality.

## Debate Workflow

### 1. Reservation and initiation

Before a provider is invoked, the runtime validates the input, chooses or
validates a debate ID, and atomically creates
`<debate_id>.state.json`. A second runtime attempting the same
session/debate pair receives `DebateExistsError` before its backend runs.

The original input is appended to the debate JSONL log as revision `r0`. For
prompt input, `execution_specialist` first creates a complete `r0` candidate.
For draft input, the supplied draft is already the `r0` candidate and there is
no drafting call.

### 2. Parallel silent audit

`plot_architect`, `character_custodian`, and `execution_specialist` audit the
same candidate concurrently. Each receives the blackboard snapshot from before
that audit, so peers do not see or react to another peer's in-flight verdict.
An audit reply must target the current revision and contain:

- `APPROVE`, with an optional concise note; or
- `CONFLICT`, with a non-empty actionable critique.

Although provider calls can finish in any order, audit outcomes are appended to
the blackboard in the stable canonical specialist order shown above. On the
first backend or validation failure, unfinished peer calls are cancelled and
all outcomes are recorded in stable order.

### 3. Proposal, synthesis, and re-audit

If any specialist reports a conflict, only the specialists that own conflicts
are asked for revision proposals. Those proposals are also requested in
parallel from one shared pre-proposal history snapshot and are logged in
canonical specialist order.

The lead orchestrator then receives the prior candidate and the full visible
blackboard—including every critique and proposal—and must return one complete
replacement candidate. The revision advances from `r0` to `r1`, and so on.
All three specialists re-audit that complete new candidate with the full
blackboard history.

`max_debate_rounds` counts synthesized replacement revisions, not audit
passes. The global default is `1`: the runtime may synthesize one replacement
after conflicts on `r0`, then audits `r1`. A value of `0` permits the initial
audit but no synthesized revision.

Consensus requires valid `APPROVE` verdicts from all three specialists for the
same current revision. Approvals from an older revision never count.

### 4. Deadlock and failure

If conflicts remain when the revision limit is reached, the result is
`deadlock` and includes an `action_required` user-ruling message. A backend
exception, peer cancellation caused by a failure, or invalid reply produces a
`failed` result.

Deadlock and pre-finalization handled failure still persist lifecycle state,
blackboard logs, agent outcomes, and a result record, but do not write a
manuscript or change core-document content. A failure during artifact commit
returns `failed` after the best-effort rollback described below; infrastructure
failure during rollback has the stated partial-durability caveat.

### 5. Finalization and artifact commit

After unanimous approval, the lead orchestrator receives a `finalize` request.
It cannot replace the approved candidate. It may return an empty map or
full-text replacements for these files only:

- `global_outline.md`
- `part_outlines.md`
- `character_sheets.md`
- `narrative_rules.md`

The runtime validates all update names and values and verifies that the target
manuscript does not exist before content mutation. It then replaces requested
core documents in allowlist order and creates the exact approved candidate
under `manuscript/`. Existing manuscripts are never silently overwritten.

Each file replacement is staged and atomic. If a later write fails, the runtime
attempts to remove the new manuscript and restore earlier core files before
returning a failed result. This is a best-effort cross-file rollback, not a
single power-loss-safe transaction.

## Lifecycle and Recovery

The reservation state can report `active`, `consensus`, `deadlock`, `failed`,
or `interrupted`. Normal returned terminal outcomes also have a
`.result.json`; externally cancelled work is marked terminal `interrupted` and
may have only state plus the blackboard written before cancellation.

An active marker records ownership but does not prove that its creating process
is still alive. Debate IDs are immutable reservations: completed, failed,
deadlocked, interrupted, or abandoned IDs are not resumed or reused. Recovery
means inspecting the durable state and starting a new debate ID.

## Filesystem Topology

`story-writing-agents init .` creates missing scaffold entries without
modifying existing content. Debate and agent-history subdirectories are
created lazily:

```text
workspace/
├── config/
│   ├── system_defaults.json
│   ├── agent_configs.json
│   └── persona_templates/
│       ├── default_kim_eun_hee.md
│       └── alternative_templates/<template>.md
├── core_documents/
│   ├── global_outline.md
│   ├── part_outlines.md
│   ├── character_sheets.md
│   └── narrative_rules.md
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── sessions/<session_id>.json
│   │   └── debates/<session_id>/<debate_id>.jsonl
│   ├── plot_architect_state/debates/<session_id>/<debate_id>.jsonl
│   ├── character_custodian_state/debates/<session_id>/<debate_id>.jsonl
│   └── execution_specialist_state/debates/<session_id>/<debate_id>.jsonl
├── discussion_logs/<session_id>/
│   ├── <debate_id>.state.json
│   ├── <debate_id>.jsonl
│   └── <debate_id>.result.json
└── manuscript/<relative_output_path>
```

Session override JSON survives new `StoryRuntime` instances. Each agent JSONL
contains only concise outcomes owned by that agent. The discussion JSONL is the
full public blackboard and preserves the original user content, candidates,
verdict notes, critiques, proposals, errors, finalization metadata, and commit
record. Result JSON intentionally omits the duplicated message list; inspection
combines the result, lifecycle state, and JSONL messages.

See [Configuration](configuration.md), [API](api.md), and
[Security, Privacy, and Limitations](security.md) for the corresponding
schemas and operational constraints.
