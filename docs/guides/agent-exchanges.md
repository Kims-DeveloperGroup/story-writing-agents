# Inspect agent exchanges

> **Availability:** bare `/exchanges` is available in the installed interactive
> shell after a story session is attached. Recording is automatic in the base
> install, and every normal base wheel or editable install includes the
> structured viewer. No setting, enablement step, or extra is required.

Agent exchanges are bounded diagnostic records for individual physical calls
to Codex or Antigravity. Use them to compare what Story asked a provider to do,
what exact valid `ProviderResult` the adapter returned, and how Story classified
the call. They are plaintext diagnostics, not a replay log or a security
boundary.

## Availability and installation

Install Story normally in the active environment. From a checkout:

```bash
python -m pip install --editable .
```

Or install the distribution normally:

```bash
python -m pip install story-writing-agents
```

Both forms install the required independent `jsonl-viewer` 0.3.0 from its exact
HTTPS VCS requirement at
`ce57f560f3ca1366ea69dfe15cb1c598513e2c82`. A first install normally needs
Git, network access, and repository access. Installation does not invoke a
story provider, open an exchange, or enable a service. The historical
`story-writing-agents[viewer]` and editable `'.[viewer]'` forms remain accepted
empty compatibility aliases; they install the same normal dependency set and
add no separate viewer capability.

Updating the checkout does not update the active environment by itself. After
pulling updated Story source, rerun the normal editable installation:

```bash
python -m pip install --editable .
```

