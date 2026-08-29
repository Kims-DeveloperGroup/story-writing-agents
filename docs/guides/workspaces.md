# Workspaces

> **Availability:** workspace initialization, discovery, safe reads and writes,
> editable role prompts, and consensus-owned writing artifacts are implemented.

Key terms: **workspace root**—the resolved project and storage boundary;
**core documents**—four durable story-context files; **role prompt**—one
agent's static instructions; **persona**—a selectable behavior template.

A workspace keeps one story project's inputs and runtime artifacts together.
Create a dedicated directory from the repository installation:

```bash
story init ./my-novel
```

Initialization is idempotent: it creates missing directories, configuration,
role prompts, and `.gitkeep` files but normally preserves every existing file
byte-for-byte. It does not create core-document templates, repair malformed
configuration, or move existing manuscripts into the canonical layout. After upgrading, running
`init` again is the safe way to add newly packaged scaffold files.

## Edit the story context

The four files under `core_documents/` have separate jobs:

- `global_outline.md` holds story-wide structure and durable facts.
- `part_outlines.md` holds section, part, or chapter planning.
- `character_sheets.md` holds character, relationship, and world continuity.
- `narrative_rules.md` holds prose, viewpoint, tone, grammar, and format rules.

Initialization does not create these files. They may first appear through a
unanimous agent publication or an external/tool-managed filesystem write made
while workspace processes are stopped. Ask for outline, character/world, or narrative-rule changes
in the run prompt or supplied draft. The execution specialist first authors the
candidate, the specialists audit it, and the lead may return complete changed
core documents during finalization. Advisory conversation never writes them.

When a file is present and safe, it is an authoritative role-relevant P2
context source: the plot specialist is eligible for both outlines, the
character specialist for character sheets, the execution specialist for
narrative rules, and the lead for all four. Earlier manuscripts are SEARCH-first
additive sources rather than automatically eager input. A manual edit changes
the current source revision and can force a delta/bootstrap; it does not change
the built-in stable role definition or fabricate authorship, commit history, or
publication provenance. Generic
`Workspace` writes still reject canonical paths, so external edits must be made
only after all workspace processes stop.

## Customize a role

Each canonical agent owns one static prompt:

```text
config/agents/<agent-id>/AGENTS.md
```

The four IDs are `lead_orchestrator`, `plot_architect`,
`character_custodian`, and `execution_specialist`. Prompt files have no
placeholders and are never interpolated into a request. Role prompt, configured
persona, language, and contextual protocol form stable provider binding
material. Mutable core documents are selected separately as current sources.
Changing stable material causes a new binding; changing a core document changes
context revision/delta state. Finish edits before starting a run.

Normal initialization preserves customized prompts. To deliberately replace
all four with packaged defaults:

```bash
story init ./my-novel --overwrite-prompts
```

This flag is all-or-nothing for the four canonical prompt files. It does not
replace configuration, personas, core documents, session or debate state,
logs, or manuscripts. Back up custom prompts first.

Persona templates are UTF-8 Markdown in `config/persona_templates/` or its
`alternative_templates/` directory. Select them through configuration or an
exact directive; do not place secrets in them.

## Run from the workspace

After initialization, enter the directory and start a session:

```bash
cd ./my-novel
story session create novel
story run --output part-01/opening.md \
  'Draft an opening scene and establish any genuinely changed core documents.'
```

Without `--workspace`, commands search upward and choose the nearest fully
initialized ancestor. An explicit path wins and is safer in automation or with
nested workspaces. Neither discovery nor an explicit workspace argument silently
initializes a directory.

For the complete generated layout and exact storage schemas, use
[Session storage](../reference/configuration/session-storage.md). For path and
symlink guarantees, use [Filesystem safety](../operations/filesystem-safety.md).
For the four exact core paths, part-scoped manuscript grammar, current-source
rules, agent authorship, and publication states, use
[Canonical writing artifacts](../reference/writing-artifacts.md).
For source selection, search/read, and limits, see
[Bounded agent context](agent-context.md).
