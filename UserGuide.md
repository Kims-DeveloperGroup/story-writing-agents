# CLI User Guide

## What This Guide Covers

`story-writing-agents` is a Python 3.11+ multi-agent runtime for reviewing and
producing long-form story material. The command-line interface provides six
commands:

- `init` creates a local workspace;
- `directive` persists an exact session override;
- `run` processes either a prompt or a supplied draft;
- `inspect` reads durable session or debate state;
- `session start` and `session end` control resumable provider sessions;
- `smoke` runs the built-in provider-free deterministic runtime check.

Installation exposes `story-writing-agents` and the shorter `story` as exact
aliases for the same CLI entry point. Command examples in this guide use
`story ...`; it can be replaced exactly with `story-writing-agents ...`.
The alias does not select a separate parser, runtime, or behavior.

The runtime bundles a concrete Codex CLI backend and uses only the Python
standard library. Codex is the default and only implemented provider. The
runtime uses the Codex CLI's existing login or `CODEX_API_KEY`; it does not
store provider credentials.

Commands in this guide assume a POSIX shell, an active `venv` or Conda
environment, and the repository root as the current directory. Run
`story <command> --help` for the live option summary.

For interactive use, enter an initialized workspace once. Commands issued
there or in a nested directory discover the nearest initialized workspace and
reuse the session selected by a successful `story session start SESSION`.
Explicit `--workspace` and `--session` always win and remain recommended for
automation.

## Prerequisites and Installation

Python 3.11 or later and the Codex CLI are required for real provider runs.
Install Codex and authenticate it:

```bash
npm install -g @openai/codex
rehash
codex login
```

Alternatively, set `CODEX_API_KEY` in the environment used to launch `story`.
Choose exactly one of the following
environment methods; do not create or activate a standard-library `venv`
inside an active Conda environment.

### Option A: standard-library `venv`

Check the available Python version:

```bash
python3 --version
```

