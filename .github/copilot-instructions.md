# Repository instructions for GitHub Copilot

Follow `AGENTS.md`, `GITHUB_WORKFLOW.md`, `COMMIT_POLICY.md`, and `DEPENDENCY_POLICY.md`.
Preserve unrelated changes and the visible headings and meaning of Issue, PR, evidence, and
status-comment templates.

## Code Review Rules

- Review the current PR head SHA and identify stale evidence.
- Prioritize correctness, security, data loss, compatibility, public API, dependency direction,
  lifecycle ownership, concurrency, and missing runtime coverage.
- Label a merge-blocking finding **High** only when it can materially break required behavior,
  safety, security, data integrity, or compatibility. Give the file/location, concrete impact,
  and smallest safe fix.
- Do not block on style, preference, speculative refactoring, or unrelated cleanup. Mark these
  nonblocking.
- Runtime tests are required only when runtime code changes. Policy, docs, templates, skills,
  development-agent configuration, module-index-only, and test-only changes need static checks
  and diff review instead.
- Treat `PYTHONPATH=src python -m tests.runner run` as the canonical runtime runner. Preserve exact
  relevant module/ID selections and JSON evidence; retain shards and reconciliation for full runs.
- Review is milestone-based: one request when ready and one final request after material fixes.
  Do not request or expect re-review after every push.
- Mutating workflow automation is allowed only with the `workflow:managed` label. The root
  manually squash-merges only after checking current-base/head reviews, threads, protections,
  and checks; auto-merge and merge queue are not used.