That ordinary reinstall upgrades older viewer installations to required 0.3.0
without an uninstall, `--force-reinstall`, or `--ignore-installed`. Reserve a forced
reinstall for a genuinely damaged same-version installation, as described in
[Troubleshooting](troubleshooting.md#exchanges-does-not-open-the-expected-response).

The matching `vendor/jsonl-viewer` git submodule is optional review and
development evidence; pip resolves the base VCS requirement rather than
importing from the vendored directory. A checkout developer who needs to audit
or test that source can initialize it with:

```bash
git submodule sync -- vendor/jsonl-viewer
git submodule update --init --checkout vendor/jsonl-viewer
```

The `.gitmodules` entry must identify the matching repository; the gitlink,
`pyproject.toml`, and `uv.lock` must identify the same full commit. Story wheels
and source distributions deliberately omit
the vendored repository, while installed metadata retains the required VCS
dependency. The external package is Python 3.11+, MIT licensed, has no runtime
dependencies, and has no Story import. Story loads and validates its exact
three-name API and physical-input capability only when `/exchanges` prepares a
view. Versions 0.1.1 and 0.2.0 lack the required 0.3.0 contract and are
rejected before Story opens the viewer terminal. Required installation does
not create a static Story-to-viewer import.

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
changing session, conversation, debate, or storage. If the chosen
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
> 1 │   "request_type": "response",
> 1 │   "content": [expanded JSON string ×1] {
> 1 │     "response_text": [expanded JSON string ×1] {
> 1 │       "c": "wrong provider correlation value",
> 1 │       "d": []
> 1 │     }
> 1 │   }
> 1 │ }
Record 1/3 • source line 1 • JSON display 2 expanded, 0 skipped, 0 truncated
↑/↓ records • PgUp/PgDn scroll • g goto • / search • n/N • m mode • h help • q close
```

This preview shows the renderer's topology, not a retained Story screenshot.
The external repository owns the byte-tested renderer contract:

- [Terminal design system and Story-hosted scenario](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/ce57f560f3ca1366ea69dfe15cb1c598513e2c82/docs/design-system.md)
- [Nine deterministic complete sample frames](https://github.com/Kims-DeveloperGroup/jsonl-viewer/tree/ce57f560f3ca1366ea69dfe15cb1c598513e2c82/docs/samples)

Every body record is well-formatted multiline JSON. The left gutter repeats
the physical JSONL source-record line on continuation rows, so `g` can move to
an exact positive source line. Up/Down or `j`/`k` changes records; Page Up/Page
Down or `b`/Space pages within a large record and across record boundaries.

Simple mode shows `timestamp`, `request_type`, and `content`, in that order.
Verbose mode keeps those fields first, followed by every other top-level field.
`m` toggles modes unless the active keyword occurrence needs hidden fields.
Simple string leaves retain a visible complete-code-point preview through 4,096 UTF-8
bytes; Verbose string leaves retain one through 65,536 bytes. Before applying
that leaf limit, a complete strict JSON object or array stored inside a string
is recursively expanded through bounded encoding layers. The visible
`[expanded JSON string ×N]` cues remove the extra outer quote/backslash escaping
that otherwise obscures nested provider data; genuine text leaves retain the
escaping required for valid JSON. Preview truncation applies independently to
string leaves only—it never truncates a container, key, structural token, or
child. A visible leaf marker and footer accounting distinguish preview
truncation from terminal-width clipping. None of these derived projections
changes the source snapshot. Full-text search independently includes bounded
decoded nested keys and values, including text beyond these display previews.

The capture store has an independent content bound: an outer `content` value
retains the longest complete UTF-8 prefix through 262,144 bytes. In Verbose,
inspect `content_full_utf8_bytes`, `content_retained_utf8_bytes`,
`content_full_characters`, `content_retained_characters`, `content_truncated`,
and `content_sha256` before treating a nested request or response as complete.
Character counts are Unicode code-point counts, not display cells or grapheme
clusters. The digest covers the complete pre-truncation UTF-8 value; it detects
comparison differences but does not reveal or recover an omitted suffix.

### Search, results, and help

Press `/`, enter a nonempty phrase at `Search query:`, and press Enter.
Search is literal and case-insensitive across the entire retained snapshot:
keys, values, hidden fields, and bounded decoded JSON inside strings. It does
not recover content omitted by capture or retention. Queries are bounded to
1,024 characters. Field selectors have been removed in viewer 0.3.0.

On a capable terminal, the viewer owns one `Search query:` draft and the
`Go to source line:` draft opened by `g`.
Each appears in the frame footer with a printable `│` insertion cursor.
Left/Right, Home/End or Ctrl-A/E, Backspace, and Delete edit the draft. Ctrl-U
deletes before the cursor, Ctrl-K deletes after it, and Ctrl-W deletes the
preceding word and intervening whitespace. Command letters such as `j`, `k`,
`n`, and `q` insert literal text while a draft is open; Up/Down and Page keys
leave the draft and viewer position unchanged. Enter submits the
draft; invalid input keeps the draft open with a visible error.

Escape discards only the unfinished draft. The viewer renders its prior
committed selection, mode, active search, current match, and `current/total`
position. Unsupported or incomplete Escape sequences that decode to the
viewer's unknown-Escape input also cancel the draft. Input failures can instead
close the view and restore Story's terminal.
Story owns the terminal lifecycle and transports physical input; the viewer
owns editing, command interpretation, and cancellation.

The footer reports `current/total occurrences`. The active keyword is focused
as `⟦keyword⟧`; `n` and `N` move to the next or previous occurrence,
wrapping in both directions. Multiple matches within one value or record are
separate stops. Matching uses nonoverlapping original-character spans, including
Unicode casefold expansions. The active record has `@`; other matching
records have `*`, without highlighting their whole JSON.

Selecting an occurrence brings it into view with a bounded context window,
even beyond normal leaf previews. A labeled raw, normalized, or decoded search
excerpt supplies a match absent from the ordinary projection. At narrow widths,
keyword focus takes priority over context and, when needed, its repeated
gutter; geometry can still clip a long keyword. Manual paging remains available,
and `n`/`N` reveals the next selected occurrence. Resizing recomputes the
window. A selected hidden-field hit promotes to Verbose and prevents Simple
while that occurrence needs hidden fields. More than 100,000 occurrences
rejects the new search with a refine-query message and preserves the previous
search and position. `c` clears search; `h` or `?` opens transient help.

Escape dismisses help, then search, then a transient status; with nothing left
to dismiss it closes. This main-view behavior begins after a prompt has
finished or been cancelled; draft Escape preserves the committed view.
Ctrl-C applies the same cancel behavior in the main view and closes the view
during a draft. EOF always closes; `q` closes from the main view. A terminal
without alternate-screen support receives a content-free notice; `/exchanges`
does not print viewer frames into ordinary scrollback.

### Where color is used

On eligible ANSI terminals, bold cyan marks the header/chrome, current record,
and Help heading; a dim gray gutter separates source line numbers from JSON.
JSON keys are blue, strings green, numbers magenta, and booleans or `null`
yellow. Only the active keyword uses black on yellow, with printable
`⟦keyword⟧` focus and an `@` record marker. Other matching records use an
underlined yellow `*` gutter marker. Warnings/status are yellow, errors red,
truncation or other muted text gray, and ordinary footer/help text dim cyan.
JSON punctuation remains terminal-default text.

Color never carries meaning alone. Plain and `NO_COLOR` views retain the same
words, JSON, `⟦keyword⟧`, `>`, `@`, `*`, line numbers, result position, and help.
The linked external design system is authoritative for exact SGR values and responsive
placement.

## Interpret request, response, and outcome

Automatic capture attempts one sequence for each physical provider call:

1. `request` immediately before provider invocation;
2. `response` immediately after an exact valid `ProviderResult` returns, or a
   content-free provider-failure marker with any available completed-process
   diagnostic, before the terminal outcome; and
3. one terminal `outcome` when Story can classify the call.

The shared `exchange_id` correlates these records. The request content is the
canonical provider-neutral invocation projection. A response contains the raw
valid `ProviderResult` projection: `response_text`, `thread_id`, nullable usage,
and nullable actual transport bytes. The outcome contains a closed outcome and
fixed code. Rejected provider replies can also include `validation_details`:
bounded descriptions of the rejected JSON
property and its expected type, allowed values, or validation rule. Cross-property
rules name the required relationship. These descriptions come from trusted
validators and omit provider values, unknown member names, and arbitrary exception
prose. Each original or repair call has its own outcome and findings; older records
without this optional field remain readable.

The request records the actual submitted `payload` and its complete response `schema`.
Verification enforces that contract, with equivalent provider-specific schema
translation. Property reduction affects explicit runtime input projections and sharing,
never schema constraints. Request-local `context_objects` and `context_ref` markers
resolve within that same request; earlier exchanges are not needed. Sharing applies only
when recursive property count and bytes both decrease. Payload, schema and complete
request counts are measured separately, including objects inside arrays. Historical
records remain readable without migration. See [request
sharing](../design/agent-context.md#typed-provider-wire-v9).

A completed result that structured or semantic validation rejects therefore
normally appears as adjacent request, response, and `rejected` outcome records.
A provider invocation failure or timeout normally has a response containing
`{"provider_failure":true}` followed by a `provider_failure` outcome. When a
Codex or Antigravity process completed, that response can also carry a private
`response_diagnostic` with error, status, exit status, and stderr. This response
is diagnostic and is never a valid `ProviderResult`. Cancellation uses its
separate `cancelled` terminal and receives no provider-failure response marker.
A cancellation or fail-open append failure can produce a shorter sequence.

The exchange channel can capture completed provider stderr and error details only
through that typed response diagnostic. Those invocation streams receive
no semantic redaction and can contain provider-emitted OAuth URLs or status,
credentials, tokens, story content, or other secrets. Capture does not
independently inspect the environment, browser, or credential store. It never
imports a partial timed-out or cancelled stream, native login/status/model-
catalog output, or Antigravity proof transcripts/private thinking. Native
sign-in terminal bytes are never copied into exchanges. A missing response is
not proof that the provider produced no native output, and a missing record is
not proof that no call occurred. Capture is fail-open, advances before each
append, and never retries.

## User scenarios

### Inspect exchanges after a normal installation

1. Install Story normally, create or resume a session, and run provider work.
2. Attach that session and enter bare `/exchanges`; there is no viewer setting,
   enablement step, extra, or identifier argument.
3. Choose the exact Conversation or Debate ID plus subject, then choose the
   agent. The base-installed viewer opens the immutable snapshot.
4. If an older automation command still installs
   `story-writing-agents[viewer]`, it remains valid but supplies no capability
   beyond the normal installation.

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

1. While inspecting a match, press `/` and begin entering a query.
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
display every outer field, and full-text search includes all retained keys
and values, including bounded decoded nested content. Protect the workspace,
process memory, terminal infrastructure, copied frames, backups, and
redirected output as plaintext
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
| Viewer 0.1.1 or 0.2.0 remains after updating a checkout | Rerun the normal editable Story install to upgrade it to required 0.3.0; do not force or uninstall. |
| Required viewer unavailable | Rerun the normal Story installation, then retry. The compatibility extra is not a separate repair path. |
| Viewer 0.3.0 is installed but incompatible or damaged | Verify the approved exact pin, then use the bounded forced-repair path in Troubleshooting. |
| Store busy | Retry after the concurrent exchange write completes. |
| Unsafe, corrupt, or uncorrelatable records | Preserve the workspace and inspect the content-safe runtime diagnostic before retrying. |
| Selected stream disappeared | Rerun `/exchanges` to refresh retained inventory. |
| Viewer failed | Preserve source records and inspect the sanitized runtime diagnostic; Story does not print external exception prose. |

The terminal's fixed debug-log hint names only
`debug_logs/runtime.jsonl`. That log has safe exception type/frame facts, not
exchange content. Storage and viewer errors are content-free in terminal and
runtime-log output. See [Troubleshooting](troubleshooting.md) and the complete
[agent-exchange privacy boundary](../operations/privacy-and-provider.md#agent-exchange-privacy).
