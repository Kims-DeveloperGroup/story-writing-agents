# Use Antigravity agents

> **Availability:** the Antigravity adapter and persistent provider-session
> components are available to ordinary Lead turns in `story chat` and to
> Python `StoryRuntime.lead_turn()`. The shell also uses `agy` for native
> authentication, model discovery, and configuration. Codex remains the final
> configured route fallback.
> Settings Model/Effort editors also use bounded `agy models` discovery for a
> pending Antigravity route without persisting the catalog.
> Focused deterministic tests cover the adapter contract and call-time
> authentication recovery. Live acceptance with CLI 1.1.27 verified fresh and
> resumed Lead turns for Pro, Flash, and Sonnet; availability and results remain
> dependent on the environment and provider. The current call-time authentication
> evidence limitation is recorded with the dated snapshot below.
> The [17-case conversation matrix](#opt-in-live-conversation-matrix) includes
> actual provider turns and explicitly labeled fault injections; see the dated
> evidence snapshot below.

Key terms: **backend name**—the configuration value `antigravity`;
**executable**—the resolved Antigravity command, `agy` by default; **provider
conversation**—Antigravity's private history identified by a runtime-managed
ID; **taint**—a marker that prevents continuing uncertain provider history.

## Start one focused Lead conversation

Configure `lead_orchestrator` for Antigravity in global `/settings` under
Agents, then confirm **Apply Changes**. Create or attach a ready story session,
focus a conversation, and enter ordinary non-command text. This illustrative
example supplies a finished outline candidate inline, so Plot can review it
without an invented drafting round:

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
story[novel]> Review this finished outline for causal order: A courier loses a letter, retraces the route, and returns the recovered letter.
[Lead]
  Outcome: proposal
  Lead: I propose Plot review of the supplied outline for causal order.
  Next action: submit_confirmation
You: structured Confirm for the exact Plot review proposal
Plot: <review of the supplied outline>
Lead: <synthesis and the next safe action>
```

Exact Lead prose varies. The runtime owns the typed outcome, evidence receipts,
state, and next action. If the Lead proposes specialist work or an artifact
review, nothing starts until the shell presents the exact proposal and the user
confirms it. A later write/Apply and a later Git commit each require their own
separate confirmation; the provider never writes or commits directly. See the
complete [Lead workflow](lead-agent.md#the-normal-workflow).

For new material, provide a sufficient brief and ask for drafting instead. The
Lead discloses `draft` assignments for every affected domain: Plot for outlines
and structure, Character for motivation/world/continuity, and Execution for
prose/POV/tone. Mixed work requires their affected union. Specialists return
drafts without writing files; the Lead integrates all required drafts for the
current task/revision before preparing an authored candidate. `review` requires
concrete material and explicit criteria. Every required reviewer assesses the
exact final candidate before a separate Apply decision and then a separate
Commit decision. A completed consultation or sign-in does not satisfy an open
authoring objective; only its exact successful Apply does.

Interpret a failed turn by its displayed next action:

| Visible result | User action |
| --- | --- |
| A normal answer, question, critique, recommendation, or confirmation request | Continue with the Lead or decide the exact displayed preparation or proposal. Preparation Confirm, review/execution, Apply, and Commit are separate decisions. |
| Native authentication opens during an active Lead operation | Complete the provider-owned sign-in. If the native interface remains open after sign-in, exit it normally so Story can perform its fresh status check. |
| `retry_lead_turn` after a read-only Lead failure or rejection | Send a new message, revise the wording, or resubmit the request in the same conversation, including after restart. Runtime preserves the original request and failure and starts a fresh turn. |
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
startup, `/auth`, or Settings flow. In those initial authentication flows, do
not enter `/exit`, Ctrl-C, EOF, another Enter, or a second code to return to
Story. If status is not authenticated,
native interaction continues with no overall Story login deadline. See
Antigravity's official
[Installation & auth guide](https://antigravity.google/docs/cli/install/).

Keep credentials out of story configuration, prompts, drafts, filenames, and
logs. Provider subprocesses inherit the story process environment, so treat
environment variables as visible to the selected local provider executable.

### Recover authentication during a Lead operation

A successful startup or `/auth` probe does not guarantee that every later
provider call will remain authenticated. The provider can reject a specific
call after the shell has already admitted the session. In interactive Lead
work, Story treats that native challenge as a fresh authentication event. It
blocks new provider calls, lets already active sibling calls reach a terminal
result, and coalesces challenges from the same Antigravity authentication
generation before returning terminal ownership for one provider-native sign-in.

Antigravity recovery invokes the same resolved bare `agy` command as ordinary
sign-in, even when the immediately preceding status probe still says
authenticated. Story does not add an OAuth flag, synthesize a browser URL, or
interpret native terminal output. Complete the native provider interaction. If
the Antigravity interface remains open after authentication, exit it normally.
Recovery proceeds only after native sign-in and a fresh provider status agree
that Antigravity is authenticated. An unchanged authenticated status while the
native process remains open does not end call-time recovery by itself.

Authentication assistance and provider-call replay are separate decisions. The
only Antigravity response that can authorize one automatic physical replay is
the supported exit-1, pre-execution rejection below, with no additional fields:

```json
{
  "conversation_id": "",
  "status": "ERROR",
  "response": "",
  "error": "authentication failed or timed out",
  "duration_seconds": 0,
  "num_turns": 0,
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "thinking_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 0
  }
}
```

After successful sign-in, Story may submit that exact logical request once
more at the physical-call boundary. The retry has a new `call_id` and exchange
identity while retaining the exact payload, schema, correlation, expected
thread, execution profile, and captured evidence. It is cancelled before
submission if the route, configuration, workspace, session, thread, or source
identity changed. The failed attempt and retry each consume physical-call
budget and remain separately inspectable.

The words `timed out` in that fixed provider message do not themselves set
Story's independent timeout fact. If the same failure also has a timeout fact,
authentication assistance and its exchange classification remain available,
but timeout defeats replay and continuation reuse and controls an eligible
read-only Lead closure as `provider_timed_out` with `retry_lead_turn`.

Any Antigravity 401 that lacks this complete pre-execution proof still receives
interactive authentication assistance, but Story does not replay it. Codex 401
events follow the same assistance path and are never automatically replayed;
a later valid `turn.completed` result remains a normal success despite an
earlier 401 event in the same native stream. Python and other noninteractive
paths raise `ProviderAuthenticationRequiredError` without launching native
sign-in. Restart never supplies replay authority for a prior authentication
failure.

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

First-start onboarding is separate from that global command. Initial
authentication establishes access only. If `default_backend` is still absent,
Story opens a standard **Default provider** picker before the lobby. The
newly authenticated or sole authenticated provider—or Codex when both
authenticate—is only highlighted as a recommendation; nothing is preselected.
Choose either enabled provider. Choosing unauthenticated Antigravity runs the
same native bare-`agy` sign-in and retries the picker if authentication does not
succeed. Only an authenticated choice reaches the guarded absent-only save.
Cancel, interrupt, or EOF exits before that save or runtime construction. The
saved choice is inherited by agents without their own route, whose Provider
setting remains editable, and later shells skip this onboarding picker.

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
the separate first-start default choice, or explicit global `/auth`, which
retains its established immediate absent-default behavior.
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
`--codex-timeout SECONDS` sets the positive per-call response timeout for both
providers, with a shared default of 900 seconds. Antigravity receives native
timeout `N` and an outer watchdog of `N + 30` seconds (900/930 by default).
Codex uses its sole watchdog at `N`, without the extra 30 seconds. A Lead turn
can make several calls, so this is not a whole-turn deadline. These options are
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

- enters the descriptor-selected exact session worktree as physical CWD and
  native project root, with no adapter-control fallback;
- derives a content-qualified native selector from the semantic story-agent ID
  and deterministic base role bytes, then uses that exact selector as the role
  YAML name, the unversioned read-only
  `.agents/agents/<native-selector>/agent.md` directory, and
  `--agent <native-selector>` value;
- adds `--new-project` for a fresh provider thread, or resumes only the exact
  compatible generation-bound ID with `--conversation <id>`; it never passes
  `--project`;
- passes the exact confidential story request JSON as one `--print` argument,
  selects `--input-format text --output-format json` (or `--output-format stream-json` when an active stream sink is bound for capable advisory progress), and sends empty stdin;
- enables the CLI sandbox, selects plan mode, and disables slash commands;
- launches the native CLI directly from the descriptor-selected session worktree
  without a Story-owned outer macOS sandbox;
- does not pass an approval-bypass or unrestricted-execution option; and
- drains stdout and stderr, validates one JSON envelope (extracting the terminal result from stream-json events when streaming) with a safe conversation
  ID, root-object structured output, and an exact rendered-schema echo, then
  applies the shared runtime correlation and semantic checks. Native `SUCCESS`
  is the normal path; only the [bounded recovery](#recover-a-completed-native-error)
  below admits the known malformed-function-call `ERROR`.

The custom definition declares `tools: [finish]`, `mainAgent: true`,
`subagent: false`, and `commandExecutionPolicy: off`. These declarations and
CLI flags do not prove provider-level capability removal. Earlier stream
probes of Antigravity CLI 1.1.27 advertised its full 57-tool main-agent catalog
when the definition declared only `finish`. The JSON result has no `init` or
step metadata, so Story cannot attest the selected agent, cwd, permission mode,
or tool/subagent behavior from this output. CLI 1.1.27 also warns that plan
mode has no effect when slash commands are disabled; Story retains both flags
without claiming that plan mode enforces containment. The native CLI is trusted
with the current user's OS permissions. Story does not prevent its independent
file reads/writes, Git access, process execution or network/IPC use. Guarded
Story reads and separately confirmed Apply/Commit remain runtime API controls.
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

Separately, `debug_logs/agent_exchanges/` can retain completed Antigravity or
Codex failure error, status, stderr, and exit status before its fixed
`provider_failure` outcome. A recovered Antigravity native `ERROR`, or a
timeout/truncation warning accepted
with exact current-call completion proof, retains the same private diagnostic
envelope alongside its returned structured result. These bytes use strict UTF-8 or
explicit base64 encoding within the existing capture bounds and retention.
Failures before a completed process, including outer-watchdog timeout and
process-start failure, supply no partial bytes through the shared process
runner; their response carries only the provider-failure marker when capture
succeeds. A completed native timeout/truncation envelope can instead be
retained, including on exit zero; acceptance still requires exact completion
proof and normal validation. Cancellation uses its separate terminal without a
failure marker. Capture remains best effort and at most once; gaps never
establish replay safety. See
[agent-exchange privacy](../operations/privacy-and-provider.md#agent-exchange-privacy).

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

Antigravity and Codex receive the same `agent-provider-wire-v10` request and response
semantics. The native invocation passes the response schema through `--json-schema`.
The [agent JSON field guide](../reference/provider-wire.md) documents complete shapes
and illustrative responses, including the `agent_action` root, `response_type`,
context source records, and direct conversation and consensus schemas.

The runtime delivers common instructions for every role, per-call
`contract_guidance`, and descriptions in the output schema. The request uses named
controls in `response_contract`, task data in `task_input`, and current evidence in
`context_observation`. The adapter's native transport envelope is distinct from the
semantic JSON returned by the agent. Provider exchange records capture the actual
payload and schema for inspection.

### Recover a completed native error

The adapter can recover the known improperly formatted function-call `ERROR`
only when the process exits with status zero and the complete strict JSON
envelope passes the same conversation, structured-output, and schema-echo
checks. It then requires matching native evidence for this physical call:

- a pre-call baseline ties fresh history to a newly created conversation, or
  resumed history to an unchanged prior transcript prefix;
- the appended request matches the exact current prompt, with valid ordered
  native steps and timestamps;
- the final single `finish` call matches `structured_output`, apart from the
  recognized string-valued native tool metadata, and is followed by the exact
  supported completion marker. Intervening tool activity or unknown errors
  cannot serve as completion proof.

Evidence is read through no-follow directory descriptors from the exact native
conversation beneath the adapter-owned provider state. The reader requires a
stable, singly linked regular transcript, bounded to 4 MiB, 512 KiB per line,
and 4,096 steps; a fresh-call directory snapshot admits at most 1,024 entries.
Missing safe filesystem primitives, unsafe paths, changed or stale evidence,
oversized traces, and unknown native formats leave the call failed. The reader
is coupled to Antigravity's native transcript format. It ignores private
`thinking` fields and does not copy the transcript into exchanges or story
history. This completion check does not attest tool containment or permissions.

Antigravity 1.1.28 can emit a native print-timeout or truncation warning and
exit zero with partial output. For either warning, even a `SUCCESS` JSON
envelope requires the same independent native completion proof for the exact
current call, plus the strict schema, identity, correlation, and domain checks.
Exit zero, structured output, or a finish marker from an earlier call is
insufficient. If completion cannot be proved, the call fails; a native
print-timeout failure is classified as timed out. The timeout/truncation path
has controlled-test coverage and native-binary marker inspection; no live
partial-output envelope verification is claimed.

Recovery adds no model retry and preserves the native error envelope in private
exchange diagnostics. The recovered semantic result still undergoes ordinary
correlation, domain, evidence, and authority validation. Native `SUCCESS` can
also fail those checks. The live audit preserves a rejected answer and verifies
any single explicit runtime-authorized correction separately; native success
alone never establishes domain acceptance.

### Shared structured-output contract

Both adapters validate the same closed, descriptive contract before semantic
normalization. Contextual replies select `search_context`, `read_context`, or
`submit_result` through `agent_action.action_type`. The current schema restricts
available actions, source types, and counts. A terminal `task_result` follows the
specific built-in result contract and includes `response_type`.

Use the [field guide](../reference/provider-wire.md) to build and interpret response
schemas. Agents receive these meanings in bound instructions, `contract_guidance`,
and schema descriptions. Invalid JSON, extra properties, unsupported values, and
wrong branches follow the bounded structured-result repair behavior. Diagnostics
refer to the descriptive provider fields. No legacy wire decoder is available.

Direct conversation, retrieval, and consensus calls use the same semantic naming
and guidance rules. Public Python and persistence codecs retain their own names.

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
| Provider default agent and project could be selected from an unrelated outer story Git root. | Runtime selects its bound role and exact correlated session worktree; compatible generation-bound history resumes, while foreign or unproved history boots fresh. |
| An attempted `ListDir` could end in request-review denial without a valid terminal result. | A successful JSON object must contain object `structured_output` and an exact schema echo; this output cannot attest tool behavior. |
| The user saw a coarse invocation failure and recovery could find a submitted unknown outcome. | Eligible read-only Lead failures and rejected results expose `retry_lead_turn`; uncertain specialist/synthesis or authoritative write outcomes retain their recovery gates. |

That failure is different from a deterministic returned-result rejection. An
invalid request/output schema, authentication or launch error, transport
failure, nonzero exit, or missing terminal result also fails before a valid
`ProviderResult` exists. By contrast, `malformed_structured_response` and
`answer_validation_failed` are returned-result rejection classes. Durable Lead
history closes such a call as `provider_result_rejected` and offers
`retry_lead_turn`; one bounded same-turn correction may be attempted when fresh
authority and budget remain, but normal success must not depend on correction.
Provider failure, timeout, cancellation, and interruption can also close an
eligible read-only Lead attempt with a typed failure. An exact legacy request
without closure exposes retry; explicit new input closes it before starting the
new turn. Inspection and reopening do not submit work. If authoritative history
or write outcomes remain uncertain, follow their recovery action. See the
[recovery table](troubleshooting.md#distinguish-lead-provider-failure-from-result-rejection).

## Sessions, provider conversations, and retention

Story session lifecycle is provider-neutral: create or reopen makes durable
state ready, pause makes it paused, and process access is held only while a
backend is attached. Antigravity additionally keeps a stable, workspace-owned
state root beneath the canonical control workspace's provider-session storage,
including when a backend was opened from a session worktree. Bound definitions
are partitioned by agent and definition fingerprint; provider-native invocation
state is isolated by story session, physical session generation, agent, and
definition fingerprint. These definition and state paths remain external to
the session worktree, while the provider's physical CWD remains that exact
session worktree. An overlap is rejected before any per-invocation state
directory or provider process is created. Mutable state is plaintext and should
be included in workspace backup and exposure decisions.

On supported POSIX systems, the adapter best-effort requests owner-only
(`0700`) modes for its Antigravity state root and adapter-owned definition and
session directory chains. Before a provider launch, it rejects a detected
symbolic link or non-directory in those components. It does not recursively
change the backend-owned containing directory, capability-probe scratch path,
or files created by `agy`; provider-native retention is unchanged.

Adapter-owned staged definitions remain external. For each immutable bound
definition snapshot, runtime recomputes the deterministic base role and
component-byte accounting. Projection owns and independently revalidates one
immutable plan tying the semantic ID, domain-separated SHA-256 selector of the
form `<semantic-agent-id>-<64 lowercase hex>`, and exact final bytes. Runtime
requires the staged file and binding accounting to match that plan, substitutes
the selector as the YAML `name`, and projects the final bytes through the live
session binding to the persistent, Git-excluded
`.agents/agents/<native-selector>/agent.md`. The same selector is passed to
`--agent`; the semantic agent ID still owns routing, settings, accounting, and
external state. Projection traversal is descriptor-relative and no-follow;
directories must be owner-controlled and the role must be regular, singly
linked, byte-exact, and have exact mode `0400`. Selected-role content
validation is bounded by the exact planned size; older unselected qualified
roles are validated through topology and metadata without reading their
content.

Each content-qualified leaf is published create-only. Supported role versions
coexist and are never replaced or pruned. If another publisher wins, runtime
adopts the winner only after its selector, topology, ownership, mode, single-
link status, and exact bytes all validate; a differing or unsafe winner is
preserved and rejected. The top-level `.agents` namespace and its case-folded
aliases must remain untracked and are excluded from story references,
SEARCH/READ sources, and artifacts. Any tracked variant fails closed before
projection mutation or provider submission, including on case-sensitive
worktrees for portability. Symbolic links, nonregular or multiply linked
leaves, writable roles, and foreign topology are likewise preserved and reject
the call. Any pre-existing qualified role directory must already contain
its one valid `agent.md`; an empty qualified directory is preserved and rejects
setup before the Git exclusion or role tree is mutated. Nonblocking reads
reject FIFOs and other special files promptly, and missing `O_NONBLOCK` support
fails before projection path access.

The common Git exclusion recognizes only LF records and CRLF records, treating
the CR immediately before LF as the line ending. A lone CR elsewhere is data,
not a separator. If a missing `/.agents/` entry would need to be appended after
a terminal lone CR, setup fails closed and preserves the exclusion bytes;
otherwise the intended final bytes remain limited to 64 KiB. An absent
`exclude` is published create-only. An existing safe file that its effective
owner can read is opened no-follow; its exact retained inode may be normalized
to `0600` before the suffix append, after identity, size, and named-path checks.
Exact final readback is required, and a normal append preserves the inode. A
concurrent pathname replacement is never
overwritten: its winner is preserved and the projection fails. Projection also
fails before opening or mutating paths when required effective-user,
descriptor-relative, no-follow, nonblocking, link, or link-no-follow
capabilities are unavailable.

The exclusion is accepted only when `/.agents/` is the final nonempty record;
a later negation causes a guarded append. Before publishing a role file and
again afterward, the Git owner proves the current generation, checks that no
`.agents` path is staged, and asks Git whether `.agents/` is effectively
ignored. A higher-priority `.gitignore` override or a concurrent index change
fails the call closed.

On failed fresh installation, cleanup removes only call-created directories
that still match their recorded identity and remain empty. It preserves
existing, changed, replaced, and nonempty paths, and cleanup failure cannot
replace the primary setup failure. The role projection is protected unversioned
control data, so “no provider residue” means no mutable provider state, schema,
or output in the session tree. Keep the complete workspace and external
provider-state paths protected through OS-level controls; the runtime does not
encrypt or securely erase them.

The runtime manifest keys threads by story session, backend, agent, and
effective continuation fingerprint. Antigravity's fingerprint includes the
physical session generation. A compatible, untainted record is resumed
automatically with its exact Antigravity conversation ID. Conversation focus
and debate IDs do not create private provider channels; one provider thread can
span them. A different backend, session, physical generation, agent, or
definition fingerprint starts separate history. Foreign or unproved native
history is retained but is not adopted as the current conversation. A
generation-qualified lookup or taint operation uses only its exact qualified
key and never falls back to the legacy base key.

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
| Definition fingerprint or session physical generation change | Starts fresh for that agent without deleting older provider-native data. |
| Provider change, including A→B→A | Advances route generation and starts a fresh target-provider thread. |
| Same-provider model/effort change | Retains route generation and remains eligible for native-thread reuse. |

A fresh Antigravity or Codex thread is not a new story session. The retained
context engine starts a new epoch by bootstrapping current role-relevant
authoritative sources; compatible later calls can use exact deltas.
Provider-private hidden history is optional acceleration and never correctness
authority. Python and interactive Lead turns enter this path.
The worktree boundary does not relax existing cancellation, timeout,
authentication-replay, accounting, diagnostic-capture, or taint rules; each
still uses its established prepared/submitted and safe-replay classification.
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

## Opt-in live conversation matrix

The repository-owned [case inventory](../../tests/fixtures/antigravity_live_cases.json)
and [runner](../../tests/antigravity_live_harness.py) declare 17 complete cases.
The dated results below describe earlier source snapshots; the current Lead v4
contract has no renewed native run or resolved-model attestation. L06C stops at
an exact pending recommendation to prepare a Plot consultation for a finished
supplied outline. Recommendation Confirm would authorize only preparation;
the resulting consultation would require its own separate structured Confirm
before specialist execution. The scenario submits neither decision.

The current routes are:

| Cases | Actual route and required scenario |
| --- | --- |
| L01-PRO-HIGH-PTY | `gemini-3.1-pro-high`: shell consultation with two specialists, history/exchanges inspection, close and resume. |
| L02-PRO-LOW-ONE | `gemini-3.1-pro-low`: Plot-only review of inline chronology against the outline, then resume. The request includes only the inline candidate and outline reference. |
| L03-FLASH-THREE | `gemini-3.8-flash-low`: three-specialist consultation. |
| L04-SONNET-ONE | `claude-sonnet-4-6`: one-specialist consultation and resume. |
| L05-MIXED | Configured Codex Lead, Antigravity Pro High plot specialist, and Sonnet character specialist. |
| L06A-QUESTION | Flash: fresh workflow-only clarification. |
| L06C-RECOMMENDATION | Flash: finished supplied outline, substantive Plot `REVIEW` scope, and an exact pending `draft_specialist_consultation_proposal` recommendation for `plot_architect`. No preparation decision, consultation proposal, specialist execution, Apply, or Commit. |
| L06B-CRITIQUE | Flash: critique unsafe workflow instructions about skipping confirmation. Locally loaded whole-outline and ranged-manuscript references supply context; no story judgment is requested. |
| L07-SEARCH-READ | Flash: fresh workflow-only literal scheduling lookup and citation through SEARCH→READ→ANSWER, without story interpretation. |
| L08-DECISIONS | Flash: finished inline outline review; exact Reject/Revise, stale/forged decisions, and ordinary `Yes`. |
| L09-SECOND-ROUND | Pro High: finished inline outline with chronology ambiguity, reviewed in two actual rounds with separate confirmation and the same task/candidate. |
| F01-REFERENCE-DRIFT | Flash: named reference drift before confirmation. |
| F02-LOST-AUTHORITY | Flash: finished inline outline Plot review; crash after confirmation, restart with original scoped proposal/evidence, then fresh confirmation through reply and synthesis. |
| F03-CANCELLATION | Flash: finished inline outline/character candidate reviewed by Plot and Character; named cancellation preserves the completed sibling, with inspection without replay. |
| F04-FAILURE-RECOVERY | Flash: finished inline outline/character review with injected exit failure, then an independent character-only finished candidate, fresh task/proposal/Confirm and native thread through terminal synthesis. |
| F05-DIAGNOSTICS-RECOVERY | Pro High: one unchanged completion plus five labeled mutations after real completion, including gated recovery. |
| F06-BUDGET-FINALIZATION | Flash: three-specialist review; inject only `next_intent` after native synthesis, preserving its current scope and adding explicit review bases. Complete budget finalization, reopen without replay, then answer a fresh workflow request. |

Run from the repository root in an installed development environment with
already authenticated providers and access to the configured models. The runner
does not install or authenticate providers. It consumes real quota and creates
disposable Git-backed writing workspaces in the private evidence directory.
For a newly authorized run:

```bash
umask 077
live_evidence="$(mktemp -d "${TMPDIR:-/tmp}/story-antigravity-live.XXXXXX")"
STORY_RUN_LIVE=1 python -B -m tests.antigravity_live_harness \
  --case all --evidence "$live_evidence"
