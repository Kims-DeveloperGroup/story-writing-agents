# Use Antigravity agents

> **Availability:** Antigravity is implemented through the `agy` CLI for
> debates, direct conversation, bounded retrieval, and persistent provider
> conversations. Codex remains the default provider.

Key terms: **backend name**—the configuration value `antigravity`;
**executable**—the command `agy`; **provider conversation**—Antigravity's private
history identified by a runtime-managed ID; **taint**—a marker that prevents
continuing uncertain provider history.

## Install and authenticate `agy`

Follow the vendor's current
[Installation & auth guide](https://antigravity.google/docs/cli/install/). On
macOS or Linux, its installer is:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

The default executable path is `$HOME/.local/bin/agy`. If it is not already on
`PATH`, add the directory to the applicable shell profile. For zsh:

```bash
export PATH="$HOME/.local/bin:$PATH"
source ~/.zshrc
command -v agy
agy --version
```

The official guide also provides Windows PowerShell and Command Prompt
installers. After installation, launch `agy` in a terminal and complete its
browser or remote-SSH authentication flow. Antigravity stores signed-in account
credentials in the operating-system keyring; `/logout` is entered inside the
Antigravity prompt. The story runtime neither performs login nor writes
credentials into its workspace.

Keep credentials out of story configuration, prompts, drafts, filenames, and
logs. Provider subprocesses inherit the story process environment, so treat
environment variables as visible to the selected local provider executable.

## Choose Antigravity for an agent

The only backend names are `codex` and `antigravity`. An omitted backend is
`codex`. The executable name `agy` is not a backend value, and `gemini` is not
an alias. A legacy `gemini` value fails with:

```text
backend 'gemini' is no longer supported; use 'antigravity'
```

For a persistent route, use global `/settings`, choose Providers, choose an
agent, and select Antigravity. Model and effort then edit that agent's active
provider settings. Switching providers preserves the inactive provider's saved
settings. Direct JSON editing is also supported; this excerpt routes only the
plot architect and leaves omitted agents on Codex:

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

The complete persistent schemas are in
[Agent configuration](../reference/configuration/agent-configuration.md).

For one shell or run, use repeatable CLI overrides. Other agents retain their
persisted routes, which default to Codex:

```bash
story chat draft \
  --backend plot_architect=antigravity \
  --antigravity-model MODEL \
  --antigravity-effort medium

story run --session draft \
  --backend plot_architect=antigravity \
  --backend execution_specialist=antigravity \
  --draft-file ./draft.md
```

`--antigravity-executable EXECUTABLE` defaults to `agy`. Use it only when the
binary is not resolved through ordinary `PATH`. The legacy-named
`--codex-timeout SECONDS` bounds both Codex and Antigravity calls. These options
are available on `story chat` and `story run`, not on bare `story` or standalone
session lifecycle commands.

## Precedence and snapshots

Each backend construction validates and snapshots the complete persistent
configuration. A later file edit or `/settings` save affects the next shell or
backend, not one already running.

| Effective value | First non-null value wins |
| --- | --- |
| Provider route | Repeatable CLI or `backend_overrides` entry; per-agent `backend`; `codex`. |
| Codex model | `--codex-model` or `AgentBackend(model=...)`; per-agent `codex_model`; system `codex_model`; provider default. |
| Codex reasoning | Per-agent `codex_reasoning_effort`; system `codex_reasoning_effort`; provider default. |
| Antigravity model | `--antigravity-model` or constructor value; per-agent `antigravity_model`; system `antigravity_model`; provider default. |
| Antigravity effort | `--antigravity-effort` or constructor value; per-agent `antigravity_effort`; system `antigravity_effort`; provider default. |

Codex reasoning accepts `minimal`, `low`, `medium`, `high`, or `xhigh`.
Antigravity effort accepts only `low`, `medium`, or `high`. Model strings are
passed through after flag-safety validation; the runtime does not query a model
catalog or prove provider support.

## Capability preflight and fixed errors

Before provider work, preflight resolves every configured executable. For an
Antigravity route it also runs a bounded, provider-free `agy --help` capability
probe in isolated state and requires the public headless flags used by the
adapter. A successful probe does not validate authentication, network access,
model access, account quota, response quality, or a provider turn.

For each turn, the runtime launches `agy` directly without shell evaluation. It:

- copies one verified bound `AGENTS.md` into an isolated provider working
  directory;
- sends the confidential story request as one newline-delimited stream-JSON
  event on standard input and requests stream-JSON output;
- enables the CLI sandbox, selects plan mode, and disables slash commands;
- does not pass an approval-bypass or unrestricted-execution option; and
- drains stdout and stderr, validates the event stream and correlation fields,
  and reports fixed provider error categories instead of provider response or
  stderr content.

The command line still contains the runtime-owned output schema, isolated state
path, and any configured model, effort, or provider conversation ID. Treat
model names and local path/session metadata as process-visible, not secret.
Story prompts, drafts, retrieval observations, provider replies, and credentials
are not inserted into argv; credentials may instead be available through the
inherited environment or provider keyring.

`agy` retains the inherited option spelling `--gemini_dir` for its state-root
argument. That external compatibility spelling does not enable a `gemini`
backend in this runtime.

## Sessions, provider conversations, and retention

Story session lifecycle is provider-neutral: create or reopen makes durable
state ready, pause makes it paused, and process access is held only while a
backend is attached. Antigravity additionally keeps a stable, workspace-owned
state root beneath provider-session storage. Bound definitions are partitioned
by agent and definition fingerprint; provider-native invocation state is
isolated by story session, agent, and definition fingerprint. This state is
plaintext and should be included in workspace backup and exposure decisions.

The runtime manifest keys threads by story session, backend, and agent. A
compatible, untainted record is resumed automatically with its exact
Antigravity conversation ID. Conversation focus and debate IDs do not create
private provider channels; one provider thread can span them. A different
backend, session, agent, or definition fingerprint starts separate history.

| Action | Provider conversation effect |
| --- | --- |
| Switch, `/quit`, EOF, or backend close | Releases process access but preserves the manifest reference and Antigravity-native state. |
| `/session pause` | Persists `paused`, releases access, and preserves provider history. |
| `/session resume` or `story session reopen` | Makes paused state ready and resumes a still-compatible provider conversation. |
| Ambiguous failure or cancellation after launch | Marks existing compatible history tainted; the next call starts fresh. |
| Definition fingerprint change | Starts fresh for that agent without deleting older provider-native data. |

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

They cover all debate phases, direct and retrieval conversations, mixed
providers, session/thread partitioning, resume, taint/restart, timeout and
cancellation cleanup, fixed diagnostics, and confidentiality boundaries.

Local vendor checks are optional and still do not make a provider turn:

```bash
agy --version
agy --help
```

A real `story chat` or `story run` call is a live provider check. It may require
credentials and network access, consume provider quota, transmit story content,
and retain remote/provider-native history. It is never required by the standard
test suite.

See [Privacy and provider exposure](../operations/privacy-and-provider.md),
[Backend and sessions](../reference/backend-and-sessions.md), and
[Session storage](../reference/configuration/session-storage.md) for the exact
shared contracts.