Create the environment and install the repository in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
```

The activation command above is for POSIX shells. On PowerShell, activate the
same environment with `.venv\Scripts\Activate.ps1` instead.

### Option B: Conda

Create and activate a Python 3.11 Conda environment, then use that
environment's Python to install the repository:

```bash
conda create --name story-writing-agents python=3.11
conda activate story-writing-agents
python -m pip install --editable .
rehash
```

After either environment setup, confirm the CLI and initialize a dedicated
workspace:

```bash
story --help
story init ./guide-workspace
cd ./guide-workspace
story session start writing_session
story run 'Draft an opening scene using the workspace core documents.'
cd ..
```

Initialization is idempotent. It creates missing scaffold files while
preserving existing content; it does not repair or replace malformed existing
configuration.

It also creates one editable static role prompt for each canonical agent:

```text
guide-workspace/config/agents/
├── lead_orchestrator/AGENTS.md
├── plot_architect/AGENTS.md
├── character_custodian/AGENTS.md
└── execution_specialist/AGENTS.md
```

If the workspace predates these files, run `story init <workspace>` again.
Missing prompts are added while existing prompt customizations and all other
workspace content are preserved.

To deliberately restore all four prompts to the runtime defaults, use:

```bash
story init ./guide-workspace --overwrite-prompts
```

This is the only initialization mode that replaces existing prompt content.
The flag resets exactly the four canonical `AGENTS.md` files. It does not
replace system or agent configuration, persona templates, core documents,
session/debate state, discussion logs, or manuscripts. Back up customized
prompts before using it.

Edit an individual `AGENTS.md` directly to customize that role. The files are
static Markdown and have no required placeholders. Make edits between runs;
the runtime hashes current content before each phase and creates a new binding
when the effective definition changes. Normal `story init` will continue to
preserve the customization.

## Built-In Smoke Test

Run the deterministic end-to-end runtime check without configuring a
workspace:

```bash
story smoke
```

With no arguments, `smoke` creates a uniquely named temporary workspace, runs
the check, prints one line of compact JSON, and removes the workspace before
the process returns. The reported temporary `workspace.path` therefore no
longer exists after normal completion.

To retain diagnostic artifacts, provide an explicit path:

```bash
story smoke --workspace ./smoke-workspace
```

Persistent mode initializes a missing scaffold or reuses an existing
workspace. Every invocation chooses unique session, debate, and manuscript
names, so repeated checks retain separate synthetic artifacts without
overwriting earlier runs. Existing core documents are referenced and verified
during deterministic agent binding but remain byte-for-byte unchanged.

The built-in check uses a strict internal deterministic backend and the
ordinary `StoryRuntime` path. It binds one file-referenced definition per
canonical role, uses prompt-free requests, and exercises:

1. workspace initialization and a persisted `EN` session directive;
2. prompt drafting of synthetic candidate revision `r0`;
3. three parallel specialist audits, including one synthetic plot conflict;
4. a plot proposal and lead-orchestrator synthesis of revision `r1`;
5. three unanimous specialist audits of `r1`;
6. finalization, manuscript commit, persisted debate inspection, and
   verification that core documents did not change.

Successful JSON reports `"status":"passed"`,
`"provider_tested":false`, temporary or persistent workspace metadata, unique
`session_id` and `debate_id`, `"debate_status":"consensus"`,
`"revision_id":"r1"`, `"rounds_completed":1`, a disclaimer, and these six
completed checks:

```text
workspace_initialized
directive_persisted
revision_workflow
consensus_persisted
manuscript_committed
core_documents_unchanged
```

This command never constructs the Codex backend and never makes a network
call. It does not test an LLM, provider integration, model, credentials,
network access, or story quality.

Smoke-specific exit behavior is:

| Code | Meaning |
| --- | --- |
| `0` | All six invariants passed; compact passed JSON was printed. |
| `1` | The workflow completed but an invariant failed; compact failed JSON includes `failed_check`. |
| `2` | CLI usage, workspace setup, existing configuration, or an unexpected exception failed under the normal CLI error contract. |

Persistent mode retains synthetic plaintext session overrides, agent states,
discussion logs, result state, and the manuscript. Use the returned
`session_id` and `debate_id` with `inspect` when diagnostics are needed.

## Codex Backend and Provider Sessions

`AgentBackend` and `BoundAgent` are concrete classes. `AgentBackend` resolves
each agent's configured vendor, verifies its prompt references, stages the
effective prompt, and manages provider session state. Each returned
`BoundAgent` embeds the vendor-specific invocation path for one immutable
definition.

Start a provider session before its first debate:

```bash
cd ./guide-workspace
story session start writing_session
```

This successful start also saves `writing_session` as the workspace's selected
CLI session. `story session start` with no session later reactivates that
selection. A failed start does not change the selection.

One team-level lease prevents two local processes from using the same active
session concurrently. The backend stores one resumable Codex thread per
`(session_id, agent_id)` and reuses it across debates. `story run` releases the
lease when it exits. A stale lease is reclaimed only when its recorded process
is on the same host and demonstrably no longer exists.

End the provider session when the team is no longer in use:

```bash
story session end
cd ..
```

Ending marks the session inactive without deleting its thread IDs. Starting
the selected session later resumes compatible threads, and ending does not
clear the selection. Choose and start a new session ID when every agent should
start with a fresh provider thread.

Each agent's effective role prompt, persona, language, and allowlisted core
documents are combined into an isolated, temporary top-level `AGENTS.md`.
Per-turn stdin contains only request-scoped JSON. The definition fingerprint
changes when any stable input changes, so the next call starts a fresh thread
for that agent. The staging directory is deleted when the backend closes.

The bound agent supports these phases:

- `draft`: create a complete `r0` candidate for prompt input;
- `audit`: return `APPROVE` or an actionable `CONFLICT`;
- `propose_revision`: propose a repair for an owned conflict;
- `synthesize`: return a complete replacement candidate;
- `finalize`: preserve the approved candidate and optionally return full-text
  updates for allowlisted core documents.

Codex output is constrained by a phase-specific JSON schema and validated
again before becoming an `AgentReply`. The backend makes no automatic retry.
See the [API reference](docs/api.md) for exact definition, request, reply, and
backend contracts.

## Prompt and Draft Commands

The complete `run` shape is:

```text
story run
  [--workspace WORKSPACE]
  [--session SESSION]
  [--backend AGENT=BACKEND]...
  [--codex-model MODEL]
  [--codex-timeout SECONDS]
  [POSITIONAL_PROMPT | --prompt PROMPT | --draft DRAFT | --draft-file PATH]
  [--output MANUSCRIPT_PATH]
  [--debate-id DEBATE_ID]
  [--max-debate-rounds MAX_DEBATE_ROUNDS]
