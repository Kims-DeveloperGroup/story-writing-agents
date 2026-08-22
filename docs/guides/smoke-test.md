# Run the provider-free smoke test

> **Availability:** `story smoke` is implemented and uses the ordinary runtime
> with a private deterministic backend and synthetic data. It does not read
> credentials or invoke Codex, a terminal, subprocesses, apps, the web, or the
> network.

Key terms: **deterministic backend**—fixed local replies with no provider;
**temporary mode**—a check whose workspace is removed; **persistent mode**—a
check that retains its workspace and any diagnostics successfully written;
**invariant**—a condition the smoke run verifies.

Use smoke immediately after installation:

```bash
story smoke
```

Temporary mode owns and initializes one uniquely named `Workspace`, runs the
check, prints one line of compact JSON, and removes the directory on every
normal return or propagated failure. It attempts bounded operational debug
records inside that workspace while running, removes any successfully written
records with the workspace, and prints no debug-log path or hint. A passing
report contains
`"status":"passed"`,
`"provider_tested":false`, unique session and debate IDs, consensus on revision
`r1`, one completed replacement round, and a disclaimer.

## What a pass proves

One run uses a single event loop and the ordinary runtime paths to:

1. initialize the workspace and persist an `EN` session directive;
2. create two active subject conversations with distinct IDs in stable catalog
   order;
3. route a lead turn and an explicitly targeted plot-specialist turn only to
   the source transcript—the lead receives empty context and the specialist
   receives the complete visible lead pair;
4. prove source/retrieval transcript isolation and that conversation work has
   not created debate, manuscript, agent-state, or core-document changes;
5. find the source once by exact subject and once by literal visible content,
   preserving match kinds and citations;
6. construct fresh catalog, event-store, runtime, backend, and memory-service
   objects, with every workspace-bound object borrowing the original
   `Workspace`, then observe the same directive, IDs, order, transcripts,
   searches, and citations;
7. run a typed, bounded `search(1) -> read(2) -> answer(3)` retrieval turn in
   the second conversation, with trusted progress and answer sources equal to
   the authorized complete-pair citations;
8. supply the source's bounded two-turn snapshot as included consensus context,
   without reading or changing CLI defaults, and verify every debate request
   plus the persisted context sidecar uses that exact snapshot; and
9. retain the original conflict/revision workflow, unanimous `r1` consensus,
   manuscript commit, exact phase/state counts, and unchanged core documents.

The exact ordered `checks` array is part of the
[CLI report contract](../reference/cli.md#smoke-report).

## Keep diagnostic artifacts

```bash
story smoke --workspace ./smoke-workspace
```

Persistent CLI mode initializes and retains one identity-bearing `Workspace`.
The CLI owns its lifetime and passes that exact instance to the smoke workflow;
the workflow and every newly constructed workspace-bound service borrow it.
Here, “fresh” means a new service object, not a reopened workspace or rebound
root identity. The CLI does not resolve the path again for handoff or
post-failure diagnostics. Every run uses unique session, conversation, debate,
manuscript, and per-agent state identifiers, so story artifacts from repeated
runs do not collide. Debug records
append to the shared bounded runtime log when storage succeeds and may rotate
under its retention limit. The report identifies the retained workspace,
session, and debate; inspect them with:

```bash
story inspect \
  --workspace ./smoke-workspace \
  --session SESSION_FROM_REPORT \
  --debate DEBATE_FROM_REPORT
```

This mode writes ordinary plaintext session data, two conversation records and
transcripts, owner-isolated agent state, debate lifecycle/log/result/context
files, and a manuscript. It also attempts fixed smoke, CLI, and runtime records
in `debug_logs/runtime.jsonl`; that file can remain absent when safe storage is
unavailable. Successfully written records contain no synthetic story content or
provider stderr. The run preserves pre-existing core-document bytes and CLI
default files, but the supplied workspace is not read-only. See
[debug-log privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

This identity continuity applies only to diagnostic storage: later records use
the originally bound root or are dropped, never redirected to a replacement.
Ordinary smoke artifact reads and writes retain the normal pathname-based
`Workspace` behavior and may observe a real directory installed at the same
root path. Do not rename or replace the workspace root during a run.

## Interpret failures

Exit code `0` means every invariant passed. Code `1` means a named invariant
failed; compact JSON includes the completed check prefix and `failed_check`,
with no stderr hint. Code `2` covers argparse usage or a setup, configuration,
or unexpected exception; non-usage exceptions print `ExceptionType: message`
to standard error. After an explicit workspace initializes successfully, a
later ordinary failure always adds exactly
`Debug log (if available, workspace-relative): debug_logs/runtime.jsonl`, even
when logging failed or the bound root was replaced and no record is available.
Initialization and argparse failures remain hint-free. Implicit temporary mode
removes its workspace on normal return or exception propagation and never
exposes a debug-log path. Diagnostic-handle acquisition and record submission
are guarded independently; neither can change the report, stderr, or exit. A
genuine smoke `KeyboardInterrupt` or `SystemExit` propagates as the same
exception with its traceback intact.

A pass does **not** validate Codex installation, credentials, provider or model
behavior, connectivity, terminal behavior, subprocess/app/web integration, or
story quality. Use it to isolate local orchestration and persistence from those
systems. Abrupt process or system termination can interrupt temporary cleanup.

The exact report and exit contract is in the [CLI reference](../reference/cli.md).
