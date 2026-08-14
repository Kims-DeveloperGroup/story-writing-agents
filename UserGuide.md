# CLI User Guide

This short page preserves links to the former single-page guide. The task guides
are now canonical; the [documentation index](docs/README.md) lists every page.

> **Availability:** the installed CLI provides `init`, `directive`, `run`,
> `inspect`, `session start|end`, and `smoke`. It does not provide a bare
> interactive `story` mode, conversation selection, transcript, or search.

## Task index

<a id="what-this-guide-covers"></a><a id="prerequisites-and-installation"></a><a id="option-a-standard-library-venv"></a><a id="option-b-conda"></a>

- Install and complete a first run: [Getting started](docs/getting-started.md)
- Create and edit project files: [Workspaces](docs/guides/workspaces.md)

<a id="built-in-smoke-test"></a><a id="codex-backend-and-provider-sessions"></a>

- Check the runtime without a provider: [Smoke test](docs/guides/smoke-test.md)
- Start, end, and safely reuse sessions: [Story sessions](docs/guides/story-sessions.md)

<a id="prompt-and-draft-commands"></a><a id="session-directives"></a>

- Run prompts or existing drafts: [Consensus runs](docs/guides/consensus-runs.md)
- Change language or persona: [Directives](docs/guides/directives.md)

<a id="inspection-statuses-and-exit-codes"></a><a id="generated-files"></a><a id="immutable-debate-ids-and-output-retries"></a>

- Inspect results and recover safely: [Inspection and recovery](docs/guides/inspection-and-recovery.md)
- Look up exact commands and files: [CLI reference](docs/reference/cli.md) and
  [session storage](docs/reference/configuration/session-storage.md)

<a id="end-to-end-story-writing-scenario"></a><a id="1-create-a-dedicated-story-workspace"></a><a id="2-seed-the-four-core-documents"></a><a id="3-set-the-session-language"></a><a id="4-request-the-opening-chapter"></a><a id="5-inspect-consensus-manuscript-and-blackboard"></a><a id="6-submit-a-user-edited-draft-as-a-new-debate"></a><a id="7-continue-a-later-chapter-in-the-same-session"></a><a id="8-recover-from-a-deadlock"></a>

- Follow the complete example: [The Glasswater scenario](docs/guides/glasswater-scenario.md)

## Troubleshooting index

<a id="troubleshooting"></a><a id="story-writing-agents-or-story-is-not-found"></a><a id="smoke-exits-with-code-1-or-2"></a><a id="backendprotocolerror"></a><a id="a-prompt-file-is-missing-invalid-or-changed-during-binding"></a><a id="no-workspace-or-session-is-selected"></a><a id="--draft-file-cannot-be-read"></a><a id="directiveerror"></a><a id="debateexistserror"></a><a id="manuscriptexistserror"></a><a id="deadlock"></a><a id="active-or-interrupted"></a>

Use [Troubleshooting](docs/guides/troubleshooting.md) for installation, provider,
workspace, input, directive, collision, and lifecycle errors.

<a id="security-and-privacy-checklist"></a>

Before supplying private material, read
[Privacy and provider exposure](docs/operations/privacy-and-provider.md),
[Filesystem safety](docs/operations/filesystem-safety.md), and
[Recovery and limitations](docs/operations/recovery-and-limitations.md).
