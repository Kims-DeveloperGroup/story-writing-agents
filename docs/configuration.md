# Configuration

## Workspace Initialization

From the repository root, initialize the repository itself or another
workspace directory:

```bash
story init .
story init ./example-workspace
```

Initialization creates missing directories and default files. It is
idempotent and normally never repairs, replaces, or validates existing file
content. Existing workspaces should rerun `story init <workspace>` after
upgrading so missing agent prompt files are added without disturbing
customizations.

The one opt-in replacement mode is:

```bash
story init ./example-workspace --overwrite-prompts
```

It resets exactly the four canonical agent `AGENTS.md` files described below.
It preserves all JSON configuration, persona templates, core documents,
session and debate state, logs, and manuscripts. Use it only when all four
custom role prompts may be discarded.

The equivalent public Python APIs are:

```python
workspace.initialize(overwrite_prompts=False)
initialize_workspace(root, overwrite_prompts=False)
```

Configuration and referenced prompt inputs are resolved before every phase,
so valid edits are visible without recreating `StoryRuntime`.

## Agent Role Prompts

Every canonical agent owns one static editable UTF-8 Markdown prompt:

```text
config/agents/lead_orchestrator/AGENTS.md
config/agents/plot_architect/AGENTS.md
config/agents/character_custodian/AGENTS.md
config/agents/execution_specialist/AGENTS.md
```

These files contain each role's domain instructions. They are ordinary static
documents with no required placeholders and are not interpolated into an
`AgentRequest`. The runtime instead gives the concrete backend an
`AgentDefinition` containing a content-addressed reference to the applicable
`AGENTS.md`, plus separate references for the resolved persona and allowlisted
core documents and a separate language value.

Normal initialization creates a missing prompt from its default and preserves
an existing prompt byte-for-byte. `--overwrite-prompts` replaces all four
prompt files with their defaults, even when only one was customized; it never
replaces another existing file. As in normal initialization, any other missing
scaffold entries are still created.

Before each phase the runtime hashes the current prompt, selected persona,
allowlisted core documents, and resolved language. An effective change causes
a new backend binding on that next phase; an unchanged definition reuses the
existing binding in the current `StoryRuntime`.

## System Defaults

`config/system_defaults.json` is a JSON object with this default content:

```json
{
  "language": "KO",
  "max_debate_rounds": 1,
  "persona": "default_kim_eun_hee"
}
```

The allowed fields are:

| Field | Type | Required behavior |
| --- | --- | --- |
| `language` | string | Must match the language-token format below. |
| `persona` | string | Template identifier without the `.md` suffix; the template must exist. |
| `max_debate_rounds` | nonnegative integer | Optional in the file; defaults to `1` when absent. Boolean values are rejected. |

`language` and `persona` must resolve to valid strings. Unknown fields are
rejected.

## Per-Agent Configuration

`config/agent_configs.json` uses canonical agent IDs as its only allowed
top-level keys:

```json
{
  "character_custodian": {
    "backend": "codex",
    "language": null,
    "persona": null
  },
  "execution_specialist": {
    "backend": "codex",
    "language": null,
    "persona": null
  },
  "lead_orchestrator": {
    "backend": "codex",
    "language": null,
    "persona": null
  },
  "plot_architect": {
    "backend": "codex",
    "language": null,
    "persona": null
  }
}
```

An omitted canonical agent is equivalent to an empty object. Each agent object
may contain only `backend`, `language`, and `persona`; values may be `null` to
inherit. An omitted backend resolves to `codex`. Allowed names are `codex` and
`gemini`; only Codex is implemented, so selecting Gemini fails before provider
invocation. Unknown agent IDs, fields, and backend names are errors.

The canonical IDs are:

- `lead_orchestrator`
- `plot_architect`
- `character_custodian`
- `execution_specialist`

## Session Runtime Overrides

Runtime overrides are a JSON-compatible object shaped as follows:

```json
{
  "language": "EN",
  "persona": null,
  "agents": {
    "plot_architect": {
      "language": "FR",
      "persona": "strict_plot_reviewer"
    }
  }
}
```

The top level accepts only `language`, `persona`, and `agents`. The first two
are session-wide runtime values and may be strings or `null`. `agents` maps
canonical IDs to objects containing only `language` and `persona`. The runtime
normalizes accepted values to contain all three top-level fields.

Programmatic overrides replace the complete override object:

```python
from story_writing_agents import StoryRuntime

runtime = StoryRuntime(".")
runtime.set_runtime_overrides(
    "demo",
    {
        "language": "EN",
        "persona": None,
        "agents": {
            "plot_architect": {
                "language": "FR",
                "persona": "strict_plot_reviewer",
            }
        },
    },
)
```

The referenced `strict_plot_reviewer` template must already exist. Session
overrides persist at:

```text
agent_states/orchestrator_state/sessions/<session_id>.json
```

They survive new runtime instances. A session with no persisted override file
inherits configuration and inspects with an empty runtime state.

### Resolution precedence

Language and persona resolve independently in this exact order:

1. per-agent value in the current session's runtime overrides;
2. session-wide top-level value in the runtime overrides;
3. value in that agent's `config/agent_configs.json` object;
4. value in `config/system_defaults.json`.

For example, a per-agent runtime persona can override an agent-config persona
without changing the language resolved from another level.

Backend routing resolves separately:

1. the `AgentBackend` per-run override for that agent;
2. the agent's `backend` in `config/agent_configs.json`;
3. `codex`.

