# Editorial effect evaluation — Issue #806

Technical delivery and observed editorial effect have separate status. The participant shell and record-binding repair are under review. **Effect: not run.** No actual matched provider captures or user quality scores have been collected. Scripted tests establish mechanics only.

The [machine-readable draft plan](lead-editorial-effect-plan.json) preserves the nine-case material and five-axis 0–4 rubric. User-owned protocol v2 SHA-256: `706a6530ccc90c4d8f2bf593d2309c36cb679e8a81c74945b7d5a0808edce885`. It remains a draft: snapshot the currently selected Story provider/model/settings, actual user intent, total capture limits, exact comparator/treatment commits and real participant ID, then publish the frozen version/hash before output access. Later changes create another version and preserve earlier outcomes.

All nine cases are included. Dialogue boundaries, intentional ambiguity, vague effect, conflicting suggestions, review-only and discarded decisions cover the required core. The unchanged stronger-effect/incompatible-recommendations briefs carry the goal-versus-preservation conflict observations from #809. Minority-setting errors, exploration/adoption and decision-memory restart/transfer are included to expose authority, provenance and capacity limits; none is silently excluded.

The proposed pilot has one run per case/arm and two separately recorded perspectives from the same real user. It is a bounded personal comparison with no independent-human or population claim. Preserve failures, unsupported baseline cells and separate retry accounting. Keep user intent, material, settings, call envelope and decision policy matched; after-only results cannot establish improvement.

The user supplies author intent and conducts both author-intent and reader/editor review passes under the same true participant ID. Save separate immutable role records. Report self-attributed IDs/count, role coverage and the single-person/two-perspective limitation. Two roles do not establish two independent people, and identifiers do not authenticate identity. Disclose constructed material and actual prior knowledge; anonymous display does not prove blinding.

Score all five axes separately with supporting passages, reasons, important errors and uncertainty. Missing evidence remains unobserved, including approval burden without author-visible interaction. Preserve protections and serious errors outside aggregate scores. Compare both recorded perspectives and retain disagreement. An improvement statement requires the frozen predicates and is limited to this user’s observed pilot.

Keep technical provenance and cost beside quality: exact input/output/source/packet/review hashes, arm commits, all physical submitted calls and retries, observable tokens/time and each class of user decision. Unknown cost is unknown, not zero. Preserve disagreement and unfavorable results. Genuine negative results may complete the evaluation; missing actual outputs or human reviews cannot complete #806.

The implemented participant interface is:

```sh
python tools/evaluate_lead_editorial.py grade --packet /path/to/review-packet.json --out-dir /path/to/new-author-review
python tools/evaluate_lead_editorial.py grade --packet /path/to/review-packet.json --out-dir /path/to/new-reader-review
```

Run the two commands as separate role passes using the same true reviewer ID for the same user: author_intent first, reader_editor second. Each pass displays original material/brief, anonymous A/B outputs and the rubric, and collects real judgments for all nine cases. Grade reads only the packet. Keep arm/model/cost mappings outside scoring and answer the prior-knowledge/blinding question honestly. Edit answers before save or type /cancel; EOF/cancellation publishes no complete review.

Place both absent output directories directly under the capture artifact root. Each exclusive publication contains reviews.json and review-record.json; preserve both original directories. Combine only their review arrays into the existing REVIEWS document with unchanged comparison_id and review_packet_sha256 and unchanged per-row evidence references. The comparator validates the packet, record bytes/hash and exact complete row equality with the saved participant record, normalizing only the exported evidence reference. Altered scores, reasons, preferences, errors or identity are rejected before report publication. Same-ID records are distinct by role/case; duplicates within a role are refused. Never rewrite judgments to make a result look better. Exit 0 means complete publication, 3 cancellation, 2 invalid input and 1 filesystem failure; retain any partial filesystem output and retry to an absent directory. Hashes do not authenticate people or prove blinding.

The original admitted source passed 32 affected tests (tool SHA `6cd5bade302527aad08b3bd6fa478dc1c2409698228cab3d169183ee1d3b441b`; smoke SHA `f1ba377c225d134617659ebaace990605e141d738576a9eaa837e93ad2c642b9`). Those results are historical mechanics evidence and do not validate later repairs or real human quality. Current-source CI/review and actual captures/user records will retain their exact identities and outcomes separately. #806 and parent #791 remain open until actual evaluation acceptance is met.
