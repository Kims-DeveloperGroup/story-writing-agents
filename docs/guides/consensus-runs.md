# Run a consensus debate

> **Availability:** prompt and complete-draft debates are available through the
> noninteractive CLI, selected-conversation TTY shell, and `StoryRuntime`.

Key terms: **prompt**—instructions used to create revision `r0`; **draft**—a
supplied complete `r0`; **context mode**—whether selected transcript events are
included; **round limit**—maximum replacement count; **debate ID**—an immutable
attempt key.

## Choose an entry point

| Entry | Conversation context | Best for |
| --- | --- | --- |
| `story run ...` | None; established noninteractive behavior | Scripts, explicit IDs, outputs, and round limits |
| Shell `/run [PROMPT]` | One selected-conversation snapshot | Turning a discussion into a manuscript |
| Shell `/run --fresh PROMPT` | Selected identity only; no transcript read | Starting without prior conversation content |
| Shell `/draft-file PATH` | One selected-conversation snapshot | Auditing a complete local draft |

The shell forms require an attached session and selected active conversation.
Bare `/run` opens the multiline composer and starts only on `/send`. See the
[selected-discussion guide](story-sessions.md#turn-a-selected-discussion-into-consensus).

## Run noninteractively

A prompt asks the execution specialist to create `r0`:

```bash
story run --prompt 'Write an opening scene.' \
  --debate-id opening_01 --output chapters/opening.md
```

A draft skips provider drafting and begins with the three audits:

```bash
story run --draft-file ./working/opening.md \
  --debate-id opening_edit_01 --output chapters/opening-edit.md
```

Exactly one positional prompt, `--prompt`, `--draft`, or `--draft-file` is
required. Draft files use the shared exact strict-UTF-8 loader. Relative paths
follow the caller's current directory; `~`, absolute paths, symlinks, and literal
metacharacters use normal path semantics without runtime shell evaluation.
Unreadable or invalid UTF-8 files fail; empty files load exactly but are rejected
because debate input must be nonempty.

## Names, rounds, and results

If omitted, the runtime generates a debate ID and writes
`manuscript/<session_id>_<debate_id>.md`. Explicit outputs must be safe relative
paths below `manuscript/`; existing files are never overwritten. A reserved
session/debate pair cannot be reused, so every retry needs a new debate ID.

`--max-debate-rounds` counts complete replacements, not audits: `0` permits only
the `r0` audit, the default `1` permits `r1`, and `2` also permits `r2`.
Consensus requires all three specialists to approve the current revision.
Deadlock creates no manuscript and needs a user decision; handled failures are
also terminal.

The noninteractive command prints the unchanged `DebateResult` JSON and exits
`0` for consensus, `3` for deadlock, or `4` for failed. The shell instead shows
safe typed progress and returns to its prompt after expected terminal outcomes.

Use [Consensus workflow](../design/consensus-workflow.md) for sequencing,
[CLI reference](../reference/cli.md) for exact grammar and exits, and
[Runtime API](../reference/runtime-api.md) for Python contracts.
