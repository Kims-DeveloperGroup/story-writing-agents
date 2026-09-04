# Run the provider-free smoke test

`story smoke` verifies local workspace and schema-v2 management/storage wiring.
It never calls a provider and never generates story content. Its behavior
depends on whether you supply a workspace.

## Disposable full smoke

```bash
story smoke
```

Story creates a temporary directory, invokes the real strict v2 initializer,
runs the provider-free checks, prints one compact JSON line, and removes the
complete directory. A typical report has this shape (IDs and path vary):

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

The actual output is one line with sorted keys. Because this path uses the real
initializer, a missing or invalid effective Git author/committer is reported.
The temporary directory is still removed; smoke never invents identity or
falls back to a weaker workspace.

### What each disposable check proves

| Check | Meaning |
| --- | --- |
| `workspace_initialized` | The exact v2 scaffold and clean one-commit Git baseline are ready. |
| `directive_persisted` | An `EN` directive survives a fresh management runtime inside the disposable workspace. |
| `conversation_catalog_persisted` | One active metadata-only conversation survives a fresh catalog object. |
| `collaboration_history_empty` | Empty schema-v2 events project to empty current state. |
| `collaboration_readable_context_empty` | Empty events produce no readable records or selected context. |
| `collaboration_search_empty` | Literal schema-v2 search returns no hit for empty history. |
| `collaboration_page_empty` | Paging empty history returns an empty stable page. |
| `execution_artifacts_unchanged` | No transcript, debate, specialist state, exchange, or manuscript was created or changed. |

This is deliberately not a fake story run. There is no deterministic story
backend, candidate, debate, or manuscript.

## Read-only explicit verification

```bash
story smoke --workspace ./my-story
```

The explicit form does not initialize or repair the supplied path. It requires
an already ready, exact, clean v2 initialization root and constructs a fresh
workspace verifier. Success has this shape:

```json
{
  "checks": [
    "workspace_initialized",
    "execution_artifacts_unchanged"
  ],
  "command": "smoke",
  "conversation_id": null,
  "note": "No LLM or external provider was called.",
  "provider_tested": false,
  "session_id": null,
  "status": "passed",
  "workspace": {
    "mode": "persistent",
    "path": "/absolute/path/to/my-story",
    "retained": true
  }
}
```

It creates no directive, conversation, history, agent state, runtime log,
provider state, manuscript, or other residue. `retained:true` means only that
the caller-owned directory remains; it does not mean smoke wrote retained
diagnostics. Capture the one-line stdout yourself if audit evidence is needed.

## Failures and limits

Exit code `0` means the applicable checks passed. A disposable invariant
failure returns `status:"failed"`, the completed check prefix, `failed_check`,
and code `1`. Argument, initialization, identity, discovery, or unexpected
failures use the CLI status-2 error path.

Explicit smoke rejects a missing, interrupted, unsupported, arbitrary, dirty,
drifted, or unsafe root without changing it. It is not a recovery mechanism;
follow [workspace initialization recovery](../operations/recovery-and-limitations.md#workspace-initialization-recovery).

A pass does not validate:

- Codex or Antigravity installation, authentication, model behavior, or network;
- interactive terminal behavior;
- provider request/response capture;
- story generation, debate, publication, or quality;
- session Git-resource provisioning, interruption/resume, or later #505
  commits/checkpoints; or
- SQLite activation (disposable smoke uses the filesystem management bundle).

Explicit `story smoke --workspace PATH` does perform fresh workspace readiness,
so any already-present session branch/worktree/internal-ref inventory must be
exactly correlated. Smoke does not create, resume, repair, or remove those
resources and current `story session` commands are not connected to them until
#455.

The exact report contract is in the [CLI reference](../reference/cli.md#smoke).
