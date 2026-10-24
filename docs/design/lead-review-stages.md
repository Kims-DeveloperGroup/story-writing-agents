# Lead review stages

This design contract defines the authority boundaries for [Issue #838](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/838). Runtime implementation and verification are tracked in that issue; this document alone does not enable a new response or execution path.

A Lead response should distinguish useful advice about existing material from work that needs specialist drafting, deeper domain judgment, or review of the exact final candidate. Mentioning several affected domains does not by itself justify calling every specialist.

| Stage | Result | Authority |
| --- | --- | --- |
| Procedure or status explanation | Explain the current workflow and recorded state. | No specialist, artifact, Apply or Commit authority. |
| Lead preliminary review | Explain inspected source material, explicit differences, findings and limitations. | A recorded first-pass finding; never final specialist review. |
| Specialist work | Produce the disclosed draft or domain judgment with the required participants. | Only the exact, separately confirmed work. |
| Final candidate review | Review the same complete candidate against all required domain obligations. | Review evidence for that candidate; Apply and Commit remain separate decisions. |

## Preliminary findings

A preliminary finding identifies its exact target, revision and applicable digest. It records the supplied or completed READ evidence, inspected scope, excluded or unread scope, findings, explicit conflicts, missing information and next action. An unread document is outside the finding's coverage. Partial inspection cannot establish whole-work completion.

Explaining an existing character motivation or identifying contradictory statements can be useful without specialist dispatch. Resolving an undecided creative direction, drafting replacement material, or making a deeper domain judgment follows the appropriate specialist and user-decision contract. Existing user decisions should be reused when still applicable.

## Final-review protection

Preliminary and final review evidence have different types and meanings. A preliminary finding cannot satisfy the final-review requirement, fulfill a pending document-change request, or authorize specialist execution, Apply or Commit. Rewording a result or omitting affected domains cannot change those boundaries.

Final review applies to the exact candidate content and the required domain coverage. Reuse requires matching content, relevant source revisions, scope and review requirements. A changed dependency invalidates affected evidence even when the candidate text is unchanged. Runtime checks determine applicability; a provider's completion statement does not grant freshness or authority.

## Current-call contracts

The delivered contract owners remain authoritative for provider schemas, selected validation and bounded repair. A new preliminary-review record must use that same current-call binding and retain task/revision and evidence ownership. Policy text, native response encoding, durable events and replay must agree on its meaning.

See [Lead workflows](../guides/lead-agent.md) for the existing confirmation and artifact workflow.
