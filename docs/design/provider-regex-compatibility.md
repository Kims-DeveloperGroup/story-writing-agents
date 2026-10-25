# Native schema regex compatibility

This boundary addresses the Codex JSON-schema rejection reported in
[Issue #881](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/881).
The owning Issue and PR retain verification evidence.

Provider-facing built-in patterns must preserve each field's accepted values.
Text and SEARCH rules keep their distinct whitespace, Unicode, NUL and surrogate
restrictions. Specialist and canonical artifact paths retain their separate
component, domain, length, suffix and trailing-newline rules. Replacing regex
syntax does not merge these languages or grant workflow authority.

The canonical strict-end token is `\z`. The shared evaluator translates that
token to Python 3.11's equivalent `\Z` without rewriting literal escapes or
character classes. Existing immutable diagnostic templates prepare matchers once
and reuse them, retaining source guards, independent exports and per-query caps.
No provider calls, call-budget changes or new owner construction are needed.

Only source-proven built-in intersections receive equivalent consuming rules.
An unproved custom conjunction keeps its original host meaning. It must not be
weakened, stripped or rejected by a new host allowlist.

No adapter-level or per-invocation schema syntax scan is added. Compatibility
comes from the field declarations and verification of their native output.
Existing payload validation, provider launch paths, accounting and cleanup remain
unchanged. This hotfix does not provide a general compatibility checker for
arbitrary custom regexes.

Python compilation alone does not establish Codex acceptance. Native checks must
record actual source, CLI/model/settings and outcome. An incomplete response
with reason `max_output_tokens` alone does not establish a schema-compilation
failure or justify changes to the workflow or provider protocol.