```

For normal interactive use inside the selected workspace, use a positional
prompt:

```bash
story run 'Draft an opening scene using the workspace core documents.'
```

The explicit form remains useful for automation:

```bash
story run \
  --workspace ./guide-workspace \
  --session writing_session \
  --prompt 'Draft an opening scene using the workspace core documents.' \
  --debate-id opening_01 \
  --output opening/chapter-01.md
```

Use a draft when you already have the complete candidate:

```bash
story run \
  --workspace ./guide-workspace \
  --session writing_session \
  --draft 'Paste the complete draft text here.' \
  --debate-id opening_revision_01 \
  --output opening/chapter-01-revised.md
```

Exactly one input is required: the positional prompt, `--prompt`, `--draft`,
or `--draft-file`. `--draft-file PATH` reads the complete file as strict UTF-8
and treats it as a draft; a relative path is resolved from the caller's current
directory, not from the discovered workspace:

```bash
story run --draft-file ./working/chapter-01.md
```

The positional prompt and `--prompt`/`--draft` values are inline content.

The backend uses the Codex CLI's built-in default model while ignoring user
configuration. Use `--codex-model MODEL` to override it for one run.
`--codex-timeout SECONDS` sets the positive per-call timeout and defaults to
`300`.

`--backend CANONICAL_AGENT_ID=BACKEND` overrides one agent for the current
run and may be repeated. For example:

```bash
story run \
  --workspace ./guide-workspace \
  --session writing_session \
  --backend plot_architect=codex \
  --backend character_custodian=codex \
  --draft 'Paste the complete draft text here.'
```

Configured names are `codex` and `gemini`. Codex is implemented; selecting
Gemini currently fails before the debate begins. Persistent routing belongs in
`config/agent_configs.json`; command-line assignments do not rewrite it.

If `--debate-id` is omitted, the runtime generates one. Explicit IDs make
inspection and automation easier. Session and debate IDs must begin with an
ASCII letter or digit and then contain at most 63 additional letters, digits,
underscores, or hyphens.

`--output` is a safe relative path beneath `manuscript/`. If omitted, it
defaults to `<session_id>_<debate_id>.md`. Absolute paths, traversal, and
symlink components are rejected. Existing manuscripts are never overwritten.

`--max-debate-rounds` counts complete synthesized replacements, not audits.
The default `1` permits one replacement after conflicts on `r0`, then re-audits
`r1`. A value of `0` permits the initial audit but deadlocks immediately if a
conflict remains.

## Session Directives

Directives are persisted per session and apply to later backend invocations.
They must be the entire positional argument—parsing does not trim whitespace.

Set the session-wide language:

```bash
story directive \
  --workspace ./guide-workspace \
  --session writing_session \
  '[SYS: LANG=EN]'
```

Override one canonical agent's persona:

```bash
story directive \
  --workspace ./guide-workspace \
  --session writing_session \
  '[OVERRIDE: plot_architect Persona=default_kim_eun_hee]'
