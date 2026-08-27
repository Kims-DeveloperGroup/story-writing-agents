# Run a consensus debate

> **Availability:** prompt and complete-draft debates are available through the
> noninteractive CLI, focused-conversation TTY shell, and `StoryRuntime`.
> Inline canonical references are enforced independently for every
> participating agent and debate phase.

Key terms: **prompt**—instructions used to create revision `r0`; **draft**—a
supplied complete `r0`; **context mode**—whether focused transcript events are
included; **round limit**—maximum replacement count; **debate ID**—an immutable
attempt key.

## Choose an entry point

| Entry | Conversation context | Best for |
| --- | --- | --- |
| `story run ...` | None; established noninteractive behavior | Scripts, explicit IDs, outputs, and round limits |
| Shell `/run --output PART/PATH.md [PROMPT]` | One current-focus snapshot | Turning a discussion into a manuscript |
| Shell `/run --fresh --output PART/PATH.md PROMPT` | Focused identity only; no transcript read | Starting without prior conversation content |
| Shell `/draft-file PATH` | One current-focus snapshot | Auditing a complete local draft |

The shell forms require current attachment and focus on an active conversation.
`/run --output PART/PATH.md` without prompt text opens the multiline composer
and starts only on `/send`. Omitting `--output` first opens a transient output
prompt, but an exact part-scoped path is still required before composition or
provider work. See the
[focused-discussion guide](story-sessions.md#turn-the-current-focus-into-consensus).
On capable POSIX live editing, a whole-buffer `/` prefix previews only `/send`
then `/cancel`; the preview does not complete or run either control. Unsupported
terminals keep the same line-oriented composer without a panel.

Within `/run` prompt text or its composer, `$` completes an eligible whole-file
indicator. The second `$` immediately shows ranged-source choices; Up/Down
selects and Enter/Tab opens the independent viewer. A complete `$${path}` opens
that artifact directly on Enter/Tab. These controls never start consensus;
selection returns to the exact prompt and `/send` or the normal main-input
submission remains necessary. Fallback selection activates after the submitted
line and requires a second Enter confirmation. Noninteractive `story run` has
no picker/viewer UI and accepts literal quoted `${...}` indicators only.

## Run noninteractively

A prompt asks the execution specialist to create `r0`:

```bash
story run --prompt 'Write an opening scene.' \
  --debate-id opening_01 --output part-01/opening.md
```

A supplied draft is transformed into agent-authored `r0` before the three
audits:

```bash
story run --draft-file ./working/opening.md \
  --debate-id opening_edit_01 --output part-01/revisions/opening-r2.md
```

Exactly one positional prompt, `--prompt`, `--draft`, or `--draft-file` and one
`--output PART/PATH.md` are required. Supplied drafts are inputs, not candidate
artifacts: the execution specialist authors a complete `r0` from them before
the three audits. Draft files use the shared exact strict-UTF-8 loader.
Relative paths
follow the caller's current directory; `~`, absolute paths, symlinks, and literal
metacharacters use normal path semantics without runtime shell evaluation.
Unreadable or invalid UTF-8 files fail; empty files load exactly but are rejected
because debate input must be nonempty.

## Names, rounds, and results

If omitted, the runtime generates only the debate ID. Output has no default:
it is required as normalized `PART/PATH.md` and becomes
`manuscript/PART/PATH.md`. Existing files are never overwritten. A manuscript
revision therefore uses a distinct part-scoped path, such as
`part-01/revisions/opening-r2.md`. A reserved session/debate pair cannot be
reused, so every retry needs a new debate ID.

`--max-debate-rounds` counts complete replacements, not audits: `0` permits only
the `r0` audit, the default `1` permits `r1`, and `2` also permits `r2`.
Consensus requires all three specialists to approve the current revision.
Deadlock creates no manuscript and needs a user decision; handled failures are
also terminal.

Ask for modifications to outlines, character sheets, narrative rules, or a
manuscript in the prompt or supplied draft. The execution specialist authors
the initial candidate; after unanimous approval the lead may return full-text
updates for only the four core documents. Every successful commit still creates
exactly one manuscript at the requested path. Advisory turns do not write.

Put `${path}` or `${path}#Lx-Ly` indicators directly in prompt text to make
each debate invocation read the complete ordered batch. A one-revision
conflict flow can perform 11 independent read preludes across drafting, audits,
revision proposals, synthesis, and finalization. See
[Canonical artifact references](artifact-references.md) for a concrete
part-scoped modification scenario and failure semantics.

The noninteractive command prints `DebateResult` JSON and exits `0` for
consensus, `3` for deadlock, or `4` for failed. A referenced result adds its
content-free `reference_operation_id`; a no-reference result omits that key.
The shell instead shows each phase as one `Debate phase · DISPLAY_NAME` panel.
Active headings name the current phase without `[loading]`; successful
completion is silent, while error or cancelled state remains visible as
applicable. The canonical Phase field and the Outcome field's existing
role/state remain visible. Fixed status
fields are followed by contiguous persisted speech sections, so complete
non-error agent speech remains readable in terminal scrollback. Message details
Off is the compact default; On additionally shows phase Handoff and each
speech's Kind. Error-kind speech uses fixed redacted text, and a log-read failure
appears as a warning inside that phase panel. Successful consensus receives a
separate final `Consensus` Record after terminal progress; deadlock and failed
outcomes instead use typed error presentation. These are interactive
presentation rules only: the command's JSON, exit codes, and durable debate log
are unchanged.

Use [Consensus workflow](../design/consensus-workflow.md) for sequencing,
[CLI reference](../reference/cli.md) for exact grammar and exits, and
[Runtime API](../reference/runtime-api.md) for Python contracts.
The exact produced-output and provenance rules are in
[Canonical writing artifacts](../reference/writing-artifacts.md).
