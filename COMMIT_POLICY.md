# Commit Policy

## Principle

A commit is a unit of a complete function or logic.

Each commit should represent one coherent, working change that can be
understood, reviewed, and integrated independently. A commit should make
clear what was changed and why it belongs together.

## Rules

- Keep each commit focused on one feature, fix, refactor, or documentation
  change.
- A commit must be complete and functional. Do not commit half-implemented
  logic, broken intermediate states, or changes that depend on a later commit.
- Keep unrelated formatting, cleanup, and generated-file changes separate.
- Include related tests and documentation in the same commit when they are
  part of the change.
- Prefer small commits, but do not split one logical change into artificial
  pieces.
- Run the relevant checks before committing and review the diff for unintended
  changes.

## Commit Messages

Use a concise imperative subject that describes the completed change. Start
with a verb and make the subject specific enough to be understood without
reading the entire diff.

Examples:

```text
Add chapter outline validation
Fix duplicate character assignments
Document story state transitions
```

Add a body when context is needed. Explain why the change was made and note
important implementation, compatibility, or migration details. Do not use the
body to combine unrelated changes.

## History Hygiene

Avoid vague messages such as `WIP`, `misc`, or `fix things`. Keep incomplete
work local until it forms a complete unit, or squash it before sharing the
branch. When correcting the immediately preceding commit, amend it when
appropriate; otherwise create a new, complete commit that clearly describes
the correction.
