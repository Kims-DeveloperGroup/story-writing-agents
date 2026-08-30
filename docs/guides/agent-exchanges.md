# Inspect agent exchanges

> **Availability:** bare `/exchanges` is available in the installed interactive
> shell after a story session is attached. Recording is automatic in the base
> install. Opening the structured viewer additionally requires the optional
> `viewer` extra.

Agent exchanges are bounded diagnostic records for individual physical calls
to Codex or Antigravity. Use them to compare what Story asked a provider to do,
what exact valid `ProviderResult` the adapter returned, and how Story classified
the call. They are plaintext diagnostics, not a replay log or a security
boundary.

## Install the optional viewer

The base project has no runtime dependencies and continues to record exchanges
without `jsonl-viewer`. From a Story checkout, install the optional boundary in
the active virtual environment with:

```bash
python -m pip install --editable '.[viewer]'
```

For an installed distribution, install the named extra instead:

```bash
python -m pip install 'story-writing-agents[viewer]'
```

The extra is an exact HTTPS VCS requirement. Its approved commit is
`5d3047aa86212810d867d5b40cb1df3262f1810f`. A first install normally needs
Git, network access, and repository access. Installation does not invoke a
story provider or open an exchange.

The matching `vendor/jsonl-viewer` git submodule is review and development
evidence; pip resolves the VCS requirement rather than importing from the
vendored directory. Initialize an existing checkout with:

```bash
git submodule sync -- vendor/jsonl-viewer
git submodule update --init --checkout vendor/jsonl-viewer
```

The `.gitmodules` entry, gitlink, `pyproject.toml`, and `uv.lock` must identify
the same full commit. Story wheels and source distributions deliberately omit
the vendored repository, while installed metadata retains the optional VCS
requirement. The external package is Python 3.11+, MIT licensed, has no runtime
dependencies, and has no Story import.

## Open a recorded scope

Attach the intended session, then enter the command without arguments:

```text
/session resume novel
/exchanges
```

`/exchanges` does not accept a session, conversation, debate, or agent ID. It
never silently creates, resumes, or selects a session, and it does not require
current conversation focus.

The first transient picker contains only scopes present in the attached
session's authoritative exchange inventory. It keeps conversations and debates
separate, orders them by their newest retained workspace sequence, and shows
the complete identity and subject:

```text
Conversation c20260830T010203_abcd1234 — Opening scene
Debate d20260830T010412_ef567890 — Draft an opening from the current constraints.
```

Conversation subjects come from the complete catalog, including archived
conversations. A debate has no durable subject field, so its display-only
subject is the whitespace-collapsed content of the exact first canonical user
initiation (`round=0`, `revision=r0`, prompt or draft). It is at most 512
characters, including a final `…` when shortened. Missing or malformed
initiation history displays `[subject unavailable]`. Neither projection changes
the stored ID or record correlation.

After scope selection, the second transient picker lists all four canonical
agents in roster order, including disabled agents and agents with no retained
records. Each row shows the full display name, canonical ID, and either its
record count or `No recorded exchanges`. Choosing an empty agent opens the
bounded empty viewer; it does not create a diagnostic record.

Cancel, Ctrl-C, or EOF at either picker returns without opening a view or
changing session, conversation, debate, agent focus, or storage. If the chosen
stream expires between inventory and snapshot, Story refreshes once and asks
you to retry `/exchanges`; it never substitutes another scope or agent.

## Read the viewer

Story passes one immutable in-memory JSONL snapshot to the isolated viewer. The
header carries the attached session, exact selected Conversation or Debate ID
and subject, and exact agent ID. A Story-hosted plain frame has this shape:

```text
AGENT EXCHANGES • READ ONLY • SIMPLE
Session: novel • Debate: d20260830T010412_ef567890 — Draft an opening. • Agent: character_custodian
> 1 │ {
> 1 │   "timestamp": "2026-08-30T01:04:12.000+00:00",
> 1 │   "request_type": "request",
> 1 │   "content": "{\"call_kind\":\"debate\",...}"
> 1 │ }
Record 1/3 • source line 1 • 15 fields hidden
↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close
```

This preview shows the renderer's topology, not a retained Story screenshot.
The external repository owns the byte-tested renderer contract:

