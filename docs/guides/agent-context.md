# Bounded agent context

This guide explains what story agents receive, how they can add missing
knowledge, and what users see while that work happens. It maps the twenty
illustrative sections of issue #380 to the implemented runtime. For field-level
progress details, see [Agent-context progress](agent-context-progress.md).

Older issue sketches used names such as `outline.md`, `character_sheet.md`, and
`world.md`. In the current workspace these map to the canonical
`core_documents/global_outline.md`, `core_documents/part_outlines.md`,
`core_documents/character_sheets.md`, and
`core_documents/narrative_rules.md` responsibilities; manuscript sources
always use `manuscript/...`.

<a id="c01-context-categories"></a>
## C01 — Context categories

The runtime treats context by purpose rather than as one growing prompt:

| Category | Examples | Current behavior |
| --- | --- | --- |
| Stable identity | Role, persona, language, behavior and action protocol | Kept in the compatible provider binding. |
| Current work | Command, phase, complete logical candidate | Required exactly; omission fails before output is accepted. |
| Working knowledge | Current core-document constraints and focused records | Selected within explicit P2 and total budgets. |
| Additive knowledge | Older conversation, manuscript passage, active-debate record, invoking agent's own state | Omitted sources remain available through bounded lexical SEARCH and exact READ. |
| Mandatory references | `${path}` and `${path}#Lx-Ly` | Read exactly, in command order, for every addressed invocation through the separate reference protocol. |

Optimization removes unchanged repetition. It does not summarize every source,
grant arbitrary file access, or relax a required reference.

<a id="c02-current-agent-binding"></a>
## C02 — From mutable bindings to contextual bindings

The historical baseline staged role-selected mutable core-document bytes with
the system prompt and persona. A core edit therefore changed the provider
definition and repeated those bytes on later calls.

The built-in runtime now creates a provider-managed contextual definition with
an empty mutable-core binding. Stable instructions describe the role and
wire-v5 context protocol; current core documents are authoritative workspace
sources. Fresh scoped work bootstraps the eligible current material, while a
compatible READY checkpoint permits later deltas. Direct custom
`AgentDefinition.context_document_files` remains an eager-binding compatibility
path, not the built-in runtime path.

<a id="c03-ordinary-conversation"></a>
## C03 — Ordinary conversation

Before this initiative, a follow-up resent a bounded projection of as many as
24 complete turns and 32,768 content bytes. Current contextual execution sends
the new required input, selected current material, and compact record/catalog
changes. The durable visible conversation still contains ordinary user and
agent events.

Included conversation-context capture only captures authorized complete turn
pairs; it does not itself search. The provider may subsequently issue bounded
SEARCH and READ actions against the focused conversation when facts were not
selected eagerly.

<a id="c04-conversation-retrieval"></a>
## C04 — Retrieval and contextual search

Established conversation retrieval and contextual search are related but
separate. Retrieval can project cited conversation results. The contextual
invocation can then answer, or search and read authorized sources. In the
interactive shell, established retrieval SEARCH/READ lines are shown once;
duplicate generic context lines are suppressed.

Optional semantic conversation RAG is also independent. Agent-context search
is deterministic lexical discovery over the current authorized source set; it
does not invoke semantic RAG.

<a id="c05-debate-context"></a>
## C05 — Debate context

Every logical debate-agent invocation gets an exact identity: session, debate,
agent, phase, round, and operation. Required phase/task data and the complete
logical candidate remain available. Compatible later calls can name an exact
unchanged-candidate pointer or a lossless single-splice delta instead of
resending unchanged candidate bytes. New debate records are delivered as
compact ordered changes; omitted older records remain searchable.

<a id="c06-explicit-reference-enforcement"></a>
## C06 — Explicit-reference enforcement

For each addressed agent, the runtime sends only reference indicators first,
requires the exact ordered read action, validates scope/path/range/order,
securely captures the current selected bytes, injects them as untrusted
mandatory context, and revalidates receipt before accepting output. The final
indicator contains a path or path plus range—not the file contents.

The limits are 16 references, 128 KiB per selected reference, and 256 KiB in
total. Search results, checkpoints, provider history, or another agent's read
cannot satisfy this obligation.

<a id="c07-measured-amplification"></a>
## C07 — Measured amplification and release baseline

Early issue examples described larger exploratory payloads. The immutable
release comparison is the versioned #381 workload: 14 scenarios, 56 physical
provider calls (3 reference preludes and 53 substantive calls), and 162 raw
reference bytes. Non-reference serialized input fell from 180,575 bytes to
84,053 bytes. These are canonical application/provider-boundary UTF-8 JSON
bytes, not token estimates or provider-hidden prompt expansion.

<a id="c08-stable-binding"></a>
## C08 — Stable binding and safe bootstrap