The repeatable CLI form
`--backend CANONICAL_AGENT_ID=BACKEND` supplies step 1 for one run and does not
rewrite configuration.

## Provider Session State

Provider lifecycle metadata is stored beneath:

```text
agent_states/orchestrator_state/provider_sessions/
├── <session_id>.json
└── <session_id>.lease.json
```

The session manifest records its schema version, active state, timestamps, and
current thread metadata keyed by backend and agent. `story session end`
retains those thread references so a later `story session start` can resume
compatible conversations. The lease file exists only while a process owns the
team session and prevents concurrent processes from resuming the same threads.
Both files are runtime-managed plaintext and must not be edited manually.

## CLI Context and Discovery

Interactive CLI selection is stored at:

```text
agent_states/orchestrator_state/cli_context.json
```

Its strict schema is:

```json
{
  "schema_version": 1,
  "selected_session": "demo"
}
```

The file is absent until a successful `story session start SESSION`. That
command atomically replaces the selected session. A failed start and every
`run`, `directive`, `inspect`, and `session end` command leave the selection
unchanged. Ending the selected provider session preserves the selection, so a
bare `story session start` can reactivate it later.

Session resolution precedence is:

1. `--session SESSION`;
2. the positional session accepted by `story session start|end`;
3. `selected_session` in CLI context.

Supplying both session forms to a lifecycle command is an error. If no source
resolves, the command fails and asks the user to start or explicitly name a
session.

For commands other than `init` and provider-free `smoke`, workspace resolution
uses an explicit `--workspace PATH` when present. Otherwise it searches from
the caller's current directory upward and chooses the nearest fully
initialized workspace. It does not silently initialize an explicit or
discovered path.

## Persona Templates

A persona identifier is the filename without `.md`. It must match:

```text
[A-Za-z0-9][A-Za-z0-9_.-]{0,63}
```

This permits 1–64 ASCII letters, digits, underscores, dots, and hyphens, with
an alphanumeric first character. Slashes, whitespace, traversal components,
and absolute paths are invalid.

Templates are UTF-8 Markdown files in either location:

```text
config/persona_templates/<identifier>.md
config/persona_templates/alternative_templates/<identifier>.md
```

The top-level location is checked first. A configured or directed template
must exist when the configuration is validated.

## Language Tokens

A language token must match:

```text
[A-Za-z][A-Za-z0-9-]{0,15}
```

It is 1–16 ASCII characters, begins with a letter, and otherwise contains only
letters, digits, or hyphens. Examples include `KO`, `EN`, and `en-US`. Tokens
are case-preserving and are not checked against a fixed language registry.
The resolved token becomes the `language` field of every agent definition.
The concrete backend applies that value when staging the provider `AGENTS.md`.

## Debate Round Limit

`max_debate_rounds` is the number of complete replacement revisions the lead
orchestrator may synthesize after specialist conflicts. It is not the number
of audits:

- `0` runs the initial `r0` audit and deadlocks immediately if a conflict
  remains;
- the default `1` permits synthesis of `r1`, followed by a full audit of `r1`;
- `2` additionally permits synthesis and audit of `r2`.

The system default can be replaced for one call with
`max_debate_rounds=` in the Python API or `--max-debate-rounds` in the CLI:

```bash
story run --workspace . --session demo \
  --draft 'A neutral draft.' \
  --max-debate-rounds 2
```

The value must be a nonnegative integer. In Python, `True` and `False` are
explicitly rejected even though `bool` is an integer subclass.

## Exact Standalone Directives

Only these complete message forms are directives:

```text
[SYS: LANG=<language-token>]
[OVERRIDE: <canonical-agent-id> Persona=<persona-identifier>]
[RESET: ALL]
```

For example:

```bash
story directive --workspace . --session demo '[SYS: LANG=EN]'
story directive --workspace . --session demo \
  '[OVERRIDE: plot_architect Persona=default_kim_eun_hee]'
story directive --workspace . --session demo '[RESET: ALL]'
```

Parsing does not trim whitespace. Leading or trailing whitespace, a trailing
newline, changed capitalization, or a directive-looking substring embedded in
other content is not a directive.

The language directive sets the session-wide runtime language. The persona
directive sets one canonical agent's runtime persona and leaves its other
override values intact. `[RESET: ALL]` clears only that session's runtime
language, session-wide runtime persona, and all per-agent runtime overrides.
It does not edit system defaults, agent configuration, templates, core
documents, debate logs, agent history, or manuscripts.

## Identifiers and Validation Errors

Session and debate IDs must match:

```text
[A-Za-z0-9][A-Za-z0-9_-]{0,63}
```

They are 1–64 ASCII letters, digits, underscores, or hyphens, with an
alphanumeric first character.

Validation is strict:

- invalid UTF-8, invalid JSON, or invalid persisted schemas raise
  `WorkspaceFormatError`;
- direct unsafe workspace or template-name operations raise
  `WorkspaceSecurityError`;
- invalid configuration values, missing templates, unsafe persona names used
  as configuration, and unknown keys raise `ConfigurationError`;
- invalid, non-standalone, unknown-agent, or otherwise unappliable directives
  raise `DirectiveError`;
- unsafe session or debate IDs raise `SessionIdError`.

Directive state is fully validated before its session JSON is atomically
replaced, so a rejected directive leaves the prior override file unchanged.
See [Security, Privacy, and Limitations](security.md) for filesystem and
plaintext-storage constraints.