```

Custom persona identifiers require a corresponding UTF-8 Markdown file in
`config/persona_templates/` or
`config/persona_templates/alternative_templates/`.

Clear all runtime overrides for only that session:

```bash
story directive \
  --workspace ./guide-workspace \
  --session writing_session \
  '[RESET: ALL]'
```

Reset does not change system defaults, agent configuration, personas, core
documents, debate history, agent history, or manuscripts. See
[Configuration](docs/configuration.md) for precedence and validation.

## Inspection, Statuses, and Exit Codes

Inspect all known debates and persisted overrides for a session:

```bash
story inspect \
  --workspace ./guide-workspace \
  --session writing_session
```

Inspect one debate, including lifecycle state, terminal result when available,
and the full validated blackboard:

```bash
story inspect \
  --workspace ./guide-workspace \
  --session writing_session \
  --debate opening_01
```

Returned debate results use:

| Status | Meaning |
| --- | --- |
| `consensus` | All three specialists approved the same revision and artifact finalization succeeded. |
| `deadlock` | Conflicts remained at the round limit; read `action_required`. |
| `failed` | A backend, protocol, finalization, or artifact operation failed. |

Inspection can additionally report `active`, `interrupted`, or `not_found`.
An `active` marker proves reservation, not process liveness.

CLI exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | `init`, `directive`, or `inspect` succeeded; `run` reached consensus; or all `smoke` invariants passed. |
| `1` | `smoke` completed but an invariant failed and identified `failed_check`. |
| `2` | CLI usage, provider/session preflight validation, or another caught exception failed. |
| `3` | `run` returned deadlock. |
| `4` | `run` returned a failed result. |

`run` writes its complete result JSON to standard output, including blackboard
messages. Caught exceptions are written to standard error as
`ExceptionType: message`.

## Generated Files

After initialization and use, a workspace contains:

```text
workspace/
├── config/
│   ├── system_defaults.json
│   ├── agent_configs.json
│   ├── agents/
│   │   ├── lead_orchestrator/AGENTS.md
│   │   ├── plot_architect/AGENTS.md
│   │   ├── character_custodian/AGENTS.md
│   │   └── execution_specialist/AGENTS.md
│   └── persona_templates/
│       ├── default_kim_eun_hee.md
│       └── alternative_templates/
├── core_documents/
│   ├── global_outline.md
│   ├── part_outlines.md
│   ├── character_sheets.md
│   └── narrative_rules.md
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── cli_context.json
│   │   ├── sessions/<session_id>.json
│   │   ├── provider_sessions/<session_id>.json
│   │   ├── provider_sessions/<session_id>.lease.json
│   │   └── debates/<session_id>/<debate_id>.jsonl
│   ├── plot_architect_state/debates/<session_id>/<debate_id>.jsonl
│   ├── character_custodian_state/debates/<session_id>/<debate_id>.jsonl
│   └── execution_specialist_state/debates/<session_id>/<debate_id>.jsonl
├── discussion_logs/<session_id>/
│   ├── <debate_id>.state.json
│   ├── <debate_id>.jsonl
│   └── <debate_id>.result.json
└── manuscript/<output path>
```

CLI context, provider-session, session override, and debate/history files are
created lazily. `cli_context.json` stores schema version `1` and the selected
session ID. Ending a session preserves that selection. The
discussion JSONL contains the full user input, candidates, visible verdicts,
critiques, proposals, errors, and commit record. A cancellation may leave
lifecycle state and messages without a result JSON.

All of these files are plaintext. Protect the workspace with appropriate
operating-system permissions and retention practices.

## Immutable Debate IDs and Output Retries

A session/debate pair is atomically reserved before the first backend call.
Once reserved, its debate ID is immutable and cannot be resumed or reused,
whether it reached consensus, deadlocked, failed, was interrupted, or was
abandoned while active.

Manuscripts are also immutable through the runtime after creation. A successful
output path cannot be overwritten. The two collision cases behave differently:

- reusing a debate ID fails during preflight with `DebateExistsError` and exit
  code `2`, before the backend runs;
- using a new debate ID with an existing output can reach finalization, then
  return `failed` with `ManuscriptExistsError` and exit code `4`; that new
  debate ID is consumed too.

For a clean retry, inspect the old debate, then choose both a new debate ID and
a new output path. A deadlock creates no manuscript, so its unused output path
is technically available to a new debate, but a new name gives clearer
provenance.

An interrupted or apparently stale `active` debate has no automatic lease,
heartbeat, or recovery command. Preserve it for inspection and start a new ID.

## End-to-End Story-Writing Scenario

This scenario uses original example material for a fictional project titled
*The Bell Beneath Glasswater*. It demonstrates the intended workflow with a
team of Codex-backed agents.

The scenario keeps explicit workspace, session, debate, and output values to
demonstrate reproducible automation. For interactive work, enter the workspace
and omit those values after `story session start glasswater_novel`.

### 1. Create a dedicated story workspace

```bash
story init ./glasswater-workspace
story session start \
  --workspace ./glasswater-workspace \
  --session glasswater_novel
