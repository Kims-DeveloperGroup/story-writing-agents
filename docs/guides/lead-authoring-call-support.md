# Authoring call support

The current runtime admits authoring only when it can reserve drafting, preparation, and every required final reviewer within two rounds and twelve submitted provider calls. A lower observed count on a particular route does not change that admission rule. This guide records current supported and unsupported paths at source commit `e9a1978645bf2b1eed873ef2282935b7e519acd2`.

## Calls and reservations

Count durable provider-call events in SUBMITTED state once per exact call identity. Lead answers, specialist responses, preparation, synthesis, READ/SEARCH responses, and corrections can submit provider calls. Host rendering, confirmation, storage, and artifact controls are separate operations. A READ operation can lead to another provider submission; a control click alone is not a provider call.

Let F be front submissions already charged, r_i the current PRESENT-reference flag for each assignment, R whether the basis contains such a reference, and N the required final reviewer count. The current authoring reservation is:

`F + sum(1 + r_i) + (1 + R) + (N + 3) * (1 + R) <= 12`.

This is a conservative admission calculation, not measured provider usage. With uniform PRESENT references, one domain reserves F+12 and three reserve F+20. Both currently reject after any front submission. The rejected route is unsupported by the current guard; that does not prove that every possible direct handoff is mathematically impossible.

## Representative READ paths

One existing-target, three-domain fresh-READ row has been observed through the public API. Other rows are source-level expected classifications and have not each been executed. Every row retains required expertise, source identity, and exact candidate review. A new target can still require current READ because it depends on a PRESENT source. A retained READ boundary does not make that source free across later stages.

| Target | Domains | READ source | Extra submission | Reserved after front | Expected current result | Observed calls |
| --- | --- | --- | --- | --- | --- | --- |
| New target with PRESENT reference | 1 | Retained qualified boundary | None | F+12 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 1 | Retained qualified boundary | One | F+12 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 1 | Fresh current READ | None | F+12 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 1 | Fresh current READ | One | F+12 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 3 | Retained qualified boundary | None | F+20 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 3 | Retained qualified boundary | One | F+20 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 3 | Fresh current READ | None | F+20 | Refuse before confirmation | Not executed; source prediction |
| New target with PRESENT reference | 3 | Fresh current READ | One | F+20 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 1 | Retained qualified boundary | None | F+12 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 1 | Retained qualified boundary | One | F+12 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 1 | Fresh current READ | None | F+12 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 1 | Fresh current READ | One | F+12 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 3 | Retained qualified boundary | None | F+20 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 3 | Retained qualified boundary | One | F+20 | Refuse before confirmation | Not executed; source prediction |
| Existing nonempty target | 3 | Fresh current READ | None | F+20 | Refuse before confirmation | 1 Lead submission; refusal observed (synthetic host-READ seam) |
| Existing nonempty target | 3 | Fresh current READ | One | F+20 | Refuse before confirmation | Not executed; source prediction |

## Paths with no current READ requirement

New MISSING or EMPTY targets whose sources require no current artifact READ have a different reservation: one domain F+6, three domains F+10. Front F=1 reserves 7/11; an already spent correction F=2 reserves 8/12. Confirmation still depends on the exact offered task, all affected domains, and each invocation's remaining allowance. These figures do not promise an arbitrary later correction.

## Verified bounded alternative

A different request used the same task's host-offered revision 2 to create a new, missing working-output file. It selected revision 1's exact draft event and digest as historical input, then confirmed new drafting by Plot, Character and Execution. Every domain retained its current owner contribution and final review duty. Artifact preparation produced one exact candidate; all three reviewers supported that candidate, final synthesis completed, and the artifact reached `reviewed`.

| Public API phase | Current revision calls so far | Rounds spent | Observed result |
| --- | ---: | ---: | --- |
| `RuntimeSession.lead_turn`: recommend fresh consultation | 1 | 0 | Host-offered same-task revision 2 selected |
| `decide_lead_recommendation`: Confirm preparation | 2 | 0 | Fresh drafting proposal awaits Confirm |
| `decide_lead_step`: Confirm drafting | 6 | 1 | Three current owner replies plus Lead synthesis; direct artifact-preparation recommendation |
| `decide_lead_recommendation`: Confirm artifact preparation | 7 | 1 | Same-candidate final review awaits Confirm |
| `decide_lead_step`: Confirm final review | 11 | 2 | All three reviewer replies plus final synthesis; artifact `reviewed` |
| `decide_artifact_application`: Reject | 11 | 2 | No provider submission or write; target remains missing |

