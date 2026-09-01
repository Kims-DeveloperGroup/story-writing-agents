# Getting started

This revision prepares and inspects writing workspaces. It does not yet send
story work to the lead agent or specialists.

## 1. Install

Use Python 3.11 or later in one virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
rehash
story --help
```

`story` and `story-writing-agents` are exact aliases. A normal install includes
the read-only exchange viewer. Optional local RAG requires the reviewed `rag`
extra before enablement.

The shell still checks configured Codex and Antigravity authentication before
constructing its controller. This prepares provider configuration; accepted
management and inspection commands do not send story content to a provider.

## 2. Run the provider-free check

```bash
story smoke
```

Success is compact JSON with `"status":"passed"` and
`"provider_tested":false`. It verifies workspace initialization, one persisted
directive, one conversation-catalog record, empty schema-v2 history/search/page
projections, and unchanged execution artifacts. It does not generate a story,
create a transcript or debate, or validate a provider.

## 3. Initialize a workspace

```bash
story init ./first-story
cd ./first-story
```

Initialization creates the canonical directories, configuration, and four role
prompt files without creating story documents. Existing files are preserved
unless the explicit prompt-overwrite option is used.

## 4. Manage a session and conversation

```bash
story
```

Then enter:

```text
/session create first_novel
/conversation new Opening structure
/status
/conversation list
/history
/search opening
/quit
```

Plain rendering has this shape (the workspace and conversation ID vary):

```text
story[lobby]> /session create first_novel
[session]
  Workspace: <resolved-workspace>
  Session: first_novel (created)
story[first_novel]> /conversation new Opening structure
[conversation] Created and focused c_<generated> | Subject: Opening structure
story[first_novel]> /history
[context] [empty]
  History displayed: 0/0 records
  Last sequence: 0
  Current state: empty
  User meaning: No collaboration event has been recorded yet.
  Next safe action: none
  Next action meaning: No history-derived action is pending.
```

Capable-terminal decoration varies; the semantic fields and empty-history
meaning do not.

You can also use `/conversation resume [ID]`, `/conversation subject SUBJECT`,
`/conversation archive [ID]`, `/transcript ID [COUNT] [--from SEQUENCE]`,
`/inspect [DEBATE|last]`, `/exchanges`, and `/status`. `/help` shows the exact
installed registry.

## 5. Understand the execution boundary

Submitting an ordinary story instruction or a retired execution command does
not call an agent:

```text
story[first_novel]> Please critique the outline.
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The same guidance applies to routed text and every removed direct-execution
form. Rejection occurs before provider binding, transcript append, RAG,
artifact-reference work, progress, or artifact mutation.

There is no noninteractive story-execution command and no public
`StoryRuntime` method for an advisory turn or debate. Do not build automation
around an earlier direct path. The selected filesystem or SQLite bundle now
uses the sole schema-v2 collaboration event store and provider-free inspection
service. Open #452 later owns lead proposal/confirmation writes, #453 owns
specialist execution/result writes, and #454 owns whole-workspace rejection of
unsupported history.

## 6. Continue

- [Story sessions and conversations](guides/story-sessions.md)
- [Lead agent and collaboration status](guides/lead-agent.md)
- [Provider-free smoke](guides/smoke-test.md)
- [CLI reference](reference/cli.md)
- [Runtime API](reference/runtime-api.md)
