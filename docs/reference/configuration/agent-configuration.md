# Agent configuration

> **Availability:** JSON defaults, per-agent routing, editable role prompts, and
> persona templates are implemented. Codex is the only working provider.

Key terms: **system default**—the final fallback value; **agent configuration**—
one role's persistent provider/language/persona choices; **role prompt**—that
agent's static instructions; **persona template**—a reusable behavior document.

## System defaults

`config/system_defaults.json` is an exact-key JSON object. Packaged defaults:

```json
{
  "language": "KO",
  "max_debate_rounds": 1,
  "persona": "default_kim_eun_hee"
}
```

`language` and `persona` are required strings that satisfy the identifier
rules and resolve to an existing template. `max_debate_rounds` is optional in
the file, defaults to `1` when absent, and must be a nonnegative integer that
is not a boolean. Unknown fields are rejected.

## Per-agent configuration

`config/agent_configs.json` accepts only the four canonical IDs and this shape:

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

An omitted canonical agent is an empty object. Each agent object permits only
`backend`, `language`, and `persona`; `null` inherits. Omitted backend becomes
`codex`. Allowed backend names are `codex` and reserved `gemini`; selecting
Gemini fails before provider invocation. Unknown agent IDs, fields, or backend
names are errors.

## Role prompts

Each role owns static strict-UTF-8 Markdown at
`config/agents/<agent-id>/AGENTS.md`. The files have no required placeholders
and are not interpolated into requests. Runtime definitions instead carry a
content-addressed reference to the role prompt, configured persona, language, and
allowlisted core documents.

Normal `story init` creates a missing prompt and preserves existing content.
`story init --overwrite-prompts` replaces all four canonical prompt files,
even if only one changed, while preserving every other existing file. Current
configuration and referenced files are resolved before every phase, so a
changed effective input causes a new binding on the next call.

The packaged `default_kim_eun_hee` persona describes strict, logical, candid,
skeptical writing-room behavior; it is not biographical impersonation.
Additional strict-UTF-8 Markdown templates may be placed at:

```text
config/persona_templates/<identifier>.md
config/persona_templates/alternative_templates/<identifier>.md
```

The top-level directory wins when both contain the same identifier. A configured
template must exist when configuration or a directive is validated.

Exact language and persona identifier formats are in
[Identifiers and errors](identifiers-and-errors.md). Runtime resolution order
is in [Runtime overrides](runtime-overrides.md).