- [Terminal design system and Story-hosted scenario](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/5d3047aa86212810d867d5b40cb1df3262f1810f/docs/design-system.md)
- [Seven deterministic complete sample frames](https://github.com/Kims-DeveloperGroup/jsonl-viewer/tree/5d3047aa86212810d867d5b40cb1df3262f1810f/docs/samples)

Every body record is well-formatted multiline JSON. The left gutter repeats
the physical JSONL source-record line on continuation rows, so `g` can move to
an exact positive source line. Up/Down or `j`/`k` changes records; Page Up/Page
Down or `b`/Space pages within a large record and across record boundaries.

Simple mode shows `timestamp`, `request_type`, and `content`, in that order.
Verbose mode keeps those fields first, followed by every other top-level field.
`m` toggles modes unless an active hidden-field search requires Verbose. Simple
strings retain a visible complete-code-point preview through 4,096 UTF-8 bytes;
Verbose strings retain one through 65,536 bytes. A visible truncation marker
and footer accounting distinguish preview truncation from terminal-width
clipping. Neither changes the source snapshot.

The capture store has an independent content bound: an outer `content` value
retains the longest complete UTF-8 prefix through 262,144 bytes. In Verbose,
inspect `content_full_utf8_bytes`, `content_retained_utf8_bytes`,
`content_full_characters`, `content_retained_characters`, `content_truncated`,
and `content_sha256` before treating a nested request or response as complete.
Character counts are Unicode code-point counts, not display cells or grapheme
clusters. The digest covers the complete pre-truncation UTF-8 value; it detects
comparison differences but does not reveal or recover an omitted suffix.

### Search, results, and help

`/` prompts for one enumerated top-level field and a nonempty query. Search is
case-insensitive substring matching over only these 18 fields:

```text
schema_version, workspace_sequence, sequence, timestamp, request_type,
session_id, scope_kind, scope_id, agent_id, provider, exchange_id, content,
content_full_utf8_bytes, content_retained_utf8_bytes,
content_full_characters, content_retained_characters, content_truncated,
content_sha256
```

On a capable terminal, both `Search field: ` and `Search query: ` are
history-free transient editors. Pressing Escape at either prompt discards only
that unfinished draft and redraws the exact last successfully presented viewer
frame. Story sends no viewer event, so the selected source record, mode, active
search, current match, and `current/total` footer position stay unchanged; the
next key is read as a viewer command. Prompt navigation keys edit locally.
Unsupported, truncated, or over-limit escape sequences cancel after bounded
consumption instead of leaking their remaining bytes into viewer commands.

The footer reports transient `current/total` position. `n` moves to the next
match and `N` to the previous match, wrapping in both directions. Searching a
field hidden by Simple mode automatically promotes the first hit to Verbose and
prevents Simple mode while that hidden-field search remains active. `c` clears
search, and `h` or `?` opens transient help.

Escape dismisses help, then search, then a transient status; with nothing left
to dismiss it closes. This main-view behavior begins after a prompt has
finished or been cancelled; prompt-local Escape does not clear or close the
view. `q` or EOF closes immediately. In ordinary-line fallback,
use `g LINE`, `/ FIELD QUERY`, `j`/`down`, `k`/`up`, `pgdn`, `pgup`, `n`, `N`,
`m`, `h`, `c`, and `q`/`quit` at `viewer> `.

### Where color is used

On eligible ANSI terminals, bold cyan marks the header/chrome, current record,
and Help heading; a dim gray gutter separates source line numbers from JSON.
JSON keys are blue, strings green, numbers magenta, and booleans or `null`
yellow. The current search match uses black on yellow and `@`; other matches
use underlined yellow and `*`. Warnings/status are yellow, errors red,
truncation or other muted text gray, and ordinary footer/help text dim cyan.
JSON punctuation remains terminal-default text.

Color never carries meaning alone. Plain and `NO_COLOR` views retain the same
words, JSON, `>`, `@`, `*`, line numbers, result position, and help. The linked
external design system is authoritative for exact SGR values and responsive
placement.

## Interpret request, response, and outcome

Automatic capture attempts one sequence for each physical provider call:

1. `request` immediately before provider invocation;
2. `response` immediately after an exact valid `ProviderResult` returns, before
   accounting, structured decoding, rejected-response storage, or provider
   thread persistence; and
3. one terminal `outcome` when Story can classify the call.

The shared `exchange_id` correlates these records. The request content is the
canonical provider-neutral invocation projection. A response contains the raw
valid `ProviderResult` projection: `response_text`, `thread_id`, nullable usage,
and nullable actual transport bytes. The outcome contains only a closed outcome
and fixed code, never arbitrary exception prose.

A completed result that structured or semantic validation rejects therefore
normally appears as adjacent request, response, and `rejected` outcome records.
A provider exception, timeout, invalid provider contract, cancellation, or
fail-open diagnostic write can produce a shorter sequence. In particular, a
malformed Antigravity no-result flow has request plus
`provider_failure`/`provider_invocation_failed` and no response when both
diagnostic appends succeed: no valid `ProviderResult` crossed the adapter
boundary.

The exchange channel never captures provider stderr, raw stdout or NDJSON,
native tool/reasoning streams, authentication or model-catalog output,
environment values, browser/OAuth facts, tokens, credentials, or arbitrary
exception text. A missing response is not proof that the provider produced no
native output, and a missing record is not proof that no call occurred. Capture
is fail-open, advances before each append, and never retries.

## User scenarios

### Diagnose a rejected structured response

If the shell reports
``BackendProtocolError: contextual provider read action is invalid: `d` must
contain source handles returned by the preceding context search``, Story
received a recognizable contextual READ action before any search or with
invalid/foreign values instead of handles from that preceding search. Story,
not the provider, raises this fixed content-safe exception. Its terminal
outcome is
`rejected`/`malformed_structured_response`, the contextual attempt is tainted,
its ready checkpoint does not advance, and the response is rejected. Raw `d`
values do not enter the error or conversation history.

If the shell instead reports
``BackendProtocolError: contextual provider response is invalid: `c` does not
match the current request correlation token``, valid exact-shape contextual
JSON supplied the wrong `c` value—for example, provider answer/prose—instead of
the current request correlation token. Story validates `c` before interpreting
a misleading READ action or its data. Story, not the provider, raises this
fixed exception; neither the received `c`, the expected token, nor prose placed
in `c` enters the error or history. The terminal outcome is
`rejected`/`response_correlation_mismatch`; the ledger is `MALFORMED`, the
contextual attempt is tainted, its provider thread state is cleared, and its
ready checkpoint does not advance.

Both cases retain internal
`ContextExecutionErrorCode.MALFORMED_ACTION`. Other malformed JSON or action
shapes retain the generic `BackendProtocolError: contextual provider action is
malformed`.

1. Keep the affected workspace private and attach the affected session.
2. Run `/exchanges`; choose the exact Debate or Conversation full ID and
   subject, then the agent named by the sanitized error or runtime log.
3. Search `exchange_id` or `request_type`; use `n`/`N` to compare the correlated
   request, optional response, and outcome.
4. In Verbose, check full/retained counts, `content_truncated`, and the digest.
5. If the outcome is `rejected`, inspect the valid raw response projection. If
   it is `provider_failure` with no response, do not infer missing native output
   or retry merely to fill the diagnostic.
6. Close with `q`. Reopening starts in fresh Simple mode with no prior cursor,
   search, match position, footer result, or help state.

### Cancel an accidental search without losing your place

1. While inspecting a match, press `/` and begin entering a field or query.
2. Press Escape before Enter. The unfinished text disappears and the exact
   prior read-only frame returns, including its source line, mode, active
   search, selected match, result position, and footer.
3. Press `n` or `N` to continue from that retained match, or `/` to start a new
   search. Nothing from the cancelled draft is searched, recorded, or added to
   command history.

The separate `debug_logs/provider_error_responses/` channel may retain an
eligible completed response rejected during decoding. It has different
eligibility and retention and is not imported into agent exchanges. See
[Privacy and provider exposure](../operations/privacy-and-provider.md#rejected-provider-response-artifacts).

### Confirm that one agent has no retained records

Choose a scope, then select an agent row marked `No recorded exchanges`. The
viewer shows its immutable empty state with the selected session/scope/agent
header and close/help cues. It does not create an exchange, invoke a provider,
or change which conversation or agent will receive the next story turn.

## Privacy, immutability, and recovery

Simple mode is not redaction, authorization, or access control. Request payloads
and provider response text can contain story content or secrets. Verbose can
display every outer field, and search enumeration limits search rather than
visibility. Protect the workspace, process memory, terminal infrastructure,
fallback scrollback, copied frames, backups, and redirected output as plaintext
provider-call data. The capable view uses one alternate screen, but terminal or
remote-session software can retain its own copies.

The viewer receives bytes, opens no Story file, performs no network request or
telemetry, and has no edit, export, replay, retry, live-tail, or persistence
operation. Story's `RawTerminalDriver` remains the sole raw-mode, signal,
geometry, input, output, alternate-screen, and restoration owner. Closing drops
picker, mode, cursor, search, match, result-index, and help state and restores
the shell. It does not securely erase workspace records or external copies.

Common bounded failures are:

| Result | Next action |
| --- | --- |
| No attached session | Attach the intended session, then retry bare `/exchanges`. |
| No records for the session | Run the intended provider work; do not invoke it merely to manufacture diagnostics. |
| Optional viewer unavailable | Install `story-writing-agents[viewer]` or editable `'.[viewer]'`, then retry. |
| Installed viewer incompatible | Reinstall the exact `viewer` extra and verify the approved pin. |
| Store busy | Retry after the concurrent exchange write completes. |
| Unsafe, corrupt, or uncorrelatable records | Preserve the workspace and inspect the content-safe runtime diagnostic before retrying. |
| Selected stream disappeared | Rerun `/exchanges` to refresh retained inventory. |
| Viewer failed | Preserve source records and inspect the sanitized runtime diagnostic; Story does not print external exception prose. |

The terminal's fixed debug-log hint names only
`debug_logs/runtime.jsonl`. That log has safe exception type/frame facts, not
exchange content. Storage and viewer errors are content-free in terminal and
runtime-log output. See [Troubleshooting](troubleshooting.md) and the complete
[agent-exchange privacy boundary](../operations/privacy-and-provider.md#agent-exchange-privacy).
