# Optional conversation RAG integration

> **Current availability:** Issues #262, #283, and #263 provide a direct Python
> boundary, a fixed pure local provider, a disposable per-session generation
> store, and provider/store-neutral runtime authorization, projection, and
> agent-context integration. Explicit local composition still needs the pinned
> `generic-rag` package, and an application must inject the low-level service
> plus its restart-safe state port. There is no RAG CLI command, `/rag`, or RAG
> field in `/settings`. Existing literal `/search` is unchanged.
>
> **Planned availability:** Issue #264 adds user commands and storage-backed
> provider/settings selection. Commands and flows labeled **planned** below are
> delivery contracts, not current behavior.

Key terms: **authoritative source**—the current conversation store whose bytes
must be reread before use; **derived projection**—disposable fragments and
vectors; **stable source reference**—session/conversation/turn identities,
revision, fragment, and range without trusted provider text; **preflight**—one
bounded retrieval attempt before an eligible agent turn.

## Install the base or optional boundary

The ordinary installation remains RAG-free. Project runtime dependencies are
empty, and this command does not install or import `generic-rag`:

```bash
python -m pip install --editable .
```

For approved internal, non-redistributing evaluation, initialize the pinned
submodule. For a fresh clone:

```bash
git clone --recurse-submodules https://github.com/Kims-DeveloperGroup/story-writing-agents.git
cd story-writing-agents
```

For an existing checkout:

```bash
git submodule sync -- vendor/generic-rag
git submodule update --init --checkout vendor/generic-rag
```

Install the optional dependency through the declared extra:

```bash
python -m pip install --editable '.[rag]'
```

