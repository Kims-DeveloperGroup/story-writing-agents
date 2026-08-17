# Workspaces

> **Availability:** workspace initialization, discovery, safe reads and writes,
> and editable role prompts are implemented in the CLI and Python API.

Key terms: **workspace root**—the resolved project and storage boundary;
**core documents**—four durable story-context files; **role prompt**—one
agent's static instructions; **persona**—a selectable behavior template.

A workspace keeps one story project's inputs and runtime artifacts together.
Create a dedicated directory from the repository installation:

```bash
story init ./my-novel
```

Initialization is idempotent: it creates missing directories and defaults but
normally preserves every existing file byte-for-byte. It does not repair or
validate malformed existing configuration. After upgrading, running `init`
again is the safe way to add newly packaged scaffold files.

## Edit the story context

The four files under `core_documents/` have separate jobs:

- `global_outline.md` holds story-wide structure and durable facts.
- `part_outlines.md` holds section, part, or chapter planning.
- `character_sheets.md` holds character, relationship, and world continuity.
- `narrative_rules.md` holds prose, viewpoint, tone, grammar, and format rules.

They begin as ordinary UTF-8 Markdown headings. Replace those placeholders
with your project material. The plot specialist sees both outlines, the
character specialist sees character sheets, the execution specialist sees
narrative rules, and the lead sees all four. Earlier manuscripts are not
automatically added to later context, so promote important durable facts into
the appropriate core document or recap them in the next prompt.

## Customize a role

Each canonical agent owns one static prompt:

```text
config/agents/<agent-id>/AGENTS.md
```

The four IDs are `lead_orchestrator`, `plot_architect`,
`character_custodian`, and `execution_specialist`. Prompt files have no
placeholders and are never interpolated into a request. Before each phase, the
runtime creates content-addressed references for the role prompt, configured
persona, language, and allowlisted core documents. Changing an effective input
causes a new binding on the next phase. Finish edits before starting a run.

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
story run 'Draft an opening scene from the core documents.'
```

Without `--workspace`, commands search upward and choose the nearest fully
initialized ancestor. An explicit path wins and is safer in automation or with
nested workspaces. Neither discovery nor an explicit workspace argument silently
initializes a directory.

For the complete generated layout and exact storage schemas, use
[Session storage](../reference/configuration/session-storage.md). For path and
symlink guarantees, use [Filesystem safety](../operations/filesystem-safety.md).
