# Getting started

> **Availability:** this guide uses the installed CLI. Real runs and routed
> interactive turns support Codex and Antigravity. A workspace default is
> established from fresh native authentication; Codex is the final fallback.
> `story smoke` is provider-free. See [Story sessions](guides/story-sessions.md)
> for the shell.

Key terms: **editable install**—an installation that points at this checkout;
**workspace**—the local story project directory; **default session**—the saved
session used when a command omits its name; **current focus**—the conversation
used for turns in this shell; **smoke test**—a provider-free deterministic check.

## 1. Prepare the tools

Use Python 3.11 or later. Install the Codex CLI for the final fallback route:

```bash
npm install -g @openai/codex
rehash
```

To use Antigravity for any agent, install `agy`, confirm it is on `PATH`, and
choose the `antigravity` backend. The complete commands,
configuration fields, precedence, and safety boundaries are in
[Use Antigravity agents](guides/antigravity.md). New workspaces leave the
workspace default and per-agent routes unset; noninteractive resolution falls
through to Codex until configuration says otherwise.

Choose one Python environment. Do not create a standard-library virtual
environment inside active Conda.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story --help
```

For Conda, replace the first two lines with:

```bash
conda create --name story-writing-agents python=3.11
conda activate story-writing-agents
```

On PowerShell, activate a `venv` with `.venv\Scripts\Activate.ps1`. The
installed `story` and `story-writing-agents` commands are exact aliases.
The editable command above installs the RAG-free base. The optional conversation
RAG dependency uses an exact VCS pin. The base can run the read-only
`story rag status` workflow, but enabling or using the deterministic local
provider requires an approved internal, non-redistributing `.[rag]` install.
Installation alone does not enable RAG or send conversation content anywhere.
The installed noninteractive CLI supports explicit opt-in, rebuild, bounded
cited search, disable, and derived-only purge; direct Python callers can also
compose the same local runtime. Interactive `/settings` can persist a local
provider and automatic policy for the next shell, and `/rag QUERY` privately
arms one forced preflight for the next eligible focused turn. Follow
[Optional conversation RAG](guides/conversation-rag.md) for fresh/existing
submodule setup, `.[rag]` installation, commands, offline pin checks,
interactive use, persistence/trust limits, and the current redistribution
restriction.

The base also records bounded plaintext agent requests, exact valid
`ProviderResult` projections, and terminal outcomes without an added
dependency. To inspect them through bare `/exchanges`, install the independent
read-only viewer from this checkout:

```bash
python -m pip install --editable '.[viewer]'
```

The `viewer` extra uses an exact VCS commit and does not invoke a provider or
enable a service. Its matching submodule, pin checks, picker-first workflow,
preview, colors, privacy limits, and recovery are in
[Inspect agent exchanges](guides/agent-exchanges.md).

## 2. Check the runtime without a provider

```bash
story smoke
```

The command creates and removes a temporary workspace while exercising local
conversation, retrieval, and consensus persistence. Success includes
`"status":"passed"` and `"provider_tested":false`; it does not validate Codex,
Antigravity, `agy`, credentials, network access, terminal behavior, story
quality, or a model.

## 3. Prepare a story workspace

Initialize a dedicated directory, then work from inside it:

```bash
story init ./first-story
cd ./first-story
```

`init` creates missing scaffold directories and configuration without replacing
existing content. It does not create core-document templates. The four role
prompts under `config/agents/<agent-id>/AGENTS.md` are strict UTF-8 Markdown.
Canonical core documents are created or changed only when story agents return
full-text updates after unanimous consensus; the runtime does not treat a
manually created placeholder as an agent-produced artifact.

## 4. Begin the human workflow

Bare `story` is the primary interactive entry:

```bash
story
```

The welcome is followed by the first interactive gate. Physical widths of 48
columns and wider progressively reveal the original five-letter ASCII `STORY`
wordmark; 32–47 columns use a compact spaced identity, and narrower terminals
use the short identity. The capable path has four frames and exactly three
0.4-second waits; the wide completed projection is nine rows. When color is
eligible, `S`, `T`, `O`, `R`, and `Y` are bold cyan, magenta, yellow, blue, and
green respectively, using local foreground resets only. `NO_COLOR` remains
color-eligible when unset, empty/whitespace, or trimmed exact `0` (including
`NO_COLOR=0`); any other value disables decoration without changing the
environment. Presence of `STORY_REDUCED_MOTION` independently renders one
final colored frame with no animation or terminal mutation. Truthy/nonzero
`NO_COLOR`, non-TTY, `TERM=dumb`, non-POSIX, callback, and uncertain-output
paths render one plain final frame without delay. The welcome does not enter
the alternate screen, hide the cursor, or include provider output. The runtime
then silently checks Codex with `codex login status` and the resolved
Antigravity executable
with `models` (`agy models` by default). Select an unauthenticated provider to
hand the real terminal to its native sign-in: `codex login` for Codex or the
resolved Antigravity executable with no arguments (bare `agy` by default). The
native process runs at the resolved workspace root with the inherited
environment and may open a provider-controlled browser. The runtime does not
capture provider output, account information, OAuth URLs, tokens, credentials,
or native persistence.

At least one provider must freshly report authenticated. The runtime then
resolves all agent routes and freshly requires every distinct effective
provider before it creates the CLI context, story backend, controller, or
session. Picker Cancel keeps startup gated; Ctrl-C or EOF leaves cleanly.

When `default_backend` is missing, a provider authenticated by that immediate
interaction becomes the workspace default. Otherwise the sole authenticated
provider wins, or Codex wins when both are already authenticated. An existing
default and explicit agent routes are preserved. See the exact
[authentication and routing contract](reference/cli.md#interactive-provider-authentication).

At the lobby prompt, create a durable session and subject conversation:

```text
/session create first_novel
/conversation new Opening scene
Which established fact should anchor the opening?
/run --output part-01/opening.md Draft a restrained opening using the current conversation focus.
/inspect last
/exchanges
/status
/quit
```

The ordinary question is one advisory line. `/run --output PART/PATH.md PROMPT`
starts consensus with one bounded snapshot of the current focus. Ask the agents
to establish or modify outlines, character sheets, or narrative rules in that
prompt when needed; only the lead's post-approval full-text updates are
published. `/inspect last` shows safe metadata
for this process's latest accepted run. `/status` names the durable state,
process access, defaults, current attachment and focus, provider readiness, and
next action. `/quit` releases access without pausing. Run `story` again to resume
the ready default and restore its active default conversation. Use confirmed
`/session pause` when provider work should stop until the session is resumed again.
Resume it in the shell with `/session resume [NAME]`; omit `NAME` to choose from
the live pre-Enter rows on a capable terminal. Submitting the omitted command
rereads the available sessions. A successful nonempty read opens a generic
picker, which is live on a capable terminal and numbered in ordinary-line
fallback; an empty or failed read reports its result or error without a picker.

## 5. Automate without a TTY

Scripts, pipelines, and other non-TTY callers must use noninteractive
`story run`; it never receives conversation focus and does not open the
interactive authentication gate. Native provider authentication remains that
CLI's responsibility. Use explicit workspace, session, and
`--output PART/PATH.md` inputs in the [single automation example](guides/glasswater-scenario.md#use-one-explicit-automation-call-then-pause),
then consult the exact [CLI contract](reference/cli.md#workspace-noninteractive-behavior-and-exits).

Provider-bound prompt text may include inline `${canonical/path.md}` or
`${canonical/path.md}#Lx-Ly` indicators. Quote noninteractive prompts so the
shell does not expand `$`. See [Canonical artifact references](guides/artifact-references.md)
for exact paths, multi-reference examples, agent read enforcement, and the
interactive `$` completion and `$$` ranged-viewer workflow. Scripts still type
literal quoted indicators; they do not receive the interactive picker UI.

Continue with [Workspaces](guides/workspaces.md), [Consensus runs](guides/consensus-runs.md),
or the complete interactive [Glasswater journey](guides/glasswater-scenario.md).
Exact grammar and exits live in the [CLI reference](reference/cli.md).
The exact output and provenance contract is in
[Canonical writing artifacts](reference/writing-artifacts.md).
