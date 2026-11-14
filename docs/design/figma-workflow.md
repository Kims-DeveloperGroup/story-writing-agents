# Figma design workflow

Use this procedure when accepted work changes UI composition, visible interaction,
visual presentation, or the design system. Root decides from the intended result,
not a filename or a mention of Figma. Ordinary backend work, output-preserving
refactors, and general documentation maintenance skip Figma discovery and designer
delegation. A small link correction may remain root-owned.

This is task-scoped synchronization through the connected Figma plugin. Code and
current reference documents establish implemented behavior; Figma represents visual
references, prototypes, and explicitly labeled proposals. Changes to behavior still
need accepted requirements and implementation evidence. There is no background sync.

## Target and source map

The official file is [story-writing-project · Interactive UI](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE).
Its file key is `js6oJ5Eskarh2Gy8h6xxRE`. Verify IDs and current content before use;
these links identify assets, not an assertion that their content is always current.

| Contract / source owner | Figma target |
| --- | --- |
| [Current terminal contract](../reference/interactive-terminal.md), `interactive_terminal._core` / `_render`, `interactive_controller` | [Current UI: eight state fixtures](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=4-24) |
| [Message frames](../reference/interactive-terminal.md#message-frames-markdown-and-response-metadata), `_model` / `_render` | [Reusable blocks](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=4-28) |
| [Confirmation decisions](../reference/interactive-terminal.md#confirmation-decisions), `_collaboration_view` / `_render` | [Review](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=6-21), [Apply](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=6-46), [Commit](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=6-56) |
| Width-dependent message layout, `_render` | [Compact reference](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=6-70) |
| Session → Lead → review → Apply → Commit navigation | [Prototype start](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=9-33) |

The Design System page links back to this map, the current contract, the
[historical CLI design record](interactive-cli-design-system.md), source owners,
the repository, and [Project #1](https://github.com/orgs/Kims-DeveloperGroup/projects/1).
The maintained [Design System page](https://www.figma.com/design/js6oJ5Eskarh2Gy8h6xxRE?node-id=18-55)
has page ID `18:54` and documentation root ID `18:55` (verified on 2026-11-15).

The original fixtures were rendered from source `65ad7c277d1da5ad7b1cab931f48364607067d67`.
At the accepted baseline `8e730b3e8f5b092d34e8d249da56a1275fc3ded4`, Git comparison
shows no changes to `interactive_terminal`, `interactive_controller`, or the terminal
reference since that snapshot. This establishes source continuity only; it does not
turn illustrative settings/usage values into live runtime observations. Revalidate
affected source and Figma nodes for each later task.

The existing 16 variables, five text styles, and nine reusable blocks are the
initial reference assets. The runtime emits semantic SGR roles and terminal-cell
geometry, not fixed RGB colors or a prescribed typeface. JetBrains Mono 14px/20px
and the Dark palette are a reference host theme. Preserve `NO_COLOR`, fallback,
compact layout, and confirmation meaning independently of that theme. Python
source links are documentation links, not native Code Connect bindings.

## Ownership and task context

Root owns requirements, workflow policy, general documentation, GitHub records,
integration, and the design-impact decision. The optional development `designer`
owns only assigned design documents and Figma work. Production implementation
and runtime tests retain their existing owners. These roles are separate from
the novel-writing agents under `config/agents/`.

In an Issue's existing implementation summary, record `Design impact: required`
or `not_required` and the reason. Required means the design procedure applies;
it does not force delegation of a trivial task. Use designer when its specialized
ownership provides a clear benefit under [subagent routing](../../.agents/skills/subagent-routing/SKILL.md).
Do not add Project fields, statuses, a separate approval stage, or an extra generic
reviewer role to implement this procedure.

For a designer assignment, add `design_context` to the normal task packet:

| Item | Required information |
| --- | --- |
| Target | File URL/key, page/node IDs, and exclusively owned nodes/document sections. |
| Contract and source | Relevant canonical document anchors, source owners, accepted design intent, and source base/head. |
| Baseline | Existing design IDs and inspected source/design state; identify fixtures or proposals. |
| Allowed operations | Read-only analysis/review, or exact implementation changes; no inferred GitHub/publishing authority. |
| Session | Root creates/reuses one Figma task session and supplies its UUID when supported; pass it unchanged to eligible tools. |
| Evidence | Required structural checks, affected states, source comparison, and visual evidence. |

`analysis` and `review` inspect and report without file, canvas, comment, or
prototype edits. `implementation` changes only the assigned design targets.
Report proposed product-code or workflow-policy changes to their owners.
The profile inherits the parent's model, permissions, provider, and MCP servers;
instructions constrain ownership, not a separate technical sandbox.

## Synchronize and verify

1. Confirm scope, identity, target access, and the existing source/design map.
   Load the applicable Figma plugin skills: `figma-use` for API operations,
   `figma-generate-design` for visible composed views, and
   `figma-generate-library` for component/token work. Follow their discovery,
   font-loading, reuse, page-context, mutation, and recovery requirements.
2. Compare affected contracts, actual renderer output, and Figma. Keep historical
   states and proposals explicitly labeled. Reuse compatible assets; update only
   accepted differences. Maintain editable text, instances, and hierarchy.
   Complete-UI rasters are visual evidence rather than design layers.
3. Return every changed/created node ID and relevant variable/style/component
   references. Read back hierarchy, text/font use, image-filled nodes, and bounds.
   Compare one complete composition screenshot with the relevant source; take
   a post-fix screenshot only after a targeted visual correction.
4. Record source base/head, file/node links, actual comparison coverage, structural
   results, visual evidence, findings, and gaps in the existing Issue/PR Evidence.
   Include prototype connections when affected. A screenshot alone does not prove
   editability, behavior, or runtime confirmation authority.

Use actual source references for new work; do not invent a new token or publish
a library solely to fill this document. The existing baseline remains reusable
when its affected source and design inputs are unchanged.

## Project handoffs

| Stage | Additional handoff only when design is required |
| --- | --- |
| Plan | Design impact/reason, target, accepted intent, source baseline, ownership and observable completion criteria. Read-only design discovery; planning authority does not authorize canvas edits. |
| Implement | Assigned document/Figma changes, returned IDs and source links, comparison results and remaining gaps. Product code stays with its implementation owner. |
| PR/review | Read-only contract/implementation/Figma comparison against the current affected inputs. Scope any fixes explicitly in implementation mode; integrate findings into existing review/severity/closure rules. |
| Complete | Verify the reviewed design state still matches the final affected change and record the reviewed head/merged SHA correspondence. Follow the existing integration gate; new design fixes return to PR/review. |

Each standalone Project stage retains its existing authority and stop point.
Design review supplements current PR validation; it does not replace required
CI, managed code review, or root's completion assessment. Runtime confirmations
shown in prototypes never authorize real Confirm, Apply, or Commit operations.

## Failures and resumption

Missing Figma capability/access, target identity drift, ownership conflict, or
material scope expansion stops the dependent design step. Report the exact cause,
affected criterion, owner, and recovery action. Continue independent authorized
work; never substitute a new file or claim synchronization passed.

Preserve successful file/node IDs and partial results. After interruption or an
uncertain write, inspect the owned targets and follow the tool's retry-safety
instructions before retrying. Reuse verified nodes instead of duplicating them;
never remove unrelated nodes through name-prefix cleanup. Revalidate evidence
when affected source or design inputs change, and retain prior observations with
their original scope.

## Verification scenarios

| Scenario | Expected decision / evidence |
| --- | --- |
| Backend fix or identical-output renderer refactor | `not_required`; no Figma discovery or designer call. |
| Screen layout, interaction, or design-system change | `required`; scoped target/context and design comparison; designer when specialized ownership helps. |
| Link-only maintenance | Root may handle directly; no automatic delegation. |
| Design review | Inspect/report only; no document, canvas, comment, or prototype writes. |
| Access failure or ownership conflict | Dependent step stops with explicit gaps and recovery owner. |
| Interrupted update | Inspect prior IDs; reuse completed assets, reconcile partial writes, and refresh affected evidence. |

Validate native profile TOML and discovery in a fresh Codex session, skill
frontmatter, document paths/anchors, template headings, and the final diff.
The CI allowlist must contain exactly `runtime_implementer`, `test_engineer`, and
`designer`. Changes to the validation workflow select its existing full CI gate;
this procedure does not alter test inventory, selection, or time budgets.
