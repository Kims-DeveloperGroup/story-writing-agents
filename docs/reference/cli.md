# CLI reference

> **Availability:** bare `story` and `story chat` open the TTY shell for sessions,
> conversations, advisory turns, local reads, and selected-context consensus.

Key terms: **attachment**—this process owns a story-session lease;
**selection**—the active conversation; **safe output**—fixed typed copy.

`story` and `story-writing-agents` are exact aliases for
`story_writing_agents.cli:main`.

## Top-level syntax

| Command | Exact syntax |
| --- | --- |
| Interactive shell | `story` |
| Interactive with options | `story chat [SESSION] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Noninteractive run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS] [PROMPT | --prompt TEXT | --draft TEXT | --draft-file PATH] [--output PATH] [--debate-id ID] [--max-debate-rounds N]` |
| Inspect | `story inspect [--workspace PATH] [--session ID] [--debate ID]` |
| Session lifecycle | `story session (start | end) [ID] [--workspace PATH] [--session ID]` |
| Smoke | `story smoke [--workspace PATH]` |

<a id="interactive-streams"></a>

`argparse` handles help and syntax first. Bare entry takes no options. Both
interactive streams must be TTYs or the command exits `2` before workspace or
backend access.

## Interactive gates

Help, `/agents`, session navigation, and quit are global. Target changes,
conversation management, `/search`, and `/transcript` require only attachment.
Advisory messages and consensus also require a selected active conversation.

Ordinary lines and `/say TEXT` use the sticky target; `@AGENT TEXT` is one-shot.
Search is active-only; `/transcript ID [COUNT] [--from SEQUENCE]` may name an
active or archived record. Exact parsing is in the
[interactive grammar](interactive-command-parser.md).

<a id="interactive-consensus"></a>

## Interactive consensus

| Syntax | Input and selected-conversation behavior |
| --- | --- |
| `/run PROMPT` | Prompt; captures one bounded snapshot. |
| `/run` | Opens the run composer; only `/send` starts with a snapshot. |
| `/run --fresh PROMPT` | Prompt; records selected identity but reads/sends no transcript. |
| `/draft-file PATH` | Complete draft; captures one bounded snapshot. |

Attachment and selection are revalidated before composition or file access.
Runs may write debate records and artifacts, but never append the transcript or
change target, selection, attachment, or conversation-memory service.

The draft loader expands `~`, keeps caller-CWD relative paths, and accepts
absolute paths and symlinks with ordinary `pathlib` behavior. TTY filename
metacharacters are literal; no shell is evaluated. Exact strict-UTF-8 text,
including line endings, BOM, NUL, and whitespace, is preserved. Unreadable,
invalid-UTF-8, or empty input reports `draft_input_invalid` before reservation.

Output uses IDs, counts, fixed codes, and committed paths—never raw exceptions,
model prose, candidates, critiques, or result error/action text.

| Trusted outcome or failure | Shell code |
| --- | --- |
| Deadlock | `consensus_deadlock` |
| `preflight` | `provider_preflight_failed` |
| `protocol` | `provider_protocol_error` |
| `context` | `transcript_invalid` |
| `workspace`, including `WorkspaceSecurityError` | `workspace_invalid` |
| `artifact_conflict` | `artifact_conflict` |
| `artifact_failure` or `internal` | `consensus_failed` |
| Empty loaded draft before progress | `draft_input_invalid` |
| Nonempty selected-context validation before progress | `transcript_invalid` |
| Unexpected controller/runtime contract | fatal `internal_error` |

Handled mapped failures and deadlock return to the prompt. A workspace error
does so only after proving the same session is active with an `OWNED` lease;
failed proof instead exits through `cleanup_failed`.

General `/compose`, `/history`, and standalone artifact commands remain
unavailable. The Python runtime cancellation contract is documented separately;
this page does not define active-run signal handling.

## Workspace, session, and noninteractive run

Except for `init` and `smoke`, commands need an initialized workspace; omission
of `--workspace` selects the nearest ancestor. Session precedence is
`--session`, lifecycle positional ID, then saved selection. Successful start or
shell acquisition reselects; failure and end preserve selection.

Noninteractive `story run` requires one input, uses no conversation context, and
keeps established JSON/stderr behavior. Its draft loader is shared, though the
invoking shell still needs quoting. `--backend` repeats, timeout is positive,
and rounds are nonnegative. Omitted IDs/outputs are generated. Debate inspection
is unchanged; context sidecars use a separate Python accessor.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Command succeeded, shell ended normally, run reached consensus, or smoke passed. |
| `1` | Smoke completed but an invariant failed. |
| `2` | Usage, TTY/setup, fatal shell/cleanup, or another caught failure. |
| `3` | Noninteractive run returned `deadlock`. |
| `4` | Noninteractive run returned `failed`. |

See [Consensus runs](../guides/consensus-runs.md) for task guidance and
[Terminal safety](../operations/terminal-safety.md) for display controls.
