# Configuration

## Workspace Initialization

From the repository root, initialize the repository itself or another
workspace directory:

```bash
story-writing-agents init .
story-writing-agents init ./example-workspace
```

Initialization creates missing directories and default files. It is
idempotent and never repairs, replaces, or validates existing file content.
Configuration is read from disk on demand, including before every backend
invocation, so valid edits are visible without recreating `StoryRuntime`.

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
    "language": null,
    "persona": null
  },
  "execution_specialist": {
    "language": null,
    "persona": null
  },
  "lead_orchestrator": {
    "language": null,
    "persona": null
  },
  "plot_architect": {
    "language": null,
    "persona": null
  }
}
```

An omitted canonical agent is equivalent to an empty object. Each agent object
may contain only `language` and `persona`; either may be `null` to inherit.
Unknown agent IDs and unknown fields are errors.

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
The resolved token becomes the visible-response instruction in every agent's
system prompt.

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
story-writing-agents run --workspace . --session demo \
  --backend provider_module:backend \
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
story-writing-agents directive --workspace . --session demo '[SYS: LANG=EN]'
story-writing-agents directive --workspace . --session demo \
  '[OVERRIDE: plot_architect Persona=default_kim_eun_hee]'
story-writing-agents directive --workspace . --session demo '[RESET: ALL]'
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