```

### 2. Seed the four core documents

Use your editor to replace the initialized headings with the following
starting material.

`glasswater-workspace/core_documents/global_outline.md`:

```markdown
# Global Outline

Working title: The Bell Beneath Glasswater

In the tidal city of Lume, municipal diver Mara Seln discovers a sealed
signal bell beneath the flooded archive. Its salt patterns preserve memories
from the night her brother vanished. Mara must expose the archive council's
cover-up without letting the bell be removed from the water.

Continuity rule: the bell never speaks aloud. It communicates only through
changing salt patterns and vibrations in nearby metal.
```

`glasswater-workspace/core_documents/part_outlines.md`:

```markdown
# Part Outlines

## Part I — Low Tide

- Chapter 1: Mara finds the bell during an unauthorized archive inspection.
- Chapter 2: She asks clockmaker Tovin Vale to decode its first vibration.
- Part turn: the pattern identifies a council vessel logged on the night of
  Mara's brother's disappearance.
```

`glasswater-workspace/core_documents/character_sheets.md`:

```markdown
# Character Sheets

## Mara Seln

A precise municipal diver who distrusts intuition in public but privately
records every unexplained detail. She will risk her job, not another diver's
life.

## Tovin Vale

An aging clockmaker and former harbor acoustics engineer. He is patient with
machines, evasive about the archive council, and never enters deep water.
```

`glasswater-workspace/core_documents/narrative_rules.md`:

```markdown
# Narrative Rules

- Close third person, past tense, anchored to Mara.
- Understated maritime atmosphere; avoid melodramatic exposition.
- Reveal world rules through physical work and dialogue.
- End each chapter on a concrete discovery, not a rhetorical question.
```

Before each applicable phase, the runtime hashes the current allowlisted files
into that agent's definition. A changed file causes a new binding on the next
phase. Each specialist's definition references only its domain documents;
later chapters do not automatically reference earlier manuscript files.

### 3. Set the session language

```bash
story directive \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  '[SYS: LANG=EN]'
```

The override persists for subsequent debates in `glasswater_novel`.

### 4. Request the opening chapter

```bash
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --prompt 'Write Chapter 1. Mara discovers the sealed signal bell during an unauthorized inspection of the flooded archive. Follow all core documents and end with the first unexplained salt pattern.' \
  --debate-id glasswater_ch01_v1 \
  --output bell-beneath-glasswater/chapter-01.md