```

Select one complete case by replacing `--case all` with, for example,
`--case L06A-QUESTION`. Alternatively, with a fresh private directory, the
unittest entry point for that case is:

```bash
STORY_RUN_LIVE=1 STORY_LIVE_EVIDENCE_DIR="$live_evidence" \
  python -B -m unittest \
  tests.test_antigravity_live_conversations.LiveConversationTests.test_l06a_question -v
```

Without `STORY_RUN_LIVE=1`, unittest skips live cases; a skip proves no live
behavior. The standalone runner rejects missing opt-in. Optional
`STORY_ANTIGRAVITY_EXECUTABLE` and `STORY_CODEX_EXECUTABLE` select executables.
Missing steps, unexpected outcomes, missing cleanup, or exhausted budgets fail
the case. The standalone matrix stops at the first failure, records remaining
cases as unexecuted, and exits nonzero. A complete matrix also requires all five
Lead outcomes and all four specialist assessment categories; one native result
or one successful case is insufficient. Positive `drafted` is draft material,
not a review assessment or final approval. Draft-mode `insufficient_evidence`
still contributes unresolved inputs; the audit must preserve them just as it
preserves non-supporting review inputs.

The declared ceiling is 312 reserved physical calls and 79,200 seconds from
the original budget epoch, with a 180-second native timeout and per-case limits
in the inventory. The reusable fresh inventory totals at most 228 calls. F04's
24-call allowance covers two separately confirmed lineages of at most 12 calls
each. Scenarios run sequentially; specialist concurrency comes only from runtime
dispatch. Reservations precede process creation, so failed attempts remain
charged. Preserve `call-budget.json`, the original epoch, and earlier
attempt directories. A separately authorized attempt in a fresh evidence
directory must retain the original ledger via `STORY_LIVE_BUDGET_ROOT` and record
prior case reservations through `STORY_LIVE_PRIOR_CASE_SUBMISSIONS`; changing
directories does not authorize a new budget. There is no automatic retry.

The harness pins the original fixture-artifact baseline before a crash and
checks that same baseline after reopening; it never treats reopened bytes as
a new baseline. F02 preserves the original proposal and required evidence,
rejects the consumed confirmation, and completes work only after a fresh
structured Confirm. F04 checks actual recovery of the failed
`character_custodian`: an independent character-only finished candidate uses
a fresh task and proposal requiring a new Confirm. It does not continue the
failed task or silently drop its Plot scope. The same specialist runs on a
fresh native thread and the Lead synthesizes the result. The successful sibling
and failed history remain intact.

For identical request payloads, the exchange audit requires a unique match from
the returned thread and structured response, or exact diagnostic stdout/stderr
bytes and exit status. Missing or indistinguishable evidence remains rejected;
list order does not establish a match.

Normal cases deliver actual provider results unchanged. Fault cases label each
injected change separately; mutations after process completion preserve the
actual native result as well as the injected change. F05's injected
`ERROR` proves recovery against authentic completion evidence; it does not prove
that a native malformed-finish error occurred naturally. Private captured
regressions and the checked-in sanitized synthetic fixture are separate evidence
classes. Keep raw story transcripts, provider thinking, local IDs, commands,
credentials, and native bytes private; review even `*.sanitized.json` before
sharing. See [exchange privacy](../operations/privacy-and-provider.md#agent-exchange-privacy).

Fixture provisioning may execute the actual internal `StoryCommitService`.
That setup does not cover the interactive artifact review, Apply, or Commit
flow. One-round scenarios explicitly request a terminal synthesis; focused
two-round coverage belongs to L09. F06 covers the provider-neutral completed
finalization added for
[#624](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/624)
when a requested continuation exceeds the remaining budget; see
[A focused second round](lead-agent.md#a-focused-second-round).
The matrix does not establish arbitrary conversation paths.

### Historical evidence snapshot (2026-09-09)

At that source revision, 122 deterministic tests passed, and all 17 then-declared
complete cases passed across accepted actual-provider runs:
the original 16 cases plus F06. The accepted cases include Pro High/Low, Flash,
Sonnet, mixed Codex/Antigravity, and the actual PTY workflow.
All five Lead outcomes and all four specialist assessment categories were
observed. F03 passed cancellation, sibling preservation, and inspection without
replay; F04 passed its then-declared separately confirmed failed-specialist
recovery.
The global ledger retains 228 charged reservations against the 312 ceiling,
including failed attempts.

Issue #631 added focused deterministic classification, replay, accounting,
concurrency, restart, and real-PTY recovery coverage. Separate live checks
completed full Lead proposal, specialist consultation, synthesis, restart, and
follow-up flows with `gemini-3.1-pro-low`, `gemini-3.8-flash-low`, and
`claude-sonnet-4-6`. The native authentication-to-replay live case did not
complete: fresh authentication remained indeterminate, so the runtime correctly
withheld the real Pro replay. This evidence does not claim a browser OAuth
completion or a live successful automatic replay.

F05 used six actual completions: the unchanged completion and a named injected
known `ERROR` were accepted; the four named invalid-UTF-8, invalid-JSON,
unrelated-error, and schema-mismatch mutations were rejected. Its corrected
full final audit reused retained calls with zero replay and preserved all 459
original evidence files. That accepted revalidation is separate from the
historical failed audit. It does not establish naturally occurring native
`ERROR` recovery.

F06 passed in eleven actual Flash calls, all native `SUCCESS`, with all 33
request/response/outcome rows audited. After the genuine native synthesis,
the named `budget_next_intent` fault changed only the delivered `next_intent`;
the original native result and every other delivered field were preserved.
The case completed budget finalization with retained synthesis and evidence,
rejected consumed authority, reopened without replay, and received a fresh
actual Lead answer. This verifies the provider-backed flow under the named
injection; it does not show that the model naturally requested that
continuation. One earlier ten-call F06 attempt failed a harness count
assertion after production had completed the consultation. That failed attempt
remains preserved; the eleven-call pass followed a harness correction and a
separately authorized retry.

The earlier Flash three-specialist attempt produced ten native `SUCCESS`
responses but failed on the unaffordable continuation tracked in #624. One
explicitly authorized repeat with identical input passed in eleven calls.
Both historical attempts remain recorded; that repeat alone did not verify the
#624 fix. F06 supplies the bounded finalization evidence above. The optional
recovery check using a copy of the naturally failed session was not run.
Earlier native 503/400 failures and incomplete attempts also remain in private
history.

F02 now passes after
[#627](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/627)
restored required proposal evidence on reconfirmation. Its earlier failure
remains recorded. The current [Lead recovery contract](lead-agent.md#internal-durability-and-recovery)
preserves the original evidence and requires fresh confirmation authority.

### Provider-free local checks

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
