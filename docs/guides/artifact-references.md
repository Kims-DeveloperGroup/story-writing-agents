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

Ordinary focused input is Lead input. `$` opens the canonical-artifact picker;
`$$` opens the same picker and then the independent range viewer. Either control
inserts an identity-only indicator, and multiple whole/ranged indicators may be
included in one line.
Submitting a completed indicator starts the runtime reference operation.

For example:

```text
story[novel]> Review ${core_documents/global_outline.md}.
[Lead]
  Outcome: critique
  Next action: continue_with_lead
```

The indicator remains path/range metadata. Runtime authorizes and performs the
mandatory read for each addressed agent; it does not embed the whole file in
the command record.

## Enforcement boundary

The Python lead performs the #452 read. #453 must repeat enforcement for every
later addressed specialist; the lead's receipt cannot be reused. A path plus
range remains an instruction to read exactly that range, not prompt content
copied by a UI. References grant read-only context: they do not authorize an
artifact change, specialist execution, or Git operation.

## What happens when an attached source is later applied

The #520 repository seam can apply an exact request through direct runtime
components, but it does not refresh an agent's prompt context or mutate an
earlier reference capture. Suppose a caller has captured:

```text
${manuscript/part-01/scenes/opening.md}#L12-L24
```

If a later application patches or replaces that file, renames it, or deletes
it, revalidating the old authorization at the caller-named
`after_application` checkpoint returns reference `drift`. Rename and delete
still validate the old source path, so moving or removing it also invalidates
the capture.

```text
input: old reference authorization + checkpoint="after_application"
output: ReferenceAuthorizationError.code.value = "drift"
history: prior evidence and invocation events retained
         + one terminal operation event for checkpoint/outcome
safe next action: capture the intended source again from current files
```

That checkpoint is a reference-revalidation label, not a Git or session
checkpoint. The old bytes are not silently replaced, and the old authorization
cannot continue. The application itself also does not invoke an agent/provider,
confirm Apply, or stage/commit Git. #491 will own the no-call Reject/Revise and
confirmed-Apply workflow; Git remains a separate later confirmed action.

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
