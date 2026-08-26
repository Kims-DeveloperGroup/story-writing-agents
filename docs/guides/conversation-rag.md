# Optional conversation RAG integration

> **Current availability:** Issue #262 provides a direct Python boundary for
> optional conversation RAG. It pins and adapts `generic-rag` but does not yet
> provide a local provider, derived store, runtime authorization/projection,
> citations, CLI commands, `/rag`, or RAG fields in `/settings`. Existing
> literal `/search` behavior is unchanged.
>
> **Planned availability:** Issues #283, #263, and #264 add those pieces in
> order. Commands and flows labeled **planned** below are delivery contracts,
> not current behavior.

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
is not supported. Installing the extra does not enable runtime RAG: #262 only
allows direct composition with caller-supplied collaborators. See the
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
| `story_writing_agents.generic_rag_adapter` | Exact translation to/from `generic-rag` 0.1.0 | Sole production module importing both projects; direct import needs the optional package |
| `story_writing_agents.conversation_rag_composition` | Deferred composition, fixed capability checks, and sanitized diagnostics | Safe to import without the optional package; composition performs the version check and adapter import |

The root facade remains unchanged at 120 exports. The provider allowlist is
exactly `("local",)`, but #262 implements neither that provider nor a vector
store. Composition does not discover plugins, inspect provider configuration,
create credentials, acquire resources, or own lifecycles.

| Fixed boundary | Limit |
| --- | ---: |
| Returned candidates | 20 |
| Conversations | 256 |
| Events | 10,000 |
| Fragment width / overlap | 800 / 80 codepoints |
| Embedding batch | 32 fragments |

Provider candidate text is untrusted adapter input. Story-facing retrieval
returns stable source references and ranks, not provider text or scores. A
future runtime must reauthorize and reread each range. Callers own the embedder,
vector writer/resetter/reader, and lexical retriever for their full lifetimes;
the adapter borrows them only during an operation. Ordinary failures cross the
boundary as fixed content-free categories. `KeyboardInterrupt` and `SystemExit`
propagate.

## Planned agent utilization

The completed integration will use RAG as a bounded preflight, never as memory
authority:

1. Automatic retrieval starts off. `/rag QUERY` may explicitly request one
   preflight; enabled automatic policy may run at most one before an eligible
   turn. Retrieval cannot invoke itself recursively.
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

Planned Issue #283 supplies the deterministic network-free local provider and
disposable atomic generation store. Issue #263 owns authorization, projection
scheduling, authoritative rereads, citations, bounded context, and failure
isolation. None of that behavior is delivered by #262.

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

## Redistribution restriction

At this pin, the generic repository has no explicit redistribution license: its
root has no `LICENSE` or `COPYING` file and its documentation grants no such
permission. Project policy therefore limits this integration to approved
internal, non-redistributing use. Do not publish or redistribute `generic-rag`
source, vendored bundles, wheels, containers, archives, or any artifact that
contains its source until an explicit compatible license is reviewed and
accepted. A pin change alone does not remove this restriction.
