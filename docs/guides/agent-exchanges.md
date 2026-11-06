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

Both forms install the required independent `jsonl-viewer` 0.7.0 from its exact
HTTPS VCS requirement at
`a38a975eda4123df11c1fa4f796fd49ab88efcee`. A first install normally needs
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

That ordinary reinstall upgrades older viewer installations to required 0.7.0
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
four-name API and physical-input capability only when `/exchanges` prepares a
view. Versions before 0.6.0 lack the required owned-terminal contract and are
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

Story opens the copied snapshot with display context for the selected session,
scope and agent. The viewer is the independent `jsonl-viewer` package: it knows
records, optional groups/sections, scalar facets and explicit search paths.
Story supplies all call grouping, stage, outcome and completeness meanings as
immutable data. Inspection metadata contains no Story runtime object, semantic
callback, authority check or runtime preset. The existing generic terminal
transport retains its input and ownership boundary.

Each list item represents one captured physical call. Its summary includes the
first capture time, a short exchange identity, provider, recorded outcome and
known stage. Exact identities remain in the details and original records.
Request, Response and Outcome sections retain their original source lines;
empty sections say that no record is retained. Other historical kinds remain
accessible. Searching a call keeps its other records available for comparison.

At 120 terminal cells or wider, list and detail appear side by side. Narrower
screens show one pane at a time. Tab/Shift+Tab changes focus without losing
selection, scroll or committed conditions; `[`/`]` changes sections. Up/Down
moves through the focused list or detail. In detail, `h/j/k/l` moves the JSON
cursor, Enter folds a container, Page Up/Down (`b`/Space) pages, and physical
Left/Right pans long rows. `g` uses the original positive source-line number.

### Filters and incomplete records

Press `f` to edit a draft. Outcome, Provider and Stage allow multiple values:
values within a field are OR; different fields and the text search are AND.
Missing metadata is an explicit value, not an inferred stage or outcome.
Canonical choices remain selectable even when they currently produce zero
items.

Record presence uses independent single-choice fields:

| Field | Choices | Meaning |
| --- | --- | --- |
| Completeness | All, complete, incomplete | All three main record kinds are present, or at least one is absent. |
| Request present | All, present, absent | A request record was retained. |
| Response present | All, present, absent | A response record was retained, including a provider-failure marker. |
| Outcome present | All, present, absent | A terminal outcome record was retained. |

For any missing response, choose Response present = absent and leave the other
fields at All. To find calls with none of the three kinds, select absent in all
three presence fields. Complete plus an absent kind is a valid zero-result
query; changing one field never silently clears another. Historical
`no_response` records stay readable but do not count as a response.

Enter opens a field's options. Space changes a choice; Enter returns to the
filter draft. Escape inside the options cancels only that field edit. Apply
commits the complete draft. Reset filters restores only draft filters to All;
Reset all also clears the draft query and restores the default search scope.
These resets take effect only with Apply. Cancel or Escape from the outer
panel discards all drafts and restores the committed selection and scroll.

Historical `no_response` records remain available in the Response section and
source view, but do not count as an actual response. Without a `response` record,
the response-presence filter is Absent and the record set is incomplete.
Outcome values with an unsupported JSON type remain readable and have a Missing
outcome facet.

### Search, results, and help

Press `/` to edit a literal phrase, including spaces, then use Search. Select
the scope in the same panel. There is no regex, field-expression or command
syntax; existing Unicode case comparison is retained. Korean input, cursor
editing and cancellation use the same terminal input contract.

| Search scope | Original source searched |
| --- | --- |
| All text (default) | Every retained key/value and capture metadata, including request schemas. |
| Request payload | The actual submitted payload; its own data keys named `schema` remain searchable. |
| Response | Returned response text and retained provider failure/diagnostic content. |
| Outcome | Outcome, code, validation details and captured host diagnostics. |

