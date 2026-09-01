# Run the provider-free smoke test

`story smoke` verifies management, catalog, and empty schema-v2 projection
wiring. It never calls a provider and never generates story content.

## Run it

```bash
story smoke
```

Without `--workspace`, Story initializes a temporary workspace, runs the
checks, prints one compact JSON line, and removes the workspace. A typical
report has this shape (IDs and path vary):

```json
{
  "checks": [
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "collaboration_history_empty",
    "collaboration_readable_context_empty",
    "collaboration_search_empty",
    "collaboration_page_empty",
    "execution_artifacts_unchanged"
  ],
  "command": "smoke",
  "conversation_id": "c_<generated>",
  "note": "No LLM or external provider was called.",
  "provider_tested": false,
  "session_id": "smoke_session_<generated>",
  "status": "passed",
  "workspace": {
    "mode": "temporary",
    "path": "<temporary-path>",
    "retained": false
  }
}
```

The actual output is one line, not pretty-printed.

## What each check proves

| Check | Meaning |
| --- | --- |
| `workspace_initialized` | Required directories and four role prompt files exist. |
| `directive_persisted` | An `EN` directive survives a fresh management runtime. |
| `conversation_catalog_persisted` | One active metadata-only conversation survives a fresh catalog object. |
| `collaboration_history_empty` | Empty schema-v2 events project to empty current state. |
| `collaboration_readable_context_empty` | Empty events produce no readable records or selected context. |
| `collaboration_search_empty` | Literal schema-v2 search returns no hit for empty history. |
| `collaboration_page_empty` | Paging empty history returns an empty stable page. |
| `execution_artifacts_unchanged` | No transcript, debate, specialist state, exchange, or manuscript was created or changed. |

This is deliberately not a fake story run. There is no deterministic story
backend, candidate, debate, or manuscript in the smoke workflow.

## Retain a workspace

```bash
story smoke --workspace ./smoke-workspace
```

Persistent mode initializes or reuses that workspace and retains the directive,
conversation catalog metadata, and any safely written bounded runtime
diagnostics. It still creates no execution artifact. The report sets
`workspace.mode` to `persistent` and `workspace.retained` to `true`.

Use the reported session ID for management inspection:

```bash
story inspect --workspace ./smoke-workspace --session SESSION_FROM_REPORT
```

The reported conversation is catalog metadata only, so an empty collaboration
history is expected.

## Failures and limits

Exit code `0` means all checks passed. Code `1` returns `status:"failed"`, the
completed check prefix, and `failed_check`. Argument/setup/unexpected failures
use the CLI's status-2 path.

A pass does not validate:

- Codex or Antigravity installation, authentication, model behavior, or network;
- interactive terminal behavior;
- provider request/response capture;
- story generation, debate, publication, or quality; or
- SQLite activation (smoke uses the filesystem management bundle).

The exact report contract is in the [CLI reference](../reference/cli.md#smoke).
