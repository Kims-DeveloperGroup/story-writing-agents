# Use Antigravity agents

> **Availability:** the Antigravity adapter and persistent provider-session
> components remain implemented. The current shell uses `agy` for native
> authentication, model discovery, and configuration, but has no public story
> turn or debate. Python `StoryRuntime.lead_turn()` can invoke the configured
> lead route. Codex remains the final configured route fallback.
> Settings Model/Effort editors also use bounded `agy models` discovery for a
> pending Antigravity route without persisting the catalog.

Key terms: **backend name**—the configuration value `antigravity`;
**executable**—the resolved Antigravity command, `agy` by default; **provider
conversation**—Antigravity's private history identified by a runtime-managed
ID; **taint**—a marker that prevents continuing uncertain provider history.

## Install and authenticate `agy`

Install Antigravity using its provider-supplied instructions, then make the
`agy` executable visible to the same environment that launches `story`. For a
zsh setup whose install location is already configured in `~/.zshrc`:

```bash
source ~/.zshrc
command -v agy
agy --version
```

At interactive startup, the runtime silently executes `agy models` by default
as the Antigravity status probe. Return code `0` means authenticated, `1` means
unauthenticated, a missing executable means unavailable, and other or timed-out
results are indeterminate. Selecting an unauthenticated Antigravity entry runs
the resolved executable without arguments: bare `agy` by default, or a
supported `--antigravity-executable PATH` override invoked the same way. Do not
substitute a Gemini command or add `login` or other sign-in arguments.

The native sign-in inherits stdin, stdout, stderr, and the process environment
and runs at the resolved story workspace root. It may open a
provider-controlled browser. The runtime does not synthesize or inspect a URL,
read provider output or account facts, capture tokens or credentials, or manage
Antigravity's native credential persistence. While native sign-in remains
open, Story runs individually bounded `models` probes with null input and
output. Those probes cannot consume terminal input or capture provider output.
Only a fresh authenticated result from that resolved executable can authorize
Antigravity; an authorization code, sign-in exit code, or prior status cannot.