The scope paths are built by Story. The viewer follows only bounded key/index
and explicit JSON-string decode steps. Folded fields, hidden fields and text
beyond previews remain searchable. Invalid or truncated inner JSON is never
recovered by parsing its prefix; unavailable coverage is reported. All text
searches what was retained, not bytes omitted by capture limits. Synthetic
annotations and host summaries are not silently added to source text.

A query accepts at most 1,024 printable characters and 100,000 occurrences.
Applying invalid or over-limit conditions keeps the previous committed result.
A valid zero-result query applies and displays an empty result instead of
leaving stale details. The viewer distinguishes total items, filter passes,
search-matched items, matching source records and individual occurrences; in
this view an item is a physical-call group. `n`/`N` moves occurrences, opening
the relevant section and ancestor containers while keeping the current
occurrence number visible. List movement remains separate from hit movement.

Escape inside the search panel cancels its draft, preserving the committed
query, scope, focus and position. `?` opens current keyboard help. `q` closes
the main view; Ctrl-C, EOF and the documented main Escape sequence retain the
existing cleanup behavior. Reopening starts a fresh view; filters, drafts,
folds and occurrence state are not saved by Story.

### Stage, task and evidence provenance

Story derives stage and task facts from exact, qualified typed history links.
Provider prose, a schema field named `stage`, task candidates offered to a
provider, and temporal proximity cannot establish them. Ambiguous or missing
links remain Missing. A displayed historical stage is not current permission
to review, apply or commit an artifact. A durably published typed stage can
remain known when later teardown records a post-response runtime failure; stage
and terminal outcome are separate diagnostic facts. Such a failure still does
not gain an accepted-task claim in captured host diagnostics.

New physical-call captures carry the local call ID in request and terminal
host diagnostics. Only the accepted terminal, after authorized validation and
successful deferred publication, can contain the actual selected task ID and
revision. Search that task ID in All text or Outcome and combine it with Stage
to inspect the same call. Rejected, cancelled and publication/runtime failures
do not gain a successful-task claim. Older captures are not rewritten; their
facts can remain Missing. A complete terminal can supply the call ID when its
request content was truncated, provided available identities agree.

Some host-bound review targets and digests exist only in typed history.
Their displayed summaries identify host provenance and exact event references;
the full values are not promised to occur in exchange source text. Captured
provider evidence remains accessible through original records and scopes.

Display metadata is bounded independently of source bytes. If the required
Story grouping or scope metadata cannot fit those bounds, the whole unchanged
snapshot opens as generic records with an Inspection notice. It never presents
only a silent prefix of the Story calls. Read failures remain content-safe.

### Where color is used

Color assists selection, keys, scalar types, matching containers and hits.
Plain and NO_COLOR output retain structural alignment, focus/caret markers,
explicit selection and occurrence counts. Mouse clicks use the same visible
items, fields and buttons as keyboard actions; wheel input scrolls its painted
region. Modal outside clicks do not discard drafts.