A contextual provider binding contains stable role/persona/language/behavior
instructions and the compact protocol. A fresh thread bootstraps from current
authoritative sources. Missing, corrupt, stale, tainted, mismatched, or
excessive-depth state cannot authorize omission: the runtime starts a distinct
bootstrap thread or fails explicitly.

<a id="c09-content-free-manifest"></a>
## C09 — Content-free manifest

The runtime inventories source identity, kind, current revision, provenance,
priority, exact UTF-8 size, and record count before planning. It does not put
all source bodies in the manifest. The four searchable authorities are:

- canonical writing artifacts;
- the focused conversation in the same story session;
- the active debate; and
- only the invoking agent's own saved state.

Other agents' private state and internal directories are excluded.

<a id="c10-priorities-and-planning"></a>
## C10 — Priorities and planning

`P0_REQUIRED` keeps current input and the complete logical candidate;
`P1_STABLE` represents compatible stable behavior; `P2_WORKING` keeps current
hard constraints/focused evidence; and `P3_OPTIONAL` is SEARCH-first. The
default source budget is 131,072 bytes, partitioned as 73,728 P0, 16,384 P1,
32,768 P2, and 0 eager P3 bytes. References have their separate 262,144-byte
budget. A plan reports selected and omitted-searchable facts exactly.

<a id="c11-search-read-answer"></a>
## C11 — Bounded SEARCH, READ, and ANSWER

An agent may answer immediately. If knowledge is missing, it can issue no more
than four nonterminal actions across five provider calls, with at most two
SEARCH and two READ actions. SEARCH creates fresh opaque invocation-bound
handles; READ accepts only live handles returned by that invocation. Evidence
returned to the provider includes exact source identity and kind, revision,
boundary, digests, UTF-8 sizes, provenance, handle, and untrusted content.

Stale, guessed, consumed, cross-scope, cross-agent, post-terminal, or drifting
evidence fails. A final ANSWER must name the read handles it used, and an
outstanding required read prevents acceptance.

<a id="c12-content-free-checkpoint"></a>
## C12 — Content-free checkpoint

After successful verification, a READY checkpoint records scope and provider
identity, definition/policy compatibility, source/revision and compact
catalog/record cursors, delta depth, accounting, and exact usage when supplied.
It stores no prompt, candidate, excerpt, reference body, provider response,
blackboard, or free-form summary. Ledger v2 is internal and reconstructable
from authoritative workspace data.

<a id="c13-ordinary-follow-up"></a>
## C13 — Scenario: ordinary no-search follow-up

Input:

```text
Make the ending quieter, but keep the decision that Mara leaves the key behind.
```

An empty fresh workspace can first show the same no-search path as a bootstrap:

```text
[context] bootstrap: 0 selected / 0 bytes; 0 searchable
[context] answer accepted
[context] checkpoint advanced
```

On a compatible checkpoint, the current decision appears in the delta and the
agent again answers in one substantive provider call. Typical output is:

```text
[context] delta: 4 selected / 21,192 bytes; 11 searchable
[context] answer accepted
[context] checkpoint advanced
```

Counts depend on the workspace. No SEARCH/READ line means no context action was
needed; it does not mean the runtime skipped authorization or verification.

<a id="c14-missing-knowledge-outline"></a>
## C14 — Scenario: missing knowledge and outline modification

Input:

```text
Why did Mara refuse the key earlier, and revise the outline so the ending
preserves that reason?
```

The release scenario places the motive in an older focused conversation and in
`manuscript/part_01/chapter_03.md`. The draft agent searches both authorized
source kinds, reads both exact results, uses both handles in ANSWER, and reaches
consensus. Publication creates
`manuscript/part_01/outline_revision.md` and replaces only
`core_documents/global_outline.md`, preserving the motive that accepting the
key would identify Sera as Mara's accomplice.

The live sequence for that draft invocation has this shape before the ordinary
debate panels continue:

```text
[context] lead_orchestrator · round 0 · draft: bootstrap: 1 selected / 31 bytes; 2 searchable
[context] lead_orchestrator · round 0 · draft: search action 1 started
[context] lead_orchestrator · round 0 · draft: search action 1 completed: 2 hits; truncated: no
[context] lead_orchestrator · round 0 · draft: read action 2 started
[context] lead_orchestrator · round 0 · draft: read 2 sources / 142 bytes
[context] lead_orchestrator · round 0 · draft: answer accepted
[context] lead_orchestrator · round 0 · draft: checkpoint advanced
```

The exact counts depend on the fixture/workspace; the wording and order do not.

<a id="c15-multi-agent-debate"></a>
## C15 — Scenario: debate, candidate delta, conflict, and deadlock

Input:

```text
Debate whether the midpoint should reveal that Sera forged the letter.
```

