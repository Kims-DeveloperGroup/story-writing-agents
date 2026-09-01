# Canonical artifact references

Artifact reference indicators remain part of the collaboration design, but no
current public command attaches one to an agent call.

## Indicator format

An indicator contains only a canonical path, optionally followed by one-based
inclusive line bounds:

```text
${core_documents/global_outline.md}
${manuscript/part-01/scenes/opening.md}#L12-L24
```

It never embeds the file body. Multiple indicators can appear in one future
request and preserve their order.

Canonical reference sources are:

- the supported files below `core_documents/`; and
- every eligible lowercase-`.md` file at any depth below `manuscript/`.

Manuscripts are grouped under part-dedicated directories. Publication paths
and reference-source paths are related but separate validation boundaries; see
[Canonical writing artifacts](../reference/writing-artifacts.md).

## Current behavior

Issue #478 removed every public provider-bound input span. Therefore:

- `$` does not open an artifact picker in the current main prompt;
- `$$` does not open the ranged-source picker/viewer workflow;
- typing a completed indicator does not trigger a read;
- ordinary or routed input is rejected before reference parsing; and
- no `reference_operation_id` is created by the installed CLI or management
  runtime.

For example:

```text
story[novel]> Review ${core_documents/global_outline.md}.
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

The file is not opened and its bytes are not sent anywhere.

## Preserved internal contract

The writing-artifact repository, authorization types, content-free operation
history, and terminal viewer components remain available to the forthcoming
collaboration integration. #479 activates the sole schema-v2
storage/inspection service; it does not own lead/specialist triggering or
artifact reads. After parent #467 completes, the #452/#453 workflows must make
the addressed lead or specialist read the attached ordered reference batch
before its answer is accepted. A path-plus-range remains an instruction to read
exactly that source range, not prompt content copied by the UI.

This guide does not promise that future activation will use an earlier
schema-v1 command. The active workflow must connect references to schema-v2
collaboration history and the lead-owned confirmation boundary.

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
