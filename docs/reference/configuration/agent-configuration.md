# Agent configuration

> **Availability:** JSON defaults, per-agent routing, interactive guarded
> settings, editable role prompts, and persona templates are implemented. Codex
> is the only working provider.

Key terms: **system default**—the final fallback value; **agent configuration**—
one role's persistent provider/language/persona choices; **role prompt**—that
agent's static instructions; **persona template**—a reusable behavior document.

## System defaults

`config/system_defaults.json` uses a closed five-key schema. New workspaces are
initialized with:

```json
{
  "codex_model": null,
  "codex_reasoning_effort": null,
  "language": "KO",
  "max_debate_rounds": 1,
  "persona": "default_kim_eun_hee"
}
```

| Key | Contract |
| --- | --- |
| `language` | Required string satisfying the language identifier rule. |
| `persona` | Required safe template identifier that resolves to an existing template. |
| `max_debate_rounds` | Optional; absent means `1`. A present value is a nonnegative integer and not a boolean. |
| `codex_model` | Optional workspace fallback; absent or `null` means no workspace model fallback. A string must be nonempty, must not begin with `-`, and must contain no Unicode `Cc`, `Cf`, or `Cs` character. The exact string is preserved: it is not trimmed, normalized, or checked against a model catalog. |
| `codex_reasoning_effort` | Optional workspace fallback; absent or `null` means no workspace reasoning fallback. The only string values are exactly `minimal`, `low`, `medium`, `high`, and `xhigh`. Whether `xhigh` is supported depends on the selected model/provider. |

Unknown fields are rejected. The complete object is validated before a backend
accepts any explicit model override, so a malformed persisted field is never
hidden by `--codex-model` or `AgentBackend(model=...)`.

Existing three-key files remain valid: missing Codex keys resolve as `null` and
remain the fallback layer for agents that do not set their own Codex values.
Running `story init` again, including with `--overwrite-prompts`, does not
rewrite or migrate existing system-default bytes. Global `/settings` edits this
canonical five-key object and the per-agent object through an isolated
current/pending view; it adds no supported public save API. The ten language
choices are exactly `KO`, `EN`,
`JA`, `ZH`, `ES`, `FR`, `DE`, `PT`, `IT`, and `RU`. Persona choices come from
the safe lexical catalog with top-level-template precedence. An existing
schema-valid language outside the UI list remains visible and preservable but
is not synthesized as a choice. Maximum debate rounds accepts a nonnegative
ASCII integer, including `0`. Blank per-agent Codex model stores `null` and
inherits the system model; Reasoning offers Inherit followed by `minimal`,
`low`, `medium`, `high`, and `xhigh`.

## Per-agent configuration

`config/agent_configs.json` accepts only the four canonical IDs and this shape:

```json
{
  "character_custodian": {
    "backend": "codex",
    "language": null,
    "persona": null,
    "codex_model": null,
    "codex_reasoning_effort": null
  },
  "execution_specialist": {
    "backend": "codex",
    "language": null,
    "persona": null,
    "codex_model": null,
    "codex_reasoning_effort": null
  },
  "lead_orchestrator": {
    "backend": "codex",
    "language": null,
    "persona": null,
    "codex_model": null,
    "codex_reasoning_effort": null
  },
  "plot_architect": {
    "backend": "codex",
    "language": null,
    "persona": null,
    "codex_model": null,
    "codex_reasoning_effort": null
  }
}
```

An omitted canonical agent is an empty object. Each agent object permits only
`backend`, `language`, `persona`, `codex_model`, and
`codex_reasoning_effort`; `null` inherits the corresponding system default
(and ultimately the Codex provider default). Omitted backend becomes `codex`.
Allowed backend names are `codex` and reserved `gemini`; selecting Gemini fails
before provider invocation. Unknown agent IDs, fields, or backend names are
errors.

The `/settings` Providers editor follows canonical roster order. It first
selects an agent, then offers Provider, Model, and Reasoning effort. Codex is
enabled; Gemini is visible but disabled for new selection. An existing Gemini
value remains visible and may be preserved by returning without a change or
remediated by selecting Codex. Model accepts exact safe text or blank to inherit
the system model; Reasoning offers Inherit followed by `minimal`, `low`,
`medium`, `high`, and `xhigh`. These edits update only the selected agent's
pending configuration.

Opening `/settings` captures exact bytes for both configuration documents, a
fully validated immutable pair, and the safe persona catalog. Save revalidates
the complete pending pair and requires all three opening facts to remain exact.
It writes only changed documents, canonically, in system-defaults then
agent-configs order. A failed write triggers reverse-order restoration of every
attempted target, including the failing target, from its exact opening bytes;
rollback uncertainty is reported separately. This is guarded coordination of
two atomic per-file replacements, not a public API or cross-file transaction.

## Role prompts

Each role owns static strict-UTF-8 Markdown at
`config/agents/<agent-id>/AGENTS.md`. The files have no required placeholders
and are not interpolated into requests. Runtime definitions instead carry a
content-addressed reference to the role prompt, configured persona, language, and
allowlisted core documents.

Normal `story init` creates a missing prompt and preserves existing content.
`story init --overwrite-prompts` replaces all four canonical prompt files,
even if only one changed, while preserving every other existing file. One
backend construction validates and snapshots the complete persisted system and
agent configuration pair. That active backend and its runtime keep the snapshot
for provider route, language, persona, debate-round, workspace fallbacks, and
each agent's Codex model/reasoning values; later valid edits, settings saves,
or persisted corruption do not reconfigure it. A later backend construction
reads and validates the then-persisted pair. Session language and persona
overrides remain live, and an explicit CLI/constructor model remains higher
precedence, while referenced role, persona, and core-document content
continues to participate in definition binding as documented.

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