Each draft, audit, proposal, synthesis, and finalization call plans and verifies
its own context. An unchanged compatible candidate uses a proven pointer. A
changed compatible candidate uses the maximal Unicode-code-point single-splice
only when applying it reconstructs the exact target and it is smaller than the
full candidate. Bootstrap, thread loss, taint, identity/digest/size mismatch,
or an unsafe/non-beneficial splice sends the complete candidate.

Conflict rounds preserve new decisions and provenance. A terminal deadlock is
durable and publishes no manuscript. Resume-in-place and debate extension are
not implemented here; issue #140 remains open, so users currently start new
work rather than relying on a resume command.

<a id="c16-part-scoped-modification"></a>
## C16 — Scenario: Part-scoped rule and manuscript change

Input:

```text
Change the narrative rule so memories are never shown as objective fact,
then update affected chapters in Part 1.
```

The runtime authorizes one shared `manuscript/part_01/` prefix before scanning,
ranking, and truncation. The release scenario reads two current nested Part 1
chapters, excludes a stronger Part 2 decoy and an internal discussion-log
decoy, and accepts both evidence handles. One recoverable catalog-last
transaction replaces `core_documents/narrative_rules.md` and creates the
single `manuscript/part_01/revised_memory_sections.md` artifact containing both
accepted sections. The source chapters remain byte-for-byte unchanged, and an
overwrite attempt fails.

The same mechanism supports outline and character-sheet replacements. A
character-sheet scenario reads the current sheet and preserves the named oath
constraint. Manuscript revision is create-only: it reads the prior manuscript
and writes a distinct new canonical path; it does not overwrite the original.

<a id="c17-multiple-mandatory-references"></a>
## C17 — Scenario: three ordered mandatory references

The canonical command indicators are:

```text
Review ${core_documents/global_outline.md} against
${manuscript/part_01/chapter_03.md}#L120-L160 and
${manuscript/part_02/chapter_07.md}#L44-L82.
```

In the shell, `$` opens the whole-file picker and inserts `${path}`. `$$` opens
the same canonical-source list for ranged selection in the independent text
viewer; `$${path}` opens that source directly. Closing a selected range returns
to the unchanged draft and inserts `${path}#Lx-Ly`. More than one indicator can
remain attached in command order. The viewer never embeds file bytes in the
command.

Every logical debate invocation receives the three indicators in this exact
order, performs its own reference prelude, receives the selected current bytes,
and is revalidated before acceptance.

<a id="c18-user-visible-observability"></a>
## C18 — Live content-free progress

The installed shell automatically reports bootstrap/delta, SEARCH/READ,
authorized answer, and terminal checkpoint outcome. Debate lines add
`agent · round · phase`; interleaved operations are validated independently.
Progress exposes only identities, fixed outcomes, booleans, and counts. It is
transient—not another provider call, story/conversation/debate record, ledger,
or post-hoc inspection API. See
[exact output and observer recovery](agent-context-progress.md#read-the-live-shell-output).

<a id="c19-state-transition"></a>
## C19 — State transition

```text
PREPARE -> PLAN -> INVOKE/ACTION -> VERIFY -> COMMIT
    |          |             |          |
    +----------+-------------+----------+-> FAIL
```

PREPARE validates the observer, scope, authority, and references and has no
separate public event. PLAN emits `bootstrap` or `delta` first on a normal
path. INVOKE/ACTION permits a direct answer or paired indexed SEARCH/READ.
VERIFY emits `answer_authorized` after source and domain checks, but references
and the checkpoint are not yet committed. COMMIT emits `checkpoint_advanced`.
FAIL can emit fixed invalidation/budget/action/taint facts and ends with
`checkpoint_not_advanced`. Each operation has exactly one terminal event and
no event follows it.

<a id="c20-decisions-limits-and-user-impact"></a>
## C20 — Accepted decisions, limits, and user impact

Users can:

- continue ordinary and retrieval conversations with the same visible replies,
  citations, and durable transcripts;
- run debates and all four modification classes while agents search and read
  exact authorized current evidence;
- constrain artifact search to one canonical directory prefix;
- attach one or many exact whole-file/ranged references in order; and
- see live content-free progress and retry after a safe failure.

Users cannot:

- assume hidden provider history/cache/compaction is authoritative;
- request semantic artifact RAG through this facility;
- expose arbitrary files, internal directories, or another agent's state;
- use progress to inspect story text, paths, digests, provider responses,
  exceptions, or provider-thread identity;
- treat `answer accepted` as an artifact or checkpoint commit;
- resume a terminal deadlock through #140 behavior that is not integrated; or
- rely on unlimited actions, bytes, sources, delta depth, or durable free-form
  memory.

Provider input-token totals are exact only when every submitted physical call,
including a reference prelude, reports an exact value; otherwise they are
unavailable. Correctness, authorization, exact references, provenance, and
recoverable publication take precedence over optimization.

For implementation ownership and wire details, continue with the
[agent-context design](../design/agent-context.md).
