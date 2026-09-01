# Canonical artifact references

Artifact reference indicators tell the Python lead which canonical source it
must read. They contain a locator, not file content.

## Indicator format

An indicator contains only a canonical path, optionally followed by one-based
inclusive line bounds:

```text
${core_documents/global_outline.md}
${manuscript/part-01/scenes/opening.md}#L12-L24
```

It never embeds the file body. Multiple indicators can appear in one lead input
and preserve their order.

Canonical reference sources are:

- the supported files below `core_documents/`; and
- every eligible lowercase-`.md` file at any depth below `manuscript/`.

Manuscripts are grouped under part-dedicated directories. Publication paths
and reference-source paths are related but separate validation boundaries; see
[Canonical writing artifacts](../reference/writing-artifacts.md).

## Python lead behavior

Pass indicators inside `StoryRuntime.lead_turn()` text. The runtime parses every
active reference, creates a content-free operation record, requires one exact
ordered provider read action, securely reads the current whole file or range,
and attaches only those selected bytes to the substantive call as untrusted
mandatory context. It revalidates the source before accepting the response.
Another agent's read, a checkpoint, or SEARCH/READ evidence cannot satisfy this
obligation.

For example:

```python
result = await runtime.lead_turn(
    "novel",
    "conversation_a",
    "Check ${core_documents/narrative_rules.md} and "
    "${manuscript/part-01/scenes/opening.md}#L12-L24, then advise me.",
)
assert result.message
```

The repository records content-free operation evidence, but
`LeadTurnResult` does not expose an operation ID or selected bytes. A proposal
may disclose only the canonical input locator, range, state, and revision
digest.

## Shell behavior

Issue #478 removed the provider-bound shell input span. Therefore:

- `$` does not open an artifact picker in the current main prompt;
- `$$` does not open the ranged-source picker/viewer workflow;
- typing a completed indicator does not trigger a read;
- ordinary or routed input is rejected before reference parsing; and
- no `reference_operation_id` is created by the installed CLI.

For example:

```text
story[novel]> Review ${core_documents/global_outline.md}.
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The file is not opened and its bytes are not sent anywhere.

## Enforcement boundary

The Python lead performs the #452 read. #453 must repeat enforcement for every
later addressed specialist; the lead's receipt cannot be reused. A path plus
range remains an instruction to read exactly that range, not prompt content
copied by a UI. References grant read-only context: they do not authorize an
artifact change, specialist execution, or Git operation.

## Prepare files safely now

Users may create or inspect canonical source files with ordinary filesystem
tools while preparing a workspace. Keep core documents in their canonical
directory and manuscripts under a part directory, for example:

```text
core_documents/global_outline.md
core_documents/character_sheets.md
core_documents/narrative_rules.md
core_documents/part_outlines.md
manuscript/part-01/scenes/opening.md
```

These files are local plaintext. Their presence alone does not establish
agent-produced provenance or cause a provider call.
