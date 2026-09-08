# Use Antigravity agents

> **Availability:** the Antigravity adapter and persistent provider-session
> components are available to ordinary Lead turns in `story chat` and to
> Python `StoryRuntime.lead_turn()`. The shell also uses `agy` for native
> authentication, model discovery, and configuration. Codex remains the final
> configured route fallback.
> Settings Model/Effort editors also use bounded `agy models` discovery for a
> pending Antigravity route without persisting the catalog.
> Focused deterministic tests cover the adapter contract. Live acceptance with
> CLI 1.1.27 verified fresh and resumed Lead turns for Pro, Flash, and Sonnet;
> availability and results remain dependent on the environment and provider.

Key terms: **backend name**—the configuration value `antigravity`;
**executable**—the resolved Antigravity command, `agy` by default; **provider
conversation**—Antigravity's private history identified by a runtime-managed
ID; **taint**—a marker that prevents continuing uncertain provider history.

## Start one focused Lead conversation

Configure `lead_orchestrator` for Antigravity in global `/settings` under
Agents, then confirm **Apply Changes**. Create or attach a ready story session,
focus a conversation, and enter ordinary non-command text:

```text
$ story chat --workspace /workspace
story> /session create novel
story[novel]> /conversation new Opening consistency
story[novel]> Hello. What can you help me do?
[Lead]
  Outcome: answer
  Lead: I can discuss the story, inspect referenced canonical artifacts, and
        propose specialist review or a reviewed change for your confirmation.
  Next action: continue_with_lead
story[novel]> Does this reveal conflict with ${core_documents/global_outline.md}?
[Lead]
  Outcome: critique
  Lead: The cited outline places the reveal later; revise the opening or the
        governing outline before changing the manuscript.
  Next action: continue_with_lead
```

