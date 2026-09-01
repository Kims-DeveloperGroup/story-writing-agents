# System design overview

The current integration separates the Python lead conversation/proposal layer
from the shell's management/inspection layer and the later specialist-execution
layer. Public schema-v1 direct-run consumers are absent; generic provider,
prompt, context, lifecycle, artifact, and terminal components remain behind
explicit module boundaries.

The production graph contains 97 modules, 369 dependency edges, no cycles, and
104 package-root exports.

## Current user flow

```mermaid
flowchart LR
    U[User] --> CLI[CLI / TTY controller]
    U --> APP[Python application]
    CLI --> M[Session and conversation management]
    CLI --> I[History, search, transcript, status, inspection]
    CLI --> S[Settings and authentication]
    M --> CS[Selected conversation services]
    I --> CS
    S --> CFG[Configuration and provider profiles]
    U -. ordinary or routed story text .-> X[Fixed unavailable guidance]
    X -. no call .-> P[Provider components]
    X -. no append .-> CS
    X -. no read/write .-> A[Writing artifacts]
    APP --> LT[StoryRuntime.lead_turn]
    LT --> CTX[Bounded context and provider]
    CTX --> L[Lead answer, question, critique, recommendation, or proposal]
    L --> D[Structured confirm / reject / revise]
    D -. confirmed proposal stops here .-> E[#453 specialist execution]
    LT -. read-only mandatory references .-> A
```

The controller reads ordinary main input with the terminal's plain management
scope. The parser returns a typed rejection for ordinary/routed/removed
execution forms. The controller does not bind an agent, append an event, run
RAG/reference handling, report provider progress, or publish an artifact.

A Python application can instead call `lead_turn()`. The lead is the sole
user-facing agent, may search/read bounded authorized context, and either
answers directly or discloses one proposal. `decide_lead_step()` records an
exact structured decision. Confirmation does not yet dispatch a specialist or
modify an artifact.

## Management runtime

`StoryRuntime` is the management/configuration/inspection and lead-workflow
facade. It owns:

- workspace-bound configuration and prompt snapshots;
- session-bound directives and overrides;
- quiescent settings prepare/commit coordination with `AgentBackend`;
- persisted session/debate/context inspection;
- content-free artifact-reference operation inspection; and
- lead provider turns, proposal construction, structured decisions,
  supersession, and restart-safe confirmation recovery.

`RuntimeSession` binds the same operations to one validated session ID. Neither
class starts specialist execution or a debate. Broad root aliases are absent.

`session_lifecycle` owns provider-neutral durable/access facts and pure
readiness. `AgentBackend` still owns claims, manifests, provider routes,
bindings, thread references, model/effort resolution, and generic bound-agent
execution seams. Codex/Antigravity adapters own provider process protocols;
`provider_process` owns subprocess lifetime. These retained components do not
create an implicit public execution path.

## Conversation and collaboration storage

Conversation management uses one startup-selected complete service bundle:
catalog, `CollaborationEventStore`, aggregate event-field search, and mutation
coordination. Filesystem and SQLite implementations preserve exact
authority/provenance rules. Selection is next-shell configuration; transfer is
a separate provider-free copy/parity operation.

The controller uses that schema-v2 service for `/history`, `/search`,
`/transcript`, and `/status`. Catalog mutations do not invent events; subjects
are displayed but never searched. Projection, exact citation, paging, context,
archive, and transfer all validate canonical events from the same authority.
Concrete filesystem/SQLite v2 adapters remain direct-module-only; public
facades expose contracts and compositions rather than concrete store classes.

## Optional local RAG

| Boundary | Owner |
| --- | --- |
| Contracts/budgets/source identities | `conversation_rag` |
| Deterministic local embedding/ranking | `conversation_rag_local` |
| Disposable generation/pointers/purge | `conversation_rag_store` |
| Optional generic adapter | `generic_rag_adapter` |
| Deferred concrete composition | `conversation_rag_composition` |
| Authorized inspection/search/projection | `conversation_rag_runtime` |
| Six provider-free/local CLI workflows | `conversation_rag_cli` |

RAG consumes a complete `ConversationStorageServices` authority and treats
derived text/vectors as non-authoritative. Direct search reauthorizes and
rereads current ranges. `/settings` can persist future Local/Automatic policy,
but no current shell story input consumes it.

## Writing artifacts and references

The `writing_artifacts` package owns canonical identities, strict source
discovery, range/path values, reference authorization/read actions,
provenance, journaled catalog-last publication, and recovery. Generic
`Workspace` writes cannot manufacture artifact provenance.

The four supported core paths and every eligible lowercase-`.md` descendant of
`manuscript/` remain reference sources. The terminal retains generic picker and
independent text-viewer components, but the current parser/controller exposes
no provider-text span. `$`/`$$` therefore do not activate from main input.

## Interactive ownership

| Owner | Responsibility |
| --- | --- |
| `interactive_commands` | Pure retained grammar, fixed agent metadata, removal guidance, and command completion. |
| `interactive_controller._core` | Stateful lifecycle/settings/catalog/read dispatch, validation, cleanup, and semantic output. |
| Controller policy leaves | Pure lifecycle, conversation, exchange, and inspection interpretation. |
| `interactive_settings` | Immutable current/pending settings, exact conflict checks, prepared writes, and rollback. |
| `interactive_terminal` | Public semantic terminal facade and internal capable/fallback input, pickers, prompts, viewers, and rendering. |
| `agent_exchanges` plus external viewer | Bounded request/result/outcome storage and immutable read-only inspection. |

The controller is the sole orchestration hub for terminal transitions. Policy
leaves retain no state and perform no I/O. The terminal owns raw-mode and
cleanup resources, sanitizes untrusted text, and exposes semantic rather than
provider-native output. `/exchanges` is inspection-only and cannot replay a
provider call.

## Lead/specialist target

The fixed roster remains Lead Orchestrator, Plot Architect, Character
Custodian, and Execution Specialist. The lead is the Python workflow's sole
user-facing channel. It critiques, recommends the next action, watches
artifact/workflow constraints, and routes a proposed assignment by subject.
Specialists remain lead-routed collaborators, but they are not yet dispatched.
None of this is a CLI dispatch claim.

The current #452 layer owns lead proposal/confirmation/reconfirmation behavior,
and #453 owns specialist execution/result/finalization/outcome-unknown
behavior. #454 owns whole-workspace rejection of unsupported history. Those
provider-bound integrations must use the sole v2 store.

## Dependency direction

High-level composition flows downward:

```text
CLI -> controller -> policies/runtime/storage/terminal
runtime -> conversations/context/backend/configuration/workspace/artifact reads
backend -> provider adapters/process/context seams
conversation services -> filesystem or SQLite ports
RAG runtime -> conversation-service contracts
terminal -> pure models/controls/render/driver leaves
```

Storage, provider, terminal, and policy leaves do not import the controller.
The external JSONL viewer imports no Story module. Agent-context and artifact
components remain provider-neutral. Exact modules and exports are in
[PYTHON_MODULE_INDEX.md](../../PYTHON_MODULE_INDEX.md) and the
[package API](../reference/package-api.md).

See [Runtime management API](../reference/runtime-api.md),
[session lifecycle](session-lifecycle.md),
[collaboration history](../reference/collaboration-history.md), and
[lead-agent status](../guides/lead-agent.md).