The earlier revision retained five actual submitted calls and its original source identity. The conversation therefore records sixteen calls across two separate revisions, with eleven in the fresh lineage. Fresh drafting is new work, not restoration of the earlier allowance or reuse of old owner/review proof. The synthetic draft text happened to be identical in both revisions; new current reply identities and exact review, rather than text novelty, establish the current proposal's provenance.

This verifies one bounded new-output example. It does not revise an existing manuscript, reuse current artifact READ as historical evidence, or promise arbitrary repair. The path avoids an additional ordinary Lead handoff by returning the structured artifact-preparation recommendation directly from drafting synthesis. An ordinary additional front submission must fit the actual remaining allowance and admission rules. Artifact-kind baseline mandatory review is Execution; the authoring scope and disclosed final-review roster retained all three specialists, and all three actually reviewed.

A one-domain or three-domain new-output route without PRESENT-reference costs has source-level reservation `F+2N+4` (respectively `F+6` or `F+10`). These are admission estimates. This guide makes no measured completion claim for unexecuted one-domain or repair variants.

## Recovery and alternatives

Keep completed calls, drafts, and incomplete states when an unexpected READ, SEARCH, or correction exhausts the remaining allowance. Do not omit required domains or READ, erase counters, import earlier review status, or invent a free cross-stage receipt.

A smaller domain count alone does not solve the current PRESENT-reference authoring reservation. A genuinely different user-owned task/output can be useful when it has different source needs; it must preserve every domain still affected by that work. A host-offered new revision can select a prior completed draft as exact historical material and perform fresh drafting/current review. This preserves the old execution and costs. It does not make the original all-domain existing-manuscript READ route supported. The verified new-output example above is a separate bounded request. Use an offered task/revision and fresh confirmations; do not relabel current artifact evidence as user text or historical source to avoid required READ.

After the final round, resolve the actual pending Apply/Commit controls or unknown outcome first. Any continuation stays with its original conversation/lineage; restart reprojects the history rather than replaying calls. Additional work needs an explicitly offered and confirmed new task/revision. Historical text remains material, with its original identity.

## Evidence and interpretation

The measured existing-target row used `RuntimeSession.lead_turn` with an actual nonempty manuscript and all three declared revision owners. The runtime returned a visible budget refusal before offering Confirm: no assignment dispatch, Apply/Commit control, or file change occurred. The exact `(invocation_id, call_id)` ledger contains one submitted Lead INITIAL. Real host READ observations contain all referenced bodies, but the canonical fake prelude supplies no separate provider-accounting facts; that host work is not an extra observed submission. The static reservation is 21, not 21 observed calls. The first attempt failed after an unrelated optional SEARCH and is retained separately; it is not budget evidence.

Measurement results link source/head, exact public API steps, submitted call identities, READ/correction facts, actual outcomes, and the unchanged limits. Estimated reservations stay separate from observed calls. Deterministic fake-provider execution can establish contract feasibility; actual model call counts and creative quality require the separate provider and human evaluation in #806.

The [machine-readable measurement record](../evaluations/lead-authoring-call-support.json) retains the exact source identity, submitted-call ledger, host-offered revision, candidate/reviewer identities, phase counters, fixture hashes, and failed first attempt. Private raw captures remain local; their hashes identify them but do not replace a standalone executable experiment. Both successful observations used typed fake provider responses and the locked Python 3.11 runtime. They establish host contracts and accounting, not actual model usage or editorial quality.

These observations did not execute every READ/repair combination. Existing source/test evidence for the guards is linked to [#797](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/797) and the same-runtime full validation on [PR824](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/824).

For continuation, stay in the source conversation; selecting material in another conversation does not transfer pending controls or execution authority. Restart reprojects the durable history and costs without automatically retrying submissions. Resolve pending Apply/Commit and `outcome_unknown` before further work. A consumed final round cannot be extended by moving conversations, restarting, dropping reviewers or reusing an earlier candidate. See [collaboration and recovery](lead-agent-collaboration.md) and [runtime API](../reference/runtime-api.md).

The bounded comparison protocol in [#800](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/800) must label supported/unsupported requests and separate observed submissions from reservations. [#806](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/806) still requires actual matched provider runs and independent author-intent and reader/editor human evaluation through the participant shell UI. This fake-provider record supplies support-range context; it is not the model comparison or human evaluation.
