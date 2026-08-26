# Agent configuration

> **Availability:** JSON defaults, per-agent routing, interactive guarded
> settings, editable role prompts, and persona templates are implemented for
> Codex and Antigravity. Desired filesystem/SQLite conversation storage is an
> independent system default. Optional local conversation RAG has separate
> provider/automatic system defaults operated by noninteractive `story rag`
> commands and interactive Settings for the next shell. Interactive
> authentication establishes a missing agent-provider default; Codex is the
> final route fallback.

Key terms: **system default**—the workspace fallback below an agent-specific
value; **agent configuration**—
one role's persistent provider/language/persona choices; **role prompt**—that
agent's static instructions; **persona template**—a reusable behavior document.

## System defaults

`config/system_defaults.json` uses a closed twelve-field schema. New workspaces
are initialized with:

```json
{
  "antigravity_effort": null,
  "antigravity_model": null,
  "codex_model": null,
  "codex_reasoning_effort": null,
  "conversation_rag_automatic": false,
  "conversation_rag_provider": null,
  "conversation_storage_backend": "filesystem",
  "default_backend": null,
  "language": "KO",
  "max_debate_rounds": 1,
  "persona": "default_kim_eun_hee",
  "show_message_details": false
}
```

| Key | Contract |
| --- | --- |
| `language` | Required string satisfying the language identifier rule. |
| `persona` | Required safe template identifier that resolves to an existing template. |
| `max_debate_rounds` | Optional; absent means `1`. A present value is a nonnegative integer and not a boolean. |
| `codex_model` | Optional workspace fallback; absent or `null` means no workspace model fallback. A string must be nonempty, must not begin with `-`, and must contain no Unicode `Cc`, `Cf`, or `Cs` character. The exact string is preserved without trimming or normalization. Persistence does not require catalog membership, so custom models remain supported. |
| `codex_reasoning_effort` | Optional workspace fallback; absent or `null` means no workspace reasoning fallback. A string must match the forward-compatible token rule `[a-z][a-z0-9_-]{0,31}`. Interactive discovery can show the pending effective model's reported choices without making catalog membership a persistence rule. |
| `antigravity_model` | Optional Antigravity workspace fallback with the same safe-text rule as `codex_model`; absent or `null` inherits the Antigravity provider default. |
| `antigravity_effort` | Optional Antigravity workspace fallback; absent or `null` inherits the provider default. The only string values are exactly `low`, `medium`, and `high`. |
| `default_backend` | Optional workspace provider fallback; absent or `null` falls through to Codex. A value is exactly `codex` or `antigravity`. Interactive authentication fills a missing value once and does not replace an existing value. |
| `conversation_storage_backend` | Independent desired conversation storage for the next shell. A non-null value is exactly `filesystem` or `sqlite`; missing or `null` is read as the effective filesystem default. |
| `conversation_rag_provider` | Independent optional conversation-RAG provider. Missing or `null` means disabled; the only non-null value is exactly `local`. `story rag enable --provider local` preflights the pinned optional package before saving it. `/settings` edits Disabled/Local without probing it. |
| `conversation_rag_automatic` | Exact boolean; missing means `false`. `true` is valid only when `conversation_rag_provider` is `local`. A newly constructed shell composes this exact automatic policy once when Local is selected. |
| `show_message_details` | A strict JSON boolean when present. Missing means `false` without rewriting the file. `false` selects compact terminal presentation; it never deletes or truncates stored content. |

Unknown fields are rejected. The complete object is validated before a backend
accepts any explicit model/effort override, so malformed persisted fields are
never hidden by CLI or `AgentBackend` constructor values.

