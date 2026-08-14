# Privacy and provider exposure

> **Availability:** the controls and storage behavior here describe the current
> implementation. They reduce exposure but do not make Codex, its executable,
> or the local workspace a security boundary.

Key terms: **plaintext**—unencrypted local content; **provider transcript**—a
conversation retained outside this workspace; **visible output**—provider text
the runtime may log; **credential**—a secret used to authenticate provider access.

## Local data is plaintext

The runtime provides no encryption, redaction, access-control manager, secure
erase, or automatic retention policy. Role prompts, configuration, persona
templates, core documents, runtime overrides, CLI selection, provider metadata,
agent outcomes, blackboard logs, lifecycle and result state, conversation
subjects, and manuscripts are ordinary local files governed by operating-system
permissions.

The public blackboard includes original user prompt or draft, complete
candidates, approvals, critiques, proposals, visible errors, finalization
metadata, and committed path. CLI run output includes that message history.
Treat `discussion_logs/` as sensitively as source material and manuscripts.

Owner-isolated agent state is narrower: the runtime does not deliberately copy
the full input, candidate, or core documents. It retains generic outcomes,
updated filenames, and that agent's visible notes, critiques, and proposals,
with a 4096-character entry cap. Provider output is not scrubbed, so a provider
can echo sensitive source content into an outcome. Session-wide history has no
compaction, count, expiry, or retention limit and is sent again in later calls
to that owner.

Conversation metadata contains no transcript, but the exact display subject
can disclose content. Its normalized comparison key is not stored. Archiving
changes discovery status; it does not delete or redact the record. CLI context
contains only version and selected ID, but the ID itself may be sensitive.

## Provider boundary

Codex is launched without shell evaluation. The runtime asks for a read-only
sandbox, no approvals, ignored user configuration and project rules, and
disabled shell, web-search, app, and multi-agent tools. It stages verified role,
persona, language, and allowlisted core content in an isolated temporary
top-level `AGENTS.md`; per-turn standard input holds dynamic request JSON.

SHA-256 detects file changes but does not encrypt, hide, authorize, or redact
content. Isolated staging and empty project-root markers prevent ordinary
repository instruction discovery, but installation-level or vendor-global
instructions can still apply. Review the installed CLI and account before
processing private material.

Codex may store resumable conversations according to vendor and account
behavior. The runtime persists one thread ID per session and agent. Session
end, detach, and backend close retain those references and do not delete
external transcripts. A changed agent-definition fingerprint starts a new
thread; it does not erase the old provider conversation.

## Credentials and visible reasoning

Use `codex login` or process-environment `CODEX_API_KEY`. Never place passwords,
API tokens, private keys, or credentials in prompts, drafts, role prompts,
configuration, personas, core documents, replies, logs, agent state,
manuscripts, or filenames.

Public models have no hidden-chain-of-thought field, and runtime tasks ask for
only the structured result. This is a provider contract, not semantic
filtering. If a backend puts hidden reasoning or secrets in ordinary `content`,
the runtime treats it as visible and may persist it.

`story smoke` uses fixed synthetic content and no provider. Persistent smoke
mode still writes ordinary plaintext diagnostics. A pass says nothing about
provider privacy, authentication, network, model, or real story content.
