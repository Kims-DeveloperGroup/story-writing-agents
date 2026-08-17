# Apply session directives

> **Availability:** exact standalone language, persona, and reset directives are
> implemented in the installed CLI and Python API. Current-attachment commands map
> to those same exact directives.

Key terms: **directive**—one exact standalone override message; **language
token**—the case-preserved language label; **persona**—a named behavior
template; **runtime override**—session-scoped configuration that wins precedence.

Directives change the effective agent definition for later calls in one story
session. They are strict messages, not conversational instructions: the quoted
text must be the complete positional argument, with exact case and no leading,
trailing, or newline whitespace.

Set a session-wide language:

```bash
story directive \
  --workspace ./my-novel \
  --session novel \
  '[SYS: LANG=EN]'
```

Override one canonical agent's persona:

```bash
story directive \
  --workspace ./my-novel \
  --session novel \
  '[OVERRIDE: plot_architect Persona=strict_plot_reviewer]'
```

Create `strict_plot_reviewer.md` first in either
`config/persona_templates/` or its `alternative_templates/` directory. Persona
names are file identifiers without `.md`; they are not arbitrary paths.

Clear every runtime override for this session:

```bash
story directive \
  --workspace ./my-novel \
  --session novel \
  '[RESET: ALL]'
```

With a current attachment, no conversation focus is needed:

| Enter | Runtime receives |
| --- | --- |
| `/language EN` | `[SYS: LANG=EN]` |
| `/persona plot strict_plot_reviewer` | `[OVERRIDE: plot_architect Persona=strict_plot_reviewer]` |
| `/reset` | `[RESET: ALL]` |

Persona aliases normalize to canonical agents. The shell calls
`StoryRuntime.apply_directive`; an unknown agent is `agent_invalid`, while a
missing/unsafe persona or other directive/configuration rejection is
`configuration_invalid`.
Only validated success changes the override file. Current attachment, defaults,
conversation focus, agent focus, transcripts, artifacts, and provider state
remain unchanged.

Reset clears the session-wide language and persona plus all per-agent language
and persona overrides. It does not modify system defaults, per-agent
configuration, templates, core documents, provider threads, debate logs,
agent history, or manuscripts.

## Know what changes next

The validated override object is atomically saved beneath orchestrator state
and survives new `StoryRuntime` instances. Language and persona resolve
independently. A per-agent runtime value wins over a session-wide runtime value,
which wins over per-agent configuration, which wins over system defaults.
Backend routing has a separate precedence and is not changed by directives.

The runtime assembles a fresh `AgentDefinition` before each phase. A changed
effective language or persona changes that definition's fingerprint, creates a
new binding when the agent is next called, and starts a fresh compatible Codex
thread for that agent. Other agents can continue using unchanged definitions.

Directive parsing itself returns `None` for text that is not an exact directive.
Applying that text raises `DirectiveError`; validation of the full candidate
state happens before atomic replacement, so the prior override file remains
unchanged after rejection. Unknown agent IDs and missing or unsafe persona
templates are rejected.

Use `story inspect --session novel` to see persisted overrides. For exact
syntax, validation, schema, and resolution order, use
[Runtime overrides](../reference/configuration/runtime-overrides.md).