```

The runtime coordinates four roles:

1. `execution_specialist` drafts revision `r0`;
2. `plot_architect`, `character_custodian`, and `execution_specialist` audit
   that same revision concurrently;
3. specialists owning conflicts propose repairs, and the non-voting
   `lead_orchestrator` synthesizes a complete new revision;
4. all three specialists re-audit until they unanimously approve one current
   revision or the round limit produces a deadlock;
5. after consensus, the lead can return allowlisted full-text core updates and
   the runtime creates the exact approved manuscript.

### 5. Inspect consensus, manuscript, and blackboard

```bash
story inspect \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --debate glasswater_ch01_v1
```

For consensus, confirm:

- `"status": "consensus"`;
- a non-null `final_candidate`;
- `"manuscript_path":
  "manuscript/bell-beneath-glasswater/chapter-01.md"`;
- three approvals on the final revision in `messages`.

The approved file is:

```text
glasswater-workspace/manuscript/bell-beneath-glasswater/chapter-01.md
```

The durable public blackboard is:

```text
glasswater-workspace/discussion_logs/glasswater_novel/glasswater_ch01_v1.jsonl
```

### 6. Submit a user-edited draft as a new debate

Copy the approved chapter to a working file and edit that copy:

```bash
mkdir -p ./glasswater-workspace/working
cp \
  ./glasswater-workspace/manuscript/bell-beneath-glasswater/chapter-01.md \
  ./glasswater-workspace/working/chapter-01-user-edit.md
```

After editing, pass the file as a strict-UTF-8 draft:

```bash
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --draft-file ./glasswater-workspace/working/chapter-01-user-edit.md \
  --debate-id glasswater_ch01_user_edit \
  --output bell-beneath-glasswater/chapter-01-user-edit.md
```

This skips provider drafting and audits the user's edit as `r0`. The new debate
ID and output preserve the original approved artifact and its provenance.

### 7. Continue a later chapter in the same session

The same session restores language/persona overrides, owner-isolated outcomes,
and resumable per-agent Codex threads. It does not automatically read earlier
manuscripts. Ensure important approved facts are present in the core documents,
then include any needed chapter recap in the new prompt:

```bash
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --prompt 'Write Chapter 2. Recap: Chapter 1 ended when a fresh salt spiral formed around the council-vessel mark. Mara brings a rubbing, not the submerged bell, to Tovin. Follow the core documents and end when he recognizes the vibration interval.' \
  --debate-id glasswater_ch02_v1 \
  --output bell-beneath-glasswater/chapter-02.md
```

If provider finalization did not update a newly established durable fact, add
that fact to the appropriate core document before later work.

### 8. Recover from a deadlock

Suppose a Chapter 3 request contradicts the bell's continuity rule and the
plot specialist reports a conflict. Setting zero replacement rounds makes the
first unresolved audit terminal:

```bash
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --prompt 'Write Chapter 3 with the submerged bell speaking a warning aloud to Mara.' \
  --debate-id glasswater_ch03_conflict \
  --output bell-beneath-glasswater/chapter-03-conflict.md \
  --max-debate-rounds 0
```

A compliant provider should flag the explicit contradiction. The CLI then
returns exit code `3`, status `deadlock`, no manuscript, and an
`action_required` value summarizing the unresolved critiques. Inspect it:

```bash
story inspect \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --debate glasswater_ch03_conflict
```

The old ID cannot resume. After the user rules that the bell must remain
nonverbal, start a new debate and output:

```bash
story run \
  --workspace ./glasswater-workspace \
  --session glasswater_novel \
  --prompt 'User ruling: preserve the rule that the bell never speaks. Write Chapter 3 so its warning appears as a broken salt lattice and a vibration in Mara's helmet clasp.' \
  --debate-id glasswater_ch03_ruling \
  --output bell-beneath-glasswater/chapter-03-ruling.md
```

The deadlocked output path was not created and could be reused, but the new
name makes the recovery lineage explicit.

End the provider session when this writing session is finished:

```bash
story session end \
  --workspace ./glasswater-workspace \
  --session glasswater_novel
