# The Glasswater interactive journey
> **Availability:** every command is implemented. Advisory/consensus uses Codex;
> management, history, search, transcript reads, and `story smoke` do not.

This journey uses *The Bell Beneath Glasswater*. Copy each generated conversation handle; replace `A_HANDLE` rather than typing it literally.

## Prepare the project

```bash
story init ./glasswater-workspace
cd ./glasswater-workspace
story smoke
story
```

Before entering the shell, edit the four `core_documents/` files: Mara finds a bell communicating through salt and vibration; Tovin understands harbor acoustics but avoids deep water; prose stays close-third past; and each chapter ends with one physical discovery.

## Create a session and focus subject A

```text
/session create glasswater_novel
/status
/language EN
/conversation new Bell continuity
```

`/status` distinguishes ready durable state, access held here, defaults, current attachment and focus, `lead` agent focus, provider readiness, and next action. Copy the handle as `A_HANDLE`, then route turns:

```text
Summarize the rule that governs how the bell communicates.
@plot Where should the council-vessel mark first appear?
/to character
How should Mara react without risking another diver?
```

`@plot` is one-shot; `/to character` changes agent focus for later ordinary text.

## Create subject B and inspect memory

```text
/conversation new Council-vessel clue
Track what Tovin knows about the council-vessel mark.
/history
/search bell communicates
/transcript A_HANDLE 6
```

The new conversation is default/current focus; agent focus stays `character`. `/history` reads B. Search covers active conversations; transcript reads A without changing focus. All are provider-free.

```text
Which earlier conversation constrains whether the bell can speak? Cite sources if you retrieve it.
/conversation resume A_HANDLE
/history 6
/to lead
```

Only chosen bounded retrieval shows `[context]` search/read progress and sources.

## Switch sessions and return

```text
/session create glasswater_notes
/conversation new Loose research
/session resume glasswater_novel
/status
/history 6
```

Creating `glasswater_notes` switches after acquiring it. Resuming ready
`glasswater_novel` reports the `opened` result, makes it default/current,
restores active `A_HANDLE`, and resets agent focus to `lead`. The source remains
ready but claimable. You may omit the session name or conversation ID: capable
main input shows ordered choices before Enter. Highlighted Enter submits an
explicit identity directly. In this scenario, submitting the omitted command
performs a fresh successful read with eligible choices, so it opens the generic
picker, live on a capable terminal and numbered in ordinary-line fallback.

## Run consensus, quit, and restart

```text
/run
Draft Chapter 1 from the core documents and the focused Bell continuity discussion.
End with the first unexplained salt pattern.
/send
/run --fresh Draft a separate harbor opening without conversation history.
/inspect last
/quit
```

Included mode gives every phase one bounded A snapshot. Fresh mode records A's
identity without its transcript. Quit releases access without pausing; defaults
remain. Restart to resume the ready default and restore A:

```bash
story
```

```text
/status
/history 6
/quit
```

## Use one explicit automation call, then pause

`story run` uses an explicit/default session, never conversation focus:

```bash
story run --workspace . --session glasswater_novel \
  --prompt 'Draft a council-vessel transition from core documents.' \
  --debate-id glasswater_automation_01 --output glasswater/automation-01.md
story session pause glasswater_novel --workspace .
```

Pause prints final `paused` plus `claimable` JSON and retains the default. Bare
`story` asks to resume it; accepting makes it ready, acquires access, reports
`reopened`, and restores the active default conversation:

```bash
story
```

```text
y
/status
/session pause
y
/quit
```

The final pause clears process scope but preserves defaults and provider threads. See [Story sessions](story-sessions.md) and [Recovery](../operations/recovery-and-limitations.md).
