# Run a consensus debate

> **Availability:** prompt and complete-draft debates are implemented through
> `story run` and `StoryRuntime`; the interactive shell does not dispatch `/run`.

Key terms: **prompt**—instructions from which an agent creates `r0`; **draft**—
a complete supplied `r0`; **debate ID**—the immutable attempt key; **round
limit**—the maximum replacement count; **manuscript**—the consensus output file.

Start an active provider session, then choose one input kind. A prompt asks the
execution specialist to create revision `r0`:

```bash
story run \
  --prompt 'Write an opening scene from the core documents.' \
  --debate-id opening_01 \
  --output chapters/opening.md
```

A complete draft skips provider drafting and audits the supplied text as `r0`:

```bash
story run \
  --draft-file ./working/opening.md \
  --debate-id opening_edit_01 \
  --output chapters/opening-edit.md
```

Exactly one of a positional prompt, `--prompt`, `--draft`, or `--draft-file`
is required. A relative draft-file path is resolved from the shell's current
directory, not the workspace, and must decode as strict UTF-8. Inline values
are passed as content without shell or nested-command parsing by the runtime.

## Pick durable names

If omitted, the runtime generates a debate ID and uses
`<session_id>_<debate_id>.md` under `manuscript/`. Explicit names make
automation and inspection clearer. Output is always a safe relative path
beneath `manuscript/`; absolute paths, traversal, and symlink components are
rejected.

A session/debate pair is reserved before the first backend call and can never
be reused. Existing manuscripts are never overwritten. For a retry after any
failure, use a new debate ID; after an output collision, also use a new output
path.

## Understand rounds and results

The three specialists audit the same revision. Conflicting specialists propose
repairs, the lead synthesizes one complete replacement, and all three audit it
again. `--max-debate-rounds` counts replacements, not audits:

- `0` allows the `r0` audit but no replacement;
- the default `1` permits `r1` and its audit;
- `2` additionally permits `r2` and its audit.

Consensus requires three approvals on the current revision. A deadlock returns
an `action_required` user ruling and creates no manuscript. A handled provider,
protocol, finalization, or artifact error returns `failed`. Only consensus
populates `final_candidate` and `manuscript_path`; deadlock and most failures
retain `latest_candidate` for diagnosis.

After unanimous approval, the lead may return full-text replacements for the
four allowlisted core documents. The runtime validates the entire update and
output set, writes core files first, and creates the exact approved manuscript
last. Rollback across files is best effort, so inspect after infrastructure
failure.

`--backend AGENT=BACKEND` may be repeated for a one-run route override;
`--codex-model` selects a model and `--codex-timeout` sets a positive per-call
timeout. Codex is implemented. Selecting the reserved `gemini` value fails
before debate execution. There is no automatic retry.

Inspect outcomes with [Inspection and recovery](inspection-and-recovery.md).
The complete option and exit contract lives in the [CLI reference](../reference/cli.md).