Exact Lead prose varies. The runtime owns the typed outcome, evidence receipts,
state, and next action. If the Lead proposes specialist work or an artifact
review, nothing starts until the shell presents the exact proposal and the user
confirms it. A later write/Apply and a later Git commit each require their own
separate confirmation; the provider never writes or commits directly. See the
complete [Lead workflow](lead-agent.md#the-normal-workflow).

Interpret a failed turn by its displayed next action:

| Visible result | User action |
| --- | --- |
| A normal answer, question, critique, recommendation, or confirmation request | Continue with the Lead or decide the exact displayed proposal. |
| `provider_result_rejected` / `retry_lead_turn` | Start a fresh user turn after correcting the request or provider behavior. Runtime never reuses or replays the rejected physical response. |
| `outcome_unknown` / `inspect_and_resolve_outcome` | Inspect the preserved outcome. Do not submit the same call again: external work may have occurred. |
| `provider_invocation_failed` with no valid provider result | Check authentication, selected route/model/effort, executable, schema support, and provider-native diagnostics. This code alone cannot establish whether replay is safe. |

## User impact

Users can now converse with the Lead through Antigravity in interactive mode,
attach canonical artifact references, receive evidence-grounded feedback, and
confirm selectively orchestrated specialist review. Compatible turns resume
the exact Antigravity conversation while runtime correctness continues to come
from current authoritative artifacts and validated evidence, not hidden
provider history.

Users still cannot treat Antigravity's advertised tool list as a runtime
capability grant, ask the provider to bypass confirmation, or assume that a
failed submitted call is safe to replay. Story content and provider-native
state remain plaintext exposure surfaces. Failures are shown as fixed runtime
categories and next actions; raw provider stderr, response bodies, story text,
and credentials are not copied into durable user-facing history.

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
public help tokens `--agent`, `--conversation`, `--disable-slash-commands`,
`--effort`, `--input-format`, `--json-schema`, `--mode`, `--model`,
`--new-project`, `--output-format`, `--print`, `--print-timeout`, and
`--sandbox`. The adapter also passes the inherited hidden option
`--gemini_dir=<state-dir>`, but
does not require that compatibility spelling to appear in public help. A
successful probe does not validate authentication, network access, model
access, account quota, response quality, or a provider turn.

For each turn, the runtime launches `agy` directly without shell evaluation. It:

- installs one verified custom main-agent definition at
  `.agents/agents/<agent-id>/agent.md` in an isolated provider working
  directory and selects it explicitly with `--agent <agent-id>`;
- adds `--new-project` for a fresh provider thread, or resumes only the exact
  retained ID with `--conversation <id>`; it never registers the canonical
  story workspace root as an Antigravity project;
- passes the exact confidential story request JSON as one `--print` argument,
  selects `--input-format text --output-format json`, and sends empty stdin;
- enables the CLI sandbox, selects plan mode, and disables slash commands;
- does not pass an approval-bypass or unrestricted-execution option; and
- drains stdout and stderr, requires one successful JSON object with a valid
  conversation ID, root-object structured output, and an exact schema echo,
  then applies the shared runtime correlation and semantic checks.

The custom definition declares `tools: [finish]`, `mainAgent: true`,
`subagent: false`, and `commandExecutionPolicy: off`. These declarations and
CLI flags do not prove provider-level capability removal. Earlier stream
probes of Antigravity CLI 1.1.27 advertised its full 57-tool main-agent catalog
when the definition declared only `finish`. The JSON result has no `init` or
step metadata, so Story cannot attest the selected agent, cwd, permission mode,
or tool/subagent behavior from this output. CLI 1.1.27 also warns that plan
mode has no effect when slash commands are disabled; Story retains both flags
without claiming that plan mode enforces containment.
The definition location and selection mechanism follow Antigravity's official
[custom-agent documentation](https://antigravity.google/docs/cli/commands/agents/).

Antigravity and Codex use the same rejected-response capture boundary. Only
after either adapter returns a completed response can structured JSON, Unicode,
domain, or identity rejection best-effort retain the response text beneath
`debug_logs/provider_error_responses/`. Raw stream events, stderr, nonzero exits,
transport failures, timeouts, cancellations, and accepted replies remain
excluded. The terminal and `debug_logs/runtime.jsonl` stay content-safe, and an
absent artifact does not change or narrow the original failure. Treat any saved
body as hostile plaintext and follow the inspection workflow in
[Privacy](../operations/privacy-and-provider.md#rejected-provider-response-artifacts).

The command line contains the full request, including any story input, drafts,
references, or retrieval observations carried in that request. Local process
listings and process-inspection tools may expose it. The runtime-owned schema,
isolated state path, configured model/effort, and provider conversation ID are
also process-visible. Credentials may be available through the inherited
environment or provider keyring; do not put them in request content.
Operating-system argument-size limits can reject a large request before the
provider process starts, producing a typed `ProviderInvocationError` with a
fixed start-failure diagnostic. Such a call has no submitted transport count.

<a id="sanitized-provider-call-and-native-stream"></a>

### Sanitized provider call and JSON result

For a session named `novel`, the process working directory has this exact
adapter-owned structure (the root and fingerprint are sanitized):

```text
/workspace/agent_states/orchestrator_state/provider_sessions/.antigravity/
  sessions/novel/lead_orchestrator/<definition-sha256>/workspace
```

The provider-state directory is its sibling `provider`. A fresh Lead call is
equivalent to this shell-free argument vector; wrapping is only for
readability:

```text
cwd=/workspace/agent_states/orchestrator_state/provider_sessions/.antigravity/sessions/novel/lead_orchestrator/<definition-sha256>/workspace
agy --gemini_dir=<runtime-root>/provider \
  --input-format text --output-format json \
  --json-schema '<exact generated wire-v7 schema>' \
  --agent lead_orchestrator --sandbox --mode plan \
  --disable-slash-commands --print-timeout 300s --new-project \
  --print '<exact confidential wire-v7 payload JSON, one argument>'
```

On a compatible later call, the adapter keeps `--agent lead_orchestrator` and
replaces `--new-project` with `--conversation agy-c-1`. Antigravity reopens the
project associated with that exact conversation. Story never supplies
`--project` and never lets Antigravity infer the outer story Git root as its
project. This matches the provider's documented
[project and conversation behavior](https://antigravity.google/docs/cli/projects/).

The payload is the exact UTF-8 inner JSON string, without a native event wrapper
or an added newline. Standard input is empty. This transport was verified with
CLI 1.1.27: raw text stdin was ignored in probes with an empty `--print` argument,
`-`, or a constant print value. The response schema still requires the descriptive
typed result even though the input uses compact runtime field names.

A successful native response is exactly one UTF-8 JSON object. This sanitized
shape is deliberately non-executable: `json_schema` stands for the complete
object supplied on argv, whose canonical bytes must match exactly.

```json
{
  "status": "SUCCESS",
  "conversation_id": "agy-c-1",
  "structured_output": {
    "correlation": "<correlation-1>",
    "action": {
      "kind": "answer",
      "result": {
        "scope": "workflow_meta",
        "evidence_status": "not_applicable",
        "evidence_ids": [],
        "kind": "answer",
        "message": "<sanitized reply>",
        "artifact_intent": null,
        "proposal": null
      },
      "used_handles": []
    }
  },
  "json_schema": {"<documentation-placeholder>": "exact generated wire-v7 schema object"},
  "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0}
}
```

The example shows a Lead ANSWER; other calls require their selected result type.
Story rejects invalid UTF-8, duplicate fields, non-finite numbers, additional
JSON objects, and trailing non-whitespace data. It requires `SUCCESS`, a safe
`conversation_id` matching the retained ID on resume, object
`structured_output`, and the exact object schema echo before constructing a
`ProviderResult`. It never falls back to a string-valued `response` field.
Usage comes only from the terminal object's recognized exact nonnegative
integer counters; absent or invalid counters remain unavailable. The complete
closed SEARCH, READ, and seven-field Lead ANSWER branches are shown in
[What providers receive](lead-agent.md#what-providers-receive).

### Shared structured-output contract

Codex and Antigravity receive byte-identical provider-neutral schema and
payload semantics. Typed wire v7 has a closed required root object with
`correlation` and one closed `action`: SEARCH, READ, or ANSWER. ANSWER embeds
the invocation-specific result object directly; there is no JSON-in-string
result layer.

OpenAI Structured Outputs requires an object root, every declared field in
`required`, `additionalProperties: false` on closed objects, explicitly typed
array items, and a union with `null` for nullable values. See the official
[OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).
The common schema also stays within the subset documented for
[Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output).
Antigravity transport success additionally requires the root-object
`structured_output` and exact `json_schema` echo described by its official
[headless CLI guide](https://antigravity.google/docs/cli/headless/).

Transport validation does not replace Story's checks of correlation,
runtime-issued handles and evidence IDs, the Lead outcome matrix, input
aliases, artifact authority, or user confirmation. Applications do not call
separate Codex or Antigravity SEARCH/READ/ANSWER APIs: Story may make bounded
continuation calls using the same provider invocation boundary.

### Why the earlier Lead call did not proceed

The previous adapter allowed Antigravity's default-agent/default-project
resolution while using an outer story Git root as its working directory.
Antigravity selected its general main agent, attempted `ListDir`, received a
request-review soft denial, and emitted no valid terminal structured result.
Story consequently had no valid `ProviderResult`: the exchange recorded
`provider_invocation_failed`, and an unmatched durable submitted call could be
projected as `outcome_unknown` during recovery.

| Before | Current implementation |
| --- | --- |
| Provider default agent and project could be selected from the outer story Git root. | Runtime selects its bound custom Lead agent in an isolated nested workspace and creates or resumes only that provider project. |
| An attempted `ListDir` could end in request-review denial without a valid terminal result. | A successful JSON object must contain object `structured_output` and an exact schema echo; this output cannot attest tool behavior. |
| The user saw a coarse invocation failure and recovery could find a submitted unknown outcome. | Deterministic returned rejections close with `retry_lead_turn`; only a genuinely unresolved submitted call stays `outcome_unknown` and non-replayable. |

That failure is different from a deterministic returned-result rejection. An
invalid request/output schema, authentication or launch error, transport
failure, nonzero exit, or missing terminal result also fails before a valid
`ProviderResult` exists. By contrast, `malformed_structured_response` and
`answer_validation_failed` are returned-result rejection classes. Durable Lead
history closes such a call as `provider_result_rejected` and offers
`retry_lead_turn`; one bounded same-turn correction may be attempted when fresh
authority and budget remain, but normal success must not depend on correction.
Only a submitted call without an authoritative accepted or deterministic
rejected closure becomes `outcome_unknown`; follow
`inspect_and_resolve_outcome` and never replay it. See the
[recovery table](troubleshooting.md#distinguish-lead-provider-failure-from-result-rejection).

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

Adapter-owned staged and workspace custom-agent definition leaves are verified as
regular and singly linked when present. Canonical bytes are installed through
an exclusively created same-directory temporary with requested `0600` mode and
atomic replacement. A detected symbolic link, non-regular entry, or multiply
linked leaf is preserved and rejected before provider launch.

These directory-mode and definition-leaf protections do not descriptor-anchor
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
authority. Python and interactive Lead turns enter this path.
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
component tests does not perform a live provider call.

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
