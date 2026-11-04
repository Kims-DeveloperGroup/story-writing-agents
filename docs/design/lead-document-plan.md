# Lead document plan

This design is accepted for [Issue #839](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/839#implementation-plan). Implementation and verification are pending in the draft PR. It builds on the current source, readiness and staged-review contracts from #836, #837, #838 and #848.

## User example

A user asks to write chapter 3 while the later part outline is incomplete. Lead keeps chapter 3 as the original goal, identifies the missing ending decision from the supplied sources, and presents the first eligible outline task. It shows the completed and remaining documents, blockers, required input, responsible writer or Lead action, and proposal/Apply/Commit state. It reuses valid earlier decisions.

The user can choose another permitted direction. Editorial preferences remain advice; source, prerequisite, authority and Commit conditions remain enforced.

## Plan and ownership

A pure document-plan value describes exact artifact nodes and prerequisites from the existing whole-book readiness policy. An active window has at most five nodes: the relevant four core files and one exact manuscript path. Every other original target remains explicitly unplanned in `remaining_target_paths`. Completing a window does not complete those other targets.

A planning-only goal reference can retain an exact user message before a canonical manuscript path exists. It marks the target unresolved and cannot satisfy artifact completion. Once an exact target is qualified and disclosed, the plan revision binds the ordinary artifact objective. The existing nonempty-path objective validation remains in force.

The existing conversation event stores preserve the original goal, plan revisions and confirmed node bindings. Existing projections derive the cursor and completion from current evidence. There is no second scheduler, store or mutable completion ledger.

The host builds the plan from actual scoped intent, sources and readiness. The provider selects the next work through the existing recommendation/proposal contracts. It does not author an arbitrary dependency graph. Schema, decoding and repair remain bound to their current contract owner.

## Execution and approval

Global outline and part outline are separate Plot tasks. A dependent writer cannot run against a future output. Independent analysis and review of the same exact immutable candidate retain their existing parallel paths.

The ordinary structured Confirm binds the current plan, node, task, sources, inherited budget and any provisional candidate use. Dispatch checks current evidence again. A changed candidate, scope, direction or task requires the appropriate fresh confirmation.

An approved unapplied candidate is supplemental input for its exact permitted consumer. It grants no canonical completion, Apply or Commit authority. A missing initial global outline still blocks part-outline authoring under #837. An already complete canonical baseline may remain qualified while a same-plan revision candidate is used as disclosed provisional input.

Lead can return a candidate with obvious missing requirements to its writer. Required specialist final review of the revised exact candidate remains necessary.

## Budget and restoration

Moving to a node or revision inherits the existing call and round lineage. It does not replenish the 12-call/2-round task limits or revive old confirmation. A separately disclosed and authorized actual new task retains its own authority without erasing the original goal or cumulative progress.

A plan can remain unfinished when the available budget is insufficient. Conversation changes and restart must restore the original target, current node, valid decisions, blockers and exact sources. Old reviews and unrelated Apply records cannot finish a current node.

## Delivery boundary

Only the current supported format is in scope. Migration, a synchronization registry (#840), post-Apply repair (#842), and new Git or provider layers are excluded. Real output comparisons and user quality reviews remain deferred to Backlog #806. Synthetic contract checks do not establish those evaluation outcomes.
