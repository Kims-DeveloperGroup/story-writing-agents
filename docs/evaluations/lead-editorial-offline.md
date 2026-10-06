# Offline editorial comparison

The standalone `tools/evaluate_lead_editorial.py` script validates supplied
captures, prepares blinded A/B packets, and renders descriptive comparison
reports. It uses the current [nine-case dataset](lead-editorial-cases.json) and
[five-axis rubric](lead-editorial-coordination.md). Replay here means checking
and rerendering captured bytes. The script does not execute writing agents.

The checked-in [example](lead-editorial-offline-example/captures.json) contains
two identical constructed dialogue outputs and two synthetic reviewer records.
It demonstrates parsing, attribution, binding, separate scores, and disagreement.
No provider was called and no human quality assessment was performed. Its
quality conclusion is **INCONCLUSIVE** and improvement is **NOT_ASSESSED**.

## Prepare and compare

Run from the repository root with Python 3.11 or later. Each output directory
must be new; existing directories are refused, even if empty.

```bash
python tools/evaluate_lead_editorial.py prepare \
  --dataset docs/evaluations/lead-editorial-cases.json \
  --captures docs/evaluations/lead-editorial-offline-example/captures.json \
  --out-dir /tmp/editorial-review-new

python tools/evaluate_lead_editorial.py compare \
  --dataset docs/evaluations/lead-editorial-cases.json \
  --captures docs/evaluations/lead-editorial-offline-example/captures.json \
  --packet /tmp/editorial-review-new/review-packet.json \
  --reviews docs/evaluations/lead-editorial-offline-example/reviews.json \
  --out-dir /tmp/editorial-comparison-new
```

`prepare` writes `review-packet.json`, `review-packet.md`, and `blind-map.json`.
Give reviewers the packet and keep the capture manifest and blind map with the
auditor. `compare` writes `comparison-report.json` and `comparison-report.md`.
The checked-in example packet/report can be reproduced byte for byte. To test
fully missing evidence, prepare `missing-captures.json` and compare it with
`missing-reviews.json`; all nine cases remain visible and inconclusive.

Exit 0 means valid offline artifacts were produced, including inconclusive
reports and recorded workflow failures. Exit 2 rejects arguments, schema,
identity, paths, limits, or stale packet/review bindings. Exit 1 reports an
operational filesystem failure; partial output is not accepted evidence.
Validation and bounded rendering precede creation of the output directory.

## Current capture format

Only `format_version: 1` is accepted. Capture/review objects reject unknown
fields. JSON rejects duplicate keys and nonfinite numbers; typed numeric fields
reject booleans where integers are required. The dataset retains its existing
case metadata while validating current case IDs, axes and source bindings.
The [example manifest](lead-editorial-offline-example/captures.json) shows every
required field. Its nulls mean unknown or unmeasured, not zero.

| Record | Required information |
| --- | --- |
| Capture manifest | Comparison ID, exact raw dataset SHA-256, `before_after` or `single_vs_multi`, declared treatment, integer seed, protocol status/evidence, exactly two ordered arms, case records. |
| Case | Dataset case ID, SHA-256 of the complete canonical case, runs keyed by the two declared arm IDs. Omitted cases and arms become `not_observed`. |
| Captured or failed run | `provider_capture` or `synthetic_capture`, exact input digest, conditions, output reference, capture/input evidence, costs, workflow observations, reason. Optional `interaction_context` is `{text,evidence}` or null. |
| Missing run | `not_observed` or `not_supported`, with a nonempty reason; no invented producer, cost, or output. |
| Artifact reference | `kind`, relative POSIX `path` beneath the capture manifest's directory, and SHA-256 of exact raw file bytes. |

Artifact kinds are `output`, `capture_record`, `input_record`,
`accounting_record`, `workflow_record`, `review_record`, and `protocol_record`.
Paths reject absolute paths, dot/parent segments, symlinks, and containment
escapes. Conflicting hashes for the same artifact path are rejected. Outputs
must decode as nonempty UTF-8 when status is `captured`; failed runs may retain
partial output. Artifact hashes bind bytes, not the truth of their attribution.

Conditions record a repository commit or null, provider/model, scalar settings,
unknown settings, call ceiling, evidence access, and user-decision policy.
Provider, model, settings, evidence access, decision policy, and call ceiling
must be known and equal to mark conditions matched. Any unknown setting keeps
matching unknown; unequal known controls are mismatched. A `before_after`
comparison retains commit differences as its declared treatment; `single_vs_multi`
requires the same known commit. There is no prose-equivalence guess.

Costs record submitted calls, prompt/completion tokens, instruction/schema/
payload UTF-8 bytes, elapsed wall seconds, and six decision counts:
clarifications, confirmations, revisions, Apply, Commit, and rejections. Each
value is actual or null. Non-null costs without cited accounting records remain
uncited declarations. Output byte length is measured from the captured file;
it is distinct from provider request bytes. A submitted-call count above the
declared ceiling is an observed violation, retained in the report.

