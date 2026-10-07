# Lead editorial evaluation

This evaluation baseline defines what to observe before changing the Lead's
editorial behavior. It contains nine constructed cases: all seven cases in
[Issue #791, section 13](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/791),
plus exploration/adoption and decision-memory recovery. The fiction in the
[case dataset](lead-editorial-cases.json) is evaluation input, not a novel or
recorded provider output.

No provider or human quality evaluation has run for this baseline. A prompt
rule, fake-provider test, or successful authority check does not demonstrate
good writing. Current behavior is source-inspected at
[`c10741ea040a7df47b01074b78b0220e82a8e1b0`](https://github.com/Kims-DeveloperGroup/story-writing-agents/tree/c10741ea040a7df47b01074b78b0220e82a8e1b0).
The dataset labels intended improvements as `proposed`; those labels are not
claims that the capabilities have shipped.

## Cases and acceptance coverage

Each case records the input, source identities, author intent, preserved
elements, permitted changes, expected observations, and delivery Issues.

| Case ID | Editorial question | Important workflow observation |
| --- | --- | --- |
| `dialogue-boundaries` | Does polishing dialogue preserve events, motivation, and voice? | Advice grounded in selected sources; changes limited to the brief. |
| `intentional-unreliable-narrator` | Is intentional ambiguity protected rather than corrected away? | Interpretation does not become a read fact or approved decision. |
| `stronger-effect` | Is "more intense" grounded in the intended effect, or clarified only when necessary? | No invented plot escalation is disguised as source evidence. |
| `minority-setting-error` | Is a source-bound setting error addressed despite two supportive opinions? | An unresolved concern is not reported as consensus. |
| `incompatible-recommendations` | Is there a reasoned choice with costs, rather than contradictory compromise? | Material recommendations retain their dispositions and basis. |
| `review-only-exact-input` | Is critique useful without rewriting the finished text? | Input, candidate, and all reviewer inputs have identical byte hashes. |
| `discarded-alternative-history` | Does a discarded idea stay discarded in a long conversation? | User approval, rejected ideas, and successful Apply remain distinct. |
| `exploration-and-controlled-adoption` | Are provisional ideas useful, and is adoption deliberate? | Only user-selected exploration is allowed; adoption needs fresh ordinary authority. |
| `decision-memory-restart-and-transfer` | Does the current direction retain its reason through storage boundaries? | Actual user/Apply provenance and rejected state survive without deleting history. |

This maps to [#793](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/793):
AC1 is the complete case/source/expectation coverage; AC2 is the separate rubric
below; AC3 is the matched comparison and blinding protocol; AC4 is explicit
fixture/status labeling and static-only publication. Future implementation
Issues are attached to each case rather than treated as completed acceptance.

### Goal and preservation conflicts

[#809](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/809)
extends observations within `stronger-effect` and `incompatible-recommendations`;
the dataset keeps its nine case IDs and exact source/input material. Reviewers
check whether explicit prohibitions remain hard constraints when a goal or a
specialist recommendation conflicts with them. Preferences permit explained
tradeoffs; undecided assumptions remain provisional. A bounded improvement,
useful unchanged result, or grounded explanation of attainable limits can be
successful. Output changes are not required to earn a good score.

Record the conflicting condition and evidence, permitted alternatives, limits,
and the benefit/cost of a proposed condition change. Check unnecessary questions
against existing answers and explicit delegation. Inspect protected events,
dialogue, voice, and ambiguity independently of intent fulfillment; a serious
fact error or protection breach cannot disappear into an average score. Scope
changes still require fresh revision/confirmation and exact final-candidate
review. Review-only input keeps its literal bytes.

The `incompatible-fresh-scope` workflow observation audits fresh scope/revision
and confirmation, plus exact final-candidate review, separately from the human
preservation judgment. Missing authority records keep this observation missing
and the case INCONCLUSIVE. Constructed examples retain that gap rather than
invent a passing workflow audit.

Policy presence, shared-brief transmission, and authority checks establish
deterministic requirements separately from actual judgment quality. These new
editorial observations are **not_run** until eligible outputs and independent
human reviews exist under [#806](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/806).
Changing observations changes dataset/case/packet hashes. Rebound checked-in
synthetic examples remain tool-mechanics evidence, not human evaluations.

## Fixture format

`format_version: 1` defines documentation data, not a runtime provider contract.
The dataset's `source_baseline` pins the inspected repository commit and
references. Every case has a stable `case_id`; indices 1–7 map to the report's
original seven cases. Extension cases have a null report index.

Each `sources` record contains a fixture `source_id`, an eligible canonical
artifact path, UTF-8 `text`, and its SHA-256 hash. Hash the exact text bytes;
do not trim whitespace, normalize line endings, or add a final newline. These
identities identify evaluation material. They are not runtime READ citations,
artifact revisions, approvals, or authority tokens.

`user_prompts` are ordered inputs. `editorial_brief` records the author's
intended effect, preserve/change boundaries, and success criteria.
`context_material` contains explicitly constructed conversation turns or
specialist recommendations. `workflow_steps` are evaluator instructions, not
new API or CLI commands. A real run must obtain the actual runtime records
needed for `expected_observations`; supplied fixture prose cannot stand in for
a provider result, confirmed decision, or successful Apply.

`current_baseline` distinguishes source inspection from an unavailable feature.
`target_behavior` is proposed work, with its owning Issues. Each observation is
either `workflow` or `editorial`, with the evidence it needs. The dimension
names are stable so later offline comparison tooling can use these cases
without turning them into new canonical test IDs.

Baseline consensus submission of a complete draft deliberately transforms it
in DRAFT before AUDIT; see [Consensus runs](../guides/consensus-runs.md).
Explicit review-only adoption is proposed in
[#796](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/796).
Likewise, explicit exploration is proposed in
[#798](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/798),
and typed editorial decision memory in
[#799](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/799).
Record unsupported baseline variants as `not_supported`. Do not label a
different authoring flow as a successful review-only or exploration result.
After-only feature evidence cannot establish a matched creative-quality gain.

### Scoped decision expectations (#810)

For the decision-memory comparison, retain the exact user decision, source evidence,
canonical scope, relation receipts, and manuscript/candidate identities. Observe these
four sequences separately: an overlapping proposed alternative leaves selected intent
unchanged; explicit partial replacement preserves residual intent; a new selected
direction stays distinct from unchanged manuscript bytes and failed or uncertain Apply;
and restoring old wording creates a fresh decision whose history survives restart and
conversation transfer. Unmappable scope and stale source anchors require clarification.

Include a cross-conversation relation change that affects an existing decision while
its original record reference, revision, digest, and the old proposal's conversation tail
remain unchanged. Audit provider-free rejection of the stale confirmation and successful
confirmation of a genuinely fresh, correctly pinned proposal. Also retain an unrelated
disjoint-decision control so irrelevant memory does not invalidate every proposal.

These are workflow observations for the #800 protocol. Actual creative-quality claims
remain #806: run matched provider arms and obtain independent author-intent and
reader/editor human reviews through the participation shell. Deterministic fixtures,
readable status, or synthetic scores cannot substitute for those reviews. Reviewers must
judge preservation of protected ambiguity, setup, and voice from eligible outputs;
a scope overlap or successful Apply receipt is insufficient evidence of semantic quality.

## Editorial rubric

Score each dimension independently from 0 to 4; higher is better. Retain the
reviewer's explanation, supporting passages, and any important error. Use
`not_observed` rather than a score when evidence is absent. Do not collapse the
vector into one score that conceals a serious error or failed authority check.

| Dimension | 0 | 2 | 4 |
| --- | --- | --- | --- |
| Intent fulfillment | Misses or reverses the author's effect. | Partly achieves it, with material compromise. | Clearly achieves it within the brief. |
| Voice preservation | Replaces the intended voice or characterization. | Preserves some distinctive qualities but flattens others. | Retains the relevant voice while making the requested improvement. |
| Important error avoidance | Introduces or hides a major source-bound error. | Leaves a meaningful issue unresolved or poorly explained. | Introduces no important error and handles material concerns accurately. |
| Edit restraint | Rewrites protected events, motives, or ambiguity without authority. | Makes some unnecessary changes or gives weak reasons. | Changes only what the brief allows; any necessary tradeoff is explicit. |
| Approval burden | Creates avoidable decisions or conceals required confirmation. | Adds some avoidable clarification or review effort. | Makes decisions easy to assess and asks only necessary questions while retaining required confirmations. |

Scores 1 and 3 fall between the adjacent anchors. A useful clarification or a
reasoned deferral can meet the brief; completion by rewriting is not always
the right outcome. Record each important error separately with its source,
impact, and whether it was resolved, deferred, or rejected with a reason.
Intentional ambiguity is not an error merely because two statements differ.

Measure approval burden as well as scoring it: count material clarification
questions, proposal/preparation confirmations, revision requests, Apply
confirmations, Commit confirmations, and rejected or abandoned steps. Required
confirmations are authority boundaries, not defects. A system cannot earn a
better burden score by silently skipping them.

## Matched comparison protocol

1. **Freeze the comparison.** Select cases, participating capabilities, scoring
   anchors, reviewers, provider-call ceilings, and the claim being tested
   before viewing outputs. Record the dataset hash, case IDs, exact repository
   commits, model/provider identity, available settings, and any unsupported
   or unknown setting. Keep historical source inspection separate from the
   actual pre-change runtime used in a comparison.
2. **Choose the arms.** Compare captured single-agent outputs with current
   runtime outputs, or compare the current runtime before and after a change.
   The single-agent arm is a separately authorized research comparator; it
   does not add a direct-specialist bypass to the production runtime. Keep the
   same author intent, source bytes, evidence access, model/settings, and
   total provider-call ceiling. Orchestration and policy changes are the
   declared treatment, so their instructions and costs remain recorded.
3. **Isolate state and costs.** Use separate disposable sessions/workspaces
   with identical source inputs and user decisions. Never reuse confirmations,
   candidate identities, or mutable provider sessions across arms. Count every
   submitted call, including Lead, specialist, evidence/context work, retries,
   failures, and resumptions; record actual tokens, elapsed time, and human
   decisions when observable. Mark unavailable measurements as unknown.
4. **Capture the output.** Preserve exact input/output bytes, source hashes,
   candidate/reviewer identities, structured decisions, and source-bound
   evidence. Record failures, unsupported flows, and missing results rather
   than replacing them with a convenient output. Keep the raw record and any
   derived display separately, with a hash linking them.
5. **Blind the editorial review.** Randomize A/B order using a recorded seed.
   Give at least one author/intent reviewer and one reader/editor reviewer the
   same brief, sources, and anonymized outputs. Hide arm, model, call count,
   and provider transcripts during quality scoring. Keep the mapping and
   blinding procedure in the audit record. Constructed author intent must be
   identified as such; it does not represent a real writer's stated preference.
6. **Report differences and disagreement.** Publish each reviewer's dimension
   vector, reasons, important errors, preference, and uncertainty. Compare the
   paired scores by case and dimension. Record disagreement without replacing
   it with a manufactured consensus. Report cost/decision burden beside quality.
   Mixed outcomes, unknown settings, missing runs, or incomparable capabilities
   produce a limited or inconclusive conclusion.

This documentation does not authorize a live provider run. Such a run needs
an explicitly selected provider, bounded total call budget, and isolated
sessions. Deterministic canonical CI remains provider-free. Existing captured
outputs may be evaluated offline when their provenance and matching inputs
are known. Future tooling is owned by
[#800](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/800).

## Workflow evidence and release assessment

Audit workflow observations separately from blinded editorial scoring. Use
`pass`, `fail`, `not_observed`, or `not_supported`, with source-bound evidence.
An absence of writes is established by operation/artifact inspection, not by
the Lead saying it did not write. A reviewer saying "approved" does not prove
user approval or successful Apply. For review-only input, verify the exact
UTF-8 hash at input, candidate adoption, and every reviewer dispatch; do not
substitute a normalized text comparison.

The audit must retain required specialist coverage, current task/revision and
candidate identity, actual READ evidence, user-owned mode selection, and
separate Confirm/Apply/Commit authority where applicable. A creative-quality
preference never overrides a failed authority gate. A runtime/fake-provider
test can demonstrate a deterministic contract while leaving editorial quality
unmeasured.

An improvement claim must name its cases, arms, settings, reviewers, observed
gates, dimension differences, costs, and limits. Do not claim overall better
writing from prompt inclusion, one favorable passage, or a lower call count.
An important error or degraded protected voice must remain visible even when
other dimensions improve. Nine constructed cases do not establish quality
across novels, genres, authors, languages, or production provider versions.

### Editorial-memory and delivery-capacity boundary (#799, #811)

The delivered #799 editorial memory supports decision discovery and restoration;
it does not solve whole-history transmission capacity. #811 covers exact current
JSON-byte diagnostics and boundary behavior at 262,144 UTF-8 bytes, not unlimited
novel length or creative-quality improvement. Contract tests are deterministic
runtime evidence, separate from #806's matched provider runs and independent
human author-intent and reader/editor reviews.

Any future selective-delivery design must define which history is required, which
history can be retrieved with provenance, and how omissions are disclosed while
preserving originals. It must also define archive/conversation/restart behavior
and the same delivery contract for both providers. The current implementation
keeps current required-history inclusion, the cap and existing authority unchanged; no
migration or older-format compatibility work is required.

## Evidence record

Use the repository's [Evidence template](../../.github/templates/evidence.md)
to report actual observations. Include the dataset version/hash, case IDs,
exact arm commits and source/output hashes, model/settings and call ceilings,
actual calls/tokens/time/decision counts, workflow observation outcomes, blind
reviewer scores/reasons, disagreements, unsupported flows, and the claim's
limits. Keep private raw artifacts identified as private; public prose does
not make inaccessible evidence reproducible.

The initial status is **not run**: no provider output, human score, or paired
quality result is included. Publication of these cases satisfies a documentation
baseline, not the runtime acceptance of #795–#799 or the comparison delivery
of #800.

## Static verification

For this documentation-only change, parse the JSON and check unique case/source/
observation IDs, complete report indices 1–7, valid source references, exact
UTF-8 hashes, preserve/change briefs, both observation categories across the
dataset, all five dimensions, and explicit constructed/proposed/not-run flags.
Check local links and required document sections, then run the current static
steps from [PR validation](../../.github/workflows/pr-validation.yml) and review
the final diff. No runtime contract or canonical test is introduced here.

## Offline tooling

Use the [offline comparison protocol](lead-editorial-offline.md) to validate supplied
captures, prepare reproducible blinded packets and render separate five-axis
reports. The [constructed example](lead-editorial-offline-example/comparison/comparison-report.md)
is INCONCLUSIVE about editorial quality; no live provider or human evaluation
was performed. See the [delivery audit](lead-editorial-delivery-audit.md) for
integrated software safeguards and separate effect/integration follow-ups.
