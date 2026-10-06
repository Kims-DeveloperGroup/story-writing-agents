# Blinded editorial review packet

Hashes bind supplied bytes, not provider/human authenticity, audit semantics or genuine blinding. No automatic improvement assessment; synthetic data is mechanics-only.

## dialogue-boundaries

### Exact input
<pre>{&quot;context_material&quot;:[],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Natural spoken rhythm without changing what happens.&quot;,&quot;permitted_changes&quot;:[&quot;Dialogue wording and punctuation&quot;],&quot;preserve&quot;:[&quot;Hesitation and reassurance&quot;,&quot;Key handoff&quot;,&quot;Trust between Mina and Joon&quot;],&quot;success_criteria&quot;:[&quot;More natural dialogue with the same events, motivation, and voice&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;46af816a5e060d7aa82ad6e4069bd834302b216740c849d4c4c198fc3a2b0a56&quot;,&quot;source_id&quot;:&quot;dialogue-outline-v1&quot;,&quot;text&quot;:&quot;Mina must hand the key to Joon at the station. She distrusts the guards, not Joon. The key&#x27;s owner remains unknown.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;0fccf50dcb3f2f9aa7c042b5bc05130e16d6f739cb55159fa0502d5cf0b46e81&quot;,&quot;source_id&quot;:&quot;dialogue-draft-v1&quot;,&quot;text&quot;:&quot;Mina said, \&quot;I—I guess you should take it. I mean, if you can.\&quot;\nJoon said, \&quot;I can take it. You can trust me.\&quot;\n&quot;}],&quot;user_prompts&quot;:[&quot;Polish only the dialogue in ${manuscript/chapter-01.md}. Keep Mina&#x27;s hesitation, Joon&#x27;s reassurance, the key handoff, and the existing relationship.&quot;],&quot;workflow_steps&quot;:[]}</pre>

### A
Status: captured

Output SHA256: 0fccf50dcb3f2f9aa7c042b5bc05130e16d6f739cb55159fa0502d5cf0b46e81
<pre>Mina said, &quot;I—I guess you should take it. I mean, if you can.&quot;
Joon said, &quot;I can take it. You can trust me.&quot;
</pre>
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: captured

Output SHA256: 0fccf50dcb3f2f9aa7c042b5bc05130e16d6f739cb55159fa0502d5cf0b46e81
<pre>Mina said, &quot;I—I guess you should take it. I mean, if you can.&quot;
Joon said, &quot;I can take it. You can trust me.&quot;
</pre>
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## intentional-unreliable-narrator

### Exact input
<pre>{&quot;context_material&quot;:[],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Unease through deliberate inconsistency.&quot;,&quot;permitted_changes&quot;:[&quot;Clarity of expression&quot;,&quot;A question about unintended confusion&quot;],&quot;preserve&quot;:[&quot;Narrator&#x27;s unreliable claim&quot;,&quot;Room&#x27;s unresolved existence&quot;],&quot;success_criteria&quot;:[&quot;Intentional ambiguity is distinguished from an actual setting error&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;0dd401ae2e2038a4619ff9663531d2e3900d9e606a16eeed6219ca8f4103b02d&quot;,&quot;source_id&quot;:&quot;unreliable-outline-v1&quot;,&quot;text&quot;:&quot;The narrator lies about returning the key. The contradiction is deliberate; whether the room exists stays unresolved.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;5fb327a846047401a397b9589ecba3bb9d2a14178cebd55317c325f303cb4998&quot;,&quot;source_id&quot;:&quot;unreliable-draft-v1&quot;,&quot;text&quot;:&quot;I never returned the key. I know that much.\nYet every night I hear it turn in the lock of the room I swore I had never entered.\n&quot;}],&quot;user_prompts&quot;:[&quot;Review ${manuscript/chapter-01.md} for clarity while preserving the deliberately unreliable narrator and uncertainty about the room.&quot;],&quot;workflow_steps&quot;:[]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## stronger-effect

### Exact input
<pre>{&quot;context_material&quot;:[],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Threatening anticipation.&quot;,&quot;permitted_changes&quot;:[&quot;Timing, sensory detail, and dialogue&quot;],&quot;preserve&quot;:[&quot;No violence&quot;,&quot;No new betrayal&quot;,&quot;Uncertain intention&quot;],&quot;success_criteria&quot;:[&quot;A grounded interpretation or one material clarification, without invented plot escalation&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;bdca2f1333c16d6921f00b3a6852e08609d7776fd72e58c0d643778a33e339fb&quot;,&quot;source_id&quot;:&quot;effect-outline-v1&quot;,&quot;text&quot;:&quot;The reunion should feel threatening without violence or a new betrayal. Mina remains unsure of the stranger&#x27;s intention.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;5fcdfb0a307a8603d537ce5187c411f51c56fff3b8b0df78152a13df49a78e67&quot;,&quot;source_id&quot;:&quot;effect-draft-v1&quot;,&quot;text&quot;:&quot;The stranger stood by the door. Mina waited for him to speak.\n&quot;}],&quot;user_prompts&quot;:[&quot;Make ${manuscript/chapter-01.md} more intense.&quot;],&quot;workflow_steps&quot;:[]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## minority-setting-error

### Exact input
<pre>{&quot;context_material&quot;:[{&quot;kind&quot;:&quot;constructed_specialist_recommendation&quot;,&quot;position&quot;:&quot;support&quot;,&quot;role&quot;:&quot;plot_architect&quot;,&quot;text&quot;:&quot;The crossing makes the escape urgent.&quot;},{&quot;kind&quot;:&quot;constructed_specialist_recommendation&quot;,&quot;position&quot;:&quot;support&quot;,&quot;role&quot;:&quot;character_custodian&quot;,&quot;text&quot;:&quot;Mina choosing Joon expresses loyalty.&quot;},{&quot;kind&quot;:&quot;constructed_specialist_recommendation&quot;,&quot;position&quot;:&quot;concern&quot;,&quot;role&quot;:&quot;execution_specialist&quot;,&quot;text&quot;:&quot;The bridge collapsed at 09:00; the 21:00 crossing conflicts with the supplied setting.&quot;}],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Urgent but causally sound escape.&quot;,&quot;permitted_changes&quot;:[&quot;Revise the escape after checking its premise&quot;,&quot;Defer the choice with the contradiction disclosed&quot;],&quot;preserve&quot;:[&quot;Established bridge collapse&quot;,&quot;No invented alternate crossing&quot;],&quot;success_criteria&quot;:[&quot;A source-bound setting error is resolved or explicitly deferred, never outvoted&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;8f803ee6cf5cb74220e1ff4527a80f20e526a31b8be9d66719e5895303a7ae8f&quot;,&quot;source_id&quot;:&quot;bridge-setting-v1&quot;,&quot;text&quot;:&quot;The only bridge collapsed at 09:00 and remains impassable throughout the chapter. No alternate crossing is established.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;014b6b615208cd428d9baaeb7ec65c5d4a411d83c19a535344ca5cede4392cf2&quot;,&quot;source_id&quot;:&quot;bridge-candidate-v1&quot;,&quot;text&quot;:&quot;At 21:00 Mina ran across the bridge and reached Joon before the guards.\n&quot;}],&quot;user_prompts&quot;:[&quot;Assess the proposed escape in ${manuscript/chapter-01.md} against ${core_documents/global_outline.md} and explain your editorial decision.&quot;],&quot;workflow_steps&quot;:[]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## incompatible-recommendations

### Exact input
<pre>{&quot;context_material&quot;:[{&quot;kind&quot;:&quot;constructed_specialist_recommendation&quot;,&quot;role&quot;:&quot;plot_architect&quot;,&quot;text&quot;:&quot;Reveal that the stranger is the traitor for a sharp twist.&quot;},{&quot;kind&quot;:&quot;constructed_specialist_recommendation&quot;,&quot;role&quot;:&quot;character_custodian&quot;,&quot;text&quot;:&quot;Keep his allegiance uncertain so Mina&#x27;s distrust stays meaningful.&quot;},{&quot;kind&quot;:&quot;constructed_specialist_recommendation&quot;,&quot;role&quot;:&quot;execution_specialist&quot;,&quot;text&quot;:&quot;Strengthen the final gesture without stating what it means.&quot;}],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;A tense unresolved ending.&quot;,&quot;permitted_changes&quot;:[&quot;Ending rhythm, gesture, and implication&quot;],&quot;preserve&quot;:[&quot;No allegiance reveal&quot;,&quot;Later reveal remains possible&quot;],&quot;success_criteria&quot;:[&quot;A coherent choice with explicit tradeoffs, rather than a contradictory compromise&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;e6a402e6cd9eb134f46d2b0c204533cafd1ed63f9a799dca155b8719be7be77b&quot;,&quot;source_id&quot;:&quot;ambiguity-outline-v1&quot;,&quot;text&quot;:&quot;The stranger&#x27;s allegiance must remain unresolved through this chapter. A later chapter reveals it.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;6caede0d3f80b980a285c78629ae744bc099ba6a7d2fda81bbdfe4097e91689a&quot;,&quot;source_id&quot;:&quot;ambiguity-draft-v1&quot;,&quot;text&quot;:&quot;The stranger left the key on the table and closed the door without looking back.\n&quot;}],&quot;user_prompts&quot;:[&quot;Improve the ending of ${manuscript/chapter-01.md} while preserving uncertainty about the stranger&#x27;s allegiance.&quot;],&quot;workflow_steps&quot;:[]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## review-only-exact-input

### Exact input
<pre>{&quot;context_material&quot;:[],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Useful critique of an immutable finished draft.&quot;,&quot;permitted_changes&quot;:[&quot;Review findings only&quot;],&quot;preserve&quot;:[&quot;Every UTF-8 byte, including doubled spaces and final blank line&quot;],&quot;success_criteria&quot;:[&quot;The selected input is the exact candidate reviewed by every reviewer&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;cc7719526995a478be5472e064809b00e1a368a65a79421ee231f8bf602aec24&quot;,&quot;source_id&quot;:&quot;finished-draft-v1&quot;,&quot;text&quot;:&quot;Mina kept the key—then waited.  Nothing moved.\n\&quot;Not yet,\&quot; she said.\n\n&quot;}],&quot;user_prompts&quot;:[&quot;This is my finished draft in ${manuscript/chapter-01.md}. Review it and list findings; do not rewrite or apply changes.&quot;],&quot;workflow_steps&quot;:[{&quot;instruction&quot;:&quot;Evaluate ordinary Lead and consensus flows separately; use the documented existing ordinary review flow and the proposed explicit consensus review-only control when it becomes available.&quot;},{&quot;instruction&quot;:&quot;Record baseline consensus review-only as not_supported; default transforming authoring is not a review-only pass.&quot;}]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## discarded-alternative-history

### Exact input
<pre>{&quot;context_material&quot;:[{&quot;kind&quot;:&quot;constructed_conversation_turn&quot;,&quot;speaker&quot;:&quot;user&quot;,&quot;text&quot;:&quot;Maybe Mina&#x27;s brother could be the traitor.&quot;},{&quot;kind&quot;:&quot;constructed_conversation_turn&quot;,&quot;speaker&quot;:&quot;lead&quot;,&quot;text&quot;:&quot;That is one possible direction.&quot;},{&quot;kind&quot;:&quot;constructed_conversation_turn&quot;,&quot;speaker&quot;:&quot;user&quot;,&quot;text&quot;:&quot;Reject the betrayal idea. Keep the brother protective; that is the current direction.&quot;}],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;A guarded but protective reunion.&quot;,&quot;permitted_changes&quot;:[&quot;Scene structure and emotional pacing&quot;],&quot;preserve&quot;:[&quot;Protective brother&quot;,&quot;Explicit rejection of betrayal&quot;],&quot;success_criteria&quot;:[&quot;Discarded ideas remain history rather than becoming current facts&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;7d3959442bb3bf8f9bdcd9de4610efd625ed7afe51832e289defbe4c7329d799&quot;,&quot;source_id&quot;:&quot;protective-brother-v1&quot;,&quot;text&quot;:&quot;Mina&#x27;s brother is protective. He must not be made the hidden traitor.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;6b8714c49a042955843626d97bd3d1b522c968b5248a91e15a61ec27af67bc7c&quot;,&quot;source_id&quot;:&quot;reunion-draft-v1&quot;,&quot;text&quot;:&quot;Her brother was waiting at the station, holding a second ticket.\n&quot;}],&quot;user_prompts&quot;:[&quot;Outline the reunion using the current direction and ${core_documents/global_outline.md}.&quot;],&quot;workflow_steps&quot;:[]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## exploration-and-controlled-adoption

### Exact input
<pre>{&quot;context_material&quot;:[],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Low-commitment exploration followed by deliberate scoped adoption.&quot;,&quot;permitted_changes&quot;:[&quot;Clearly provisional discussion&quot;,&quot;New confirmed specialist work after adoption&quot;],&quot;preserve&quot;:[&quot;Canonical outline until separately confirmed Apply&quot;,&quot;Only the selected option enters ordinary work&quot;,&quot;Existing pending Apply/Commit safeguards&quot;],&quot;success_criteria&quot;:[&quot;No exploration-to-write or stale-authority shortcut&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;46af816a5e060d7aa82ad6e4069bd834302b216740c849d4c4c198fc3a2b0a56&quot;,&quot;source_id&quot;:&quot;exploration-outline-v1&quot;,&quot;text&quot;:&quot;Mina must hand the key to Joon at the station. She distrusts the guards, not Joon. The key&#x27;s owner remains unknown.\n&quot;}],&quot;user_prompts&quot;:[&quot;Explore two provisional ways Mina could approach the key handoff. Do not change current story facts or files.&quot;,&quot;Choose the honest handoff option and prepare ordinary authoring work for it, with fresh confirmations.&quot;],&quot;workflow_steps&quot;:[{&quot;instruction&quot;:&quot;Select the explicit user-owned exploration control introduced by #798; no command or API name is specified by this fixture.&quot;},{&quot;instruction&quot;:&quot;Repeat adoption with an outstanding Apply/Commit request; existing unresolved-operation safeguards must still hold.&quot;},{&quot;instruction&quot;:&quot;Baseline capability is not_supported. After-only workflow evidence does not establish a matched before/after creative-quality improvement.&quot;}]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

## decision-memory-restart-and-transfer

### Exact input
<pre>{&quot;context_material&quot;:[],&quot;editorial_brief&quot;:{&quot;intended_effect&quot;:&quot;Continuity of a reasoned editorial direction.&quot;,&quot;permitted_changes&quot;:[&quot;Fresh ordinary drafting under the retained direction&quot;],&quot;preserve&quot;:[&quot;Protected uncertainty&quot;,&quot;Rejected immediate reveal&quot;,&quot;Distinct user-approved and applied states&quot;],&quot;success_criteria&quot;:[&quot;Current decisions retain reason and actual provenance through supported storage boundaries&quot;]},&quot;sources&quot;:[{&quot;artifact_path&quot;:&quot;core_documents/global_outline.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;153b9b0f42b6e2a169b4ffa90e1a4b71cf6fd6176d1c2c35cf4eff4ab6ab2514&quot;,&quot;source_id&quot;:&quot;memory-outline-v1&quot;,&quot;text&quot;:&quot;Protect the uncertainty about the key&#x27;s owner. Mina should show doubt rather than make an accusation.\n&quot;},{&quot;artifact_path&quot;:&quot;manuscript/chapter-01.md&quot;,&quot;encoding&quot;:&quot;utf-8&quot;,&quot;sha256&quot;:&quot;ce6a18e8b735e39c76c129d93fcc3952c3172cfe2594bed2556da2c389f33952&quot;,&quot;source_id&quot;:&quot;memory-draft-v1&quot;,&quot;text&quot;:&quot;Mina weighed the key in her hand and let the accusation go unspoken.\n&quot;}],&quot;user_prompts&quot;:[&quot;Keep the doubt and defer the accusation. Reject an immediate owner reveal.&quot;,&quot;After restart and transfer, continue the scene using the current direction and explain why the immediate reveal was rejected.&quot;],&quot;workflow_steps&quot;:[{&quot;instruction&quot;:&quot;Use real structured runtime confirmations to create an approved direction and, separately, a matching successful Apply; retain the actual records.&quot;},{&quot;instruction&quot;:&quot;Also keep one unapproved suggestion and one rejected alternative, then restart and switch conversations.&quot;},{&quot;instruction&quot;:&quot;Exercise supported filesystem/SQLite archive-transfer boundaries; verify current decision provenance before requesting the follow-up.&quot;},{&quot;instruction&quot;:&quot;Typed ledger assertions are proposed behavior; raw-history availability alone is not a ledger pass.&quot;}]}</pre>

### A
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>

### B
Status: not_observed

Output: not observed.
Interaction: not_observed
<pre>{&quot;context_status&quot;:&quot;not_observed&quot;,&quot;context_text&quot;:null,&quot;decision_counts&quot;:{&quot;apply&quot;:null,&quot;clarifications&quot;:null,&quot;commit&quot;:null,&quot;confirmations&quot;:null,&quot;rejections&quot;:null,&quot;revisions&quot;:null},&quot;evidence_sha256&quot;:{&quot;accounting&quot;:[],&quot;interaction&quot;:[]},&quot;scoring_note&quot;:&quot;Required confirmations are safeguards. Missing interaction context requires a null human approval_burden score; output and brief alone do not establish avoidability.&quot;}</pre>
