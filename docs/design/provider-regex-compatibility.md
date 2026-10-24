# Native schema regex compatibility

This contract defines the native schema regex boundary for
[Issue #881](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/881).
The issue and its linked PR retain implementation and verification evidence.

Provider-facing patterns must preserve the owning field's accepted values while
using syntax the provider admits. Python compilation alone does not establish
native compatibility. A generated definition alias is a diagnostic location,
not a stable identity for a field or source owner.

Text, SEARCH prefixes, specialist paths, and canonical artifact paths retain
their separate whitespace, Unicode, NUL, component, suffix, and newline rules.
An equivalent consuming grammar replaces lookaround-dependent declarations;
restrictions are not dropped or combined into one broader path language.

The canonical strict-end token is `\z`. One central compiler maps it
to Python 3.11's equivalent `\Z` at evaluation, preserving literal escapes and
character classes. Actual native admission must be verified before accepting
this representation. Schema, validation, repair, and contract fingerprints keep
the same authoritative source rules.

A shared check on the final invocation schema rejects known unsupported
lookarounds before adapter launch, including patterns inside definitions and
continuation or repair schemas. Existing diagnostics identify the schema path
without exposing pattern or story content. This is a check for known
incompatibilities, not a complete provider-regex certification.

Regex keys under `patternProperties` use source insertion ordinals in diagnostic
locations, such as `$.patternProperties[0]`, instead of exposing the key itself.
The guard also visits their child schemas and schema-valued dependencies.

Arbitrary implicit intersections are not silently weakened. Existing owned
primitives and canonical-kind intersections require exact equivalent rewrites;
an unproved native intersection fails explicitly before submission. Standalone
supported custom patterns retain their normal interpretation.

A Lead call retains its full eligible semantic index while binding only complete
members delivered on the physical call. Original owners can declare direct
delivery for ordinary front answers and clarifications, a consultation conclusion,
ordinary structured recommendations, and a prepared proposal
after its exact user confirmation. The declaration covers every complete physical
partition of that owner. A current full-index singleton is also direct. Other
results require a charged selection call followed by a call bound to the chosen
complete member. Schema, validation, selection guidance and repair remain tied to
the same frozen owners. An adapter cannot independently prune semantic authority.

Selection adds no source evidence, READ receipt or publication authority. Accepted
Ordinary SEARCH or READ refreshes the eligible contracts and invalidates the selection.
Repair retains the rejected physical graph and cannot broaden its terminal subset,
including after retrieval or selection, or grant another repair attempt.
The existing physical-call and nonterminal-action limits still apply, including the
selection call and its required successor. Insufficient room is reported before
provider launch instead of increasing the budget or offering the failing monolith.

The original workspace and failure history remain diagnostic evidence. Native
syntax probes and the two reported input checks use disposable verification
state, separate from canonical fake-provider tests and the deferred editorial
evaluation.
