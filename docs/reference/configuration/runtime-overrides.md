# Runtime overrides and directives

> **Availability:** session-wide and per-agent language/persona overrides,
> standalone directives, attached shell directive commands, backend run
> overrides, round limits, and provider-specific model/effort defaults with
> per-agent fallback
> are implemented.

Key terms: **session override**—temporary configuration persisted for one story
session; **precedence**—which source wins; **directive**—an exact message that
changes overrides; **round limit**—the allowed replacement-revision count.

## Override schema

`StoryRuntime.set_runtime_overrides()` replaces the complete object for one
session. Accepted input and normalized persisted shape are:

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

The top level permits only `language`, `persona`, and `agents`. The first two
may be valid strings or `null`. `agents` maps canonical IDs to objects that
permit only `language` and `persona`, again strings or `null`. Validation
normalizes all three top-level keys. Referenced personas must already exist.

The object is atomically stored at
`agent_states/orchestrator_state/sessions/<session_id>.json` and survives new
runtime instances. No file means inheritance and an empty inspected runtime
state.

## Resolution precedence

Language and persona resolve independently, first match winning:

1. per-agent value in session runtime overrides;
2. session-wide runtime value;
3. value in that agent's `config/agent_configs.json` object;
4. value in `config/system_defaults.json`.

Backend routing is separate: `AgentBackend` one-run override, then per-agent
configured backend, then `codex`. Supported values are exactly `codex` and
`antigravity`; `gemini` is rejected with guidance to use `antigravity`. The
repeatable CLI `--backend CANONICAL_AGENT_ID=BACKEND` supplies only the first
level and never rewrites configuration.

Provider model and effort resolution is independent of the language/persona
and backend-routing cascades. First non-null value wins:

| Effective value | Precedence |
| --- | --- |
| Codex model | Explicit `--codex-model` or non-null `AgentBackend(model=...)` for every agent; then that agent's `codex_model` in `config/agent_configs.json`; then workspace `codex_model` in `config/system_defaults.json`; then the Codex provider default. |
| Codex reasoning | That agent's `codex_reasoning_effort` in `config/agent_configs.json`; then workspace `codex_reasoning_effort` in `config/system_defaults.json`; then the Codex provider default. |
| Antigravity model | Explicit `--antigravity-model` or non-null `AgentBackend(antigravity_model=...)` for every Antigravity-routed agent; then that agent's `antigravity_model`; then workspace `antigravity_model`; then the Antigravity provider default. |
| Antigravity effort | Explicit `--antigravity-effort` or non-null `AgentBackend(antigravity_effort=...)` for every Antigravity-routed agent; then that agent's `antigravity_effort`; then workspace `antigravity_effort`; then the Antigravity provider default. |

Passing a constructor model or effort as `None` is inheritance, not an explicit
request to clear a persisted value. There is no Codex reasoning CLI flag or
constructor argument. No provider model/effort is a session runtime override or
directive. The interactive `/settings Providers` editor is the installed
workflow for changing an agent's persisted active-provider model or effort; it
preserves inactive-provider values. Each `AgentBackend` validates and snapshots
the complete persisted system-default and per-agent configuration pair once
during construction. Later direct edits or `/settings` saves do not change that
backend or its runtime's persisted provider, language, persona, round, model,
reasoning, or effort defaults. Even late persisted corruption does not
reconfigure the active lifecycle; the next backend construction reads and
validates the then-persisted pair.

Session-wide and per-agent runtime language/persona overrides remain live above
that snapshot, and explicit backend and run arguments retain their higher
precedence. `/settings` changes persistent fallbacks only; it does not clear or
rewrite those overrides, refresh provider threads, or alter the precedence
table.

Codex reasoning accepts `minimal`, `low`, `medium`, `high`, and `xhigh`.
Antigravity effort accepts `low`, `medium`, and `high`. See
[Use Antigravity agents](../../guides/antigravity.md) for mixed routing and
provider-boundary details.

## Exact standalone directives

Only these complete forms are directives:

```text
[SYS: LANG=<language-token>]
[OVERRIDE: <canonical-agent-id> Persona=<persona-identifier>]
[RESET: ALL]
```

Parsing does not trim. Leading/trailing whitespace, a newline, changed case, or
a directive-looking substring returns no directive. The language form changes
the session-wide runtime language. Persona changes one agent and preserves its
other override values. Reset clears session-wide runtime language and persona
and all per-agent runtime values; it does not edit configuration, templates,
core documents, provider threads, histories, logs, or manuscripts.

`parse_directive(text)` returns `RuntimeDirective | None`.
`apply_directive(workspace, session_id, text)` validates the complete candidate
state before atomic replacement and returns the normalized object. Invalid
application raises `DirectiveError` and preserves prior state.

## Interactive directive bridge

With a current attachment, no conversation focus is needed:

| Shell syntax | Exact `StoryRuntime.apply_directive` text |
| --- | --- |
| `/language ARG` | `[SYS: LANG=ARG]` |
| `/persona AGENT ARG` | `[OVERRIDE: CANONICAL_AGENT Persona=ARG]` |
| `/reset` | `[RESET: ALL]` |

Persona route aliases normalize to a canonical ID before application. An
unknown agent reports `agent_invalid`; directive/configuration application
failure reports `configuration_invalid`. Candidate validation and atomic
replacement remain runtime-owned, so only validated success changes overrides.
These commands call no provider and change no defaults or focus state.

## Debate round limit

`max_debate_rounds` counts complete synthesized replacements, not audit passes.
`0` runs the initial `r0` audit and deadlocks if conflict remains; default `1`
also permits synthesis and audit of `r1`; `2` additionally permits `r2`.

The default can be replaced per call with `max_debate_rounds=` or CLI
`--max-debate-rounds`. It must be a nonnegative integer; Python booleans are
rejected even though they subclass `int`.

Formats and validation failures are in [Identifiers and errors](identifiers-and-errors.md).
