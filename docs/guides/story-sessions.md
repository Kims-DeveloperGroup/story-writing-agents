# Story sessions

> **Availability:** `story session start|end` is implemented. Strict create,
> resume, attach, and detach operations are Python APIs only; an interactive
> switch controller and session-list CLI are not implemented.

Key terms: **active**—durable permission for provider work; **selected**—the
CLI's saved default session; **lease**—one backend's temporary attachment;
**provider thread**—a resumable agent conversation reference.

A story session groups runtime overrides, agent history, and four resumable
provider threads. Its three status dimensions are independent: `active` allows
new provider work, CLI `selected` supplies a default session name, and a
temporary `lease` gives one backend process exclusive use of the threads.

## A complete lifecycle

```bash
story init ./session-demo
cd ./session-demo
story session start chapter_01
story run \
  --debate-id chapter_01_draft \
  --output chapter-01.md \
  'Draft chapter one from the core documents.'
story session end
story session start
story inspect --debate chapter_01_draft
story session end
```

The first start creates or reactivates `chapter_01`, then atomically saves it
as this workspace's selected CLI session. The run attaches to the active
provider session, uses its compatible per-agent thread IDs, and releases the
lease when the backend closes. Ending writes `active: false` but deliberately
keeps thread metadata and CLI selection. The bare later `session start`
resolves that selection and reactivates the same session.

Choose a new session ID when every agent should start with a separate session
history. Reusing an ID may resume a compatible provider thread; editing a role
prompt, persona, language, or allowlisted core document changes the agent
definition fingerprint and starts a fresh thread for that agent.

## Explicit session resolution

For automation, name both workspace and session:

```bash
story run \
  --workspace ./session-demo \
  --session chapter_01 \
  --draft 'A complete candidate.'
```

An explicit `--session` wins over saved selection. Lifecycle commands also
accept one positional session name, but supplying both forms is an error. If no
form resolves, the CLI asks you to start or name a session. Only a successful
`session start` changes selection; run, inspect, directive, failed start, and
session end do not.

## Contention and safe handling

Two cooperating processes cannot attach the same session simultaneously. A
lease that reports another or uncertain owner blocks the second process; stop
the known owner normally and do not manually edit lease files or the persistent
`.lease-claims.lock` guard. The exact state classifications, platform rules,
and reclamation contract live in
[Backend and sessions](../reference/backend-and-sessions.md).

Ending a session does not delete provider transcripts, runtime overrides,
agent state, debates, manuscripts, or selected-session state. `close()` and
detach release attachment without ending the durable session.

Python lifecycle method contracts and lease states are in
[Backend and sessions](../reference/backend-and-sessions.md); ordering and race
handling are in [Session lifecycle design](../design/session-lifecycle.md).
