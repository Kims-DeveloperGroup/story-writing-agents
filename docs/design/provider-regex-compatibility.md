# Native schema regex compatibility

This boundary addresses the Codex JSON-schema rejection reported in
[Issue #881](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/881).
The owning Issue and PR retain verification evidence.

Provider-facing built-in patterns preserve each field's accepted values. Text search
rules keep their distinct whitespace, Unicode, NUL and surrogate restrictions.
Specialist and canonical artifact paths retain separate component, domain, length,
suffix and trailing-newline rules. Replacing regex syntax does not grant workflow
authority.

The canonical strict-end token is `\z`. The shared evaluator translates it to Python
3.11's equivalent `\Z` without rewriting literal escapes or character classes.
Existing immutable diagnostic templates prepare matchers once and reuse them, retaining
source guards, independent exports and per-query caps. Only source-proven built-in
intersections receive equivalent consuming rules. An unproved custom conjunction keeps
its original host meaning.

No adapter-level or per-invocation schema syntax scan is added. Existing payload
validation, provider launch paths, accounting and cleanup remain unchanged.

## Development guardrail

The stdlib-only `tests/test_provider_schema_spec.py` harness checks final projected output
against the [OpenAI Structured Outputs supported schemas](https://developers.openai.com/api/docs/guides/structured-outputs).
It checks object closure and required fields, supported schema constructs, references,
published limits and an explicitly supported repository regex grammar. Unknown pattern
syntax fails the development check and requires review. Codex's actual lookaround
rejection is additional observed compatibility evidence; the public guide does not
specify the complete native regex dialect.

Run it on a captured final schema:

```sh
PYTHONPATH=src python -m tests.test_provider_schema_spec /absolute/path/final-schema.json
```

The JSON report names findings and whether the static profile passed. It never claims
native admission. Existing canonical tests apply the checker to representative exported
schemas, including shared definitions. Production code does not import it. Test inventory,
per-test subtest caps and all helper source remain governed by
[TEST_POLICY.md](../../TEST_POLICY.md).

Any change to schema producers, projection or pattern syntax also requires an actual
local call through the configured provider with the final runtime-generated schema.
Record the source revision, CLI/model/settings, schema hash, native response or error,
typed outcome, attempted calls and owned-process cleanup. Check both original #881
inputs for this hotfix; later changes must exercise their affected schema-producing
paths. Keep credentials and paid calls outside ordinary CI.

Python compilation and an offline pass cannot establish provider acceptance. A timeout,
refusal, incomplete result or invalid schema remains a named verification gap.
An incomplete response with reason `max_output_tokens` alone does not establish a
schema-compilation failure or justify changes to workflow or provider protocol.
