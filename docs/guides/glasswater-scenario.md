# Management-only workspace journey

This former story-execution walkthrough now demonstrates only the capabilities
available in the current integration stage.

## Prepare and verify

```bash
story init ./example-workspace
cd ./example-workspace
story smoke
story
```

## Create local management scope

```text
/session create example_novel
/override language EN
/conversation new Opening structure
/status
/conversation list
/history
/search opening
```

The session and conversation metadata persist. History and event search are
empty because creating catalog metadata does not invent collaboration events.

## Switch and return

```text
/session create research_notes
/conversation new Source questions
/session resume example_novel
/conversation list
/status
```

The destination is acquired before the source is released. Returning to
`example_novel` restores its saved active conversation when possible.

## Observe the execution boundary

```text
Please ask the specialists to review the opening.
```

The shell responds with the fixed unavailable guidance and performs no
provider, history, reference, or artifact work. Use `/help` to see the retained
management and inspection commands.

## Release or pause

```text
/quit
```

Quit releases process access while leaving the session ready. To durably pause
instead, reopen the shell, attach the session, use `/session pause`, and confirm
the action. The noninteractive equivalent is:

```bash
story session pause example_novel --workspace .
```

See [Story sessions](story-sessions.md) and
[Lead agent status](lead-agent.md).