The external package owns exact rendering and input behavior. See its pinned
[generic design system](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/a38a975eda4123df11c1fa4f796fd49ab88efcee/docs/design-system.md),
[inspection contract](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/a38a975eda4123df11c1fa4f796fd49ab88efcee/docs/inspection.md),
and [nineteen deterministic sample frames](https://github.com/Kims-DeveloperGroup/jsonl-viewer/tree/a38a975eda4123df11c1fa4f796fd49ab88efcee/docs/samples).
Story's grouping and provenance rules live only in this repository.

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
never schema constraints. Request-local `shared_context_objects` and `shared_context_object_id` markers
resolve within that same request; earlier exchanges are not needed. Sharing applies only
when recursive property count and bytes both decrease. Payload, schema and complete
request counts are measured separately, including objects inside arrays. Historical
records remain readable without migration. See [request
sharing](../design/agent-context.md#agent-provider-wire-v11).

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

### Native token counters

Each response record belongs to one physical call, but its `usage` counters retain
the provider's reported scope. For Codex, they can be cumulative across a native
thread; inspect `usage_observation.scope` before comparing or adding them.

When native inspection identifies the current completed Codex request, the response
also records its latest valid native `last_token_usage`. This optional object has
`input_tokens`, `output_tokens`, and `cached_input_tokens`, separately from `usage`.
For example, cumulative input/output counters of 84,460/593 can accompany native
last counters of 44,142/417. These numbers illustrate the two different scopes.

The last counters are observed diagnostic evidence, not a replacement for cumulative
accounting. They can remain available when native total counters are missing or
malformed, or when `usage_observation.scope` is `unknown`. Response metadata still
uses the existing verified usage attribution; this extra record does not change it.
Missing, malformed, or unbound native last counters omit the field. A malformed
latest observation never falls back to an earlier counter, while measured zero
remains zero. Other providers and older records need not contain this field;
existing exchange files are not rewritten.

## User scenarios

### Inspect exchanges after a normal installation

Install Story normally, attach the intended session and enter `/exchanges`.
Choose the exact recorded scope and agent. Use Tab to inspect a call's source
sections. Closing the view returns to the same shell scope without another
provider call or any stored filter state.

### Diagnose a rejected structured response

Open Filters and choose Outcome = rejected. Optionally add Provider and Stage,
then Apply. Search a validation property or literal message in Outcome. Use
`n`/`N` for individual occurrences and the section controls to compare the
same call's submitted payload, response and terminal classification. A genuine
payload data key named `schema` remains in Request payload search; the outer
request contract schema is available through All text. Missing stage evidence
is not filled from schema text.

### Cancel an accidental search without losing your place

Open the search panel, change its phrase or scope, then press Escape. The
committed query, selected call and occurrence, focus and scroll remain in place.
Filter options have a separate local cancellation level; Apply is the point
where the outer filter draft changes the result. A deliberate valid zero-result
query applies and shows no stale call details.

### Confirm that one agent has no retained records

The complete agent roster includes agents with no retained records. Selecting
one opens the empty view without creating a diagnostic record. Distinguish
this state from zero filter matches, zero text matches and unavailable scoped
content. A retained provider-failure response marker counts as a response;
older `no_response` records remain separate readable evidence.
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
the shell without retaining the `/exchanges` command, picker, or viewer frames.
Earlier messages and results remain visible. It does not securely erase workspace
records or external copies.

Common bounded failures are:

| Result | Next action |
| --- | --- |
| No attached session | Attach the intended session, then retry bare `/exchanges`. |
| No records for the session | Run the intended provider work; do not invoke it merely to manufacture diagnostics. |
| Viewer 0.1.1, 0.2.0, 0.3.0, 0.4.0, 0.4.1, 0.4.2, 0.5.0, 0.5.1, 0.5.2, 0.5.3, 0.5.4, or 0.6.0 remains after updating a checkout | Rerun the normal editable Story install to upgrade it to required 0.7.0; do not force or uninstall. |
| Required viewer unavailable | Rerun the normal Story installation, then retry. The compatibility extra is not a separate repair path. |
| Viewer 0.7.0 is installed but incompatible or damaged | Verify the approved exact pin, then use the bounded forced-repair path in Troubleshooting. |
| Store busy | Retry after the concurrent exchange write completes. |
| Unsafe, corrupt, or uncorrelatable records | Preserve the workspace and inspect the content-safe runtime diagnostic before retrying. |
| Selected stream disappeared | Rerun `/exchanges` to refresh retained inventory. |
| Viewer failed | Preserve source records and inspect the sanitized runtime diagnostic; Story does not print external exception prose. |

The terminal's fixed debug-log hint names only
`debug_logs/runtime.jsonl`. That log has safe exception type/frame facts, not
exchange content. Storage and viewer errors are content-free in terminal and
runtime-log output. See [Troubleshooting](troubleshooting.md) and the complete
[agent-exchange privacy boundary](../operations/privacy-and-provider.md#agent-exchange-privacy).
