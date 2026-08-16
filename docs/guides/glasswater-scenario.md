# The Glasswater interactive journey

> **Availability:** every command is implemented. Advisory/consensus uses Codex;
> management, history, search, transcript reads, and `story smoke` do not.

This is one sequential journey through *The Bell Beneath Glasswater*. Copy each
generated handle; replace `A_HANDLE` below rather than typing it literally.

## Prepare the project

```bash
story init ./glasswater-workspace
cd ./glasswater-workspace
```

Edit the four `core_documents/` files with these durable facts:

- `global_outline.md`: Mara finds a bell that communicates only through salt patterns and metal vibrations.
- `part_outlines.md`: Chapter 1 finds it; Chapter 2 takes a rubbing to Tovin; a council-vessel mark links to Mara's missing brother.
- `character_sheets.md`: Mara risks her job, never another diver; Tovin knows harbor acoustics but avoids deep water.
- `narrative_rules.md`: close third past from Mara, understated maritime mood, physical revelation, and one concrete discovery per chapter.

Verify local orchestration, then open the human workflow:

```bash
story smoke
story
```

Smoke uses a temporary synthetic workspace. Bare `story` opens this workspace's lobby.

## Attach a session and discuss subject A

```text
/session new glasswater_novel
/language EN
/conversation new Bell continuity
```

Copy the handle after `Created and selected` as `A_HANDLE`. The default sticky target is `lead`:

```text
Summarize the rule that governs how the bell communicates.
@plot Where should the council-vessel mark first appear?
/to character
How should Mara react without risking another diver?
```

`@plot` is one-shot; `/to character` makes the final ordinary line use the character custodian.

## Create subject B and inspect memory

```text
/conversation new Council-vessel clue
Track what Tovin knows about the council-vessel mark.
/history
/search bell communicates
/transcript A_HANDLE 6
```

The new conversation is selected while the sticky target stays. `/history` locally reads B plus context accounting. `/search` returns bounded active-session hits; `/transcript` reads a fresh bounded A page without selecting it. All three are provider-free.

```text
Which earlier conversation constrains whether the bell can speak? Cite sources if you retrieve it.
```

The agent may answer directly. Only chosen bounded retrieval shows `[context]`
search/read progress and sources; it differs from explicit local reads above.

```text
/conversation use A_HANDLE
/history 6
/to lead
```

## Run consensus with and without A

```text
/run
Draft Chapter 1 from the core documents and selected Bell continuity discussion.
End with the first unexplained salt pattern.
/send
```

The runtime gives one bounded A snapshot to every phase. Main-prompt advisory
input stays single-line; standalone `/compose`, `/send`, and `/cancel` are
unavailable. Only inside bare `/run` does `/send` submit or `/cancel` discard.

```text
/run --fresh Draft a separate harbor opening without conversation history.
/inspect last
/quit
```

Fresh mode records selected A's identity but sends none of its transcript. `/inspect last` reports this process's latest accepted run; `/quit` releases the lease but keeps session and selections.

## Use one explicit automation call, then end

```bash
story run --workspace . --session glasswater_novel \
  --prompt 'Draft a council-vessel transition from core documents.' \
  --debate-id glasswater_automation_01 --output glasswater/automation-01.md
```

Noninteractive `story run` never reads selected conversation context. Re-enter the active saved session, verify restored A, then end it:

```bash
story
```

```text
/status
/history 6
/session end
y
/quit
```

Ending deactivates the provider session but retains selections; later resume starts a new lease.
