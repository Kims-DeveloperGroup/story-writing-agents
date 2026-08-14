# Run the provider-free smoke test

> **Availability:** `story smoke` is implemented and uses the ordinary runtime
> with a private deterministic backend. It never invokes Codex or the network.

Key terms: **deterministic backend**—fixed local replies with no provider;
**temporary mode**—a check whose workspace is removed; **persistent mode**—a
check that retains diagnostics; **invariant**—a condition the smoke run verifies.

Use smoke immediately after installation:

```bash
story smoke
```

Temporary mode creates a uniquely named workspace, runs the check, prints one
line of compact JSON, and removes the directory before normal return. A passing
report contains `"status":"passed"`, `"provider_tested":false`, unique
session and debate IDs, consensus on revision `r1`, one completed replacement
round, and a disclaimer.

The check exercises the same `StoryRuntime` path as a normal debate:

1. initialize the workspace and persist an `EN` session directive;
2. draft synthetic revision `r0` from a prompt;
3. run three parallel audits, with one deterministic plot conflict;
4. record the plot proposal and synthesize `r1`;
5. run three unanimous audits, finalize, and commit a manuscript;
6. re-inspect consensus and verify all four core documents are unchanged.

Its six named checks are `workspace_initialized`, `directive_persisted`,
`revision_workflow`, `consensus_persisted`, `manuscript_committed`, and
`core_documents_unchanged`.

## Keep diagnostic artifacts

```bash
story smoke --workspace ./smoke-workspace
```

Persistent mode initializes missing scaffold or reuses the directory. Each run
uses unique synthetic session, debate, and manuscript names, so it does not
overwrite an earlier smoke run. The report identifies the retained workspace,
session, and debate; inspect them with:

```bash
story inspect \
  --workspace ./smoke-workspace \
  --session SESSION_FROM_REPORT \
  --debate DEBATE_FROM_REPORT
```

This mode writes ordinary plaintext session overrides, owner-isolated agent
state, blackboard and lifecycle files, a result, and a manuscript. It preserves
existing files and verifies pre-existing core-document bytes, but it is not a
read-only check of the rest of the supplied workspace.

## Interpret failures

Exit code `0` means all six invariants passed. Code `1` means the workflow
completed but an invariant failed; failed compact JSON includes
`failed_check`. Code `2` is normal CLI usage, workspace setup, existing
configuration, or unexpected-error handling and prints
`ExceptionType: message` to standard error.

A pass does **not** test an LLM, Codex installation, account authentication,
model, provider protocol, connectivity, or story quality. Use it to isolate
local orchestration and persistence from provider problems. An abrupt process
or system termination can interrupt temporary-directory cleanup.

The exact report and exit contract is in the [CLI reference](../reference/cli.md).
