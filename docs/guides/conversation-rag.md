# Optional conversation RAG

Conversation RAG is a local, disposable discovery index over authoritative
conversation storage. The six noninteractive lifecycle/search commands and the
direct service APIs remain available. Interactive provider-turn preflight is
not available in this integration stage.

<a id="interactive-conversation-rag"></a>

## Current boundary

| Available | Not available now |
| --- | --- |
| Inspect, enable, rebuild, search, disable, or purge a local index with `story rag`. | Arm a one-turn RAG query in the shell. |
| Configure Local/Disabled and Automatic policy in `/settings` for future integration. | Apply Automatic policy to ordinary or routed shell text. |
| Compose and call direct local service/search/projection APIs. | Send retrieved excerpts to a story agent through `StoryRuntime`. |
| Use filesystem or SQLite as the authoritative source. | Treat derived fragments/vectors as transcript truth. |

The current shell parser rejects the removed one-turn form with the fixed
direct-execution-unavailable guidance. No pending query is created.

## Mental model

The selected filesystem or SQLite conversation store is authoritative. The RAG
generation contains derived fragments and vectors that can suggest candidate
ranges. Before a direct authorized search returns an excerpt, the service
reauthorizes the stable source identity and rereads the exact current range
from authoritative storage. Missing, changed, out-of-scope, corrupt, or
incompatible derived state falls back or fails with a content-free reason.

The fixed local provider performs process-local deterministic embedding/ranking
without a runtime network request or credentials. Installing its exact optional
dependency can require Git/network access; using the installed local provider
does not.

## Install

The base remains RAG-free:

```bash
python -m pip install --editable .
```

For approved internal, non-redistributing use:

```bash
git submodule sync -- vendor/generic-rag
git submodule update --init --checkout vendor/generic-rag
python -m pip install --editable '.[rag]'
```

Installation does not enable RAG. The optional package is pinned to
`c77456321c614518e420e45c033e3f71dc5363b8` and remains subject to the
repository's dependency and redistribution policy.

## Noninteractive commands

```bash
story rag status --session novel
story rag enable --provider local
story rag rebuild --session novel --yes
story rag search 'query text' --session novel
story rag disable
story rag purge --session novel --yes
```

| Command | Behavior |
| --- | --- |
| `status` | Provider-free optimistic comparison of authoritative and derived checkpoints; no write. |
| `enable --provider local` | Validate the exact optional package/capabilities, then save Local. Does not build an index. |
| `rebuild --yes` | Read the active authoritative store and atomically publish a completely verified new derived generation. |
| `search QUERY` | Run bounded literal-first/local-vector discovery, reauthorize/reread retained ranges, and print citations plus authoritative excerpts. Calls no story agent. |
| `disable` | Save provider Disabled and force Automatic Off. Retains derived generations. |
| `purge --yes` | Remove only the validated derived contents for one session. Does not delete authoritative conversations. |

Status, rebuild, search, and purge accept `--workspace` and optional
`--session`; omission uses workspace discovery/default-session rules. Enable
and disable are workspace-scoped. Rebuild and purge require exact confirmation
for mutation.

Every valid command emits one compact schema-v1 JSON report. Successful
operations exit 0; semantic/precondition failures and argparse errors exit 2.
Search is intentionally content-bearing: protect stdout, pipes, redirects, and
terminal scrollback as conversation data. Other reports contain checkpoints,
counts, state, and fixed failure vocabulary, not queries or excerpt bodies.

## Settings

`/settings` retains two persistent fields:

| Field | Choices |
| --- | --- |
| Provider | Disabled or Local |
| Automatic | Off or On; On requires pending Local |

Changing these values remains pending until Apply Changes is confirmed. A
RAG-only apply writes configuration without provider authentication, storage
activation, optional-package probing, network, or agent work. It affects a
future shell construction, not the current shell.

In this revision, even a later shell does not have an eligible provider-bound
story input. Automatic therefore has no interactive turn to act on. The field
is preserved configuration for the later lead/specialist integration.

## Direct service composition

```python
from story_writing_agents.conversation_rag_composition import (
    compose_local_conversation_rag_runtime,
)

bundle = compose_local_conversation_rag_runtime(
    workspace,
    automatic_enabled=False,
)

result = bundle.service.search_authorized(
    services,
    "novel",
    "query text",
)
```

Use the exact signatures in the direct modules; the example emphasizes
ownership, not a package-root facade. The runtime service provides authorized
inspection, projection, and forced bounded search. Direct applications own the
workspace, complete `ConversationStorageServices` authority bundle, generation
store/state, and optional dependency lifecycle.

`StoryRuntime` can still receive a `conversation_rag_service` as configuration,
but its public management API has no story-turn method that consumes it. Direct
applications that need local search call the direct service rather than
recreating a removed runtime execution method.

See the [package API](../reference/package-api.md#optional-conversation-rag-boundary)
for exact types and composition signatures.

## Data and trust

| Data | Authority and exposure |
| --- | --- |
| Active conversations | Sole source of transcript truth in selected filesystem/SQLite storage. |
| RAG configuration | Provider/Automatic policy only; contains no query or excerpt. |
| Source map | Disposable plaintext conversation fragments; protect as conversation data. |
| Vectors | Derived numeric data tied to fragments; protect the whole generation. |
| Direct search output | Bounded authoritative excerpt bodies and citations; content-bearing. |
| Status/rebuild output | Content-free checkpoints, counts, compatibility, and state. |

The generation store lives under
`agent_states/orchestrator_state/conversation_rag/sessions/SESSION_ID/`. It
uses validated private paths, atomic publication, identity/digest checks, and
bounded locks. Rebuild restarts from the beginning; it does not resume a
partial build. Purge is derived-state deletion, not secure erase, and does not
remove backups, redirected output, or authoritative storage.

Fixed limits include 20 returned candidates, 256 conversations, 10,000 events,
800-codepoint fragments with 80-codepoint overlap, and 32-fragment embedding
batches. The local hash model is deterministic baseline matching, not a learned
semantic-quality guarantee.

## Relationship to collaboration cutover

#479 activates the sole schema-v2 storage/inspection service and removes the
last active-v1 service path. It does not itself make interactive RAG a provider
turn. After parent #467 completes, open #452/#453 integrate lead/specialist
workflows with that service; any future RAG preflight must be authorized within
those workflows rather than restoring an earlier shell command.