```

Its thread IDs remain available if the same session is started again.

## Troubleshooting

### `story-writing-agents` or `story` is not found

Activate the `venv` or Conda environment used for the installation, then
repeat the editable installation. An existing editable installation exposes
Python source changes immediately, but new console-script names such as
`story` are created only when the package is installed again.

```bash
python -m pip install --editable .
rehash
story --help
```

In an existing zsh terminal session, execute `rehash` after reinstalling.

The repeated editable install creates both executables. The short command
above reaches the same parser as `story-writing-agents --help`.

### `smoke` exits with code `1` or `2`

Code `1` prints compact failed JSON. Read `failed_check` and use persistent
mode if retained artifacts are needed for inspection. Code `2` uses the normal
CLI error output; with an existing workspace, validate its UTF-8 JSON
configuration before retrying.

### `BackendProtocolError`

Confirm the Codex CLI is installed, `codex login` has succeeded or
`CODEX_API_KEY` is present, and the provider session is active. Check the
reported Codex exit status, selected model, and timeout. A timeout,
cancellation, malformed response, or ambiguous failure can taint a saved
thread; the next invocation starts a fresh compatible thread.

### A prompt file is missing, invalid, or changed during binding

Run `story init <workspace>` to add missing canonical `AGENTS.md` files without
overwriting customized ones. Prompt, persona, and referenced core files must be
readable strict-UTF-8 files inside the workspace and cannot use symlinks. If a
file was edited while the backend was binding it, retry with a new debate ID
after the edit is complete.

### No workspace or session is selected

Without `--workspace`, run the command from an initialized workspace or one of
its descendants; discovery chooses the nearest initialized ancestor. Otherwise
pass `--workspace PATH`. Run `story session start SESSION` to save a selection,
or pass `--session SESSION` explicitly. Only a successful session start
changes `cli_context.json`.

### `--draft-file` cannot be read

The file must exist, be readable, and contain strict UTF-8. Relative paths are
resolved from the shell's current directory, even when `--workspace` points
elsewhere.

### `DirectiveError`

Pass one exact standalone directive. Do not add spaces, trailing newlines,
commentary, or changed capitalization around it. Persona templates must exist
before applying their override.

### `DebateExistsError`

The debate ID is already reserved. Inspect it and choose a new ID; deleting or
reusing lifecycle files is not a supported recovery mechanism.

### `ManuscriptExistsError`

The selected output already exists beneath `manuscript/`. Preserve it and use
a new relative output path. The failed debate ID is also consumed.

### `deadlock`

Read `action_required` and the blackboard critiques, make the user-level
ruling, update core documents if appropriate, and run a new debate ID.

### `active` or `interrupted`

Inspection cannot prove process liveness, and there is no resume operation.
Preserve the old state for diagnosis and use a new debate ID.

## Security and Privacy Checklist

- Keep provider credentials outside the workspace and outside prompts,
  replies, filenames, logs, and manuscripts. Use `codex login` or
  `CODEX_API_KEY`.
- Treat agent `AGENTS.md`, persona templates, and core documents as sensitive
  plaintext that the Codex backend stages and supplies to the provider.
- Treat discussion logs as sensitive: they include user input, complete
  candidates, critiques, proposals, and visible errors.
- Agent-state history has no current compaction or retention limit.
- Workspace files are plaintext and rely on operating-system permissions.
- Absolute paths, traversal, and all symlink components are rejected.
- Review Codex account and transcript-retention settings. Ending a story
  session does not delete Codex CLI transcripts.
- Artifact writes are atomic per file with best-effort multi-file rollback,
  not a power-loss-safe transaction across the entire workspace.
- The runtime has no hidden-reasoning field. A noncompliant provider that puts
  secrets or hidden reasoning in visible `content` can cause that text to be
  logged.

Read [Security, Privacy, and Limitations](docs/security.md) before using
sensitive material, and [Architecture](docs/architecture.md) for the exact
debate and persistence model.