Existing legacy files remain valid: all missing provider-specific keys and
`default_backend` resolve as `null`, providing no workspace model/effort or
agent-provider fallback beyond Codex. Missing or null
`conversation_storage_backend` resolves to filesystem; missing RAG keys resolve
to `null`/`false`, and missing `show_message_details` resolves to `false`.
Loading does not rewrite legacy bytes. The next successful complete
`/settings`, `story storage use`, or `story rag enable|disable` persistence
emits the canonical twelve-key object with explicit storage, RAG, and message-
details values. A binary that enforces an older closed nine-, ten-, or eleven-
key schema rejects that newly saved file because it does not recognize every
new field; downgrading requires an explicit compatible migration.
Running `story init` again, including with `--overwrite-prompts`, does not
rewrite or migrate existing system-default bytes. Global `/settings` edits this
canonical twelve-field object and the per-agent object through an isolated
current/pending view. Its Conversation RAG editor offers Provider
Disabled/Local and Automatic Off/On; Disabled canonicalizes both keys to
`null`/`false`, selecting Local preserves Off, and On requires pending Local.
These editors perform no optional-package probe or installation and add no
supported public save API. The ten language
choices are exactly `KO`, `EN`,
`JA`, `ZH`, `ES`, `FR`, `DE`, `PT`, `IT`, and `RU`. Persona choices come from
the safe lexical catalog with top-level-template precedence. An existing
schema-valid language outside the UI list remains visible and preservable but
is not synthesized as a choice. Maximum debate rounds accepts a nonnegative
ASCII integer, including `0`. System-level provider model/effort values are
preserved but are not edited by the UI; direct validated JSON edits may change
them. Conversation storage is edited independently from agent provider routes
and offers Filesystem or SQLite as a pending next-shell value. Message details
offers Off and On; Off is the default compact view and On reveals documented
diagnostic fields without changing stored messages. Selection
readiness, installation consent, provisioning, parity, and activation are
described by the
[CLI storage contract](../cli.md#conversation-storage-selection).

Conversation RAG is independent of both agent-provider routing and desired
conversation storage. `story rag enable --provider local` preserves the
current automatic boolean; `story rag disable` clears the provider and forces
automatic to `false`. Neither operation edits agent configurations. Direct
validated JSON or `/settings` may set automatic to `true` only beside provider
`local`. A successful Apply of those RAG values uses the established exact-byte
guarded replacement, and the RAG change affects the next shell only. A RAG-only Apply performs no
authentication, storage readiness, runtime activation, route/thread work, or
optional-package probe. That shell reads its backend
construction snapshot once and, when Local is selected, composes one runtime
service with the exact automatic value. Disabled selection performs no
optional-package probe; missing or incompatible Local dependencies leave the
configuration unchanged and produce a usable RAG-free shell with one fixed
warning. Exact command and shell behavior is in the
[conversation RAG CLI contract](../cli.md#conversation-rag-operations) and
[interactive RAG guide](../../guides/conversation-rag.md#interactive-conversation-rag).

At interactive startup, `default_backend` is written only when missing. A
provider authenticated by that immediate interaction wins; otherwise a sole
authenticated provider wins; when both are already authenticated, Codex wins.
The first durable default does not rewrite any explicit agent route. Later
`/auth` or settings authentication follows the same absent-only rule. Within
Settings, a newly authenticated missing default is rebased into the pending
candidate and becomes durable only with the final complete Apply Changes.

## Per-agent configuration

`config/agent_configs.json` accepts only the four canonical IDs and this shape:

```json
{
  "character_custodian": {
    "antigravity_effort": null,
    "antigravity_model": null,
    "backend": null,
    "codex_model": null,
    "codex_reasoning_effort": null,
    "language": null,
    "persona": null
  },
  "execution_specialist": {
    "antigravity_effort": null,
    "antigravity_model": null,
    "backend": null,
    "codex_model": null,
    "codex_reasoning_effort": null,
    "language": null,
    "persona": null
  },
  "lead_orchestrator": {
    "antigravity_effort": null,
    "antigravity_model": null,
    "backend": null,
    "codex_model": null,
    "codex_reasoning_effort": null,
    "language": null,
    "persona": null
  },
  "plot_architect": {
    "antigravity_effort": "medium",
    "antigravity_model": null,
    "backend": "antigravity",
    "codex_model": null,
    "codex_reasoning_effort": null,
    "language": null,
    "persona": null
  }
}
```

An omitted canonical agent is an empty object. Each agent object permits only
`backend`, `language`, `persona`, `codex_model`,
`codex_reasoning_effort`, `antigravity_model`, and `antigravity_effort`.
New workspaces initialize every agent `backend` as `null`.
Provider-specific `null` values inherit the corresponding system value and then
provider default. A null or omitted backend inherits `default_backend` and then
Codex. Allowed backend names are exactly `codex` and `antigravity`. A legacy
`gemini` value is rejected with
`backend 'gemini' is no longer supported; use 'antigravity'`. Unknown agent IDs,
fields, or backend names are errors.

The `/settings` Agents editor follows canonical roster order. It first
selects an agent, then offers Provider, Model, and Effort. Both Codex
and Antigravity are selectable. Provider assignment freshly checks status and,
when needed, completes native authentication before mutating the candidate.
Authentication is subordinate and returns to Provider; a successful selection
changes only the pending in-memory route. Failed or cancelled authentication
leaves that route unchanged, and any missing workspace default also remains
pending in memory before confirmed Apply Changes.

Only entering Model or Effort performs one fresh bounded discovery for the
pending effective provider; Effort also uses the pending effective model.
Model order is Inherit, provider-order discovered models, Current custom when
needed, and Custom model. Effort order is Inherit, exact selected-model Codex
efforts or Antigravity's global `low`, `medium`, `high`, then Current
compatibility when needed. OpenAI's
[current model guidance](https://developers.openai.com/api/docs/guides/latest-model)
is intentionally not frozen into this schema. Google's
[Antigravity headless/CLI guide](https://antigravity.google/docs/cli/headless/)
documents `agy models` and the `low`, `medium`, `high` effort vocabulary.

Edits update only the selected agent's
active-provider pending fields and preserve that agent's inactive-provider
values. Editor labels remain Provider, Model, and Effort; Current/Pending
confirmation stays provider-qualified so inactive values remain clear.
Discovery writes no catalog data. Apply Changes persists only the selected opaque
model/effort token or null. If a launch override supplies an effective
provider/model/effort value, that field is disabled and read-only in the active
shell because the override remains higher precedence.

Opening `/settings` captures exact bytes for both configuration documents and
the optional provider-route generation record, a fully validated immutable
configuration pair, and the safe persona catalog. The root picker has exactly
seven rows in this order: Language, Persona, Maximum debate rounds,
Conversation storage, Conversation RAG, Message details, and Agents. The
system object is the canonical twelve-field shape, including independent
desired conversation storage, startup-fixed RAG policy, and the message-
presentation preference. No configuration value is persisted before Apply
Changes is confirmed; Escape or cancellation leaves the captured bytes
unchanged.

After confirmation, Apply Changes uses one of three exact paths. If
`show_message_details` is the only difference from the immutable opening
snapshot, the controller prepares, verifies, and commits that configuration
change without authentication, backend or runtime staging/activation, storage
readiness, route switching or generation work, or provider/thread changes. The
active presentation flag changes only after the commit succeeds. If only the
two Conversation RAG values differ, the controller prepares, verifies, and
commits the system configuration without authentication, storage selection or
readiness, backend/runtime prepare, commit, discard, or activation, route/thread
work, composition, optional-package probing or installation, provider catalog,
subprocess, socket, or network work. The running RAG service and automatic
policy remain unchanged. Every other mixed change uses the full path: it
freshly authenticates every distinct effective
provider, preflights staged adapters, prepares runtime quiescence, validates the
candidate, coordinates storage readiness and fresh parity, and requires the
opening facts to remain exact. The active presentation flag changes only after
full activation succeeds. A missing provider default learned inside Settings
remains in the pending candidate and becomes durable only through this complete
Apply Changes.

The prepared boundary is exactly `config/system_defaults.json`,
`config/agent_configs.json`, and optional
`agent_states/orchestrator_state/provider_route_generations.json`. Changed
targets are written in that order; a write failure triggers reverse-order
restoration of every attempted target, including the failing target, from its
exact opening bytes. After persistence, the staged backend and runtime snapshot
become active in the current shell without a restart, except that desired
conversation storage and Conversation RAG apply only to the next shell. The
current controller/runtime storage and RAG bundles remain unchanged. A
RAG-only Apply reports `Conversation RAG settings were saved for the next
shell. This running shell retains its current Conversation RAG service and
automatic policy.` A storage change reports `Restart is required for BACKEND.`
A mixed Apply emits only the applicable current-shell, RAG-next-shell, and
storage-restart notices.

A safe post-persist activation failure restores the exact configuration and
route files and leaves the old runtime active. If the runtime cannot prove
either restoration or the active generation, it reports `recovery_required`;
stop using that shell and restart against the persisted state. Host installation,
an empty provisioned SQLite database, or a lazy storage guard may remain after
a later failure. This is guarded coordination, not a public save API or a
general filesystem transaction. Provider-native authentication can also remain
after cancellation, but Settings does not persist its missing provider default
separately. Startup authentication and explicit global `/auth` remain outside
`/settings` and retain their immediate absent-default durability policy.

## Role prompts

Each role owns static strict-UTF-8 Markdown at
`config/agents/<agent-id>/AGENTS.md`. The files have no required placeholders
and are not interpolated into requests. Runtime definitions instead carry a
content-addressed reference to the role prompt, configured persona, language, and
allowlisted core documents.

Normal `story init` creates a missing prompt and preserves existing content.
`story init --overwrite-prompts` replaces all four canonical prompt files,
even if only one changed, while preserving every other existing file. One
backend construction validates and captures the complete persisted system and
agent configuration pair. Direct on-disk edits have no watcher: a newly
launched backend validates and reconciles them, while existing other shells
retain their captured state. Confirmed `/settings` Apply Changes is the distinct
current-shell hot-activation path for live-capable runtime settings described
above; desired conversation storage and Conversation RAG remain next-shell
selections. Session
language and persona overrides remain live, and explicit applicable
CLI/constructor provider settings remain higher precedence. Role, persona, and
core-document content continues to follow its separate definition-snapshot and
binding lifecycle; the settings hot-activation contract does not turn those
files into watched live inputs.

Exact route and model/effort precedence is in
[Runtime overrides](runtime-overrides.md#resolution-precedence). Installation
and mixed-provider examples are in
[Use Antigravity agents](../../guides/antigravity.md).

The packaged `default_kim_eun_hee` persona describes strict, logical, candid,
skeptical writing-room behavior; it is not biographical impersonation.
Additional strict-UTF-8 Markdown templates may be placed at:

```text
config/persona_templates/<identifier>.md
config/persona_templates/alternative_templates/<identifier>.md
```

The top-level directory wins when both contain the same identifier. A configured
template must exist when configuration or a directive is validated.

Exact language and persona identifier formats are in
[Identifiers and errors](identifiers-and-errors.md).
