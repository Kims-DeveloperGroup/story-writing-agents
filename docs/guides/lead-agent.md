# Lead agent: responsibilities and current availability

The Lead Orchestrator is the user's intended front channel for story work. In
the target workflow, users converse with one lead; the lead autonomously
cooperates with specialists and returns one coherent next step. That active
provider workflow is not callable in this integration stage.

## What the lead owns

The lead is specialized in coordination rather than being a generic drafting
shortcut. Its responsibilities are:

- maintain the user-facing conversation and explain the current decision;
- determine or recommend the next useful action;
- critique proposals and identify missing evidence or policy conflicts;
- route plot, character/continuity, and prose/execution questions to the
  appropriate specialists;
- frame specialist prompts with the confirmed task, current collaboration
  state, and authorized artifact references;
- compare specialist responses, expose meaningful disagreement, and lead a
  bounded debate when needed;
- watch canonical artifacts, collaboration history, and workflow state so
  promised narrative rules and policies are not silently broken;
- ask for user confirmation before an action that changes the agreed plan or
  artifacts; and
- synthesize an answer without pretending that an unconfirmed proposal is a
  committed decision.

The lead may act as facilitator, critic, planner, continuity reviewer, evidence
router, debate moderator, and final synthesizer. It does not replace specialist
expertise: plot structure goes to the Plot Architect, character/world
continuity to the Character Custodian, and prose/drafting constraints to the
Execution Specialist.

## Confirmation boundary

The intended sequence is interactive:

1. The user states or revises a goal.
2. The lead summarizes the understood scope, recommends a next action, and asks
   for confirmation when the action would create or modify artifacts.
3. After confirmation, the lead calls the necessary specialists with bounded,
   additive context and explicit file references.
4. Specialists search or read only what their task authorizes, then answer the
   lead. They do not become independent user-facing channels.
5. The lead critiques and reconciles the responses. A disagreement can become
   a resumable debate rather than a copied replacement prompt.
6. Before a material artifact update, the lead presents the proposed change
   and asks the user to confirm it.
7. The runtime records the structured collaboration steps and performs only the
   confirmed write.

A confirmation approves the named next action, not every possible downstream
write. A changed outline, character sheet, narrative rule, or manuscript must
remain explicit in the proposal.

## Target provider-call shape

The following illustrates the later #452/#453 workflow. The #479 storage and
inspection path is active, but this provider-bound workflow is not callable
today.

User input:

```text
Please check whether the opening conflicts with the outline.
```

Lead response before delegation:

```text
I can compare the opening with the relevant outline section, ask the Plot
Architect to assess structural consistency, and return a critique without
changing either file. Shall I proceed with that read-only review?
```

After the user confirms, the lead's specialist request is conceptually:

```json
{
  "agent_id": "plot_architect",
  "task": "Identify structural conflicts and propose the smallest correction.",
  "references": [
    {"path": "core_documents/global_outline.md"},
    {"path": "manuscript/part-01/scenes/opening.md", "from_line": 1, "to_line": 80}
  ],
  "write_authorized": false
}
```

The provider receives the specialist system prompt plus bounded selected
context and the exact authorized source bytes. Search/read are agent-context
actions inside that logical invocation; they are not separate public provider
endpoints. The specialist returns a structured answer to the lead, and the lead
returns a user-facing critique.

## Current input/output

Today the same user input never reaches the lead:

```text
story[novel]> Please check whether the opening conflicts with the outline.
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

No specialist is triggered, no provider call is made, no reference is read,
and no collaboration event or artifact is written.

Users can still prepare the lead's future scope:

```text
/session create novel
/conversation new Opening review
/override language EN
/status
/conversation list
/history
```

The sole schema-v2 storage/inspection service is active now. #452 owns the
later lead-facing proposal/confirmation/reconfirmation workflow and #453 owns
later specialist execution/result/finalization/outcome-unknown behavior. #454
owns whole-workspace rejection of unsupported history. Until those
integrations complete, lead delegation is target behavior, not an installed
command promise.
