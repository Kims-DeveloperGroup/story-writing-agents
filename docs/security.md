# Security, Privacy, and Limitations

This compatibility hub preserves former security fragments. Read the focused
operations pages before supplying sensitive material or recovering interrupted
work.

> **Availability:** the safeguards described in the linked pages are current
> runtime behavior, not a claim that the workspace or provider is trusted.

<a id="filesystem-boundary"></a><a id="artifact-mutation-guarantees"></a>

- [Filesystem containment, symlinks, atomic files, and rollback](operations/filesystem-safety.md)

<a id="local-plaintext-data"></a><a id="hidden-reasoning"></a><a id="codex-provider-boundary"></a>

- [Plaintext retention, credentials, visible outputs, and provider exposure](operations/privacy-and-provider.md)

<a id="built-in-smoke-command"></a>

- [Provider-free smoke test and retained diagnostics](guides/smoke-test.md)

<a id="provider-session-attachment-and-lease-liveness"></a><a id="debate-ownership-and-recovery"></a><a id="operational-limitations"></a>

- [Recovery and current limitations](operations/recovery-and-limitations.md)
- [Session lease design](design/session-lifecycle.md)

Exact path and persistence schemas are in
[Session storage](reference/configuration/session-storage.md); exported error
types are in [Identifiers and errors](reference/configuration/identifiers-and-errors.md).