The `rag` extra is a direct VCS requirement pinned to an HTTPS GitHub URL. Its
first installation normally needs `git`, network access, and repository access.
The submodule is review/development evidence; pip resolves the VCS URL rather
than using `vendor/generic-rag` as its dependency source. A populated local
Git/pip cache can avoid a new fetch, but an offline install from an empty cache
is not supported. Installing the extra does not enable runtime RAG by itself.
Direct local composition still requires an explicit caller-owned store and
lexical retriever; runtime use additionally requires an injected restart-safe
state port and `ConversationRagRuntimeService`. See the
[direct-module API](../reference/package-api.md#optional-conversation-rag-boundary).

## Verify the pin offline

The approved commit is
`c77456321c614518e420e45c033e3f71dc5363b8`. Once the objects and metadata are
present, these checks make no network request:

```bash
rag_expected_pin=c77456321c614518e420e45c033e3f71dc5363b8
git ls-files --stage -- vendor/generic-rag
git submodule status --cached vendor/generic-rag
git submodule status vendor/generic-rag
test "$(git -C vendor/generic-rag rev-parse HEAD)" = "$rag_expected_pin"
git -C vendor/generic-rag cat-file -e "${rag_expected_pin}^{commit}"
git -C vendor/generic-rag status --short
grep -nF "$rag_expected_pin" pyproject.toml uv.lock
```

The index row must use mode `160000` and the approved commit. An ordinary
submodule-status row begins with a space when it matches; `+` is a mismatch and
`-` is uninitialized. A clean nested status prints nothing. `pyproject.toml`
must pin the same full commit, while `uv.lock` must record it in the resolved
Git source and requirement metadata. With the required uv cache available,
`uv lock --check --offline` adds a local metadata check. The installed
distribution reports version `0.1.0`; composition checks that version and a
fixed capability manifest.

## Update the pin as one controlled change

A pin update changes a dependency and cross-repository API boundary. Give it an
approved Issue and dependency/modularity review, then:

1. Review the exact full commit, provenance, Python compatibility, dependency
   direction, security history, tests, API, and license. Confirm `generic-rag`
   still has no dependency on this project.
2. Fetch the generic repository and detach the submodule at that commit. Do not
   follow a floating branch or tag.
3. Update the exact VCS commit in `pyproject.toml`. Change the supported version
   or capability manifest only if the reviewed API requires it.
4. Regenerate `uv.lock` under
   [the dependency policy](../../DEPENDENCY_POLICY.md); never edit it manually.
5. Repeat the agreement checks, then verify the RAG-free base, the VCS-backed
   extra, isolated imports, provider-free and adapter tests, and built artifacts.
   Story distributions must not copy the submodule tree.

Commit the gitlink, `pyproject.toml`, and generated lock change together.

## Current boundary and trust model

| Direct module | Current responsibility | Dependency behavior |
| --- | --- | --- |
| `story_writing_agents.conversation_rag` | Frozen story-owned values, fixed budgets, collaborator protocols, outcomes/reasons, and content-free failures | Standard library only; safe in the base install |
| `story_writing_agents.conversation_rag_local` | Pure deterministic local embedding and vector ranking | Imports only story contracts/literal normalization; no generic package, workspace, I/O, network, credentials, subprocess, or environment discovery |
| `story_writing_agents.conversation_rag_store` | Identity-bound disposable generation construction, verification, active/status pointers, reading, and confirmed purge | Imports no provider or generic package; construction performs no I/O |
| `story_writing_agents.generic_rag_adapter` | Exact translation to/from `generic-rag` 0.1.0 | Sole production module importing both projects; direct import needs the optional package |
| `story_writing_agents.conversation_rag_composition` | Deferred generic composition plus fixed local-provider/store composition, capability checks, fallback mapping, and sanitized diagnostics | Safe to import without the optional package; an explicit composition call performs the version check and adapter import |
| `story_writing_agents.conversation_rag_runtime` | Active-turn projection, literal-first merge, authoritative reread, citation construction, bounded context, and restart-safe runtime-state coordination | Imports only story-owned contracts and conversation-storage ports; owns no store, provider, settings, network, or credentials |

The root facade remains unchanged at 120 exports. The provider allowlist is
exactly `("local",)`. Composition does not discover plugins, inspect provider
configuration, create credentials, or own an external provider lifecycle.

| Fixed boundary | Limit |
| --- | ---: |
| Returned candidates | 20 |
| Conversations | 256 |
| Events | 10,000 |
| Fragment width / overlap | 800 / 80 codepoints |
| Embedding batch | 32 fragments |

Provider candidate text is untrusted adapter input. Story-facing retrieval
returns stable source references and ranks, not provider text or scores. A
configured runtime reauthorizes and rereads each range before agent use.
Callers own the embedder, vector writer/resetter/reader, lexical retriever, and
runtime-state port for their full lifetimes; adapters borrow them only during an
operation. Ordinary failures cross the boundary as fixed content-free
categories. `KeyboardInterrupt` and `SystemExit` propagate.

## Current local provider

`conversation_rag_local` exports provider `local`, model
`story-conversation-rag-local-hash-v1`, and exactly 256 dimensions. The
embedder first pins later Unicode assignments against the standard library's
Unicode 3.2 database, applies the shared strict NFKC/casefold/control/whitespace
normalization, and hashes whole-token plus boundary-padded codepoint 1/2/3-gram
features through a fixed length-framed SHA-256 domain. Integer accumulation and
L2 normalization produce deterministic finite vectors; normalized empty input
produces the zero vector.

The ranker scans at most 65,536 exact 256-coordinate records, removes identical
duplicates, rejects conflicts or cross-session records, retains only positive
similarity, and orders ties by the complete stable source key before the
request's at-most-20 limit. It never returns scores to the story-facing result.

This is intentionally a weak, dependency-free baseline. It offers deterministic
local feature matching, not a semantic-quality guarantee, and must not be
described as equivalent to a learned embedding model.

## Current derived generation store

`ConversationRagGenerationStore(workspace)` retains the identity-bound
`Workspace` without I/O. Its first stateful operation uses the fixed subtree:

```text
agent_states/orchestrator_state/conversation_rag/
├── .lock
└── sessions/
    └── SESSION_ID/
        ├── .lock
        ├── active.json
        ├── status.json
        └── generations/
            └── gen-SHA256/
                ├── generation.json
                ├── generic-manifest.json
                ├── source-map.jsonl
                └── vectors.jsonl
```

Owned directories require mode `0700`; regular files require `0600`. The store
uses descriptor-relative, no-follow operations, meaningful identity checks,
process-local and bounded POSIX advisory locks, canonical bounded JSON/JSONL,
complete digest/count/source-range reconstruction, and finite-vector
verification. Unsupported filesystem primitives fail closed rather than using
a pathname fallback. Deep JSON, non-finite values, numeric overflow, malformed
canonical bytes, unsafe names, and inconsistent persisted records become fixed
content-free corrupt/unsafe outcomes. Status, snapshots, exceptions, and
diagnostics contain no query or source text; `source-map.jsonl` deliberately
does contain derived plaintext source fragments, so protect this subtree as
conversation data.

Two schema identities have different owners. The fixed
`story-conversation-rag-store-v1` value versions only the persisted envelopes in
the active/status pointers and generation metadata. The caller's
`ConversationRagProjectionRequest.schema_id` identifies the projected content;
the store preserves it separately as `projection_schema_id` and in the
checkpoint. Direct callers inspecting a nonlegacy projection must pass that
exact value through the keyword-only `expected_projection_schema_id` argument.
Omitting it keeps the original store-v1 projection expectation and fails closed
against a generation built for another projection schema.

Projection writes an inactive `build-*` generation, completely verifies it,
renames it to its content-derived generation ID, reopens and verifies it, then
writes advisory `status.json`. `active.json` is the sole commit record and its
atomic rename is the final callable publication operation after the complete
temporary pointer and session directory have been synchronized. There is no
post-rename synchronization claim: an immediate system crash may retain either
the old or new valid pointer. A reported failure before that commit preserves
the old pointer byte-for-byte. Partial builds and inactive generations are
orphans; inspection reports but never removes them, while a later safe mutation
validates and cleans them.

Rebuild is deterministic and restartable from the beginning, not resumable.
Inspection status describes compatibility with the requested provider, model,
and projection schema; projection state describes whether a checkpoint was
verified. A structurally verified identity mismatch is therefore
`incompatible` with a `present` checkpoint and safe generation/count metadata,
which lets an authorized caller request a rebuild. An invalid or unverifiable
store envelope exposes no trusted checkpoint. Advisory `ready`, `stale`, or
`provider_failed` status never overrides either identity result.

Existing generations whose projected content used the store-v1 value remain
ready for legacy callers. A caller expecting a different schema sees them as
incompatible; the store does not relabel or migrate their bytes. An explicit
rebuild writes and verifies a new inactive generation before atomically
publishing it. Failure before publication preserves the previous active pointer.
Missing, stale, corrupt, incompatible, and provider-failed state maps through
local composition to `literal_fallback` with a fixed reason and no candidate
text. `purge(session_id, confirmed=True)` prevalidates and deletes only that
session's active/status and generation contents; its lock/directories remain.
It never deletes authoritative conversations, SQLite/filesystem conversation
storage, configuration, manuscripts, or writing artifacts.

## Use the current direct composition

The current composition entry point is direct Python API:

```python
from story_writing_agents.conversation_rag_composition import (
    compose_local_conversation_rag,
)
from story_writing_agents.conversation_rag_store import (
    ConversationRagGenerationStore,
)

store = ConversationRagGenerationStore(workspace)
rag = compose_local_conversation_rag(store, lexical_retriever)
```

`workspace` must be an initialized `Workspace`; `lexical_retriever` is a
caller-owned implementation of `ConversationRagLexicalRetriever`. Explicit
composition immediately validates installed `generic-rag` 0.1.0 and its fixed
capabilities, then creates a call-local generic adapter for each projection or
retrieval that reaches generic work. Among caller-supplied objects, the
composition retains the store and lexical retriever; it also owns the stateless
fixed embedder/ranker. Projection uses the request's schema; retrieval with a
present state inspects against that checkpoint's schema. A different stored
schema becomes an `incompatible` literal fallback, while drift from the same
schema checkpoint becomes a `stale` fallback.
Missing/incompatible optional-package state raises the sanitized composition
error before storage is touched. Composition alone does not register the
service with a runtime or expose it to the shell.

## Use the current programmatic runtime integration

The direct runtime boundary exports seven names and remains outside the
120-name package-root facade:

```python
from story_writing_agents.conversation_rag_runtime import (
    CONVERSATION_RAG_RUNTIME_POLICY,
    CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
    ConversationRagRuntimeError,
    ConversationRagRuntimePolicy,
    ConversationRagRuntimeService,
    ConversationRagRuntimeState,
    ConversationRagRuntimeStateSnapshot,
)
```

An application constructs `ConversationRagRuntimeService(low_level_service,
state_port, automatic_enabled=False)`. The low-level object implements the four
`ConversationRagService` operations. The application-owned state port exposes
the same workspace key and embedding identity plus synchronous
`inspect_projection_state(session_id)` and
`mark_projection_stale(session_id)` operations. This release does not provide
a settings-owned state-port factory; the application must explicitly compose
and retain that collaborator.

`StoryRuntime(..., conversation_rag_service=service)` accepts only that exact
runtime-service type. It uses the default composed filesystem
`ConversationStorageServices` bundle when no conversation-storage argument is
given, or an explicitly supplied complete bundle. RAG cannot be combined with
the legacy raw `conversation_event_store` compatibility argument because that
argument has no catalog, search, provenance, or mutation-coordinator authority.

The additive retrieval call is:

```python
await runtime.converse_with_retrieval(
    session_id,
    conversation_id,
    agent_id,
    text,
    memory_service=memory_service,
    progress_sink=None,
    rag_query=None,
)
```

Automatic preflight is off by default. A non-`None` `rag_query` forces one
attempt and takes precedence over automatic query derivation. If no RAG service
is configured, that forced attempt returns the fixed disabled literal-fallback
outcome; with no forced query, no attempt occurs and the established request and
result shapes omit RAG values. An application may opt into automatic preflight
only when constructing the runtime service with `automatic_enabled=True`; the
normalized current turn is then capped at 200 codepoints for the query.

`ConversationRagRuntimeService.project_authorized()` is also available for an
explicit programmatic projection or rebuild. A configured `StoryRuntime`
automatically calls it after each successfully committed ordinary or retrieval
reply, even when that turn did not run a preflight. Ordinary projection failure
is isolated from the committed reply and marks state stale best-effort;
`KeyboardInterrupt`, `SystemExit`, and cancellation controls propagate after
the reply commit.

## Current runtime and agent utilization

The programmatic integration uses RAG as a bounded preflight, never as memory
authority:

1. Automatic retrieval starts off. A Python caller can pass `rag_query=QUERY`
   to request one preflight; enabled automatic policy may run at most one before
   an eligible retrieval turn. Retrieval cannot invoke itself recursively.
2. The runtime authorizes the workspace/session and sends only normalized scope
   and fixed budgets. Agents receive no vector handle, credential, or permission
   to widen scope.
3. Exact subject/literal candidates retain precedence; semantic candidates add
   bounded discovery under deterministic candidate, conversation, event,
   excerpt, and token limits.
4. The runtime rejects provider text as authority, reauthorizes every stable
   reference, and rereads the exact authoritative range.
5. Only bounded authoritative excerpts, stable labels, and citation ranges enter
   agent context; the agent retains citations in its answer.
6. After a visible complete user/agent-reply turn commits, best-effort projection
   updates disposable state. Failure marks it stale without rolling back or
   blocking authoritative append/read.
7. Semantic retrieval reports `complete`, `partial`, or `literal_fallback` with
   a sanitized reason. Disabled, unavailable, stale, corrupt, incompatible, or
   failed state degrades explicitly to authorized literal retrieval.

Only active conversations and complete visible `user_message`/`agent_reply`
pairs enter the fixed `story-conversation-rag-visible-turn-v1` projection. Each
document is framed as `Subject`, `User`, and addressed `Agent` content with
length-framed SHA-256 identity and revision values. A preflight merges exact
literal hits first, accepts at most one semantic excerpt per current document,
reauthorizes every stable reference against a fresh complete target, and emits
at most 20 `g1` through `g20` citations. Each excerpt is at most 800 codepoints;
the complete context is at most 32,768 UTF-8 bytes. Rejected or changed semantic
references mark derived state stale best-effort rather than trusting old text.

The initial retrieval-provider request receives attempted `rag_context` with a
content-free outcome/reason and bounded authoritative excerpts explicitly
labeled as untrusted story data. Continuation requests omit that context. The
Python result carries the optional content-free `rag_outcome`; no-attempt turns
leave it `None`. See the exact
[payload and citation contract](../reference/conversation-retrieval.md#optional-conversation-rag-preflight).

## Planned user utilization

Planned Issue #264 will expose the completed integration:

| User action | Planned behavior |
| --- | --- |
| `story rag status` | Inspect enable/provider state, versions, checkpoints, stale/error counts, and rebuild need without source text or credentials |
| `story rag enable --provider local` | Opt in to the allowlisted network-free local provider |
| `story rag rebuild --yes` | Rebuild and verify an inactive derived generation, then atomically publish it; interrupted builds restart |
| `story rag search QUERY` | Run bounded hybrid retrieval, authoritative rereads, and cited output without an agent |
| `/rag QUERY` | Force one cited preflight for the next eligible story-agent turn |
| `/settings` | Persist the allowlisted provider and default-off automatic policy through the storage-backed settings owner; never persist credentials |
| `story rag disable` | Stop semantic retrieval without deleting authoritative conversations |
| `story rag purge --yes` | Delete only derived generations after authorization and confirmation |

The `/settings` choice will be durable configuration, independent of
filesystem/SQLite conversation-storage selection. Literal `/search` remains
unchanged. External providers, arbitrary plugin discovery, silent network
transmission, credential storage, SQLite coupling, and MySQL are excluded.
Generic algorithm changes remain owned by the independent `generic-rag`
repository rather than this integration.

## Redistribution restriction

At this pin, the generic repository has no explicit redistribution license: its
root has no `LICENSE` or `COPYING` file and its documentation grants no such
permission. Project policy therefore limits this integration to approved
internal, non-redistributing use. Do not publish or redistribute `generic-rag`
source, vendored bundles, wheels, containers, archives, or any artifact that
contains its source until an explicit compatible license is reviewed and
accepted. A pin change alone does not remove this restriction.