Workflow observations have an ID, auditor ID, status, rationale, and cited
workflow records. `pass` and `fail` require rationale and evidence; missing
expected workflow observations remain `not_observed`. These are supplied audit
observations, not new runtime approval or independently certified proof.

## Identity and blinding

The dataset digest hashes its exact raw bytes. Case digests hash the complete
case object. Input digests hash the ordered material with these exact keys:
`sources`, `user_prompts`, `editorial_brief`, `context_material`, and
`workflow_steps`. Source digests hash literal UTF-8 text without normalization;
prompt digests hash the ordered prompt list.

Canonical JSON uses sorted keys, UTF-8 without ASCII escaping, compact comma/
colon separators, and finite numbers. Generated JSON ends in one LF. The A/B
mapping uses the low bit of the first SHA-256 byte of comparison ID, NUL, ASCII
seed, NUL, and case ID. It is stable across platforms.

The blinded packet contains exact source/input/output text and hashes, statuses,
the rubric, and neutral author-visible interaction context. It excludes arm
labels, treatment, producer/model/commit/settings, provider-call/token costs,
raw transcripts, and audit identities. It includes an opaque `captures_sha256`
binding the **same raw manifest bytes that were parsed**, including hidden
metadata, seed, and formatting.

`compare` regenerates the entire packet from current immutable inputs and
requires exact canonical packet bytes. Reviews bind the SHA-256 of those exact
packet bytes. Even a hidden-metadata-only or formatting-only capture change
requires a new packet and deliberately rebound reviews. A matching visible
output is insufficient. Hashes do not authenticate providers or reviewers and
do not independently certify blinding. Operators must remove identifying
content from neutral interaction notes before handing out the packet.

## Reviews and interpretation

A review manifest has format version, comparison ID, `review_packet_sha256`,
and `reviews`. The [synthetic review example](lead-editorial-offline-example/reviews.json)
shows the current shape. Reviewer/case pairs must be unique. Each record names
reviewer ID, role (`author_intent`, `reader_editor`, or `other`), evidence kind
(`human_blinded`, `human_unblinded`, `model_generated`, or `synthetic_fixture`),
case ID, A/B ratings, preference, important errors, and optional review evidence.

Each side lists all five axes separately: intent fulfillment, voice preservation,
important-error avoidance, edit restraint, and approval burden. Scores are
integers 0–4 or null; a score requires a rationale and output to judge. Null is
unobserved and is never imputed as zero. Important errors retain side, optional
source ID, description, impact, disposition, and reason. The report retains
every reviewer's scores, rationales, mapped preference, errors, paired deltas,
and disagreements. It computes no majority verdict, mean, weighted total, or
automatic improvement claim.

Approval burden concerns the necessity and ease of decisions. Required
confirmations are safeguards and are not automatically avoidable burden. A
human score on this axis requires all six decision counts with accounting
backing and nonempty neutral interaction text with workflow backing. Otherwise
the score must be null. Synthetic/model numerical values can demonstrate
mechanics but remain explicitly unbacked and cannot establish human quality.

A case remains `INCONCLUSIVE` unless both actual provider captures have input/
capture backing, known matched controls, a backed predeclared protocol, complete
distinct blinded human reviews in author-intent and reader/editor roles with
backed interaction context, and no required workflow or call-limit gap. When
these records exist, `PAIRED_EVIDENCE_AVAILABLE` describes availability only;
it does not accept an editorial effect. The overall conclusion is inconclusive
if any case is inconclusive. Improvement remains `NOT_ASSESSED`.

Actual comparative execution and effect-acceptance criteria belong to
[Issue #806](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/806).
No real provider/call budget or human assessment is authorized by invoking
this offline utility. Captured external single-agent data does not introduce a
production direct-specialist execution path. The tool cannot Apply, Commit,
publish, collect credentials, call a provider, or reuse confirmation authority.

## Bounds and delivery evidence

Metadata JSON is limited to 4 MiB per file; prepared packets and generated files
to 128 MiB each; raw artifacts to 16 MiB each; output text to 1 MiB each; unique
artifact bytes to 64 MiB total; references to 512. There are exactly nine cases
and two arms, at most 16 reviews per case, 32 workflow observations per run,
32 evidence references per record, 32 important errors per review, and 32 scalar
settings. General metadata text is at most 4,096 UTF-8 bytes; safe IDs are at
most 128 characters. Invalid or oversized inputs are rejected without trimming.

Only current formats/contracts are required. Migration and older-version reader
compatibility are outside acceptance for this and every other issue.
See the [original delivery audit](lead-editorial-delivery-audit.md) for integrated
workflow safeguards and the remaining effect/integration follow-ups. Canonical
tests use synthetic local data and remain provider-free; their success verifies
software contracts, not writing quality.
