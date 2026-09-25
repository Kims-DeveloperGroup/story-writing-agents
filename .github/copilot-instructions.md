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
- Runtime and test changes require full CI under TEST_POLICY.md; runtime-code changes also
  need affected tests. Policy/docs/templates/skills/development-agent/index-only changes use
  static validation and diff review.
- Use `PYTHONPATH=src python -m tests.runner run` for runtime checks; retain exact IDs and JSON.
- Follow GITHUB_WORKFLOW.md for provider selection, numbered rounds, and verified closure.
  Read the current round record: Medium findings and small in-scope improvements are optional
  through round 2. From round 3, report only confirmed High/Critical defects in bugs or agreed
  requirements; exclude enhancements, preferences, speculative refactors, and minor changes.
- Give each finding severity, concrete impact, affected requirement, and supporting evidence.
  Do not relabel a preference High to bypass the threshold. Deduplicate repeated findings.
- Review the current diff without demanding another review for every push. Evidence-backed
  closure of blockers plus required verification is sufficient; a clean extra AI review is
  unnecessary. Do not expect both providers or reset rounds when head/base changes.
- Mutating workflow automation is allowed only with the `workflow:managed` label. The root
  manually squash-merges only after checking review closure, the current diff, threads, protections,
  and checks; auto-merge and merge queue are not used.