On a local machine, Antigravity uses a saved keyring session when available and
otherwise opens the default browser; that normal flow does not require Story to
show or accept an authorization code. Over remote SSH, Antigravity itself
prints a URL and requires its provider-controlled copy/paste flow, including
one authorization-code paste back into its prompt. Story cannot remove that
remote provider requirement, but it never asks for the code again. Once the
silent probe reports authenticated, Story boundedly stops and reaps that exact
bare-`agy` login child if it is still interactive and automatically resumes the
startup, `/auth`, or Settings flow. Do not enter `/exit`, Ctrl-C, EOF, another
Enter, or a second code to return to Story. If status is not authenticated,
native interaction continues with no overall Story login deadline. See
Antigravity's official
[Installation & auth guide](https://antigravity.google/docs/cli/install/).

Keep credentials out of story configuration, prompts, drafts, filenames, and
logs. Provider subprocesses inherit the story process environment, so treat
environment variables as visible to the selected local provider executable.

## Choose Antigravity for an agent

The only backend names are `codex` and `antigravity`. The executable name `agy`
is not a backend value. Provider resolution is a CLI override, an explicit
per-agent route, the workspace `default_backend`, then Codex. A legacy
`gemini` value is not an executable route or alias and fails with:

```text
backend 'gemini' is no longer supported; use 'antigravity'
```

Use global no-argument `/auth` from the lobby or an attached shell to refresh
both providers and open the same native authentication picker. A completed new
authentication may fill a missing workspace default; cancellation keeps any
existing default and explicit routes unchanged.

For a persistent route, use global `/settings`, choose Agents, choose an agent,
and select Antigravity. If needed, native authentication runs as a subordinate
Provider-editor action and returns to Provider. The route and any missing
workspace default remain pending in memory; Escape or cancellation before
confirmed Apply Changes leaves configuration bytes unchanged. No configuration
value is persisted before Apply Changes is confirmed. Model and effort then edit
that agent's active provider settings. Route, model, and effort changes use the
full Apply Changes path. Switching providers preserves the inactive provider's
saved settings.

After that full-path Apply Changes is confirmed, Apply-time authentication may
rebase a missing workspace default only in the pending candidate. It becomes
durable only when conversation-storage readiness, parity, and the final guarded
settings write complete. This does not change startup authentication or
explicit global `/auth`, which retain immediate absent-default durability.
Direct JSON editing is also supported; this excerpt routes only the plot
architect and leaves omitted agents to inherit the workspace default:

```json
{
  "plot_architect": {
    "antigravity_effort": "medium",
    "antigravity_model": null,
    "backend": "antigravity",
    "codex_model": null,
    "codex_reasoning_effort": null,
    "language": null,
    "persona": null
  }
}
```

Entering Model or Effort runs one fresh `agy models` discovery for the pending
effective provider. This is separate from the startup status probe even though
both use the same command: Settings open, Agents open, agent/provider
navigation, and Provider editing launch no discovery process. Model choices are
Inherit, aligned opaque slug/display rows in provider order, Current custom
when needed, and Custom model. The picker does not freeze the example list in
Google's
[Antigravity headless/CLI guide](https://antigravity.google/docs/cli/headless/).

Effort choices are Inherit, global `low`, `medium`, `high`, and Current
compatibility when needed. Story does not infer effort from a model slug or
display name. Each attempt is bounded to five seconds and one MiB retained
stdout, discards stderr/raw diagnostics, and has no automatic retry or cache.
Fixed failure guidance preserves inherit/current/custom paths; re-entering
retries later. For these model/effort changes, Apply Changes writes only the
selected opaque value or null and affects the running shell immediately after
the complete staged snapshot is validated, persisted, and activated; no
restart is required and no catalog row is persisted. A launch override remains
higher precedence and makes its effective Provider, Model, or Effort field
read-only in that shell.

The complete persistent schemas are in
[Agent configuration](../reference/configuration/agent-configuration.md).

For one shell, use repeatable `story chat` overrides. Other agents retain their
persisted routes, then inherit the workspace default and final Codex fallback:

```bash
story chat draft \
  --backend plot_architect=antigravity \
  --antigravity-model MODEL \
  --antigravity-effort medium

```

`--antigravity-executable EXECUTABLE` defaults to `agy`. Use it only when the
binary is not resolved through ordinary `PATH`. The legacy-named
`--codex-timeout SECONDS` bounds provider subprocess work. These options are
available on `story chat`, not on bare `story` or standalone session lifecycle
commands.

## Precedence and snapshots

Each backend construction validates and captures the complete persistent
configuration. Direct file edits have no watcher: a newly launched backend
reconciles them, while already running shells retain their captured state.
A confirmed full-path `/settings` Apply Changes is the no-restart path for the
current shell; it authenticates and preflights the candidate, persists the
complete prepared snapshot, and activates a new backend/runtime generation. It
does not broadcast the change to other active shells. The global
Message-details-only exception prepares, verifies, and commits configuration,
then updates the active presentation flag after commit without authentication,
conversation-storage, backend/runtime activation, route-generation, or thread
work.

| Effective value | First non-null value wins |
| --- | --- |
| Provider route | Repeatable CLI or `backend_overrides` entry; per-agent `backend`; workspace `default_backend`; `codex`. |
| Codex model | `--codex-model` or `AgentBackend(model=...)`; per-agent `codex_model`; system `codex_model`; provider default. |
| Codex reasoning | Per-agent `codex_reasoning_effort`; system `codex_reasoning_effort`; provider default. |
| Antigravity model | `--antigravity-model` or constructor value; per-agent `antigravity_model`; system `antigravity_model`; provider default. |
| Antigravity effort | `--antigravity-effort` or constructor value; per-agent `antigravity_effort`; system `antigravity_effort`; provider default. |

Codex reasoning accepts any lowercase forward-compatible token matching
`[a-z][a-z0-9_-]{0,31}`. Antigravity effort accepts only `low`, `medium`, or
`high`. Model strings are passed through after flag-safety validation; backend
construction does not query a catalog or prove provider support. Interactive
discovery is advisory and does not make membership a persistence requirement,
so safe custom models remain supported.

## Capability preflight and fixed errors

Interactive authentication and adapter capability preflight are separate.
Authentication invokes the resolved executable with `models` for status and no
arguments for sign-in: `agy models` and bare `agy` by default.

Before provider work, preflight resolves each executable required by the
effective provider routes. For an Antigravity route it also runs a bounded,
provider-free `agy --help` capability probe in isolated state and requires the
public headless flags used by the adapter. A successful probe does not validate
authentication, network access, model access, account quota, response quality,
or a provider turn.

For each turn, the runtime launches `agy` directly without shell evaluation. It:

- copies one verified bound `AGENTS.md` into an isolated provider working
  directory;
- sends the confidential story request as one newline-delimited stream-JSON
  event on standard input and requests stream-JSON output;
- enables the CLI sandbox, selects plan mode, and disables slash commands;
- does not pass an approval-bypass or unrestricted-execution option; and
- drains stdout and stderr, validates the event stream and correlation fields,
  and reports fixed provider error categories instead of rendering provider
  response or stderr content.

Antigravity and Codex use the same rejected-response capture boundary. Only
after either adapter returns a completed response can structured JSON, Unicode,
domain, or identity rejection best-effort retain the response text beneath
`debug_logs/provider_error_responses/`. Raw stream events, stderr, nonzero exits,
transport failures, timeouts, cancellations, and accepted replies remain
excluded. The terminal and `debug_logs/runtime.jsonl` stay content-safe, and an
absent artifact does not change or narrow the original failure. Treat any saved
body as hostile plaintext and follow the inspection workflow in
[Privacy](../operations/privacy-and-provider.md#rejected-provider-response-artifacts).

The command line still contains the runtime-owned output schema, isolated state
path, and any configured model, effort, or provider conversation ID. Treat
model names and local path/session metadata as process-visible, not secret.
Story prompts, drafts, retrieval observations, provider replies, and credentials
are not inserted into argv; credentials may instead be available through the
inherited environment or provider keyring.

## Sessions, provider conversations, and retention

Story session lifecycle is provider-neutral: create or reopen makes durable
state ready, pause makes it paused, and process access is held only while a
backend is attached. Antigravity additionally keeps a stable, workspace-owned
state root beneath provider-session storage. Bound definitions are partitioned
by agent and definition fingerprint; provider-native invocation state is
isolated by story session, agent, and definition fingerprint. This state is
plaintext and should be included in workspace backup and exposure decisions.

On supported POSIX systems, the adapter best-effort requests owner-only
(`0700`) modes for its Antigravity state root and adapter-owned definition and
session directory chains. Before a provider launch, it rejects a detected
symbolic link or non-directory in those components. It does not recursively
change the backend-owned containing directory, capability-probe scratch path,
or files created by `agy`; provider-native retention is unchanged.

Adapter-owned staged and workspace `AGENTS.md` rules leaves are verified as
regular and singly linked when present. Canonical bytes are installed through
an exclusively created same-directory temporary with requested `0600` mode and
atomic replacement. A detected symbolic link, non-regular entry, or multiply
linked leaf is preserved and rejected before provider launch.

These directory-mode and rules-leaf protections do not descriptor-anchor
parent-directory traversal and are not application access control or race-proof
containment. Unsupported platforms, including Windows, and ordinary
mode-setting failures retain platform behavior. Keep the complete workspace
and provider-state paths protected through OS-level controls; the runtime does
not encrypt or securely erase them.

The runtime manifest keys threads by story session, backend, and agent. A
compatible, untainted record is resumed automatically with its exact
Antigravity conversation ID. Conversation focus and debate IDs do not create
private provider channels; one provider thread can span them. A different
backend, session, agent, or definition fingerprint starts separate history.

Every provider change advances the agent's durable route generation. The
nonzero generation participates in the definition fingerprint, so Codex →
Antigravity → Codex starts a fresh target-provider thread on both switches;
returning to an older provider does not revive the pre-switch route. A
same-provider model or effort change does not advance the generation and
remains eligible, but is not guaranteed, to reuse its compatible native thread.
Generation zero preserves the legacy definition fingerprint.

| Action | Provider conversation effect |
| --- | --- |
| Switch, `/quit`, EOF, or backend close | Releases process access but preserves the manifest reference and Antigravity-native state. |
| `/session pause` | Persists `paused`, releases access, and preserves provider history. |
| `/session resume` or `story session reopen` | Makes paused state ready and resumes a still-compatible provider conversation. |
| Ambiguous failure or cancellation after launch | Marks existing compatible history tainted; the next call starts fresh. |
| Definition fingerprint change | Starts fresh for that agent without deleting older provider-native data. |
| Provider change, including A→B→A | Advances route generation and starts a fresh target-provider thread. |
| Same-provider model/effort change | Retains route generation and remains eligible for native-thread reuse. |

A fresh Antigravity or Codex thread is not a new story session. The retained
context engine starts a new epoch by bootstrapping current role-relevant
authoritative sources; compatible later calls can use exact deltas.
Provider-private hidden history is optional acceleration and never correctness
authority. Python lead turns enter this path; the public shell does not.
See [Story sessions](story-sessions.md) and
[Agent-context design](../design/agent-context.md).

There is no user-facing session close or delete command. `AgentBackend.close()`
ends one local backend instance; it does not pause the durable story session,
erase its manifest, or delete Antigravity-native conversations. Do not promise
secure deletion or manually edit provider state and access claims.

## Verify without a provider turn

`story smoke` is provider-free but does not exercise either provider adapter.
The Antigravity suites instead use a deterministic fake `agy` subprocess and
require no provider credentials or network:

```bash
PYTHONPATH=src python -W error -m unittest \
  tests.test_antigravity_adapter \
  tests.test_antigravity_parity -v
```

They cover retained adapter/provider component contracts, mixed providers,
session/thread partitioning, resume, taint/restart, timeout and cancellation
cleanup, fixed diagnostics, and confidentiality boundaries. Passing these
component tests does not expose a public story command.

Local vendor checks are optional and still do not make a provider turn:

```bash
agy --version
agy --help
```

The default native authentication probe is `agy models`; it is not part of
`story smoke` or the adapter capability check. Sign-in invokes that resolved
executable with no arguments.

`story chat` performs live native authentication/status and can perform model
catalog discovery through Settings. After session attachment and conversation
focus, ordinary Lead input and confirmed collaboration can invoke the selected
Antigravity provider and consume quota. Low-level direct adapter invocation can
also transmit caller-provided content; it is never required by the standard
test suite.

See [Privacy and provider exposure](../operations/privacy-and-provider.md),
[Backend and sessions](../reference/backend-and-sessions.md), and
[Session storage](../reference/configuration/session-storage.md) for the exact
shared contracts.
